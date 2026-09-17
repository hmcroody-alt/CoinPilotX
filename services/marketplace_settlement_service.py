"""Canonical post-payment Marketplace finance and payout authority.

This service projects a paid ``seller_transactions`` row into immutable,
idempotent seller/platform ledger effects and owns every later commercial
adjustment.  Historical economics always come from the transaction's stored
quote; the current fee policy is never consulted during a refund.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from services import db
from services.business_os.ledger import ledger
from services.business_os.marketplace import policy

PAYOUT_STATES = frozenset({"pending_onboarding", "pending_order", "pending_fulfillment",
                           "protection_hold", "eligible", "scheduled", "paid", "failed",
                           "held", "disputed", "reversed"})
ALLOWED_TRANSITIONS = {
    "pending_order": {"pending_onboarding", "pending_fulfillment", "held", "disputed", "reversed"},
    "pending_onboarding": {"pending_fulfillment", "held", "disputed", "reversed"},
    "pending_fulfillment": {"protection_hold", "held", "disputed", "reversed"},
    "protection_hold": {"eligible", "held", "disputed", "reversed"},
    "eligible": {"scheduled", "held", "disputed", "reversed"},
    "scheduled": {"paid", "failed", "held", "disputed", "reversed"},
    "failed": {"scheduled", "held", "disputed", "reversed"},
    "held": {"pending_onboarding", "pending_fulfillment", "protection_hold", "eligible", "reversed"},
    "disputed": {"held", "pending_fulfillment", "protection_hold", "eligible", "reversed"},
    "paid": {"reversed"}, "reversed": set(),
}

class SettlementError(ValueError):
    pass

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def _row(value):
    return dict(value) if value is not None else None

def _metadata(tx: Mapping[str, Any]) -> dict:
    raw = tx.get("metadata_json") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}

def _snapshot(tx: Mapping[str, Any]) -> dict:
    meta = _metadata(tx)
    quote = meta.get("commercial_quote") if isinstance(meta.get("commercial_quote"), dict) else {}
    total = int(tx.get("amount_cents") or 0)
    fee = int(tx.get("platform_fee_cents") or 0)
    seller = int(tx.get("seller_net_cents") or max(0, total - fee))
    merchandise = int(quote.get("merchandise_net_minor", total))
    shipping = int(quote.get("shipping_minor", 0))
    tax = int(quote.get("tax_minor", 0))
    shipping_credit = int(quote.get("seller_shipping_credit_minor", shipping))
    return {
        "quote_id": quote.get("quote_id"),
        "fee_policy_version": quote.get("fee_policy_version") or "MARKETPLACE_LEGACY_CURRENT",
        "payout_policy_version": quote.get("payout_policy_version") or policy.PAYOUT_POLICY_VERSION,
        "fee_rate_bps": int(quote.get("platform_fee_bps", 0) or 0),
        "merchandise_net_minor": merchandise, "shipping_minor": shipping, "tax_minor": tax,
        "seller_shipping_credit_minor": shipping_credit, "platform_fee_minor": fee,
        "seller_earnings_minor": seller, "buyer_total_minor": int(quote.get("buyer_total_minor", total)),
        "currency": str(tx.get("currency") or quote.get("currency") or "USD").lower(),
    }

_TRANSFER_GROUP_COLUMN_READY = False


def _ensure_transfer_group_column(conn) -> None:
    """One cart checkout is one charge but several settlements (one per line).

    Stripe groups the resulting transfers back to that charge by `transfer_group`,
    so each settlement has to remember the group its charge carried; it cannot be
    derived from `order_id`, which is per-transaction.
    """
    global _TRANSFER_GROUP_COLUMN_READY
    if _TRANSFER_GROUP_COLUMN_READY:
        return
    cols = set()
    try:
        cols = {str(r[1]).lower() for r in conn.execute(
            "PRAGMA table_info(marketplace_commercial_settlements)").fetchall()}
    except Exception:  # noqa: BLE001 - Postgres has no PRAGMA
        cols = {str(r[0]).lower() for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='marketplace_commercial_settlements'").fetchall()}
    if "transfer_group" not in cols:
        conn.execute("ALTER TABLE marketplace_commercial_settlements ADD COLUMN transfer_group TEXT")
    # `delivered_at` anchors the buyer's return window. It is stored rather than
    # derived from `protection_ends_at`, which would mean changing
    # STANDARD_PAYOUT_PROTECTION_DAYS silently moved every past order's return
    # deadline — two unrelated policies must not share one stored number.
    if "delivered_at" not in cols:
        conn.execute("ALTER TABLE marketplace_commercial_settlements ADD COLUMN delivered_at TEXT")
    # Cached only once the caller's commit has landed, so a rolled-back DDL is
    # not remembered as applied.


def ensure_schema(conn=None) -> None:
    global _TRANSFER_GROUP_COLUMN_READY
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_commercial_settlements (
            seller_transaction_id INTEGER PRIMARY KEY, order_id TEXT NOT NULL UNIQUE,
            seller_id TEXT NOT NULL, quote_id TEXT, currency TEXT NOT NULL,
            fee_policy_version TEXT NOT NULL, payout_policy_version TEXT NOT NULL,
            fee_rate_bps INTEGER NOT NULL, merchandise_net_minor INTEGER NOT NULL,
            shipping_minor INTEGER NOT NULL, tax_minor INTEGER NOT NULL,
            seller_shipping_credit_minor INTEGER NOT NULL, buyer_total_minor INTEGER NOT NULL,
            gross_platform_fee_minor INTEGER NOT NULL, fee_reversed_minor INTEGER NOT NULL DEFAULT 0,
            net_platform_fee_minor INTEGER NOT NULL, gross_seller_earnings_minor INTEGER NOT NULL,
            seller_reversed_minor INTEGER NOT NULL DEFAULT 0, net_seller_earnings_minor INTEGER NOT NULL,
            payout_state TEXT NOT NULL, payout_ready INTEGER NOT NULL DEFAULT 0,
            blocker_code TEXT, protection_ends_at TEXT, provider_payment_id TEXT,
            provider_payout_id TEXT, seller_ledger_ref TEXT, fee_ledger_ref TEXT, tax_ledger_ref TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_commercial_refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, seller_transaction_id INTEGER NOT NULL,
            provider_refund_id TEXT NOT NULL, merchandise_refund_minor INTEGER NOT NULL,
            shipping_refund_minor INTEGER NOT NULL, tax_refund_minor INTEGER NOT NULL,
            other_refund_minor INTEGER NOT NULL, fee_reversal_minor INTEGER NOT NULL,
            seller_reversal_minor INTEGER NOT NULL, total_refund_minor INTEGER NOT NULL,
            currency TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(seller_transaction_id, provider_refund_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_payout_state_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, seller_transaction_id INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE, from_state TEXT, to_state TEXT NOT NULL,
            actor TEXT NOT NULL, reason TEXT, provider_reference TEXT, created_at TEXT NOT NULL)""")
        _ensure_transfer_group_column(conn)
        if owned:
            conn.commit()
            _TRANSFER_GROUP_COLUMN_READY = True
    finally:
        if owned:
            conn.close()

