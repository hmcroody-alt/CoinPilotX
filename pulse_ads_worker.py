"""PulseSoc Ads worker — the `pulsesoc-ads-worker` Railway service.

Start command:
    python pulse_ads_worker.py

One process, five logical domains on independent cadences (split into separate
services later by moving a cycle call into its own entrypoint — no rewrites):

    every cycle        drain pulse_ad_jobs queue (events/attribution/billing/
                       reporting jobs enqueued by the web tier)
    every cycle        operations sweep (activate/complete/pause campaigns)
    every ~5 minutes   attribution cycle (idempotent last-click purchases)
    every ~10 minutes  billing reconciliation (wallet vs ledger, report-only)
    every ~5 minutes   reporting cycle (daily aggregate precompute)

The web app boots and serves ads without this worker; the worker only makes
scheduling, attribution, reconciliation and dashboards automatic. It never
charges wallets — the synchronous delivery path owns money and is idempotent.
"""

from __future__ import annotations

import logging
import os
import signal
import time

import bot
from services import pulse_ads_worker_service as engine


WORKER_NAME = "ads_worker"
SLEEP_SECONDS = max(5, int(os.getenv("ADS_WORKER_SLEEP_SECONDS", "20")))
BATCH_SIZE = int(os.getenv("ADS_WORKER_BATCH_SIZE", "20"))
ATTRIBUTION_EVERY = max(60, int(os.getenv("ADS_WORKER_ATTRIBUTION_SECONDS", "300")))
BILLING_EVERY = max(60, int(os.getenv("ADS_WORKER_BILLING_SECONDS", "600")))
REPORTING_EVERY = max(60, int(os.getenv("ADS_WORKER_REPORTING_SECONDS", "300")))

RUNNING = True


def _handle_stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def _conn():
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    return conn


def run_cycle(state: dict) -> dict:
    """One worker cycle. Restart-safe: every step re-derives its work from the
    database, and jobs stuck in 'processing' from a killed worker are the only
    residue — they are re-queued here after a visibility timeout."""
    summary: dict = {}
    now = time.monotonic()
    conn = _conn()
    try:
        # Recover jobs orphaned by a crash mid-processing (>10 min old).
        recovered = engine.recover_orphaned_jobs(conn)
        if recovered:
            summary["recovered_orphans"] = recovered

        summary["jobs"] = engine.process_pending_jobs(conn, BATCH_SIZE)
        summary["operations"] = engine.run_operations_cycle(conn)
        if now - state.get("last_attribution", 0) >= ATTRIBUTION_EVERY:
            summary["attribution"] = engine.run_attribution_cycle(conn)
            state["last_attribution"] = now
        if now - state.get("last_billing", 0) >= BILLING_EVERY:
            summary["billing"] = engine.run_billing_cycle(conn)
            state["last_billing"] = now
        if now - state.get("last_reporting", 0) >= REPORTING_EVERY:
            summary["reporting"] = engine.run_reporting_cycle(conn)
            state["last_reporting"] = now
        summary["queue_health"] = engine.queue_health(conn)
    finally:
        conn.close()
    return summary


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    bot.init_db()
    conn = _conn()
    try:
        engine.ensure_schema(conn)
    finally:
        conn.close()
    logging.info(
        "ADS_WORKER_START database_url=%s redis_url=%s sleep=%ss batch=%s",
        bool(os.getenv("DATABASE_URL")),
        bool(os.getenv("REDIS_URL")),
        SLEEP_SECONDS,
        BATCH_SIZE,
    )
    state: dict = {}
    while RUNNING:
        try:
            summary = run_cycle(state)
            jobs = summary.get("jobs", {})
            bot.record_worker_heartbeat(
                WORKER_NAME,
                "healthy",
                metadata={
                    "jobs_processed": jobs.get("processed", 0),
                    "jobs_retried": jobs.get("retried", 0),
                    "jobs_dead": jobs.get("dead", 0),
                    "dead_letter_total": summary.get("queue_health", {}).get("dead_letter_total", 0),
                    "operations": summary.get("operations", {}),
                    "batch_size": BATCH_SIZE,
                },
            )
            if any(
                summary.get(k)
                for k in ("attribution", "billing", "reporting", "recovered_orphans")
            ) or jobs.get("claimed"):
                logging.info("ADS_WORKER_CYCLE %s", summary)
        except Exception as exc:
            logging.exception("ADS_WORKER_CYCLE_FAILED error=%s", exc)
            try:
                bot.record_worker_heartbeat(WORKER_NAME, "error", str(exc))
            except Exception:
                logging.exception("Ads worker heartbeat failed")
        # Graceful shutdown: finish the cycle, then exit between cycles.
        for _ in range(SLEEP_SECONDS):
            if not RUNNING:
                break
            time.sleep(1)
    logging.info("ADS_WORKER_STOPPED gracefully")


if __name__ == "__main__":
    main()
