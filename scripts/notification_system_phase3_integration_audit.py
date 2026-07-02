#!/usr/bin/env python3
"""Audit PulseSoc Notification Phase 3 real-event integration.

This audit uses an isolated SQLite database for runtime checks. It verifies
that real event helpers create central records, dedupe repeated events, respect
self/mute suppression, create delivery jobs, and preserve provider-safe Phase 2
behavior without sending real push, email, or SMS.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = Path(tempfile.gettempdir()) / "pulsesoc-notification-phase3-audit"
TMP_DIR.mkdir(parents=True, exist_ok=True)
TMP_DB = TMP_DIR / "notification_phase3.sqlite3"
if TMP_DB.exists():
    TMP_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB}"
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
for key in [
    "WEB_PUSH_PUBLIC_KEY",
    "WEB_PUSH_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
    "BREVO_API_KEY",
    "BREVO_SMS_API_KEY",
    "FCM_SERVER_KEY",
    "FCM_PROJECT_ID",
    "FCM_CLIENT_EMAIL",
    "FCM_PRIVATE_KEY",
    "APNS_TEAM_ID",
    "APNS_KEY_ID",
    "APNS_PRIVATE_KEY",
    "APNS_BUNDLE_ID",
]:
    os.environ.pop(key, None)

import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

from services import db as db_service  # noqa: E402
from services import pulsesoc_notification_system as notifications  # noqa: E402


failures: list[str] = []


def require(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def read(path: str) -> str:
    return (ROOT / path).read_text()


def scalar(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int((row[0] if row else 0) or 0)


def setup_runtime_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            email TEXT,
            phone TEXT,
            phone_number TEXT,
            phone_verified INTEGER DEFAULT 0,
            sms_opt_in INTEGER DEFAULT 0,
            display_name TEXT,
            username TEXT
        )
        """
    )
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
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            endpoint TEXT UNIQUE,
            subscription_json TEXT,
            p256dh TEXT,
            auth TEXT,
            user_agent TEXT,
            device_type TEXT,
            browser TEXT,
            active INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            last_seen_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker_user_id INTEGER,
            blocked_user_id INTEGER
        )
        """
    )


def run_runtime_audit() -> dict:
    conn = db_service.connect()
    cur = conn.cursor()
    setup_runtime_schema(cur)
    notifications.ensure_schema(conn)
    cur.executemany(
        """
        INSERT INTO users (user_id, email, phone, phone_number, phone_verified, sms_opt_in, display_name, username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (9101, "actor@example.com", "", "", 0, 0, "Actor Audit", "actor_audit"),
            (9102, "recipient@example.com", "+17185551002", "+17185551002", 1, 1, "Recipient Audit", "recipient_audit"),
            (9103, "muted@example.com", "", "", 0, 0, "Muted Audit", "muted_audit"),
            (9104, "blocked@example.com", "", "", 0, 0, "Blocked Audit", "blocked_audit"),
        ],
    )
    cur.execute("INSERT INTO blocked_users (blocker_user_id, blocked_user_id) VALUES (9102, 9104)")
    conn.commit()
    conn.close()

    notifications.update_preferences(
        9102,
        {
            "experience": {
                "enable_push_notifications": True,
                "enable_notification_sound": True,
                "enable_notification_vibration": True,
                "muted_user_ids": [],
                "muted_conversation_ids": [],
                "blocked_user_ids": [],
            },
            "preferences": {
                "messages": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
                "social": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
                "comments": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
                "security": {"in_app": True, "push": True, "email": True, "sms": True, "sound": True, "vibration": True, "lock_screen_preview": False},
            },
        },
    )

    message = notifications.notify_new_message(
        recipient_user_id=9102,
        actor_user_id=9101,
        conversation_id=44,
        message_id=1001,
        body="Phase 3 message",
        actor_name="Actor Audit",
    )
    require(message.get("ok") is True and int(message.get("notification_id") or 0) > 0, "message helper creates a central notification")
    duplicate = notifications.notify_new_message(
        recipient_user_id=9102,
        actor_user_id=9101,
        conversation_id=44,
        message_id=1001,
        body="Phase 3 message",
        actor_name="Actor Audit",
    )
    require(duplicate.get("deduped") is True, "message helper dedupes repeated message events")
    self_event = notifications.notify_post_like(9102, 9102, 6001, "like", "Recipient Audit")
    require(self_event.get("suppressed") is True and self_event.get("reason") == "self_notification_suppressed", "self notification is suppressed")

    notifications.update_preferences(
        9103,
        {
            "experience": {
                "enable_push_notifications": True,
                "enable_notification_sound": True,
                "enable_notification_vibration": True,
                "muted_user_ids": [],
                "muted_conversation_ids": [44],
                "blocked_user_ids": [],
            },
            "preferences": {"messages": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True}},
        },
    )
    muted = notifications.notify_new_message(9103, 9101, 44, 1002, "Muted conversation message", actor_name="Actor Audit")
    require(muted.get("suppressed") is True and muted.get("reason") == "muted_conversation", "muted conversation suppresses noisy message notification")

    blocked = notifications.notify_post_comment(9102, 9104, 6001, 7001, "Blocked actor comment", actor_name="Blocked Audit")
    require(blocked.get("suppressed") is True and blocked.get("reason") == "blocked_actor", "blocked actor social notification is suppressed")

    like = notifications.notify_post_like(9102, 9101, 6001, "love", "Actor Audit")
    require(like.get("ok") is True and int(like.get("notification_id") or 0) > 0, "post reaction helper creates notification")
    like_dupe = notifications.notify_post_like(9102, 9101, 6001, "love", "Actor Audit")
    require(like_dupe.get("deduped") is True, "post reaction helper dedupes same actor/post/reaction")

    security = notifications.notify_security_event(9102, "new_device_login", "New device login", "A new device signed in.", source_id="login-audit-1")
    require(security.get("ok") is True and int(security.get("notification_id") or 0) > 0, "security helper creates urgent notification")
    counts = notifications.badge_counts(9102)
    require(int(counts.get("unread_count") or 0) >= 3, "server-side unread badge count increases")
    notifications.mark_read(9102, int(message.get("notification_id") or 0))
    counts_after_read = notifications.badge_counts(9102)
    require(int(counts_after_read.get("unread_count") or 0) < int(counts.get("unread_count") or 0), "mark one read decreases unread count")
    notifications.mark_all_read(9102)
    counts_after_all = notifications.badge_counts(9102)
    require(int(counts_after_all.get("unread_count") or 0) == 0, "mark all read clears unread count")

    processed = notifications.process_delivery_jobs(limit=50)
    statuses = {item["channel"]: item["status"] for item in processed.get("results", [])}
    require("push" in statuses, "push delivery job is created for eligible events")
    require(any(item["status"] in {"skipped_no_device", "config_missing", "ready"} for item in processed.get("results", [])), "provider failures skip safely")

    conn = db_service.connect()
    cur = conn.cursor()
    require(scalar(cur, "SELECT COUNT(*) FROM notification_delivery_jobs WHERE channel='in_app'") >= 3, "in-app delivery jobs created")
    require(scalar(cur, "SELECT COUNT(*) FROM notifications WHERE deep_link LIKE '/pulse/messages/%'") >= 1, "message deep link stored")
    require(scalar(cur, "SELECT COUNT(*) FROM notifications WHERE sound_key!='' AND vibration_json!=''") >= 1, "sound/vibration metadata stored")
    conn.close()
    return {"processed": processed.get("counts"), "router": notifications.delivery_router_status()}