def get_settlement(transaction_id: Any, conn=None) -> dict | None:
    owned = conn is None
    if owned:
        ensure_schema(); conn = db.connect()
    try:
        return _row(conn.execute("SELECT * FROM marketplace_commercial_settlements WHERE seller_transaction_id=?",
                                 (int(transaction_id),)).fetchone())
    finally:
        if owned:
            conn.close()

def delivered_at_map(cur, transaction_ids) -> dict:
    """``{seller_transaction_id: delivered_at}`` for the ids that have a delivery.

    Two different callers need this — the returns route, to decide whether the
    window is still open, and the buyer-orders serializer, to tell the buyer when
    it closes — so it lives here, with the table it reads, rather than being
    written twice with two different failure behaviours.

    Batched on purpose: the orders list renders up to 100 rows, and a per-row
    lookup there is exactly the N+1 that made that screen the slowest surface in
    the app once already.

    Two things it must not do:

      * it must not raise. This table is created on this module's first use,
        which in a fresh deployment may not have happened, and neither caller
        should fail because an optional fact is unavailable.
      * it must not poison the caller's transaction. On Postgres a failed
        statement aborts the whole transaction, so a bare try/except would leave
        every *later* statement in the same request failing with "current
        transaction is aborted" — the orders list would go blank, or the returns
        INSERT would be lost. Hence the SAVEPOINT. SQLite never shows this.

    An absent table or column yields ``{}``, which both callers read as "no
    delivery recorded" and fall back to the purchase date. Bounded either way —
    never "no deadline".
    """
    ids = []
    for value in transaction_ids or ():
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    ids = sorted(set(i for i in ids if i))
    if not ids:
        return {}
    try:
        cur.execute("SAVEPOINT mkt_delivered_at_map")
    except Exception:  # noqa: BLE001 - driver without savepoint support
        return {}
    placeholders = ",".join(["?"] * len(ids))
    try:
        cur.execute(
            "SELECT seller_transaction_id, delivered_at "
            "FROM marketplace_commercial_settlements "
            f"WHERE seller_transaction_id IN ({placeholders})", tuple(ids))
        found = {}
        for row in cur.fetchall() or ():
            record = dict(row)
            value = record.get("delivered_at")
            if value:
                found[int(record.get("seller_transaction_id") or 0)] = value
        cur.execute("RELEASE SAVEPOINT mkt_delivered_at_map")
        return found
    except Exception:  # noqa: BLE001 - absent table/column is a normal state here
        try:
            cur.execute("ROLLBACK TO SAVEPOINT mkt_delivered_at_map")
            cur.execute("RELEASE SAVEPOINT mkt_delivered_at_map")
        except Exception:  # noqa: BLE001
            pass
        return {}


