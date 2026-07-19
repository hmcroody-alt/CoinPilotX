#!/usr/bin/env python3
"""Audit compact native voice-message rendering and canonical playback reuse."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CHAT = ROOT / "mobile-native/src/screens/ChatScreen.tsx"
API = ROOT / "mobile-native/src/api/messenger.ts"
DOMAIN = ROOT / "mobile-native/src/pulseCommand/domain.ts"
PLAYBACK = ROOT / "mobile-native/src/core/voiceMessagePlayback.ts"
CALL = ROOT / "mobile-native/src/screens/CallScreen.tsx"
WEBVIEW = ROOT / "static/js/pulse_messages_v2.js"
BACKEND = ROOT / "pulse_communications_v2/service.py"
REPORT = ROOT / "reports/pulsesoc_native_voice_message_compact_redesign_2026-07-18.md"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    chat = CHAT.read_text()
    api = API.read_text()
    domain = DOMAIN.read_text()
    playback = PLAYBACK.read_text()
    call = CALL.read_text()
    webview = WEBVIEW.read_text()
    backend = BACKEND.read_text()
    failures: list[str] = []

    require('body: input.voice ? "" : input.name' in chat, "voice upload must not send its generated filename as body", failures)
    require("voiceCardKicker" not in chat and "end-to-end private channel" not in chat, "redundant playback heading/security copy must be removed", failures)
    require("voiceCardHeader" not in chat and "voiceTimeRow" not in chat, "duplicate header/time layers must be removed", failures)
    require("minHeight: 44" in chat and "voiceDuration" in chat, "voice controls must retain compact accessible geometry", failures)
    require("accessibilityRole=\"adjustable\"" in chat and "Playback speed" in chat, "seek and speed controls must be accessible", failures)
    require("normalizedWaveform(message.waveform" in chat, "canonical waveform metadata must drive the native bubble", failures)

    require("isTechnicalVoiceValue" in api, "voice normalization must suppress technical values", failures)
    for token in ("file:\\/\\/", "https?:\\/\\/", "storage|object|media", "m4a|mp4|aac|mp3|wav|webm|ogg"):
        require(token in api, f"technical voice suppression missing {token}", failures)
    require("attachment?.duration_seconds" in api and "attachment?.waveform" in api, "legacy attachment duration/waveform must normalize", failures)
    require("attachment_id:" in api and "attachment_ids?: number[]" in api, "canonical attachment identity must be preserved", failures)
    require("pulsesoc-voice-1784432743856.m4a" in api, "legacy filename-body QA fixture must remain covered", failures)

    require("let sound: Audio.Sound | null = null" in playback, "one shared voice sound owner is required", failures)
    require("activeMessageId" in playback and "listeners.get(messageId)" in playback, "playback updates must be scoped to the active row", failures)
    require("progressUpdateIntervalMillis: 250" in playback, "localized progress callback must be throttled", failures)
    require("pausePulseRadio" in playback, "voice playback must yield Pulse Radio", failures)
    require("app_backgrounded" in playback and "releaseVoicePlayback" in playback, "background/navigation cleanup must be wired", failures)
    require("stopVoiceMessagePlayback(\"call_opened\")" in call, "calls must stop voice-message playback", failures)

    require("Voice message" in domain and "durationLabel" in domain and "formatShortTime(message.created_at)" in domain, "voice accessibility summary must include duration/time", failures)
    require("data-voice-progress" in webview and "data-voice-speed" in webview, "WebView production playback contract must remain present", failures)
    require('"attachment_public_id"' in backend and '"duration_seconds"' in backend and '"waveform"' in backend, "backend canonical attachment metadata must remain present", failures)
    require(REPORT.exists(), "voice-message implementation report must exist", failures)

    if failures:
        print("PulseSoc voice message bubble audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PulseSoc voice message bubble audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
