#!/usr/bin/env python3
"""Pulse realtime social infrastructure audit.

This verifies the production behaviors that make Pulse feel alive without
becoming noisy: deduped realtime, silent room joins, idempotent sends, replies,
emoji reactions, and reconnect sync.
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
from services import realtime_engine  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def client(user_id):
    c = bot.app.test_client()
    with c.session_transaction() as session:
        session["account_user_id"] = user_id
    return c


def json_call(c, method, path, payload=None):
    if method == "GET":
        r = c.get(path)
    else:
        r = c.open(path, method=method, data=json.dumps(payload or {}), content_type="application/json")
    return r.status_code, r.get_json() or {}


def ensure_users():
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users = [
        (930001, "realtime_one", "Realtime One", "realtime-one@example.test"),
        (930002, "realtime_two", "Realtime Two", "realtime-two@example.test"),
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


def main():
    bot.init_db()
    bot.init_db()
    user_id, other_id = ensure_users()
    c = client(user_id)

    before = realtime_engine.health_snapshot().get("coalesced_events", 0)
    realtime_engine.publish_event("pulse:global", "pulse_typing_started", {"conversation_id": 1, "user_id": user_id})
    realtime_engine.publish_event("pulse:global", "pulse_typing_started", {"conversation_id": 1, "user_id": user_id})
    after = realtime_engine.health_snapshot().get("coalesced_events", 0)
    expect(after >= before + 1, "typing realtime coalesces duplicate bursts")

    status, data = json_call(c, "POST", "/api/pulse/messages/direct/open", {"target_user_id": other_id})
    expect(status == 200 and data.get("ok"), "direct open for realtime audit", str(data))
    conversation_id = int(data.get("conversation_id") or 0)
    expect(conversation_id > 0, "conversation id present", str(data))

    local_id = f"audit-local-{datetime.now(timezone.utc).timestamp()}"
    status, sent = json_call(c, "POST", f"/api/pulse/messages/{conversation_id}/send", {"message": "Realtime idempotent hello", "client_message_id": local_id})
    expect(status == 200 and sent.get("ok"), "idempotent send first attempt", str(sent))
    first_message_id = int(sent.get("message_id") or 0)
    status, retry = json_call(c, "POST", f"/api/pulse/messages/{conversation_id}/send", {"message": "Realtime idempotent hello", "client_message_id": local_id})
    expect(status == 200 and retry.get("ok") and retry.get("idempotent"), "idempotent retry returns existing message", str(retry))
    expect(int(retry.get("message_id") or 0) == first_message_id, "idempotent retry preserves message id", str(retry))

    status, reply = json_call(c, "POST", f"/api/pulse/messages/{conversation_id}/send", {"message": "Reply context preserved", "reply_to_id": first_message_id})
    expect(status == 200 and reply.get("ok"), "reply send works", str(reply))

    status, reaction = json_call(c, "POST", f"/api/pulse/messages/{first_message_id}/react", {"reaction": "🔥"})
    expect(status == 200 and reaction.get("ok") and reaction.get("reactions", {}).get("🔥", 0) >= 1, "unicode emoji reaction works", str(reaction))

    status, sync = json_call(c, "GET", f"/api/pulse/messages/{conversation_id}/sync?after_id=0")
    expect(status == 200 and sync.get("ok") and len(sync.get("messages") or []) >= 1, "reconnect sync returns messages", str(sync))
    expect("presence" in sync and "events" in sync, "sync includes presence and live events", str(sync))

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    before_events = cur.execute("SELECT COUNT(*) AS c FROM pulse_live_events WHERE event_type IN ('chat_room_joined','participant_joined')").fetchone()["c"]
    room_status, room = json_call(c, "POST", "/api/pulse/messages/rooms/general-pulse/join", {})
    expect(room_status == 200 and room.get("ok"), "room join still succeeds silently", str(room))
    after_events = cur.execute("SELECT COUNT(*) AS c FROM pulse_live_events WHERE event_type IN ('chat_room_joined','participant_joined')").fetchone()["c"]
    conn.close()
    expect(after_events == before_events, "room joins do not persist noisy joined events")

    print("pulse realtime infrastructure audit ok")


if __name__ == "__main__":
    main()
