"""Future Command Center dispatch client for the PulseSoc main app.

This module is intentionally passive by default. It prepares the Flask/web
service to hand work to a future internal worker without making that worker a
runtime dependency today.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)

TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_SERVICE_NAME = "main-app"
DEFAULT_SERVICE_ROLE = "web"
DEFAULT_TIMEOUT_SECONDS = 1.5
MAX_TIMEOUT_SECONDS = 5.0

_last_dispatch_status: dict[str, Any] = {
    "kind": "",
    "ok": True,
    "dispatched": False,
    "reason": "not_run",
    "timestamp": "",
}


def _env_text(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else default


def _env_bool(key: str, default: bool = False) -> bool:
    if key in os.environ:
        return _env_text(key).lower() in TRUE_VALUES
    return default


def _timeout_seconds() -> float:
    raw_value = _env_text("COMMAND_CENTER_TIMEOUT_SECONDS")
    try:
        value = float(raw_value) if raw_value else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_SECONDS
    return max(0.2, min(value, MAX_TIMEOUT_SECONDS))


def service_identity() -> dict[str, str]:
    return {
        "service_name": _env_text("PULSESOC_SERVICE_NAME", DEFAULT_SERVICE_NAME) or DEFAULT_SERVICE_NAME,
        "service_role": _env_text("PULSESOC_SERVICE_ROLE", DEFAULT_SERVICE_ROLE) or DEFAULT_SERVICE_ROLE,
    }


def is_enabled() -> bool:
    return _env_bool("COMMAND_CENTER_ENABLED", False)


def url_configured() -> bool:
    return bool(_env_text("COMMAND_CENTER_INTERNAL_URL"))


def token_configured() -> bool:
    return bool(_env_text("COMMAND_CENTER_INTERNAL_TOKEN"))


def status() -> dict[str, Any]:
    identity = service_identity()
    return {
        **identity,
        "enabled": is_enabled(),
        "url_configured": url_configured(),
        "token_configured": token_configured(),
        "timeout_seconds": _timeout_seconds(),
        "last_dispatch": last_dispatch_status(),
    }


def last_dispatch_status() -> dict[str, Any]:
    return dict(_last_dispatch_status)


def _record_status(kind: str, ok: bool, dispatched: bool, reason: str, **extra: Any) -> dict[str, Any]:
    _last_dispatch_status.update(
        {
            "kind": kind,
            "ok": bool(ok),
            "dispatched": bool(dispatched),
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            **extra,
        }
    )
    return last_dispatch_status()


def _dispatch_url() -> str:
    return _env_text("COMMAND_CENTER_INTERNAL_URL").rstrip("/") + "/internal/dispatch"


def _worker_url(path: str) -> str:
    return _env_text("COMMAND_CENTER_INTERNAL_URL").rstrip("/") + path


def _internal_headers(idempotency_key: str = "") -> dict[str, str]:
    identity = service_identity()
    headers = {
        "Content-Type": "application/json",
        "X-PulseSoc-Internal-Token": _env_text("COMMAND_CENTER_INTERNAL_TOKEN"),
        "X-PulseSoc-Service-Name": identity["service_name"],
        "X-PulseSoc-Service-Role": identity["service_role"],
    }
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key[:128]
    return headers


def dispatch_event(kind: str, payload: dict[str, Any] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    safe_kind = (kind or "unknown").strip().lower().replace(" ", "_")[:64]
    if not is_enabled():
        LOGGER.info("COMMAND_CENTER_DISPATCH_SKIPPED kind=%s reason=disabled", safe_kind)
        return _record_status(safe_kind, True, False, "disabled")

    internal_url = _env_text("COMMAND_CENTER_INTERNAL_URL").rstrip("/")
    internal_token = _env_text("COMMAND_CENTER_INTERNAL_TOKEN")
    if not internal_url or not internal_token:
        LOGGER.warning(
            "COMMAND_CENTER_DISPATCH_SKIPPED kind=%s reason=not_configured url_configured=%s token_configured=%s",
            safe_kind,
            bool(internal_url),
            bool(internal_token),
        )
        return _record_status(safe_kind, False, False, "not_configured")

    identity = service_identity()
    event_body = {
        "kind": safe_kind,
        "payload": payload or {},
        "source": identity,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    headers = _internal_headers(idempotency_key)

    try:
        response = requests.post(_dispatch_url(), json=event_body, headers=headers, timeout=_timeout_seconds())
        ok = 200 <= response.status_code < 300
        if not ok:
            LOGGER.warning(
                "COMMAND_CENTER_DISPATCH_FAILED kind=%s status_code=%s",
                safe_kind,
                response.status_code,
            )
            return _record_status(safe_kind, False, True, "worker_rejected", status_code=response.status_code)
        LOGGER.info("COMMAND_CENTER_DISPATCH_COMPLETE kind=%s status_code=%s", safe_kind, response.status_code)
        return _record_status(safe_kind, True, True, "sent", status_code=response.status_code)
    except requests.RequestException as exc:
        LOGGER.warning(
            "COMMAND_CENTER_DISPATCH_FAILED kind=%s error_type=%s",
            safe_kind,
            exc.__class__.__name__,
        )
        return _record_status(safe_kind, False, False, "request_failed", error_type=exc.__class__.__name__)


def enqueue_message_event(payload: dict[str, Any] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    return dispatch_event("message", payload, idempotency_key=idempotency_key)


def enqueue_notification_event(payload: dict[str, Any] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    return dispatch_event("notification", payload, idempotency_key=idempotency_key)


def enqueue_security_event(payload: dict[str, Any] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    return dispatch_event("security", payload, idempotency_key=idempotency_key)


def enqueue_media_event(payload: dict[str, Any] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    return dispatch_event("media", payload, idempotency_key=idempotency_key)


def enqueue_ai_event(payload: dict[str, Any] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    return dispatch_event("ai", payload, idempotency_key=idempotency_key)


def enqueue_presence_event(user_id: int, status: str, source: str | None = None, device_label: str | None = None) -> dict[str, Any]:
    safe_status = str(status or "").strip().lower()
    payload = {
        "user_id": int(user_id or 0),
        "status": safe_status,
        "source": str(source or "web")[:80],
        "device_label": str(device_label or "")[:120],
    }
    if payload["user_id"] <= 0 or safe_status not in {"online", "away", "offline"}:
        LOGGER.info("COMMAND_CENTER_PRESENCE_SKIPPED reason=invalid_payload status=%s", safe_status)
        return _record_status("presence", False, False, "invalid_payload")
    if not is_enabled():
        LOGGER.info("COMMAND_CENTER_PRESENCE_SKIPPED user_id=%s reason=disabled", payload["user_id"])
        return _record_status("presence", True, False, "disabled")
    internal_url = _env_text("COMMAND_CENTER_INTERNAL_URL").rstrip("/")
    internal_token = _env_text("COMMAND_CENTER_INTERNAL_TOKEN")
    if not internal_url or not internal_token:
        LOGGER.warning(
            "COMMAND_CENTER_PRESENCE_SKIPPED reason=not_configured url_configured=%s token_configured=%s",
            bool(internal_url),
            bool(internal_token),
        )
        return _record_status("presence", False, False, "not_configured")
    try:
        response = requests.post(
            _worker_url("/internal/command-center/presence/update"),
            json=payload,
            headers=_internal_headers(f"presence-{payload['user_id']}-{safe_status}"),
            timeout=_timeout_seconds(),
        )
        ok = 200 <= response.status_code < 300
        if not ok:
            LOGGER.warning("COMMAND_CENTER_PRESENCE_FAILED status_code=%s", response.status_code)
            return _record_status("presence", False, True, "worker_rejected", status_code=response.status_code)
        return _record_status("presence", True, True, "sent", status_code=response.status_code)
    except requests.RequestException as exc:
        LOGGER.warning("COMMAND_CENTER_PRESENCE_FAILED error_type=%s", exc.__class__.__name__)
        return _record_status("presence", False, False, "request_failed", error_type=exc.__class__.__name__)
