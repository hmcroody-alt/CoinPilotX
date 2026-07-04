#!/usr/bin/env python3
"""Static audit for the PulseSoc native Reels Player + Reel Detail foundation."""

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
    report = read("reports/pulsesoc_native_reels_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/reels.ts")
    screen = read("mobile-native/src/screens/ReelsScreen.tsx")
    card = read("mobile-native/src/components/ReelPlayerCard.tsx")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Reels does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"Reels report must document reuse/safety/device truth: {phrase}")

    for route in (
        "/api/pulse/reels/feed",
        "/api/pulse/reels/${reelId}/view",
        "/api/pulse/reels/${reelId}/react",
        "/api/pulse/reels/${reelId}/comments",
        "/api/pulse/reels/${reelId}/save",
        "/api/pulse/reels/${reelId}/repost",
        "/api/pulse/reels/${reelId}/share",
        "/api/pulse/reels/${reelId}/not-interested",
        "/api/pulse/reels/${reelId}/follow-creator",
        "/api/pulse/report",
    ):
        require(route in api, f"Reels API must reuse backend route: {route}")

    for token in (
        "listReels",
        "loadCachedReels",
        "cacheReels",
        "getReelDetail",
        "loadCachedReelDetail",
        "listReelComments",
        "addReelComment",
        "reactToReel",
        "saveReel",
        "repostReel",
        "shareReel",
        "markReelNotInterested",
        "followReelCreator",
        "reportReel",
        "trackReelView",
        "reelVideoUrl",
        "reelPosterUrl",
        "reelIsPlayable",
    ):
        require(token in api, f"Reels API helper missing: {token}")

    for token in (
        "FlatList",
        "pagingEnabled",
        "snapToInterval",
        "RefreshControl",
        "onEndReached",
        "loadCachedReels",
        "trackReelView",
        "CommentsModal",
        "addReelComment",
        "markReelNotInterested",
        "followReelCreator",
        "reportReel",
        "ProfileDetail",
    ):
        require(token in screen, f"native Reels screen behavior missing: {token}")

    for token in (
        "Video",
        "ResizeMode.COVER",
        "shouldPlay",
        "isMuted",
        "onPlaybackStatusUpdate",
        "onLongPress",
        "onToggleMuted",
        "onOpenComments",
        "onNotInterested",
        "onReport",
        "reelIsPlayable",
        "reelVideoUrl",
        "reelPosterUrl",
    ):
        require(token in card, f"native Reel player behavior missing: {token}")

    require("ReelsScreen" in navigator and 'name="Reels"' in navigator and 'name="ReelDetail"' in navigator, "navigator must register Reels")
    require("Reels: { reelId?: number" in types and "ReelDetail: { reelId: number" in types, "navigation types must include Reels params")
    require("pulse/reels" in linking and "pulse/reels/:reelId" in linking, "linking must include PulseSoc Reels routes")
    require('"ReelDetail"' in routing and "pulse\\/reels" in routing, "notification routing must open native Reel Detail")

    for phrase in (
        "Reels Player + Reel Detail",
        "Native Status Viewer + Status Detail",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Reels and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native Reels must not introduce WebView")

    print("PulseSoc native Reels audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
