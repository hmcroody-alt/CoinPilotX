#!/usr/bin/env python3
"""Audit PulseSoc Notification System Foundation.

Runs against an isolated SQLite database so it can verify schema, rules,
dedupe, unread counts, mark-read behavior, device tokens, and route wiring
without touching production/local user data.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = Path(tempfile.gettempdir()) / "pulsesoc-notification-foundation-audit"
TMP_DIR.mkdir(parents=True, exist_ok=True)
TMP_DB = TMP_DIR / "notification_foundation.sqlite3"
if TMP_DB.exists():
    TMP_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB}"

import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

from services import db as db_service  # noqa: E402
from services import pulsesoc_notification_system as notifications  # noqa: E402


failures: list[str] = []


def require(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def scalar(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return 0
    try:
        return int(row[0] or 0)
    except Exception:
        return 0


def table_columns(cur, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def setup_legacy_mirror(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            actor_user_id INTEGER DEFAULT 0,
            type TEXT,
            title TEXT,
            body TEXT,
            entity_type TEXT,
            entity_id TEXT,
            deep_link TEXT,
            target_url TEXT,
            is_read INTEGER DEFAULT 0,
            read_at TEXT,
            delivery_status TEXT DEFAULT 'created',
            metadata_json TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            blocked_user_id INTEGER,
            created_at TEXT
        )
        """
    )


def run_runtime_audit() -> dict:
    conn = db_service.connect()
    cur = conn.cursor()
    setup_legacy_mirror(cur)
    conn.commit()
    notifications.ensure_schema(conn)
    required_tables = {
        "notifications",
        "notification_events",
        "notification_delivery_jobs",
        "notification_device_tokens",
        "notification_preferences",
    }
    for table in required_tables:
        require(scalar(cur, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)) == 1, f"{table} exists")
    notification_columns = table_columns(cur, "notifications")
    for column in {
        "recipient_user_id",
        "actor_user_id",
        "type",
        "category",
        "priority",
        "urgency",
        "preview",
        "deep_link",
        "source_type",
        "source_id",
        "metadata_json",
        "read_at",
        "seen_at",
        "delivered_at",
        "opened_at",
        "failed_at",
        "failure_reason",
        "dedupe_key",
        "delivery_status",
    }:
        require(column in notification_columns, f"notifications.{column} exists")
    conn.close()

    created = notifications.intake_event(
        event_type="new_message",
        recipient_user_id=1001,
        actor_user_id=1002,
        source_type="conversation",
        source_id="233",
        title="New message from Maria",
        body="Maria sent you a message.",
        deep_link="/pulse/messages/233",
        metadata={"conversation_id": 233},
        channels=["in_app", "push"],
        dedupe_key="audit-new-message-1001-233",
    )
    require(created.get("ok") is True, "notification event intake works")
    notification_id = int(created.get("notification_id") or 0)
    require(notification_id > 0, "notification record id returned")
    require(created.get("delivery_jobs"), "delivery jobs created")
    require(created.get("pulse_notification_id", 0) > 0, "legacy pulse mirror created")

    duplicate = notifications.intake_event(
        event_type="new_message",
        recipient_user_id=1001,
        actor_user_id=1002,
        source_type="conversation",
        source_id="233",
        title="Duplicate",
        body="Duplicate should not create another row.",
        deep_link="/pulse/messages/233",
        channels=["in_app"],
        dedupe_key="audit-new-message-1001-233",
    )
    require(duplicate.get("deduped") is True, "duplicate prevention works")

    counts = notifications.badge_counts(1001)
    require(counts.get("alert_unread_count") == 1, "unread count is server authoritative")
    fetched = notifications.list_notifications(1001, limit=5)
    require(len(fetched.get("notifications") or []) == 1, "fetch notifications returns own record")
    require(notifications.get_notification(9999, notification_id) is None, "users cannot fetch other users' notifications")
    require((fetched["notifications"][0] or {}).get("deep_link") == "/pulse/messages/233", "deep link returned")

    read = notifications.mark_read(1001, notification_id)
    require(read.get("updated") == 1, "mark one as read works")
    require(read.get("alert_unread_count") == 0, "mark one updates unread count")

    created_two = notifications.intake_event(
        event_type="system_announcement",
        recipient_user_id=1001,
        title="System notice",
        body="Foundation audit notice.",
        deep_link="/pulse/notifications",
        channels=["in_app"],
        dedupe_key="audit-system-1001",
    )
    require(created_two.get("notification_id", 0) > 0, "second notification created")
    all_read = notifications.mark_all_read(1001)
    require(all_read.get("updated") >= 1, "mark all as read works")
    require(all_read.get("alert_unread_count") == 0, "mark all updates unread count")

    notifications.update_preferences(2001, {"experience": {"muted_user_ids": [3001]}})
    muted = notifications.intake_event(
        event_type="comment",
        recipient_user_id=2001,
        actor_user_id=3001,
        source_type="post",
        source_id="55",
        body="Muted actor should not notify.",
        channels=["in_app"],
        dedupe_key="audit-muted-actor",
    )
    require(muted.get("suppressed") is True and muted.get("reason") == "muted_actor", "muted user rule suppresses noisy notification")

    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO blocked_users (user_id, blocked_user_id, created_at) VALUES (2002, 3002, 'now')")
    conn.commit()
    conn.close()
    blocked = notifications.intake_event(
        event_type="follow",
        recipient_user_id=2002,
        actor_user_id=3002,
        source_type="profile",
        source_id="3002",
        body="Blocked actor should not notify.",
        channels=["in_app"],
        dedupe_key="audit-blocked-actor",
    )
    require(blocked.get("suppressed") is True and blocked.get("reason") == "blocked_actor", "blocked user rule suppresses social notification")

    device = notifications.register_device_token(
        1001,
        {
            "platform": "ios",
            "device_id": "audit-ios-1",
            "push_token": "ExponentPushToken[audit]",
            "app_version": "audit",
        },
        "AuditAgent",
    )
    require(device.get("ok") is True and device.get("device", {}).get("enabled") is True, "device token registers safely")
    status = notifications.device_status(1001)
    require(status.get("notification_os_active_devices") == 1, "device status counts enabled token")
    disabled = notifications.disable_device_token(1001, "audit-ios-1")
    require(disabled.get("disabled") == 1, "device token disables safely")

    conn = db_service.connect()
    cur = conn.cursor()
    report = {
        "notifications": scalar(cur, "SELECT COUNT(*) FROM notifications"),
        "events": scalar(cur, "SELECT COUNT(*) FROM notification_events"),
        "jobs": scalar(cur, "SELECT COUNT(*) FROM notification_delivery_jobs"),
        "devices": scalar(cur, "SELECT COUNT(*) FROM notification_device_tokens"),
    }
    conn.close()
    return report


