#!/usr/bin/env python3
"""Static release-gate audit for native PulseSoc Live audio + camera pipeline."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    core = read("mobile-native/src/core/realtimeAudioEngine.ts")
    call = read("mobile-native/src/calls/useNativeCallRoom.ts")
    live = read("mobile-native/src/live/useLiveBroadcastRoom.ts")
    core_test = read("mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts")
    live_audio_test = read("mobile-native/src/live/__tests__/liveAudioConfiguration.test.ts")
    live_remote_test = read("mobile-native/src/live/__tests__/remoteAudioReapply.test.ts")
    call_audio_test = read("mobile-native/src/calls/__tests__/useNativeCallRoomAudio.test.ts")

    for token in (
        'export type RealtimeAudioMode',
        '"audio_call"',
        '"video_call"',
        '"live_host"',
        '"live_guest"',
        '"live_viewer"',
        "activeRealtimeAudioOwner",
        "claimRealtimeAudioSession",
        "activateRealtimeAudioSession",
        "releaseRealtimeAudioSession",
        "playAndRecord",
        "videoChat",
        "defaultToSpeaker",
        "allowBluetooth",
        "ensureMicrophonePublished",
        "applyRemoteAudioEnabled",
    ):
        require(token in core, f"Shared realtime audio engine missing {token}", failures)

    for token in (
        "PULSE_LIVE_VIDEO_CAPTURE_OPTIONS",
        "width: 720",
        "height: 1280",
        "aspectRatio: 9 / 16",
        'facingMode: "user"',
        "PULSE_LIVE_VIDEO_PUBLISH_OPTIONS",
        "maxBitrate: 2_300_000",
    ):
        require(token in core, f"Shared realtime engine missing portrait Live camera contract {token}", failures)

    for token in (
        "activateRealtimeAudioSession",
        "releaseRealtimeAudioSession",
        "selectRealtimeAudioOutput",
        "ensureMicrophonePublished",
        "applyRemoteAudioEnabled",
    ):
        require(token in call, f"Call room does not use shared realtime audio helper {token}", failures)

    require("setAppleAudioConfiguration({" not in call, "Call room still configures iOS audio session inline instead of shared engine", failures)

    for token in (
        "activateRealtimeAudioSession",
        "releaseRealtimeAudioSession",
        "liveAudioMode",
        "PULSE_LIVE_VIDEO_CAPTURE_OPTIONS",
        "PULSE_LIVE_VIDEO_PUBLISH_OPTIONS",
        "videoCaptureDefaults: PULSE_LIVE_VIDEO_CAPTURE_OPTIONS",
        "await ensureLiveMicrophonePublished(room);",
        "setCameraEnabled(true, PULSE_LIVE_VIDEO_CAPTURE_OPTIONS, PULSE_LIVE_VIDEO_PUBLISH_OPTIONS)",
        "setCameraEnabled(enabled, PULSE_LIVE_VIDEO_CAPTURE_OPTIONS, PULSE_LIVE_VIDEO_PUBLISH_OPTIONS)",
        "Camera changed, but microphone audio is no longer published.",
        "Camera switched, but microphone audio is no longer published.",
        "LIVE_LOCAL_AUDIO_NOT_PUBLISHED",
        "applyRemoteAudioEnabled(room, remoteAudioEnabledRef.current)",
    ):
        require(token in live, f"Live room missing permanent media pipeline guard {token}", failures)

    require("setAppleAudioConfiguration(" not in live, "Live room still configures iOS audio session inline instead of shared engine", failures)

    for token in (
        "call-grade iOS audio profile",
        "one active realtime audio owner",
        "portrait front-camera capture",
        "bounded premium Live video encoding",
    ):
        require(token in core_test, f"Core realtime audio/camera tests missing assertion text {token}", failures)

    for source, label in (
        (live_audio_test, "live audio configuration"),
        (live_remote_test, "live remote audio"),
        (call_audio_test, "call audio"),
    ):
        require("playAndRecord" in source or "ensure" in source or "remote audio" in source.lower(), f"{label} regression test does not cover media behavior", failures)

    if failures:
        print("PulseSoc Live audio/camera pipeline audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc Live audio/camera pipeline audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
