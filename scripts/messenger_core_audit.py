#!/usr/bin/env python3
"""Canonical Pulse Messenger core audit.

This covers the direct, group, and room paths that power both mobile and web.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


DEFAULT_ROOMS = [
    "general-pulse",
    "crypto-education",
    "ai-builders",
    "cybersecurity",
    "creator-lounge",
    "marketplace-help",
    "reels-music",
    "live-stage",
]


def _ensure_users():
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users = [
        (920001, "messenger_core_one", "Messenger Core One", "messenger-core-one@example.test"),
        (920002, "messenger_core_two", "Messenger Core Two", "messenger-core-two@example.test"),
        (920003, "messenger_core_three", "Messenger Core Three", "messenger-core-three@example.test"),
        (920004, "messenger_core_empty", "Messenger Core Empty", "messenger-core-empty@example.test"),
    ]
    for user_id, username, display_name, email in users:
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
    conn.commit()
    conn.close()
    return [user[0] for user in users]


def _client(user_id):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def _json_response(client, method, path, payload=None):
    if method == "GET":
        response = client.get(path)
    else:
        response = client.open(path, method=method, data=json.dumps(payload or {}), content_type="application/json")
    try:
        data = response.get_json() or {}
    except Exception:
        data = {}
    return response.status_code, data


def _expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def _assert_ok(status, data, label):
    _expect(status == 200 and data.get("ok") is True, label, f"status={status} data={data}")


def main():
    bot.init_db()
    bot.init_db()
    user_id, other_id, third_id, empty_user_id = _ensure_users()
    client = _client(user_id)

    status, data = _json_response(client, "GET", "/api/pulse/messages/conversations")
    _assert_ok(status, data, "direct conversations empty/state-safe")
    _expect(isinstance(data.get("items") or data.get("conversations"), list), "direct conversations shape", str(data))

    status, data = _json_response(client, "POST", "/api/pulse/messages/direct/open", {"target_user_id": other_id})
    _assert_ok(status, data, "direct open")
    direct_id = int(data.get("conversation_id") or 0)
    _expect(direct_id > 0, "direct open returned conversation id", str(data))

    status, data = _json_response(client, "POST", "/api/pulse/messages/direct/open", {"target_user_id": other_id})
    _assert_ok(status, data, "direct reuse")
    _expect(int(data.get("conversation_id") or 0) == direct_id, "direct reuse same conversation", str(data))

    status, data = _json_response(client, "POST", f"/api/pulse/messages/{direct_id}/send", {"message": "Messenger core direct canonical ping"})
    _assert_ok(status, data, "direct canonical send")
    _expect(int(data.get("message_id") or 0) > 0, "direct canonical send message id", str(data))

    status, data = _json_response(client, "POST", "/api/pulse/messages/send", {"conversation_id": direct_id, "message": "Messenger core direct compatibility ping"})
    _assert_ok(status, data, "direct compatibility send")

    status, data = _json_response(client, "GET", f"/api/pulse/messages/{direct_id}")
    _assert_ok(status, data, "direct load")
    _expect(len(data.get("messages") or []) >= 1, "direct messages returned", str(data))

    status, data = _json_response(client, "GET", "/api/pulse/messages/rooms")
    _assert_ok(status, data, "rooms list")
    rooms = data.get("rooms") or data.get("items") or []
    room_keys = {str(room.get("room_id") or room.get("id") or "") for room in rooms}
    _expect(set(DEFAULT_ROOMS).issubset(room_keys), "all default rooms listed", str(room_keys))

    for room_key in DEFAULT_ROOMS:
        status, data = _json_response(client, "POST", f"/api/pulse/messages/rooms/{room_key}/join", {})
        _assert_ok(status, data, f"room join {room_key}")
        room_conversation_id = int(data.get("conversation_id") or 0)
        _expect(room_conversation_id > 0, f"room {room_key} backing conversation", str(data))

        status, data = _json_response(client, "GET", f"/api/pulse/messages/rooms/{room_key}/messages")
        _assert_ok(status, data, f"room load {room_key}")
        _expect(int(data.get("conversation_id") or 0) == room_conversation_id, f"room load uses backing conversation {room_key}", str(data))

        status, data = _json_response(client, "POST", f"/api/pulse/messages/rooms/{room_key}/messages", {"message": f"Messenger core room ping {room_key}"})
        _assert_ok(status, data, f"room send {room_key}")
        _expect(int(data.get("message_id") or 0) > 0, f"room send message id {room_key}", str(data))

    status, data = _json_response(
        client,
        "POST",
        "/api/pulse/messages/groups/create",
        {"title": "Core Audit Group", "description": "Messenger core group audit", "member_ids": [other_id, str(third_id), other_id]},
    )
    _assert_ok(status, data, "group create")
    group_id = int(data.get("conversation_id") or (data.get("conversation") or {}).get("id") or 0)
    _expect(group_id > 0, "group create returned conversation id", str(data))

    status, data = _json_response(client, "GET", "/api/pulse/messages/group-conversations")
    _assert_ok(status, data, "groups list")
    group_ids = {int(item.get("conversation_id") or item.get("id") or 0) for item in (data.get("items") or data.get("conversations") or [])}
    _expect(group_id in group_ids, "created group appears in group list", str(data))

    status, data = _json_response(client, "POST", f"/api/pulse/messages/{group_id}/send", {"message": "Messenger core group ping"})
    _assert_ok(status, data, "group canonical send")

    empty_client = _client(empty_user_id)
    status, data = _json_response(empty_client, "GET", "/api/pulse/messages/conversations")
    _assert_ok(status, data, "empty direct list ok")
    status, data = _json_response(empty_client, "GET", "/api/pulse/messages/group-conversations")
    _assert_ok(status, data, "empty group list ok")

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute("INSERT INTO pulse_conversations (conversation_type, title, status, created_at, updated_at) VALUES ('direct', 'Broken Core Direct', 'active', ?, ?)", (now, now))
    stale_direct_id = int(cur.lastrowid)
    cur.execute("INSERT INTO pulse_conversation_participants (conversation_id, user_id, role, joined_at, created_at) VALUES (?, ?, 'member', ?, ?)", (stale_direct_id, user_id, now, now))
    cur.execute("INSERT INTO pulse_conversations (conversation_type, title, status, created_at, updated_at) VALUES ('group', 'Broken Core Group', 'active', ?, ?)", (now, now))
    stale_group_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    status, data = _json_response(client, "GET", "/api/pulse/messages/conversations")
    _assert_ok(status, data, "stale direct rows do not crash chats")
    status, data = _json_response(client, "GET", "/api/pulse/messages/group-conversations")
    _assert_ok(status, data, "stale group rows do not crash groups")

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_conversation_participants WHERE conversation_id IN (?, ?)", (stale_direct_id, stale_group_id))
    cur.execute("DELETE FROM pulse_conversations WHERE id IN (?, ?)", (stale_direct_id, stale_group_id))
    conn.commit()
    conn.close()

    print("messenger core audit ok")


if __name__ == "__main__":
    main()
