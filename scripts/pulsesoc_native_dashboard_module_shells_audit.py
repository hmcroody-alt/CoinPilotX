#!/usr/bin/env python3
"""Audit PulseSoc native User Dashboard module detail shell wiring."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def ok(message: str) -> None:
    print(f"[OK] {message}")


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)
    ok(message)


def main() -> int:
    data = read("mobile-native/src/data/dashboardModules.ts")
    detail = read("mobile-native/src/screens/DashboardModuleDetailScreen.tsx")
    dashboard = read("mobile-native/src/screens/UserDashboardScreen.tsx")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/dashboardRouting.ts")
    qa_auth = read("mobile-native/src/session/qaSimulatorAuth.ts")
    bot = read("bot.py")
    progress = read("reports/pulsesoc_native_progress.md")
    visible_qa = read("reports/pulsesoc_native_visible_dashboard_qa.md")
    shell_report = read("reports/pulsesoc_native_dashboard_module_shells.md")

    required_groups = [
        "economy-earnings",
        "creator-studio",
        "intelligence",
        "pulse-radio-media",
        "crypto-command-center",
        "ads-sponsorships",
        "moderation-safety",
        "system-status",
    ]
    for group in required_groups:
        require(group in data, f"{group} exists in production dashboard catalog")
        require(group in detail, f"{group} has native shell related-route coverage")

    require('navigation.navigate("DashboardModuleDetail"' in dashboard, "dashboard cards open native module detail shells")
    require("DashboardModuleDetailScreen" in navigator, "module detail shell registered in stack navigator")
    require("DashboardModuleDetail:" in types, "module detail route typed in RootStackParamList")
    require("pulse/dashboard/module/:groupKey/:moduleKey" in linking, "module detail route deep link registered")
    require("openDashboardRoute" in routing and "openDashboardWebFallback" in routing, "shared dashboard routing helper exposes native and fallback actions")
    require("runtimeWebCredentials" in qa_auth and "sessionStorage" in qa_auth, "QA-only web auth bootstrap can avoid credentials in visible URLs")
    require("simulator-login" not in bot, "production backend still does not expose simulator-login QA auth")

    module_count = len(re.findall(r"key: \"[^\"]+\", title:", data))
    require(module_count >= 130, f"{module_count} dashboard modules remain represented")

    require("reports/pulsesoc_native_dashboard_module_shells.md" in progress or "Dashboard Module Detail Shell" in progress, "progress report references dashboard module shells")
    require("native-dashboard-module-shells-2026-07-06" in visible_qa, "visible QA report references module shell screenshot evidence")
    require("Visible QA shell coverage" in shell_report, "module shell report records visible QA shell coverage")
    screenshots = ROOT / "reports" / "screenshots" / "native-dashboard-module-shells-2026-07-06"
    for name in (
        "creator-tools.png",
        "intelligence-alerts.png",
        "media-pulse-radio.png",
        "crypto-create-alert.png",
        "ads-campaign-builder.png",
        "safety-reports-submitted.png",
        "economy-earnings-direct.png",
        "system-feed-intelligence-direct.png",
    ):
        require((screenshots / name).exists(), f"visible QA screenshot exists: {name}")
    require("LogiNexus" not in detail and "LogiNexus" not in dashboard, "internal design label is not exposed in native user-facing dashboard source")

    print("PulseSoc native dashboard module shell audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
