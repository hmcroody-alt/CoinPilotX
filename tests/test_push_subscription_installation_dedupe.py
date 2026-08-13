"""Installation-scoped push registration and delivery regression coverage."""

import json
import sqlite3

import pytest

from services import push_service


@pytest.fixture()
def push_db(tmp_path, monkeypatch):
    path = tmp_path / "push.db"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect()
    conn.execute(
        """CREATE TABLE push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, endpoint TEXT UNIQUE,
        subscription_json TEXT, p256dh TEXT, auth TEXT, user_agent TEXT,
        device_type TEXT, browser TEXT, active INTEGER DEFAULT 1,
        is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, last_seen_at TEXT)"""
    )
    conn.commit(); conn.close()
    monkeypatch.setattr(push_service.user_context, "connect", connect)
    monkeypatch.setattr(push_service, "_provider_send_enabled", lambda: True)
    return connect


def registration(installation_id, token):
    return {
        "endpoint": token,
        "token": token,
        "provider": "expo",
        "device_id": installation_id,
        "installation_id": installation_id,
        "platform": "ios",
    }


def active_endpoints(connect, user_id=7):
    conn = connect(); cur = conn.cursor()
    cur.execute("SELECT endpoint FROM push_subscriptions WHERE user_id=? AND active=1 AND is_active=1 ORDER BY endpoint", (user_id,))
    values = [row[0] for row in cur.fetchall()]
    conn.close()
    return values


def test_same_installation_refresh_retires_old_token_and_sends_once(push_db, monkeypatch):
    push_service.save_subscription(7, registration("iphone-a", "ExponentPushToken[old]"))
    push_service.save_subscription(7, registration("iphone-a", "ExponentPushToken[new]"))
    assert active_endpoints(push_db) == ["ExponentPushToken[new]"]
    calls = []
    monkeypatch.setattr(push_service, "_send_expo_push", lambda endpoint, _payload: calls.append(endpoint) or {"ok": True, "status": "sent"})
    result = push_service.send_push(7, "Message", "Hello", {"message_id": 1238}, push_type="message")
    assert result["sent"] == 1
    assert calls == ["ExponentPushToken[new]"]


def test_stable_registration_retires_only_unattributed_legacy_expo(push_db):
    push_service.save_subscription(7, {"endpoint": "ExponentPushToken[legacy]", "provider": "expo"})
    push_service.save_subscription(7, registration("iphone-a", "ExponentPushToken[current]"))
    push_service.save_subscription(7, registration("ipad-b", "ExponentPushToken[ipad]"))
    assert active_endpoints(push_db) == ["ExponentPushToken[current]", "ExponentPushToken[ipad]"]


def test_two_installations_each_receive_one_push(push_db, monkeypatch):
    push_service.save_subscription(7, registration("iphone-a", "ExponentPushToken[a]"))
    push_service.save_subscription(7, registration("ipad-b", "ExponentPushToken[b]"))
    calls = []
    monkeypatch.setattr(push_service, "_send_expo_push", lambda endpoint, _payload: calls.append(endpoint) or {"ok": True, "status": "sent"})
    result = push_service.send_push(7, "Message", "Hello", {"message_id": 1239}, push_type="message")
    assert result["sent"] == 2
    assert set(calls) == {"ExponentPushToken[a]", "ExponentPushToken[b]"}


def test_send_time_dedupe_collapses_exact_token_and_installation_rows():
    rows = [
        (1, "ExponentPushToken[old]", json.dumps({"installation_id": "iphone-a"}), "native", "", "2026-01-01", "2026-01-01"),
        (2, "ExponentPushToken[new]", json.dumps({"installation_id": "iphone-a"}), "native", "", "2026-01-02", "2026-01-02"),
        (3, "ExponentPushToken[new]", json.dumps({}), "native", "", "2026-01-01", "2026-01-01"),
        (4, "ExponentPushToken[new]", json.dumps({}), "native", "", "2026-01-02", "2026-01-02"),
    ]
    deduped = push_service._dedupe_subscription_rows(rows)
    assert [row[0] for row in deduped] == [2]


@pytest.mark.parametrize(
    ("provider_error", "expected_status"),
    [("InvalidCredentials", "invalid"), ("DeviceNotRegistered", "invalid"), ("MessageRateExceeded", "failed")],
)
def test_expo_error_classification(monkeypatch, provider_error, expected_status):
    class Response:
        ok = False
        status_code = 400
        content = b"yes"

        def json(self):
            return {"data": {"status": "error", "details": {"error": provider_error}}}

    monkeypatch.setattr(push_service.requests, "post", lambda *_args, **_kwargs: Response())
    result = push_service._send_expo_push("ExponentPushToken[test]", {"data": {}})
    assert result["status"] == expected_status


def test_invalid_credentials_deactivates_but_temporary_failure_does_not(push_db, monkeypatch):
    push_service.save_subscription(7, registration("iphone-a", "ExponentPushToken[invalid]"))
    monkeypatch.setattr(push_service, "_send_expo_push", lambda *_args: {"ok": False, "status": "invalid", "provider_error": "InvalidCredentials", "message": "invalid"})
    result = push_service.send_push(7, "Message", "Hello", {"message_id": 1240}, push_type="message")
    assert result["invalidated"] == 1
    assert active_endpoints(push_db) == []

    push_service.save_subscription(7, registration("iphone-a", "ExponentPushToken[retry]"))
    monkeypatch.setattr(push_service, "_send_expo_push", lambda *_args: {"ok": False, "status": "failed", "provider_error": "MessageRateExceeded", "message": "retry"})
    result = push_service.send_push(7, "Message", "Hello", {"message_id": 1241}, push_type="message")
    assert result["invalidated"] == 0
    assert active_endpoints(push_db) == ["ExponentPushToken[retry]"]
