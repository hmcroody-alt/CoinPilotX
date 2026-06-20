#!/usr/bin/env python3
"""Audit the PulseSoc Command Center realtime transport layer."""

from __future__ import annotations

import json
import os
import py_compile
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "command-center-realtime-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_client.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        ROOT / "services" / "command_center_worker" / "realtime_transport.py",
        Path(__file__).resolve(),
    ]
    compiled = []
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        compiled.append(str(target.relative_to(ROOT)))
    return compiled


def configure_env(db_path: Path) -> None:
    os.environ["PULSESOC_SERVICE_NAME"] = "command-center-worker"
    os.environ["PULSESOC_SERVICE_ROLE"] = "worker"
    os.environ["COMMAND_CENTER_WORKER_ENABLED"] = "true"
    os.environ["COMMAND_CENTER_INTERNAL_TOKEN"] = AUDIT_TOKEN
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("REDIS_URL", None)


def prepare_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comm_v2_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            membership_state TEXT DEFAULT 'active',
            left_at TEXT,
            unread_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, unread_count) VALUES (77, 55, 'active', '', 0)"
    )
    conn.execute(
        "INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, unread_count) VALUES (77, 44, 'active', '', 0)"
    )
    conn.execute(
        "INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, unread_count) VALUES (88, 66, 'active', '', 0)"
    )
    conn.commit()
    conn.close()


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AUDIT_TOKEN}"}


def audit_worker_realtime_endpoints() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "realtime_transport_audit.db"
        configure_env(db_path)
        prepare_db(db_path)
        sys.path.insert(0, str(ROOT))

        from services.command_center_worker.app import app

        client = app.test_client()

        missing_token = client.get("/internal/command-center/realtime/status")
        expect(missing_token.status_code == 401, "realtime status should reject missing token")

        invalid_token = client.get("/internal/command-center/realtime/status", headers={"Authorization": "Bearer wrong"})
        expect(invalid_token.status_code == 403, "realtime status should reject invalid token")

        connect = client.post(
            "/internal/command-center/realtime/connect",
            headers=auth_headers(),
            json={"user_id": 55, "session_id": "audit-session", "device_type": "desktop", "subscribed_conversations": [77, 88]},
        )
        expect(connect.status_code == 200, f"connect returned {connect.status_code}")
        connect_json = connect.get_json() or {}
        expect(connect_json.get("subscribed_conversations") == [77], "connect should filter unauthorized conversations")

        denied_subscribe = client.post(
            "/internal/command-center/realtime/subscribe",
            headers=auth_headers(),
            json={"user_id": 55, "session_id": "audit-session", "conversation_id": 88},
        )
        expect(denied_subscribe.status_code == 403, "unauthorized conversation subscribe should be denied")

        event = client.post(
            "/internal/command-center/realtime/event",
            headers=auth_headers(),
            json={
                "event_type": "message_created",
                "conversation_id": 77,
                "actor_id": 44,
                "payload": {"conversation_id": 77, "message_id": 9901, "body": "hello", "token": "must-not-leak"},
            },
        )
        expect(event.status_code == 200, f"valid realtime event returned {event.status_code}")
        event_json = event.get_json() or {}
        expect(event_json.get("accepted") is True, "valid realtime event was not accepted")
        expect(event_json.get("recipient_count") == 1, "message event should route to the non-actor participant only")

        allowed_poll = client.get("/internal/command-center/realtime/poll/55?after_id=0", headers=auth_headers())
        expect(allowed_poll.status_code == 200, f"allowed poll returned {allowed_poll.status_code}")
        allowed_json = allowed_poll.get_json() or {}
        expect(len(allowed_json.get("events") or []) == 1, "recipient should receive routed event")
        serialized = json.dumps(allowed_json, sort_keys=True)
        expect("must-not-leak" not in serialized, "sensitive payload key leaked through realtime poll")
        expect(AUDIT_TOKEN not in serialized, "internal token leaked through realtime poll")
        expect(str(db_path) not in serialized, "database path leaked through realtime poll")

        blocked_poll = client.get("/internal/command-center/realtime/poll/66?after_id=0", headers=auth_headers())
        expect(blocked_poll.status_code == 200, f"blocked poll returned {blocked_poll.status_code}")
        expect((blocked_poll.get_json() or {}).get("events") == [], "non-recipient user should not receive conversation event")

        typing_one = client.post(
            "/internal/command-center/realtime/event",
            headers=auth_headers(),
            json={"event_type": "typing_started", "conversation_id": 77, "actor_id": 44, "payload": {"conversation_id": 77, "user_id": 44}},
        )
        typing_two = client.post(
            "/internal/command-center/realtime/event",
            headers=auth_headers(),
            json={"event_type": "typing_started", "conversation_id": 77, "actor_id": 44, "payload": {"conversation_id": 77, "user_id": 44}},
        )
        expect(typing_one.status_code == 200 and typing_two.status_code == 200, "typing events should not crash")
        expect((typing_two.get_json() or {}).get("rate_limited") is True, "noisy typing event should be rate limited")

        invalid_event = client.post(
            "/internal/command-center/realtime/event",
            headers=auth_headers(),
            json={"event_type": "unknown_event", "recipient_ids": [55]},
        )
        expect(invalid_event.status_code == 400, "invalid realtime event type should be rejected")

        stream = client.get("/internal/command-center/realtime/stream/55?after_id=0", headers=auth_headers(), buffered=False)
        expect(stream.status_code == 200, f"SSE stream returned {stream.status_code}")
        expect(stream.mimetype == "text/event-stream", "SSE stream should use text/event-stream")
        stream.close()

        status = client.get("/internal/command-center/realtime/status", headers=auth_headers())
        expect(status.status_code == 200, f"status returned {status.status_code}")
        status_json = status.get_json() or {}
        expect("active_connections" in status_json, "status missing active_connections")
        expect("events_per_minute" in status_json, "status missing events_per_minute")

        return {
            "missing_token": missing_token.status_code,
            "invalid_token": invalid_token.status_code,
            "connect": connect_json,
            "event": event_json,
            "allowed_poll_events": len(allowed_json.get("events") or []),
            "blocked_poll_events": len((blocked_poll.get_json() or {}).get("events") or []),
            "typing_rate_limited": (typing_two.get_json() or {}).get("rate_limited") is True,
            "stream_status": stream.status_code,
            "status": status_json,
        }


