#!/usr/bin/env python3
"""Static guard for PulseSoc native dashboard legacy alias routing."""

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
    dashboard_modules = read("mobile-native/src/data/dashboardModules.ts")
    routing = read("mobile-native/src/navigation/dashboardRouting.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    types = read("mobile-native/src/navigation/types.ts")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    legacy_screen = read("mobile-native/src/screens/DashboardLegacyModuleScreen.tsx")
    report = read("reports/pulsesoc_native_dashboard_legacy_aliases.md")
    visible_report = read("reports/pulsesoc_native_visible_dashboard_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    expected_groups = {
        "account": "account-command-center",
        "network": "pulse-network",
        "creator": "creator-studio",
        "intelligence": "intelligence",
        "economy": "economy-earnings",
        "media": "pulse-radio-media",
        "crypto": "crypto-command-center",
        "safety": "moderation-safety",
        "ads": "ads-sponsorships",
        "ai": "pulsesoc-ai",
        "system": "system-status",
    }
    for legacy_group, native_group in expected_groups.items():
        require(f'{legacy_group}: "{native_group}"' in routing, f"Missing legacy group mapping for {legacy_group}")

    representative_routes = [
        "/dashboard/account/security",
        "/dashboard/network/community-intelligence",
        "/dashboard/creator/content-planner",
        "/dashboard/intelligence/ai-advisor",
        "/dashboard/economy/earnings",
        "/dashboard/media/pulse-radio",
        "/dashboard/crypto/alerts/create",
        "/dashboard/safety/reports-submitted",
        "/dashboard/ads/campaign-builder",
        "/dashboard/ai/assistant",
        "/dashboard/system/feed",
    ]
    for route in representative_routes:
        require(route in report, f"Report does not document representative route: {route}")

    required_tokens = [
        "DASHBOARD_LEGACY_GROUPS",
        "dashboardModuleParamsForRoute",
        "findDashboardModuleByRoute",
        "legacyModuleAliases",
        "normalizeDashboardPath",
    ]
    for token in required_tokens:
        require(token in routing, f"Routing helper missing {token}")

    require("DashboardLegacyModule" in types, "Root stack type missing DashboardLegacyModule")
    require("dashboard/:legacyGroup/:legacyModule/:legacySubmodule?" in linking, "Linking config missing legacy dashboard path")
    conflicting_link_paths = [
        '"dashboard/account/settings"',
        '"dashboard/account/security"',
        '"dashboard/account/health"',
        '"dashboard/account/verification"',
        '"dashboard/network/:section?"',
        '"dashboard/creator/content-planner"',
        '"dashboard/creator/post-scheduler"',
        '"dashboard/creator/draft-studio"',
        '"dashboard/intelligence/:subsystem?"',
        '"dashboard/crypto/alerts"',
    ]
    for token in conflicting_link_paths:
        require(token not in linking, f"Conflicting legacy dashboard deep-link entry still present: {token}")
    require("DashboardLegacyModuleScreen" in navigator, "Navigator does not register DashboardLegacyModuleScreen")
    require("dashboardModuleParamsForRoute(legacyPath)" in legacy_screen, "Legacy screen does not resolve through shared module registry")
    require('navigation.replace("DashboardModuleDetail"' in legacy_screen, "Legacy screen does not open DashboardModuleDetail shell")
    require("dashboardModuleParamsForRoute(normalized)" in notification_routing, "Notification routing does not use dashboard module resolver")
    require('navigationRef.navigate("DashboardModuleDetail"' in notification_routing, "Notification routing does not open dashboard module shell")

    dashboard_route_count = len(re.findall(r'route: "/dashboard/', dashboard_modules))
    module_key_count = len(re.findall(r'key: "[a-z0-9_]+"', dashboard_modules))
    require(dashboard_route_count >= 100, f"Expected at least 100 production dashboard routes, found {dashboard_route_count}")
    require(module_key_count >= 135, f"Expected at least 135 dashboard modules, found {module_key_count}")

    for document_name, document in {
        "legacy alias report": report,
        "visible QA report": visible_report,
        "native progress report": progress,
    }.items():
        require("Legacy Dashboard Route Alias Mapping" in document, f"{document_name} missing legacy alias section")

    print("PulseSoc native dashboard legacy alias audit passed.")
    print(f"Dashboard route entries represented: {dashboard_route_count}")
    print(f"Dashboard module entries represented: {module_key_count}")
    print(f"Legacy dashboard group prefixes covered: {len(expected_groups)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
