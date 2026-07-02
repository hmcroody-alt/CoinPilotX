#!/usr/bin/env python3
"""Audit locked-device push eligibility across PulseSoc notification events.

The test uses an isolated SQLite database and disables async delivery so it can
prove each eligible event creates a central push delivery job without contacting
APNs, FCM, Web Push, Brevo, or SMS providers.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = Path(tempfile.gettempdir()) / "pulsesoc-locked-push-audit"
TMP_DIR.mkdir(parents=True, exist_ok=True)
TMP_DB = TMP_DIR / "locked_push.sqlite3"
if TMP_DB.exists():
    TMP_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB}"
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
for key in [
    "WEB_PUSH_PUBLIC_KEY",
    "WEB_PUSH_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
    "FCM_SERVER_KEY",
    "FCM_PROJECT_ID",
    "FCM_CLIENT_EMAIL",
    "FCM_PRIVATE_KEY",
    "APNS_TEAM_ID",
    "APNS_KEY_ID",
    "APNS_PRIVATE_KEY",
    "APNS_BUNDLE_ID",
    "BREVO_API_KEY",
    "BREVO_SMS_API_KEY",
]:
    os.environ.pop(key, None)

import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

from services import db as db_service  # noqa: E402
from services import pulsesoc_notification_system as notifications  # noqa: E402


failures: list[str] = []
results: dict[str, dict] = {}


def require(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def read(path: str) -> str:
    return (ROOT / path).read_text()


def row_count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int((row[0] if row else 0) or 0)


def setup_runtime_schema() -> None:
    conn = db_service.connect()
    cur = conn.cursor()
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
    notifications.ensure_schema(conn)
    cur.executemany(
        """
        INSERT INTO users (user_id, email, phone, phone_number, phone_verified, sms_opt_in, display_name, username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (9201, "actor@example.com", "", "", 0, 0, "Push Actor", "push_actor"),
            (9202, "recipient@example.com", "+17185559202", "+17185559202", 1, 1, "Push Recipient", "push_recipient"),
            (9203, "quiet@example.com", "", "", 0, 0, "Quiet Recipient", "quiet_recipient"),
            (9204, "blocked@example.com", "", "", 0, 0, "Blocked Actor", "blocked_actor"),
        ],
    )
    cur.execute("INSERT INTO blocked_users (blocker_user_id, blocked_user_id) VALUES (9202, 9204)")
    conn.commit()
    conn.close()


def configure_recipient() -> None:
    notifications.register_device_token(
        9202,
        {
            "platform": "ios",
            "device_id": "locked-push-audit-ios",
            "push_provider": "apns",
            "push_token": "audit-apns-token",
            "app_version": "audit",
        },
        user_agent="PulseSocAudit/1.0",
    )
    notifications.update_preferences(
        9202,
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
                "likes": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
                "reposts": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
            },
        },
    )
    notifications.update_preferences(
        9203,
        {
            "experience": {
                "enable_push_notifications": True,
                "enable_notification_sound": True,
                "enable_notification_vibration": True,
                "muted_user_ids": [],
                "muted_conversation_ids": [],
                "blocked_user_ids": [],
            }
        },
    )


def delivery_channels(notification_id: int) -> set[str]:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT channel FROM notification_delivery_jobs WHERE notification_id=?", (int(notification_id),))
    channels = {str(row[0]) for row in cur.fetchall()}
    conn.close()
    return channels


def notification_payload(notification_id: int) -> dict:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT category, priority, deep_link, sound_key, vibration_json FROM notifications WHERE id=? LIMIT 1",
        (int(notification_id),),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {}
    return {
        "category": row[0],
        "priority": row[1],
        "deep_link": row[2],
        "sound_key": row[3],
        "vibration_json": row[4],
    }


