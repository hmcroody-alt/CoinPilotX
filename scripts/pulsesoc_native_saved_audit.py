#!/usr/bin/env python3
"""Static audit for the PulseSoc native Saved Content + Collections foundation."""

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
    report = read("reports/pulsesoc_native_saved_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/saved.ts")
    screen = read("mobile-native/src/screens/SavedScreen.tsx")
    nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    notifications = read("mobile-native/src/navigation/notificationRouting.ts")
    backend = read("bot.py")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Saved does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"saved report must document reuse/safety/device truth: {phrase}")

    for token in (
        "/api/pulse/saved",
        "/api/pulse/saved/collections",
        "listSavedContent",
        "addSavedItem",
        "removeSavedItem",
        "moveSavedItem",
        "createSavedCollection",
        "updateSavedCollection",
        "deleteSavedCollection",
        "loadCachedSavedLibrary",
        "normalizeSavedLibrary",
    ):
        require(token in api, f"saved API wrapper missing: {token}")

    for token in (
        "SavedScreen",
        "TYPE_FILTERS",
        "TextInput",
        "FlatList",
        "RefreshControl",
        "loadCachedSavedLibrary",
        "routeNotificationTarget",
        "handleCreateCollection",
        "handleUpdateCollection",
        "handleDeleteCollection",
        "handleRemove",
        "handleMove",
        "Posts",
        "Marketplace",
        "Learning",
    ):
        require(token in screen, f"Saved screen behavior missing: {token}")

    require("SavedScreen" in nav, "navigation missing SavedScreen component")
    require("Saved" in nav, "navigation missing Saved route")
    require("Saved" in types, "navigation types missing Saved route")

    for token in ("pulse/saved", "Saved"):
        require(token in linking, f"linking missing Saved route: {token}")
        require(token in notifications, f"notification routing missing Saved target: {token}")

    for token in (
        '@webhook_app.route("/api/pulse/saved"',
        '@webhook_app.route("/api/pulse/saved/collections"',
        '@webhook_app.route("/api/pulse/saved/<int:item_id>"',
        '@webhook_app.route("/api/pulse/saved/<int:item_id>/move"',
        "pulse_saved_items",
        "pulse_saved_collections",
        "pulse_saved_snapshot",
        "pulse_saved_items_query",
    ):
        require(token in backend, f"backend saved contract missing expected token: {token}")

    for phrase in (
        "Saved Content + Collections Foundation",
        "Native Groups, Communities + Rooms Foundation",
        "Why This Comes Next",
        "Risk: Medium",
        "Complexity: Medium",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Saved and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("react-native-webview" not in mobile_native.lower(), "native Saved must not introduce WebView")

    print("PulseSoc native Saved Content + Collections audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
