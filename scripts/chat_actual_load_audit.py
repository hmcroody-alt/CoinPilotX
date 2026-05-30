#!/usr/bin/env python3
"""Verify Pulse Messenger actually loads selected private, room, and group messages."""
from __future__ import annotations

import json
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
        (950101, "chat_load_one", "Chat Load One", "chat-load-one@example.test"),
        (950102, "chat_load_two", "Chat Load Two", "chat-load-two@example.test"),
        (950103, "chat_load_three", "Chat Load Three", "chat-load-three@example.test"),
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
    expect(bool(data.get("trace_id")), f"{label} includes trace id", str(data))


def main() -> None:
    bot.init_db()
    bot.init_db()
    user_id, other_id, third_id = ensure_users()
    client = client_for(user_id)

    status, data = request_json(client, "POST", "/api/pulse/messages/direct/open", {"target_user_id": other_id})
    assert_ok(status, data, "direct conversation opens")
    direct_id = int(data.get("conversation_id") or 0)
    expect(direct_id > 0, "direct conversation id returned", str(data))

    status, data = request_json(client, "POST", f"/api/pulse/messages/{direct_id}/send", {"message": "actual load direct seed"})
    assert_ok(status, data, "direct seed sends")
    status, data = request_json(client, "GET", f"/api/pulse/messages/{direct_id}/messages?limit=80")
    assert_ok(status, data, "direct selected thread loads messages")
    expect(data.get("endpoint", "").endswith(f"/{direct_id}/messages"), "direct load endpoint is explicit", str(data))
    expect(any((m.get("body") or "") == "actual load direct seed" for m in data.get("messages") or []), "direct loaded message body present", str(data))

    intruder = client_for(third_id)
    status, data = request_json(intruder, "GET", f"/api/pulse/messages/{direct_id}/messages?limit=80")
    expect(status == 403 and data.get("ok") is False, "direct unauthorized load returns 403", f"status={status} body={data}")
    expect("access" in (data.get("message") or "").lower(), "direct unauthorized copy is specific", str(data))

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(id),0) + 999999 AS missing_id FROM pulse_conversations")
    missing_row = cur.fetchone()
    missing_id = int((missing_row["missing_id"] if hasattr(missing_row, "keys") else missing_row[0]) if missing_row else 999999)
    conn.close()
    status, data = request_json(client, "GET", f"/api/pulse/messages/{missing_id}/messages?limit=80")
    expect(status == 404 and data.get("ok") is False, "missing direct conversation returns 404", f"status={status} body={data}")
    expect("not found" in (data.get("message") or "").lower(), "missing direct copy is specific", str(data))

    status, data = request_json(client, "GET", f"/api/messages/{direct_id}")
    assert_ok(status, data, "legacy private endpoint bridges to Pulse direct messages")
    expect(any((m.get("body") or "") == "actual load direct seed" for m in data.get("messages") or []), "legacy private bridge returns direct body", str(data))

    status, data = request_json(client, "POST", "/api/pulse/chatrooms/general-pulse/join", {})
    assert_ok(status, data, "room joins")
    room_conversation_id = int(data.get("conversation_id") or 0)
    status, data = request_json(client, "POST", "/api/pulse/chatrooms/general-pulse/messages", {"message": "actual load room seed"})
    assert_ok(status, data, "room seed sends")
    status, data = request_json(client, "GET", "/api/pulse/chatrooms/general-pulse/messages?limit=80")
    assert_ok(status, data, "room selected thread loads messages")
    expect(int(data.get("conversation_id") or 0) == room_conversation_id, "room load uses backing conversation id", str(data))
    expect((data.get("conversation") or {}).get("conversation_type") == "room", "room load declares room conversation type", str(data))
    expect(isinstance(data.get("messages"), list), "room load returns clean message list", str(data))
    status, data = request_json(client, "GET", "/api/chat-room/general-pulse/messages?limit=80")
    assert_ok(status, data, "legacy room endpoint bridges to Pulse room messages")
    expect(int(data.get("conversation_id") or 0) == room_conversation_id, "legacy room bridge uses backing conversation id", str(data))

    status, data = request_json(client, "POST", "/api/pulse/messages/groups/create", {"title": "Actual Load Group", "member_ids": [other_id, third_id]})
    assert_ok(status, data, "group creates")
    group_id = int(data.get("conversation_id") or (data.get("conversation") or {}).get("id") or 0)
    status, data = request_json(client, "POST", f"/api/pulse/messages/{group_id}/send", {"message": "actual load group seed"})
    assert_ok(status, data, "group seed sends")
    status, data = request_json(client, "GET", f"/api/pulse/messages/{group_id}/messages?limit=80")
    assert_ok(status, data, "group selected thread loads messages")
    expect((data.get("conversation") or {}).get("conversation_type") in {"group", "community", "community_group"}, "group load declares group-like type", str(data))
    expect(any((m.get("body") or "") == "actual load group seed" for m in data.get("messages") or []), "group loaded message body present", str(data))
    expect(isinstance(data.get("messages"), list), "group load returns clean message list", str(data))
    status, data = request_json(client, "GET", f"/api/messages/{group_id}")
    assert_ok(status, data, "legacy group endpoint bridges to Pulse group messages")
    expect(any((m.get("body") or "") == "actual load group seed" for m in data.get("messages") or []), "legacy group bridge returns group body", str(data))
    status, data = request_json(client, "GET", "/api/group-chat")
    assert_ok(status, data, "legacy group list endpoint bridges to Pulse group list")

    print("chat actual load audit ok")


if __name__ == "__main__":
    main()