def run_static_audit() -> None:
    service = read("services/pulsesoc_notification_system.py")
    chat = read("services/chat_realtime_service.py")
    feed = read("services/pulse_feed_engine.py")
    legacy = read("services/notification_service.py")
    sw = read("static/service-worker.js")
    notifications_js = read("static/notifications.js")

    for helper in [
        "notify_new_message",
        "notify_missed_call",
        "notify_live_started",
        "notify_live_invite",
        "notify_cohost_request",
        "notify_follow",
        "notify_post_like",
        "notify_post_comment",
        "notify_security_event",
        "notify_payment_event",
        "notify_creator_event",
        "notify_crypto_alert",
        "notify_system_announcement",
        "notify_legacy_event",
    ]:
        require(f"def {helper}" in service, f"{helper} helper exists")
    for token in [
        "self_notification_suppressed",
        "muted_conversation",
        "blocked_actor",
        "dedupe_key=",
        "sound_key",
        "vibration",
        "skip_pulse_legacy_mirror",
    ]:
        require(token in service, f"central service includes {token}")
    require("notify_new_message" in chat and "recipient_rows" in chat and "conversation_type" in chat, "Messenger send route calls central message helper")
    require("notify_post_comment" in feed and "notify_post_like" in feed and "notify_follow" in feed, "Feed comment/reaction/follow routes call central helpers")
    require("notify_legacy_event" in legacy and "PULSESOC_CENTRAL_LEGACY_BRIDGE_SKIPPED" in legacy, "legacy notification service bridges to central system")
    require("notificationclick" in sw and "deep_link" in sw, "service worker notification click deep links remain wired")
    require("refreshNotificationList" in notifications_js and "markAllReadAction" in notifications_js, "notification center frontend remains wired")
    require("javascript:void(0)" not in notifications_js, "notification UI has no javascript:void dead links")


def main() -> int:
    run_static_audit()
    runtime = run_runtime_audit()
    report = {"ok": not failures, "failures": failures, "runtime": runtime}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
