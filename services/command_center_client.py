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
        "Authorization": f"Bearer {_env_text('COMMAND_CENTER_INTERNAL_TOKEN')}",
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


def _post_worker(path: str, payload: dict[str, Any], kind: str, idempotency_key: str = "") -> dict[str, Any]:
    if not is_enabled():
        return _record_status(kind, True, False, "disabled")
    if not url_configured() or not token_configured():
        return _record_status(kind, False, False, "not_configured")
    try:
        response = requests.post(
            _worker_url(path),
            json=payload,
            headers=_internal_headers(idempotency_key),
            timeout=_timeout_seconds(),
        )
        ok = 200 <= response.status_code < 300
        if not ok:
            LOGGER.warning("COMMAND_CENTER_%s_FAILED status_code=%s", kind.upper(), response.status_code)
            return _record_status(kind, False, True, "worker_rejected", status_code=response.status_code)
        response_payload = response.json() if response.content else {}
        return {**_record_status(kind, True, True, "sent", status_code=response.status_code), "response": response_payload}
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("COMMAND_CENTER_%s_FAILED error_type=%s", kind.upper(), exc.__class__.__name__)
        return _record_status(kind, False, False, "request_failed", error_type=exc.__class__.__name__)


def _get_worker(path: str, kind: str) -> dict[str, Any]:
    if not is_enabled():
        return {"ok": True, "available": False, "reason": "disabled"}
    if not url_configured() or not token_configured():
        return {"ok": False, "available": False, "reason": "not_configured"}
    try:
        response = requests.get(_worker_url(path), headers=_internal_headers(), timeout=_timeout_seconds())
        if not (200 <= response.status_code < 300):
            return {"ok": False, "available": False, "reason": "worker_rejected", "status_code": response.status_code}
        return {"ok": True, "available": True, **(response.json() or {})}
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("COMMAND_CENTER_%s_FAILED error_type=%s", kind.upper(), exc.__class__.__name__)
        return {"ok": False, "available": False, "reason": "request_failed"}