def settlements_for_payment(provider_payment_id: str, conn=None) -> list[dict]:
    """Every settlement funded by one Stripe payment, oldest first.

    A dispute event does not carry the charge's metadata — Stripe hands over a
    Dispute object whose own `metadata` is empty — so the seller transaction ids
    the refund path reads straight off a Charge are simply not there. What a
    dispute does carry is the payment intent, which is what `provider_payment_id`
    records, so that is the way back from a chargeback to the sellers it affects.

    A list, not a row: one cart checkout is one payment intent and one settlement
    per seller line, and a chargeback takes back the whole charge.
    """
    reference = str(provider_payment_id or "").strip()
    if not reference:
        return []
    owned = conn is None
    if owned:
        ensure_schema(); conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM marketplace_commercial_settlements WHERE provider_payment_id=? "
            "ORDER BY seller_transaction_id", (reference,)).fetchall()
    finally:
        if owned:
            conn.close()
    return [dict(row) for row in rows]


def hold_origin_state(transaction_id: Any) -> str:
    """The payout state a settlement was in before its current hold.

    `place_hold` overwrites `payout_state`, so the state the settlement should go
    back to when a dispute is won survives only in the immutable event log. Read
    it from there rather than guessing: releasing everything to
    `pending_fulfillment` would demand a second delivery confirmation that
    `mark_delivered` would dedupe away, stranding the seller's money forever.
    """
    ensure_schema(); conn = db.connect()
    try:
        row = conn.execute(
            "SELECT from_state FROM marketplace_payout_state_events "
            "WHERE seller_transaction_id=? AND to_state IN ('held','disputed') "
            "ORDER BY id DESC LIMIT 1", (int(transaction_id),)).fetchone()
    finally:
        conn.close()
    return str(dict(row).get("from_state") or "") if row else ""


