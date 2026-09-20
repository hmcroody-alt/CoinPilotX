"""Distributed-idempotent adapter from eligible settlements to canonical payouts."""
from __future__ import annotations
import logging
import time
from typing import Callable, Mapping, Any
from services import db
from services import marketplace_settlement_service as settlements
from services.business_os.payments import incidents, seller_payouts

logger = logging.getLogger(__name__)

#: Used when a caller does not supply one. The worker owns the clamped, env-fed
#: policy; these are the same numbers, restated so `run_once` is callable
#: directly (tests, a one-off admin run) without the worker's configuration.
DEFAULT_RETRY_POLICY = {"max_attempts": 5, "base_seconds": 900, "max_seconds": 21600}

#: Fenced because the platform cannot proceed on its own. `place_hold` writes
#: this into `blocker_code`, which removes the row from both work selectors and
#: makes `transition_payout` refuse to schedule it — so an operator's
#: `release_hold` is the only way back. Money stays in `seller_payout_pending`;
#: nothing is written off.
EXHAUSTED_BLOCKER = "payout_retries_exhausted"
UNRECOVERABLE_BLOCKER = "payout_failure_unrecoverable"

def run_once(*, account_resolver: Callable[[str], Mapping[str, Any]],
             provider_transfer: Callable[[dict], Mapping[str, Any]],
             provider_create: Callable[[dict], Mapping[str, Any]], limit: int = 50,
             retry_policy: Mapping[str, Any] | None = None) -> dict:
    """Schedule eligible rows, transfer the seller's cut, then pay it out.

    Two distinct money movements, in order: `provider_transfer` moves funds from
    the platform balance to the connected account, and `provider_create` moves
    that balance to the seller's bank. Under separate charges and transfers the
    second draws on nothing until the first lands, so a failed transfer must not
    be followed by a payout attempt.

    Two passes, not one. The first is the original: settlements that have never
    been submitted. The second re-offers settlements whose earlier attempt failed
    for a reason that classified as transient and whose backoff has elapsed —
    previously an impossibility, because `failed` had no selector reading it and
    the money simply stopped there. Both passes run the same submission body, so
    a retry cannot diverge from a first attempt.

    Retries deliberately reuse the settlement's original `payout_key`. The Stripe
    idempotency keys are derived from it (`seller_transfer:<key>`,
    `seller_payout:<key>`), so an attempt that failed *after* Stripe accepted the
    call returns the existing object instead of creating a second transfer. A
    fresh key per attempt would be the version of this feature that pays sellers
    twice.

    Callers inject the network operations. Tests use fixtures; production passes
    Stripe-backed callables. Database idempotency keys, not memory, fence
    duplicate replicas and provider retries.
    """
    started = time.monotonic(); settlements.ensure_schema()
    policy = {**DEFAULT_RETRY_POLICY, **dict(retry_policy or {})}
    batch = max(1, min(int(limit), 200)); conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute("""SELECT * FROM marketplace_commercial_settlements
            WHERE payout_state='eligible' AND payout_ready=1 AND blocker_code IS NULL
            ORDER BY seller_transaction_id LIMIT ?""", (batch,)).fetchall()]
    finally: conn.close()
    retries = settlements.retryable_settlements(limit=batch)
    metrics = {"eligible_count": len(rows), "retry_count": len(retries), "scheduled_count": 0,
               "transferred_count": 0, "paid_count": 0, "failed_count": 0,
               "retried_count": 0, "exhausted_count": 0, "seller_action_count": 0,
               "duplicate_prevention": 0}
    for row in rows + retries:
        _submit(row, account_resolver=account_resolver, provider_transfer=provider_transfer,
                provider_create=provider_create, policy=policy, metrics=metrics)
    metrics["job_duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return metrics

def _submit(row: Mapping[str, Any], *, account_resolver, provider_transfer, provider_create,
            policy: Mapping[str, Any], metrics: dict) -> None:
    """One settlement, one attempt: request, transfer, pay out."""
    tx_id = int(row["seller_transaction_id"]); payout_key = f"marketplace:payout:{tx_id}"
    attempts = int(row.get("payout_attempt_count") or 0)
    if attempts: metrics["retried_count"] += 1
    # The first attempt's transition key is the historical one; a retry needs its
    # own, because `transition_payout` dedupes on the key and would otherwise
    # report the retry as a duplicate and leave the row sitting in `failed`.
    schedule_key = f"scheduled:{payout_key}" if not attempts else f"retry:{payout_key}:{attempts}"
    account = dict(account_resolver(str(row["seller_id"])) or {})
    try:
        req = seller_payouts.request_payout(
            row["seller_id"], int(row["net_seller_earnings_minor"]), requested_by="marketplace_scheduler",
            payout_key=payout_key, account_status=account, currency=row["currency"])
        if req.get("duplicate"): metrics["duplicate_prevention"] += 1
        payout = req["payout"]
        # A canonical payout row that Stripe has already terminated cannot be
        # resubmitted under its own key, and minting a fresh one would mint a
        # second transfer. Not this function's decision to make: stop, and let
        # the failure handler fence it for an operator.
        if payout.get("status") in seller_payouts.TERMINAL_STATUSES:
            raise _TerminalPayout(f"canonical payout is {payout.get('status')}; cannot resubmit")
        settlements.transition_payout(tx_id, "scheduled", actor="marketplace_scheduler",
            reason="canonical payout request created", idempotency_key=schedule_key)
        metrics["scheduled_count"] += 1
        transfer = dict(provider_transfer(seller_payouts.build_stripe_transfer_args(
            payout, transfer_group=str(row.get("transfer_group") or row["order_id"]))) or {})
        transfer_id = str(transfer.get("id") or transfer.get("transfer_id") or "")
        if not transfer_id: raise RuntimeError("provider returned no transfer id")
        # Recorded here rather than with the payout id below, because between
        # these two lines the platform has irreversibly moved money to a
        # connected account. If the payout call fails, that fact still has to be
        # written down — a refund later needs to know a transfer exists before it
        # can decide whether to reverse it.
        seller_payouts.record_transfer_id(int(payout["id"]), transfer_id, actor="marketplace_scheduler")
        metrics["transferred_count"] += 1
        provider = dict(provider_create(seller_payouts.build_stripe_payout_args(payout)) or {})
        provider_id = str(provider.get("id") or provider.get("payout_id") or "")
        if not provider_id: raise RuntimeError("provider returned no payout id")
        seller_payouts.mark_payout_submitted(int(payout["id"]), stripe_payout_id=provider_id,
                                             stripe_transfer_id=transfer_id)
        conn = db.connect()
        try:
            conn.execute("UPDATE marketplace_commercial_settlements SET provider_payout_id=? WHERE seller_transaction_id=? AND payout_state='scheduled'",
                         (provider_id, tx_id)); conn.commit()
        finally: conn.close()
        settlements.clear_payout_retry_schedule(tx_id)
        # Provider submission is not payment. Stripe's payout.paid webhook
        # remains the only authority allowed to transition scheduled→paid.
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
        metrics["failed_count"] += 1
        _handle_failure(tx_id, payout_key, row, exc, policy=policy, metrics=metrics)

class _TerminalPayout(RuntimeError):
    """The canonical payout row is terminal; resubmission would duplicate money."""

def _handle_failure(tx_id: int, payout_key: str, row: Mapping[str, Any], exc: Exception, *,
                    policy: Mapping[str, Any], metrics: dict) -> None:
    """Classify one failed attempt and give it the outcome its class deserves.

    Three outcomes, and the difference between them is what happens *next*, not
    how loudly it is logged:

    * transient, budget remaining → record the attempt with a future attempt
      time. Nobody is told; a retry that works is not news.
    * the seller's bank details → tell the seller, because they are the only
      party who can change the answer, and fence the row so it stops being
      offered.
    * anything else, including exhaustion and anything unclassified → fence the
      row and open an incident. The funds stay fenced in `seller_payout_pending`
      exactly where they were; nothing is written off and nothing is returned to
      the payable balance behind an operator's back.
    """
    classified = seller_payouts.classify_payout_failure(exc)
    if isinstance(exc, _TerminalPayout):
        # Not a provider failure at all — a structural refusal. Never retryable,
        # whatever the exception happens to look like.
        classified = {"failure_class": seller_payouts.PERMANENT, "failure_code": "payout_terminal",
                      "retryable": False, "seller_action": False}
    outcome = settlements.record_payout_failure(
        tx_id, failure_code=classified["failure_code"], failure_class=classified["failure_class"],
        retryable=classified["retryable"], max_attempts=int(policy["max_attempts"]),
        base_seconds=int(policy["base_seconds"]), max_seconds=int(policy["max_seconds"]))
    current = settlements.get_settlement(tx_id)
    if current and current["payout_state"] == "scheduled":
        settlements.transition_payout(tx_id, "failed", actor="marketplace_scheduler",
            reason=f"provider submission failed ({classified['failure_code']}); liability preserved",
            idempotency_key=f"failed:{payout_key}:{outcome['attempt']}")
    will_retry = bool(classified["retryable"]) and not outcome["exhausted"]
    if will_retry:
        logger.warning("MARKETPLACE_PAYOUT_RETRY tx=%s attempt=%s code=%s next=%s",
                       tx_id, outcome["attempt"], classified["failure_code"], outcome["next_attempt_at"])
        return
    blocker = EXHAUSTED_BLOCKER if outcome["exhausted"] else UNRECOVERABLE_BLOCKER
    if outcome["exhausted"]: metrics["exhausted_count"] += 1
    _fence(tx_id, payout_key, blocker=blocker, attempt=outcome["attempt"])
    if classified["seller_action"]:
        metrics["seller_action_count"] += 1
        _notify_seller(row, classified, attempt=outcome["attempt"])
    _open_incident(tx_id, row, classified, outcome, blocker=blocker)

def _fence(tx_id: int, payout_key: str, *, blocker: str, attempt: int) -> None:
    """Stop offering this settlement, without moving its money anywhere.

    `place_hold` is the existing mechanism and it does both halves: it writes
    `blocker_code`, which removes the row from every work selector and makes
    `transition_payout` refuse to schedule it, and it moves the state to `held`,
    which `release_hold` already knows how to undo once an operator has fixed
    whatever the incident describes.
    """
    try:
        settlements.place_hold(tx_id, actor="marketplace_scheduler", reason_code=blocker,
                               idempotency_key=f"{blocker}:{payout_key}:{attempt}")
    except Exception as exc:  # noqa: BLE001 - the incident below is the backstop
        logger.warning("MARKETPLACE_PAYOUT_FENCE_FAILED tx=%s blocker=%s error=%s", tx_id, blocker, exc)

def _notify_seller(row: Mapping[str, Any], classified: Mapping[str, Any], *, attempt: int) -> None:
    """Tell the seller their bank details refused the payout. Never raises.

    Uses the payments notification engine the rest of this domain already uses;
    a second notification path for one event would be a second thing to keep
    correct. `emit` swallows its own failures by contract, and the import is
    local so a scheduler cycle does not depend on the notification stack being
    importable.
    """
    try:
        from services import payments_notifications
        seller_id = int(str(row.get("seller_id") or "").strip() or 0)
        if seller_id <= 0: return
        payments_notifications.emit(payments_notifications.PAYOUT_FAILED, seller_id, {
            "payout_id": f"marketplace:payout:{row.get('seller_transaction_id')}",
            "amount_cents": int(row.get("net_seller_earnings_minor") or 0),
            "currency": str(row.get("currency") or "usd"),
            "order_reference": str(row.get("order_id") or ""),
            "failure_reason": str(classified.get("failure_code") or ""),
        })
    except Exception as exc:  # noqa: BLE001 - a payout must not fail on a notification
        logger.warning("MARKETPLACE_PAYOUT_NOTIFY_FAILED tx=%s error=%s",
                       row.get("seller_transaction_id"), exc)

def _open_incident(tx_id: int, row: Mapping[str, Any], classified: Mapping[str, Any],
                   outcome: Mapping[str, Any], *, blocker: str) -> None:
    """Make a stranded payout an operator's problem rather than a log line.

    `SUSPENSE_FUNDS_HELD` is the honest type: the seller's money is fenced in
    `seller_payout_pending` and is going nowhere until a human acts. Severity is
    critical when the budget ran out — that means the platform tried everything
    it knows how to try — and warning when the class was never retryable, where
    the next step is known even if nobody has taken it yet.
    """
    try:
        incidents.open_incident(
            incidents.SUSPENSE_FUNDS_HELD, domain="seller_payments",
            severity="critical" if outcome.get("exhausted") else "warning",
            summary=(f"Marketplace payout for settlement {tx_id} stopped after "
                     f"{outcome.get('attempt')} attempt(s): {classified.get('failure_code')} "
                     f"({classified.get('failure_class')})"),
            details={"seller_transaction_id": tx_id, "seller_id": str(row.get("seller_id") or ""),
                     "order_id": str(row.get("order_id") or ""),
                     "amount_minor": int(row.get("net_seller_earnings_minor") or 0),
                     "currency": str(row.get("currency") or ""),
                     "failure_code": classified.get("failure_code"),
                     "failure_class": classified.get("failure_class"),
                     "attempts": outcome.get("attempt"), "blocker_code": blocker,
                     "seller_notified": bool(classified.get("seller_action"))},
            related_object=f"marketplace_settlement:{tx_id}",
            incident_key=f"{incidents.SUSPENSE_FUNDS_HELD}:marketplace_payout:{tx_id}:{blocker}")
    except Exception as exc:  # noqa: BLE001 - never let reporting break the cycle
        logger.warning("MARKETPLACE_PAYOUT_INCIDENT_FAILED tx=%s error=%s", tx_id, exc)

def run_refund_recovery_once(*, provider_reversal: Callable[[dict], Mapping[str, Any]],
                             limit: int = 50) -> dict:
    """Claw back the seller's share of refunds that can still be clawed back.

    Exactly one case reaches this function: a refund whose transfer landed and
    whose payout has not. `apply_refund` decided that at refund time and stamped
    it, and the stamp is what puts a row in the queue — but the stamp is only
    ever a claim about the past, and the payout keeps moving after it is made.
    So the case is re-derived here against a freshly read payout, and a row that
    no longer classifies as reversible is skipped rather than reversed. Without
    that re-check a payout that settled between the refund and this cycle would
    be clawed back by a process still working from the older answer, which is
    the case that either fails or overdraws the seller's account.

    Everything else was already handled without a Stripe call: a refund before
    the transfer needs none, and a refund after the payout cannot have one — that
    one is a recorded debt with an incident against it, and it is deliberately
    not in this queue. Draining a queue is an automatic act, and an
    unrecoverable debt must not be settled automatically.
    """
    started = time.monotonic(); rows = settlements.refund_recovery_queue(limit=limit)
    metrics = {"candidate_count": len(rows), "reversed_count": 0, "failed_count": 0,
               "skipped_count": 0, "reversed_minor": 0}
    for row in rows:
        tx_id = int(row["seller_transaction_id"])
        owed = int(row.get("refund_recovery_minor") or 0)
        done = int(row.get("refund_reversed_minor") or 0)
        outstanding = owed - done
        payout = seller_payouts.get_payout(payout_key=f"marketplace:payout:{tx_id}")
        # Re-read the payout and re-check the case. The stamp says what was true
        # at refund time; if the payout has settled since, the money has left and
        # a reversal would either fail or overdraw the seller's account.
        if outstanding <= 0 or settlements.classify_refund_recovery(
                payout, seller_reversal_minor=outstanding) != settlements.RECOVERY_TRANSFER_REVERSIBLE:
            metrics["skipped_count"] += 1
            logger.warning("MARKETPLACE_REFUND_RECOVERY_MOVED tx=%s status=%s",
                           tx_id, (payout or {}).get("status"))
            continue
        try:
            args = seller_payouts.build_stripe_transfer_reversal_args(
                payout, amount_cents=outstanding, reversal_key=f"{tx_id}:{done}:{owed}")
            result = dict(provider_reversal(args) or {})
            reversal_id = str(result.get("id") or result.get("reversal_id") or "")
            if not reversal_id: raise RuntimeError("provider returned no reversal id")
            settlements.record_refund_reversal(tx_id, reversal_id, owed)
            metrics["reversed_count"] += 1; metrics["reversed_minor"] += outstanding
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            metrics["failed_count"] += 1
            logger.warning("MARKETPLACE_REFUND_RECOVERY_FAILED tx=%s error=%s", tx_id, exc)
            _open_recovery_failure(tx_id, row, exc)
    metrics["job_duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return metrics

def _open_recovery_failure(tx_id: int, row: Mapping[str, Any], exc: Exception) -> None:
    """A reversal that would not go through is a debt, and gets said so."""
    try:
        incidents.open_incident(
            incidents.NEGATIVE_BALANCE_DETECTED, domain="seller_payments", severity="critical",
            summary=(f"Transfer reversal for settlement {tx_id} failed; "
                     f"{row.get('refund_recovery_minor')} minor remains owed by the seller"),
            details={"seller_transaction_id": tx_id, "seller_id": str(row.get("seller_id") or ""),
                     "order_id": str(row.get("order_id") or ""),
                     "recoverable_minor": int(row.get("refund_recovery_minor") or 0),
                     "currency": str(row.get("currency") or ""), "error": str(exc)[:300]},
            related_object=f"marketplace_settlement:{tx_id}",
            incident_key=f"{incidents.NEGATIVE_BALANCE_DETECTED}:reversal_failed:{tx_id}")
    except Exception as incident_exc:  # noqa: BLE001 - reporting is the last resort
        logger.warning("MARKETPLACE_REFUND_RECOVERY_INCIDENT_FAILED tx=%s error=%s",
                       tx_id, incident_exc)

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
