"""Business OS — Advertising: canonical billing events + escrow consumption.

Turns an ACCEPTED, billing-eligible impression/click (written immutably by
``events.py``) into an immutable billing decision, and — when a whole cent is due —
an overdraft-guarded double-entry posting on the canonical ledger:

    debit  ad_campaign_escrow:<campaign_id>      (the reserved budget)
    credit platform:advertising_revenue          (platform revenue accrual)

Money integrity is delegated ENTIRELY to the shared ledger
(``services.business_os.ledger.ledger``) — this module never mutates a bare
balance and creates NO second financial foundation. The escrow account is the SAME
one ``funding.py`` reserves into; the ledger's overdraft guard (escrow is not in
the allow-negative prefix set) is exactly what enforces "no overdraft / budget
exhaustion": a debit that would take escrow negative is refused, the billing event
is marked ``failed`` with ``budget_exhausted``, and the campaign's derived
``budget_exhausted`` latch is set. The platform revenue account IS allow-negative
(``platform:`` prefix) so revenue accrual is never blocked.

Determinism & correctness:

  * **Integer money only.** CPC charges the whole per-click rate. CPM charges a
    fraction of a cent per impression; the sub-cent remainder is carried in
    ``business_os_ad_spend_accumulator.accrued_millicents`` (1 cent = 1000
    milli-cents) and only whole cents are ever posted, so rounding is deterministic
    and value-preserving across many impressions. The accumulator read-modify-write
    is done under ``BEGIN IMMEDIATE`` so concurrent impressions on one campaign can
    neither double-count nor under-count.
  * **Bill exactly once.** ``business_os_ad_billing_events.idempotency_key`` is
    UNIQUE per source event, so a retried/concurrent bill of the same impression or
    click collides and is a no-op. The ledger posting is itself idempotent
    (UNIQUE ledger idempotency key), so even a crash-resume cannot double-charge.
  * **Fail closed.** No hardcoded prices: the unit price comes only from the
    versioned ``pricing`` policy; with none published, billing raises rather than
    inventing a number.
  * **Self-healing.** A billing event left ``pending`` by a crash is resumed on the
    next pass (the ledger post is idempotent), never stranded and never doubled.
"""

from __future__ import annotations

from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising.service import AdvertisingError
from services.business_os.advertising import pricing as _pricing
from services.business_os.ledger import ledger as _ledger

try:  # canonical notification adapters; import defensively (never a precondition).
    from services.business_os.advertising import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


# --- accounts ---------------------------------------------------------------
def _escrow_account(campaign_id: str) -> str:
    """Per-campaign escrow holding reserved budget. Overdraft-guarded by the ledger
    (NOT in the allow-negative prefix set) — this is what enforces no-overdraft."""
    return f"ad_campaign_escrow:{campaign_id}"


PLATFORM_REVENUE_ACCOUNT = "platform:advertising_revenue"  # allow-negative accrual

MILLICENTS_PER_CENT = 1000  # 1 cent == 1000 milli-cents (CPM sub-cent unit)

_SOURCE_TABLE = {
    "impression": "business_os_ad_impression_events",
    "click": "business_os_ad_click_events",
}
_MODEL_FOR_SOURCE = {"impression": "cpm", "click": "cpc"}


# --- small helpers ----------------------------------------------------------
def _is_unique(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "integrityerror" in name or "uniqueviolation" in name:
        return True
    return "unique" in msg or "duplicate key" in msg


def _norm_source_type(source_event_type: Any) -> str:
    t = str(source_event_type or "").strip().lower()
    if t not in _SOURCE_TABLE:
        raise AdvertisingError(
            f"Unknown source event type: {source_event_type!r}.",
            400, "bad_source_type")
    return t


def _billing_key(source_type: str, source_event_id: str) -> str:
    return f"ad_billing:{source_type}:{source_event_id}"


def _load_source_event(conn, source_type: str, event_id: str) -> Optional[dict]:
    table = _SOURCE_TABLE[source_type]
    return _svc._row_to_dict(conn.execute(
        f"SELECT * FROM {table} WHERE event_id = ?", (event_id,)).fetchone())


def _get_billing_by_key(conn, key: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_billing_events WHERE idempotency_key = ?",
        (key,)).fetchone())


