"""Business OS — Advertising: authoritative spend projection + billing reconciliation.

Two read-only surfaces built ENTIRELY on top of the immutable records written by
``billing.py`` and the shared canonical ledger. Nothing here moves money, mutates a
billing event, or "repairs" a discrepancy — reconciliation *reports* drift and leaves
correction to a human/operational decision (spec §1: "Reconciliation reports
discrepancies without silent repair").

Spend is derived from THREE independent, immutable sources that must agree:

  1. **Billing events** — SUM(total_amount_cents) of ``processed`` rows. This is the
     per-event record of what was decided.
  2. **Spend accumulator** — ``billed_cents`` (whole cents flushed) plus the carried
     sub-cent ``accrued_millicents`` remainder.
  3. **Canonical ledger** — SUM of ``ad_billing_charge`` transactions posted against
     ``ad_campaign:<cid>`` (debit escrow / credit platform revenue). This is where the
     money actually moved.

When (1) and (3) disagree, real money and the billing record have drifted and a human
must look — ``reconcile_campaign`` surfaces exactly that, never hides it.

Metric confidence follows the reporting contract: spend/impressions/clicks billed are
**Confirmed** (they come from immutable, ledger-backed records); the budget-exhaustion
runway forecast is **Modeled** (a linear extrapolation from observed spend, returned as
null when there is not enough history to model — never fabricated, never shown as 0).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising import billing as _billing
from services.business_os.ledger import ledger as _ledger


LEDGER_CHARGE_ENTRY_TYPE = "ad_billing_charge"


def _norm_currency(currency: Any) -> str:
    return str(currency or "usd").strip().lower()


def _campaign_related_object(campaign_id: str) -> str:
    return f"ad_campaign:{campaign_id}"


def _scalar(conn, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    v = row[0] if not hasattr(row, "keys") else row[list(row.keys())[0]]
    return int(v or 0)


# --- billing-event roll-up --------------------------------------------------
def _billing_rollup(conn, campaign_id: str, currency: str) -> dict:
    """Per-status counts + processed-spend from the immutable billing-event log."""
    counts = {"processed": 0, "pending": 0, "ineligible": 0,
              "failed": 0, "reversed": 0}
    cur = conn.execute(
        "SELECT billing_status, COUNT(*) AS n, "
        "COALESCE(SUM(total_amount_cents), 0) AS cents "
        "FROM business_os_ad_billing_events "
        "WHERE campaign_id = ? AND currency = ? GROUP BY billing_status",
        (campaign_id, currency))
    processed_cents = 0
    for r in cur.fetchall():
        d = _svc._row_to_dict(r)
        status = d.get("billing_status")
        if status in counts:
            counts[status] = int(d.get("n") or 0)
        if status == "processed":
            processed_cents = int(d.get("cents") or 0)
    return {"counts": counts, "processed_cents": processed_cents}


def _ledger_charged_cents(conn, campaign_id: str, currency: str) -> int:
    """Whole cents actually posted to the ledger for this campaign's ad billing."""
    return _scalar(
        conn,
        "SELECT COALESCE(SUM(amount_cents), 0) FROM ledger_transactions "
        "WHERE entry_type = ? AND related_object = ? AND currency = ? "
        "AND status = 'posted'",
        (LEDGER_CHARGE_ENTRY_TYPE, _campaign_related_object(campaign_id), currency))


# --- public: authoritative spend view ---------------------------------------
def get_campaign_spend(campaign_id: Any, currency: Any = "usd", *,
                       conn=None) -> dict:
    """Return the authoritative spend view for one campaign.

    Read-only; every figure is derived live from immutable records, never a stored
    aggregate that could drift. ``spent_cents`` is the whole cents posted to the
    ledger (the money that actually moved); ``accrued_millicents`` is the carried
    sub-cent remainder not yet crossing a whole-cent boundary.
    """
    campaign_id = _svc._sid(campaign_id)
    cur = _norm_currency(currency)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        funding = _svc._row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_campaign_funding WHERE campaign_id = ?",
            (campaign_id,)).fetchone())
        acc = _svc._row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_spend_accumulator "
            "WHERE campaign_id = ? AND currency = ?",
            (campaign_id, cur)).fetchone())
        roll = _billing_rollup(conn, campaign_id, cur)
        ledger_cents = _ledger_charged_cents(conn, campaign_id, cur)

        budget_cents = None if funding is None else funding.get("budget_cents")
        reserved_cents = None if funding is None else \
            funding.get("reserved_amount_cents")
        remaining_escrow = _ledger.get_balance(
            _billing._escrow_account(campaign_id), cur)

        spent_cents = ledger_cents  # authoritative: what actually moved
        accrued_millicents = 0 if acc is None else int(acc.get("accrued_millicents") or 0)
        impressions_billed = 0 if acc is None else int(acc.get("impressions_billed") or 0)
        clicks_billed = 0 if acc is None else int(acc.get("clicks_billed") or 0)

        pct_budget_spent = None
        if budget_cents:
            try:
                pct_budget_spent = round(100.0 * spent_cents / int(budget_cents), 2)
            except Exception:
                pct_budget_spent = None

        return {
            "campaign_id": campaign_id,
            "currency": cur,
            "budget_cents": budget_cents,
            "reserved_amount_cents": reserved_cents,
            "spent_cents": spent_cents,                 # Confirmed
            "accrued_millicents": accrued_millicents,   # Confirmed (sub-cent carry)
            "remaining_escrow_cents": remaining_escrow,  # Confirmed (live ledger)
            "impressions_billed": impressions_billed,   # Confirmed
            "clicks_billed": clicks_billed,             # Confirmed
            "pct_budget_spent": pct_budget_spent,
            "budget_exhausted": _billing.budget_exhausted(campaign_id, cur, conn=conn),
            "billing_event_counts": roll["counts"],
            "confidence": {
                "spent_cents": "Confirmed",
                "impressions_billed": "Confirmed",
                "clicks_billed": "Confirmed",
                "budget_exhausted": "Confirmed",
            },
        }
    finally:
        if owned:
            conn.close()


