"""Regression checks for native crypto-alert push delivery.

Run directly (no pytest required):

    python tests/test_crypto_alert_native_push.py
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_native_alert_push_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import alert_engine, db, pulsesoc_notification_system as notifications  # noqa: E402


def _setup_expo_registration(user_id=77):
    # ``services.db`` resolves DATABASE_URL lazily on every connection, and
    # pytest imports every selected module during collection, so by the time
    # these tests actually run the environment points at whichever module was
    # imported last. Re-asserting the path here keeps this file pointed at its
    # own database instead of writing into a neighbouring test's — which is how
    # it started tripping over the real ``push_subscriptions.endpoint`` UNIQUE
    # constraint that the bare table created below does not have.
    os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, email TEXT)")
    cur.execute("INSERT OR REPLACE INTO users (user_id, email) VALUES (?, ?)", (user_id, "native@example.com"))
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            endpoint TEXT,
            subscription_json TEXT,
            active INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
        )
        """
    )
    token = "ExponentPushToken[native-alert-test]"
    # Both tests in this file register the same token, so the write has to be
    # idempotent rather than relying on the loose local table definition above.
    cur.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (token,))
    cur.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, subscription_json) VALUES (?, ?, ?)",
        (user_id, token, '{"expo_push_token":"ExponentPushToken[native-alert-test]"}'),
    )
    conn.commit()
    conn.close()
    notifications.register_device_token(user_id, {
        "device_id": "ios-test-device",
        "platform": "ios",
        "push_provider": "expo",
        "push_token": token,
        "endpoint": token,
    })
    return user_id, token


def test_expo_is_ready_without_vapid():
    user_id, _ = _setup_expo_registration()
    for key in ("WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"):
        os.environ.pop(key, None)
    readiness = alert_engine.channel_readiness(user_id, browser_permission="native", require_recent_test=False)
    assert readiness["push"]["ready"] is True, readiness["push"]
    assert readiness["push"]["subscription_count"] == 1, readiness["push"]
    assert readiness["push"]["routes"]["expo"] >= 1, readiness["push"]


def test_expo_token_is_not_sent_to_apns_or_duplicated():
    _, token = _setup_expo_registration(user_id=78)
    originals = {
        "active": notifications._active_device_tokens,
        "legacy": notifications._legacy_push_subscription_count,
        "legacy_send": notifications.push_service.send_push,
        "expo_send": notifications.push_service.send_expo_push_token,
        "apns_send": notifications._send_apns_token,
    }
    calls = {"legacy": 0, "expo": 0, "apns": 0}
    try:
        notifications._active_device_tokens = lambda cur, user_id: [{
            "push_provider": "expo", "platform": "ios", "push_token": token,
        }]
        notifications._legacy_push_subscription_count = lambda cur, user_id: 1
        notifications.push_service.send_push = lambda *args, **kwargs: calls.__setitem__("legacy", calls["legacy"] + 1) or {"ok": True, "status": "sent", "provider": "expo"}
        notifications.push_service.send_expo_push_token = lambda *args, **kwargs: calls.__setitem__("expo", calls["expo"] + 1) or {"ok": True, "status": "sent", "provider": "expo"}
        notifications._send_apns_token = lambda *args, **kwargs: calls.__setitem__("apns", calls["apns"] + 1) or {"ok": False}
        result = notifications._dispatch_push(object(), {
            "recipient_user_id": 78,
            "title": "BTC crossed",
            "body": "BTC crossed your target.",
            "type": "crypto_alert_triggered",
        }, {})
        assert result["ok"] is True, result
        assert calls == {"legacy": 1, "expo": 0, "apns": 0}, calls

        notifications._legacy_push_subscription_count = lambda cur, user_id: 0
        result = notifications._dispatch_push(object(), {
            "recipient_user_id": 78,
            "title": "BTC crossed",
            "body": "BTC crossed your target.",
            "type": "crypto_alert_triggered",
        }, {})
        assert result["ok"] is True, result
        assert calls == {"legacy": 1, "expo": 1, "apns": 0}, calls
    finally:
        notifications._active_device_tokens = originals["active"]
        notifications._legacy_push_subscription_count = originals["legacy"]
        notifications.push_service.send_push = originals["legacy_send"]
        notifications.push_service.send_expo_push_token = originals["expo_send"]
        notifications._send_apns_token = originals["apns_send"]


def _run():
    tests = [test_expo_is_ready_without_vapid, test_expo_token_is_not_sent_to_apns_or_duplicated]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")


if __name__ == "__main__":
    _run()