def _get_accumulator(conn, campaign_id: str, currency: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_spend_accumulator "
        "WHERE campaign_id = ? AND currency = ?",
        (campaign_id, currency)).fetchone())


def _mark_source_processed(conn, source_type: str, event_id: str) -> None:
    table = _SOURCE_TABLE[source_type]
    conn.execute(
        f"UPDATE {table} SET billing_processed = 1 WHERE event_id = ?",
        (event_id,))


# --- public projection ------------------------------------------------------
def _billing_public(row: dict) -> dict:
    if row is None:
        return None
    return {
        "billing_event_id": row.get("billing_event_id"),
        "campaign_id": row.get("campaign_id"),
        "advertiser_user_id": row.get("advertiser_user_id"),
        "source_event_type": row.get("source_event_type"),
        "source_event_id": row.get("source_event_id"),
        "billing_model": row.get("billing_model"),
        "billable_quantity": row.get("billable_quantity"),
        "unit_price_cents": row.get("unit_price_cents"),
        "pricing_policy_version": row.get("pricing_policy_version"),
        "total_amount_cents": row.get("total_amount_cents"),
        "currency": row.get("currency"),
        "billing_status": row.get("billing_status"),
        "ledger_txn_reference": row.get("ledger_txn_reference"),
        "eligibility_decision": row.get("eligibility_decision"),
        "failure_reason": row.get("failure_reason"),
        "created_at": row.get("created_at"),
        "processed_at": row.get("processed_at"),
    }


# --- phase 1: claim ---------------------------------------------------------
def _claim(conn, *, key, source_type, source, model, unit_price_cents,
           policy_version, currency) -> Optional[dict]:
    """Insert the immutable billing-event row in ``pending``. Returns the claimed
    row, or None if another claimer already owns this source event (UNIQUE hit)."""
    billing_event_id = _svc._uid()
    now = _svc._now_iso()
    try:
        _svc._begin(conn)
        conn.execute(
            "INSERT INTO business_os_ad_billing_events "
            "(billing_event_id, advertiser_user_id, campaign_id, ad_set_id, "
            "creative_id, creative_version, delivery_instance_id, "
            "source_event_type, source_event_id, billing_model, billable_quantity, "
            "unit_price_cents, pricing_policy_version, total_amount_cents, "
            "accrued_millicents, currency, billing_status, idempotency_key, "
            "eligibility_decision, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, 0, ?, 'pending', "
            "?, ?, ?)",
            (
                billing_event_id, source.get("advertiser_user_id"),
                source.get("campaign_id"), source.get("ad_set_id"),
                source.get("creative_id"), source.get("creative_version"),
                source.get("delivery_id"), source_type, source.get("event_id"),
                model, unit_price_cents, policy_version, currency, key, "ok", now,
            ),
        )
        _svc._commit(conn)
    except Exception as exc:  # noqa: BLE001
        _svc._rollback(conn)
        if _is_unique(exc):
            return None
        raise
    return _get_billing_by_key(conn, key)


def _record_ineligible(conn, *, key, source_type, source, model, currency,
                       decision) -> dict:
    """Record an immutable ``ineligible`` billing decision (no ledger posting) and
    mark the source processed so it is never re-scanned."""
    billing_event_id = _svc._uid()
    now = _svc._now_iso()
    _svc._begin(conn)
    try:
        conn.execute(
            "INSERT INTO business_os_ad_billing_events "
            "(billing_event_id, advertiser_user_id, campaign_id, ad_set_id, "
            "creative_id, creative_version, delivery_instance_id, "
            "source_event_type, source_event_id, billing_model, billable_quantity, "
            "unit_price_cents, total_amount_cents, accrued_millicents, currency, "
            "billing_status, idempotency_key, eligibility_decision, created_at, "
            "processed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, 'ineligible', "
            "?, ?, ?, ?)",
            (
                billing_event_id, source.get("advertiser_user_id"),
                source.get("campaign_id"), source.get("ad_set_id"),
                source.get("creative_id"), source.get("creative_version"),
                source.get("delivery_id"), source_type, source.get("event_id"),
                model, currency, key, decision, now, now,
            ),
        )
        _mark_source_processed(conn, source_type, source.get("event_id"))
        _svc._commit(conn)
    except Exception as exc:  # noqa: BLE001
        _svc._rollback(conn)
        if _is_unique(exc):
            return _get_billing_by_key(conn, key)
        raise
    return _get_billing_by_key(conn, key)


