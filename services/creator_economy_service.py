"""Creator economy ledger, wallet, payout, and reconciliation service."""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from typing import Any

from services import db as db_service


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def new_trace_id() -> str:
    return secrets.token_hex(8)


def cents(value: Any) -> int:
    try:
        if isinstance(value, str):
            text = value.replace("$", "").replace(",", "").strip()
            if "." in text:
                return int(round(float(text) * 100))
        return int(value or 0)
    except Exception:
        return 0


def parse_price_label(label: str) -> int:
    import re

    match = re.search(r"(\d+(?:\.\d{1,2})?)", str(label or ""))
    return cents(match.group(1)) if match else 0


def get_fee_rule(seller_type: str, item_type: str = "") -> dict[str, Any]:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM platform_fee_rules
        WHERE active=1 AND seller_type=? AND (item_type=? OR item_type='' OR item_type IS NULL)
        ORDER BY CASE WHEN item_type=? THEN 0 ELSE 1 END, id DESC
        LIMIT 1
        """,
        (seller_type, item_type or "", item_type or ""),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        legacy_bps = 1000 if seller_type == "merchant" else 1500 if seller_type == "teacher" else 0
        return {"seller_type": seller_type, "item_type": item_type, "fee_percent": legacy_bps / 100, "fixed_fee_cents": 0}
    data = dict(row)
    if not data.get("fee_percent") and data.get("fee_bps") is not None:
        data["fee_percent"] = float(data.get("fee_bps") or 0) / 100
    conn.close()
    return data


def calculate_fees(gross_amount_cents: int, seller_type: str, item_type: str = "") -> dict[str, int]:
    gross = max(0, int(gross_amount_cents or 0))
    rule = get_fee_rule(seller_type, item_type)
    fee_percent = float(rule.get("fee_percent") or 0)
    fixed = int(rule.get("fixed_fee_cents") or 0)
    platform_fee = min(gross, int(round(gross * (fee_percent / 100))) + fixed)
    provider_fee = 0
    net = max(0, gross - platform_fee - provider_fee)
    return {"gross_amount_cents": gross, "platform_fee_cents": platform_fee, "provider_fee_cents": provider_fee, "net_amount_cents": net}


def ensure_wallet(user_id: int, wallet_type: str, currency: str = "USD") -> dict[str, Any]:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM creator_wallets WHERE user_id=? AND wallet_type=? AND currency=? LIMIT 1",
        (int(user_id), wallet_type, currency.upper()),
    )
    row = cur.fetchone()
    if row:
        conn.close()
        return dict(row)
    cur.execute(
        """
        INSERT INTO creator_wallets
        (user_id, wallet_type, currency, available_balance_cents, pending_balance_cents, lifetime_earnings_cents, lifetime_fees_cents, status, created_at, updated_at)
        VALUES (?, ?, ?, 0, 0, 0, 0, 'active', ?, ?)
        """,
        (int(user_id), wallet_type, currency.upper(), now, now),
    )
    wallet_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM creator_wallets WHERE id=?", (wallet_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row)


def add_ledger_entry(
    *,
    wallet_id: int,
    user_id: int,
    related_user_id: int = 0,
    source_type: str,
    source_id: str | int = "",
    entry_type: str,
    amount_cents: int,
    currency: str = "USD",
    status: str = "posted",
    description: str = "",
    provider: str = "",
    provider_reference: str = "",
    trace_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO creator_ledger_entries
        (wallet_id, user_id, related_user_id, source_type, source_id, entry_type, amount_cents, currency, status, description, provider, provider_reference, trace_id, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(wallet_id),
            int(user_id),
            int(related_user_id or 0),
            source_type,
            str(source_id or ""),
            entry_type,
            int(amount_cents or 0),
            currency.upper(),
            status,
            description[:600],
            provider,
            provider_reference,
            trace_id or new_trace_id(),
            json.dumps(metadata or {}, default=str),
            now,
        ),
    )
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(entry_id)


def reconcile_wallet(wallet_id: int) -> dict[str, Any]:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM creator_wallets WHERE id=?", (int(wallet_id),))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "message": "Wallet not found."}
    wallet = dict(row)
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN entry_type IN ('credit','release') AND status IN ('posted','available') THEN amount_cents ELSE 0 END) AS credits,
            SUM(CASE WHEN entry_type IN ('debit','fee','refund','payout') AND status IN ('posted','available') THEN amount_cents ELSE 0 END) AS debits,
            SUM(CASE WHEN entry_type='hold' AND status IN ('pending','posted') THEN amount_cents ELSE 0 END) AS pending,
            SUM(CASE WHEN entry_type='fee' THEN amount_cents ELSE 0 END) AS fees
        FROM creator_ledger_entries WHERE wallet_id=?
        """,
        (int(wallet_id),),
    )
    totals = dict(cur.fetchone() or {})
    available = max(0, int(totals.get("credits") or 0) - int(totals.get("debits") or 0))
    pending = max(0, int(totals.get("pending") or 0))
    fees = max(0, int(totals.get("fees") or 0))
    cur.execute(
        """
        UPDATE creator_wallets
        SET available_balance_cents=?, pending_balance_cents=?, lifetime_earnings_cents=?, lifetime_fees_cents=?, updated_at=?
        WHERE id=?
        """,
        (available, pending, available + pending, fees, now_iso(), int(wallet_id)),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "wallet_id": int(wallet_id), "available_balance_cents": available, "pending_balance_cents": pending}


