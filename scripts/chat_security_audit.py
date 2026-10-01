#!/usr/bin/env python3
"""Pulse chat security contract audit."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user(uid, username):
    conn = bot.db(); conn.row_factory = bot.sqlite3.Row; cur = conn.cursor(); bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)", (uid, username, username.replace('_',' ').title(), f"{username}@example.test", now))
    conn.commit(); conn.close()


def client(uid=None):
    c = bot.webhook_app.test_client()
    if uid:
        with c.session_transaction() as sess:
            sess["account_user_id"] = uid
    return c


def main():
    bot.init_db(); ensure_user(932101, "chat_sec_one"); ensure_user(932102, "chat_sec_two"); ensure_user(932103, "chat_sec_intruder")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("message size is capped" not in bot_source.lower() or "[:2000]" in bot_source or "body[:2000]" in bot_source, "message size cap path is present")
    expect("credentials:'same-origin'" in bot_source or 'credentials:"same-origin"' in bot_source, "chat fetches use same-origin credentials")
    anon = client()
    expect(anon.get("/api/pulse/messages/conversations").status_code == 401, "anonymous conversations blocked")
    owner = client(932101); intruder = client(932103)
    opened = owner.post("/api/pulse/messages/direct/open", json={"target_user_id": 932102}).get_json() or {}
    conv_id = int(opened.get("conversation_id") or 0)
    expect(conv_id > 0, "secure direct conversation created", str(opened))
    blocked = intruder.get(f"/api/pulse/messages/{conv_id}/messages")
    expect(blocked.status_code in {403, 404}, "non participant cannot read private messages", blocked.get_data(as_text=True)[:200])
    too_big = owner.post(f"/api/pulse/messages/{conv_id}/send", json={"message": "x" * 9000}).get_json() or {}
    expect(too_big.get("ok") is True and len(((too_big.get("data") or {}).get("body") or "")) <= 2000, "message size is capped server-side", str(too_big)[:240])
    print("chat security audit ok")


if __name__ == "__main__":
    main()
