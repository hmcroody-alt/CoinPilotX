#!/usr/bin/env python3
"""Static audit for the PulseSoc Communications Engine foundation."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "pulsesoc_communications_engine.py"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
JS = ROOT / "static" / "pulsesoc_calls.js"
MESSENGER_JS = ROOT / "static" / "js" / "pulse_messages_v2.js"
CSS = ROOT / "static" / "css" / "pulse_messages_v2.css"
MIGRATION = ROOT / "migrations" / "pulsesoc_communications_engine.sql"
REPORT = ROOT / "reports" / "pulsesoc_communications_engine_foundation.md"
PHASE2_REPORT = ROOT / "reports" / "pulsesoc_real_call_experience_phase2.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    service = read(SERVICE)
    routes = read(ROUTES)
    template = read(TEMPLATE)
    js = read(JS)
    messenger_js = read(MESSENGER_JS)
    css = read(CSS)
    migration = read(MIGRATION)
    report = read(REPORT) if REPORT.exists() else ""
    phase2_report = read(PHASE2_REPORT) if PHASE2_REPORT.exists() else ""

    for table in [
        "communication_calls",
        "communication_call_participants",
        "communication_call_events",
        "communication_call_quality_reports",
        "communication_call_device_sessions",
    ]:
        require(checks, f"{table} migration exists", table in migration and table in service)

    for env_name in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_WEBHOOK_SECRET"]:
        require(checks, f"{env_name} handled", env_name in service)

    for route in [
        '"/api/calls/start"',
        '"/api/calls/<path:call_id>/accept"',
        '"/api/calls/<path:call_id>/decline"',
        '"/api/calls/<path:call_id>/end"',
        '"/api/calls/<path:call_id>/join-token"',
        '"/api/calls/<path:call_id>/status"',
        '"/api/calls/active"',
        '"/api/calls/<path:call_id>/quality"',
        '"/api/livekit/webhook"',
    ]:
        require(checks, f"route {route} exists", route in routes)

    for admin_route in ['"/api/admin/calls/recent"', '"/api/admin/calls/<path:call_id>"', '"/api/admin/calls/test-config"']:
        require(checks, f"admin route {admin_route} exists", admin_route in routes and "_current_admin()" in routes)

    for fn in [
        "start_call",
        "accept_call",
        "decline_call",
        "end_call",
        "join_token",
        "call_status",
        "active_calls",
        "submit_quality_report",
        "livekit_webhook",
        "mark_missed_stale_calls",
    ]:
        require(checks, f"{fn} service function exists", f"def {fn}" in service)

    require(checks, "call state transitions exist", "ALLOWED_TRANSITIONS" in service and "ACTIVE_STATUSES" in service and "FINAL_STATUSES" in service)
    require(checks, "participant validation exists", "_conversation_access" in service and "_participant_allowed" in service and "Every recipient must be a conversation participant" in service)
    require(checks, "self-call blocked", "You cannot call yourself" in service)
    require(checks, "join token validates participant", "Only call participants can access this call" in service and "_generate_livekit_token" in service)
    require(checks, "config missing is structured", "config_missing" in service and "LIVEKIT_CONFIG_MISSING" in service and "error_code" in service)
    require(checks, "incoming call notification hook exists", "incoming_call" in service and "pulsesoc_notification_system.intake_event" in service)
    require(checks, "missed call notification hook exists", "notify_missed_call" in service and "missed_call" in service)
    require(checks, "webhook verification exists", "LIVEKIT_WEBHOOK_SECRET" in service and "hmac.new" in service)
    require(checks, "quality telemetry endpoint saves metrics", "communication_call_quality_reports" in service and "quality_score" in service)
    require(checks, "phase 2 connected route exists", '"/api/calls/<path:call_id>/connected"' in routes and "mark_connected" in service)
    require(checks, "phase 2 call events route exists", '"/api/calls/<path:call_id>/events"' in routes and "call_events" in service)
    require(checks, "phase 2 call history route exists", '"/api/conversations/<path:conversation_ref>/calls"' in routes and "conversation_calls" in service)
    require(checks, "phase 2 participant controls exist", "mute-audio" in routes and "screen-share/start" in routes and "update_participant_control" in service)
    require(checks, "frontend call service exists", "window.PulseSocCalls" in js and "startAudioCall" in js and "startVideoCall" in js)
    require(checks, "frontend LiveKit join flow exists", "new LK.Room" in js and "publishLocalTracks" in js and "TrackSubscribed" in js)
    require(checks, "permission readiness checks exist", "getUserMedia" in js and "NotAllowedError" in js)
    require(checks, "messenger loads call service and LiveKit bundle", "pulsesoc_calls.js" in template and "livekit-client.umd.js" in template)
    require(checks, "messenger header call buttons exist", "data-thread-call-audio" in template and "data-thread-call-video" in template)
    require(checks, "messenger buttons use central call service", "PulseSocCalls.startAudioCall" in messenger_js and "PulseSocCalls.startVideoCall" in messenger_js)
    require(checks, "control center call quick actions wired", "start-audio-call" in messenger_js and "start-video-call" in messenger_js)
    require(checks, "call overlay styles exist", ".pulsesoc-call-shell" in css and ".pulsesoc-call-card" in css)
    require(checks, "secrets not exposed to frontend", "LIVEKIT_API_SECRET" not in js and "LIVEKIT_API_SECRET" not in messenger_js)
    require(checks, "completion report exists", "PulseSoc Communications Engine" in report and "Phase 2" in report)
    require(checks, "phase 2 report exists", "PulseSoc Real Call Experience" in phase2_report and "Phase 2" in phase2_report)

    passed = sum(1 for check in checks if check["passed"])
    failed = [check for check in checks if not check["passed"]]
    print(json.dumps({"ok": not failed, "passed": passed, "failed": failed, "total": len(checks)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
