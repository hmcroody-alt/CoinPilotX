#!/usr/bin/env python3
"""Static guard for PulseSoc native dashboard live state panels."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    live_state = read("mobile-native/src/api/dashboardLiveState.ts")
    detail_screen = read("mobile-native/src/screens/DashboardModuleDetailScreen.tsx")
    modules = read("mobile-native/src/data/dashboardModules.ts")
    report = read("reports/pulsesoc_native_dashboard_live_state_panels.md")
    visible_report = read("reports/pulsesoc_native_visible_dashboard_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    required_imports = [
        "loadUserDashboardState",
        "listCryptoAlerts",
        "creatorScore",
        "growthMoney",
        "premiumPlanLabel",
    ]
    for token in required_imports:
        require(token in live_state, f"Live-state adapter is not reusing expected API/helper: {token}")

    group_keys = [
        "account-command-center",
        "pulse-network",
        "creator-studio",
        "intelligence",
        "economy-earnings",
        "pulse-radio-media",
        "crypto-command-center",
        "moderation-safety",
        "ads-sponsorships",
        "pulsesoc-ai",
        "system-status",
    ]
    for group in group_keys:
        require(group in live_state, f"Live-state adapter missing group coverage: {group}")
        require(group in modules, f"Dashboard module registry missing group: {group}")

    required_panel_tokens = [
        "LiveStateSection",
        "loadDashboardModuleLiveState",
        "Live state",
        "loadedFromCache",
        "liveMetricGrid",
        "liveWarning",
        "Foundation status",
    ]
    for token in required_panel_tokens:
        require(token in detail_screen, f"Dashboard detail screen missing live-state UI token: {token}")

    require("No production WebView routes were changed" in visible_report, "Visible QA report missing WebView safety note")
    require("Dashboard Live State Panels" in report, "Live-state report missing title")
    require("Dashboard Live State Panels" in progress, "Native progress report missing live-state section")

    module_count = len(re.findall(r'key: "[a-z0-9_]+"', modules))
    require(module_count >= 135, f"Expected at least 135 represented dashboard modules, found {module_count}")

    report_groups = [
        "Account",
        "Network",
        "Creator",
        "Intelligence",
        "Economy",
        "Media",
        "Crypto",
        "Safety",
        "Ads",
        "System Status",
    ]
    for group in report_groups:
        require(group in report, f"Report does not document live-state group: {group}")

    print("PulseSoc native dashboard live state panels audit passed.")
    print(f"Dashboard modules represented: {module_count}")
    print(f"Live-state groups covered: {len(group_keys)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
