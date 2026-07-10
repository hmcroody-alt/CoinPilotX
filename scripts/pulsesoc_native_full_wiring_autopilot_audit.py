#!/usr/bin/env python3
"""Audit PulseSoc Native action wiring coverage.

This is a static guard for the full-wiring foundation mission. It does not
prove every backend mutation, but it ensures the main native action surfaces
are represented by explicit routes, native shells, or safe fallbacks.
"""

from __future__ import annotations

import json
import re
import sys
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


def count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.MULTILINE))


def main() -> int:
    failures: list[str] = []

    types = read("mobile-native/src/navigation/types.ts")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    dashboard_routing = read("mobile-native/src/navigation/dashboardRouting.ts")
    dashboard_modules = read("mobile-native/src/data/dashboardModules.ts")
    composer = read("mobile-native/src/components/HomePulseComposer.tsx")
    post_card = read("mobile-native/src/components/PostCard.tsx")

    require("Create: undefined" in types, "Bottom tab route type for Create is missing.", failures)
    require("function CreateTabScreen" in navigator, "Create tab redirect screen is missing.", failures)
    require('name="Create"' in navigator, "Create bottom-tab registration is missing.", failures)

    for needle in (
        "HOME_DRAWER_SECTIONS",
        "HomeTopBar",
        "HomeDrawer",
        "home-top-menu",
        "home-top-search",
        "home-top-activity",
        "home-top-profile",
    ):
        require(needle in home, f"Home wiring surface missing: {needle}", failures)

    expected_home_routes = [
        "/pulse/dashboard",
        "/pulse/search",
        "/pulse/activity",
        "/pulse/messages",
        "/pulse/compose",
        "/pulse/camera/photo?target=feed",
        "/pulse/status/create",
        "/pulse/marketplace",
        "/pulse/seller-store",
        "/pulse/orders",
        "/pulse/safety",
        "/pulse/verification",
        "/pulse/account-health",
        "/pulse/ai",
        "/pulse/alerts",
    ]
    for route in expected_home_routes:
        require(route in home, f"Home drawer/action route missing: {route}", failures)

    for needle in ("Privacy Policy", "Terms of Service", "Telegram companion setup", "openSupportWebFallback"):
        require(needle in settings, f"Settings support/legal/provider action missing: {needle}", failures)

    for needle in ("classifyDashboardActionRoute", "DashboardModuleDetail", "isKnownSafeFallbackPath"):
        require(needle in dashboard_routing, f"Dashboard route classifier missing: {needle}", failures)

    for needle in (
        "Photo",
        "Video",
        "Music",
        "Feeling",
        "Location",
        "Mention",
        "Topic",
        "Publish Signal",
    ):
        require(needle in composer, f"Home composer action missing: {needle}", failures)

    for needle in ("onReport", "onHide", "onBlock", "onMute", "onFollow", "NativeMediaViewer", "onAuthorPress"):
        require(needle in post_card, f"Feed card action missing: {needle}", failures)

    dashboard_module_count = count(r"\{\s*key:\s*\"", dashboard_modules)
    dashboard_quick_actions = count(r"\{\s*label:\s*\"", dashboard_modules)
    bottom_tabs = count(r"<Tabs\.Screen\s+name=", navigator)
    stack_screens = count(r"<Stack\.Screen\s+name=", navigator)
    home_drawer_actions = count(r"\{\s*label:\s*\"[^\"]+\",\s*route:\s*\"", home)
    settings_buttons = count(r"<Pressable\s+accessibilityRole=\"button\"", settings)
    composer_actions = count(r"<Pressable", composer)
    feed_card_actions = count(r"<Pressable", post_card)

    total_discovered = (
        dashboard_module_count
        + dashboard_quick_actions
        + bottom_tabs
        + stack_screens
        + home_drawer_actions
        + settings_buttons
        + composer_actions
        + feed_card_actions
    )

    summary = {
        "total_actions_buttons_discovered_static": total_discovered,
        "dashboard_modules": dashboard_module_count,
        "dashboard_quick_actions": dashboard_quick_actions,
        "bottom_tabs": bottom_tabs,
        "stack_screens": stack_screens,
        "home_drawer_actions": home_drawer_actions,
        "settings_buttons": settings_buttons,
        "composer_pressables": composer_actions,
        "feed_card_pressables": feed_card_actions,
        "missing_or_invalid": failures,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
