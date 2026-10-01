#!/usr/bin/env python3
"""Audit direct chat creation for Pulse Communications 2.0."""

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


def seed_users() -> tuple[int, int]:
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    users = [
        (984101, "direct_v2_owner", "Direct V2 Owner", "direct-v2-owner@example.test"),
        (984102, "direct_v2_peer", "Direct V2 Peer", "direct-v2-peer@example.test"),
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
    return users[0][0], users[1][0]


def client_for(user_id: int):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def post_json(client, path: str, payload: dict) -> tuple[int, dict]:
    response = client.post(path, data=json.dumps(payload), content_type="application/json")
    return response.status_code, response.get_json(silent=True) or {}


def main() -> None:
    owner_id, peer_id = seed_users()
    client = client_for(owner_id)

    response = client.get("/api/pulse/communications/v2/people/search?q=direct_v2_peer")
    data = response.get_json(silent=True) or {}
    expect(response.status_code == 200 and data.get("ok"), "people search succeeds", str(data))
    people = data.get("people") or data.get("items") or []
    expect(any(int(person.get("user_id") or 0) == peer_id for person in people), "people search finds peer")
    expect(all("email" not in person for person in people), "people search does not expose email addresses")

    status, data = post_json(client, "/api/pulse/communications/v2/direct/open", {"target_user_id": peer_id})
    expect(status == 200 and data.get("ok"), "direct conversation opens", str(data))
    conversation = data.get("conversation") or {}
    conversation_id = int(data.get("conversation_id") or conversation.get("conversation_id") or 0)
    expect(conversation_id > 0, "direct conversation id returned")
    expect(conversation.get("conversation_type") == "direct", "conversation is direct", str(conversation))

    status, message = post_json(client, f"/api/pulse/communications/v2/conversations/{conversation_id}/messages", {"body": "direct creation audit"})
    expect(status == 200 and message.get("ok"), "message sends after direct creation", str(message))

    print("pulse communications v2 create direct audit ok")


if __name__ == "__main__":
    main()
