#!/usr/bin/env python3
"""Audit group chat creation for Pulse Communications 2.0."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def seed_users() -> tuple[int, int, int]:
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    users = [
        (984201, "group_v2_owner", "Group V2 Owner", "group-v2-owner@example.test"),
        (984202, "group_v2_peer_one", "Group V2 Peer One", "group-v2-peer-one@example.test"),
        (984203, "group_v2_peer_two", "Group V2 Peer Two", "group-v2-peer-two@example.test"),
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


def post_json(client, path: str, payload: dict) -> tuple[int, dict]:
    response = client.post(path, data=json.dumps(payload), content_type="application/json")
    return response.status_code, response.get_json(silent=True) or {}


def main() -> None:
    owner_id, peer_one, peer_two = seed_users()
    client = client_for(owner_id)

    response = client.get("/api/pulse/communications/v2/people/search?q=group_v2_peer")
    data = response.get_json(silent=True) or {}
    expect(response.status_code == 200 and data.get("ok"), "group people search succeeds", str(data))
    found_ids = {int(person.get("user_id") or 0) for person in (data.get("people") or data.get("items") or [])}
    expect(peer_one in found_ids and peer_two in found_ids, "group search finds selectable members", str(found_ids))

    status, data = post_json(client, "/api/pulse/communications/v2/groups", {"title": "Group Creation Audit", "member_ids": [peer_one, peer_two]})
    expect(status == 200 and data.get("ok"), "group conversation creates", str(data))
    conversation = data.get("conversation") or {}
    conversation_id = int(data.get("conversation_id") or conversation.get("conversation_id") or 0)
    expect(conversation_id > 0, "group conversation id returned")
    expect(conversation.get("conversation_type") == "group", "conversation is group", str(conversation))
    expect(int(conversation.get("member_count") or 0) >= 3, "group includes owner and selected members", str(conversation))

    members_response = client.get(f"/api/pulse/communications/v2/conversations/{conversation_id}/members")
    members_data = members_response.get_json(silent=True) or {}
    expect(members_response.status_code == 200 and members_data.get("ok"), "group members load", str(members_data))
    member_ids = {int(member.get("user_id") or 0) for member in members_data.get("members") or []}
    expect({owner_id, peer_one, peer_two}.issubset(member_ids), "group member list contains selected people", str(member_ids))

    status, message = post_json(client, f"/api/pulse/communications/v2/conversations/{conversation_id}/messages", {"body": "group creation audit"})
    expect(status == 200 and message.get("ok"), "message sends after group creation", str(message))

    print("pulse communications v2 create group audit ok")


if __name__ == "__main__":
    main()
