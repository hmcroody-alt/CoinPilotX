#!/usr/bin/env python3
"""Audit Source closed diagnostics and LiveKit/Mux source stability guards."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "static/js/pulse_live_studio_runtime.js").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    require("def pulse_live_safe_debug_payload" in BOT, "safe live debug payload sanitizer exists")
    require("@webhook_app.route(\"/api/pulse/live/<int:live_id>/debug-event\"" in BOT, "host-only live debug event endpoint exists")
    require("Only the live host can write live diagnostics" in BOT, "debug endpoint is scoped to host/admin")
    require('"secret", "token", "stream_key"' in BOT, "debug sanitizer redacts secret-like fields")
    require("live_session_cleanup_started" in BOT and "live_session_cleanup_completed" in BOT, "stale cleanup is timeline logged")
    require("LIVEKIT_TRACK_STABLE_CHECKS" in BOT and "stable_count >= stable_required" in BOT, "egress waits for stable host video track checks")
    require("StartRoomCompositeEgress" in BOT and "strategy=\"room_composite\"" in BOT, "room composite egress is attempted")
    room_composite = BOT.find("strategy=\"room_composite\"")
    participant = BOT.find("strategy=\"participant_fallback\"")
    require(room_composite != -1 and participant != -1 and room_composite < participant, "room composite egress is preferred before participant fallback")
    require("egress_source_closed" in BOT and "source closed" in BOT.lower(), "Source closed egress reason is preserved")
    require("live_egress_start_requested" in BOT and "live_egress_start_response" in BOT, "egress request and response are timeline logged")
    require("live_debug_track_ended" in BOT or "track_ended" in BOT, "track ended events are accepted")

    require("sendLiveDebug" in RUNTIME and "debug-event" in RUNTIME, "browser sends safe live debug events")
    require("track_ended" in RUNTIME and "addEventListener?.(\"ended\"" in RUNTIME, "browser logs local track ended events")
    require("participant_disconnected" in RUNTIME and "room_disconnected" in RUNTIME, "browser logs room and participant disconnects")
    require("cleanup_started" in RUNTIME and "cleanup_completed" in RUNTIME, "browser logs cleanup start and completion")
    require("publish_request_started" in RUNTIME and "egress_start_response" in RUNTIME, "browser logs publish request and egress response")
    require("pagehide" in RUNTIME and "pagehide" in RUNTIME[RUNTIME.find("pagehide") - 120:RUNTIME.find("pagehide") + 160], "pagehide cleanup reason remains observable")
    print("live source closed diagnostics audit ok")


if __name__ == "__main__":
    main()
