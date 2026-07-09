#!/usr/bin/env python3
"""Audit the PulseSoc native Home feed interaction and media handoff contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "mobile-native" / "src" / "screens" / "HomeScreen.tsx"
POST_CARD = ROOT / "mobile-native" / "src" / "components" / "PostCard.tsx"
MEDIA_VIEWER = ROOT / "mobile-native" / "src" / "components" / "NativeMediaViewer.tsx"
FEED_API = ROOT / "mobile-native" / "src" / "api" / "feed.ts"


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Missing required file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> None:
    home = read(HOME)
    post_card = read(POST_CARD)
    media_viewer = read(MEDIA_VIEWER)
    feed_api = read(FEED_API)

    home_handlers = {
        "post detail routing": 'navigation.navigate("PostDetail"',
        "profile routing": 'navigation.navigate("ProfileDetail"',
        "safety report routing": 'navigation.navigate("SafetyHub", { title: "Report"',
        "block routing": 'navigation.navigate("SafetyHub", { title: "Blocked Users"',
        "mute routing": 'navigation.navigate("SafetyHub", { title: "Muted Users"',
        "growth promote routing": 'navigation.navigate("GrowthCenter"',
        "status routing": 'navigation.navigate("StatusDetail"',
        "status creator routing": 'screen: "Status", params: { openCreator: true }',
        "live routing": 'screen: "Live"',
        "hide local state update": "setPosts((current) => current.filter",
        "event invalidation registration": 'registerSyncInvalidation("activity"',
    }
    for label, needle in home_handlers.items():
        require(home, needle, label)

    action_selectors = [
        "home-feed-post-",
        "home-feed-author-",
        "home-feed-reaction-",
        "home-feed-comment-",
        "home-feed-save-",
        "home-feed-repost-",
        "home-feed-promote-",
        "home-feed-share-",
        "home-feed-follow-",
        "home-feed-report-",
        "home-feed-hide-",
        "home-feed-block-",
        "home-feed-mute-",
        "home-feed-media-",
    ]
    for selector in action_selectors:
        require(post_card, selector, f"stable Home feed QA selector {selector}")

    post_card_contract = {
        "NativeMediaViewer import": "NativeMediaViewer",
        "media viewer item adapter": "mediaViewerItemFromPulseMedia",
        "media display URL reuse": "mediaDisplayUrl",
        "media kind fallback": "mediaKind",
        "media click stops post open": "event.stopPropagation();\n              setViewerIndex(index);",
        "unsupported media fallback card": "Open viewer",
        "share fallback URL": "pulsePostUrl(post.id)",
    }
    for label, needle in post_card_contract.items():
        require(post_card, needle, label)

    viewer_contract = {
        "viewer root QA selector": 'testID="native-media-viewer"',
        "viewer close selector": 'testID="native-media-viewer-close"',
        "viewer previous selector": 'testID="native-media-viewer-prev"',
        "viewer next selector": 'testID="native-media-viewer-next"',
        "viewer share selector": 'testID="native-media-viewer-share"',
        "processing state": "ProcessingState",
        "unsupported state": "UnsupportedState",
        "video playback shell": "<Video",
        "image pinch path": "<PinchGestureHandler",
    }
    for label, needle in viewer_contract.items():
        require(media_viewer, needle, label)

    feed_contract = {
        "feed API": "/api/pulse/feed",
        "post detail API": "/api/pulse/posts/${postId}",
        "reaction API": "/api/pulse/posts/${postId}/react",
        "save API": "/api/pulse/posts/${postId}/save",
        "comment API": "/api/pulse/posts/${postId}/comments",
        "media normalization": "normalizeMedia(item.media || item.media_assets || item.attachments || [], item)",
        "image URL fallback": "item.image_url",
        "video URL fallback": "item.video_url",
        "broken media filtering": "Boolean(mediaDisplayUrl(media))",
    }
    for label, needle in feed_contract.items():
        require(feed_api, needle, label)

    print("PulseSoc native Home feed interaction QA audit passed.")
    print(f"- Home action contracts: {len(home_handlers)}")
    print(f"- Feed QA selectors: {len(action_selectors)}")
    print(f"- Media viewer contracts: {len(viewer_contract)}")
    print("- Browser/device evidence remains documented separately in the QA report.")


if __name__ == "__main__":
    main()
