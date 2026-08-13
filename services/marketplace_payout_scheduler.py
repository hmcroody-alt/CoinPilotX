"""Distributed-idempotent adapter from eligible settlements to canonical payouts."""
from __future__ import annotations
import time
from typing import Callable, Mapping, Any
from services import db
from services import marketplace_settlement_service as settlements
from services.business_os.payments import seller_payouts

def run_once(*, account_resolver: Callable[[str], Mapping[str, Any]],
             provider_create: Callable[[dict], Mapping[str, Any]], limit: int = 50) -> dict:
    """Schedule eligible rows and invoke the existing Stripe payout adapter.

    Callers inject the network operation. Tests use a fixture; production may
    pass a Stripe-backed callable. Database idempotency keys, not memory, fence
    duplicate replicas and provider retries.
    """
    started = time.monotonic(); settlements.ensure_schema(); conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute("""SELECT * FROM marketplace_commercial_settlements
            WHERE payout_state='eligible' AND payout_ready=1 AND blocker_code IS NULL
            ORDER BY seller_transaction_id LIMIT ?""", (max(1, min(int(limit), 200)),)).fetchall()]
    finally: conn.close()
    metrics = {"eligible_count": len(rows), "scheduled_count": 0, "paid_count": 0,
               "failed_count": 0, "duplicate_prevention": 0}
    for row in rows:
        tx_id = int(row["seller_transaction_id"]); payout_key = f"marketplace:payout:{tx_id}"
        account = dict(account_resolver(str(row["seller_id"])) or {})
        try:
            req = seller_payouts.request_payout(
                row["seller_id"], int(row["net_seller_earnings_minor"]), requested_by="marketplace_scheduler",
                payout_key=payout_key, account_status=account, currency=row["currency"])
            if req.get("duplicate"): metrics["duplicate_prevention"] += 1
            settlements.transition_payout(tx_id, "scheduled", actor="marketplace_scheduler",
                reason="canonical payout request created", idempotency_key=f"scheduled:{payout_key}")
            metrics["scheduled_count"] += 1
            payout = req["payout"]
            provider = dict(provider_create(seller_payouts.build_stripe_payout_args(payout)) or {})
            provider_id = str(provider.get("id") or provider.get("payout_id") or "")
            if not provider_id: raise RuntimeError("provider returned no payout id")
            seller_payouts.mark_payout_submitted(int(payout["id"]), stripe_payout_id=provider_id)
            conn = db.connect()
            try:
                conn.execute("UPDATE marketplace_commercial_settlements SET provider_payout_id=? WHERE seller_transaction_id=? AND payout_state='scheduled'",
                             (provider_id, tx_id)); conn.commit()
            finally: conn.close()
            # Provider submission is not payment. Stripe's payout.paid webhook
            # remains the only authority allowed to transition scheduled→paid.
        except Exception:
            metrics["failed_count"] += 1
            current = settlements.get_settlement(tx_id)
            if current and current["payout_state"] == "scheduled":
                settlements.transition_payout(tx_id, "failed", actor="marketplace_scheduler",
                    reason="provider submission failed; liability preserved", idempotency_key=f"failed:{payout_key}")
    metrics["job_duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return metrics

def apply_provider_event(provider_payout_id: str, *, paid: bool, event_id: str) -> dict:
    """Project authoritative Stripe payout outcome onto linked settlements."""
    settlements.ensure_schema(); conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute("SELECT seller_transaction_id FROM marketplace_commercial_settlements WHERE provider_payout_id=? AND payout_state='scheduled'",
                                              (provider_payout_id,)).fetchall()]
    finally: conn.close()
    changed = 0
    for row in rows:
        settlements.transition_payout(row["seller_transaction_id"], "paid" if paid else "failed",
            actor="stripe_webhook", reason="authoritative provider payout outcome",
            idempotency_key=f"provider:{event_id}:{row['seller_transaction_id']}",
            provider_reference=provider_payout_id)
        changed += 1
    return {"matched": len(rows), "changed": changed, "paid": paid}
