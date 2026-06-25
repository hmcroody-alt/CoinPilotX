#!/usr/bin/env python3
"""Verify queued message pushes are drained even when no dedicated worker is running."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.NamedTemporaryFile(prefix="pulse-push-opportunistic-", suffix=".db", delete=False)
tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp.name}"
os.environ["PUSH_ASYNC_DELIVERY_ENABLED"] = "1"
os.environ["PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "1"
os.environ["PUSH_OPPORTUNISTIC_PROCESSOR_LIMIT"] = "5"

from services import push_service, user_context  # noqa: E402


TOKEN = "ExpoPushToken[opportunistic-audit]"


class FakeResponse:
    ok = True
    status_code = 200
    content = b"{}"

    def json(self):
        return {"data": {"status": "ok", "id": "opportunistic-ticket-1"}}


def expect(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message} failed{': ' + details if details else ''}")
    print(f"ok - {message}")


def setup():
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, endpoint TEXT UNIQUE,
            subscription_json TEXT, p256dh TEXT, auth TEXT, user_agent TEXT,
            device_type TEXT, browser TEXT, active INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, last_seen_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE pulse_notification_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, endpoint TEXT UNIQUE,
            active INTEGER DEFAULT 1, updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE pulse_notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, notification_id INTEGER, user_id INTEGER,
            channel TEXT, provider TEXT, status TEXT, error_message TEXT,
            provider_response TEXT, created_at TEXT, sent_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def job_statuses():
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) AS total FROM push_delivery_jobs GROUP BY status")
    rows = {str(row[0]): int(row[1]) for row in cur.fetchall()}
    conn.close()
    return rows


def main():
    setup()
    calls = []

    def fake_post(url, json=None, **_kwargs):
        calls.append({"url": url, "json": json or {}})
        return FakeResponse()

    original_post = push_service.requests.post
    push_service.requests.post = fake_post
    try:
        saved = push_service.save_subscription(
            88101,
            {"endpoint": TOKEN, "token": TOKEN, "device_id": "opportunistic-device"},
            device_type="native",
            browser="Expo",
        )
        expect(saved.get("ok"), "native Expo token registration succeeds", str(saved))
        queued = push_service.enqueue_push(
            88101,
            "PulseSoc",
            "Out-of-app message audit",
            {
                "notification_id": 9101,
                "conversationId": 44,
                "messageId": 55,
                "type": "chat_message",
                "push_type": "chat_message",
                "native_url": "pulse://pulse/messages-v2?conversation=44",
                "web_url": "/pulse/messages/44",
            },
            push_type="chat_message",
            notification_id=9101,
        )
        expect(queued.get("status") == "queued", "message push job is queued", str(queued))
        deadline = time.time() + 3
        while time.time() < deadline and not calls:
            time.sleep(0.05)
        send_calls = [call for call in calls if str(call.get("url") or "").endswith("/push/send")]
        expect(len(send_calls) == 1, "opportunistic processor calls Expo send provider without dedicated worker", str(calls))
        payload = send_calls[0]["json"]
        expect(payload.get("to") == TOKEN, "provider request targets saved native token", str(payload))
        expect(payload.get("channelId") == "pulse-messages-v2", "dedicated message channel is used for chat delivery", str(payload))
        expect(payload.get("sound") == "default" and payload.get("priority") == "high", "sound and high priority are preserved", str(payload))
        expect(payload.get("interruptionLevel") == "active", "message interruption level is preserved", str(payload))
        data = payload.get("data") or {}
        expect(data.get("conversationId") == 44 and data.get("messageId") == 55, "exact conversation/message ids are included", str(data))
        expect(data.get("type") == "chat_message" and data.get("push_type") == "chat_message", "chat push type is preserved", str(data))
        expect(data.get("native_url") == "pulse://pulse/messages-v2?conversation=44", "native deep link is included", str(data))
        deadline = time.time() + 2
        statuses = job_statuses()
        while time.time() < deadline and statuses.get("sent") != 1:
            time.sleep(0.05)
            statuses = job_statuses()
        expect(statuses.get("sent") == 1, "queued job is marked sent", str(statuses))
    finally:
        push_service.requests.post = original_post
        Path(tmp.name).unlink(missing_ok=True)
    print("push opportunistic delivery audit ok")


if __name__ == "__main__":
    main()
