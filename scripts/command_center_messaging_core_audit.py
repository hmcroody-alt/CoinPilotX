#!/usr/bin/env python3
"""Audit the PulseSoc Command Center real-time messaging core."""

from __future__ import annotations

import json
import os
import py_compile
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "command-center-messaging-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_client.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        ROOT / "services" / "command_center_worker" / "messaging.py",
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


def prepare_existing_messaging_state(db_path: Path) -> None:
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
        "INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, unread_count) VALUES (77, 55, 'active', '', 3)"
    )
    conn.commit()
    conn.close()


def audit_worker_endpoints() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "messaging_core_audit.db"
        configure_env(db_path)
        prepare_existing_messaging_state(db_path)
        sys.path.insert(0, str(ROOT))

        from services.command_center_worker.app import app
        from services.command_center_worker import messaging

        messaging.ensure_messaging_schema()
        client = app.test_client()

        missing_token = client.post(
            "/internal/command-center/messages/event",
            json={"event_type": "message_created", "conversation_id": 77, "message_id": 88, "sender_id": 44},
        )
        expect(missing_token.status_code == 401, "message event endpoint should reject missing token")

        invalid_event = client.post(
            "/internal/command-center/messages/event",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={"event_type": "unknown_event", "conversation_id": 77, "message_id": 88, "sender_id": 44},
        )
        expect(invalid_event.status_code == 400, "invalid event type should be rejected")

        accepted = client.post(
            "/internal/command-center/messages/event",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={
                "event_type": "message_created",
                "conversation_id": 77,
                "message_id": 88,
                "sender_id": 44,
                "recipient_id": 55,
                "payload": {"message_type": "text", "token": "must-be-redacted"},
            },
        )
        expect(accepted.status_code == 200, f"valid message event returned {accepted.status_code}")
        accepted_json = accepted.get_json() or {}
        expect(accepted_json.get("accepted") is True, "valid message event was not accepted")
        expect(accepted_json.get("event_type") == "message_created", "accepted event type mismatch")

        unread = client.get(
            "/internal/command-center/messages/unread/55",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        )
        expect(unread.status_code == 200, f"unread endpoint returned {unread.status_code}")
        unread_json = unread.get_json() or {}
        expect(unread_json.get("total_unread") == 3, "unread total mismatch")
        expect(isinstance(unread_json.get("conversations"), list), "unread conversations shape is unsafe")

        state = client.get(
            "/internal/command-center/messages/conversation/77/state",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        )
        expect(state.status_code == 200, f"conversation state returned {state.status_code}")
        state_json = state.get_json() or {}
        expect(state_json.get("conversation_id") == 77, "conversation state id mismatch")
        expect(isinstance(state_json.get("event_counts"), dict), "conversation state event_counts shape is unsafe")
        expect(isinstance(state_json.get("typing"), list), "conversation state typing shape is unsafe")

        typing = client.post(
            "/internal/command-center/messages/typing",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={"conversation_id": 77, "sender_id": 44, "is_typing": True},
        )
        expect(typing.status_code == 200, f"typing endpoint returned {typing.status_code}")
        expect((typing.get_json() or {}).get("accepted") is True, "typing event was not accepted")

        serialized = json.dumps(
            {"accepted": accepted_json, "unread": unread_json, "state": state_json, "typing": typing.get_json()},
            sort_keys=True,
        )
        expect(AUDIT_TOKEN not in serialized, "internal token leaked in endpoint payloads")
        expect("must-be-redacted" not in serialized, "sensitive payload field leaked in endpoint responses")
        expect(str(db_path) not in serialized, "database path leaked in endpoint payloads")

        return {
            "missing_token": missing_token.status_code,
            "invalid_event": invalid_event.status_code,
            "accepted": accepted.status_code,
            "unread": unread_json,
            "state": state_json,
            "typing": typing.status_code,
        }


def audit_disabled_main_app_path() -> dict:
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    from services import command_center_client

    event = command_center_client.enqueue_message_event(
        "message_created",
        conversation_id=77,
        message_id=88,
        sender_id=44,
        recipient_id=55,
        payload={"message_type": "text"},
    )
    expect(event.get("ok") is True, "disabled message dispatch should be safe")
    expect(event.get("dispatched") is False, "disabled message dispatch should not call worker")
    expect(event.get("reason") == "disabled", "disabled message dispatch reason mismatch")
    unread = command_center_client.get_unread_counts(55)
    expect(unread.get("available") is False, "disabled unread lookup should report unavailable")
    expect(isinstance(unread.get("conversations"), list), "disabled unread fallback shape mismatch")
    return {"event": event, "unread": unread}


def main() -> int:
    compiled = compile_targets()
    endpoints = audit_worker_endpoints()
    disabled = audit_disabled_main_app_path()
    report = {
        "ok": True,
        "compiled": compiled,
        "worker_imports": True,
        "messaging_module_imports": True,
        "protected_endpoints_reject_missing_token": endpoints["missing_token"] == 401,
        "valid_token_accepts_message_event": endpoints["accepted"] == 200,
        "invalid_event_type_rejected": endpoints["invalid_event"] == 400,
        "unread_endpoint_safe_shape": isinstance(endpoints["unread"].get("conversations"), list),
        "conversation_state_safe_shape": isinstance(endpoints["state"].get("event_counts"), dict),
        "typing_endpoint_accepts_valid_payload": endpoints["typing"] == 200,
        "worker_disabled_main_app_path_safe": disabled["event"].get("reason") == "disabled",
        "secrets_printed": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