# --- phase 2: atomic accumulator RMW; pin the flush to THIS billing event ----
def _pin_flush(conn, billing: dict) -> dict:
    """Atomically advance the campaign accumulator and pin the whole-cent flush
    amount onto the (still pending) billing event. Serialized by BEGIN IMMEDIATE so
    concurrent impressions on one campaign neither double- nor under-count."""
    campaign_id = billing.get("campaign_id")
    currency = billing.get("currency")
    model = billing.get("billing_model")
    unit = int(billing.get("unit_price_cents") or 0)
    now = _svc._now_iso()
    _svc._begin(conn)
    try:
        acc = _get_accumulator(conn, campaign_id, currency)
        if acc is None:
            conn.execute(
                "INSERT INTO business_os_ad_spend_accumulator "
                "(campaign_id, currency, accrued_millicents, billed_cents, "
                "impressions_billed, clicks_billed, budget_exhausted, updated_at) "
                "VALUES (?, ?, 0, 0, 0, 0, 0, ?)",
                (campaign_id, currency, now))
            accrued = 0
        else:
            accrued = int(acc.get("accrued_millicents") or 0)

        if model == "cpm":
            contribution = unit  # milli-cents for one impression (cents per 1000)
            total_milli = accrued + contribution
            flush_cents = total_milli // MILLICENTS_PER_CENT
            new_remainder = total_milli % MILLICENTS_PER_CENT
            impr_inc, click_inc = 1, 0
        else:  # cpc — whole cents per click, no sub-cent carry
            contribution = unit * MILLICENTS_PER_CENT
            flush_cents = unit
            new_remainder = accrued
            impr_inc, click_inc = 0, 1

        # Advance the accumulator: consume the milli-cents now, optimistically count
        # the whole-cent flush. A ledger refusal (budget exhausted) reverses the
        # billed_cents count in _finalize_failed.
        conn.execute(
            "UPDATE business_os_ad_spend_accumulator "
            "SET accrued_millicents = ?, billed_cents = billed_cents + ?, "
            "impressions_billed = impressions_billed + ?, "
            "clicks_billed = clicks_billed + ?, updated_at = ? "
            "WHERE campaign_id = ? AND currency = ?",
            (new_remainder, flush_cents, impr_inc, click_inc, now,
             campaign_id, currency))
        conn.execute(
            "UPDATE business_os_ad_billing_events "
            "SET total_amount_cents = ?, accrued_millicents = ? "
            "WHERE billing_event_id = ?",
            (flush_cents, contribution, billing.get("billing_event_id")))
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    return _get_billing_by_key(conn, billing.get("idempotency_key"))


# --- phase 4: finalize ------------------------------------------------------
def _finalize_processed(conn, billing, source_type, txn_reference) -> dict:
    now = _svc._now_iso()
    _svc._begin(conn)
    try:
        conn.execute(
            "UPDATE business_os_ad_billing_events "
            "SET billing_status = 'processed', ledger_txn_reference = ?, "
            "processed_at = ?, eligibility_decision = 'ok' "
            "WHERE billing_event_id = ?",
            (txn_reference, now, billing.get("billing_event_id")))
        _mark_source_processed(conn, source_type, billing.get("source_event_id"))
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    return _get_billing_by_key(conn, billing.get("idempotency_key"))


