#!/usr/bin/env python3
"""Static audit for the PulseSoc Calls Backend Command Center."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
SERVICE = ROOT / "services" / "pulsesoc_communications_engine.py"
REGISTRY = ROOT / "services" / "backend_management_registry.py"
TEMPLATE = ROOT / "templates" / "admin_calls_command_center.html"
BOT = ROOT / "bot.py"
REPORT = ROOT / "reports" / "calls_backend_command_center.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    routes = read(ROUTES)
    service = read(SERVICE)
    registry = read(REGISTRY)
    template = read(TEMPLATE) if TEMPLATE.exists() else ""
    bot = read(BOT)
    report = read(REPORT) if REPORT.exists() else ""

    require(checks, "admin Calls nav link exists", "href='/admin/calls'" in bot or 'href="/admin/calls"' in bot)
    require(checks, "backend registry contains Calls", 'BackendFeature("network.calls"' in registry and '"/admin/calls"' in registry)
    require(checks, "admin calls template exists", TEMPLATE.exists() and "Calls Command Center" in template)
    require(checks, "top dashboard cards exist", all(label in template for label in ["Active Calls", "Calls Today", "Failed Calls", "Missed Calls", "Average Duration", "Average Quality", "Notification Delivery", "Last Error"]))
    require(checks, "LiveKit status card exists", "LIVEKIT_URL" in template and "LIVEKIT_API_KEY" in template and "LIVEKIT_API_SECRET" in template and "LIVEKIT_WEBHOOK_SECRET" in template)
    require(checks, "template states secrets are hidden", "Secret values are never rendered" in template and "Provider secrets and token values are hidden" in template)

    for route in [
        '"/admin/calls"',
        '"/admin/calls/recent"',
        '"/admin/calls/active"',
        '"/admin/calls/failed"',
        '"/admin/calls/missed"',
        '"/admin/calls/<path:call_id>"',
        '"/admin/calls/<path:call_id>/timeline"',
        '"/admin/calls/<path:call_id>/delivery"',
        '"/admin/calls/<path:call_id>/inspector"',
        '"/admin/calls/test-config"',
    ]:
        require(checks, f"admin route {route} exists", route in routes)

    for route in [
        '"/api/admin/calls/recent"',
        '"/api/admin/calls/active"',
        '"/api/admin/calls/failed"',
        '"/api/admin/calls/<path:call_id>"',
        '"/api/admin/calls/<path:call_id>/timeline"',
        '"/api/admin/calls/<path:call_id>/delivery"',
        '"/api/admin/calls/<path:call_id>/inspector"',
        '"/api/admin/calls/<path:call_id>/force-end"',
        '"/api/admin/calls/test-config"',
    ]:
        require(checks, f"API route {route} exists", route in routes)

    require(checks, "admin routes are protected", routes.count("_current_admin()") >= 10 and "Admin access required" in routes)
    require(checks, "force-end is admin protected", "api_admin_call_force_end" in routes and "admin_force_end_call" in routes and "admin_calls_force_end_page" in routes)
    require(checks, "dashboard summary helper exists", "def calls_dashboard_summary" in service and "notification_delivery" in service)
    require(checks, "filtered call lists helper exists", "def admin_calls_list" in service and "ACTIVE_STATUSES" in service)
    require(checks, "call inspector helper exists", "def call_inspector" in service and "call_delivery_diagnostics" in service and "call_timeline" in service)
    require(checks, "timeline helper exists", "def call_timeline" in service and "communication_call_events" in service)
    require(checks, "delivery diagnostic answers ringing chain", all(token in service for token in ["incoming_notification_created", "push_job_created", "recipient_push_token_exists", "realtime_event_emitted", "media_tracks_published"]))
    require(checks, "LiveKit config test exists", "def test_config" in service and "can_generate_token" in service and "can_create_test_room" in service)
    require(checks, "no provider secrets exposed to static frontend", "LIVEKIT_API_SECRET" not in read(ROOT / "static" / "pulsesoc_calls.js"))
    require(checks, "completion report exists", REPORT.exists() and "Calls Backend Command Center" in report)

    passed = sum(1 for check in checks if check["passed"])
    failed = [check for check in checks if not check["passed"]]
    print(json.dumps({"ok": not failed, "passed": passed, "failed": failed, "total": len(checks)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
