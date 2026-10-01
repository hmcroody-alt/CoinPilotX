#!/usr/bin/env python3
"""Verify room joins are presence-only and do not appear as chat messages."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db()
    cur = conn.cursor()
    user_id = 940204
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (user_id, "silent_join_audit", "Silent Join Audit", "silent-join@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    conn.commit()
    conn.close()
    return user_id


def visible_join_count(cur, conversation_id):
    cur.execute(
        """
        SELECT COUNT(*) AS total
        FROM pulse_messages
        WHERE conversation_id=?
          AND COALESCE(deleted_at,'')=''
          AND COALESCE(message_type,'') IN ('system','system_join','chat_event')
          AND lower(COALESCE(body,'')) LIKE '% joined'
        """,
        (conversation_id,),
    )
    return int((cur.fetchone() or {"total": 0})["total"] or 0)


def main():
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    joined = client.post("/api/pulse/messages/rooms/general-pulse/join")
    data = joined.get_json() or {}
    expect(joined.status_code == 200 and data.get("ok") is True, "room join endpoint works", joined.get_data(as_text=True)[:300])
    conversation_id = int(data.get("conversation_id") or (data.get("conversation") or {}).get("id") or 0)
    expect(conversation_id > 0, "room has backing conversation")
    conn = bot.db()
    cur = conn.cursor()
    expect(visible_join_count(cur, conversation_id) == 0, "join did not create visible message")
    messages = client.get("/api/pulse/chatrooms/general-pulse/messages").get_json() or {}
    rendered = " ".join((m.get("body") or "") for m in messages.get("messages", []))
    expect("joined" not in rendered.lower(), "room message API hides legacy join notices")
    conn.close()
    print("chatroom silent join audit ok")


if __name__ == "__main__":
    main()
