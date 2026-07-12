#!/usr/bin/env python3
"""Static audit for the PulseSoc native Groups/Communities + Rooms foundation."""

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
    report = read("reports/pulsesoc_native_groups_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/groups.ts")
    screen = read("mobile-native/src/screens/GroupsScreen.tsx")
    nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    notifications = read("mobile-native/src/navigation/notificationRouting.ts")
    backend = read("bot.py")

    for phrase in (
        "does not touch production WebView routes",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Groups does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"groups report must document reuse/safety/device truth: {phrase}")

    for token in (
        "/api/pulse/groups",
        "listGroups",
        "getGroupDetail",
        "joinGroup",
        "leaveGroup",
        "openGroupChat",
        "reportGroup",
        "listRooms",
        "joinRoom",
        "loadCachedGroups",
        "loadCachedGroupDetail",
    ):
        require(token in api, f"groups API wrapper missing: {token}")

    for token in (
        "GroupsScreen",
        "RoomCard",
        "GroupCard",
        "GroupDetail",
        "GroupPostCard",
        "PulseCommandSearch",
        "FlatList",
        "RefreshControl",
        "loadCachedGroups",
        "joinGroup",
        "leaveGroup",
        "openGroupChat",
        "joinRoom",
        "Community Feed",
        "Rooms",
    ):
        require(token in screen, f"Groups screen behavior missing: {token}")

    require("GroupsScreen" in nav, "navigation missing GroupsScreen component")
    for token in ("Groups", "GroupDetail"):
        require(token in nav, f"navigation missing group route: {token}")
        require(token in types, f"navigation types missing group route: {token}")

    for token in ("pulse/groups", "Groups", "GroupDetail"):
        require(token in linking, f"linking missing group route: {token}")
        require(token in notifications, f"notification routing missing group target: {token}")

    for token in (
        '@webhook_app.route("/api/pulse/groups", methods=["GET"])',
        '@webhook_app.route("/api/pulse/groups/<group_slug>", methods=["GET"])',
        "pulse_native_group_payload",
        "pulse_native_group_post_payload",
        "pulse_ensure_default_rooms",
        "pulse_group_members",
        "pulse_group_posts",
        '@webhook_app.route("/api/pulse/groups/<group_slug>/join"',
        '@webhook_app.route("/api/pulse/groups/<group_slug>/leave"',
        '@webhook_app.route("/api/pulse/groups/<group_slug>/chat/open"',
    ):
        require(token in backend, f"backend group contract missing expected token: {token}")

    for phrase in (
        "Groups/Communities + Rooms Foundation",
        "Architecture Health Report + Shared Core Consolidation",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Groups and next recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("react-native-webview" not in mobile_native.lower(), "native Groups must not introduce WebView")

    print("PulseSoc native Groups/Communities + Rooms audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
