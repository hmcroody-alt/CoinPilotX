#!/usr/bin/env python3
"""Runtime audit for queued Push delivery payloads and device diagnostics."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ["PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import notification_service, push_service  # noqa: E402


USER_ID = 985611
TOKEN = "ExpoPushToken[pulse-runtime-audit]"


def expect(condition: bool, message: str, details: str = "") -> None:
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


class FakeResponse:
    ok = True
    content = b"{}"
    status_code = 200

    def json(self) -> dict:
        return {"data": {"status": "ok", "id": "ExponentPushTicket[pulse-runtime-audit]"}}


def setup_user() -> None:
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (USER_ID,))
    if cur.fetchone():
        cur.execute("UPDATE users SET account_status='active', email=? WHERE user_id=?", ("push-runtime-audit@example.test", USER_ID))
    else:
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
            (USER_ID, "push_runtime_audit", "Push Runtime Audit", "push-runtime-audit@example.test", now),
        )
    for table in ("push_subscriptions", "user_device_tokens", "pulse_notifications", "pulse_notification_deliveries", "push_delivery_jobs", "expo_push_tickets"):
        try:
            cur.execute(f"DELETE FROM {table} WHERE user_id=?", (USER_ID,))
        except Exception:
            pass
    conn.commit()
    conn.close()


def main() -> int:
    setup_user()
    saved = notification_service.save_pulse_device(
        USER_ID,
        {
            "endpoint": TOKEN,
            "token": TOKEN,
            "provider": "expo",
            "subscription": {"expo_push_token": TOKEN},
            "device_type": "native",
            "platform": "ios",
            "app_version": "1.0.0",
        },
        "PulseSoc iOS QA",
    )
    expect(saved.get("ok") is True, "native Expo token is saved", str(saved))

    note = notification_service.create_pulse_notification(
        USER_ID,
        "message",
        "Runtime Audit Sender",
        "Runtime Audit Sender: delivery payload check",
        actor_user_id=1,
        entity_type="conversation",
        entity_id="71001",
        deep_link="/pulse/messages/71001",
        metadata={
            "type": "chat_message",
            "push_type": "chat_message",
            "conversation_id": 71001,
            "conversationId": 71001,
            "message_id": 81001,
            "messageId": 81001,
            "senderId": 1,
            "badge": 3,
            "deepLink": "pulse://pulse/messages-v2?conversation=71001",
            "mobile_deep_link": "pulse://pulse/messages-v2?conversation=71001",
            "web_url": "https://pulsesoc.com/pulse/messages/71001",
        },
    )
    expect(note.get("ok") is True and note.get("notification_id"), "chat notification creates push job", str(note))

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT push_type, payload_json, status FROM push_delivery_jobs WHERE user_id=? ORDER BY id DESC LIMIT 1", (USER_ID,))
    job = dict(cur.fetchone() or {})
    conn.close()
    expect(job.get("push_type") == "chat_message", "push job keeps chat_message type", str(job))
    expect('"channelId": "pulse-messages-v2"' in (job.get("payload_json") or ""), "push job carries dedicated message channel", job.get("payload_json") or "")

    posts: list[dict] = []
    original_post = push_service.requests.post

    def fake_post(_url: str, json: dict | None = None, **_kwargs):
        posts.append(json or {})
        return FakeResponse()

    push_service.requests.post = fake_post
    try:
        processed = push_service.process_push_delivery_jobs(limit=5)
    finally:
        push_service.requests.post = original_post
    expect(processed.get("sent") == 1, "queued push job sends successfully", str(processed))
    expect(len(posts) == 1, "exactly one provider request sent", str(posts))
    payload = posts[0]
    data = payload.get("data") or {}
    expect(payload.get("channelId") == "pulse-messages-v2", "provider payload uses message channel", str(payload))
    expect(payload.get("sound") == "default" and payload.get("priority") == "high", "provider payload is audible and high priority", str(payload))
    expect(data.get("type") == "chat_message" and data.get("push_type") == "chat_message", "provider payload keeps chat type", str(data))
    expect(data.get("deepLink") == "pulse://pulse/messages-v2?conversation=71001", "provider payload keeps exact native deep link", str(data))
    expect(data.get("web_url") == "https://pulsesoc.com/pulse/messages/71001", "provider payload keeps HTTPS fallback", str(data))
    expect(payload.get("badge") == 3, "provider payload includes badge count", str(payload))

    print("push_delivery_runtime_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"push_delivery_runtime_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
