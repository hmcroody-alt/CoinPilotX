"""Heartbeat logging for the Command Center worker skeleton."""

from __future__ import annotations

import logging
import os
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
    LOGGER.info(
        "PUSH_WORKER_READINESS vapid_public=%s vapid_private=%s sound=%s badge=%s apns=%s fcm=%s",
        bool(os.getenv("VAPID_PUBLIC_KEY")),
        bool(os.getenv("VAPID_PRIVATE_KEY")),
        bool(os.getenv("PUSH_DEFAULT_SOUND")),
        str(os.getenv("PUSH_BADGE_ENABLED", "1")).lower() not in {"0", "false", "off", "no"},
        all(bool(os.getenv(name)) for name in ("APNS_BUNDLE_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_TEAM_ID")),
        all(bool(os.getenv(name)) for name in ("FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY")),
    )
    if config.worker_enabled:
        try:
            from services.notification_service import process_queued_email_notifications
            from services.push_service import process_expo_receipts, process_push_delivery_jobs

            job_result = process_push_delivery_jobs(limit=50)
            if job_result.get("processed") or not job_result.get("ok"):
                LOGGER.info(
                    "PUSH_JOBS processed=%s sent=%s retry=%s dead_letter=%s failed=%s ok=%s",
                    job_result.get("processed", 0),
                    job_result.get("sent", 0),
                    job_result.get("retry", 0),
                    job_result.get("dead_letter", 0),
                    job_result.get("failed", 0),
                    bool(job_result.get("ok")),
                )

            receipt_result = process_expo_receipts(limit=100)
            if receipt_result.get("checked") or not receipt_result.get("ok"):
                LOGGER.info(
                    "PUSH_RECEIPTS checked=%s confirmed=%s failed=%s invalidated=%s ok=%s",
                    receipt_result.get("checked", 0),
                    receipt_result.get("confirmed", 0),
                    receipt_result.get("failed", 0),
                    receipt_result.get("invalidated", 0),
                    bool(receipt_result.get("ok")),
                )
            email_result = process_queued_email_notifications(limit=25)
            if email_result.get("attempted") or not email_result.get("ok"):
                LOGGER.info(
                    "EMAIL_JOBS attempted=%s sent=%s retry=%s dead_letter=%s ok=%s",
                    email_result.get("attempted", 0),
                    email_result.get("sent", 0),
                    email_result.get("retry", 0),
                    email_result.get("dead_letter", 0),
                    bool(email_result.get("ok")),
                )
        except Exception as exc:
            LOGGER.warning("NOTIFICATION_WORKER_SKIPPED error_type=%s", exc.__class__.__name__)
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
