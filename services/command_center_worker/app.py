"""Flask entrypoint for the PulseSoc Command Center worker skeleton."""

from __future__ import annotations

import logging
import os
import secrets
from collections import deque
from typing import Any

from flask import Flask, jsonify, request

from .config import load_config
from .health import health_payload, utc_timestamp
from .heartbeat import start_heartbeat
from .ai_messaging import (
    AIMessagingValidationError,
    create_moderation_insight,
    ensure_ai_schema,
    explain_scam_risk,
    suggest_replies,
    summarize_conversation,
)
from .messaging import (
    MessagingValidationError,
    accept_message_event,
    clear_typing,
    ensure_messaging_schema,
    get_conversation_state,
    get_unread_counts,
    set_typing,
)
from .notifications import (
    NotificationValidationError,
    accept_notification_event,
    ensure_notification_schema,
    get_recent_notifications,
    get_unread_count,
    mark_read as mark_notification_read,
)
from .presence import PresenceValidationError, get_presence, update_presence
from .security import require_internal_auth
from .security_engine import (
    SecurityValidationError,
    create_security_event,
    ensure_security_schema,
    get_recent_security_events,
    get_user_risk_score,
)


logging.basicConfig(level=os.getenv("COMMAND_CENTER_LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger(__name__)
MAX_RECEIVED_EVENTS = 100
RECEIVED_EVENTS: deque[dict[str, Any]] = deque(maxlen=MAX_RECEIVED_EVENTS)


def _clean_event_type(value: str) -> str:
    return (value or "test").strip().lower().replace(" ", "_")[:80] or "test"


def create_app() -> Flask:
    worker_app = Flask(__name__)

    @worker_app.get("/internal/command-center/health")
    def command_center_health():
        return jsonify(health_payload(load_config()))

    @worker_app.post("/internal/command-center/events/test")
    @require_internal_auth
    def command_center_test_event():
        body = request.get_json(silent=True) or {}
        event = {
            "event_id": secrets.token_urlsafe(18),
            "event_type": _clean_event_type(str(body.get("event_type") or "test")),
            "source": str(body.get("source") or "unknown")[:120],
            "payload": body.get("payload") if isinstance(body.get("payload"), dict) else {},
            "received_at": utc_timestamp(),
        }
        RECEIVED_EVENTS.append(event)
        LOGGER.info(
            "COMMAND_CENTER_TEST_EVENT_RECEIVED event_id=%s event_type=%s source=%s",
            event["event_id"],
            event["event_type"],
            event["source"],
        )
        return jsonify({"accepted": True, "event_id": event["event_id"], "status": "received"})

    @worker_app.post("/internal/command-center/presence/update")
    @require_internal_auth
    def command_center_presence_update():
        body = request.get_json(silent=True) or {}
        try:
            presence = update_presence(
                body.get("user_id"),
                body.get("status"),
                source=str(body.get("source") or "worker")[:80],
                device_label=str(body.get("device_label") or "")[:120],
            )
        except PresenceValidationError as exc:
            return jsonify({"ok": False, "accepted": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_PRESENCE_UPDATE_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "accepted": False, "error": "presence_update_failed"}), 503
        return jsonify({"ok": True, "accepted": True, "presence": presence})

    @worker_app.get("/internal/command-center/presence/<int:user_id>")
    @require_internal_auth
    def command_center_presence_get(user_id):
        try:
            return jsonify(get_presence(user_id))
        except PresenceValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_PRESENCE_GET_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "presence_lookup_failed"}), 503

    @worker_app.post("/internal/command-center/messages/event")
    @require_internal_auth
    def command_center_message_event():
        body = request.get_json(silent=True) or {}
        try:
            event = accept_message_event(
                body.get("event_type"),
                body.get("conversation_id"),
                message_id=body.get("message_id") or 0,
                sender_id=body.get("sender_id") or 0,
                recipient_id=body.get("recipient_id"),
                payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                event_id=str(body.get("event_id") or request.headers.get("X-Idempotency-Key") or ""),
            )
        except MessagingValidationError as exc:
            return jsonify({"accepted": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_MESSAGE_EVENT_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"accepted": False, "error": "message_event_failed"}), 503
        return jsonify(event)

    @worker_app.get("/internal/command-center/messages/unread/<int:user_id>")
    @require_internal_auth
    def command_center_message_unread(user_id):
        try:
            return jsonify(get_unread_counts(user_id))
        except MessagingValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_MESSAGE_UNREAD_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "unread_lookup_failed"}), 503

    @worker_app.get("/internal/command-center/messages/conversation/<int:conversation_id>/state")
    @require_internal_auth
    def command_center_message_conversation_state(conversation_id):
        try:
            viewer_user_id = int(request.args.get("user_id") or 0)
            return jsonify(get_conversation_state(conversation_id, viewer_user_id=viewer_user_id))
        except PermissionError:
            return jsonify({"ok": False, "error": "conversation_access_denied"}), 403
        except MessagingValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_MESSAGE_STATE_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "conversation_state_failed"}), 503

    @worker_app.post("/internal/command-center/messages/typing")
    @require_internal_auth
    def command_center_message_typing():
        body = request.get_json(silent=True) or {}
        try:
            is_typing = body.get("is_typing", body.get("typing", True)) not in {False, 0, "0", "false", "off", "no"}
            result = set_typing(body.get("conversation_id"), body.get("sender_id") or body.get("user_id"), body) if is_typing else clear_typing(body.get("conversation_id"), body.get("sender_id") or body.get("user_id"), body)
        except MessagingValidationError as exc:
            return jsonify({"accepted": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_MESSAGE_TYPING_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"accepted": False, "error": "typing_event_failed"}), 503
        return jsonify(result)

    @worker_app.post("/internal/command-center/notifications/event")
    @require_internal_auth
    def command_center_notification_event():
        body = request.get_json(silent=True) or {}
        try:
            event = accept_notification_event(
                body.get("recipient_id") or body.get("user_id"),
                body.get("notification_type") or body.get("type"),
                body.get("title"),
                body=body.get("body") or "",
                actor_id=body.get("actor_id"),
                payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                channel=body.get("channel") or "in_app",
                event_id=str(body.get("event_id") or request.headers.get("X-Idempotency-Key") or ""),
            )
        except NotificationValidationError as exc:
            return jsonify({"accepted": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_NOTIFICATION_EVENT_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"accepted": False, "error": "notification_event_failed"}), 503
        return jsonify(event)

    @worker_app.get("/internal/command-center/notifications/unread/<int:user_id>")
    @require_internal_auth
    def command_center_notification_unread(user_id):
        try:
            return jsonify(get_unread_count(user_id))
        except NotificationValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_NOTIFICATION_UNREAD_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "notification_unread_failed"}), 503

    @worker_app.get("/internal/command-center/notifications/recent/<int:user_id>")
    @require_internal_auth
    def command_center_notification_recent(user_id):
        try:
            return jsonify(get_recent_notifications(user_id, limit=int(request.args.get("limit") or 50)))
        except NotificationValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_NOTIFICATION_RECENT_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "notification_recent_failed"}), 503

    @worker_app.post("/internal/command-center/notifications/read")
    @require_internal_auth
    def command_center_notification_read():
        body = request.get_json(silent=True) or {}
        try:
            result = mark_notification_read(
                body.get("recipient_id") or body.get("user_id"),
                event_id=str(body.get("event_id") or ""),
                mark_all=bool(body.get("mark_all")),
            )
        except NotificationValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_NOTIFICATION_READ_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "notification_read_failed"}), 503
        return jsonify(result)

    @worker_app.post("/internal/command-center/security/event")
    @require_internal_auth
    def command_center_security_event():
        body = request.get_json(silent=True) or {}
        try:
            event = create_security_event(
                body.get("event_type") or body.get("type"),
                user_id=body.get("user_id") or 0,
                actor_id=body.get("actor_id") or 0,
                payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                event_id=str(body.get("event_id") or request.headers.get("X-Idempotency-Key") or ""),
            )
        except SecurityValidationError as exc:
            return jsonify({"accepted": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_SECURITY_EVENT_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"accepted": False, "error": "security_event_failed"}), 503
        return jsonify(event)

    @worker_app.get("/internal/command-center/security/user/<int:user_id>/risk")
    @require_internal_auth
    def command_center_security_user_risk(user_id):
        try:
            return jsonify(get_user_risk_score(user_id))
        except SecurityValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_SECURITY_RISK_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "security_risk_failed"}), 503

    @worker_app.get("/internal/command-center/security/recent")
    @require_internal_auth
    def command_center_security_recent():
        try:
            return jsonify(get_recent_security_events(limit=int(request.args.get("limit") or 50), user_id=int(request.args.get("user_id") or 0)))
        except SecurityValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_SECURITY_RECENT_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "error": "security_recent_failed"}), 503

    @worker_app.post("/internal/command-center/ai/summary")
    @require_internal_auth
    def command_center_ai_summary():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(summarize_conversation(body))
        except AIMessagingValidationError as exc:
            return jsonify({"ok": False, "available": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_AI_SUMMARY_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "available": False, "error": "ai_summary_failed"}), 503

    @worker_app.post("/internal/command-center/ai/smart-replies")
    @require_internal_auth
    def command_center_ai_smart_replies():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(suggest_replies(body))
        except AIMessagingValidationError as exc:
            return jsonify({"ok": False, "available": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_AI_SMART_REPLIES_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "available": False, "error": "ai_smart_replies_failed"}), 503

    @worker_app.post("/internal/command-center/ai/scam-explanation")
    @require_internal_auth
    def command_center_ai_scam_explanation():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(explain_scam_risk(body))
        except AIMessagingValidationError as exc:
            return jsonify({"ok": False, "available": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_AI_SCAM_EXPLANATION_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "available": False, "error": "ai_scam_explanation_failed"}), 503

    @worker_app.post("/internal/command-center/ai/moderation-insight")
    @require_internal_auth
    def command_center_ai_moderation_insight():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(create_moderation_insight(body))
        except AIMessagingValidationError as exc:
            return jsonify({"ok": False, "available": False, "error": str(exc)}), 400
        except Exception as exc:
            LOGGER.warning("COMMAND_CENTER_AI_MODERATION_INSIGHT_FAILED error_type=%s", exc.__class__.__name__)
            return jsonify({"ok": False, "available": False, "error": "ai_moderation_insight_failed"}), 503

    return worker_app


app = create_app()
try:
    ensure_messaging_schema()
except Exception as exc:
    LOGGER.warning("COMMAND_CENTER_MESSAGE_SCHEMA_INIT_SKIPPED error_type=%s", exc.__class__.__name__)
try:
    ensure_notification_schema()
except Exception as exc:
    LOGGER.warning("COMMAND_CENTER_NOTIFICATION_SCHEMA_INIT_SKIPPED error_type=%s", exc.__class__.__name__)
try:
    ensure_security_schema()
except Exception as exc:
    LOGGER.warning("COMMAND_CENTER_SECURITY_SCHEMA_INIT_SKIPPED error_type=%s", exc.__class__.__name__)
try:
    ensure_ai_schema()
except Exception as exc:
    LOGGER.warning("COMMAND_CENTER_AI_SCHEMA_INIT_SKIPPED error_type=%s", exc.__class__.__name__)
start_heartbeat(load_config())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8081"))
    app.run(host="0.0.0.0", port=port)
