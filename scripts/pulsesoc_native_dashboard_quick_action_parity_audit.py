#!/usr/bin/env python3
"""Audit PulseSoc native dashboard quick-action route parity.

This is a static foundation guard. It verifies that dashboard quick actions are
not dead links and that action routing is classified as native, native shell, or
safe fallback before QA clicks through representative routes.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "mobile-native" / "src" / "data" / "dashboardModules.ts"
ROUTING = ROOT / "mobile-native" / "src" / "navigation" / "dashboardRouting.ts"
LINKING = ROOT / "mobile-native" / "src" / "navigation" / "linking.ts"
APP_NAVIGATOR = ROOT / "mobile-native" / "src" / "navigation" / "AppNavigator.tsx"
USER_DASHBOARD = ROOT / "mobile-native" / "src" / "screens" / "UserDashboardScreen.tsx"
DETAIL_SCREEN = ROOT / "mobile-native" / "src" / "screens" / "DashboardModuleDetailScreen.tsx"
ACTION_ALIAS = ROOT / "mobile-native" / "src" / "screens" / "DashboardActionAliasScreen.tsx"
REPORT = ROOT / "reports" / "pulsesoc_native_dashboard_quick_action_parity.md"
VISIBLE_QA = ROOT / "reports" / "pulsesoc_native_visible_dashboard_qa.md"
PROGRESS = ROOT / "reports" / "pulsesoc_native_progress.md"


LEGACY_GROUPS = {
    "account",
    "network",
    "creator",
    "intelligence",
    "economy",
    "media",
    "crypto",
    "safety",
    "ads",
    "ai",
    "system",
}

NATIVE_PREFIXES = (
    "/pulse/camera",
    "/pulse/activity",
    "/pulse/inbox",
    "/pulse/messages",
    "/pulse/reels",
    "/pulse/status",
    "/pulse/live",
    "/pulse/marketplace",
    "/pulse/seller-store",
    "/pulse/merchant",
    "/pulse/orders",
    "/pulse/purchases",
    "/pulse/notifications",
    "/pulse/search",
    "/pulse/saved",
    "/pulse/groups",
    "/pulse/events",
    "/pulse/premium",
    "/pulse/creator-studio",
    "/pulse/content-planner",
    "/pulse/dashboard/content-planner",
    "/pulse/dashboard/post-scheduler",
    "/pulse/dashboard/draft-studio",
    "/pulse/growth",
    "/pulse/intelligence",
    "/pulse/alerts",
    "/pulse/settings",
    "/pulse/account-health",
    "/pulse/safety",
    "/pulse/blocks",
    "/pulse/mutes",
    "/pulse/reports",
    "/pulse/verification",
    "/pulse/courses",
    "/pulse/teachers",
    "/pulse/teacher-dashboard",
    "/pulse/ai",
    "/education/lesson",
    "/support",
    "/scam-shield",
    "/trust-center",
    "/security",
    "/dashboard/orders",
    "/dashboard/activity",
    "/dashboard/inbox",
)

SAFE_FALLBACK_PREFIXES = (
    "/pulse/live/studio",
    "/pulse/videos",
    "/pulse/music",
    "/dashboard/home",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize(route: str) -> str:
    path = route.split("#", 1)[0].split("?", 1)[0].strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") if len(path) > 1 else path


def starts_with_any(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def classify(route: str, module_routes: set[str]) -> str:
    raw = route.strip()
    path = normalize(raw)
    if not raw or raw.lower().startswith(("javascript:", "data:", "file:")) or path.startswith(("/api/", "/admin/", "/static/")):
        return "missing_invalid_route"
    if path in module_routes:
        return "native_shell_route"
    if path.startswith("/dashboard/"):
        group = path.split("/")[2] if len(path.split("/")) > 2 else ""
        if group in LEGACY_GROUPS:
            return "native_shell_route"
    if starts_with_any(path, SAFE_FALLBACK_PREFIXES):
        return "safe_web_fallback"
    if path in {"/dashboard", "/pulse/dashboard", "/pulse", "/pulse/compose"} or starts_with_any(path, NATIVE_PREFIXES):
        return "native_route"
    return "missing_invalid_route"


def main() -> int:
    modules = read(MODULES)
    routing = read(ROUTING)
    linking = read(LINKING)
    app_navigator = read(APP_NAVIGATOR)
    user_dashboard = read(USER_DASHBOARD)
    detail_screen = read(DETAIL_SCREEN)
    action_alias = read(ACTION_ALIAS)
    report = read(REPORT)
    visible = read(VISIBLE_QA)
    progress = read(PROGRESS)

    module_section = modules.split("export const dashboardQuickActions", 1)[0]
    module_routes = {normalize(route) for route in re.findall(r'route:\s*"([^"]+)"', module_section)}
    quick_actions = re.findall(r'\{\s*label:\s*"([^"]+)",\s*route:\s*"([^"]+)",\s*capability:\s*"([^"]+)"\s*\}', modules)
    assert quick_actions, "No dashboard quick actions found."

    labels = [label for label, _, _ in quick_actions]
    routes = [route for _, route, _ in quick_actions]
    assert len(labels) == len(set(labels)), f"Duplicate quick action labels: {labels}"
    assert len(routes) == len(set(routes)), f"Duplicate quick action routes: {routes}"

    classes = {label: classify(route, module_routes) for label, route, _ in quick_actions}
    invalid = {label: cls for label, cls in classes.items() if cls == "missing_invalid_route"}
    assert not invalid, f"Invalid dashboard quick action routes: {invalid}"

    assert classes.get("Create Post") == "native_route", "Create Post must open native composer route."
    assert classes.get("Upload Video") == "native_route", "Upload Video must open native Camera Studio."
    assert classes.get("Add Status") == "native_route", "Add Status must open native Status creator path."
    assert classes.get("Invite Friends") == "native_shell_route", "Invite Friends must map to Network/Friends shell."
    assert classes.get("Go Live") == "safe_web_fallback", "Go Live must remain a safe Live Studio fallback."

    required_routing_tokens = [
        "classifyDashboardActionRoute",
        "native_shell_route",
        "safe_web_fallback",
        'path === "/pulse/compose"',
        'path.startsWith("/pulse/camera")',
        'path.startsWith("/pulse/live/studio")',
    ]
    for token in required_routing_tokens:
        assert token in routing, f"Missing routing token: {token}"

    assert "dashboardModuleParamsForRoute(action.route)" in user_dashboard, "Quick actions must open native module shells when applicable."
    assert "classifyDashboardActionRoute(action.route)" in user_dashboard, "Quick action UI must display route classification."
    assert "classifyDashboardActionRoute(module.route)" in detail_screen, "Module detail shell must display route classification."
    assert "DashboardComposeAlias" in app_navigator and "DashboardMusicAlias" in app_navigator, "Quick-action direct URL aliases must be registered."
    assert 'DashboardComposeAlias: "pulse/compose"' in linking, "Direct /pulse/compose alias must be linked."
    assert 'Music:' in linking and 'path: "pulse/music"' in linking, "Direct /pulse/music must open the native Music screen."
    assert '"/pulse/compose"' in action_alias and 'navigation.replace("Music"' in action_alias, "Alias screen must delegate compose and redirect legacy music native."

    for token in [
        "Dashboard Quick Action Parity Hardening",
        "Native route",
        "Native shell",
        "Safe web fallback",
        "Dead/stale routes eliminated",
        "Authenticated visible sweep",
    ]:
        assert token in report, f"Missing quick-action report token: {token}"
    assert "Dashboard Quick Action Parity Hardening" in progress, "Progress report missing quick-action section."
    assert "Authenticated visible sweep" in visible, "Visible QA report missing authenticated quick-action sweep."

    counts = {kind: list(classes.values()).count(kind) for kind in sorted(set(classes.values()))}
    print("PulseSoc native dashboard quick-action parity audit passed.")
    print(f"Quick actions audited: {len(quick_actions)}")
    print(f"Route classes: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