def enqueue_message_event(
    event_type: str | dict[str, Any] = "message_created",
    conversation_id: int = 0,
    message_id: int = 0,
    sender_id: int = 0,
    recipient_id: int | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    if isinstance(event_type, dict):
        event_payload = dict(event_type)
        event_type = str(event_payload.pop("event_type", "message_created"))
        conversation_id = int(event_payload.pop("conversation_id", conversation_id) or 0)
        message_id = int(event_payload.pop("message_id", message_id) or 0)
        sender_id = int(event_payload.pop("sender_id", sender_id) or 0)
        recipient_id = event_payload.pop("recipient_id", recipient_id)
        payload = payload or event_payload
    body = {
        "event_type": str(event_type or "message_created")[:80],
        "conversation_id": int(conversation_id or 0),
        "message_id": int(message_id or 0),
        "sender_id": int(sender_id or 0),
        "recipient_id": int(recipient_id) if recipient_id else None,
        "payload": payload or {},
    }
    return _post_worker(
        "/internal/command-center/messages/event",
        body,
        "message",
        idempotency_key=idempotency_key or f"{body['event_type']}-{body['conversation_id']}-{body['message_id']}-{body['sender_id']}",
    )


def enqueue_message_delivered(conversation_id: int, message_id: int, recipient_id: int, sender_id: int = 0) -> dict[str, Any]:
    return enqueue_message_event("message_delivered", conversation_id, message_id, sender_id, recipient_id, {"delivered": True})


def enqueue_message_read(conversation_id: int, message_id: int, recipient_id: int, sender_id: int = 0) -> dict[str, Any]:
    return enqueue_message_event("message_read", conversation_id, message_id, sender_id, recipient_id, {"read": True})


def enqueue_typing_event(conversation_id: int, sender_id: int, is_typing: bool = True) -> dict[str, Any]:
    return _post_worker(
        "/internal/command-center/messages/typing",
        {"conversation_id": int(conversation_id or 0), "sender_id": int(sender_id or 0), "is_typing": bool(is_typing)},
        "typing",
        idempotency_key=f"typing-{int(conversation_id or 0)}-{int(sender_id or 0)}-{int(bool(is_typing))}",
    )


def get_unread_counts(user_id: int) -> dict[str, Any]:
    result = _get_worker(f"/internal/command-center/messages/unread/{int(user_id or 0)}", "message_unread")
    if not result.get("available"):
        result.setdefault("user_id", int(user_id or 0))
        result.setdefault("total_unread", 0)
        result.setdefault("conversations", [])
    return result


def get_conversation_state(conversation_id: int, user_id: int = 0) -> dict[str, Any]:
    suffix = f"?user_id={int(user_id)}" if int(user_id or 0) > 0 else ""
    result = _get_worker(f"/internal/command-center/messages/conversation/{int(conversation_id or 0)}/state{suffix}", "message_state")
    if not result.get("available"):
        result.setdefault("conversation_id", int(conversation_id or 0))
        result.setdefault("typing", [])
        result.setdefault("event_counts", {})
    return result


def enqueue_notification_event(
    recipient_id: int | dict[str, Any] = 0,
    notification_type: str = "",
    title: str = "",
    body: str = "",
    actor_id: int | None = None,
    payload: dict[str, Any] | None = None,
    channel: str = "in_app",
    event_id: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    if isinstance(recipient_id, dict):
        values = dict(recipient_id)
        recipient_id = int(values.pop("recipient_id", values.pop("user_id", 0)) or 0)
        notification_type = str(values.pop("notification_type", values.pop("type", notification_type)) or notification_type)
        title = str(values.pop("title", title) or title)
        body = str(values.pop("body", body) or body)
        actor_id = values.pop("actor_id", actor_id)
        channel = str(values.pop("channel", channel) or channel)
        event_id = str(values.pop("event_id", event_id) or event_id)
        payload = payload or values
    request_body = {
        "recipient_id": int(recipient_id or 0),
        "actor_id": int(actor_id) if actor_id else None,
        "notification_type": str(notification_type or "")[:80],
        "title": str(title or "")[:180],
        "body": str(body or "")[:2000],
        "payload": payload or {},
        "channel": str(channel or "in_app")[:30],
        "event_id": str(event_id or "")[:160],
    }
    return _post_worker(
        "/internal/command-center/notifications/event",
        request_body,
        "notification",
        idempotency_key=idempotency_key or request_body["event_id"] or f"notification-{request_body['recipient_id']}-{request_body['notification_type']}",
    )


def get_notification_unread_count(user_id: int) -> dict[str, Any]:
    result = _get_worker(f"/internal/command-center/notifications/unread/{int(user_id or 0)}", "notification_unread")
    if not result.get("available"):
        result.setdefault("recipient_id", int(user_id or 0))
        result.setdefault("alert_unread_count", 0)
        result.setdefault("unread_count", 0)
        result.setdefault("count", 0)
    return result


def get_recent_notifications(user_id: int, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 100))
    result = _get_worker(f"/internal/command-center/notifications/recent/{int(user_id or 0)}?limit={safe_limit}", "notification_recent")
    if not result.get("available"):
        result.setdefault("recipient_id", int(user_id or 0))
        result.setdefault("notifications", [])
        result.setdefault("items", [])
    return result


def mark_notification_read(user_id: int, event_id: str = "", mark_all: bool = False) -> dict[str, Any]:
    return _post_worker(
        "/internal/command-center/notifications/read",
        {"recipient_id": int(user_id or 0), "event_id": str(event_id or "")[:160], "mark_all": bool(mark_all)},
        "notification_read",
        idempotency_key=f"notification-read-{int(user_id or 0)}-{str(event_id or 'all')[:80]}",
    )


def enqueue_security_event(payload: dict[str, Any] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    values = dict(payload or {})
    event_id = str(values.pop("event_id", "") or idempotency_key or "")[:160]
    event_type = str(values.pop("event_type", values.pop("type", "")) or "")[:80]
    if not event_type:
        return _record_status("security", False, False, "invalid_payload")
    request_body = {
        "event_id": event_id,
        "user_id": int(values.pop("user_id", 0) or 0),
        "actor_id": int(values.pop("actor_id", 0) or 0),
        "event_type": event_type,
        "payload": values.pop("payload", values) if isinstance(values.get("payload", values), dict) else values,
    }
    return _post_worker(
        "/internal/command-center/security/event",
        request_body,
        "security",
        idempotency_key=event_id or idempotency_key or f"security-{event_type}-{request_body['user_id']}",
    )


def get_user_risk_score(user_id: int) -> dict[str, Any]:
    result = _get_worker(f"/internal/command-center/security/user/{int(user_id or 0)}/risk", "security_risk")
    if not result.get("available"):
        result.setdefault("user_id", int(user_id or 0))
        result.setdefault("risk_score", 0)
        result.setdefault("risk_level", "Low")
    return result


def get_recent_security_events(user_id: int = 0, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 100))
    suffix = f"?limit={safe_limit}"
    if int(user_id or 0) > 0:
        suffix += f"&user_id={int(user_id)}"
    result = _get_worker(f"/internal/command-center/security/recent{suffix}", "security_recent")
    if not result.get("available"):
        result.setdefault("events", [])
    return result


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
