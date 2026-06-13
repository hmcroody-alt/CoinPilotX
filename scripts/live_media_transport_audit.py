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
    original_ready = bot.pulse_livekit_wait_for_host_tracks
    bot.pulse_livekit_wait_for_host_tracks = lambda live, trace_id="": {
        "ready": True,
        "room": live.get("webrtc_room_id") or "pulse-webrtc-transportaudit",
        "host_joined": True,
        "participant_count": 1,
        "audio_tracks": 1,
        "video_tracks": 1,
        "published_tracks": [{"type": "audio"}, {"type": "video"}],
    }
    bot.pulse_livekit_start_mux_egress = lambda live, trace_id="": {
        "ok": True,
        "egress_id": "EG_TRANSPORT",
        "status": "EGRESS_STARTING",
        "room": live.get("webrtc_room_id") or "pulse-webrtc-transportaudit",
    }
    publish = client.post(f"/api/pulse/live/{live_id}/browser-publish", json={"audio_tracks": 1, "video_tracks": 1}).get_json() or {}
    bot.pulse_livekit_start_mux_egress = original_egress
    bot.pulse_livekit_wait_for_host_tracks = original_ready
    require(publish.get("ok"), "browser media publish accepts audio and video tracks")
    require(publish.get("publish_path") == "livekit_mux_egress", "browser media publish starts LiveKit to Mux bridge")
    require(publish.get("mux_waiting") is True, "Mux playback waits for active ingest")
    require(publish.get("playback", {}).get("supports_hls") is False, "HLS is hidden until Mux is active")
    require(publish.get("playback", {}).get("supports_webrtc"), "WebRTC fallback remains available")
    viewer = client.get(f"/pulse/live/{live_id}")
    html = viewer.get_data(as_text=True)
    require("data-live-player" not in html, "viewer does not render public player before Mux active")
    require("live-ready-orb" in html, "viewer shows waiting state before Mux active")
    source = (ROOT / "static/js/pulse_live_studio.js").read_text(encoding="utf-8")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("pulse_livekit_start_mux_egress" in bot_source, "backend starts LiveKit egress to Mux")
    require("connectLiveKitRoom" in source, "browser transport connects to LiveKit room")
    require("getAudioTracks" in source and "getVideoTracks" in source, "publisher diagnostics inspect audio and video tracks")
    require("publishTrack(track)" in source, "publisher sends media tracks to LiveKit")
    require("StartRoomCompositeEgress" in bot_source, "server bridges LiveKit room composite to Mux first")
    require("StartParticipantEgress" in bot_source, "server keeps host participant fallback to Mux")
    require(bot_source.find('strategy="room_composite"') < bot_source.find('strategy="participant_fallback"'), "room composite is preferred before participant fallback")
    require("participant_error" in bot_source and "room_composite_error" in bot_source, "server exposes safe bridge failure diagnostics")
    print("live media transport audit ok")


if __name__ == "__main__":
    main()
