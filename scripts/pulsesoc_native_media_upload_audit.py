#!/usr/bin/env python3
"""Static audit for the PulseSoc native media capture/upload foundation."""

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
    report = read("reports/pulsesoc_native_media_upload_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    service = read("mobile-native/src/media/nativeMediaUpload.ts")
    hook = read("mobile-native/src/media/useNativeMediaUpload.ts")
    preview = read("mobile-native/src/media/MediaUploadPreview.tsx")
    package = read("mobile-native/package.json")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native media upload does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"media upload report must document reuse/safety/device truth: {phrase}")

    for route in (
        "/api/pulse/media/upload",
        "/api/pulse/media/${mediaId}/status",
    ):
        require(route in service, f"media upload service must reuse backend route: {route}")

    for token in (
        "expo-image-picker",
        "expo-camera",
        "expo-file-system",
    ):
        require(token in package, f"required Expo media dependency missing from package.json: {token}")

    for token in (
        "pickNativeImage",
        "pickNativeVideo",
        "captureNativeMedia",
        "requestMediaLibraryPermissionsAsync",
        "requestCameraPermissionsAsync",
        "launchImageLibraryAsync",
        "launchCameraAsync",
        "validateNativeMedia",
        "uploadNativeMedia",
        "XMLHttpRequest",
        "xhr.upload.onprogress",
        "xhr.abort",
        "pollNativeMediaProcessing",
        "mediaUploadIntegrationTargets",
        "pulse_status",
        "marketplace_product",
        "creator_studio",
    ):
        require(token in service, f"native media upload service missing behavior: {token}")

    for token in (
        "useNativeMediaUpload",
        "chooseImage",
        "chooseVideo",
        "openCamera",
        "upload",
        "retry",
        "cancel",
        "setProgress",
        "pollNativeMediaProcessing",
    ):
        require(token in hook, f"native media upload hook missing behavior: {token}")

    for token in (
        "MediaUploadPreview",
        "Video",
        "Image",
        "progress.percent",
        "onRetry",
        "onCancel",
        "formatFileSize",
    ):
        require(token in preview, f"native media preview missing behavior: {token}")

    for phrase in (
        "Media Capture + Upload Foundation",
        "Native Feed Composer Foundation",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed media upload and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native media upload must not introduce WebView")

    print("PulseSoc native media upload audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
