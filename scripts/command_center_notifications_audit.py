#!/usr/bin/env python3
"""Audit the PulseSoc Command Center notifications pipeline."""

from __future__ import annotations

import json
import os
import py_compile
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "command-center-notifications-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_client.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        ROOT / "services" / "command_center_worker" / "notifications.py",
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


def audit_worker_endpoints() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "notifications_audit.db"
        configure_env(db_path)
        sys.path.insert(0, str(ROOT))

        from services.command_center_worker.app import app
        from services.command_center_worker import notifications

        notifications.ensure_notification_schema()
        client = app.test_client()
        payload = {
            "recipient_id": 55,
            "actor_id": 44,
            "notification_type": "post_comment",
            "title": "New comment",
            "body": "Someone commented on your post.",
            "channel": "in_app",
            "payload": {"post_id": 123, "token": "must-not-leak"},
        }

        missing = client.post("/internal/command-center/notifications/event", json=payload)
        expect(missing.status_code == 401, "notification endpoint should reject missing token")

        accepted = client.post(
            "/internal/command-center/notifications/event",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}", "X-Idempotency-Key": "audit-notification-1"},
            json=payload,
        )
        expect(accepted.status_code == 200, f"valid notification event returned {accepted.status_code}")
        accepted_json = accepted.get_json() or {}
        expect(accepted_json.get("accepted") is True, "valid notification event was not accepted")
        event_id = accepted_json.get("event_id") or ""
        expect(event_id == "audit-notification-1", "notification idempotency key was not preserved")

        unread = client.get(
            "/internal/command-center/notifications/unread/55",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        )
        expect(unread.status_code == 200, f"notification unread endpoint returned {unread.status_code}")
        unread_json = unread.get_json() or {}
        expect(unread_json.get("alert_unread_count") == 1, "notification unread count mismatch")

        recent = client.get(
            "/internal/command-center/notifications/recent/55",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        )
        expect(recent.status_code == 200, f"recent notification endpoint returned {recent.status_code}")
        recent_json = recent.get_json() or {}
        expect(isinstance(recent_json.get("notifications"), list), "recent notifications shape is unsafe")
        expect(len(recent_json.get("notifications") or []) == 1, "recent notification was not returned")

        marked = client.post(
            "/internal/command-center/notifications/read",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={"recipient_id": 55, "event_id": event_id},
        )
        expect(marked.status_code == 200, f"mark read returned {marked.status_code}")
        expect((marked.get_json() or {}).get("updated") == 1, "mark read did not update notification")

        unread_after = client.get(
            "/internal/command-center/notifications/unread/55",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        ).get_json() or {}
        expect(unread_after.get("alert_unread_count") == 0, "read notification remained unread")

        message_event = client.post(
            "/internal/command-center/notifications/event",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={**payload, "notification_type": "chat_message", "event_id": "chat-must-not-enter-alerts"},
        )
        expect(message_event.status_code == 400, "chat notification should not enter general alerts pipeline")

        serialized = json.dumps({"accepted": accepted_json, "unread": unread_json, "recent": recent_json}, sort_keys=True)
        expect(AUDIT_TOKEN not in serialized, "internal token leaked in endpoint payloads")
        expect("must-not-leak" not in serialized, "sensitive payload leaked in endpoint payloads")
        expect(str(db_path) not in serialized, "database path leaked in endpoint payloads")
        return {
            "missing": missing.status_code,
            "accepted": accepted.status_code,
            "unread": unread_json,
            "recent": recent_json,
            "marked": marked.get_json() or {},
            "unread_after": unread_after,
            "message_rejected": message_event.status_code,
        }


def audit_badge_separation() -> dict:
    frontend = (ROOT / "static" / "notifications.js").read_text(encoding="utf-8")
    service = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")
    expect('setBadgeNodes("[data-alert-unread], [data-notification-unread]", alertCount)' in frontend, "alerts badge selector is missing")
    expect('setBadgeNodes("[data-chat-unread]", chatCount)' in frontend, "chat badge selector is missing")
    expect("alert_unread_count" in service and "chat_unread_count" in service, "backend badge counts are not separated")
    expect("NOT (" in service and "_message_notification_where_clause" in service, "alert count does not exclude message notifications")
    return {"chat_selector": "data-chat-unread", "alert_selectors": ["data-alert-unread", "data-notification-unread"]}


def audit_disabled_main_app_path() -> dict:
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    from services import command_center_client

    event = command_center_client.enqueue_notification_event(
        55,
        "post_comment",
        "New comment",
        "Someone commented.",
        actor_id=44,
        payload={"post_id": 123},
    )
    expect(event.get("ok") is True and event.get("reason") == "disabled", "disabled notification dispatch should be safe")
    unread = command_center_client.get_notification_unread_count(55)
    recent = command_center_client.get_recent_notifications(55)
    expect(unread.get("available") is False, "disabled unread lookup should report unavailable")
    expect(recent.get("available") is False, "disabled recent lookup should report unavailable")
    return {"event": event, "unread": unread, "recent": recent}


def main() -> int:
    compiled = compile_targets()
    endpoints = audit_worker_endpoints()
    badges = audit_badge_separation()
    disabled = audit_disabled_main_app_path()
    report = {
        "ok": True,
        "compiled": compiled,
        "module_imports": True,
        "protected_endpoints_reject_missing_token": endpoints["missing"] == 401,
        "valid_token_accepts_notification_event": endpoints["accepted"] == 200,
        "unread_endpoint_safe_shape": "alert_unread_count" in endpoints["unread"],
        "recent_endpoint_safe_shape": isinstance(endpoints["recent"].get("notifications"), list),
        "mark_read_works": endpoints["marked"].get("updated") == 1 and endpoints["unread_after"].get("alert_unread_count") == 0,
        "message_events_rejected_from_alert_pipeline": endpoints["message_rejected"] == 400,
        "badge_separation": badges,
        "worker_disabled_main_app_path_safe": disabled["event"].get("reason") == "disabled",
        "secrets_printed": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