def settle_paid_transaction(tx: Mapping[str, Any], *, payout_ready: bool,
                            provider_payment_id: str = "", transfer_group: str = "",
                            actor: str = "stripe") -> dict:
    transaction_id = int(tx.get("id") or 0)
    seller_id = str(tx.get("seller_user_id") or "")
    if not transaction_id or not seller_id or str(tx.get("item_type") or "") != "marketplace_product":
        raise SettlementError("a paid Marketplace seller transaction is required")
    snap = _snapshot(tx); now = _now(); order_id = f"marketplace_order:{transaction_id}"
    initial = "pending_fulfillment" if payout_ready else "pending_onboarding"
    ensure_schema(); conn = db.connect()
    try:
        conn.execute("""INSERT INTO marketplace_commercial_settlements
            (seller_transaction_id,order_id,seller_id,quote_id,currency,fee_policy_version,payout_policy_version,
             fee_rate_bps,merchandise_net_minor,shipping_minor,tax_minor,seller_shipping_credit_minor,
             buyer_total_minor,gross_platform_fee_minor,net_platform_fee_minor,gross_seller_earnings_minor,
             net_seller_earnings_minor,payout_state,payout_ready,provider_payment_id,transfer_group,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(seller_transaction_id) DO NOTHING""",
            (transaction_id, order_id, seller_id, snap["quote_id"], snap["currency"],
             snap["fee_policy_version"], snap["payout_policy_version"], snap["fee_rate_bps"],
             snap["merchandise_net_minor"], snap["shipping_minor"], snap["tax_minor"],
             snap["seller_shipping_credit_minor"], snap["buyer_total_minor"], snap["platform_fee_minor"],
             snap["platform_fee_minor"], snap["seller_earnings_minor"], snap["seller_earnings_minor"],
             initial, 1 if payout_ready else 0, provider_payment_id,
             str(transfer_group or order_id), now, now))
        conn.commit()
    finally:
        conn.close()
    seller_ref = None; fee_ref = None; tax_ref = None
    if snap["seller_earnings_minor"]:
        seller_ref = ledger.post_entry(
            idempotency_key=f"marketplace:settlement:seller:{transaction_id}", actor=actor,
            amount_cents=snap["seller_earnings_minor"], currency=snap["currency"],
            entry_type="marketplace_seller_earning", source="external:stripe_marketplace",
            destination=f"seller_payable:{seller_id}", related_object=order_id,
            provider_reference=provider_payment_id, metadata=snap)["transaction_id"]
    if snap["platform_fee_minor"]:
        fee_ref = ledger.post_entry(
            idempotency_key=f"marketplace:settlement:fee:{transaction_id}", actor=actor,
            amount_cents=snap["platform_fee_minor"], currency=snap["currency"],
            entry_type="marketplace_platform_fee", source="external:stripe_marketplace",
            destination="platform:marketplace_revenue", related_object=order_id,
            provider_reference=provider_payment_id, metadata=snap)["transaction_id"]
    if snap["tax_minor"]:
        tax_ref = ledger.post_entry(
            idempotency_key=f"marketplace:settlement:tax:{transaction_id}", actor=actor,
            amount_cents=snap["tax_minor"], currency=snap["currency"],
            entry_type="marketplace_tax_liability", source="external:stripe_marketplace",
            destination="liability:marketplace_tax", related_object=order_id,
            provider_reference=provider_payment_id, metadata=snap)["transaction_id"]
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET seller_ledger_ref=COALESCE(seller_ledger_ref,?), fee_ledger_ref=COALESCE(fee_ledger_ref,?), tax_ledger_ref=COALESCE(tax_ledger_ref,?), updated_at=? WHERE seller_transaction_id=?",
                     (seller_ref, fee_ref, tax_ref, _now(), transaction_id)); conn.commit()
    finally:
        conn.close()
    return get_settlement(transaction_id)

