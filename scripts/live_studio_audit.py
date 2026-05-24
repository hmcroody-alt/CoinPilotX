#!/usr/bin/env python3
"""Audit the Pulse Live Studio creator experience."""

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
            ("liveaudit", "Live Audit", "live-audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute("SELECT id FROM pulse_live_sessions WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    if row:
        live_id = int(row["id"])
    else:
        cur.execute(
            """
            INSERT INTO pulse_live_sessions
            (user_id,title,category,status,stream_key,viewer_count,created_at,started_at,stream_uuid,ingest_url,rtmp_url,hls_url,webrtc_room_id,stream_health,bitrate_kbps,fps,updated_at)
            VALUES (?, 'Audit Live', 'Creator QA', 'live', 'audit_key', 3, ?, ?, 'auditstream', 'rtmp://live.coinpilotxai.app/live', 'rtmp://live.coinpilotxai.app/live/audit_key', 'https://live.coinpilotxai.app/hls/auditstream.m3u8', 'pulse-webrtc-auditstream', 'stable', 2800, 30, ?)
            """,
            (user_id, now, now, now),
        )
        live_id = int(cur.lastrowid)
        cur.execute(
            "INSERT INTO pulse_live_chat (live_id,user_id,body,message_type,moderation_status,pinned,created_at) VALUES (?, ?, 'Welcome to Audit Live.', 'system', 'approved', 1, ?)",
            (live_id, user_id, now),
        )
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
    res = client.get(f"/pulse/live/studio/{live_id}")
    html = res.get_data(as_text=True)
    require(res.status_code == 200, "studio route loads")
    require("pulse_live_studio.css" in html, "studio uses dedicated live stylesheet")
    require("pulse_live_studio.js" in html, "studio uses dedicated live script")
    require("live-command-shell" in html, "creator command shell renders")
    require("live-preview-stage" in html, "cinematic preview stage renders")
    require("Ready to go live" in html, "offline camera state is not a dead black void")
    require("Stream Health Center" in html, "stream health center replaces dominant OBS block")
    require("Professional Streaming" in html and "<details" in html, "OBS/RTMP setup is collapsible")
    require("live-chat-feed" in html, "animated live chat feed renders")
    require("live-floating-reactions" in html, "reaction overlay layer renders")
    print("live studio audit ok")


if __name__ == "__main__":
    main()
