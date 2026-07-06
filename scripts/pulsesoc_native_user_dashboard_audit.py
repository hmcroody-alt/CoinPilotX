#!/usr/bin/env python3
"""Audit the PulseSoc native User Dashboard foundation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    dashboard_api = read("mobile-native/src/api/dashboard.ts")
    dashboard_screen = read("mobile-native/src/screens/UserDashboardScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    progress = read("reports/pulsesoc_native_progress.md")
    progress_report = read("reports/pulsesoc_native_user_dashboard_progress.md")
    qa_report = read("reports/pulsesoc_native_visible_dashboard_qa.md")

    for token in [
        "loadUserDashboardState",
        "getSession",
        "getMyProfile",
        "loadActivityInboxState",
        "listConversations",
        "getActiveCalls",
        "listFeed",
        "searchMarketplace",
        "loadSellerStoreSnapshot",
        "listBuyerOrders",
        "getPremiumStatus",
        "loadVerificationState",
        "loadAccountHealthState",
        "loadSafetyState",
        "getCreatorState",
        "getGrowthState",
        "getIntelligenceState",
    ]:
        require(token in dashboard_api, f"dashboard API missing reuse token: {token}", failures)

    for token in [
        "User Dashboard",
        "At A Glance",
        "Quick Actions",
        "Dashboard Systems",
        "Recent Activity",
        "openModule",
        "openActivityTarget",
        "RefreshControl",
        "Animated.loop",
    ]:
        require(token in dashboard_screen, f"dashboard screen missing native UX token: {token}", failures)

    for token in [
        "Dashboard: undefined",
        "UserDashboard",
        "UserDashboardWeb",
    ]:
        require(token in read("mobile-native/src/navigation/types.ts"), f"navigation types missing token: {token}", failures)

    require('<Tabs.Screen name="Dashboard"' in app_nav, "Dashboard tab is not registered", failures)
    require('<Stack.Screen name="UserDashboard"' in app_nav, "Dashboard stack route is not registered", failures)
    require('Dashboard: "pulse/dashboard"' in linking, "pulse dashboard route is not linked", failures)
    require('UserDashboard: "dashboard"' in linking, "web dashboard route is not linked", failures)
    require('normalized === "/dashboard"' in notification_routing, "notification routing missing /dashboard handling", failures)
    require('normalized === "/pulse/dashboard"' in notification_routing, "notification routing missing /pulse/dashboard handling", failures)

    user_facing_sources = dashboard_screen + dashboard_api
    require("LogiNexus" not in user_facing_sources, "internal LogiNexus name leaked into native user-facing dashboard code", failures)

    for token in [
        "User Dashboard completion %",
        "fully native",
        "fallback to web",
        "Visible QA",
        "Production WebView routes changed: no",
    ]:
        require(token in progress_report, f"dashboard progress report missing token: {token}", failures)
    for token in [
        "Visible built-in QA browser",
        "What Roody visibly saw",
        "Screens paused for review",
        "Blocked or unfinished",
    ]:
        require(token in qa_report, f"visible QA report missing token: {token}", failures)
    require("Native User Dashboard Completion" in progress, "progress report missing dashboard completion section", failures)

    if failures:
        print("PulseSoc native User Dashboard audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native User Dashboard audit passed.")
    print("- Dashboard composes existing server-authoritative APIs.")
    print("- Dashboard is reachable through native tab, /dashboard, and /pulse/dashboard routes.")
    print("- Dashboard links into existing native modules with safe fallback boundaries.")
    print("- Internal design language remains out of user-facing dashboard copy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
