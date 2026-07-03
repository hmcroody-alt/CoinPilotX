#!/usr/bin/env python3
"""Static audit for PulseSoc call Phase 3 live-provider activation gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "pulsesoc_communications_engine.py"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
MESSENGER_JS = ROOT / "static" / "js" / "pulse_messages_v2.js"
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
REPORT = ROOT / "reports" / "pulsesoc_calls_phase3_live_qa_activation.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    service = read(SERVICE)
    routes = read(ROUTES)
    call_js = read(CALL_JS)
    messenger_js = read(MESSENGER_JS)
    template = read(TEMPLATE)
    report = read(REPORT) if REPORT.exists() else ""

    for env_name in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_WEBHOOK_SECRET"]:
        require(checks, f"{env_name} presence is checked", env_name in service)

    require(checks, "LiveKit config check exists", "def livekit_config_status" in service and "missing" in service)
    require(checks, "safe config_missing still works with structured error", "safe_mode" in service and "LIVEKIT_CONFIG_MISSING" in service and "error_description" in service)
    require(checks, "admin test-config route exists", '"/api/admin/calls/test-config"' in routes and "api_admin_call_test_config" in routes)
    require(checks, "test-config route is admin-only", "admin = _current_admin()" in routes and "Admin access required" in routes)
    require(checks, "test-config returns provider flags", "url_present" in service and "api_key_present" in service and "api_secret_present" in service)
    require(checks, "token generation diagnostic exists", "can_generate_token" in service and "_generate_livekit_token" in service)
    require(checks, "test room diagnostic exists", "can_create_test_room" in service and "CreateRoom" in service and "DeleteRoom" in service)
    require(checks, "provider diagnostic does not expose secrets", "LIVEKIT_API_SECRET" not in call_js and "LIVEKIT_API_SECRET" not in messenger_js)

    for route in [
        '"/api/calls/start"',
        '"/api/calls/<path:call_id>/accept"',
        '"/api/calls/<path:call_id>/ring-seen"',
        '"/api/calls/<path:call_id>/decline"',
        '"/api/calls/<path:call_id>/end"',
        '"/api/calls/<path:call_id>/join-token"',
        '"/api/calls/<path:call_id>/status"',
        '"/api/calls/<path:call_id>/events"',
        '"/api/calls/<path:call_id>/quality"',
        '"/api/conversations/<path:conversation_ref>/calls"',
        '"/api/admin/calls/<path:call_id>/delivery"',
    ]:
        require(checks, f"{route} route exists", route in routes)

    require(checks, "join-token validates participant", "_require_call_access" in service and "Only call participants can access this call" in service)
    require(checks, "incoming call notification hook exists", "incoming_call" in service and "priority=\"urgent\"" in service and "sound_key" in service)
    require(checks, "incoming call uses communication_call source", 'source_type="communication_call"' in service and '"source_type": "communication_call"' in service)
    require(checks, "incoming call realtime publish exists", "_publish_call_realtime" in service and "communication_call_incoming" in service and "call_started" in service)
    require(checks, "recipient ring acknowledgement exists", "mark_ring_seen" in service and "incoming_call_overlay_opened" in service and "ring-seen" in call_js)
    require(checks, "call delivery diagnostics exist", "call_delivery_diagnostics" in service and "push_job_created" in service and "recipient_push_token_exists" in service and "recipient_overlay_opened" in service)
    require(checks, "missed-call cleanup exists", "_mark_missed_stale_calls_cur" in service and "missed_call" in service)
    require(checks, "call history route exists", "conversation_calls" in service and "/api/conversations/" in routes)
    require(checks, "quality reporting route exists", "submit_quality_report" in service and "communication_call_quality_reports" in service)
    require(checks, "Messenger buttons are wired", "PulseSocCalls.startAudioCall" in messenger_js and "PulseSocCalls.startVideoCall" in messenger_js)
    require(checks, "Conversation Control Center buttons are wired", "start-audio-call" in messenger_js and "start-video-call" in messenger_js)
    require(checks, "LiveKit client bundle is loaded", "livekit-client.umd.js" in template)
    require(checks, "frontend joins LiveKit room", "new LK.Room" in call_js and ".connect(join.livekit_url, join.token)" in call_js)
    require(checks, "frontend handles remote tracks", "TrackSubscribed" in call_js and "attachRemoteTrack" in call_js)
    require(checks, "frontend handles reconnect states", "Reconnecting" in call_js and "Reconnected" in call_js)
    require(checks, "frontend listens for incoming call realtime events", "communication_call_incoming" in call_js and "handleIncomingRealtime" in call_js)
    require(checks, "frontend wakes polling on browser resume", "pageshow" in call_js and "focus" in call_js and "wakeCallPolling" in call_js)
    require(checks, "frontend call failures are structured", "structuredFailure" in call_js and "correlation_id" in call_js and "View Diagnostics" in call_js)
    require(checks, "frontend quality reporter exists", "QUALITY_MS" in call_js and "submitQualityReport" in call_js)
    require(checks, "Phase 3 report exists", "PulseSoc Calls Phase 3" in report and "two-user" in report)

    passed = sum(1 for check in checks if check["passed"])
    failed = [check for check in checks if not check["passed"]]
    print(json.dumps({"ok": not failed, "passed": passed, "failed": failed, "total": len(checks)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
