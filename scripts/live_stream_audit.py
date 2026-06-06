#!/usr/bin/env python3
"""Audit secure Pulse Live session creation with traceable backend logs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def create_owner() -> int:
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, display_name, email, email_verified, avatar_url, bio, signup_time, created_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
        ("livestreamaudit", "Roody Cherie", "coinpilotxai@gmail.com", "/static/brand/pulse-logo-20260606.png", "Live audit creator", now, now),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id


def main():
    bot.init_db()
    user_id = create_owner()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.post(
        "/api/pulse/live/start",
        headers={"X-Trace-Id": "audit-live-stream"},
        json={"title": "Live Stream Audit", "category": "Creator QA", "destinations": [{"platform": "pulse"}]},
    )
    data = response.get_json() or {}
    require(response.status_code == 200 and data.get("ok"), "live start succeeds")
    require(data.get("trace_id") == "audit-live-stream", "client trace id is preserved in response/log path")
    require(data.get("live_id") and data.get("feed_post_id"), "live session and feed post are created")
    require(data.get("playback_url", "").endswith(".m3u8"), "HLS playback URL is attached")
    require(data.get("webrtc_room_id"), "WebRTC room id is attached")
    print("live stream audit ok")


if __name__ == "__main__":
    main()
