#!/usr/bin/env python3
"""Runtime audit for Messenger push notifications without contacting providers."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from pulse_communications_v2 import service  # noqa: E402
from services import push_service  # noqa: E402
from services import notification_service  # noqa: E402


SENDER_ID = 984201
RECEIVER_ID = 984202
SENDER_TOKEN = "ExpoPushToken[audit-sender]"
RECEIVER_TOKEN = "ExpoPushToken[audit-receiver]"
INVALID_TOKEN = "ExpoPushToken[audit-invalid]"


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


class FakeResponse:
    ok = True
    content = b"{}"
    status_code = 200

    def __init__(self, status: str = "ok", details: dict | None = None):
        self._status = status
        self._details = details or {}

    def json(self) -> dict:
        return {"data": {"status": self._status, "details": self._details}}


def setup_data() -> int:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for user_id, username, display_name, email in (
        (SENDER_ID, "push_audit_sender", "Push Audit Sender", "push-audit-sender@example.test"),
        (RECEIVER_ID, "push_audit_receiver", "Push Audit Receiver", "push-audit-receiver@example.test"),
    ):
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute(
                "UPDATE users SET username=?, display_name=?, email=?, account_status='active' WHERE user_id=?",
                (username, display_name, email, user_id),
            )
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
                (user_id, username, display_name, email, now),
            )
    direct_key = ":".join(str(x) for x in sorted([SENDER_ID, RECEIVER_ID]))
    cur.execute("SELECT id FROM comm_v2_conversations WHERE direct_key=?", (direct_key,))
    conversation_ids = [int(row["id"]) for row in cur.fetchall()]
    if conversation_ids:
        placeholders = ",".join("?" for _ in conversation_ids)
        for table in ("comm_v2_messages", "comm_v2_participants", "comm_v2_read_receipts"):
            cur.execute(f"DELETE FROM {table} WHERE conversation_id IN ({placeholders})", tuple(conversation_ids))
        cur.execute(f"DELETE FROM comm_v2_conversations WHERE id IN ({placeholders})", tuple(conversation_ids))
    cur.execute("DELETE FROM comm_v2_blocks WHERE blocker_user_id IN (?, ?) OR blocked_user_id IN (?, ?)", (SENDER_ID, RECEIVER_ID, SENDER_ID, RECEIVER_ID))
    cur.execute("DELETE FROM comm_v2_user_settings WHERE user_id IN (?, ?)", (SENDER_ID, RECEIVER_ID))
    cur.execute("DELETE FROM push_subscriptions WHERE user_id IN (?, ?)", (SENDER_ID, RECEIVER_ID))
    cur.execute("DELETE FROM pulse_notifications WHERE user_id IN (?, ?) OR actor_user_id IN (?, ?)", (SENDER_ID, RECEIVER_ID, SENDER_ID, RECEIVER_ID))
    conn.commit()
    conn.close()

    created = service.create_conversation(SENDER_ID, {"type": "direct", "target_user_id": RECEIVER_ID})
    expect(created.get("ok") is True, "direct conversation created", str(created))
    conversation_id = int(created.get("conversation_id") or 0)
    expect(conversation_id > 0, "conversation id returned", str(created))

    notification_service.save_pulse_device(SENDER_ID, {"endpoint": SENDER_TOKEN, "provider": "expo", "token": SENDER_TOKEN, "subscription": {"expo_push_token": SENDER_TOKEN}, "device_type": "native"}, "PulseSoc iOS QA")
    notification_service.save_pulse_device(RECEIVER_ID, {"endpoint": RECEIVER_TOKEN, "provider": "expo", "token": RECEIVER_TOKEN, "subscription": {"expo_push_token": RECEIVER_TOKEN}, "device_type": "native"}, "PulseSoc Android QA")
    return conversation_id


def notification_rows(user_id: int) -> list[dict]:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_notifications WHERE user_id=? ORDER BY id DESC LIMIT 10", (int(user_id),))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def latest_push_delivery_status(user_id: int) -> str:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT status FROM pulse_notification_deliveries WHERE user_id=? AND channel='push' ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )
    row = cur.fetchone()
    conn.close()
    return str(dict(row or {}).get("status") or "")


def active_push_subscription_count(user_id: int) -> int:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS total FROM push_subscriptions WHERE user_id=? AND COALESCE(is_active, active, 1)=1",
        (int(user_id),),
    )
    total = int(dict(cur.fetchone() or {}).get("total") or 0)
    conn.close()
    return total


def main() -> None:
    conversation_id = setup_data()
    posts: list[dict] = []
    original_post = push_service.requests.post

    def fake_post(_url: str, json: dict | None = None, **_kwargs):
        posts.append(json or {})
        token = str((json or {}).get("to") or "")
        if token == INVALID_TOKEN:
            return FakeResponse("error", {"error": "DeviceNotRegistered"})
        return FakeResponse("ok")

    push_service.requests.post = fake_post
    try:
        sent = service.send_message(SENDER_ID, conversation_id, {"body": "Runtime push audit hello"})
        expect(sent.get("ok") is True, "message send succeeds", str(sent))
        expect(len(posts) == 1, "only receiver token pushed", str(posts))
        payload = posts[-1]
        data = payload.get("data") or {}
        expect(payload.get("to") == RECEIVER_TOKEN, "sender token skipped", str(payload))
        expect(payload.get("channelId") == "messages", "Expo messages channel used", str(payload))
        expect(payload.get("sound") == "default" and payload.get("priority") == "high", "audible high-priority push", str(payload))
        expect(data.get("conversationId") == conversation_id and data.get("messageId"), "conversation and message ids included", str(data))
        expect(data.get("senderId") == SENDER_ID and data.get("type") == "message", "sender and type included", str(data))
        expect(data.get("deepLink") == f"/pulse/messages/{conversation_id}", "exact conversation deep link included", str(data))
        expect(bool(data.get("push_trace_id")), "push trace id included in provider payload", str(data))

        posts.clear()
        muted_until = (datetime.now(UTC) + timedelta(minutes=10)).isoformat(timespec="seconds")
        conn = bot.db()
        cur = conn.cursor()
        cur.execute("UPDATE comm_v2_participants SET muted_until=? WHERE conversation_id=? AND user_id=?", (muted_until, conversation_id, RECEIVER_ID))
        conn.commit()
        conn.close()
        muted = service.send_message(SENDER_ID, conversation_id, {"body": "Runtime muted push audit"})
        expect(muted.get("ok") is True, "muted message send succeeds", str(muted))
        expect(len(posts) == 0, "muted conversation suppresses provider push", str(posts))
        expect(latest_push_delivery_status(RECEIVER_ID) == "skipped", "muted suppression logged")

        posts.clear()
        conn = bot.db()
        cur = conn.cursor()
        cur.execute("UPDATE comm_v2_participants SET muted_until='' WHERE conversation_id=? AND user_id=?", (conversation_id, RECEIVER_ID))
        cur.execute(
            "INSERT OR IGNORE INTO comm_v2_user_settings (user_id, presence_privacy, read_receipts_enabled, message_preview_privacy, updated_at) VALUES (?, 'everyone', 1, 'hide', ?)",
            (RECEIVER_ID, datetime.now(UTC).isoformat(timespec="seconds")),
        )
        cur.execute("UPDATE comm_v2_user_settings SET message_preview_privacy='hide' WHERE user_id=?", (RECEIVER_ID,))
        conn.commit()
        conn.close()
        private = service.send_message(SENDER_ID, conversation_id, {"body": "Runtime private preview audit"})
        expect(private.get("ok") is True, "private-preview message send succeeds", str(private))
        expect(len(posts) == 1, "private-preview push delivered", str(posts))
        expect(posts[-1].get("title") == "New message" and posts[-1].get("body") == "Open PulseSoc to view.", "private preview uses generic lock-screen copy", str(posts[-1]))
        expect(notification_rows(RECEIVER_ID), "receiver notification stored")

        conn = bot.db()
        cur = conn.cursor()
        cur.execute("DELETE FROM push_subscriptions WHERE user_id=?", (RECEIVER_ID,))
        conn.commit()
        conn.close()
        notification_service.save_pulse_device(RECEIVER_ID, {"endpoint": INVALID_TOKEN, "provider": "expo", "token": INVALID_TOKEN, "subscription": {"expo_push_token": INVALID_TOKEN}, "device_type": "native"}, "Audit Invalid")
        invalid = push_service.send_push(RECEIVER_ID, "Invalid audit", "Invalid token audit", {"type": "message", "conversationId": conversation_id}, push_type="message")
        expect(invalid.get("invalidated") == 1, "invalid Expo token is deactivated", str(invalid))
        notification_service.save_pulse_device(RECEIVER_ID, {"endpoint": RECEIVER_TOKEN, "provider": "expo", "token": RECEIVER_TOKEN, "subscription": {"expo_push_token": RECEIVER_TOKEN}, "device_type": "native"}, "Audit Receiver")
        expect(active_push_subscription_count(RECEIVER_ID) == 1, "receiver token active before unsubscribe")
        unsubscribed = notification_service.unsubscribe_push(RECEIVER_ID, RECEIVER_TOKEN)
        expect(unsubscribed.get("updated") == 1, "endpoint unsubscribe updates push_subscriptions", str(unsubscribed))
        expect(active_push_subscription_count(RECEIVER_ID) == 0, "endpoint unsubscribe clears active delivery eligibility")
    finally:
        push_service.requests.post = original_post

    print("PASS: runtime Messenger push delivery, trace metadata, deep links, suppression, private previews, invalid-token cleanup, and unsubscribe cleanup verified.")


if __name__ == "__main__":
    main()
