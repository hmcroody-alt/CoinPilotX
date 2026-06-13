#!/usr/bin/env python3
"""Verify PulseSoc does not start LiveKit egress before host video is published."""

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
            ("livegateaudit", "Live Gate Audit", "live-gate-audit@example.com", now, now),
        )
        user_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,status,publish_state,stream_key,viewer_count,created_at,started_at,stream_uuid,
         hls_url,playback_url,webrtc_room_id,stream_health,mux_live_status,mux_stream_key,ingest_url,rtmp_url,updated_at)
        VALUES (?, 'Egress Gate Live', 'Creator QA', 'starting', 'starting', 'gate_key', 0, ?, ?, 'gateaudit',
                '', '', 'pulse-webrtc-gateaudit', 'starting', 'idle', 'mux_key_gate', 'rtmp://global-live.mux.com:5222/app',
                'rtmp://global-live.mux.com:5222/app', ?)
        """,
        (user_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    egress_calls = {"count": 0}
    original_ready = bot.pulse_livekit_wait_for_host_tracks
    original_egress = bot.pulse_livekit_start_mux_egress

    def not_ready(live, trace_id=""):
        return {
            "ready": False,
            "room": live.get("webrtc_room_id") or "",
            "host_joined": True,
            "participant_count": 1,
            "audio_tracks": 1,
            "video_tracks": 0,
            "reason": "video_track_not_ready",
            "published_tracks": [{"type": "audio"}],
        }

    def fail_if_called(live, trace_id=""):
        egress_calls["count"] += 1
        return {"ok": False, "reason": "should_not_start"}

    bot.pulse_livekit_wait_for_host_tracks = not_ready
    bot.pulse_livekit_start_mux_egress = fail_if_called
    try:
        client = bot.webhook_app.test_client()
        with client.session_transaction() as sess:
            sess["account_user_id"] = user_id
        response = client.post(f"/api/pulse/live/{live_id}/browser-publish", json={"audio_tracks": 1, "video_tracks": 1})
        data = response.get_json() or {}
    finally:
        bot.pulse_livekit_wait_for_host_tracks = original_ready
        bot.pulse_livekit_start_mux_egress = original_egress

    require(response.status_code == 409, "browser publish returns retryable 409 while host video is missing")
    require(data.get("retryable") is True, "response tells browser to retry instead of failing hard")
    require(data.get("status") == "waiting_for_tracks", "response exposes waiting_for_tracks status")
    require(egress_calls["count"] == 0, "LiveKit egress is not started before video track readiness")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT status,publish_state,stream_health,livekit_egress_status,is_live FROM pulse_live_sessions WHERE id=?", (live_id,))
    saved = dict(cur.fetchone() or {})
    conn.close()
    require(saved.get("status") == "starting", "session remains starting while egress waits")
    require(saved.get("publish_state") == "livekit_waiting_for_tracks", "publish state records LiveKit track wait")
    require(saved.get("stream_health") == "livekit_waiting_for_tracks", "stream health records LiveKit track wait")
    require(saved.get("livekit_egress_status") == "waiting_for_tracks", "egress status records wait state")
    require(int(saved.get("is_live") or 0) == 0, "session is not marked live before Mux egress starts")
    print("livekit mux egress gate audit ok")


if __name__ == "__main__":
    main()
