#!/usr/bin/env python3
"""Pulse chat performance smoke audit."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import chat_health_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user(uid, username):
    conn = bot.db(); conn.row_factory = bot.sqlite3.Row; cur = conn.cursor(); bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)", (uid, username, username.replace('_',' ').title(), f"{username}@example.test", now))
    conn.commit(); conn.close()


def client(uid):
    c = bot.webhook_app.test_client()
    with c.session_transaction() as sess:
        sess["account_user_id"] = uid
    return c


def main():
    bot.init_db(); ensure_user(931101, "chat_perf_one"); ensure_user(931102, "chat_perf_two")
    c = client(931101)
    direct = chat_health_service.check_endpoint(c, "POST", "/api/pulse/messages/direct/open", {"target_user_id": 931102})
    expect(direct.get("ok") and direct.get("latency_ms", 9999) < 1000, "direct open under 1s", str(direct))
    conv_id = int((direct.get("data") or {}).get("conversation_id") or 0)
    expect(conv_id > 0, "performance conversation id")
    messages = chat_health_service.check_endpoint(c, "GET", f"/api/pulse/messages/{conv_id}/messages?limit=30")
    expect(messages.get("ok") and messages.get("latency_ms", 9999) < 1000, "message page under 1s", str(messages))
    rooms = chat_health_service.check_endpoint(c, "GET", "/api/pulse/messages/rooms")
    expect(rooms.get("ok") and rooms.get("latency_ms", 9999) < 1000, "rooms list under 1s", str(rooms))
    print("chat performance audit ok")


if __name__ == "__main__":
    main()
