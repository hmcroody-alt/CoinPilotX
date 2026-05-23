#!/usr/bin/env python3
"""Focused smoke test for Pulse Messenger APIs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def _ensure_users():
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users = [
        (910001, "messenger_audit_one", "Messenger Audit One", "messenger-audit-one@example.test"),
        (910002, "messenger_audit_two", "Messenger Audit Two", "messenger-audit-two@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute(
                "UPDATE users SET username=?, display_name=?, email=? WHERE user_id=?",
                (username, display_name, email, user_id),
            )
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
                (user_id, username, display_name, email, now),
            )
    bot.ensure_pulse_messenger_schema(cur, conn)
    conn.commit()
    conn.close()
    return users[0][0], users[1][0]


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


def main():
    bot.init_db()
    user_id, other_user_id = _ensure_users()
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id

    status, data = _json_response(client, "GET", "/api/pulse/messages/conversations")
    _expect(status == 200 and data.get("ok") is True and isinstance(data.get("conversations"), list), "chats API", str(data))

    status, data = _json_response(client, "GET", "/api/pulse/chatrooms")
    _expect(status == 200 and data.get("ok") is True and len(data.get("rooms") or []) >= 8, "rooms API and default rooms", str(data))

    status, data = _json_response(client, "GET", "/api/pulse/messages/group-conversations")
    _expect(status == 200 and data.get("ok") is True and isinstance(data.get("conversations"), list), "groups API", str(data))

    status, data = _json_response(client, "POST", "/api/pulse/messages/start", {"target_user_id": other_user_id})
    _expect(status == 200 and data.get("ok") is True and int(data.get("conversation_id") or 0) > 0, "start direct chat", str(data))
    direct_conversation_id = int(data["conversation_id"])

    status, data = _json_response(client, "POST", "/api/pulse/messages/send", {"conversation_id": direct_conversation_id, "message": "Messenger audit direct ping"})
    _expect(status == 200 and data.get("ok") is True and int(data.get("message_id") or 0) > 0, "send direct message", str(data))

    status, data = _json_response(client, "POST", "/api/pulse/chatrooms/general-pulse/join", {})
    _expect(status == 200 and data.get("ok") is True and int(data.get("conversation_id") or 0) > 0, "join room", str(data))

    status, data = _json_response(client, "POST", "/api/pulse/chatrooms/general-pulse/messages", {"message": "Messenger audit room ping"})
    _expect(status == 200 and data.get("ok") is True and int(data.get("message_id") or 0) > 0, "send room message", str(data))

    status, data = _json_response(
        client,
        "POST",
        "/api/pulse/messages/group/create",
        {"title": "Audit Group", "description": "Messenger API audit", "member_ids": [other_user_id]},
    )
    _expect(status == 200 and data.get("ok") is True and int(data.get("conversation_id") or 0) > 0, "create group", str(data))
    group_conversation_id = int(data["conversation_id"])

    status, data = _json_response(client, "GET", "/api/pulse/messages/group-conversations")
    group_ids = {int(item.get("conversation_id") or item.get("id") or 0) for item in data.get("conversations") or data.get("items") or []}
    _expect(status == 200 and data.get("ok") is True and group_conversation_id in group_ids, "created group appears in group list", str(data))

    status, data = _json_response(client, "POST", "/api/pulse/messages/send", {"conversation_id": group_conversation_id, "message": "Messenger audit group ping"})
    _expect(status == 200 and data.get("ok") is True and int(data.get("message_id") or 0) > 0, "send group message", str(data))

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute("INSERT INTO pulse_conversations (conversation_type, title, status, created_at, updated_at) VALUES ('group', 'Stale Audit Row', 'active', ?, ?)", (now, now))
    stale_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    status, data = _json_response(client, "GET", "/api/pulse/messages/group-conversations")
    _expect(status == 200 and data.get("ok") is True, "stale group row does not crash groups API", str(data))

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_conversations WHERE id=?", (stale_id,))
    cur.execute("INSERT INTO pulse_conversations (conversation_type, title, status, created_at, updated_at) VALUES ('direct', 'Stale Direct Row', 'active', ?, ?)", (now, now))
    stale_direct_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO pulse_conversation_participants (conversation_id, user_id, role, joined_at, created_at) VALUES (?, ?, 'member', ?, ?)",
        (stale_direct_id, user_id, now, now),
    )
    conn.commit()
    conn.close()

    status, data = _json_response(client, "GET", "/api/pulse/messages/conversations")
    _expect(status == 200 and data.get("ok") is True, "stale direct row does not crash chats API", str(data))

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM pulse_conversation_participants WHERE conversation_id=?", (stale_direct_id,))
    cur.execute("DELETE FROM pulse_conversations WHERE id=?", (stale_direct_id,))
    conn.commit()
    conn.close()
    print("messenger api audit ok")


if __name__ == "__main__":
    main()
