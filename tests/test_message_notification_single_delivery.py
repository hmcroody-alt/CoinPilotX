"""Regression checks for one locked-screen notification per message."""

from __future__ import annotations

import ast
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_message_push_once_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from services import db, notification_service, push_service  # noqa: E402


def _notification_schema():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, preferred_language TEXT, email TEXT)")
    cur.execute("INSERT OR IGNORE INTO users (user_id, preferred_language, email) VALUES (7, 'en', 'recipient@example.com')")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, actor_user_id INTEGER,
            type TEXT, title TEXT, body TEXT, entity_type TEXT, entity_id TEXT,
            deep_link TEXT, target_url TEXT, is_read INTEGER, read_at TEXT,
            delivery_status TEXT, metadata_json TEXT, created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, notification_id INTEGER, user_id INTEGER,
            channel TEXT, provider TEXT, status TEXT, error_message TEXT,
            provider_response TEXT, created_at TEXT, sent_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def test_central_handoff_suppresses_legacy_push():
    _notification_schema()
    originals = {
        "central": notification_service.pulsesoc_notification_system.notify_legacy_event,
        "push": notification_service.send_push_alert,
        "language": notification_service._preferred_language_for_user,
        "badges": notification_service.pulse_badge_counts,
    }
    direct_calls = []
    try:
        notification_service._preferred_language_for_user = lambda user_id: "en"
        notification_service.pulse_badge_counts = lambda user_id: {"chat_unread_count": 1}
        notification_service.pulsesoc_notification_system.notify_legacy_event = lambda *args, **kwargs: {
            "ok": True,
            "notification_id": 901,
            "delivery_jobs": [{"channel": "push", "status": "scheduled"}],
        }
        notification_service.send_push_alert = lambda *args, **kwargs: direct_calls.append(args) or {"ok": True, "status": "queued"}
        result = notification_service.create_pulse_notification(
            7,
            "message",
            "New message",
            "Hello",
            actor_user_id=8,
            entity_type="conversation",
            entity_id="501",
            deep_link="/pulse/messages/22",
            metadata={"message_id": 501, "conversation_id": 22, "push_type": "chat_message"},
        )
        assert result["ok"] is True, result
        assert result["push"]["status"] == "delegated", result
        assert direct_calls == [], "legacy adapter sent a second push after central handoff"
    finally:
        notification_service.pulsesoc_notification_system.notify_legacy_event = originals["central"]
        notification_service.send_push_alert = originals["push"]
        notification_service._preferred_language_for_user = originals["language"]
        notification_service.pulse_badge_counts = originals["badges"]


def test_token_refresh_deactivates_previous_installation_endpoint():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, endpoint TEXT UNIQUE,
            subscription_json TEXT, p256dh TEXT, auth TEXT, user_agent TEXT,
            device_type TEXT, browser TEXT, active INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, last_seen_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    common = {"device_id": "native-ios-stable", "installation_id": "native-ios-stable", "platform": "ios", "provider": "expo"}
    push_service.save_subscription(7, {**common, "endpoint": "ExponentPushToken[old]"}, device_type="native")
    push_service.save_subscription(7, {**common, "endpoint": "ExponentPushToken[new]"}, device_type="native")
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT endpoint, active, is_active FROM push_subscriptions WHERE user_id=7 ORDER BY id")
    rows = [(row[0], int(row[1]), int(row[2])) for row in cur.fetchall()]
    conn.close()
    assert rows == [("ExponentPushToken[old]", 0, 0), ("ExponentPushToken[new]", 1, 1)], rows


def test_central_failure_keeps_one_legacy_fallback():
    _notification_schema()
    originals = {
        "central": notification_service.pulsesoc_notification_system.notify_legacy_event,
        "push": notification_service.send_push_alert,
        "language": notification_service._preferred_language_for_user,
        "badges": notification_service.pulse_badge_counts,
    }
    direct_calls = []
    try:
        notification_service._preferred_language_for_user = lambda user_id: "en"
        notification_service.pulse_badge_counts = lambda user_id: {"chat_unread_count": 1}
        notification_service.pulsesoc_notification_system.notify_legacy_event = lambda *args, **kwargs: {"ok": False}
        notification_service.send_push_alert = lambda *args, **kwargs: direct_calls.append(args) or {"ok": True, "status": "queued"}
        result = notification_service.create_pulse_notification(
            7,
            "message",
            "New message",
            "Fallback",
            actor_user_id=8,
            entity_type="conversation",
            entity_id="502",
            deep_link="/pulse/messages/22",
            metadata={"message_id": 502, "conversation_id": 22, "push_type": "chat_message"},
        )
        assert result["push"]["status"] == "queued", result
        assert len(direct_calls) == 1, direct_calls
    finally:
        notification_service.pulsesoc_notification_system.notify_legacy_event = originals["central"]
        notification_service.send_push_alert = originals["push"]
        notification_service._preferred_language_for_user = originals["language"]
        notification_service.pulse_badge_counts = originals["badges"]


def test_message_finalizer_has_one_notification_owner():
    source = open(os.path.join(_ROOT, "bot.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "pulse_finalize_message_delivery")
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert "create_pulse_notification" in calls, calls
    assert "pulse_emit_comms_safety_event" not in calls, "message finalizer still creates a second user notification"


def _run():
    tests = [
        test_central_handoff_suppresses_legacy_push,
        test_token_refresh_deactivates_previous_installation_endpoint,
        test_central_failure_keeps_one_legacy_fallback,
        test_message_finalizer_has_one_notification_owner,
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")


if __name__ == "__main__":
    _run()
