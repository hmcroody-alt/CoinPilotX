#!/usr/bin/env python3
"""Audit Pulse Live chat/reaction APIs and UI hooks."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def ensure_live():
    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    if row:
        user_id = int(row["user_id"])
    else:
        cur.execute(
            "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
            ("livechat", "Live Chat", "live-chat@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,webrtc_room_id,stream_health,bitrate_kbps,fps,updated_at)
        VALUES (?, 'Chat Audit Live', 'Community', 'live', 'chat_key', 1, ?, ?, 'chataudit', 'https://live.coinpilotxai.app/hls/chataudit.m3u8', 'pulse-webrtc-chataudit', 'stable', 2600, 30, ?)
        """,
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id, live_id


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    user_id, live_id = ensure_live()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    res = client.post(f"/api/pulse/live/{live_id}/chat", json={"body": "Live audit message"})
    data = res.get_json() or {}
    require(res.status_code == 200 and data.get("ok"), "live chat send API works")
    res = client.post(f"/api/pulse/live/{live_id}/react", json={"reaction_type": "🔥"})
    data = res.get_json() or {}
    require(res.status_code == 200 and data.get("ok"), "live reaction API works")
    res = client.get(f"/api/pulse/live/{live_id}/state")
    data = res.get_json() or {}
    require(res.status_code == 200 and data.get("ok"), "live state API works")
    require(any(m.get("body") == "Live audit message" for m in data.get("messages", [])), "state includes sent chat message")
    require(bool(data.get("reaction_cloud")), "state includes reaction cloud")
    html = client.get(f"/pulse/live/{live_id}").get_data(as_text=True)
    require("data-live-chat-feed" in html and "data-live-reaction" in html, "viewer page has realtime chat and reaction hooks")
    print("live chat audit ok")


if __name__ == "__main__":
    main()
