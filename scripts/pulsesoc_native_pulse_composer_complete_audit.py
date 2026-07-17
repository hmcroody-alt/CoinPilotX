#!/usr/bin/env python3
"""Focused regression gate for the native Home Pulse Composer."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    value = (ROOT / path).read_text(encoding="utf-8")
    if not value:
        raise AssertionError(f"empty required source: {path}")
    return value


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    composer = source("mobile-native/src/components/HomePulseComposer.tsx")
    home = source("mobile-native/src/screens/HomeScreen.tsx")
    feed = source("mobile-native/src/api/feed.ts")
    reels = source("mobile-native/src/api/reels.ts")
    music = source("mobile-native/src/api/composerMusic.ts")
    upload = source("mobile-native/src/media/nativeMediaUpload.ts")
    queue = source("mobile-native/src/media/useComposerMediaQueue.ts")
    queue_ui = source("mobile-native/src/media/ComposerMediaQueue.tsx")
    backend = source("services/pulse_feed_engine.py")
    routes = source("bot.py")
    report = source("reports/pulsesoc_native_composer_completion_2026-07-17.md")
    notification_routing = source("mobile-native/src/navigation/notificationRouting.ts")

    for token in (
        "pulsesoc.native.home.composer.draft.v1", "MAX_BODY = 3000", "createPost(payload)",
        "useComposerMediaQueue", "home-composer-audience-options", "public", "followers", "private",
        "home-composer-photo", "home-composer-video", "home-composer-camera", "home-composer-more",
        "home-composer-publish", "home-composer-retry", "ComposerMediaQueue", "onOpenLive()",
        'route: "/pulse/marketplace/create"', 'route: "/pulse/questions"', "identity?.avatarUrl",
        "accessibilityState={{ disabled: publishing || !hasPublishPayload }}",
        'key: "poll"', 'key: "scam_report"', "suggestComposerMusic", "music_track_id",
        "findMatchingPost", 'listFeed({ feed: "my_posts", limit: 20 })',
        "findMatchingReel", "findMatchingReelPost", "toggleMusicPreview", "Audio.Sound.createAsync",
        'accessibilityLiveRegion="polite"', "failedPublish: lastFailedPublish",
    ):
        require(token in composer, f"Composer contract missing: {token}")

    require("ScrollView horizontal" not in composer, "Composer must not reintroduce clipped horizontal rails")
    require("cycleVisibility" not in composer, "Audience must be an explicit selector, not a hidden cycle")
    collapsed = composer.split("!expanded ? (", 1)[1].split(") : (", 1)[0]
    require('label="Music"' not in collapsed and 'label="Feeling"' not in collapsed and ">⌁ Transmit<" not in collapsed, "collapsed Composer must remain intent-only")
    require("Feeling:" not in composer, "native Composer must not fabricate structured feeling metadata in the post body")
    require('minHeight: 44' in composer and 'minHeight: 44' in queue_ui, "Composer controls must preserve 44-point touch targets")
    require("<HomePulseComposer" in home and "identity={identity}" in home and "onOpenRoute={onOpenRoute}" in home, "Home integration missing identity or route wiring")
    require('pulseApi<CreatePostResponse>("/api/pulse/posts"' in feed, "server-authoritative post API must remain reused")
    require('pulseApi<CreateReelResponse>("/api/pulse/reels/create"' in reels, "Reels must use their canonical production create route")
    require('pulseApi<ComposerMusicResponse>("/api/pulse/music/ai-suggest"' in music, "music must use the approved production catalog")
    for token in ("COMPOSER_MEDIA_LIMIT = 4", "pickNativeImages", "Promise.all", "uploadAll", "retry", "cancel", "remove", "move", "restore"):
        require(token in queue, f"multi-attachment queue contract missing: {token}")
    for token in ("onMove", "onRemove", "onRetry", "onCancel", "accessibilityLabel"):
        require(token in queue_ui, f"per-attachment UI contract missing: {token}")
    for token in ("MAX_IMAGE_BYTES = 5 * 1024 * 1024", "MAX_GIF_BYTES = 8 * 1024 * 1024", "MAX_VIDEO_BYTES = 150 * 1024 * 1024"):
        require(token in upload, f"production upload limit missing: {token}")
    require('POST_TYPES = {"text", "image", "video", "gif", "poll", "replay", "scam_report", "arena_result", "roast_clip", "live"}' in backend, "backend post types changed without Composer audit")
    require('@webhook_app.route("/api/pulse/posts", methods=["POST"])' in routes, "canonical post route missing")
    require('@webhook_app.route("/api/pulse/media/upload", methods=["POST"])' in routes, "canonical upload route missing")
    require('@webhook_app.route("/api/pulse/reels/create", methods=["POST"])' in routes, "canonical Reel route missing")
    require('normalized === "/pulse/compose"' in notification_routing and 'params: { openComposer: true }' in notification_routing, "Composer deep link must restore the native expanded Composer")
    for heading in ("Required implementation matrix", "Canonical production post model", "Implemented native Composer", "Completion and release readiness", "Rollback plan"):
        require(heading in report, f"Composer report missing section: {heading}")

    native = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native/src").rglob("*.ts*"))
    require("react-native-webview" not in native.lower(), "native Composer must not add WebView")
    print("PulseSoc native Pulse Composer complete audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
