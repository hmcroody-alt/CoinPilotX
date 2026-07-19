#!/usr/bin/env python3
"""Audit PulseSoc Native Camera Studio media QA automation documentation and guards."""

from __future__ import annotations

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
    report = read("reports/pulsesoc_native_camera_studio_media_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")
    camera = read("mobile-native/src/screens/CameraStudioScreen.tsx")
    qa_auth = read("mobile-native/src/session/qaSimulatorAuth.ts")
    qa_media = read("mobile-native/src/media/qaCameraMedia.ts")
    types = read("mobile-native/src/navigation/types.ts")
    bot = read("bot.py")

    require_terms(
        "media QA report",
        report,
        [
            "# PulseSoc Native Camera Studio Media QA Automation",
            "xcrun simctl addmedia",
            "QA-only Camera Studio media injection",
            "__DEV__",
            "localhost",
            "/api/mobile/auth/login",
            "/api/pulse/media/upload",
            "/api/pulse/camera/preview",
            "/api/pulse/posts",
            "/api/pulse/reels/create",
            "Feed destination publish routing to native Post Detail",
            "Status destination publish routing to native Status viewer",
            "Reel destination publish routing to native Reels viewer",
            "Upload cancel/retry",
            "Physical iPhone",
            "Physical Android",
            "Do not move to Native LiveKit calls yet",
        ],
    )

    require_terms(
        "progress report",
        progress,
        [
            "Native Camera Studio Media QA Automation",
            "Feed publish to native Post Detail",
            "Status publish to native Status viewer",
            "Reel publish to native Reels viewer",
            "upload retry/cancel",
            "physical iPhone and Android Camera Studio QA",
            "before moving to Native LiveKit calls",
        ],
    )

    require_terms(
        "Camera Studio QA automation",
        camera,
        [
            "shouldEnableQaCameraMediaAutomation",
            "createQaCameraImageAsset",
            "route.params?.qaMedia",
            "route.params?.qaAutoPublish",
            "qaMediaSeedRef",
            "qaPublishRef",
            "destinationFromParams(route.params)",
            "Platform.OS === \"ios\" ? 58 : 18",
            "Platform.OS === \"ios\" ? 104 : 24",
        ],
    )

    require_terms(
        "QA auth parser",
        qa_auth,
        [
            "qaMedia",
            "qaAutoPublish",
            "qaCaption",
            "signIn(identifier.trim(), password)",
            "isLocalApiBaseUrl(PULSE_API_BASE_URL)",
        ],
    )

    require_terms(
        "QA media helper",
        qa_media,
        [
            "shouldEnableQaCameraMediaAutomation",
            "isQaSimulatorAuthEnabled",
            "createQaCameraImageAsset",
            "expo-file-system/legacy",
            "camera-studio-qa-image.png",
            "nativeMediaAssetFromUri",
        ],
    )

    require("qaMedia?: \"image\"" in types, "Camera Studio route params include QA media type")
    require("qaAutoPublish?: boolean" in types, "Camera Studio route params include QA autopublish flag")
    require("qaCaption?: string" in types, "Camera Studio route params include QA caption")
    require("qaMedia" not in bot and "qaAutoPublish" not in bot, "QA media automation must not add production backend params")
    require("simulator-login" not in bot, "production backend must not expose simulator-login route")

    forbidden_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_camera_engine.js",
        "static/css/pulse_camera_engine.css",
    ]
    for path in forbidden_paths:
        file_path = ROOT / path
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        require("qaAutoPublish" not in text and "camera-studio-qa-image" not in text, f"QA media automation leaked into WebView path: {path}")

    print("PulseSoc native Camera Studio media QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