def _finalize_failed(conn, billing, source_type, reason) -> dict:
    """Budget exhausted: reverse the optimistic billed-cents count, latch
    ``budget_exhausted``, mark the source processed (never overdraft, never retry
    forever). No ledger posting happened, so escrow is untouched."""
    now = _svc._now_iso()
    flush = int(billing.get("total_amount_cents") or 0)
    # Read the latch BEFORE we set it, so we notify only on the 0->1 transition
    # (one alert per exhaustion, not once per subsequent refused event).
    prev_latch = _scalar_latch(conn, billing.get("campaign_id"),
                               billing.get("currency"))
    _svc._begin(conn)
    try:
        conn.execute(
            "UPDATE business_os_ad_spend_accumulator "
            "SET billed_cents = billed_cents - ?, budget_exhausted = 1, "
            "updated_at = ? WHERE campaign_id = ? AND currency = ?",
            (flush, now, billing.get("campaign_id"), billing.get("currency")))
        conn.execute(
            "UPDATE business_os_ad_billing_events "
            "SET billing_status = 'failed', total_amount_cents = 0, "
            "failure_reason = ?, eligibility_decision = 'budget_exhausted', "
            "processed_at = ? WHERE billing_event_id = ?",
            (str(reason)[:500], now, billing.get("billing_event_id")))
        _mark_source_processed(conn, source_type, billing.get("source_event_id"))
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    # Emit AFTER commit — side effect only, never rolls the billing decision back.
    if _notify is not None and prev_latch == 0:
        uid = billing.get("advertiser_user_id")
        cid = billing.get("campaign_id")
        _notify.notify_budget_exhausted(uid, cid)
        _notify.notify_billing_failure(uid, cid, reason=str(reason)[:300])
    return _get_billing_by_key(conn, billing.get("idempotency_key"))


def _scalar_latch(conn, campaign_id, currency) -> int:
    """Current ``budget_exhausted`` latch (0/1) for the accumulator, 0 if absent."""
    row = conn.execute(
        "SELECT budget_exhausted FROM business_os_ad_spend_accumulator "
        "WHERE campaign_id = ? AND currency = ?",
        (campaign_id, currency)).fetchone()
    if row is None:
        return 0
    v = row[0] if not hasattr(row, "keys") else row["budget_exhausted"]
    return int(v or 0)


def _post_and_finalize(conn, billing, source_type) -> dict:
    """Post the whole-cent ledger charge (if any) and finalize. The ledger posting
    is idempotent (keyed by billing_event_id) so a crash-resume cannot double-charge."""
    flush = int(billing.get("total_amount_cents") or 0)
    if flush <= 0:
        # Sub-cent CPM impression: nothing to post, but it IS processed and the
        # milli-cent is carried in the accumulator.
        return _finalize_processed(conn, billing, source_type, None)
    try:
        txn = _ledger.post_entry(
            idempotency_key=f"ad_billing:{billing.get('billing_event_id')}",
            actor="system:ad_billing",
            amount_cents=flush,
            currency=billing.get("currency"),
            entry_type="ad_billing_charge",
            source=_escrow_account(billing.get("campaign_id")),
            destination=PLATFORM_REVENUE_ACCOUNT,
            reason=f"advertising {billing.get('billing_model')} charge",
            related_object=f"ad_campaign:{billing.get('campaign_id')}",
            provider_reference=billing.get("source_event_id") or "",
            metadata={
                "billing_event_id": billing.get("billing_event_id"),
                "source_event_type": source_type,
                "source_event_id": billing.get("source_event_id"),
            })
    except _ledger.LedgerError as exc:
        return _finalize_failed(conn, billing, source_type, f"budget_exhausted: {exc}")
    return _finalize_processed(conn, billing, source_type,
                               txn.get("transaction_id"))


