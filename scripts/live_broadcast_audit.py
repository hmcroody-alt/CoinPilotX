#!/usr/bin/env python3
"""Audit end-to-end Pulse Live broadcast primitives."""

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
    user_id = int(row["user_id"]) if row else 0
    if not user_id:
        cur.execute(
            "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
            ("broadcastaudit", "Broadcast Audit", "broadcast-audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,stream_key,viewer_count,created_at,started_at,stream_uuid,ingest_url,rtmp_url,hls_url,webrtc_room_id,stream_health,bitrate_kbps,fps,updated_at)
        VALUES (?, 'Broadcast Audit', 'Creator QA', 'live', 'broadcast_key', 2, ?, ?, 'broadcastaudit', 'rtmp://live.coinpilotxai.app/live', 'rtmp://live.coinpilotxai.app/live/broadcast_key', 'https://live.coinpilotxai.app/hls/broadcastaudit.m3u8', 'pulse-webrtc-broadcastaudit', 'stable', 3000, 30, ?)
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
    state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require(state.get("ok"), "live state endpoint supports broadcast polling")
    require(state.get("playback", {}).get("supports_hls"), "broadcast state exposes HLS playback")
    require(state.get("playback", {}).get("supports_webrtc"), "broadcast state exposes WebRTC playback")
    viewer = client.get(f"/pulse/live/{live_id}")
    html = viewer.get_data(as_text=True)
    require(viewer.status_code == 200, "public live viewer route loads")
    require("live-public-player" in html, "public viewer uses cinematic player shell")
    require("ambient-ready-state" in str(state.get("playback", {})) or "live-ready-state" in html, "viewer has non-dead fallback state")
    print("live broadcast audit ok")


if __name__ == "__main__":
    main()
