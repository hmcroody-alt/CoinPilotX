#!/usr/bin/env python3
"""Audit PulseSoc Intelligence Engine Phase 2 collectors."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED = [
    "base",
    "crypto",
    "markets",
    "world",
    "security",
    "technology",
    "pulsesoc",
    "creator",
    "music",
    "system",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> int:
    package = ROOT / "services" / "intelligence_collectors"
    for name in REQUIRED:
        path = package / f"{name}.py"
        if not path.exists():
            fail(f"missing collector module {path}")
        importlib.import_module(f"services.intelligence_collectors.{name}")

    from services import pulsesoc_intelligence_engine as engine
    from services.intelligence_collectors import COLLECTOR_CLASSES, collector_keys, run_collectors

    expected_streams = {
        "crypto_pulse",
        "market_pulse",
        "world_pulse",
        "security_pulse",
        "technology_pulse",
        "pulsesoc_discoveries",
        "pulsesoc_pulse",
        "creator_pulse",
        "music_pulse",
        "system_pulse",
    }
    missing = expected_streams - set(collector_keys())
    if missing:
        fail(f"missing collector registrations: {sorted(missing)}")
    if "system_pulse" not in engine.STREAM_KEYS:
        fail("system_pulse stream missing from central engine")
    if not callable(run_collectors):
        fail("run_collectors is not callable")

    service_text = (ROOT / "services" / "pulsesoc_intelligence_engine.py").read_text(encoding="utf-8")
    for token in ["validate_actions", "default_actions_for_signal", "PULSESOC_APP_STORE_URL", "deliver_event"]:
        if token not in service_text:
            fail(f"central engine missing {token}")

    route_text = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")
    if "run_collectors" not in route_text or "/api/admin/intelligence/collect" not in route_text:
        fail("admin collector route does not use Phase 2 runner")

    run_script = ROOT / "scripts" / "run_intelligence_collectors.py"
    if "--dry-run" not in run_script.read_text(encoding="utf-8"):
        fail("collector run script missing dry-run support")

    report = ROOT / "reports" / "pulsesoc_intelligence_collectors_phase2.md"
    if not report.exists():
        fail("Phase 2 collector report missing")

    print("PASS: PulseSoc Intelligence collector Phase 2 audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
