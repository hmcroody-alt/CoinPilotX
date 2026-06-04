#!/usr/bin/env python3
"""Exercise Phase 1 Communications V2 messaging APIs."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///coinpilotx.db")
os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

import bot  # noqa: E402


def client_for(user_id: int):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def ok(condition, label, detail=""):
    if not condition:
        raise AssertionError(f"FAIL: {label} {detail}")
    print(f"PASS: {label}")


bot.init_db()
conn = bot.db()
cur = conn.cursor()
now = datetime.now(UTC).isoformat(timespec="seconds")
for uid, name in [(986301, "phase_one_owner"), (986302, "phase_one_peer"), (986303, "phase_one_target")]:
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
            (uid, name, name.replace("_", " ").title(), f"{name}@example.test", now),
        )
conn.commit()
conn.close()

owner = client_for(986301)
peer = client_for(986302)

direct = owner.post("/api/pulse/communications/v2/direct/open", json={"target_user_id": 986302})
ok(direct.status_code == 200 and direct.get_json().get("conversation_id"), "direct conversation opens")
conversation_id = direct.get_json()["conversation_id"]

heartbeat = owner.post("/api/pulse/communications/v2/presence/heartbeat", json={"status": "online"})
ok(heartbeat.status_code == 200 and heartbeat.get_json().get("presence", {}).get("status") == "online", "presence heartbeat works")

presence = peer.get(f"/api/pulse/communications/v2/conversations/{conversation_id}/presence")
ok(presence.status_code == 200 and presence.get_json().get("presence"), "conversation presence loads")

settings = peer.post("/api/pulse/communications/v2/settings", json={"presence_privacy": "contacts", "read_receipts_enabled": False})
ok(settings.status_code == 200 and settings.get_json().get("settings", {}).get("read_receipts_enabled") is False, "privacy settings save")

sent = owner.post(f"/api/pulse/communications/v2/conversations/{conversation_id}/messages", json={"body": "Phase 1 hello"})
ok(sent.status_code == 200 and sent.get_json().get("message_id"), "message sends")
message_id = sent.get_json()["message_id"]

typed = peer.post(f"/api/pulse/communications/v2/conversations/{conversation_id}/typing", json={"is_typing": True})
ok(typed.status_code == 200, "typing indicator sets")

loaded = peer.get(f"/api/pulse/communications/v2/conversations/{conversation_id}/messages")
ok(loaded.status_code == 200 and loaded.get_json().get("messages"), "messages load")

reply = peer.post(f"/api/pulse/communications/v2/conversations/{conversation_id}/messages", json={"body": "Reply", "reply_to_message_id": message_id})
ok(reply.status_code == 200 and reply.get_json().get("message", {}).get("reply_to_message_id") == message_id, "reply sends")

reaction = peer.post(f"/api/pulse/communications/v2/messages/{message_id}/reactions", json={"reaction": "fire"})
ok(reaction.status_code == 200 and reaction.get_json().get("message", {}).get("reactions"), "reaction aggregates")

edit = owner.patch(f"/api/pulse/communications/v2/messages/{message_id}", json={"body": "Phase 1 hello edited"})
ok(edit.status_code == 200 and edit.get_json().get("message", {}).get("is_edited"), "owner edits message")

other = owner.post("/api/pulse/communications/v2/groups", json={"title": "Forward Target", "member_ids": [986303]})
ok(other.status_code == 200 and other.get_json().get("conversation_id"), "forward target created")
forward = owner.post(f"/api/pulse/communications/v2/messages/{message_id}/forward", json={"conversation_ids": [other.get_json()["conversation_id"]]})
ok(forward.status_code == 200 and forward.get_json().get("count") == 1, "message forwards")

self_delete = peer.delete(f"/api/pulse/communications/v2/messages/{message_id}", json={"delete_for": "self"})
ok(self_delete.status_code == 200 and self_delete.get_json().get("delete_for") == "self", "delete for self works")

everyone_delete = owner.delete(f"/api/pulse/communications/v2/messages/{message_id}", json={"delete_for": "everyone"})
ok(everyone_delete.status_code == 200 and everyone_delete.get_json().get("delete_for") == "everyone", "delete for everyone works")

print("pulse comm v2 phase1 API audit ok")