# --- public: modeled runway forecast ----------------------------------------
def project_budget_exhaustion(campaign_id: Any, currency: Any = "usd", *,
                              conn=None) -> dict:
    """Linear runway forecast (MODELED). Returns null projection fields when there is
    not enough spend history to model — it never invents a date or shows zero as if it
    were real. Uses the first→last processed billing event span as the spend window.
    """
    campaign_id = _svc._sid(campaign_id)
    cur = _norm_currency(currency)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        span = _svc._row_to_dict(conn.execute(
            "SELECT MIN(processed_at) AS first_at, MAX(processed_at) AS last_at, "
            "COALESCE(SUM(total_amount_cents), 0) AS spent, COUNT(*) AS n "
            "FROM business_os_ad_billing_events "
            "WHERE campaign_id = ? AND currency = ? AND billing_status = 'processed' "
            "AND total_amount_cents > 0",
            (campaign_id, cur)).fetchone())
        remaining_escrow = _ledger.get_balance(
            _billing._escrow_account(campaign_id), cur)

        out = {
            "campaign_id": campaign_id,
            "currency": cur,
            "confidence": "Modeled",
            "remaining_escrow_cents": remaining_escrow,
            "spend_per_hour_cents": None,
            "projected_hours_remaining": None,
            "projected_exhaustion_at": None,
            "basis": "insufficient_history",
        }
        first_at = (span or {}).get("first_at")
        last_at = (span or {}).get("last_at")
        spent = int((span or {}).get("spent") or 0)
        if not first_at or not last_at or spent <= 0:
            return out
        try:
            t0 = datetime.fromisoformat(first_at)
            t1 = datetime.fromisoformat(last_at)
        except Exception:
            return out
        hours = (t1 - t0).total_seconds() / 3600.0
        if hours <= 0:
            out["basis"] = "single_point"
            return out
        rate = spent / hours  # cents per hour
        out["spend_per_hour_cents"] = round(rate, 4)
        out["basis"] = "linear_extrapolation"
        if rate > 0 and remaining_escrow > 0:
            out["projected_hours_remaining"] = round(remaining_escrow / rate, 2)
        elif remaining_escrow <= 0:
            out["projected_hours_remaining"] = 0.0
        return out
    finally:
        if owned:
            conn.close()


# --- public: reconciliation -------------------------------------------------
def reconcile_campaign(campaign_id: Any, currency: Any = "usd", *,
                       conn=None) -> dict:
    """Cross-check the three immutable spend sources for ONE campaign and REPORT any
    drift. Never repairs. ``consistent`` is True only when billing events, the
    accumulator, and the ledger all agree on whole-cent spend.
    """
    campaign_id = _svc._sid(campaign_id)
    cur = _norm_currency(currency)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        roll = _billing_rollup(conn, campaign_id, cur)
        billing_cents = roll["processed_cents"]
        acc = _svc._row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_spend_accumulator "
            "WHERE campaign_id = ? AND currency = ?",
            (campaign_id, cur)).fetchone())
        accumulator_cents = 0 if acc is None else int(acc.get("billed_cents") or 0)
        ledger_cents = _ledger_charged_cents(conn, campaign_id, cur)

        discrepancies = []
        if billing_cents != ledger_cents:
            discrepancies.append({
                "kind": "billing_vs_ledger",
                "billing_events_cents": billing_cents,
                "ledger_charged_cents": ledger_cents,
                "delta_cents": billing_cents - ledger_cents,
            })
        if accumulator_cents != ledger_cents:
            discrepancies.append({
                "kind": "accumulator_vs_ledger",
                "accumulator_billed_cents": accumulator_cents,
                "ledger_charged_cents": ledger_cents,
                "delta_cents": accumulator_cents - ledger_cents,
            })
        return {
            "campaign_id": campaign_id,
            "currency": cur,
            "billing_events_cents": billing_cents,
            "accumulator_billed_cents": accumulator_cents,
            "ledger_charged_cents": ledger_cents,
            "accrued_millicents": 0 if acc is None else int(acc.get("accrued_millicents") or 0),
            "billing_event_counts": roll["counts"],
            "consistent": not discrepancies,
            "discrepancies": discrepancies,
        }
    finally:
        if owned:
            conn.close()


def reconcile_all(currency: Any = "usd", *, limit: int = 1000, conn=None) -> dict:
    """Reconcile every campaign that has at least one billing event. Returns a
    summary + the list of campaigns whose sources disagree (empty when all agree).
    """
    cur = _norm_currency(currency)
    try:
        lim = max(1, min(int(limit), 10000))
    except Exception:
        lim = 1000
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT campaign_id FROM business_os_ad_billing_events "
            "WHERE currency = ? LIMIT ?", (cur, lim)).fetchall()
        campaign_ids = [(r[0] if not hasattr(r, "keys") else r["campaign_id"])
                        for r in rows]
        inconsistent = []
        checked = 0
        for cid in campaign_ids:
            rep = reconcile_campaign(cid, cur, conn=conn)
            checked += 1
            if not rep["consistent"]:
                inconsistent.append(rep)
        return {
            "currency": cur,
            "campaigns_checked": checked,
            "inconsistent_count": len(inconsistent),
            "consistent": not inconsistent,
            "inconsistent": inconsistent,
        }
    finally:
        if owned:
            conn.close()
