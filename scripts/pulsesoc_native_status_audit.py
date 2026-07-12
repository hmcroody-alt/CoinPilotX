#!/usr/bin/env python3
"""Static audit for the PulseSoc native Status Viewer + Status Detail foundation."""

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
    report = read("reports/pulsesoc_native_status_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/status.ts")
    screen = read("mobile-native/src/screens/StatusScreen.tsx")
    card = read("mobile-native/src/components/StatusViewerCard.tsx")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Status does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"Status report must document reuse/safety/device truth: {phrase}")

    for route in (
        "/api/pulse/status/rail",
        "/api/pulse/status/${statusId}/view",
        "/api/pulse/status/${statusId}/react",
        "/api/pulse/status/${statusId}/reply",
        "/api/pulse/status/${statusId}/share",
    ):
        require(route in api, f"Status API must reuse backend route: {route}")

    for token in (
        "listStatuses",
        "loadCachedStatuses",
        "cacheStatuses",
        "trackStatusView",
        "reactToStatus",
        "replyToStatus",
        "shareStatus",
        "updateStatus",
        "deleteStatus",
        "pulseStatusUrl",
        "statusMediaUrl",
        "statusPosterUrl",
        "statusMediaKind",
        "statusMusicLabel",
    ):
        require(token in api, f"Status API helper missing: {token}")

    for token in (
        "FlatList",
        "RefreshControl",
        "loadCachedStatuses",
        "StatusViewerCard",
        "trackStatusView",
        "reactToStatus",
        "replyToStatus",
        "shareStatus",
        "ReplyModal",
        "StatusManageModal",
        "ProfileDetail",
    ):
        require(token in screen, f"native Status screen behavior missing: {token}")

    for token in (
        "Video",
        "ResizeMode.COVER",
        "Image",
        "onPrevious",
        "onNext",
        "onToggleMuted",
        "onReact",
        "onReply",
        "onShare",
        "onMore",
        "setPaused",
        "onViewed",
        "statusMusicLabel",
    ):
        require(token in card, f"native Status viewer behavior missing: {token}")

    require("StatusScreen" in navigator and 'name="Status"' in navigator and 'name="StatusDetail"' in navigator, "navigator must register Status")
    require("Status: { openCreator?: boolean; statusId?: number } | undefined" in types and "StatusDetail: { statusId: number" in types, "navigation types must include Status params")
    require("pulse/status" in linking and "pulse/status/:statusId" in linking, "linking must include PulseSoc Status routes")
    require('"StatusDetail"' in routing and "pulse\\/status" in routing and "pulse://status" not in routing, "notification routing must open native Status Detail safely")
    require("mobileStatusMatch" in routing and "StatusDetail" in routing, "notification routing must support backend mobile Status deep links")

    for token in (
        "accessibilityLabel=\"Previous Status\"",
        "accessibilityLabel=\"Next Status\"",
        "accessibilityLabel=\"Status options\"",
        "onPressIn={() => setPaused(true)}",
        "setTimeout(() => { markComplete(); onNext(); }, 6000)",
    ):
        require(token in card, f"Status viewer interaction/accessibility missing: {token}")

    for token in (
        "StatusManageModal",
        "updateStatusOnServer",
        "deleteStatus(status.id)",
        "status.author_live && styles.bubbleLive",
        "status.story_count",
    ):
        require(token in screen, f"Status rail/owner lifecycle missing: {token}")

    for phrase in (
        "Status Viewer + Status Detail",
        "Focused Native Status Design and Deep Wiring",
        "Status is the active focused subsystem",
        "Next recommendation: stay on Status",
    ):
        require(phrase in progress, f"native progress report must include completed Status and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native Status must not introduce WebView")

    print("PulseSoc native Status audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