def audit_disabled_main_app_path() -> dict:
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    from services import command_center_client

    event = command_center_client.enqueue_realtime_event(
        "message_created",
        {"conversation_id": 77, "message_id": 88},
        conversation_id=77,
        actor_id=44,
    )
    expect(event.get("ok") is True, "disabled realtime dispatch should be safe")
    expect(event.get("dispatched") is False, "disabled realtime dispatch should not call worker")
    status = command_center_client.get_realtime_status()
    expect(status.get("available") is False, "disabled realtime status should report unavailable")
    expect(status.get("transport") == "polling_fallback", "disabled realtime status should preserve polling fallback")
    return {"event": event, "status": status}


def audit_frontend_hooks() -> dict:
    source = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")
    for required in (
        "presence_updated",
        "typing_started",
        "typing_stopped",
        "unread_count_updated",
        "message_read",
        "scheduleRealtimePoll(3000)",
    ):
        expect(required in source, f"messages frontend missing {required}")
    expect("COMMAND_CENTER_INTERNAL_TOKEN" not in source, "frontend must not reference internal token")
    expect('dataset.pulseSse !== "enabled"' in source, "browser SSE must remain opt-in")
    return {"messages_realtime_hooks": True, "polling_fallback_present": True}


def main() -> int:
    compiled = compile_targets()
    endpoints = audit_worker_realtime_endpoints()
    disabled = audit_disabled_main_app_path()
    frontend = audit_frontend_hooks()
    report = {
        "ok": True,
        "compiled": compiled,
        "auth_required": endpoints["missing_token"] == 401 and endpoints["invalid_token"] == 403,
        "unauthorized_users_rejected": endpoints["connect"].get("subscribed_conversations") == [77],
        "event_routing_respects_permissions": endpoints["allowed_poll_events"] == 1 and endpoints["blocked_poll_events"] == 0,
        "typing_rate_limit_enabled": endpoints["typing_rate_limited"],
        "sse_transport_available": endpoints["stream_status"] == 200,
        "fallback_polling_still_present": frontend["polling_fallback_present"] and disabled["status"].get("transport") == "polling_fallback",
        "no_secrets_exposed": True,
        "worker_disabled_main_app_path_safe": disabled["event"].get("reason") == "disabled",
        "status_shape": endpoints["status"],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