def create_transaction(
    *,
    buyer_user_id: int,
    seller_user_id: int,
    seller_type: str,
    item_type: str,
    item_id: int | str,
    gross_amount_cents: int,
    currency: str = "USD",
    metadata: dict[str, Any] | None = None,
    trace_id: str = "",
) -> dict[str, Any]:
    trace_id = trace_id or new_trace_id()
    now = now_iso()
    fees = calculate_fees(gross_amount_cents, seller_type, item_type)
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO creator_transactions
        (buyer_user_id, seller_user_id, seller_type, item_type, item_id, gross_amount_cents, platform_fee_cents, provider_fee_cents, net_amount_cents, currency, status, provider, trace_id, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'stripe', ?, ?, ?, ?)
        """,
        (
            int(buyer_user_id or 0),
            int(seller_user_id or 0),
            seller_type,
            item_type,
            str(item_id or ""),
            fees["gross_amount_cents"],
            fees["platform_fee_cents"],
            fees["provider_fee_cents"],
            fees["net_amount_cents"],
            currency.upper(),
            trace_id,
            json.dumps(metadata or {}, default=str),
            now,
            now,
        ),
    )
    transaction_id = cur.lastrowid
    cur.execute(
        """
        INSERT INTO seller_transactions
        (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency, platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
        """,
        (int(buyer_user_id or 0), int(seller_user_id or 0), seller_type, item_type, int(item_id or 0) if str(item_id or "").isdigit() else 0, fees["gross_amount_cents"], currency.upper(), fees["platform_fee_cents"], fees["net_amount_cents"], json.dumps({"creator_transaction_id": transaction_id, **(metadata or {})}, default=str), now, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "transaction_id": int(transaction_id), "trace_id": trace_id, **fees}


def attach_checkout(transaction_id: int, provider_checkout_id: str = "", provider_payment_id: str = "", provider_transfer_id: str = "") -> None:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE creator_transactions
        SET provider_checkout_id=COALESCE(NULLIF(?, ''), provider_checkout_id),
            provider_payment_id=COALESCE(NULLIF(?, ''), provider_payment_id),
            provider_transfer_id=COALESCE(NULLIF(?, ''), provider_transfer_id),
            updated_at=?
        WHERE id=?
        """,
        (provider_checkout_id, provider_payment_id, provider_transfer_id, now, int(transaction_id)),
    )
    cur.execute(
        "UPDATE seller_transactions SET stripe_checkout_session_id=COALESCE(NULLIF(?, ''), stripe_checkout_session_id), stripe_payment_intent_id=COALESCE(NULLIF(?, ''), stripe_payment_intent_id), updated_at=? WHERE metadata_json LIKE ?",
        (provider_checkout_id, provider_payment_id, now, f'%"creator_transaction_id": {int(transaction_id)}%'),
    )
    conn.commit()
    conn.close()