def assert_push_event(label: str, result: dict) -> None:
    notification_id = int(result.get("notification_id") or 0)
    payload = notification_payload(notification_id)
    channels = delivery_channels(notification_id)
    results[label] = {
        "notification_id": notification_id,
        "channels": sorted(channels),
        **payload,
    }
    require(result.get("ok") is True and notification_id > 0, f"{label} creates a notification")
    require("push" in channels, f"{label} creates a push delivery job")
    require(bool(payload.get("deep_link")), f"{label} includes a deep link")
    require(bool(payload.get("category")), f"{label} includes a category")
    require(bool(payload.get("priority")), f"{label} includes a priority")
    require(bool(payload.get("sound_key")), f"{label} includes a sound key")
    require(bool(payload.get("vibration_json")), f"{label} includes vibration metadata")


def run_event_audit() -> None:
    assert_push_event(
        "comment",
        notifications.notify_post_comment(9202, 9201, 3001, 4001, "Locked phone comment", actor_name="Push Actor"),
    )
    assert_push_event(
        "new_message",
        notifications.notify_new_message(9202, 9201, 44, 5001, "Locked phone message", actor_name="Push Actor"),
    )
    assert_push_event(
        "image_message",
        notifications.notify_new_message(9202, 9201, 44, 5002, "", media_type="photo", actor_name="Push Actor"),
    )
    assert_push_event(
        "voice_message",
        notifications.notify_new_message(9202, 9201, 44, 5003, "", media_type="voice", actor_name="Push Actor"),
    )
    assert_push_event(
        "video_message",
        notifications.notify_new_message(9202, 9201, 44, 5004, "", media_type="video", actor_name="Push Actor"),
    )
    assert_push_event(
        "missed_call",
        notifications.notify_missed_call(9202, 9201, 44, "call-5005", actor_name="Push Actor"),
    )
    assert_push_event(
        "incoming_call",
        notifications.intake_event(
            "incoming_call",
            9202,
            actor_user_id=9201,
            source_type="call",
            source_id="call-5006",
            title="Incoming call",
            body="Push Actor is calling you.",
            deep_link="/pulse/messages/44?tab=calls",
            metadata={"conversation_id": 44, "call_id": "call-5006"},
            channels=["in_app", "push", "call"],
        ),
    )
    assert_push_event("follow", notifications.notify_follow(9202, 9201, actor_name="Push Actor"))
    assert_push_event("like", notifications.notify_post_like(9202, 9201, 3002, "like", "Push Actor"))
    assert_push_event(
        "repost",
        notifications.intake_event(
            "repost",
            9202,
            actor_user_id=9201,
            source_type="post",
            source_id="3003",
            title="New repost",
            body="Push Actor reposted your post.",
            deep_link="/pulse/post/3003",
            channels=["in_app", "push"],
        ),
    )
    assert_push_event(
        "mention",
        notifications.intake_event(
            "mention",
            9202,
            actor_user_id=9201,
            source_type="post",
            source_id="3004",
            title="You were mentioned",
            body="Push Actor mentioned you.",
            deep_link="/pulse/post/3004",
            channels=["in_app", "push"],
        ),
    )
    assert_push_event("live_started", notifications.notify_live_started(9202, 9201, "live-7001", "Push Actor", "Locked device live"))
    assert_push_event("live_invite", notifications.notify_live_invite(9202, 9201, "live-7002", "Push Actor"))
    assert_push_event("cohost_request", notifications.notify_cohost_request(9202, 9201, "live-7003", "Push Actor"))
    assert_push_event(
        "security_login",
        notifications.notify_security_event(9202, "new_device_login", "New device login", "A new device signed in.", "login-8001"),
    )
    assert_push_event(
        "password_changed",
        notifications.notify_security_event(9202, "password_changed", "Password changed", "Your password was changed.", "password-8002"),
    )
    assert_push_event(
        "payment_failed",
        notifications.notify_payment_event(9202, "payment_failed", "Payment failed", "Update your payment method.", "pi_9001"),
    )
    assert_push_event(
        "premium_activated",
        notifications.notify_payment_event(9202, "founder_premium_activated", "Founder Premium active", "Founder Premium is active.", "sub_9002"),
    )
    assert_push_event(
        "verification_creator",
        notifications.notify_creator_event(9202, "verification_approved", "Verification approved", "Your verification was approved.", "verify-1001", "/pulse/dashboard/verification"),
    )
    assert_push_event(
        "admin_warning",
        notifications.intake_event(
            "admin_warning",
            9202,
            source_type="admin_notice",
            source_id="admin-1002",
            title="Account notice",
            body="Review your PulseSoc account notice.",
            deep_link="/pulse/notifications",
            channels=["in_app", "push", "email"],
        ),
    )
    assert_push_event(
        "crypto_alert",
        notifications.notify_crypto_alert(9202, "btc-1101", "BTC alert", "Your BTC alert was triggered.", "BTC", critical=True),
    )
    assert_push_event(
        "system_announcement",
        notifications.notify_system_announcement(9202, "PulseSoc update", "A system announcement is available.", "sys-1201"),
    )

    noisy_default = notifications.notify_post_like(9203, 9201, 3333, "like", "Push Actor")
    noisy_channels = delivery_channels(int(noisy_default.get("notification_id") or 0))
    require("push" not in noisy_channels, "likes remain preference-controlled when the category is not enabled")

    blocked = notifications.notify_post_comment(9202, 9204, 3005, 4005, "Blocked comment", actor_name="Blocked Actor")
    require(blocked.get("suppressed") is True and blocked.get("reason") == "blocked_actor", "blocked actors do not create pushable comments")


