#!/usr/bin/env python3
"""Audit PulseSoc Notification Phase 2 delivery adapters.

The runtime section uses an isolated SQLite database and intentionally leaves
provider credentials unset. Passing therefore proves safe config-missing and
no-device behavior without sending real push, email, or SMS.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = Path(tempfile.gettempdir()) / "pulsesoc-notification-delivery-phase2-audit"
TMP_DIR.mkdir(parents=True, exist_ok=True)
TMP_DB = TMP_DIR / "notification_delivery_phase2.sqlite3"
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


def run_runtime_audit() -> dict:
    conn = db_service.connect()
    cur = conn.cursor()
    setup_runtime_schema(cur)
    notifications.ensure_schema(conn)
    cur.execute(
        """
        INSERT INTO users (user_id, email, phone, phone_number, phone_verified, sms_opt_in, display_name, username)
        VALUES (7001, 'delivery-audit@example.com', '+17185551234', '+17185551234', 1, 1, 'Delivery Audit', 'delivery_audit')
        """
    )
    conn.commit()
    conn.close()

    notifications.update_preferences(
        7001,
        {
            "experience": {"enable_push_notifications": True, "enable_notification_sound": True, "enable_notification_vibration": True},
            "preferences": {
                "security": {"in_app": True, "push": True, "email": True, "sms": True, "sound": True, "vibration": True, "lock_screen_preview": False},
                "messages": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
            },
        },
    )
    created = notifications.intake_event(
        event_type="security_login_alert",
        recipient_user_id=7001,
        title="Security login alert",
        body="A new login needs review.",
        deep_link="/dashboard/account/security",
        category="security",
        priority="urgent",
        channels=["in_app", "push", "email", "sms"],
        dedupe_key="phase2-security-7001",
    )
    require(created.get("ok") is True and int(created.get("notification_id") or 0) > 0, "urgent security notification created")
    processed = notifications.process_delivery_jobs(limit=20)
    statuses = {item["channel"]: item["status"] for item in processed.get("results", [])}
    require(statuses.get("push") == "skipped_no_device", "push safely skips with no active device")
    require(statuses.get("email") == "config_missing", "Brevo email safely marks config_missing")
    require(statuses.get("sms") == "config_missing", "Brevo SMS safely marks config_missing when provider disabled")

    conn = db_service.connect()
    cur = conn.cursor()
    require(scalar(cur, "SELECT COUNT(*) FROM notification_delivery_jobs WHERE status='ready' AND channel='in_app'") == 1, "in-app job remains ready")
    require(scalar(cur, "SELECT COUNT(*) FROM notifications WHERE sound_key!='' AND vibration_json!=''") >= 1, "sound and vibration metadata stored")
    conn.close()

    notifications.register_device_token(7001, {"platform": "android", "device_id": "audit-android", "push_token": "fcm-token-audit", "push_provider": "fcm"}, "AuditAgent")
    notifications.register_device_token(7001, {"platform": "ios", "device_id": "audit-ios", "push_token": "apns-token-audit", "push_provider": "apns"}, "AuditAgent")
    native_created = notifications.intake_event(
        event_type="new_message",
        recipient_user_id=7001,
        actor_user_id=8001,
        title="New message",
        body="A message arrived.",
        deep_link="/pulse/messages/233",
        metadata={"conversation_id": 233},
        channels=["push"],
        dedupe_key="phase2-native-push-7001",
    )
    require(native_created.get("ok") is True, "native push job created")
    native_processed = notifications.process_delivery_jobs(limit=20)
    require(any(item["channel"] == "push" and item["status"] == "config_missing" for item in native_processed.get("results", [])), "FCM/APNs safely mark config_missing without credentials")

    return {
        "processed": processed.get("counts"),
        "native_processed": native_processed.get("counts"),
        "router": notifications.delivery_router_status(),
    }


def run_static_audit() -> None:
    service = read("services/pulsesoc_notification_system.py")
    push = read("services/push_service.py")
    sw = read("static/service-worker.js")
    js = read("static/notifications.js")
    bot = read("bot.py")
    env = read(".env.example")
    migration = read("migrations/pulsesoc_notification_delivery_phase2.sql")
    report_foundation = read("reports/notification_system_foundation.md")

    for token in [
        "_dispatch_push",
        "_dispatch_email",
        "_dispatch_sms",
        "_send_fcm_token",
        "_send_apns_token",
        "process_delivery_jobs",
        "config_missing",
        "skipped_no_device",
        "skipped_no_contact",
        "provider_response_json",
    ]:
        require(token in service, f"service contains {token}")
    require("WEB_PUSH_PUBLIC_KEY" in push and "WEB_PUSH_PRIVATE_KEY" in push, "push service supports WEB_PUSH env aliases")
    require("safeNotificationUrl" in sw and "notificationclick" in sw and "deep_link" in sw, "service worker sanitizes and opens deep links")
    require("vibration" in sw and "sound_key" in sw and "CACHE_NAME = \"coinplotx-cache-v23-notification-delivery-adapters\"" in sw, "service worker supports sound/vibration metadata and cache bump")
    require("showPushPermissionOnboarding" in js and "Notification.requestPermission" in js and "Push permission was not granted" in js, "frontend has permission onboarding before browser prompt")
    require("/api/admin/notifications/process-delivery" in bot and "api_admin_notifications_test_event" in bot, "admin-only test/process delivery routes exist")
    require("WEB_PUSH_PUBLIC_KEY" in bot and "VAPID_PUBLIC_KEY" in bot, "push public key endpoint supports new and legacy names")
    for env_key in [
        "WEB_PUSH_PUBLIC_KEY",
        "WEB_PUSH_PRIVATE_KEY",
        "WEB_PUSH_SUBJECT",
        "FCM_PROJECT_ID",
        "FCM_CLIENT_EMAIL",
        "FCM_PRIVATE_KEY",
        "FCM_SERVER_KEY",
        "APNS_TEAM_ID",
        "APNS_KEY_ID",
        "APNS_PRIVATE_KEY",
        "APNS_BUNDLE_ID",
        "APNS_USE_SANDBOX",
        "BREVO_SMS_SENDER",
    ]:
        require(env_key in env, f".env.example contains {env_key}")
    require("AUTOINCREMENT" not in migration.upper(), "Phase 2 migration is PostgreSQL-compatible additive SQL")
    require("Phase 1" in report_foundation, "Phase 1 report remains present")


def main() -> int:
    run_static_audit()
    runtime = run_runtime_audit()
    result = {"ok": not failures, "failures": failures, "runtime": runtime}
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
