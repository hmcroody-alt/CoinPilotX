"""CoinPlotXAI Pulse Feed background worker."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import bot
from services import pulse_ai, pulse_feed_engine
from services import marketplace_reservation_sweeper as reservation_sweeper


WORKER_NAME = "pulse_worker"
SLEEP_SECONDS = int(os.getenv("PULSE_WORKER_SLEEP_SECONDS", "20"))
BATCH_SIZE = int(os.getenv("PULSE_WORKER_BATCH_SIZE", "12"))

# --------------------------------------------------------------------------
# Marketplace reservation sweep
#
# This worker hosts the sweep; it does not implement it. Every decision about
# what a reservation means — whether Stripe settled it, whether the stock may
# be returned, which reason is written — lives in
# ``services/marketplace_reservation_sweeper.py`` and the shared settlement
# path beneath it. What is added here is scheduling and nothing else, so that
# there is exactly one implementation of release in the codebase.
#
# The sweep runs on its own cadence, deliberately decoupled from the feed
# loop. The feed loop wakes every ~20 seconds because feed jobs are latency
# sensitive; a reservation sweep is not, and it costs up to one Stripe read
# per expired candidate. Riding the feed cadence would multiply provider
# traffic by fifteen for no benefit, so the sweep keeps a monotonic deadline
# and simply declines most cycles.
# --------------------------------------------------------------------------

#: One canonical cadence setting. Clamped to 1-5 minutes: below a minute the
#: sweep starts to behave like the feed loop it was separated from, and above
#: five minutes stranded stock sits longer than the reservation TTL warrants.
SWEEP_SECONDS_ENV_VAR = "MARKETPLACE_RESERVATION_SWEEP_SECONDS"
DEFAULT_SWEEP_SECONDS = 300
MIN_SWEEP_SECONDS = 60
MAX_SWEEP_SECONDS = 300

#: Both flags default to the non-acting value. A worker booted with no
#: configuration at all must not begin mutating inventory, and a typo in a
#: Railway variable must not be the thing that turns mutation on.
SWEEP_ENABLED_ENV_VAR = "MARKETPLACE_RESERVATION_SWEEPER_ENABLED"
SWEEP_DRY_RUN_ENV_VAR = "MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN"

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "f", "no", "n", "off"})


def _env_flag(name: str, *, default: bool) -> bool:
    """Parse a boolean environment variable, falling back on anything unclear.

    Unset, blank and unrecognised values all return ``default`` rather than
    raising or guessing. The defaults are chosen so that "unclear" always
    resolves to the safe direction: an unparseable
    ``MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN`` leaves the sweep in dry run.
    """
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    return default


def sweep_enabled() -> bool:
    """Whether the sweep runs at all. Off unless explicitly switched on."""
    return _env_flag(SWEEP_ENABLED_ENV_VAR, default=False)


def sweep_dry_run() -> bool:
    """Whether the sweep only reports. On unless explicitly switched off.

    This is the flag that stands between a scheduling bug and released
    inventory, so it fails closed in every ambiguous case.
    """
    return _env_flag(SWEEP_DRY_RUN_ENV_VAR, default=True)


def sweep_interval_seconds() -> int:
    """Sweep cadence in seconds, clamped so a typo cannot produce a hot loop."""
    raw = (os.getenv(SWEEP_SECONDS_ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_SWEEP_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SWEEP_SECONDS
    return max(MIN_SWEEP_SECONDS, min(value, MAX_SWEEP_SECONDS))


def run_reservation_sweep_if_due(state: dict) -> dict | None:
    """Run one sweep if its own deadline has passed, else return ``None``.

    ``state`` carries the monotonic deadline across cycles. Monotonic rather
    than a cycle counter because the feed loop's real period is
    ``sleep + however long the feed took``, which is variable — counting cycles
    would make the sweep cadence drift with feed load, and a slow feed would
    silently stretch the interval.

    Every failure mode is contained here. The sweep gets its own connection so
    a failed sweep cannot roll back committed feed work, and the deadline is
    advanced in ``finally`` so a sweep that raises waits a full interval before
    trying again instead of retrying on every 20-second cycle.
    """
    if not sweep_enabled():
        return None

    interval = sweep_interval_seconds()
    now = time.monotonic()
    due_at = state.get("reservation_sweep_due_at")
    if due_at is not None and now < due_at:
        return None

    dry_run = sweep_dry_run()
    summary = None
    committed = False
    try:
        conn = None
        try:
            conn = bot.db()
            conn.row_factory = bot.sqlite3.Row
            cur = conn.cursor()
            summary = reservation_sweeper.run_reservation_expiry_sweep(
                cur,
                dry_run=dry_run,
                limit=reservation_sweeper.batch_limit(),
            )
            conn.commit()
            committed = True
        finally:
            # ``is not None`` rather than truthiness: a DBAPI connection object
            # is free to define __bool__/__len__, and a falsy-but-open handle
            # would be leaked by a bare ``if conn``.
            if conn is not None:
                conn.close()
    except Exception as exc:
        # A sweep failure is an incident for the sweep, not for the worker.
        # The feed loop keeps running and the next interval tries again.
        logging.exception(
            "RESERVATION_SWEEP_CYCLE_FAILED dry_run=%s interval=%s committed=%s error=%s",
            dry_run, interval, committed, exc,
        )
        if committed and summary is not None:
            # The sweep ran and its transaction committed; only teardown
            # failed. Reporting status=error with the counts zeroed would
            # understate real mutation, and ``released`` is precisely the
            # number the dry-run-to-mutate GO/NO-GO decision reads. Keep the
            # measured counts and mark the cycle degraded instead.
            outcome = {**summary, "status": "degraded",
                       "error": str(exc)[:500], "dry_run": dry_run}
        else:
            outcome = {"status": "error", "error": str(exc)[:500], "dry_run": dry_run}
        state["reservation_sweep_last"] = _sweep_metrics(outcome)
        return outcome
    finally:
        state["reservation_sweep_due_at"] = time.monotonic() + interval

    logging.info(
        "RESERVATION_SWEEP_CYCLE dry_run=%s interval=%s summary=%s",
        dry_run, interval, summary,
    )
    outcome = {"status": "ok", **summary}
    state["reservation_sweep_last"] = _sweep_metrics(outcome)
    return outcome


def _sweep_metrics(sweep: dict) -> dict:
    """Flatten one sweep outcome into heartbeat fields."""
    failed = sweep.get("failed", 0) or 0
    status = sweep.get("status")
    if status in ("error", "degraded"):
        last_status = status
    else:
        last_status = "degraded" if failed else "ok"
    return {
        "last_sweep_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_sweep_status": last_status,
        # Why the status alone is not enough: a sweep blocked by a missing
        # lifecycle column, a sweep whose candidate query broke, and a sweep
        # that released some rows and failed on others all arrive here as
        # `degraded` with zero or partial counts. Only the reason separates
        # "this database needs a migration" from "some rows misbehaved", and
        # the first needs an operator while the second usually resolves itself.
        # ``None`` on a healthy cycle, so its presence is itself the signal.
        "last_sweep_reason": sweep.get("reason"),
        "last_sweep_dry_run": bool(sweep.get("dry_run")),
        "last_sweep_candidates": sweep.get("candidates", 0),
        "last_sweep_released": sweep.get("released", 0),
        "last_sweep_would_release": sweep.get("would_release", 0),
        "last_sweep_deferred": sweep.get("deferred", 0),
        "last_sweep_failed": failed,
        "last_sweep_needs_attention": sweep.get("needs_attention", 0),
        "last_sweep_duration_ms": sweep.get("duration_ms", 0),
    }


def sweep_heartbeat_metadata(state: dict) -> dict:
    """Sweep fields for the heartbeat, read from ``state``, not from this cycle.

    The sweep runs on roughly one cycle in fifteen, and
    ``record_worker_heartbeat`` replaces ``metadata_json`` wholesale rather than
    merging it. Reporting only the current cycle's sweep would therefore erase
    ``last_sweep_at`` on the fourteen cycles in between, and an operator
    checking the heartbeat at an arbitrary moment would almost always see
    nothing — which reads identically to a sweep that never ran. Holding the
    last outcome in ``state`` keeps the answer on every heartbeat.

    Reuses ``worker_heartbeats``, the table every other worker already reports
    into, rather than introducing a second place to look during an incident.
    """
    if not sweep_enabled():
        return {"reservation_sweep_enabled": False}
    return {"reservation_sweep_enabled": True,
            "reservation_sweep_interval": sweep_interval_seconds(),
            **state.get("reservation_sweep_last", {})}


def main():
    # Railway workers have no HTTP surface, so stdout is their readiness and
    # incident evidence. Force the root handler in case importing the web app
    # configured logging first, and keep each line immediately visible.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    logging.info("PULSE_WORKER_BOOT sha=%s", os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown")[:12])
    bot.init_db()
    logging.info(
        "PULSE_WORKER_START database_url=%s openai_key_present=%s media_provider=%s",
        bool(os.getenv("DATABASE_URL")),
        bool(os.getenv("OPENAI_API_KEY")),
        os.getenv("MEDIA_STORAGE_PROVIDER", "local"),
    )
    # Announce the sweep's configuration once at boot so a deployment's
    # behaviour is readable from the first ten lines of its logs, rather than
    # inferred from whether releases start appearing.
    logging.info(
        "RESERVATION_SWEEP_CONFIG enabled=%s dry_run=%s interval=%s batch=%s "
        "stripe_key_present=%s",
        sweep_enabled(),
        sweep_dry_run(),
        sweep_interval_seconds(),
        reservation_sweeper.batch_limit(),
        bool(os.getenv("STRIPE_SECRET_KEY")),
    )
    state: dict = {}
    while True:
        try:
            result = pulse_feed_engine.process_pending_jobs(BATCH_SIZE)
            conn = None
            try:
                conn = bot.db()
                conn.row_factory = bot.sqlite3.Row
                cur = conn.cursor()
                space_ai_result = pulse_ai.run_due_space_ai_posts(
                    cur,
                    bot.PULSE_SPACES,
                    pulse_create_post=pulse_feed_engine.create_post,
                    force=False,
                    limit=24,
                )
                conn.commit()
            finally:
                if conn:
                    conn.close()
            run_reservation_sweep_if_due(state)
            bot.record_worker_heartbeat(
                WORKER_NAME,
                "healthy",
                metadata={
                    "processed": result.get("processed", 0),
                    "failed": result.get("failed", 0),
                    "space_ai_posts": space_ai_result.get("ran", 0),
                    "batch_size": BATCH_SIZE,
                    "openai_key_present": bool(os.getenv("OPENAI_API_KEY")),
                    **sweep_heartbeat_metadata(state),
                },
            )
        except Exception as exc:
            logging.exception("PULSE_WORKER_CYCLE_FAILED error=%s", exc)
            try:
                bot.record_worker_heartbeat(WORKER_NAME, "error", str(exc), {"openai_key_present": bool(os.getenv("OPENAI_API_KEY"))})
            except Exception:
                logging.exception("Pulse worker heartbeat failed")
        time.sleep(max(5, SLEEP_SECONDS))


if __name__ == "__main__":
    main()
