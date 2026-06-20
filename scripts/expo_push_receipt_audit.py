#!/usr/bin/env python3
"""Audit durable Expo ticket and receipt reconciliation without network access."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.NamedTemporaryFile(prefix="pulse-expo-receipts-", suffix=".db", delete=False)
tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp.name}"

from services import push_service, user_context  # noqa: E402


TOKEN = "ExpoPushToken[receipt-audit]"


class FakeResponse:
    ok = True
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = b"{}"

    def json(self):
        return self.payload


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


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


def main():
    setup()
    calls = []

    def fake_post(url, json=None, **_kwargs):
        calls.append((url, json or {}))
        if url.endswith("/push/send"):
            return FakeResponse({"data": {"status": "ok", "id": "ticket-audit-1"}})
        return FakeResponse(
            {
                "data": {
                    "ticket-audit-1": {
                        "status": "error",
                        "message": "Device is no longer registered.",
                        "details": {"error": "DeviceNotRegistered"},
                    }
                }
            }
        )

    original_post = push_service.requests.post
    push_service.requests.post = fake_post
    try:
        saved = push_service.save_subscription(
            501,
            {"endpoint": TOKEN, "token": TOKEN, "device_id": "receipt-device"},
            device_type="native",
            browser="Expo",
        )
        expect(saved.get("ok"), "Expo token registration failed")
        sent = push_service.send_push(
            501,
            "PulseSoc",
            "Receipt audit",
            {"notification_id": 99, "conversationId": 77},
            push_type="message",
        )
        expect(sent.get("ok") and sent.get("accepted_tickets") == 1, "Expo ticket was not persisted")
        reconciled = push_service.process_expo_receipts()
        expect(
            reconciled == {"ok": True, "checked": 1, "confirmed": 0, "failed": 1, "invalidated": 1},
            f"unexpected receipt result: {reconciled}",
        )
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute("SELECT status, error_code, receipt_json FROM expo_push_tickets WHERE provider_ticket_id='ticket-audit-1'")
        row = cur.fetchone()
        expect(row and row[0] == "invalid" and row[1] == "DeviceNotRegistered", "receipt status was not persisted")
        expect(TOKEN not in str(row[2] or ""), "receipt storage leaked the push token")
        cur.execute("SELECT enabled FROM user_device_tokens WHERE user_id=501")
        expect(cur.fetchone()[0] == 0, "invalid receipt did not revoke mirrored device token")
        cur.execute("SELECT COALESCE(is_active, active, 1) FROM push_subscriptions WHERE user_id=501")
        expect(cur.fetchone()[0] == 0, "invalid receipt did not revoke subscription")
        conn.close()
        expect(any(url.endswith("/push/getReceipts") for url, _payload in calls), "Expo receipt endpoint was not called")
    finally:
        push_service.requests.post = original_post
        Path(tmp.name).unlink(missing_ok=True)
    print("expo_push_receipt_audit: PASS")


if __name__ == "__main__":
    main()
