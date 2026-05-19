"""CoinPilotXAI Pulse Feed background worker."""

from __future__ import annotations

import logging
import os
import time

import bot
from services import pulse_feed_engine


WORKER_NAME = "pulse_worker"
SLEEP_SECONDS = int(os.getenv("PULSE_WORKER_SLEEP_SECONDS", "20"))
BATCH_SIZE = int(os.getenv("PULSE_WORKER_BATCH_SIZE", "12"))


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    bot.init_db()
    logging.info(
        "PULSE_WORKER_START database_url=%s openai_key_present=%s media_provider=%s",
        bool(os.getenv("DATABASE_URL")),
        bool(os.getenv("OPENAI_API_KEY")),
        os.getenv("MEDIA_STORAGE_PROVIDER", "local"),
    )
    while True:
        try:
            result = pulse_feed_engine.process_pending_jobs(BATCH_SIZE)
            bot.record_worker_heartbeat(
                WORKER_NAME,
                "healthy",
                metadata={
                    "processed": result.get("processed", 0),
                    "failed": result.get("failed", 0),
                    "batch_size": BATCH_SIZE,
                    "openai_key_present": bool(os.getenv("OPENAI_API_KEY")),
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
