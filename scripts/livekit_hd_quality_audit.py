#!/usr/bin/env python3
"""Audit PulseSoc LiveKit HD quality wiring."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


CHECKS = [
    (
        "call client has HD room options",
        "static/pulsesoc_calls.js",
        ["livekitRoomOptions", "adaptiveStream: true", "dynacast: true", "publishDefaults"],
    ),
    (
        "call client has 720p/30 capture constraints",
        "static/pulsesoc_calls.js",
        ["CALL_AUDIO_CONSTRAINTS", "callVideoConstraints", "ideal: 1280", "ideal: 720", "ideal: 30"],
    ),
    (
        "call client publishes simulcast video",
        "static/pulsesoc_calls.js",
        ["livekitPublishOptions", "simulcast: true", "videoSimulcastLayers", "maxBitrate: 2_500_000"],
    ),
    (
        "call client requests remote subscription quality by layout",
        "static/pulsesoc_calls.js",
        ["syncRemoteSubscriptionQuality", "setPublicationQuality", "setVideoQuality", "remote_video_attached"],
    ),
    (
        "call client keeps camera state sourced from real media",
        "static/pulsesoc_calls.js",
        ["syncLocalCameraSurface", "localCameraIsLive", "clearVideoElement", "publicationVideoIsLive"],
    ),
    (
        "call quality telemetry captures media stats",
        "static/pulsesoc_calls.js",
        ["callQualitySnapshot", "outbound_bitrate_kbps", "inbound_bitrate_kbps", "packet_loss", "jitter_ms"],
    ),
    (
        "live studio has 1080p host fallback ladder",
        "static/js/pulse_live_studio_runtime.js",
        ["liveHdCameraProfiles", "ideal: 1920", "ideal: 1080", "ideal: 1280", "ideal: 720"],
    ),
    (
        "live studio publishes HD simulcast defaults",
        "static/js/pulse_live_studio_runtime.js",
        ["livekitHdRoomOptions", "livekitHdPublishOptions", "simulcast: true", "videoSimulcastLayers", "maxBitrate: 4_200_000"],
    ),
    (
        "live studio tunes browser fallback sender quality",
        "static/js/pulse_live_studio_runtime.js",
        ["tuneRtcSender", "setParameters", "maxFramerate: 30", "maintain-framerate"],
    ),
    (
        "live diagnostics report capture and sender stats",
        "static/js/pulse_live_studio_runtime.js",
        ["collectVideoSenderStats", "capture_width", "capture_height", "liveVideoBitrate", "liveVideoFps"],
    ),
    (
        "backend exposes HD quality policy",
        "services/pulsesoc_communications_engine.py",
        ["livekit_hd_quality_policy", "adaptive_stream", "dynacast", "simulcast", "Mux quality depends"],
    ),
    (
        "backend summarizes call quality reports",
        "services/pulsesoc_communications_engine.py",
        ["_quality_summary", "capture_resolution", "rendered_resolution", "hd_capture_observed", "quality_summary"],
    ),
    (
        "admin API has quality test route",
        "pulse_communications_v2/routes.py",
        ["/api/admin/calls/quality-test", "admin_livekit_quality_test", "Admin access required"],
    ),
    (
        "Calls Command Center shows HD diagnostics",
        "templates/admin_calls_command_center.html",
        ["Run HD Quality Test", "Quality Reports", "quality_summary", "capture_resolution"],
    ),
    (
        "upgrade report exists",
        "reports/livekit_hd_quality_upgrade.md",
        ["LiveKit HD Quality Upgrade", "Mux", "QA"],
    ),
]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    for name, rel, needles in CHECKS:
        path = ROOT / rel
        if not path.exists():
            failures.append(f"{name}: missing {rel}")
            continue
        text = read(rel)
        missing = [needle for needle in needles if needle not in text]
        if missing:
            failures.append(f"{name}: missing {', '.join(missing)}")
    if failures:
        print("LiveKit HD quality audit failed:")
        for item in failures:
            print(f"- {item}")
        return 1
    print(f"LiveKit HD quality audit passed ({len(CHECKS)} checks).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
