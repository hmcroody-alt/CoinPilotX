#!/usr/bin/env python3
"""Audit PulseSoc Native Camera Studio device QA readiness documentation."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read(path: str) -> str:
    file_path = ROOT / path
    require(file_path.exists(), f"missing required file: {path}")
    return file_path.read_text(encoding="utf-8")


def require_terms(label: str, text: str, terms: list[str]) -> None:
    for term in terms:
        require(term in text, f"{label} missing required term: {term}")


def main() -> int:
    report = read("reports/pulsesoc_native_camera_studio_device_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")
    camera_screen = read("mobile-native/src/screens/CameraStudioScreen.tsx")
    media_upload = read("mobile-native/src/media/nativeMediaUpload.ts")
    app_json = json.loads(read("mobile-native/app.json"))

    expo = app_json.get("expo", {})
    ios = expo.get("ios", {})
    android = expo.get("android", {})
    plugins = set(expo.get("plugins", []))
    info_plist = ios.get("infoPlist", {})
    android_permissions = set(android.get("permissions", []))

    require(ios.get("bundleIdentifier") == "com.pulsesoc.nativeapp", "iOS bundle uses parallel native QA identity")
    require(android.get("package") == "com.pulsesoc.nativeapp", "Android package uses parallel native QA identity")
    require("expo-camera" in plugins, "expo-camera plugin is configured")
    require("expo-image-picker" in plugins, "expo-image-picker plugin is configured")
    require("NSCameraUsageDescription" in info_plist, "iOS camera usage description is configured")
    require("NSMicrophoneUsageDescription" in info_plist, "iOS microphone usage description is configured")
    require("NSPhotoLibraryUsageDescription" in info_plist, "iOS photo library usage description is configured")
    require("CAMERA" in android_permissions, "Android CAMERA permission is configured")
    require("RECORD_AUDIO" in android_permissions, "Android RECORD_AUDIO permission is configured")
    require("READ_MEDIA_IMAGES" in android_permissions, "Android image media permission is configured")
    require("READ_MEDIA_VIDEO" in android_permissions, "Android video media permission is configured")

    require_terms(
        "device QA report",
        report,
        [
            "# PulseSoc Native Camera Studio Device QA + Hardening",
            "Production WebView camera routes were not modified",
            "Physical iPhone",
            "Physical Android",
            "camera permission denied/allowed",
            "microphone permission denied/allowed",
            "photo capture",
            "video capture",
            "front/back camera switch",
            "gallery fallback",
            "compression metadata",
            "upload cancel/retry",
            "publish to Feed",
            "publish to Status",
            "publish to Reels",
            "Profile avatar/cover",
            "Messenger attachment",
            "background interruption recovery",
            "xcrun simctl",
            "adb",
            "com.pulsesoc.nativeapp",
            "Do not move to Native LiveKit calls yet",
        ],
    )

    require_terms(
        "camera screen",
        camera_screen,
        [
            "CameraView",
            "useCameraPermissions",
            "useMicrophonePermissions",
            "setCameraFacing",
            "chooseFromGallery",
            "cameraCompressionPolicy",
            "uploadProfileAvatar",
            "uploadProfileCover",
            "uploadMessengerMedia",
            "createStatus",
            "createPostFromCamera",
            "createReelFromCamera",
            "device-unverified",
        ],
    )

    require_terms(
        "media upload",
        media_upload,
        [
            "compressionPolicy",
            "compression_policy",
            "destination",
            "cameraCompressionPolicy",
            "serverAuthoritative",
            "deviceVerified: false",
        ],
    )

    require("Native Camera Studio + Media Compression/Preview Foundation" in progress, "progress records camera foundation")
    require("Native Camera Studio QA hardening" in progress or "Camera Studio device QA" in progress, "progress recommends camera QA hardening")

    forbidden = [
        "bot.py",
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_camera_engine.js",
        "static/css/pulse_camera_engine.css",
    ]
    for path in forbidden:
        file_path = ROOT / path
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        require("PulseSoc Native Camera Studio Device QA + Hardening" not in text, f"native device QA report leaked into production WebView path: {path}")

    print("PulseSoc native Camera Studio device QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
