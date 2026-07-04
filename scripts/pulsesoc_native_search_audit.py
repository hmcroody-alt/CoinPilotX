#!/usr/bin/env python3
"""Static audit for the PulseSoc native Search + Discovery foundation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    report = read("reports/pulsesoc_native_search_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/search.ts")
    screen = read("mobile-native/src/screens/SearchScreen.tsx")
    nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    notifications = read("mobile-native/src/navigation/notificationRouting.ts")
    backend = read("bot.py")
    web_bridge = read("static/js/pulse_search_bridge.js")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Search does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"search report must document reuse/safety/device truth: {phrase}")

    for token in (
        "/api/pulse/search",
        "PulseSearchResult",
        "PulseSearchResults",
        "SEARCH_GROUPS",
        "searchPulse",
        "loadCachedPulseSearch",
        "cachePulseSearch",
        "loadRecentSearches",
        "saveRecentSearch",
        "defaultTrendingSearches",
        "normalizeSearchResponse",
    ):
        require(token in api, f"search API wrapper missing: {token}")

    for token in (
        "SearchScreen",
        "DISCOVERY_TABS",
        "debounceRef",
        "TextInput",
        "FlatList",
        "RefreshControl",
        "loadCachedPulseSearch",
        "loadRecentSearches",
        "routeNotificationTarget",
        "Recent searches",
        "Suggested searches",
        "Events",
        "Trending",
        "Hashtags",
        "Discovery tab is not native yet",
    ):
        require(token in screen, f"Search screen behavior missing: {token}")

    require("SearchScreen" in nav, "navigation missing SearchScreen component")
    require("Search" in nav, "navigation missing Search route")
    require("Search" in types, "navigation types missing Search route")

    for token in ("pulse/search", "Search"):
        require(token in linking, f"linking missing Search route: {token}")
        require(token in notifications, f"notification routing missing Search target: {token}")

    for group in ("posts", "creators", "videos", "reels", "statuses", "marketplace", "music", "groups", "rooms", "comments"):
        require(group in backend, f"backend search route missing expected group: {group}")
        require(group in web_bridge, f"web search bridge missing expected group: {group}")
        require(group in api, f"native search wrapper missing expected group: {group}")

    for phrase in (
        "Search + Discovery Foundation",
        "Native Saved Content + Collections Foundation",
        "Why This Comes Next",
        "Risk: Medium",
        "Complexity: Medium",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Search and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("react-native-webview" not in mobile_native.lower(), "native Search must not introduce WebView")

    print("PulseSoc native Search + Discovery audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
