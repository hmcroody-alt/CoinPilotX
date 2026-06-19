"""Heartbeat logging for the Command Center worker skeleton."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .config import WorkerConfig


LOGGER = logging.getLogger(__name__)
_heartbeat_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def heartbeat_once(config: WorkerConfig) -> dict:
    payload = {
        "service_name": config.service_name,
        "service_role": config.service_role,
        "worker_enabled": config.worker_enabled,
        "timestamp": _timestamp(),
    }
    LOGGER.info(
        "COMMAND_CENTER_WORKER_HEARTBEAT service=%s role=%s enabled=%s",
        config.service_name,
        config.service_role,
        config.worker_enabled,
    )
    return payload


def _heartbeat_loop(config: WorkerConfig) -> None:
    while not _stop_event.wait(config.heartbeat_seconds):
        heartbeat_once(config)


def start_heartbeat(config: WorkerConfig) -> bool:
    global _heartbeat_thread
    if not config.worker_enabled:
        return False
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        return False
    _stop_event.clear()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(config,),
        name="command-center-heartbeat",
        daemon=True,
    )
    _heartbeat_thread.start()
    heartbeat_once(config)
    return True


def stop_heartbeat() -> None:
    _stop_event.set()
