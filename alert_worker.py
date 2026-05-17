"""Standalone CoinPilotXAI alert worker.

Railway worker command:
    python alert_worker.py

The web app can boot without this worker, but alerts become automatic only when
this process is running.
"""

from __future__ import annotations

import logging
import os
import signal
import time

from services import alert_engine


RUNNING = True


def _handle_stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    interval = max(15, int(os.getenv("ALERT_WORKER_INTERVAL_SECONDS", "45")))
    limit = max(1, int(os.getenv("ALERT_WORKER_BATCH_LIMIT", "500")))
    try:
        import bot

        bot.init_db()
    except Exception as exc:
        logging.exception("Alert worker database initialization failed: %s", exc)
        alert_engine.record_worker_heartbeat("alert_worker", 0, 0, 1, f"init_db failed: {exc}")
    logging.info("CoinPilotXAI alert worker started interval=%s limit=%s", interval, limit)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    while RUNNING:
        try:
            result = alert_engine.evaluate_all_active_alerts(limit=limit, worker_name="alert_worker")
            logging.info(
                "Alert worker cycle checked=%s triggered=%s errors=%s latency_ms=%s",
                result.get("checked_count"),
                result.get("triggered_count"),
                result.get("error_count"),
                result.get("latency_ms"),
            )
        except Exception as exc:
            logging.exception("Alert worker cycle failed: %s", exc)
            try:
                alert_engine.record_worker_heartbeat("alert_worker", 0, 0, 1, str(exc))
            except Exception:
                logging.exception("Alert worker heartbeat failed after cycle error.")
        for _ in range(interval):
            if not RUNNING:
                break
            time.sleep(1)
    logging.info("CoinPilotXAI alert worker stopped.")


if __name__ == "__main__":
    main()
