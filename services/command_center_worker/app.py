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

    return worker_app


app = create_app()
start_heartbeat(load_config())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8081"))
    app.run(host="0.0.0.0", port=port)
