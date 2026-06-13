#!/usr/bin/env python3
"""Audit LiveKit egress quota fallback keeps Browser Live usable."""

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
    cur.execute(
        "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
        ("livequotaaudit", "Live Quota Audit", "live-quota-audit@example.com", now, now),
    )
    user_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,publish_state,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,playback_url,webrtc_room_id,stream_health,updated_at)
        VALUES (?, 'Quota Fallback Live', 'Creator QA', 'starting', 'starting', 'quota_key', 0, ?, ?, 'quotaaudit', 'https://live.coinpilotxai.app/hls/quotaaudit.m3u8', 'https://live.coinpilotxai.app/hls/quotaaudit.m3u8', 'pulse-webrtc-quotaaudit', 'starting', ?)
        """,
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id

    original_egress = bot.pulse_livekit_start_mux_egress
    original_ready = bot.pulse_livekit_wait_for_host_tracks
    bot.pulse_livekit_wait_for_host_tracks = lambda live, trace_id="": {
        "ready": True,
        "room": live.get("webrtc_room_id") or "pulse-webrtc-quotaaudit",
        "host_joined": True,
        "participant_count": 1,
        "audio_tracks": 1,
        "video_tracks": 1,
        "published_tracks": [{"type": "audio"}, {"type": "video"}],
    }
    bot.pulse_livekit_start_mux_egress = lambda live, trace_id="": {
        "ok": False,
        "reason": "egress_rejected",
        "message": "LiveKit egress was rejected. Participant: egress minutes exceeded | resource_exhausted. Room composite: egress minutes exceeded | resource_exhausted.",
        "participant_error": "egress minutes exceeded | resource_exhausted",
        "room_composite_error": "egress minutes exceeded | resource_exhausted",
    }
    try:
        response = client.post(f"/api/pulse/live/{live_id}/browser-publish", json={"audio_tracks": 1, "video_tracks": 1})
        publish = response.get_json() or {}
    finally:
        bot.pulse_livekit_start_mux_egress = original_egress
        bot.pulse_livekit_wait_for_host_tracks = original_ready

    require(response.status_code == 200, "egress quota exhaustion does not fail Browser Live publish")
    require(publish.get("ok") is True, "Browser Live remains accepted")
    require(publish.get("publish_path") == "livekit_direct", "publish path downgrades to LiveKit direct")
    require(publish.get("egress", {}).get("quota_exhausted") is True, "response flags egress quota exhaustion")
    require(publish.get("playback", {}).get("supports_webrtc") is True, "direct playback remains available")
    require(publish.get("playback", {}).get("supports_hls") is False, "stale HLS is suppressed in direct mode")

    state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require(state.get("status") == "starting", "session does not become public live after quota fallback")
    require(state.get("publish_state") == "browser_live_livekit_direct", "state records LiveKit direct fallback")
    require(state.get("direct_mode") is True, "state exposes direct mode")
    require(state.get("health", {}).get("ingest_source") == "livekit-direct-unpublished", "health reports LiveKit direct as unpublished")
    require("Mux active" not in state.get("health", {}).get("bitrate_label", ""), "health does not fake Mux active")

    viewer_html = client.get(f"/pulse/live/{live_id}").get_data(as_text=True)
    require("data-live-player" not in viewer_html, "direct viewer does not render public Mux player")
    require("live-ready-orb" in viewer_html, "direct viewer shows waiting card")
    print("live egress quota fallback audit ok")


if __name__ == "__main__":
    main()
