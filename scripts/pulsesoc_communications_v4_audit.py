#!/usr/bin/env python3
"""Static audit for PulseSoc Communications V4 call polish."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
MESSENGER_JS = ROOT / "static" / "js" / "pulse_messages_v2.js"
I18N_JS = ROOT / "static" / "js" / "pulse_i18n.js"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
ENGINE = ROOT / "services" / "pulsesoc_communications_engine.py"
NOTIFICATIONS = ROOT / "services" / "pulsesoc_notification_system.py"
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
REPORT = ROOT / "reports" / "pulsesoc_communications_v4_call_experience.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[tuple[str, bool]], name: str, ok: bool) -> None:
    checks.append((name, ok))


def main() -> int:
    call_js = read(CALL_JS)
    messenger_js = read(MESSENGER_JS)
    i18n_js = read(I18N_JS)
    routes = read(ROUTES)
    engine = read(ENGINE)
    notifications = read(NOTIFICATIONS)
    template = read(TEMPLATE)
    report = read(REPORT) if REPORT.exists() else ""
    checks: list[tuple[str, bool]] = []

    require(checks, "Pulse language keys are localized", all(token in i18n_js for token in [
        "pulse.call.outgoing",
        "pulse.call.connected",
        "pulse.call.missed",
        "pulse.call.interrupted",
    ]))
    require(checks, "Messenger loads i18n before call client", "pulse_i18n.js" in template and template.find("pulse_i18n.js") < template.find("pulsesoc_calls.js"))
    require(checks, "outgoing/incoming call tones exist", "startCallTone" in call_js and "playPulseTone" in call_js and "stopCallTone" in call_js)
    require(checks, "camera off unpublishes video", "stopLocalTracks(\"video\")" in call_js and "disable-video" in call_js and "unpublished" in call_js)
    require(checks, "camera on republishes without reconnect", "publishSingleLocalTrack(\"video\")" in call_js and "enable-video" in call_js)
    require(checks, "flip camera uses restart or republish", "restartTrack" in call_js and "method: \"republish\"" in call_js and "switch-camera" in call_js)
    require(checks, "speaker uses real output API where supported", "setSinkId" in call_js and "enumerateDevices" in call_js and "speaker" in call_js)
    require(checks, "end performs local cleanup before stale overlay can remain", "await disconnectRoom(\"ended_by_user\")" in call_js and "shell.hidden = true" in call_js)
    require(checks, "background foreground recovery exists", "visibilitychange" in call_js and "ensureLocalAudioTrack" in call_js and "visibility" in call_js)
    require(checks, "quality telemetry includes call control state", all(token in call_js for token in ["reconnect_count", "muted_audio", "muted_video", "speaker_mode", "local_audio_tracks"]))
    require(checks, "backend event routes for extra controls exist", all(token in routes for token in ["api_call_switch_camera", "api_call_speaker", "api_call_minimize", "api_call_restore", "api_call_visibility"]))
    require(checks, "engine records event-only controls", all(token in engine for token in ["camera_switched", "speaker_changed", "call_minimized", "call_restored", "client_visibility_changed"]))
    require(checks, "incoming notification uses Pulse language", "is Pulsing You" in engine and "Voice Connection" in engine)
    require(checks, "missed notification uses Pulse language", "Missed Pulse" in notifications and "tried to reach you" in notifications)
    require(checks, "Messenger start status uses Pulse wording", "Voice Pulse sent." in messenger_js and "Video Pulse sent." in messenger_js)
    require(checks, "no internal LogiNexus text exposed in call surface", "LogiNexus" not in call_js and "LogiNexus" not in messenger_js and "LogiNexus" not in template)
    require(checks, "V4 report exists", "PulseSoc Communications V4" in report and "Pulsing" in report)

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
