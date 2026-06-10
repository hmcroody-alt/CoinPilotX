#!/usr/bin/env python3
"""Audit browser A/V publish, playback fallback, and viewer transport metadata."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, live_feed_service  # noqa: E402


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
    cur.execute(
        "INSERT INTO users (username, display_name, email, email_verified, avatar_url, bio, signup_time, created_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
        ("livetransportaudit", "Roody Cherie", "coinpilotxai@gmail.com", "/static/brand/pulsesoc-logo-20260606.png", "Transport audit creator", now, now),
    )
    user_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,publish_state,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,playback_url,webrtc_room_id,stream_health,updated_at)
        VALUES (?, 'Transport Audit', 'Creator QA', 'publishing', 'publishing', 'transport_key', 0, ?, ?, 'transportaudit', 'https://live.coinpilotxai.app/hls/transportaudit.m3u8', 'https://live.coinpilotxai.app/hls/transportaudit.m3u8', 'pulse-webrtc-transportaudit', 'starting', ?)
        """,
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    post_id = live_feed_service.ensure_live_feed_post(cur, user_id=user_id, live_id=live_id, title="Transport Audit", category="Creator QA", playback_url="https://live.coinpilotxai.app/hls/transportaudit.m3u8")
    cur.execute("UPDATE pulse_live_sessions SET feed_post_id=? WHERE id=?", (post_id, live_id))
    conn.commit()
    conn.close()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    original_egress = bot.pulse_livekit_start_mux_egress
    bot.pulse_livekit_start_mux_egress = lambda live, trace_id="": {
        "ok": True,
        "egress_id": "EG_TRANSPORT",
        "status": "EGRESS_STARTING",
        "room": live.get("webrtc_room_id") or "pulse-webrtc-transportaudit",
    }
    publish = client.post(f"/api/pulse/live/{live_id}/browser-publish", json={"audio_tracks": 1, "video_tracks": 1}).get_json() or {}
    bot.pulse_livekit_start_mux_egress = original_egress
    require(publish.get("ok"), "browser media publish accepts audio and video tracks")
    require(publish.get("publish_path") == "livekit_mux_egress", "browser media publish starts LiveKit to Mux bridge")
    require(publish.get("playback", {}).get("supports_hls"), "HLS fallback remains available")
    require(publish.get("playback", {}).get("supports_webrtc"), "WebRTC fallback remains available")
    viewer = client.get(f"/pulse/live/{live_id}")
    html = viewer.get_data(as_text=True)
    require("data-live-player" in html, "active viewer uses real media player")
    require("live-ready-orb" not in html, "active viewer does not show fake placeholder avatar")
    source = (ROOT / "static/js/pulse_live_studio.js").read_text(encoding="utf-8")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("pulse_livekit_start_mux_egress" in bot_source, "backend starts LiveKit egress to Mux")
    require("connectLiveKitRoom" in source, "browser transport connects to LiveKit room")
    require("getAudioTracks" in source and "getVideoTracks" in source, "publisher diagnostics inspect audio and video tracks")
    require("publishTrack(track)" in source, "publisher sends media tracks to LiveKit")
    require("StartRoomCompositeEgress" in bot_source, "server bridges LiveKit room to Mux")
    print("live media transport audit ok")


if __name__ == "__main__":
    main()
