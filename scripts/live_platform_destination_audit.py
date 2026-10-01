#!/usr/bin/env python3
"""Verify platform destination secrets are protected and validated."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, live_destination_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    valid, reason = live_destination_service.validate_rtmp_url("rtmps://example.com/live")
    require(valid and not reason, "valid RTMPS destination is accepted")
    invalid, reason = live_destination_service.validate_rtmp_url("https://example.com/live")
    require(not invalid and reason, "non-RTMP destination is rejected")
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    user_id = int(row["user_id"]) if row else 0
    if not user_id:
        cur.execute(
            "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
            ("destaudit", "Destination Audit", "destination-audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    destination_id = live_destination_service.upsert_destination(
        cur,
        user_id=user_id,
        platform="twitch",
        label="Twitch",
        rtmp_url="rtmps://live.twitch.tv/app",
        stream_key="super-secret-stream-key",
    )
    conn.commit()
    cur.execute("SELECT * FROM pulse_live_destinations WHERE id=?", (destination_id,))
    public = live_destination_service.public_destination(cur.fetchone())
    conn.close()
    require(public["platform"] == "twitch", "destination platform stored")
    require("super-secret" not in public["stream_key_preview"], "stream key is never exposed publicly")
    require(public["stream_key_preview"].startswith("••••"), "stream key preview is masked")
    print("live platform destination audit ok")


if __name__ == "__main__":
    main()
