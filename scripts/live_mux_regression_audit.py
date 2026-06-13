#!/usr/bin/env python3
"""Audit PulseSoc LiveKit-to-Mux regression protections."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
DISTRIBUTION = ROOT / "services/live_distribution_service.py"
HEALTH = ROOT / "services/live_stream_health_service.py"
RUNTIME = ROOT / "static/js/pulse_live_studio_runtime.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    distribution = DISTRIBUTION.read_text(encoding="utf-8")
    health = HEALTH.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")

    expect("def pulse_live_cleanup_stale_sessions" in bot, "stale live session cleanup helper exists")
    expect("pulse_live_cleanup_stale_sessions(cur, user_id" in bot, "new live start cleans stale sessions first")
    expect("pulse_livekit_stop_egress" in bot and "StopEgress" in bot, "stale LiveKit egress can be stopped")
    expect("pulse_livekit_delete_room" in bot and "DeleteRoom" in bot, "stale LiveKit room can be deleted")
    expect("disable_mux_live_stream" in bot, "stale Mux live streams are disabled before replacement")
    expect("publish_state='ready'" in bot, "new live sessions start ready, not public publishing")
    expect("follower_notifications = 0" in bot and "livestream_created" in bot, "followers are not notified before Mux active")
    expect("WHERE s.status='live' AND COALESCE(s.mux_live_status,'') IN ('active','live')" in bot, "live discovery only lists Mux-active streams")
    expect("is_active_live = mux_active and bool(playback_url)" in bot, "public/studio player gates on Mux-active playback")
    egress_ok_block = bot[bot.find('if egress.get("ok")'):bot.find("elif quota_exhausted")]
    expect("is_live = 0" in egress_ok_block and 'status = "starting"' in egress_ok_block, "egress start does not mark stream public live")
    expect("egress ended before mux became active" in bot.lower(), "egress source-closed/ended records a safe failure")
    expect("def pulse_sync_mux_live_state" in bot and "mux_live_service.get_mux_live_stream" in bot, "Mux status is synced from API")

    expect('mux_public_live = mux_status in {"active", "live"}' in distribution, "playback helper defines Mux-public-live gate")
    expect("hls_url = explicit_hls if mux_public_live else """ in distribution, "stale HLS URLs are hidden until Mux active")
    expect('"mux_public_live": mux_public_live' in distribution, "playback manifest reports Mux-public-live state")
    expect('"rtmp_url": ""' in distribution, "playback manifest does not expose private ingest RTMP URLs")
    expect('mux_ingest_active = mux_status in {"active", "live"}' in health, "health treats only Mux active/live as ingest active")
    expect('"livekit-direct-unpublished"' in health, "LiveKit direct is not counted as public Mux ingest")

    expect('const active = ["active", "live"].includes(muxStatus) && Boolean(playbackUrl);' in runtime, "viewer player only opens for active Mux playback")
    expect("public playback waits for Mux active" in runtime, "runtime explains Mux-active gate")
    expect('const muxStatus = String(data.mux?.live_status || "").toLowerCase();' in runtime, "runtime defines muxStatus in applyState")
    print("live mux regression audit ok")


if __name__ == "__main__":
    main()
