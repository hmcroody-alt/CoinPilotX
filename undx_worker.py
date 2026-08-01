"""UNDX background worker for the Railway coinpilotx-undx-worker service.

This worker keeps the UNDX Intelligence Router service warm and reports safe
provider configuration status. It does not call providers, read files, run
commands, or execute repository actions.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading

import bot
import undx_router


WORKER_NAME = "coinpilotx-undx-worker"
SLEEP_SECONDS = int(os.getenv("UNDX_WORKER_SLEEP_SECONDS", "60"))
STOP_EVENT = threading.Event()


def _build_sha() -> str:
    """Return immutable release lineage without exposing any configuration value."""
    return (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("APP_BUILD_SHA") or "unknown")[:40]


def _request_shutdown(signum, _frame) -> None:
    logging.info("UNDX_WORKER_STOP_REQUESTED service=%s signal=%s", WORKER_NAME, signum)
    STOP_EVENT.set()


def _status_payload() -> dict:
    providers = undx_router.provider_status()
    return {
        "worker_type": "undx",
        "runtime_version": os.getenv("UNDX_CONFIG_VERSION", "default"),
        "deployed_sha": _build_sha(),
        "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT") or "unknown",
        "agent_enabled": os.getenv("UNDX_AGENT_ENABLED", "false").lower() == "true",
        "reads_enabled": os.getenv("UNDX_AGENT_READS_ENABLED", "false").lower() == "true",
        "writes_enabled": os.getenv("UNDX_AGENT_WRITES_ENABLED", "false").lower() == "true",
        "writes_disabled": os.getenv("UNDX_AGENT_DISABLE_WRITES", "true").lower() == "true",
        "brain_enabled": os.getenv("UNDX_BRAIN_ENABLED", "false").lower() == "true",
        "brain_qa_only": os.getenv("UNDX_BRAIN_QA_ONLY", "true").lower() == "true",
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
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, _request_shutdown)
    bot.init_db()
    status = _status_payload()
    # ``bot`` may establish logging before this worker is imported.  Stdout is
    # therefore the authoritative Railway startup marker; the payload contains
    # only release metadata and booleans, never credentials or provider values.
    print(
        "UNDX_WORKER_START " + json.dumps({"service": WORKER_NAME, **status}, sort_keys=True),
        flush=True,
    )
    logging.info("UNDX_WORKER_START service=%s status=%s", WORKER_NAME, status)
    while not STOP_EVENT.is_set():
        try:
            undx_router.log_provider_status()
            bot.record_worker_heartbeat(WORKER_NAME, "healthy", metadata=_status_payload())
        except Exception as exc:
            logging.exception("UNDX_WORKER_HEARTBEAT_FAILED error=%s", exc)
            try:
                bot.record_worker_heartbeat(WORKER_NAME, "error", str(exc), _status_payload())
            except Exception:
                logging.exception("UNDX worker error heartbeat failed")
        STOP_EVENT.wait(max(15, SLEEP_SECONDS))
    logging.info("UNDX_WORKER_STOPPED service=%s", WORKER_NAME)


if __name__ == "__main__":
    main()