def run_static_audit() -> None:
    service = read("services/pulsesoc_notification_system.py")
    feed = read("services/pulse_feed_engine.py")
    chat = read("services/chat_realtime_service.py")
    legacy = read("services/notification_service.py")
    push_service = read("services/push_service.py")
    sw = read("static/service-worker.js")
    require("notify_post_comment" in feed, "comment path still calls central comment helper")
    require("notify_new_message" in chat, "message path calls central message helper")
    require("LOCKED_DEVICE_PUSH_DEFAULT_CATEGORIES" in service, "central service defines locked-device push defaults")
    require("PREFERENCE_CONTROLLED_PUSH_CATEGORIES" in service, "central service keeps noisy categories preference-controlled")
    require("_default_channels_for_event" in service, "legacy bridge has event-to-channel inference")
    require("channels=list(channels)" in service and 'channels=["in_app"]' not in service.split("def notify_legacy_event", 1)[1].split("def format_notification", 1)[0], "legacy bridge no longer forces in-app only")
    require("notification_delivery_jobs" in service and "channel, provider, status" in service, "central push delivery jobs remain modeled")
    require("push_service.send_push" in service, "central dispatcher routes Web Push through push service")
    require("PULSESOC_NOTIFICATION_INVALID_PUSH_TOKEN_DISABLED" in service, "invalid APNs/FCM tokens are disabled")
    require("process_push_delivery_jobs" in push_service and "send_push" in push_service, "legacy working push queue/provider path remains available")
    require("notificationclick" in sw and "deep_link" in sw, "service worker handles push deep links")


def main() -> int:
    setup_runtime_schema()
    configure_recipient()
    run_static_audit()
    run_event_audit()
    conn = db_service.connect()
    cur = conn.cursor()
    push_jobs = row_count(cur, "SELECT COUNT(*) FROM notification_delivery_jobs WHERE channel='push'")
    in_app_jobs = row_count(cur, "SELECT COUNT(*) FROM notification_delivery_jobs WHERE channel='in_app'")
    device_count = row_count(cur, "SELECT COUNT(*) FROM notification_device_tokens WHERE user_id=9202 AND enabled=1")
    conn.close()
    report = {
        "ok": not failures,
        "failures": failures,
        "push_jobs": push_jobs,
        "in_app_jobs": in_app_jobs,
        "active_device_tokens": device_count,
        "events": results,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
