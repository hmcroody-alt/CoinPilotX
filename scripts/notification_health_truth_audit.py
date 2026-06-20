#!/usr/bin/env python3
"""Audit truthful notification health and non-amplifying setup failures."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    health = read("services/notification_health_engine.py")
    alerts = read("services/alert_engine.py")
    bot = read("bot.py")

    require("setup_skipped_24h" in health, "setup and permission gaps are reported separately")
    require("status IN ('failed','rate_limited')" in health, "only actionable delivery failures reduce recent success")
    require("push_delivery_jobs" in health and "jobs_24h" in health, "durable Messenger push jobs inform push health")
    require('"runtime_scope": "command_center_worker"' in health, "push health identifies the worker runtime")
    require('retryable_job = not setup_status and status != "skipped"' in alerts, "missing optional providers do not create retryable alert jobs")
    require("WHERE alert_event_id=? AND channel=?" in alerts, "duplicate alert event channel logs are suppressed")
    require("setup_status_throttled" in alerts and "created_at>=?" in alerts, "repeated setup gaps are rate-limited in delivery logs")
    require("Actionable Failures 24h" in bot, "admin dashboard labels recent actionable failures")
    require("Setup / Skipped 24h" in bot, "admin dashboard separates setup states")
    require("Invalid or expired security token." in bot and "csrf_token" in bot, "queue actions require CSRF protection")
    require("WHERE status='failed' AND COALESCE(attempts,0)>=5" in bot, "purge action cannot delete setup or skipped records")

    from services import alert_engine, notification_health_engine

    with tempfile.NamedTemporaryFile(prefix="notification-health-", suffix=".db") as tmp:
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE notification_delivery_logs (
                id INTEGER PRIMARY KEY, user_id INTEGER, notification_id INTEGER,
                alert_rule_id INTEGER, alert_event_id INTEGER, channel TEXT,
                status TEXT, provider TEXT, provider_response TEXT,
                error_message TEXT, retry_count INTEGER, created_at TEXT, sent_at TEXT
            );
            CREATE TABLE alert_delivery_jobs (
                id INTEGER PRIMARY KEY, alert_id INTEGER, user_id INTEGER,
                channel TEXT, status TEXT, provider TEXT, provider_message_id TEXT,
                error_message TEXT, attempts INTEGER, next_retry_at TEXT,
                created_at TEXT, sent_at TEXT
            );
            CREATE TABLE push_delivery_jobs (
                id INTEGER PRIMARY KEY, status TEXT, created_at TEXT
            );
            CREATE TABLE push_subscriptions (
                id INTEGER PRIMARY KEY, active INTEGER, is_active INTEGER
            );
            CREATE TABLE user_device_tokens (
                id INTEGER PRIMARY KEY, enabled INTEGER, revoked_at TEXT
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, telegram_chat_id TEXT, sms_opt_in INTEGER
            );
            """
        )
        now = "2999-01-01T00:00:00"
        cur.execute("INSERT INTO notification_delivery_logs(channel,status,created_at) VALUES ('push','not_configured',?)", (now,))
        cur.execute("INSERT INTO notification_delivery_logs(channel,status,created_at) VALUES ('in_app','created',?)", (now,))
        cur.execute("INSERT INTO alert_delivery_jobs(status,attempts,created_at) VALUES ('not_configured',1,?)", (now,))
        cur.execute("INSERT INTO push_delivery_jobs(status,created_at) VALUES ('sent',?)", (now,))
        conn.commit()
        snapshot = notification_health_engine.health_snapshot(conn)
        require(snapshot["failed_24h"] == 0, "not_configured delivery is not counted as an actionable failure")
        require(snapshot["setup_skipped_24h"] == 1, "not_configured delivery remains visible as setup evidence")
        require(snapshot["alert_failed_jobs"] == 0, "historical setup rows are not reported as failed jobs")
        require(snapshot["providers"]["push"]["status"] == "healthy", "recent durable push success proves worker delivery health")
        conn.close()

        original_connect = alert_engine.user_context.connect
        alert_engine.user_context.connect = lambda: sqlite3.connect(tmp.name)
        try:
            alert_engine._log_delivery(7, "email", "not_configured", alert_rule_id=8, alert_event_id=9)
            result = alert_engine._log_delivery(7, "email", "not_configured", alert_rule_id=8, alert_event_id=10)
        finally:
            alert_engine.user_context.connect = original_connect
        conn = sqlite3.connect(tmp.name)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM notification_delivery_logs WHERE alert_rule_id=8 AND channel='email'")
        require(cur.fetchone()[0] == 1 and result.get("reason") == "setup_status_throttled", "setup gaps create at most one audit row per rule and channel per day")
        cur.execute("SELECT COUNT(*) FROM alert_delivery_jobs WHERE alert_id=8")
        require(cur.fetchone()[0] == 0, "setup gaps never enter the retry queue")
        conn.close()
    print("notification_health_truth_audit: PASS")


if __name__ == "__main__":
    main()