def run_static_audit() -> None:
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    service = (ROOT / "services" / "pulsesoc_notification_system.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations" / "pulsesoc_notifications_foundation.sql").read_text(encoding="utf-8")
    notifications_js = (ROOT / "static" / "notifications.js").read_text(encoding="utf-8")
    require("pulsesoc_notification_system" in bot, "bot imports central notification system")
    require("/api/pulse/notifications" in bot, "fetch notifications API exists")
    require("/api/pulse/badge-counts" in bot, "badge count API exists")
    require("/api/pulse/notifications/read-all" in bot, "mark all read API exists")
    require("/api/admin/notifications/test-event" in bot and 'require_admin_api("system.view")' in bot, "admin test route is protected")
    require("notification_delivery_jobs" in service and "dedupe_key" in service, "queue-ready delivery jobs and dedupe exist")
    require("notification_device_tokens" in service and "register_device_token" in service, "device token foundation exists")
    require("APNS_KEY_ID" in service and "FCM_SERVER_KEY" in service and "BREVO_API_KEY" in service, "delivery router placeholders use env names")
    require("/api/pulse/badge-counts" in notifications_js and "data-notification-unread" in notifications_js, "notification frontend foundation exists")
    require("/api/pulse/notifications?limit=12" in notifications_js, "notification dropdown fetches backend list")
    require("AUTOINCREMENT" not in migration.upper(), "migration avoids SQLite-only AUTOINCREMENT")
    require("CREATE TABLE IF NOT EXISTS notification_events" in migration, "migration defines notification_events")
    require("CREATE TABLE IF NOT EXISTS notification_delivery_jobs" in migration, "migration defines notification_delivery_jobs")
    require("CREATE TABLE IF NOT EXISTS notification_device_tokens" in migration, "migration defines notification_device_tokens")


def main() -> int:
    runtime_report = run_runtime_audit()
    run_static_audit()
    payload = {
        "ok": not failures,
        "failures": failures,
        "runtime_report": runtime_report,
        "database_url": str(TMP_DB),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
