#!/usr/bin/env python3
"""Audit the PulseSoc native Camera Studio foundation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    file_path = ROOT / path
    if not file_path.exists():
        fail(f"missing required file: {path}")
    return file_path.read_text(encoding="utf-8")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def require_terms(label: str, text: str, terms: list[str]) -> None:
    for term in terms:
        require(term in text, f"{label} missing required term: {term}")


def main() -> int:
    report = read("reports/pulsesoc_native_camera_studio_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    camera_api = read("mobile-native/src/api/camera.ts")
    camera_screen = read("mobile-native/src/screens/CameraStudioScreen.tsx")
    media_upload = read("mobile-native/src/media/nativeMediaUpload.ts")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    nav_types = read("mobile-native/src/navigation/types.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    status = read("mobile-native/src/screens/StatusScreen.tsx")
    chat = read("mobile-native/src/screens/ChatScreen.tsx")

    require_terms(
        "camera progress report",
        report,
        [
            "# PulseSoc Native Camera Studio Progress",
            "Production WebView camera routes were not modified",
            "CameraStudioScreen",
            "/api/pulse/camera/config",
            "/api/pulse/media/upload",
            "/api/pulse/media/mux/direct-upload",
            "/api/pulse/media/mux/direct-upload/complete",
            "/api/pulse/camera/preview",
            "/api/pulse/posts/create-from-camera",
            "/api/pulse/reels/create-from-camera",
            "Not device verified",
            "safe web fallback",
        ],
    )

    require_terms(
        "camera API wrapper",
        camera_api,
        [
            "getCameraConfig",
            "createCameraPreview",
            "markCameraPreviewPublished",
            "createPostFromCamera",
            "createReelFromCamera",
            "/api/pulse/camera/config",
            "/api/pulse/camera/preview",
            "/api/pulse/camera/preview/mark-published",
            "/api/pulse/posts/create-from-camera",
            "/api/pulse/reels/create-from-camera",
        ],
    )

    require_terms(
        "camera screen",
        camera_screen,
        [
            "CameraView",
            "useCameraPermissions",
            "useMicrophonePermissions",
            "cameraCompressionPolicy",
            "useNativeMediaUpload",
            "MediaUploadPreview",
            "uploadProfileAvatar",
            "uploadProfileCover",
            "uploadMessengerMedia",
            "sendConversationMessage",
            "createStatus",
            "createPostFromCamera",
            "createReelFromCamera",
            "openWebFallback",
            "device-unverified",
        ],
    )

    require("CameraStudio" in nav_types, "root navigation types include CameraStudio")
    require("CameraStudioScreen" in app_nav and 'name="CameraStudio"' in app_nav, "app navigator registers CameraStudio screen")
    require("pulse/camera/:mode?" in linking, "linking supports pulse camera deep links")
    require("CameraStudio" in home and "target: \"feed\"" in home, "Home exposes native camera entry")
    require("CameraStudio" in status and "target: \"status\"" in status, "Status exposes native camera entry")
    require("CameraStudio" in chat and "target: \"message\"" in chat, "Messenger exposes native camera entry")
    require("cameraCompressionPolicy" in media_upload and "compression_policy" in media_upload, "shared media upload carries camera compression policy metadata")
    require("nativeMediaAssetFromUri" in media_upload, "shared media upload can accept CameraView captured assets")
    require("Native Camera Studio + Media Compression/Preview Foundation" in progress, "progress report recommends or records camera studio foundation")

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
        require("PulseSoc Native Camera Studio Progress" not in text, f"native camera report leaked into production WebView path: {path}")

    print("PulseSoc native Camera Studio audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
