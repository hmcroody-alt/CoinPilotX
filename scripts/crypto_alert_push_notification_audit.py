#!/usr/bin/env python3
"""Audit locked-screen push wiring for crypto alert triggers.

The audit uses an isolated SQLite database and disables async provider dispatch
so it can prove the crypto alert worker creates central notification records and
push delivery jobs without contacting APNs, FCM, Web Push, Brevo, or SMS.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = Path(tempfile.gettempdir()) / "pulsesoc-crypto-alert-push-audit"
TMP_DIR.mkdir(parents=True, exist_ok=True)
TMP_DB = TMP_DIR / "crypto_alert_push.sqlite3"
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

sys.path.insert(0, str(ROOT))

from services import alert_engine  # noqa: E402
from services import db as db_service  # noqa: E402
from services import pulsesoc_notification_system as notifications  # noqa: E402


failures: list[str] = []
results: dict[str, object] = {}


def require(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def read(path: str) -> str:
    return (ROOT / path).read_text()


def row_count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int((row[0] if row else 0) or 0)


def fetch_one(cur, sql: str, params: tuple = ()) -> dict:
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else {}


def setup_schema() -> None:
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
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            alert_type TEXT,
            symbol TEXT,
            target TEXT,
            condition TEXT,
            threshold_value REAL,
            target_value REAL,
            channels_json TEXT,
            channels TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1,
            cooldown_seconds INTEGER DEFAULT 900,
            last_checked_at TEXT,
            last_triggered_at TEXT,
            trigger_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_rule_id INTEGER,
            user_id INTEGER,
            watch_rule_id INTEGER,
            symbol TEXT,
            alert_type TEXT,
            condition TEXT,
            threshold_value REAL,
            observed_value REAL,
            title TEXT,
            body TEXT,
            status TEXT,
            message TEXT,
            metadata TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_delivery_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            notification_id INTEGER,
            alert_rule_id INTEGER,
            alert_event_id INTEGER,
            channel TEXT,
            status TEXT,
            provider TEXT,
            provider_response TEXT,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            created_at TEXT,
            sent_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_delivery_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            user_id INTEGER,
            channel TEXT,
            status TEXT,
            provider TEXT,
            provider_message_id TEXT,
            error_message TEXT,
            attempts INTEGER DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT,
            sent_at TEXT
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
            (9401, "crypto-recipient@example.com", "+17185559401", "+17185559401", 1, 1, "Crypto Recipient", "crypto_recipient"),
            (9402, "push-disabled@example.com", "", "", 0, 0, "Push Disabled", "push_disabled"),
        ],
    )
    conn.commit()
    conn.close()


def configure_preferences() -> None:
    notifications.register_device_token(
        9401,
        {
            "platform": "ios",
            "device_id": "crypto-alert-audit-ios",
            "push_provider": "apns",
            "push_token": "audit-crypto-apns-token",
            "app_version": "audit",
        },
        user_agent="PulseSocCryptoAudit/1.0",
    )
    notifications.update_preferences(
        9401,
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
                "crypto": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
            },
        },
    )
    notifications.update_preferences(
        9402,
        {
            "experience": {
                "enable_push_notifications": False,
                "enable_notification_sound": True,
                "enable_notification_vibration": True,
                "muted_user_ids": [],
                "muted_conversation_ids": [],
                "blocked_user_ids": [],
            },
            "preferences": {
                "crypto": {"in_app": True, "push": True, "email": False, "sms": False, "sound": True, "vibration": True, "lock_screen_preview": True},
            },
        },
    )


def delivery_channels(notification_id: int) -> set[str]:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT channel FROM notification_delivery_jobs WHERE notification_id=?", (int(notification_id),))
    channels = {str(row[0]) for row in cur.fetchall()}
    conn.close()
    return channels


def latest_crypto_notification(user_id: int) -> dict:
    conn = db_service.connect()
    cur = conn.cursor()
    row = fetch_one(
        cur,
        """
        SELECT id, category, priority, deep_link, source_type, source_id, sound_key, vibration_json, metadata_json
        FROM notifications
        WHERE recipient_user_id=? AND type='crypto_alert_triggered'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(user_id),),
    )
    conn.close()
    return row


def dispatch_rule(user_id: int, rule_id: int, event_id: int, channels: dict, observed_value: float = 70500.0) -> dict:
    rule = {
        "id": rule_id,
        "user_id": user_id,
        "alert_type": "coin_price",
        "symbol": "BTC",
        "condition": "above",
        "threshold_value": 70000.0,
        "channels": channels,
        "status": "active",
        "cooldown_seconds": 900,
    }
    event = {
        "id": event_id,
        "alert_rule_id": rule_id,
        "user_id": user_id,
        "symbol": "BTC",
        "alert_type": "coin_price",
        "condition": "above",
        "threshold_value": 70000.0,
        "observed_value": observed_value,
        "status": "triggered",
        "message": "BTC crossed above $70,000. Live observed value: $70,500.",
    }
    return alert_engine.dispatch_alert_event(event, rule)


