"""UNDX background worker for the Railway coinpilotx-undx-worker service.

This worker keeps the UNDX Intelligence Router service warm and reports safe
provider configuration status. It does not call providers, read files, run
commands, or execute repository actions.
"""

from __future__ import annotations

import logging
import os
import time

import bot
import undx_router


WORKER_NAME = "coinpilotx-undx-worker"
SLEEP_SECONDS = int(os.getenv("UNDX_WORKER_SLEEP_SECONDS", "60"))


def _status_payload() -> dict:
    providers = undx_router.provider_status()
    return {
        "router_enabled": undx_router.router_enabled(),
        "multi_model_mode": undx_router.multi_model_mode(),
        "default_provider": undx_router.default_provider(),
        "openai_key_present": providers.get("openai", False),
        "claude_key_present": providers.get("claude", False),
        "gemini_key_present": providers.get("gemini", False),
        "deepseek_key_present": providers.get("deepseek", False),
        "groq_key_present": providers.get("groq", False),
    }


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    bot.init_db()
    logging.info("UNDX_WORKER_START service=%s status=%s", WORKER_NAME, _status_payload())
    while True:
        try:
            undx_router.log_provider_status()
            bot.record_worker_heartbeat(WORKER_NAME, "healthy", metadata=_status_payload())
        except Exception as exc:
            logging.exception("UNDX_WORKER_HEARTBEAT_FAILED error=%s", exc)
            try:
                bot.record_worker_heartbeat(WORKER_NAME, "error", str(exc), _status_payload())
            except Exception:
                logging.exception("UNDX worker error heartbeat failed")
        time.sleep(max(15, SLEEP_SECONDS))


if __name__ == "__main__":
    main()
