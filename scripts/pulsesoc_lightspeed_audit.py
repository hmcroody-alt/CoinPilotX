#!/usr/bin/env python3
"""Structural release gate for the PulseSoc Lightspeed operation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


REQUIRED_FILES = (
    "scripts/pulsesoc_route_latency_audit.py",
    "scripts/pulsesoc_static_asset_audit.py",
    "scripts/pulsesoc_database_speed_audit.py",
    "scripts/pulsesoc_worker_queue_audit.py",
    "reports/pulsesoc_lightspeed_inventory.md",
    "reports/pulsesoc_lightspeed_bottlenecks.md",
    "reports/pulsesoc_lightspeed_fixes.md",
    "reports/pulsesoc_lightspeed_operation.md",
)


def emit(status: str, item: str, detail: str) -> None:
    print(f"{status}\t{item}\t{detail}")


def main() -> int:
    failures = 0
    for relative in REQUIRED_FILES:
        path = ROOT / relative
        if path.exists() and path.stat().st_size:
            emit("PASS", relative, f"{path.stat().st_size} bytes")
        else:
            failures += 1
            emit("FAIL", relative, "missing or empty")

    checks = (
        (
            "dashboard summary loading",
            ROOT / "bot.py",
            ("include_details=False", 'request.args.get("detail") == "1"'),
        ),
        (
            "dashboard schema-query collapse",
            ROOT / "services/pulse_dashboard_mission_control.py",
            ("def _table_names", "tables=tables", 'detail_mode'),
        ),
        (
            "dashboard render containment",
            ROOT / "templates/dashboard.html",
            ("content-visibility: auto", "contain-intrinsic-size"),
        ),
        (
            "Messenger adaptive fallback polling",
            ROOT / "static/js/pulse_messages_v2.js",
            ("realtimePollDelay", "state.realtimeConnected ? 15000 : 3000"),
        ),
        (
            "call adaptive fallback polling",
            ROOT / "static/pulsesoc_calls.js",
            ("POLL_CONNECTED_MS", "activePollDelay", "scheduleActivePoll"),
        ),
        (
            "Messenger deferred media runtime",
            ROOT / "templates/pulse_messages_v2.html",
            ("defer", "pulse_media_renderer.js"),
        ),
    )
    for label, path, markers in checks:
        text = path.read_text(errors="ignore") if path.exists() else ""
        missing = [marker for marker in markers if marker not in text]
        if missing:
            failures += 1
            emit("FAIL", label, f"missing: {', '.join(missing)}")
        else:
            emit("PASS", label, "verified")

    print(f"SUMMARY\tfailures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
