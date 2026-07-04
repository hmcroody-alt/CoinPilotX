#!/usr/bin/env python3
"""Static audit for the PulseSoc native Home Feed + Post Detail foundation."""

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
    report = read("reports/pulsesoc_native_feed_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/feed.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    detail = read("mobile-native/src/screens/PostDetailScreen.tsx")
    card = read("mobile-native/src/components/PostCard.tsx")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "No native-only feed ranking",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"feed report must document reuse/safety/device truth: {phrase}")

    for route in (
        "/api/pulse/feed",
        "/api/pulse/posts/${postId}",
        "/api/pulse/posts/${postId}/react",
        "/api/pulse/posts/${postId}/save",
        "/api/pulse/posts/${postId}/repost",
        "/api/pulse/posts/${postId}/comments",
    ):
        require(route in api, f"feed API must reuse backend route: {route}")

    for token in (
        "listFeed",
        "loadCachedFeed",
        "cacheFeed",
        "getPostDetail",
        "loadCachedPostDetail",
        "cachePostDetail",
        "reactToPost",
        "savePost",
        "repostPost",
        "addPostComment",
        "listPostComments",
        "mediaDisplayUrl",
        "mediaKind",
    ):
        require(token in api, f"feed API helper missing: {token}")

    for token in (
        "FlatList",
        "RefreshControl",
        "listFeed",
        "loadCachedFeed",
        "onEndReached",
        "PostCard",
        "reactToPost",
        "savePost",
        "repostPost",
        "Share.share",
        "PostDetail",
    ):
        require(token in home, f"native Home Feed behavior missing: {token}")

    for token in (
        "getPostDetail",
        "loadCachedPostDetail",
        "addPostComment",
        "TextInput",
        "RefreshControl",
        "PostCard",
        "Comments",
        "reactToPost",
        "savePost",
        "repostPost",
    ):
        require(token in detail, f"native Post Detail behavior missing: {token}")

    for token in (
        "author",
        "mediaDisplayUrl",
        "mediaKind",
        "Image",
        "Open in PulseSoc",
        "reaction_counts",
        "viewer_reaction",
        "preview_comments",
        "pulsePostUrl",
    ):
        require(token in card, f"post card rendering/action behavior missing: {token}")

    require("PostDetailScreen" in navigator and "PostDetail" in navigator, "navigator must register PostDetail")
    require("PostDetail: { postId: number" in types, "navigation types must include PostDetail params")
    require("pulse/post/:postId" in linking, "linking must include PulseSoc post route")
    require('"PostDetail"' in routing and "pulse\\/post" in routing, "notification routing must open native PostDetail")

    for phrase in (
        "Home Feed + Post Detail",
        "Native Profile Detail + Profile Edit",
        "Why This Comes Next",
        "Risk: Medium",
        "Complexity: Medium",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed feed and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native Feed must not introduce WebView")

    print("PulseSoc native Feed/Post audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
