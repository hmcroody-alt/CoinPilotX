#!/usr/bin/env python3
"""Verify post-live sharing options are created when a stream ends."""

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


def main():
    bot.init_db()
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
            ("postliveaudit", "Post Live Audit", "post-live-audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO pulse_live_sessions (user_id,title,category,status,publish_state,viewer_count,created_at,started_at,updated_at) VALUES (?, 'Post Live Audit', 'Creator QA', 'live', 'live', 4, ?, ?, ?)",
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    ended = client.post(f"/api/pulse/live/{live_id}/end", json={"replay_url": "https://cdn.coinpilotx.app/live/replay-audit.mp4"}).get_json() or {}
    require(ended.get("ok"), "live end endpoint succeeds")
    require(ended.get("post_live_options"), "ending live creates post-live sharing options")
    state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require(state.get("post_live_options"), "state endpoint exposes post-live options")
    print("live post broadcast options audit ok")


if __name__ == "__main__":
    main()