# --- entry point: bill ONE source event -------------------------------------
def bill_event(source_event_type: Any, source_event_id: Any, *,
               currency: str = "usd", conn=None) -> dict:
    """Bill ONE accepted impression/click, idempotently. Safe to call repeatedly.

    Returns the immutable billing-event projection with ``duplicate`` set when the
    event was already billed. Ineligible (fraud/self-view) source events yield an
    ``ineligible`` billing record and are never charged. A whole-cent charge debits
    escrow and credits platform revenue via the ledger; an escrow overdraft is
    refused and recorded as ``failed`` / ``budget_exhausted`` (no overdraft ever).
    """
    _svc._require_enabled()
    source_type = _norm_source_type(source_event_type)
    event_id = _svc._sid(source_event_id)
    model = _MODEL_FOR_SOURCE[source_type]
    currency = str(currency or "usd").strip().lower()
    key = _billing_key(source_type, event_id)

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # Fast path: already billed / claimed?
        existing = _get_billing_by_key(conn, key)
        if existing is not None and existing.get("billing_status") != "pending":
            out = _billing_public(existing); out["duplicate"] = True
            return out

        source = _load_source_event(conn, source_type, event_id)
        if source is None:
            raise AdvertisingError("Source event not found.", 404, "not_found")

        if existing is not None and existing.get("billing_status") == "pending":
            # Resume a crash-interrupted bill (ledger post is idempotent).
            billing = existing
            if int(billing.get("total_amount_cents") or 0) == 0 and \
                    int(billing.get("accrued_millicents") or 0) == 0:
                billing = _pin_flush(conn, billing)
            out = _billing_public(_post_and_finalize(conn, billing, source_type))
            out["duplicate"] = True
            return out

        # Eligibility gate — copied from the immutable source event, not the client.
        if not source.get("billing_eligible"):
            out = _billing_public(_record_ineligible(
                conn, key=key, source_type=source_type, source=source,
                model=model, currency=currency, decision="not_billing_eligible"))
            out["duplicate"] = False
            return out

        # Server-authoritative price from the versioned policy (fail closed).
        policy = _pricing.get_active_policy(model, currency, conn=conn)
        unit_price = int(policy.get("unit_price_cents"))
        policy_version = int(policy.get("effective_version"))

        billing = _claim(conn, key=key, source_type=source_type, source=source,
                         model=model, unit_price_cents=unit_price,
                         policy_version=policy_version, currency=currency)
        if billing is None:
            # Lost the claim race — someone else owns it; return their record.
            other = _get_billing_by_key(conn, key)
            out = _billing_public(other); out["duplicate"] = True
            return out

        billing = _pin_flush(conn, billing)
        out = _billing_public(_post_and_finalize(conn, billing, source_type))
        out["duplicate"] = False
        return out
    finally:
        if owned:
            conn.close()


# --- batch driver -----------------------------------------------------------
def process_pending(*, campaign_id: Optional[str] = None, currency: str = "usd",
                    limit: int = 500) -> dict:
    """Bill all eligible, not-yet-processed impression + click events (optionally
    scoped to one campaign). Returns a summary. Each event is billed in its own
    connection/transaction; a failure on one does not abort the batch."""
    _svc._require_enabled()
    currency = str(currency or "usd").strip().lower()
    try:
        lim = max(1, min(int(limit), 5000))
    except Exception:
        lim = 500
    summary = {"processed": 0, "charged_cents": 0, "ineligible": 0,
               "failed": 0, "duplicate": 0, "events": 0}
    conn = db.connect()
    try:
        rows = []
        for source_type, table in _SOURCE_TABLE.items():
            clause = "WHERE billing_processed = 0"
            params = []
            if campaign_id is not None:
                clause += " AND campaign_id = ?"; params.append(campaign_id)
            cur = conn.execute(
                f"SELECT event_id FROM {table} {clause} "
                "ORDER BY event_at ASC LIMIT ?", tuple(params + [lim]))
            for r in cur.fetchall():
                rows.append((source_type, r[0]))
    finally:
        conn.close()

    for source_type, event_id in rows:
        res = bill_event(source_type, event_id, currency=currency)
        summary["events"] += 1
        status = res.get("billing_status")
        if res.get("duplicate"):
            summary["duplicate"] += 1
        if status == "processed":
            summary["processed"] += 1
            summary["charged_cents"] += int(res.get("total_amount_cents") or 0)
        elif status == "ineligible":
            summary["ineligible"] += 1
        elif status == "failed":
            summary["failed"] += 1
    return summary


# --- derived budget-exhaustion state ----------------------------------------
def budget_exhausted(campaign_id: str, currency: str = "usd", *, conn=None) -> bool:
    """Derived: True when the accumulator latch is set OR escrow is empty. Never a
    stored authority on the campaign — recomputed from the ledger + accumulator."""
    currency = str(currency or "usd").strip().lower()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        acc = _get_accumulator(conn, campaign_id, currency)
        if acc is not None and int(acc.get("budget_exhausted") or 0) == 1:
            return True
        try:
            return _ledger.get_balance(_escrow_account(campaign_id), currency) <= 0
        except Exception:
            return False
    finally:
        if owned:
            conn.close()
