#!/usr/bin/env python3
"""Focused contract audit for native Feed Posts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    api = read("mobile-native/src/api/feed.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    card = read("mobile-native/src/components/PostCard.tsx")
    detail = read("mobile-native/src/screens/PostDetailScreen.tsx")
    backend = read("services/pulse_feed_engine.py")

    for route in ("/api/pulse/feed", "/api/pulse/posts/${postId}/react", "/api/pulse/posts/${postId}/save", "/api/pulse/posts/${postId}/repost", "/api/pulse/posts/${postId}/comments"):
        require(route in api, f"missing canonical API reuse: {route}")
    for token in ("FlatList", "RefreshControl", "onEndReached", "mergePosts", "loadCachedFeed", "FEED_SELECTION_KEY", "registerSyncInvalidation"):
        require(token in home, f"missing feed lifecycle behavior: {token}")
    for token in ("NativeMediaViewer", "mediaGrid", "bodyExpanded", "reactionSelector", "onLongPress", "result.removed", "home-feed-report", "home-feed-hide", "home-feed-block", "home-feed-mute"):
        source = home if token == "result.removed" else card
        require(token in source, f"missing card interaction: {token}")
    require("socialAvatarDot" not in card, "fabricated reaction avatars must not be rendered")
    require("home-feed-comment-photo" not in card, "unsupported photo-comment control must not remain visible")
    require("result.removed" in detail, "Post Detail must reconcile reaction removal")
    for reaction in ("like", "love", "fire", "funny", "wow", "rocket"):
        require(f'"{reaction}"' in card and f'"{reaction}"' in backend, f"reaction not shared with production: {reaction}")
    native = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native/src").rglob("*.ts*"))
    require("react-native-webview" not in native.lower(), "native Feed must not add WebView")
    print("PulseSoc native Feed Posts complete audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
