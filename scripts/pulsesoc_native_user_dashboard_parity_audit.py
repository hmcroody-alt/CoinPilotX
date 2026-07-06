#!/usr/bin/env python3
"""Audit native User Dashboard parity against the production dashboard module map."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "mobile-native" / "src" / "data" / "dashboardModules.ts"
SCREEN = ROOT / "mobile-native" / "src" / "screens" / "UserDashboardScreen.tsx"
API = ROOT / "mobile-native" / "src" / "api" / "dashboard.ts"
PROGRESS = ROOT / "reports" / "pulsesoc_native_progress.md"
PARITY_REPORT = ROOT / "reports" / "pulsesoc_native_user_dashboard_parity.md"
VISIBLE_QA = ROOT / "reports" / "pulsesoc_native_visible_dashboard_qa.md"

REQUIRED_GROUPS = [
    "Account Command Center",
    "Pulse Network",
    "Creator Studio",
    "Intelligence",
    "Economy & Earnings",
    "Pulse Radio & Media",
    "Crypto Command Center",
    "Moderation / Safety",
    "Ads & Sponsorships",
    "PulseSoc AI",
    "System Status",
]

REQUIRED_QUICK_ACTIONS = [
    "Create Post",
    "Go Live",
    "Upload Video",
    "Add Status",
    "Invite Friends",
    "Create Crypto Alert",
    "Ask Crypto AI",
    "Scan Token",
    "Add Watchlist Asset",
    "Open Scam Shield",
    "Open Pulse Radio",
]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"[OK] {message}")


def read(path: Path) -> str:
    if not path.exists():
        fail(f"missing {path.relative_to(ROOT)}")
    return path.read_text()


def main() -> int:
    modules = read(MODULES)
    screen = read(SCREEN)
    api = read(API)
    progress = read(PROGRESS)
    parity = read(PARITY_REPORT)
    visible = read(VISIBLE_QA)

    missing_groups = [group for group in REQUIRED_GROUPS if f'title: "{group}"' not in modules]
    if missing_groups:
        fail(f"missing dashboard module groups: {', '.join(missing_groups)}")
    ok(f"{len(REQUIRED_GROUPS)} production dashboard groups represented")

    module_count = len(re.findall(r'\bkey: "[a-z0-9_]+", title:', modules))
    if module_count < 130:
        fail(f"expected at least 130 dashboard modules, found {module_count}")
    ok(f"{module_count} production dashboard modules represented")

    missing_actions = [action for action in REQUIRED_QUICK_ACTIONS if f'label: "{action}"' not in modules]
    if missing_actions:
        fail(f"missing dashboard quick actions: {', '.join(missing_actions)}")
    ok(f"{len(REQUIRED_QUICK_ACTIONS)} production quick actions represented")

    for needle in ["moduleGroups", "dashboardQuickActionLinks"]:
        if needle not in api:
            fail(f"dashboard API state missing {needle}")
    ok("dashboard API exposes module groups and quick actions")

    for needle in ["Production Dashboard Map", "ModuleGroupSection", "openDashboardRoute", "Fallback"]:
        if needle not in screen:
            fail(f"dashboard screen missing {needle}")
    ok("dashboard screen renders parity sections and fallback routing")

    for forbidden in ["LogiNexus"]:
        if forbidden in modules or forbidden in screen:
            fail(f"user-facing dashboard source exposes internal term {forbidden}")
    ok("user-facing dashboard source keeps internal design language out of UI copy")

    for report, label in [(parity, "parity report"), (visible, "visible QA report"), (progress, "progress report")]:
        if "User Dashboard" not in report:
            fail(f"{label} does not mention User Dashboard")
    ok("reports updated for User Dashboard parity")

    print("PulseSoc native User Dashboard parity audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
