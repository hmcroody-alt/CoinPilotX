#!/usr/bin/env python3
"""Static audit for the PulseSoc native Media Viewer foundation."""

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
    report = read("reports/pulsesoc_native_media_viewer_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    app = read("mobile-native/App.tsx")
    viewer = read("mobile-native/src/components/NativeMediaViewer.tsx")
    post_card = read("mobile-native/src/components/PostCard.tsx")
    chat = read("mobile-native/src/screens/ChatScreen.tsx")
    status = read("mobile-native/src/screens/StatusScreen.tsx")
    media_upload = read("mobile-native/src/media/nativeMediaUpload.ts")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Media Viewer does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"media viewer report must document reuse/safety/device truth: {phrase}")

    for token in (
        "NativeMediaViewer",
        "NativeMediaViewerItem",
        "nativeMediaViewerIntegrationTargets",
        "Feed/Post",
        "Messenger",
        "Profile",
        "Status",
        "Reels",
        "Marketplace",
        "Creator Studio",
        "mediaViewerItemFromPulseMedia",
        "mediaDisplayUrl",
        "mediaKind",
        "Video",
        "PinchGestureHandler",
        "PanGestureHandler",
        "pollNativeMediaProcessing",
        "Share.share",
        "onSave",
        "onAuthorPress",
        "ProcessingState",
        "UnsupportedState",
    ):
        require(token in viewer, f"NativeMediaViewer behavior missing: {token}")

    require("GestureHandlerRootView" in app, "native app must install gesture root for media viewer gestures")

    for token in (
        "NativeMediaViewer",
        "mediaViewerItemFromPulseMedia",
        "viewerIndex",
        "Post media",
        "Open viewer",
    ):
        require(token in post_card, f"PostCard media viewer integration missing: {token}")

    for token in (
        "NativeMediaViewer",
        "NativeMediaViewerItem",
        "viewerOpen",
        "Messenger media",
        "Video attachment",
    ):
        require(token in chat, f"Messenger media viewer integration missing: {token}")

    for token in (
        "NativeMediaViewer",
        "mediaViewerItemFromPulseMedia",
        "onLongPress",
        "Status media",
    ):
        require(token in status, f"Status media viewer integration hook missing: {token}")

    for token in ("pollNativeMediaProcessing", "/api/pulse/media/", "/status"):
        require(token in media_upload, f"media processing status reuse missing: {token}")

    for phrase in (
        "Media Viewer Foundation",
        "Native Marketplace Browse + Listing Detail Foundation",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Media Viewer and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("react-native-webview" not in mobile_native.lower(), "native Media Viewer must not introduce WebView")

    print("PulseSoc native Media Viewer audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
