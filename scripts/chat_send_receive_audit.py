#!/usr/bin/env python3
"""Verify private, room, and group send paths persist and can be reloaded immediately."""
from __future__ import annotations

import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_users() -> tuple[int, int, int]:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users = [
        (950201, "chat_send_one", "Chat Send One", "chat-send-one@example.test"),
        (950202, "chat_send_two", "Chat Send Two", "chat-send-two@example.test"),
        (950203, "chat_send_three", "Chat Send Three", "chat-send-three@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE users SET username=?, display_name=?, email=?, account_status='active' WHERE user_id=?", (username, display_name, email, user_id))
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
                (user_id, username, display_name, email, now),
            )
    conn.commit()
    conn.close()
    return users[0][0], users[1][0], users[2][0]


def client_for(user_id: int):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def request_json(client, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    if method == "GET":
        response = client.get(path)
    else:
        response = client.open(path, method=method, data=json.dumps(payload or {}), content_type="application/json")
    return response.status_code, response.get_json(silent=True) or {}


def assert_ok(status: int, data: dict, label: str) -> None:
    expect(status == 200 and data.get("ok") is True, label, f"status={status} body={data}")


def assert_round_trip(client, send_path: str, load_path: str, body: str, label: str) -> None:
    unique_body = f"{body} {secrets.token_hex(4)}"
    client_message_id = f"audit-{label}-{secrets.token_hex(6)}"
    status, data = request_json(client, "POST", send_path, {"message": unique_body, "client_message_id": client_message_id})
    assert_ok(status, data, f"{label} send")
    sent_id = int(data.get("message_id") or (data.get("data") or {}).get("id") or 0)
    expect(sent_id > 0, f"{label} send returns message id", str(data))
    status, data = request_json(client, "GET", load_path)
    assert_ok(status, data, f"{label} reload")
    messages = data.get("messages") or data.get("items") or []
    expect(
        any(int(m.get("id") or m.get("message_id") or 0) == sent_id or (m.get("body") or m.get("content") or "") == unique_body for m in messages),
        f"{label} message appears after reload",
        str(data),
    )


def main() -> None:
    bot.init_db()
    user_id, other_id, third_id = ensure_users()
    client = client_for(user_id)

    status, data = request_json(client, "POST", "/api/pulse/messages/direct/open", {"target_user_id": other_id})
    assert_ok(status, data, "direct open")
    direct_id = int(data.get("conversation_id") or 0)
    assert_round_trip(client, f"/api/pulse/messages/{direct_id}/send", f"/api/pulse/messages/{direct_id}/messages?limit=80", "send receive direct body", "direct")
    assert_round_trip(client, f"/api/messages/{direct_id}/send", f"/api/messages/{direct_id}", "legacy direct send receive body", "legacy direct")

    status, data = request_json(client, "POST", "/api/pulse/chatrooms/general-pulse/join", {})
    assert_ok(status, data, "room join")
    assert_round_trip(client, "/api/pulse/chatrooms/general-pulse/messages", "/api/pulse/chatrooms/general-pulse/messages?limit=80", "send receive room body", "room")
    assert_round_trip(client, "/api/chat-room/general-pulse/messages", "/api/chat-room/general-pulse/messages?limit=80", "legacy room send receive body", "legacy room")

    status, data = request_json(client, "POST", "/api/pulse/messages/groups/create", {"title": "Send Receive Group", "member_ids": [other_id, third_id]})
    assert_ok(status, data, "group create")
    group_id = int(data.get("conversation_id") or (data.get("conversation") or {}).get("id") or 0)
    assert_round_trip(client, f"/api/pulse/messages/{group_id}/send", f"/api/pulse/messages/{group_id}/messages?limit=80", "send receive group body", "group")
    assert_round_trip(client, f"/api/messages/{group_id}/send", f"/api/messages/{group_id}", "legacy group send receive body", "legacy group")

    print("chat send receive audit ok")


if __name__ == "__main__":
    main()
