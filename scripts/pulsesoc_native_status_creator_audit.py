#!/usr/bin/env python3
"""Static audit for the PulseSoc native Status Creator foundation."""

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
    report = read("reports/pulsesoc_native_status_creator_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    status_api = read("mobile-native/src/api/status.ts")
    status_screen = read("mobile-native/src/screens/StatusScreen.tsx")
    creator = read("mobile-native/src/components/StatusCreator.tsx")
    media_hook = read("mobile-native/src/media/useNativeMediaUpload.ts")
    media_preview = read("mobile-native/src/media/MediaUploadPreview.tsx")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Status Creator does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"status creator report must document reuse/safety/device truth: {phrase}")

    for token in (
        "createStatus",
        "/api/pulse/status",
        "CreateStatusPayload",
        "media_ids",
        "visibility",
        "duration_hours",
        "searchStatusMusic",
        "/api/pulse/status/music/search",
        "listTrendingStatusMusic",
        "/api/pulse/status/music/trending",
        "generateStatusAiStory",
        "/api/pulse/status/ai-story",
        "normalizeStatus",
    ):
        require(token in status_api, f"status API creator wrapper missing: {token}")

    for token in (
        "StatusCreator",
        "creatorOpen",
        "setCreatorOpen",
        "handleCreatedStatus",
        "load(\"refresh\")",
        "Create",
        "onCreated",
    ):
        require(token in status_screen, f"Status screen creator integration missing: {token}")

    for token in (
        "useNativeMediaUpload",
        "MediaUploadPreview",
        "createStatus",
        "chooseImage",
        "chooseVideo",
        "openCamera",
        "uploadResultMediaId",
        "searchStatusMusic",
        "listTrendingStatusMusic",
        "generateStatusAiStory",
        "pulse_status",
        "public",
        "followers",
        "private",
        "24",
        "48",
        "72",
        "168",
        "Advanced editor tools remain available in PulseSoc web",
    ):
        require(token in creator, f"StatusCreator behavior missing: {token}")

    for token in ("upload", "retry", "cancel", "chooseImage", "chooseVideo", "openCamera"):
        require(token in media_hook, f"shared media hook should support Status Creator behavior: {token}")

    for token in ("MediaUploadPreview", "onRetry", "onCancel", "progress.percent"):
        require(token in media_preview, f"shared media preview should support Status Creator behavior: {token}")

    for phrase in (
        "Status Creator Foundation",
        "Native Media Viewer Foundation",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Status Creator and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("react-native-webview" not in mobile_native.lower(), "native Status Creator must not introduce WebView")

    print("PulseSoc native Status Creator audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