def mark_transaction_paid(transaction_id: int, provider_payment_id: str = "", provider_reference: str = "") -> dict[str, Any]:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM creator_transactions WHERE id=? LIMIT 1", (int(transaction_id),))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "message": "Transaction not found."}
    tx = dict(row)
    if tx.get("status") == "paid":
        conn.close()
        return {"ok": True, "idempotent": True, "transaction_id": int(transaction_id)}
    cur.execute(
        "UPDATE creator_transactions SET status='paid', provider_payment_id=COALESCE(NULLIF(?, ''), provider_payment_id), updated_at=? WHERE id=?",
        (provider_payment_id, now, int(transaction_id)),
    )
    cur.execute(
        "UPDATE seller_transactions SET status='paid', stripe_payment_intent_id=COALESCE(NULLIF(?, ''), stripe_payment_intent_id), updated_at=? WHERE metadata_json LIKE ?",
        (provider_payment_id, now, f'%"creator_transaction_id": {int(transaction_id)}%'),
    )
    conn.commit()
    conn.close()
    seller_wallet = ensure_wallet(int(tx["seller_user_id"]), tx["seller_type"], tx.get("currency") or "USD")
    platform_wallet = ensure_wallet(0, "platform", tx.get("currency") or "USD")
    add_ledger_entry(wallet_id=seller_wallet["id"], user_id=int(tx["seller_user_id"]), related_user_id=int(tx["buyer_user_id"] or 0), source_type=tx["item_type"] + "_sale", source_id=int(transaction_id), entry_type="hold", amount_cents=int(tx["net_amount_cents"] or 0), currency=tx.get("currency") or "USD", status="pending", description=f"Pending {tx['item_type']} sale", provider="stripe", provider_reference=provider_reference or provider_payment_id, trace_id=tx.get("trace_id") or "", metadata={"transaction_id": transaction_id})
    add_ledger_entry(wallet_id=platform_wallet["id"], user_id=0, related_user_id=int(tx["seller_user_id"] or 0), source_type=tx["item_type"] + "_sale", source_id=int(transaction_id), entry_type="fee", amount_cents=int(tx["platform_fee_cents"] or 0), currency=tx.get("currency") or "USD", status="posted", description=f"Platform fee for {tx['item_type']} sale", provider="stripe", provider_reference=provider_reference or provider_payment_id, trace_id=tx.get("trace_id") or "", metadata={"transaction_id": transaction_id})
    reconcile_wallet(int(seller_wallet["id"]))
    reconcile_wallet(int(platform_wallet["id"]))
    return {"ok": True, "transaction_id": int(transaction_id), "seller_wallet_id": int(seller_wallet["id"])}


def handle_refund(transaction_id: int, amount_cents: int | None = None, provider_reference: str = "") -> dict[str, Any]:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM creator_transactions WHERE id=? LIMIT 1", (int(transaction_id),))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "message": "Transaction not found."}
    tx = dict(row)
    refund_amount = int(amount_cents if amount_cents is not None else tx.get("gross_amount_cents") or 0)
    cur.execute("UPDATE creator_transactions SET status='refunded', updated_at=? WHERE id=?", (now_iso(), int(transaction_id)))
    conn.commit()
    conn.close()
    seller_wallet = ensure_wallet(int(tx["seller_user_id"]), tx["seller_type"], tx.get("currency") or "USD")
    add_ledger_entry(wallet_id=seller_wallet["id"], user_id=int(tx["seller_user_id"]), related_user_id=int(tx["buyer_user_id"] or 0), source_type="refund", source_id=int(transaction_id), entry_type="refund", amount_cents=min(refund_amount, int(tx.get("net_amount_cents") or 0)), currency=tx.get("currency") or "USD", status="posted", description="Refund reversal", provider="stripe", provider_reference=provider_reference, trace_id=tx.get("trace_id") or "", metadata={"transaction_id": transaction_id})
    reconcile_wallet(int(seller_wallet["id"]))
    return {"ok": True, "transaction_id": int(transaction_id), "refunded_cents": refund_amount}


