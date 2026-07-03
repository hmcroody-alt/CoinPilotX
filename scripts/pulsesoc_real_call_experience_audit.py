#!/usr/bin/env python3
"""Static audit for PulseSoc Communications Engine Phase 2 real call UX."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "pulsesoc_communications_engine.py"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
MESSENGER_JS = ROOT / "static" / "js" / "pulse_messages_v2.js"
CSS = ROOT / "static" / "css" / "pulse_messages_v2.css"
REPORT = ROOT / "reports" / "pulsesoc_real_call_experience_phase2.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    service = read(SERVICE)
    routes = read(ROUTES)
    template = read(TEMPLATE)
    call_js = read(CALL_JS)
    messenger_js = read(MESSENGER_JS)
    css = read(CSS)
    report = read(REPORT) if REPORT.exists() else ""

    for fn in [
        "start_call",
        "accept_call",
        "decline_call",
        "end_call",
        "join_token",
        "mark_connected",
        "call_events",
        "conversation_calls",
        "update_participant_control",
        "submit_quality_report",
        "mark_missed_stale_calls",
    ]:
        require(checks, f"{fn} service function exists", f"def {fn}" in service)

    for route in [
        '"/api/calls/start"',
        '"/api/calls/<path:call_id>/accept"',
        '"/api/calls/<path:call_id>/ring-seen"',
        '"/api/calls/<path:call_id>/decline"',
        '"/api/calls/<path:call_id>/end"',
        '"/api/calls/<path:call_id>/join-token"',
        '"/api/calls/<path:call_id>/connected"',
        '"/api/calls/<path:call_id>/status"',
        '"/api/calls/<path:call_id>/events"',
        '"/api/calls/active"',
        '"/api/conversations/<path:conversation_ref>/calls"',
        '"/api/calls/<path:call_id>/quality"',
        '"/api/calls/<path:call_id>/mute-audio"',
        '"/api/calls/<path:call_id>/unmute-audio"',
        '"/api/calls/<path:call_id>/enable-video"',
        '"/api/calls/<path:call_id>/disable-video"',
        '"/api/calls/<path:call_id>/screen-share/start"',
        '"/api/calls/<path:call_id>/screen-share/stop"',
    ]:
        require(checks, f"route {route} exists", route in routes)

    require(checks, "LiveKit client bundle loaded", "livekit-client.umd.js" in template)
    require(checks, "Messenger buttons use central service", "PulseSocCalls.startAudioCall" in messenger_js and "PulseSocCalls.startVideoCall" in messenger_js)
    require(checks, "Conversation Control Center uses central service", "start-audio-call" in messenger_js and "start-video-call" in messenger_js)
    require(checks, "frontend LiveKit Room connect exists", "new LK.Room" in call_js and ".connect(join.livekit_url, join.token)" in call_js)
    require(checks, "frontend publishes local tracks", "publishLocalTracks" in call_js and "publishTrack" in call_js)
    require(checks, "frontend subscribes remote tracks", "TrackSubscribed" in call_js and "attachRemoteTrack" in call_js)
    require(checks, "incoming call UI exists", "data-call-incoming-actions" in call_js and "showIncoming" in call_js)
    require(checks, "incoming call overlay acknowledgement exists", "mark_ring_seen" in service and "incoming_call_overlay_opened" in service and "ring-seen" in call_js)
    require(checks, "active/outgoing call UI exists", "data-call-active-controls" in call_js and "renderMode(\"outgoing\"" in call_js)
    require(checks, "permissions handled by track creation", "getUserMedia" in call_js and "NotAllowedError" in call_js)
    require(checks, "mic/camera controls update backend", "mute-audio" in call_js and "disable-video" in call_js)
    require(checks, "camera flip exists", "switchCamera" in call_js and "restartTrack" in call_js)
    require(checks, "incoming polling exists", "/active" in call_js and "pollActiveCalls" in call_js)
    require(checks, "incoming polling resumes on app return", "wakeCallPolling" in call_js and "pageshow" in call_js and "focus" in call_js)
    require(checks, "deep link call handling exists", "call_id" in call_js and "handleDeepLinkedCall" in call_js)
    require(checks, "quality reporting exists", "submitQualityReport" in call_js and "QUALITY_MS" in call_js)
    require(checks, "config missing is user safe and structured", "LIVEKIT_CONFIG_MISSING" in service and "error_title" in service and "config_missing" in service)
    require(checks, "incoming call notification hook exists", "incoming_call" in service and "sound_key" in service and "vibration" in service)
    require(checks, "missed call timeout exists", "_mark_missed_stale_calls_cur" in service and "ring_timeout" in service)
    require(checks, "call overlay CSS exists", ".pulsesoc-call-shell" in css and ".pulsesoc-call-stage" in css and ".pulsesoc-call-actions .is-accept" in css)
    require(checks, "frontend does not expose LiveKit secret", "LIVEKIT_API_SECRET" not in call_js and "LIVEKIT_API_SECRET" not in messenger_js)
    require(checks, "Phase 2 report exists", "PulseSoc Real Call Experience" in report and "Phase 2" in report)

    passed = sum(1 for check in checks if check["passed"])
    failed = [check for check in checks if not check["passed"]]
    print(json.dumps({"ok": not failed, "passed": passed, "failed": failed, "total": len(checks)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
