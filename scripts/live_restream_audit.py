#!/usr/bin/env python3
"""Verify multi-destination live target orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, live_restream_service  # noqa: E402


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
            ("restreamaudit", "Restream Audit", "restream-audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO pulse_live_sessions (user_id,title,category,status,created_at,started_at,updated_at) VALUES (?, 'Restream Audit', 'Creator QA', 'live', ?, ?, ?)",
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    targets = live_restream_service.prepare_restream_targets(
        cur,
        live_id=live_id,
        user_id=user_id,
        destinations=[{"platform": "pulse"}, {"platform": "youtube"}, {"platform": "custom_rtmp"}],
        custom_rtmp_url="rtmps://example.com/live",
        custom_stream_key="secret-key",
    )
    conn.commit()
    statuses = live_restream_service.destination_statuses(cur, live_id=live_id)
    conn.close()
    require(any(t["platform"] == "pulse" and t["status"] == "live" for t in targets), "Pulse Live is always primary")
    require(any(t["platform"] == "youtube" for t in statuses), "YouTube destination target is stored")
    require(any(t["platform"] == "custom_rtmp" and t["status"] == "connecting" for t in statuses), "custom RTMP destination validates and queues")
    print("live restream audit ok")


if __name__ == "__main__":
    main()