def apply_refund(transaction_id: Any, *, provider_refund_id: str,
                 merchandise_refund_minor: int = 0, shipping_refund_minor: int = 0,
                 tax_refund_minor: int = 0, other_refund_minor: int = 0,
                 actor: str = "stripe") -> dict:
    values = [merchandise_refund_minor, shipping_refund_minor, tax_refund_minor, other_refund_minor]
    if not provider_refund_id or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        raise SettlementError("provider_refund_id and non-negative integer refund components are required")
    if not sum(values):
        raise SettlementError("refund total must be positive")
    ensure_schema(); conn = db.connect(); transaction_id = int(transaction_id)
    try:
        existing = conn.execute("SELECT * FROM marketplace_commercial_refunds WHERE seller_transaction_id=? AND provider_refund_id=?",
                                (transaction_id, provider_refund_id)).fetchone()
        if existing:
            return {**dict(existing), "duplicate": True}
        conn.execute("UPDATE marketplace_commercial_settlements SET updated_at=updated_at WHERE seller_transaction_id=?", (transaction_id,))
        settlement = get_settlement(transaction_id, conn=conn)
        if not settlement:
            raise SettlementError("settlement not found")
        totals = dict(conn.execute("""SELECT COALESCE(SUM(merchandise_refund_minor),0) merchandise,
            COALESCE(SUM(shipping_refund_minor),0) shipping, COALESCE(SUM(tax_refund_minor),0) tax,
            COALESCE(SUM(other_refund_minor),0) other, COALESCE(SUM(fee_reversal_minor),0) fee,
            COALESCE(SUM(seller_reversal_minor),0) seller FROM marketplace_commercial_refunds
            WHERE seller_transaction_id=?""", (transaction_id,)).fetchone())
        new_merch = totals["merchandise"] + merchandise_refund_minor
        if new_merch > settlement["merchandise_net_minor"] or totals["shipping"] + shipping_refund_minor > settlement["shipping_minor"] or totals["tax"] + tax_refund_minor > settlement["tax_minor"]:
            raise SettlementError("refund components exceed the original commercial snapshot")
        if sum(totals[k] for k in ("merchandise", "shipping", "tax", "other")) + sum(values) > settlement["buyer_total_minor"]:
            raise SettlementError("refunds exceed the original buyer total")
        target_fee = policy.platform_fee_reversal(
            original_merchandise_net_cents=settlement["merchandise_net_minor"],
            original_platform_fee_cents=settlement["gross_platform_fee_minor"],
            refunded_merchandise_cents=new_merch)
        fee_delta = target_fee - totals["fee"]
        shipping_credit_delta = min(shipping_refund_minor, max(0, settlement["seller_shipping_credit_minor"] - totals["shipping"]))
        seller_delta = merchandise_refund_minor + shipping_credit_delta - fee_delta
        seller_delta = max(0, min(seller_delta, settlement["gross_seller_earnings_minor"] - totals["seller"]))
        total = sum(values); now = _now()
        conn.execute("""INSERT INTO marketplace_commercial_refunds
            (seller_transaction_id,provider_refund_id,merchandise_refund_minor,shipping_refund_minor,
             tax_refund_minor,other_refund_minor,fee_reversal_minor,seller_reversal_minor,total_refund_minor,currency,created_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (transaction_id, provider_refund_id, merchandise_refund_minor, shipping_refund_minor,
             tax_refund_minor, other_refund_minor, fee_delta, seller_delta, total, settlement["currency"], now))
        new_fee_reversed = totals["fee"] + fee_delta; new_seller_reversed = totals["seller"] + seller_delta
        payout_state = "reversed" if total + sum(totals[k] for k in ("merchandise", "shipping", "tax", "other")) >= settlement["buyer_total_minor"] else "held"
        conn.execute("""UPDATE marketplace_commercial_settlements SET fee_reversed_minor=?,
            net_platform_fee_minor=gross_platform_fee_minor-?, seller_reversed_minor=?,
            net_seller_earnings_minor=gross_seller_earnings_minor-?, payout_state=?,
            blocker_code='refund',updated_at=? WHERE seller_transaction_id=?""",
            (new_fee_reversed, new_fee_reversed, new_seller_reversed, new_seller_reversed,
             payout_state, now, transaction_id)); conn.commit()
    finally:
        conn.close()
    related = f"marketplace_order:{transaction_id}"
    if seller_delta:
        ledger.post_entry(idempotency_key=f"marketplace:refund:seller:{transaction_id}:{provider_refund_id}",
                          actor=actor, amount_cents=seller_delta, currency=settlement["currency"],
                          entry_type="marketplace_seller_reversal", source=f"seller_payable:{settlement['seller_id']}",
                          destination="external:stripe_marketplace_refunds", related_object=related,
                          provider_reference=provider_refund_id, allow_negative=True)
    if fee_delta:
        ledger.post_entry(idempotency_key=f"marketplace:refund:fee:{transaction_id}:{provider_refund_id}",
                          actor=actor, amount_cents=fee_delta, currency=settlement["currency"],
                          entry_type="marketplace_fee_reversal", source="platform:marketplace_revenue",
                          destination="external:stripe_marketplace_refunds", related_object=related,
                          provider_reference=provider_refund_id, allow_negative=True)
    result = get_settlement(transaction_id)
    return {"settlement": result, "provider_refund_id": provider_refund_id,
            "fee_reversal_minor": fee_delta, "seller_reversal_minor": seller_delta,
            "total_refund_minor": total, "duplicate": False}

def transition_payout(transaction_id: Any, to_state: str, *, actor: str, reason: str,
                      idempotency_key: str, provider_reference: str = "") -> dict:
    if to_state not in PAYOUT_STATES or not actor or not reason or not idempotency_key:
        raise SettlementError("valid state, actor, reason, and idempotency key are required")
    ensure_schema(); conn = db.connect(); transaction_id = int(transaction_id)
    try:
        prior = conn.execute("SELECT * FROM marketplace_payout_state_events WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if prior:
            return {"settlement": get_settlement(transaction_id, conn=conn), "duplicate": True}
        conn.execute("UPDATE marketplace_commercial_settlements SET updated_at=updated_at WHERE seller_transaction_id=?", (transaction_id,))
        current = get_settlement(transaction_id, conn=conn)
        if not current:
            raise SettlementError("settlement not found")
        from_state = current["payout_state"]
        if to_state not in ALLOWED_TRANSITIONS.get(from_state, set()):
            raise SettlementError(f"illegal payout transition: {from_state} -> {to_state}")
        if to_state in {"eligible", "scheduled", "paid"} and current.get("blocker_code"):
            raise SettlementError("payout is blocked")
        now = _now()
        conn.execute("INSERT INTO marketplace_payout_state_events (seller_transaction_id,idempotency_key,from_state,to_state,actor,reason,provider_reference,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (transaction_id, idempotency_key, from_state, to_state, actor, reason, provider_reference, now))
        conn.execute("UPDATE marketplace_commercial_settlements SET payout_state=?, provider_payout_id=CASE WHEN ?<>'' THEN ? ELSE provider_payout_id END, updated_at=? WHERE seller_transaction_id=?",
                     (to_state, provider_reference, provider_reference, now, transaction_id)); conn.commit()
    finally:
        conn.close()
    return {"settlement": get_settlement(transaction_id), "duplicate": False}

def mark_delivered(transaction_id: Any, *, actor: str, idempotency_key: str) -> dict:
    result = transition_payout(transaction_id, "protection_hold", actor=actor,
                               reason="delivery confirmed", idempotency_key=idempotency_key)
    now = datetime.now(timezone.utc)
    ends = (now + timedelta(days=policy.STANDARD_PAYOUT_PROTECTION_DAYS)).isoformat()
    conn = db.connect()
    try:
        # COALESCE so a repeated delivery confirmation cannot restart the buyer's
        # return window; the first confirmation is the one that counts.
        conn.execute("UPDATE marketplace_commercial_settlements SET protection_ends_at=?, "
                     "delivered_at=COALESCE(delivered_at, ?) WHERE seller_transaction_id=?",
                     (ends, now.isoformat(), int(transaction_id))); conn.commit()
    finally:
        conn.close()
    result["settlement"] = get_settlement(transaction_id)
    return result

def reconcile_onboarding(transaction_id: Any, *, actor: str, idempotency_key: str) -> dict:
    result = transition_payout(transaction_id, "pending_fulfillment", actor=actor,
                               reason="Stripe Connect onboarding completed",
                               idempotency_key=idempotency_key)
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET payout_ready=1 WHERE seller_transaction_id=?",
                     (int(transaction_id),)); conn.commit()
    finally:
        conn.close()
    result["settlement"] = get_settlement(transaction_id)
    return result

def reconcile_seller_onboarding(seller_id: Any, *, actor: str, reference: str) -> list[dict]:
    """Unstick every sale a seller made before they finished Connect onboarding.

    A settlement opens in `pending_onboarding` when the seller had no usable
    connected account at the moment of the sale, and `reconcile_onboarding` is
    what moves it on once they finish. Nothing ever called it: `account.updated`
    refreshed `seller_payout_accounts` and stopped there, so a seller who sold
    first and onboarded second stayed unpayable forever while their money sat in
    the ledger looking perfectly healthy.

    Keyed on the Stripe account id rather than the event, so a redelivery and a
    later `account.updated` for the same seller both dedupe to one transition per
    settlement. Nothing here moves money — it only lets the release chain reach
    the states where the protection window and the payout gates still apply.
    """
    seller_id = str(seller_id or "").strip()
    if not seller_id or not reference:
        return []
    ensure_schema(); conn = db.connect()
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT seller_transaction_id FROM marketplace_commercial_settlements "
            "WHERE seller_id=? AND payout_state='pending_onboarding' AND blocker_code IS NULL "
            "ORDER BY seller_transaction_id", (seller_id,)).fetchall()]
    finally:
        conn.close()
    reconciled = []
    for row in rows:
        tx_id = int(row["seller_transaction_id"])
        try:
            reconciled.append(reconcile_onboarding(
                tx_id, actor=actor, idempotency_key=f"connect:{reference}:{tx_id}"))
        except SettlementError:
            # Raced by another worker or already moved on. Idempotent by design.
            continue
    return reconciled


def place_hold(transaction_id: Any, *, actor: str, reason_code: str,
               idempotency_key: str, disputed: bool = False) -> dict:
    if not reason_code:
        raise SettlementError("reason_code is required")
    result = transition_payout(transaction_id, "disputed" if disputed else "held", actor=actor,
                               reason=reason_code, idempotency_key=idempotency_key)
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET blocker_code=? WHERE seller_transaction_id=?",
                     (reason_code, int(transaction_id))); conn.commit()
    finally:
        conn.close()
    result["settlement"] = get_settlement(transaction_id)
    return result

def release_hold(transaction_id: Any, *, to_state: str, actor: str, reason: str,
                 idempotency_key: str) -> dict:
    if to_state not in {"pending_onboarding", "pending_fulfillment", "protection_hold", "eligible"}:
        raise SettlementError("invalid hold release target")
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET blocker_code=NULL WHERE seller_transaction_id=?",
                     (int(transaction_id),)); conn.commit()
    finally:
        conn.close()
    return transition_payout(transaction_id, to_state, actor=actor, reason=reason,
                             idempotency_key=idempotency_key)

def evaluate_eligibility(transaction_id: Any, *, now: datetime | None = None,
                         idempotency_key: str | None = None) -> dict:
    current = get_settlement(transaction_id)
    if not current:
        raise SettlementError("settlement not found")
    if current["payout_state"] != "protection_hold" or current.get("blocker_code") or not current.get("payout_ready"):
        return {"eligible": False, "settlement": current}
    due = datetime.fromisoformat(str(current.get("protection_ends_at") or "").replace("Z", "+00:00"))
    check = now or datetime.now(timezone.utc)
    if check < due:
        return {"eligible": False, "settlement": current}
    result = transition_payout(transaction_id, "eligible", actor="payout_scheduler",
                               reason="versioned protection window satisfied",
                               idempotency_key=idempotency_key or f"eligible:{transaction_id}:{current['protection_ends_at']}")
    return {"eligible": True, **result}

def readiness() -> dict:
    return {"quote_authority": "PASS", "refund_ledger": "PASS", "payout_state_machine": "PASS",
            "seller_disclosure": "FAIL", "material_edit_re_review": "FAIL", "ip_counterfeit": "FAIL",
            "appeals": "FAIL", "high_volume_compliance": "FAIL", "admin_economics": "FAIL",
            "reconciliation": "FAIL", "owner_approved": "NO", "effective_at": "UNSET", "activatable": "NO"}
