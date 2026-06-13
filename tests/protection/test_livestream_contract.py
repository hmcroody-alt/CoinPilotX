#!/usr/bin/env python3
"""Protect the PulseSoc livestream golden path from accidental regressions."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
LIVE_RUNTIME = (ROOT / "static/js/pulse_live_studio_runtime.js").read_text(encoding="utf-8")
LIVE_STUDIO = (ROOT / "static/js/pulse_live_studio.js").read_text(encoding="utf-8")
MUX_SERVICE = (ROOT / "services/mux_live_service.py").read_text(encoding="utf-8")


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    combined_live_js = LIVE_RUNTIME + "\n" + LIVE_STUDIO

    expect("StartRoomCompositeEgress" in BOT, "LiveKit room-composite egress remains wired")
    expect("StartParticipantEgress" in BOT, "LiveKit participant egress fallback remains wired")
    expect("LIVEKIT_TRACK_STABLE_CHECKS" in BOT, "host track stability gate remains present")
    expect("resource_exhausted" in BOT and "egress minutes exceeded" in BOT, "egress quota exhaustion is handled")
    expect("source closed" in BOT.lower(), "source closed egress failures are classified")
    expect("pulse_livekit_webhook" in BOT and "LIVEKIT_WEBHOOK_SECRET" in BOT, "LiveKit webhook verification route remains present")
    expect("api_pulse_live_mux_webhook" in BOT and "MUX_WEBHOOK_SECRET" in MUX_SERVICE, "Mux webhook verification route remains present")
    expect("video.defaultMuted = true" in combined_live_js, "host preview remains muted to prevent echo")
    expect("video.volume = 0" in combined_live_js, "host preview volume remains zero")
    expect("createLocalTracks" in combined_live_js, "browser live still publishes local tracks")
    expect("width: { ideal: 1920 }" in combined_live_js and "height: { ideal: 1080 }" in combined_live_js, "1080p live camera profile remains first")
    expect("width: { ideal: 1280 }" in combined_live_js and "height: { ideal: 720 }" in combined_live_js, "720p live camera fallback remains present")
    expect("maskDeviceId" in combined_live_js, "camera diagnostics mask device identifiers")
    expect("rtmp_url" in BOT and "stream_key" in BOT, "RTMP fields remain backend-owned")
    expect("Stream keys are visible only to the host Studio" in BOT, "stream key exposure warning remains host-only")
    print("livestream protection contract ok")


if __name__ == "__main__":
    main()
