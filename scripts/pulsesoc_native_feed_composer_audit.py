#!/usr/bin/env python3
"""Static audit for the PulseSoc native Feed Composer foundation."""

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
    report = read("reports/pulsesoc_native_feed_composer_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    feed_api = read("mobile-native/src/api/feed.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    composer = read("mobile-native/src/components/FeedComposer.tsx")
    media_hook = read("mobile-native/src/media/useNativeMediaUpload.ts")
    media_preview = read("mobile-native/src/media/MediaUploadPreview.tsx")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Feed Composer does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"feed composer report must document reuse/safety/device truth: {phrase}")

    for token in (
        "createPost",
        "/api/pulse/posts",
        "CreatePostPayload",
        "media_ids",
        "visibility",
        "normalizePost",
    ):
        require(token in feed_api, f"feed API composer wrapper missing: {token}")

    for token in (
        "FeedComposer",
        "composerOpen",
        "setComposerOpen",
        "load(\"refresh\")",
        "Create",
        "onCreated",
    ):
        require(token in home, f"Home Feed composer integration missing: {token}")

    for token in (
        "useNativeMediaUpload",
        "MediaUploadPreview",
        "createPost",
        "chooseImage",
        "chooseVideo",
        "openCamera",
        "uploadResultMediaId",
        "public",
        "followers",
        "private",
        "Advanced composer options remain available in PulseSoc web",
    ):
        require(token in composer, f"FeedComposer behavior missing: {token}")

    for token in ("upload", "retry", "cancel", "chooseImage", "chooseVideo", "openCamera"):
        require(token in media_hook, f"shared media hook should support composer behavior: {token}")

    for token in ("MediaUploadPreview", "onRetry", "onCancel", "progress.percent"):
        require(token in media_preview, f"shared media preview should support composer behavior: {token}")

    for phrase in (
        "Feed Composer Foundation",
        "Native Status Creator Foundation",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Feed Composer and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native Feed Composer must not introduce WebView")

    print("PulseSoc native Feed Composer audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
