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
from services import undx_agent_policy
from services import undx_mission_runtime
from services.undx_brain import config as undx_config


WORKER_NAME = "coinpilotx-undx-worker"
STOP_EVENT = threading.Event()


def _build_sha() -> str:
    """Return immutable release lineage without exposing any configuration value."""
    return (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("APP_BUILD_SHA") or "unknown")[:40]


def _request_shutdown(signum, _frame) -> None:
    logging.info("UNDX_WORKER_STOP_REQUESTED service=%s signal=%s", WORKER_NAME, signum)
    STOP_EVENT.set()


def _status_payload() -> dict:
    providers = undx_router.provider_status()
    policy = undx_agent_policy.flags()
    runtime = undx_mission_runtime.surface()
    return {
        "worker_type": "undx",
        "runtime_version": os.getenv("UNDX_CONFIG_VERSION", "default"),
        "deployed_sha": _build_sha(),
        "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT") or "unknown",
        "agent_enabled": policy["agent_enabled"],
        "reads_enabled": undx_agent_policy.reads_available(),
        "writes_enabled": policy["writes_enabled"],
        "writes_disabled": not undx_agent_policy.writes_available(),
        "emergency_stop": policy["emergency_kill_switch"],
        "global_write_stop": policy["global_write_kill_switch"],
        "brain_enabled": bool(undx_config.flags().get("UNDX_BRAIN_ENABLED")),
        "brain_qa_only": bool(undx_config.flags().get("UNDX_BRAIN_QA_ONLY")),
        "mission_runtime_enabled": runtime.enabled,
        "mission_runtime_reason": runtime.reason,
        "dynamic_limit_escalation": runtime.dynamic_escalation,
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
    resolved = undx_config.flags()
    sleep_seconds = int(resolved.get("UNDX_WORKER_SLEEP_SECONDS", 60))
    heartbeat_enabled = bool(resolved.get("UNDX_WORKER_HEARTBEAT_ENABLED", True))
    while not STOP_EVENT.is_set():
        try:
            undx_router.log_provider_status()
            mission = undx_mission_runtime.poll_once()
            heartbeat_metadata = {**_status_payload(), "mission_poll": mission}
            if heartbeat_enabled:
                bot.record_worker_heartbeat(WORKER_NAME, "healthy", metadata=heartbeat_metadata)
            if mission.get("advanced") or mission.get("status") in {"blocked", "failed", "succeeded"}:
                logging.info("UNDX_WORKER_MISSION %s", json.dumps(mission, sort_keys=True))
        except Exception as exc:
            logging.exception("UNDX_WORKER_HEARTBEAT_FAILED error=%s", exc)
            try:
                bot.record_worker_heartbeat(WORKER_NAME, "error", str(exc), _status_payload())
            except Exception:
                logging.exception("UNDX worker error heartbeat failed")
        STOP_EVENT.wait(max(15, sleep_seconds))
    logging.info("UNDX_WORKER_STOPPED service=%s", WORKER_NAME)


if __name__ == "__main__":
    main()
