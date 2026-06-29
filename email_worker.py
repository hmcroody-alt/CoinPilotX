"""Bounded persistent email-outbox worker for Railway or local operation."""

from __future__ import annotations

import logging
import os
import signal
import time

os.environ.setdefault("COINPILOTX_INIT_DB_ON_IMPORT", "0")
os.environ.setdefault("EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED", "0")

import bot  # noqa: E402


STOP_REQUESTED = False


def _stop(*_args):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    interval = max(2, min(int(os.getenv("EMAIL_WORKER_INTERVAL_SECONDS", "10") or 10), 300))
    batch_size = max(1, min(int(os.getenv("EMAIL_WORKER_BATCH_SIZE", "20") or 20), 50))
    bot.init_db()
    logging.info("EMAIL_WORKER_STARTED interval=%s batch_size=%s", interval, batch_size)
    while not STOP_REQUESTED:
        try:
            result = bot.process_email_delivery_jobs(limit=batch_size)
            if result.get("attempted"):
                logging.info("EMAIL_WORKER_CYCLE result=%s", result)
        except Exception:
            logging.exception("EMAIL_WORKER_CYCLE_FAILED")
        deadline = time.monotonic() + interval
        while not STOP_REQUESTED and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    logging.info("EMAIL_WORKER_STOPPED")


if __name__ == "__main__":
    main()
