"""The two unattended steps between a shipped order and an eligible payout.

Three things have to happen in order before a seller can be paid: an order has
to reach the buyer, a confirmed delivery has to clear its hold, and the payout
scheduler has to move the money. The third has had a governed caller for a
while — :mod:`services.marketplace_payout_worker` — and the first two have had
none, which is why nothing was ever paid: every piece of the chain worked and no
piece was joined to the next.

This module is that joint, and nothing else. It holds no opinion about which
orders may advance or which settlements may be released; those answers live in
:mod:`services.marketplace_order_fulfillment` and
:mod:`services.marketplace_settlement_service`. What is added here is scheduling
and the flags around it, so that there is exactly one implementation of each
decision in the codebase.

Neither cycle moves money. The fulfillment sweep advances an order's state and
the eligibility sweep grants permission to pay; both mutation gates on the
payout worker still stand between ``eligible`` and a Stripe transfer. That is
why these are gated more lightly than the payout cycle — but they are still
gated, because a deployment should begin doing something new because someone
decided to, not because a commit landed.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from services import marketplace_order_fulfillment as order_fulfillment
from services import marketplace_settlement_service as settlements

FULFILLMENT_SWEEP_ENABLED_ENV_VAR = "MARKETPLACE_FULFILLMENT_SWEEP_ENABLED"
SETTLEMENT_SWEEP_ENABLED_ENV_VAR = "MARKETPLACE_SETTLEMENT_SWEEP_ENABLED"
INTERVAL_ENV_VAR = "MARKETPLACE_RELEASE_CYCLE_SECONDS"

#: Neither step is latency sensitive. A timeout measured in hours does not need
#: to be noticed within a minute, and a hold that has just elapsed can wait five
#: more. The floor is what matters: both sweeps scan a table per cycle.
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 3600

BATCH_LIMIT = 200

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "f", "no", "n", "off"})


def _env_flag(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    return default


def fulfillment_sweep_enabled() -> bool:
    return _env_flag(FULFILLMENT_SWEEP_ENABLED_ENV_VAR, default=False)


def settlement_sweep_enabled() -> bool:
    return _env_flag(SETTLEMENT_SWEEP_ENABLED_ENV_VAR, default=False)


def interval_seconds() -> int:
    raw = (os.getenv(INTERVAL_ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(value, MAX_INTERVAL_SECONDS))


def run_cycle(*, now: datetime | None = None) -> dict:
    """One pass of both sweeps, in the order the money travels.

    Fulfillment first, because an order it advances to ``delivered`` opens a
    settlement hold that the eligibility sweep may then be able to clear in the
    same pass. The reverse order would work too but would always cost an extra
    interval, and there is no reason to make a seller wait one.

    Each sweep's failure is contained: a broken fulfillment sweep must not stop
    settlements that are already delivered and past their hold from being
    released.
    """
    outcome: dict = {"fulfillment": None, "settlement": None}
    if fulfillment_sweep_enabled():
        try:
            outcome["fulfillment"] = order_fulfillment.sweep_auto_advance(
                now=now, limit=BATCH_LIMIT)
        except Exception as exc:
            logging.exception("FULFILLMENT_SWEEP_FAILED error=%s", exc)
            outcome["fulfillment"] = {"error": str(exc)[:500]}
    if settlement_sweep_enabled():
        try:
            outcome["settlement"] = settlements.sweep_eligibility(
                now=now, limit=BATCH_LIMIT)
        except Exception as exc:
            logging.exception("SETTLEMENT_SWEEP_FAILED error=%s", exc)
            outcome["settlement"] = {"error": str(exc)[:500]}
    return outcome


def run_release_cycle_if_due(state: dict) -> dict | None:
    """Run one cycle if its own monotonic deadline has passed, else ``None``.

    Monotonic rather than a cycle count, for the reason the reservation sweep
    documents: a host loop's real period is ``sleep + however long the host
    took``, so counting cycles lets a busy host stretch the interval silently.
    The deadline advances in ``finally`` so a cycle that raises waits a full
    interval rather than retrying on every host tick.
    """
    if not (fulfillment_sweep_enabled() or settlement_sweep_enabled()):
        return None

    interval = interval_seconds()
    due_at = state.get("release_cycle_due_at")
    if due_at is not None and time.monotonic() < due_at:
        return None

    try:
        outcome = run_cycle()
    except Exception as exc:
        logging.exception("RELEASE_CYCLE_FAILED interval=%s error=%s", interval, exc)
        outcome = {"error": str(exc)[:500]}
    finally:
        state["release_cycle_due_at"] = time.monotonic() + interval

    logging.info("RELEASE_CYCLE interval=%s outcome=%s", interval, outcome)
    state["release_cycle_last"] = _cycle_metrics(outcome)
    return outcome


def _cycle_metrics(outcome: dict) -> dict:
    fulfillment = outcome.get("fulfillment") or {}
    settlement = outcome.get("settlement") or {}
    return {
        "last_release_cycle_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_orders_considered": fulfillment.get("considered", 0),
        "last_orders_advanced": fulfillment.get("advanced", 0),
        "last_orders_refused": fulfillment.get("refused", 0),
        "last_deliveries_settled": fulfillment.get("settled", 0),
        "last_settlements_considered": settlement.get("considered", 0),
        "last_settlements_released": settlement.get("released", 0),
        "last_settlements_blocked": settlement.get("blocked", 0),
        # The tally of *why* nothing moved. Without it a deployment where the
        # sweep is broken looks exactly like one where nothing is due.
        "last_settlement_reasons": settlement.get("reasons", {}),
        "last_release_cycle_error": outcome.get("error")
        or fulfillment.get("error") or settlement.get("error"),
    }


def heartbeat_metadata(state: dict) -> dict:
    """Release fields for the host worker's heartbeat, read from ``state``.

    ``record_worker_heartbeat`` replaces ``metadata_json`` wholesale and this
    cycle runs on roughly one host tick in fifteen, so reporting only the
    current tick would blank the fields in between — which reads identically to
    a cycle that never ran.
    """
    if not (fulfillment_sweep_enabled() or settlement_sweep_enabled()):
        return {"release_cycle_enabled": False}
    return {
        "release_cycle_enabled": True,
        "release_cycle_interval": interval_seconds(),
        "fulfillment_sweep_enabled": fulfillment_sweep_enabled(),
        "settlement_sweep_enabled": settlement_sweep_enabled(),
        **state.get("release_cycle_last", {}),
    }