def record_webhook_event(provider_event_id: str, event_type: str, raw: dict[str, Any], status: str = "received", error: str = "") -> dict[str, Any]:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO payment_webhook_events
            (provider_event_id, event_type, processed_at, status, error, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (provider_event_id, event_type, now if status in {"processed", "skipped"} else "", status, error[:1000], json.dumps(raw or {}, default=str)[:20000]),
        )
        event_id = cur.lastrowid
        conn.commit()
        return {"ok": True, "id": int(event_id), "duplicate": False}
    except Exception:
        conn.rollback()
        cur.execute("SELECT * FROM payment_webhook_events WHERE provider_event_id=? LIMIT 1", (provider_event_id,))
        row = cur.fetchone()
        return {"ok": True, "duplicate": True, "event": dict(row) if row else {}}
    finally:
        conn.close()


def update_webhook_event(provider_event_id: str, status: str, error: str = "") -> None:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE payment_webhook_events SET status=?, error=?, processed_at=? WHERE provider_event_id=?",
        (status, error[:1000], now_iso(), provider_event_id),
    )
    conn.commit()
    conn.close()


def payment_summary() -> dict[str, Any]:
    conn = db_service.connect()
    cur = conn.cursor()

    def scalar(sql: str, params=()):
        cur.execute(sql, params)
        row = cur.fetchone()
        return list(dict(row).values())[0] if row else 0

    summary = {
        "gross_volume_cents": scalar("SELECT COALESCE(SUM(gross_amount_cents),0) AS total FROM creator_transactions WHERE status IN ('paid','refunded','disputed')"),
        "platform_fees_cents": scalar("SELECT COALESCE(SUM(platform_fee_cents),0) AS total FROM creator_transactions WHERE status IN ('paid','refunded','disputed')"),
        "pending_seller_balance_cents": scalar("SELECT COALESCE(SUM(pending_balance_cents),0) AS total FROM creator_wallets WHERE wallet_type!='platform'"),
        "available_seller_balance_cents": scalar("SELECT COALESCE(SUM(available_balance_cents),0) AS total FROM creator_wallets WHERE wallet_type!='platform'"),
        "failed_payments": scalar("SELECT COUNT(*) AS total FROM creator_transactions WHERE status='failed'"),
        "disputes": scalar("SELECT COUNT(*) AS total FROM creator_transactions WHERE status='disputed'"),
        "refunds": scalar("SELECT COUNT(*) AS total FROM creator_transactions WHERE status='refunded'"),
        "webhook_events": scalar("SELECT COUNT(*) AS total FROM payment_webhook_events"),
        "onboarding_incomplete": scalar("SELECT COUNT(*) AS total FROM seller_payout_accounts WHERE COALESCE(payouts_enabled,0)=0 OR COALESCE(charges_enabled,0)=0"),
    }
    cur.execute("SELECT * FROM creator_transactions ORDER BY id DESC LIMIT 25")
    transactions = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM creator_wallets ORDER BY lifetime_earnings_cents DESC LIMIT 20")
    wallets = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"summary": summary, "transactions": transactions, "wallets": wallets}


def audit_log(actor_user_id: int, action: str, entity_type: str, entity_id: str | int, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None, trace_id: str = "") -> None:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payment_audit_logs
        (actor_user_id, action, entity_type, entity_id, before_json, after_json, trace_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(actor_user_id or 0), action, entity_type, str(entity_id or ""), json.dumps(before or {}, default=str), json.dumps(after or {}, default=str), trace_id or new_trace_id(), now_iso()),
    )
    conn.commit()
    conn.close()


def holding_period_days(seller_type: str) -> int:
    key = "MERCHANT_HOLD_DAYS" if seller_type == "merchant" else "TEACHER_HOLD_DAYS" if seller_type == "teacher" else "CREATOR_HOLD_DAYS"
    try:
        return max(0, int(os.getenv(key, "7" if seller_type == "merchant" else "3")))
    except Exception:
        return 7
