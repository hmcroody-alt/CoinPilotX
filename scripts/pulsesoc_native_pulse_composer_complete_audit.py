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
    upload = source("mobile-native/src/media/useNativeMediaUpload.ts")

    for token in (
        "pulsesoc.native.home.composer.draft.v1", "MAX_BODY = 3000", "createPost(payload)",
        "uploadResultMediaId", "home-composer-audience-options", "public", "followers", "private",
        "home-composer-photo", "home-composer-video", "home-composer-camera", "home-composer-more",
        "home-composer-publish", "home-composer-retry", "MediaUploadPreview", "onOpenLive()",
        'route: "/pulse/marketplace/create"', 'route: "/pulse/questions"', "identity?.avatarUrl",
        "accessibilityState={{ disabled: publishing || !hasPublishPayload }}",
    ):
        require(token in composer, f"Composer contract missing: {token}")

    require("ScrollView horizontal" not in composer, "Composer must not reintroduce clipped horizontal rails")
    require("cycleVisibility" not in composer, "Audience must be an explicit selector, not a hidden cycle")
    require("HomePulseComposer identity={identity}" in home and "onOpenRoute={onOpenRoute}" in home, "Home integration missing identity or route wiring")
    require('pulseApi<CreatePostResponse>("/api/pulse/posts"' in feed, "server-authoritative post API must remain reused")
    for token in ("chooseImage", "chooseVideo", "upload", "retry", "cancel"):
        require(token in upload, f"shared upload pipeline missing: {token}")

    native = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native/src").rglob("*.ts*"))
    require("react-native-webview" not in native.lower(), "native Composer must not add WebView")
    print("PulseSoc native Pulse Composer complete audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