def run_static_audit() -> None:
    alert_engine_source = read("services/alert_engine.py")
    notification_source = read("services/pulsesoc_notification_system.py")
    locked_audit_source = read("scripts/locked_device_push_notification_audit.py")
    require("pulsesoc_notification_system.notify_crypto_alert" in alert_engine_source, "crypto alert worker calls central crypto notification helper")
    dispatch_block = alert_engine_source.split("def dispatch_alert_event", 1)[1].split("def evaluate_all_active_alerts", 1)[0]
    require("send_push_alert" not in dispatch_block, "crypto alert worker does not bypass central push delivery")
    require("dispatch_universal_notification" not in dispatch_block, "crypto alert worker does not create legacy-only in-app notifications")
    require("alert_type: str = \"price_target_reached\"" in notification_source, "crypto helper supports alert_type")
    require("trigger_price" in notification_source and "target_price" in notification_source, "crypto helper stores trigger and target prices")
    require("trigger_window" in notification_source, "crypto helper has dedupe trigger window")
    require("/pulse/alerts/" in notification_source, "crypto helper uses an alert-specific deep link")
    require("crypto_alert" in locked_audit_source and "notify_crypto_alert" in locked_audit_source, "existing locked-device social audit still covers crypto helper compatibility")


def run_runtime_audit() -> None:
    eligible = dispatch_rule(9401, 88001, 99001, {"in_app": True, "push": True, "email": False, "sms": False, "telegram": False})
    notification = latest_crypto_notification(9401)
    notification_id = int(notification.get("id") or 0)
    channels = delivery_channels(notification_id)
    results["eligible_crypto_alert"] = {
        "delivery": eligible,
        "notification": notification,
        "channels": sorted(channels),
    }
    require(notification_id > 0, "eligible crypto alert creates a central notification")
    require(notification.get("category") == "crypto", "eligible crypto alert category is crypto")
    require(notification.get("priority") == "high", "eligible price target crypto alert priority is high")
    require(notification.get("deep_link") == "/pulse/alerts/88001", "eligible crypto alert deep link targets the alert route")
    require("push" in channels, "eligible crypto alert creates a push delivery job")
    require("in_app" in channels, "eligible crypto alert creates an in-app delivery job")
    require(bool(notification.get("sound_key")), "eligible crypto alert includes sound metadata")
    require(bool(notification.get("vibration_json")), "eligible crypto alert includes vibration metadata")

    duplicate = dispatch_rule(9401, 88001, 99001, {"in_app": True, "push": True, "email": False, "sms": False, "telegram": False})
    conn = db_service.connect()
    cur = conn.cursor()
    same_alert_notifications = row_count(
        cur,
        "SELECT COUNT(*) FROM notifications WHERE recipient_user_id=? AND type='crypto_alert_triggered' AND source_id=?",
        (9401, "88001"),
    )
    same_push_jobs = row_count(
        cur,
        """
        SELECT COUNT(*)
        FROM notification_delivery_jobs j
        JOIN notifications n ON n.id=j.notification_id
        WHERE n.recipient_user_id=? AND n.source_id=? AND j.channel='push'
        """,
        (9401, "88001"),
    )
    conn.close()
    results["duplicate_crypto_alert"] = {"delivery": duplicate, "notification_count": same_alert_notifications, "push_job_count": same_push_jobs}
    require(same_alert_notifications == 1, "same crypto alert event does not create duplicate notifications")
    require(same_push_jobs == 1, "same crypto alert event does not create duplicate push jobs")

    time.sleep(1)
    later = dispatch_rule(9401, 88001, 99002, {"in_app": True, "push": True, "email": False, "sms": False, "telegram": False}, observed_value=70600.0)
    conn = db_service.connect()
    cur = conn.cursor()
    later_count = row_count(
        cur,
        "SELECT COUNT(*) FROM notifications WHERE recipient_user_id=? AND type='crypto_alert_triggered' AND source_id=?",
        (9401, "88001"),
    )
    conn.close()
    results["later_crypto_alert"] = {"delivery": later, "notification_count": later_count}
    require(later_count == 2, "new crypto alert event after a new trigger window can create a fresh notification")

    push_disabled = dispatch_rule(9402, 88002, 99003, {"in_app": True, "push": True, "email": False, "sms": False, "telegram": False})
    disabled_notification = latest_crypto_notification(9402)
    disabled_channels = delivery_channels(int(disabled_notification.get("id") or 0))
    results["push_disabled_crypto_alert"] = {
        "delivery": push_disabled,
        "notification": disabled_notification,
        "channels": sorted(disabled_channels),
    }
    require(int(disabled_notification.get("id") or 0) > 0, "push-disabled user still receives in-app crypto alert")
    require("push" not in disabled_channels, "push-disabled user does not get a push delivery job")

    no_device = notifications.process_delivery_jobs(limit=10, channels=["push"])
    results["push_delivery_processing_without_provider"] = no_device
    require(no_device.get("ok") is True, "push delivery processor records provider/device skip states safely")


def main() -> int:
    setup_schema()
    configure_preferences()
    run_static_audit()
    run_runtime_audit()
    conn = db_service.connect()
    cur = conn.cursor()
    summary = {
        "ok": not failures,
        "failures": failures,
        "notifications": row_count(cur, "SELECT COUNT(*) FROM notifications"),
        "push_jobs": row_count(cur, "SELECT COUNT(*) FROM notification_delivery_jobs WHERE channel='push'"),
        "in_app_jobs": row_count(cur, "SELECT COUNT(*) FROM notification_delivery_jobs WHERE channel='in_app'"),
        "delivery_logs": row_count(cur, "SELECT COUNT(*) FROM notification_delivery_logs"),
        "results": results,
    }
    conn.close()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
