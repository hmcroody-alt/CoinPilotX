"""An unattended caller for the payments reconciliation engine.

:mod:`services.business_os.payments.reconciliation` has been able to find every
kind of financial drift the platform can observe for some time, and until now the
only thing that ever asked it was an admin pressing a button. A detector nobody
runs is not a detector; it is a report you can generate after you already know
something is wrong, which is the moment it stops being useful.

This module is the caller, and nothing else. It holds no opinion about what
counts as drift, opens no incident and repairs nothing — those decisions stay in
the engine, which is deliberately append-only and detect-only. What is added here
is scheduling, the flags around it, and one derived judgement: whether the last
run finished with a critical incident still open. That judgement is in the
heartbeat rather than in a notification, because an incident is already the
platform's durable record of a financial discrepancy and a second, lossier copy
of it in a chat message would be one more thing to keep in sync.

The engine is read-mostly but not free: it scans balance tables, the webhook
inbox and every settlement row. That is why the interval floor here is minutes
rather than seconds, and why the flag defaults to off — a deployment should start
scanning because someone decided it should, not because a commit landed.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

RECONCILIATION_ENABLED_ENV_VAR = "PAYMENTS_RECONCILIATION_ENABLED"
INTERVAL_ENV_VAR = "PAYMENTS_RECONCILIATION_SECONDS"

#: Hourly. Every finding this engine produces describes a condition measured in
#: hours or days — a stale payout, a stuck webhook, a hold that did not release —
#: so a shorter period would cost table scans to learn nothing new. The floor
#: exists because the sweep is the most expensive read in the worker.
DEFAULT_INTERVAL_SECONDS = 3600
MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 86400

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "f", "no", "n", "off"})


def _env_flag(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    return default


def reconciliation_enabled() -> bool:
    """Read per call, and off unless a deployment says otherwise."""
    return _env_flag(RECONCILIATION_ENABLED_ENV_VAR, default=False)


def interval_seconds() -> int:
    """Seconds between sweeps, clamped so a typo cannot produce a hot loop.

    An unparseable value falls back to the default rather than to the floor: a
    misconfigured deployment should reconcile at the intended rate, not at the
    most expensive one the clamp permits.
    """
    raw = (os.getenv(INTERVAL_ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(value, MAX_INTERVAL_SECONDS))


def run_cycle() -> dict:
    """One full sweep, with the engine's own per-check containment relied upon.

    :func:`reconciliation.run_all` already turns a failing check into a recorded
    error plus a RECONCILIATION_FAILURE incident, so a failure that reaches here
    is the sweep itself failing — a dead database, or the run-history insert
    being refused. That is worth reporting as its own thing rather than being
    folded into the per-check errors it is not.
    """
    from services.business_os.payments import reconciliation

    try:
        return reconciliation.run_all()
    except Exception as exc:
        logging.exception("PAYMENTS_RECONCILIATION_SWEEP_FAILED error=%s", exc)
        return {"error": str(exc)[:500]}


def run_reconciliation_cycle_if_due(state: dict) -> dict | None:
    """Run one sweep if its own monotonic deadline has passed, else ``None``.

    Monotonic rather than a cycle count, for the reason the reservation sweep
    documents: a host loop's real period is ``sleep + however long the host
    took``, so counting ticks lets a busy host stretch the interval silently.
    The deadline advances in ``finally`` so a sweep that raises waits a full
    interval instead of retrying on every host tick — which, for a sweep that
    scans several tables, is the difference between a failure and an outage.
    """
    if not reconciliation_enabled():
        return None

    interval = interval_seconds()
    due_at = state.get("reconciliation_due_at")
    if due_at is not None and time.monotonic() < due_at:
        return None

    try:
        summary = run_cycle()
    finally:
        state["reconciliation_due_at"] = time.monotonic() + interval

    metrics = _cycle_metrics(summary)
    state["reconciliation_last"] = metrics

    critical = metrics["last_reconciliation_critical_open"]
    if critical:
        # The one line in this module that raises its voice. An open critical
        # incident means money is somewhere it should not be and the platform
        # already knows; a log at INFO would put that fact in the same stream as
        # the sweeps that found nothing.
        logging.error(
            "PAYMENTS_RECONCILIATION_CRITICAL open=%s incidents_touched=%s",
            critical, metrics["last_reconciliation_incidents"])
    else:
        logging.info("PAYMENTS_RECONCILIATION interval=%s metrics=%s", interval, metrics)
    return summary


def _cycle_metrics(summary: dict) -> dict:
    checks = summary.get("checks") or {}
    marketplace = checks.get("marketplace_settlements") or {}
    return {
        "last_reconciliation_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_reconciliation_incidents": summary.get("incidents_opened_or_refreshed", 0),
        "last_reconciliation_check_errors": summary.get("check_errors", 0),
        # Standing state rather than this run's output, so a sweep that finds
        # nothing new while an old critical is still open does not read as clean.
        "last_reconciliation_critical_open": summary.get("open_critical_incidents", 0),
        # The marketplace chain broken out, because it is the part of the
        # platform this worker also schedules: a rising count here and a flat
        # release cycle is the signature of a sweep that is running and a chain
        # that is not moving.
        "last_marketplace_stuck_scheduled": marketplace.get(
            "scheduled_without_provider_id", 0),
        "last_marketplace_hold_not_released": marketplace.get(
            "protection_hold_not_released", 0),
        "last_marketplace_never_scheduled": marketplace.get(
            "eligible_but_never_scheduled", 0),
        "last_marketplace_snapshot_mismatches": marketplace.get(
            "snapshot_mismatches", 0),
        # True when the arithmetic invariant was checked on only part of the
        # settlement table, which is the one way this report can be clean and
        # wrong at the same time.
        "last_marketplace_scan_truncated": bool(marketplace.get("scan_truncated")),
        "last_reconciliation_error": summary.get("error"),
    }


def heartbeat_metadata(state: dict) -> dict:
    """Reconciliation fields for the host worker's heartbeat, read from ``state``.

    ``record_worker_heartbeat`` replaces ``metadata_json`` wholesale, and this
    sweep runs on roughly one host tick in a few hundred, so reporting only the
    current tick would blank every field in between — which reads identically to
    a sweep that has never run.
    """
    if not reconciliation_enabled():
        return {"reconciliation_enabled": False}
    return {
        "reconciliation_enabled": True,
        "reconciliation_interval": interval_seconds(),
        **state.get("reconciliation_last", {}),
    }
