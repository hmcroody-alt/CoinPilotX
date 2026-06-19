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
from .messaging import (
    MessagingValidationError,
    accept_message_event,
    clear_typing,
    ensure_messaging_schema,
    get_conversation_state,
    get_unread_counts,
    set_typing,
)
from .presence import PresenceValidationError, get_presence, update_presence
from .security import require_internal_auth


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

    return worker_app


app = create_app()
try:
    ensure_messaging_schema()
except Exception as exc:
    LOGGER.warning("COMMAND_CENTER_MESSAGE_SCHEMA_INIT_SKIPPED error_type=%s", exc.__class__.__name__)
start_heartbeat(load_config())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8081"))
    app.run(host="0.0.0.0", port=port)
