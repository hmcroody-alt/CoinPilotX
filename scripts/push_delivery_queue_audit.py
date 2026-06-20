#!/usr/bin/env python3
"""Audit durable asynchronous push delivery without contacting real providers."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.NamedTemporaryFile(prefix="pulse-push-queue-", suffix=".db", delete=False)
tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp.name}"
os.environ["PUSH_ASYNC_DELIVERY_ENABLED"] = "1"
os.environ["PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"

from services import push_service, user_context  # noqa: E402


TOKEN = "ExpoPushToken[queue-audit]"


class FakeResponse:
    ok = True
    status_code = 200
    content = b"{}"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def expect(condition, message):
    if not condition:
        raise AssertionError(message)
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


def count_jobs():
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM push_delivery_jobs GROUP BY status ORDER BY status")
    rows = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return rows


def main():
    setup()
    calls = []

    def fake_post(url, json=None, **_kwargs):
        calls.append((url, json or {}))
        return FakeResponse({"data": {"status": "ok", "id": "queue-ticket-1"}})

    original_post = push_service.requests.post
    push_service.requests.post = fake_post
    try:
        saved = push_service.save_subscription(
            701,
            {"endpoint": TOKEN, "token": TOKEN, "device_id": "queue-device"},
            device_type="native",
            browser="Expo",
        )
        expect(saved.get("ok"), "token registration succeeds")
        first = push_service.enqueue_push(
            701,
            "PulseSoc",
            "Queue audit",
            {"notification_id": 3001, "conversationId": 44, "messageId": 55},
            push_type="message",
            notification_id=3001,
        )
        second = push_service.enqueue_push(
            701,
            "PulseSoc",
            "Queue audit",
            {"notification_id": 3001, "conversationId": 44, "messageId": 55},
            push_type="message",
            notification_id=3001,
        )
        expect(first.get("status") == "queued", "first push job queued")
        expect(second.get("duplicate") is True, "duplicate push job deduped")
        expect(count_jobs().get("pending") == 1, "only one pending job exists")
        expect(calls == [], "provider not called while enqueueing")
        processed = push_service.process_push_delivery_jobs(limit=10)
        expect(processed.get("processed") == 1 and processed.get("sent") == 1, "worker processes queued job")
        expect(len(calls) == 1 and calls[0][0].endswith("/push/send"), "provider called by worker only")
        expect(calls[0][1].get("to") == TOKEN, "queued provider request targets saved token")
        expect(count_jobs().get("sent") == 1, "job status persisted as sent")

        def failing_post(_url, json=None, **_kwargs):
            calls.append((_url, json or {}))
            return FakeResponse({"data": {"status": "error", "details": {"error": "ProviderDown"}}})

        push_service.requests.post = failing_post
        retry_job = push_service.enqueue_push(701, "PulseSoc", "Retry audit", {"messageId": 56}, push_type="message")
        expect(retry_job.get("status") == "queued", "retry test job queued")
        retry_result = push_service.process_push_delivery_jobs(limit=10)
        expect(retry_result.get("retry") == 1, "temporary provider failure schedules retry")
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute("UPDATE push_delivery_jobs SET attempts=5, status='retry', next_retry_at='' WHERE status='retry'")
        conn.commit()
        conn.close()
        dead = push_service.process_push_delivery_jobs(limit=10)
        expect(dead.get("dead_letter") == 1, "max attempts moves job to dead letter")

        source = (ROOT / "services" / "push_service.py").read_text(encoding="utf-8")
        expect("push_token)[:" not in source and "subscription_json)[:1200]" not in source, "push service avoids raw token logging markers")
    finally:
        push_service.requests.post = original_post
        Path(tmp.name).unlink(missing_ok=True)
    print("push_delivery_queue_audit: PASS")


if __name__ == "__main__":
    main()
