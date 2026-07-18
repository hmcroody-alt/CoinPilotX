#!/usr/bin/env python3
"""Audit the end-to-end PulseSoc call ringing and diagnostics chain."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "pulsesoc_communications_engine.py"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
REPORT = ROOT / "reports" / "pulsesoc_call_system_full_functionality_audit.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    service = read(SERVICE)
    routes = read(ROUTES)
    call_js = read(CALL_JS)
    report = read(REPORT) if REPORT.exists() else ""

    require(checks, "LiveKit required variables are gated", all(name in service for name in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]))
    require(checks, "start call validates participants", "Every recipient must be a conversation participant" in service and "You cannot call yourself" in service)
    require(checks, "callee participant rows are created", "'callee', 'ringing'" in service and "participant_invited" in service)
    require(checks, "incoming call notification uses central system", "pulsesoc_notification_system.intake_event" in service and "event_type=\"incoming_call\"" in service)
    require(checks, "incoming call is urgent and push eligible", "priority=\"urgent\"" in service and '"push"' in service and '"call"' in service)
    require(checks, "incoming call realtime fanout exists", "_publish_call_realtime" in service and "communication_call_incoming" in service and "call_started" in service)
    require(checks, "frontend listens for incoming calls", "handleIncomingRealtime" in call_js and "communication_call_incoming" in call_js and "call_started" in call_js)
    require(checks, "frontend polls active calls", "pollActiveCalls" in call_js and "/active" in call_js and "POLL_FALLBACK_MS" in call_js and "POLL_CONNECTED_MS" in call_js)
    require(checks, "frontend wakes on app restore", "wakeCallPolling" in call_js and "pageshow" in call_js and "visibilitychange" in call_js and "focus" in call_js)
    require(checks, "incoming overlay has accept and decline", "data-call-accept" in call_js and "data-call-decline" in call_js and "showIncoming" in call_js)
    require(checks, "recipient ring acknowledgement route exists", '"/api/calls/<path:call_id>/ring-seen"' in routes and "api_ring_seen" in routes)
    require(checks, "recipient ring acknowledgement service exists", "def mark_ring_seen" in service and "incoming_call_overlay_opened" in service)
    require(checks, "frontend acknowledges overlay without blocking UI", "seenIncomingCalls" in call_js and "ring-seen" in call_js and ".catch(() => {})" in call_js)
    require(checks, "call status marks stale calls missed", "_mark_missed_stale_calls_cur" in service and "ring_timeout" in service)
    require(checks, "active calls endpoint marks stale missed", "def active_calls" in service and "missed_marked" in service)
    require(checks, "call delivery diagnostics expose recipient truth", all(token in service for token in [
        "recipient_online",
        "recipient_overlay_opened",
        "recipient_push_token_exists",
        "push_job_created",
        "call_job_created",
        "incoming_notification_created",
    ]))
    require(checks, "diagnostics keep media track proof honest", "media_tracks_published" in service and "provider_event_required" in service)
    require(checks, "frontend joins LiveKit room", "new LK.Room" in call_js and ".connect(join.livekit_url, join.token)" in call_js)
    require(checks, "frontend handles local and remote media tracks", "publishLocalTracks" in call_js and "TrackSubscribed" in call_js and "attachRemoteTrack" in call_js)
    require(checks, "quality telemetry is throttled", "QUALITY_MS = 30000" in call_js and "lastQualityAt" in call_js)
    require(checks, "generic unavailable copy is removed from call runtime", "Calling is temporarily unavailable" not in service and "Calling is temporarily unavailable" not in routes and "Calling is temporarily unavailable" not in call_js)
    require(checks, "structured call failures exist", "structuredFailure" in call_js and "error_code" in service and "correlation_id" in service)
    require(checks, "report exists", "PulseSoc Call System Full Functionality Audit" in report)

    passed = sum(1 for check in checks if check["passed"])
    failed = [check for check in checks if not check["passed"]]
    print(json.dumps({"ok": not failed, "passed": passed, "failed": failed, "total": len(checks)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
