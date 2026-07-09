#!/usr/bin/env python3
"""Audit PulseSoc Native Home manual iPhone release QA honesty and scope."""

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
    return file_path.read_text(encoding="utf-8", errors="ignore")


def require_terms(label: str, text: str, terms: list[str]) -> None:
    for term in terms:
        require(term in text, f"{label} missing required term: {term}")


def main() -> int:
    report = read("reports/pulsesoc_native_home_manual_iphone_release_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")
    app_json = read("mobile-native/app.json")
    push_api = read("mobile-native/src/api/push.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    post_detail = read("mobile-native/src/screens/PostDetailScreen.tsx")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    bot = read("bot.py")

    require_terms(
        "manual Home iPhone report",
        report,
        [
            "# PulseSoc Native Home Manual iPhone Release QA",
            "manual screen recording",
            "This report does not claim Home physical interaction passed.",
            "iPhone 16 Pro",
            "iOS 18.7.3",
            "com.pulsesoc.nativeapp",
            "com.pulsesoc.app",
            "pulsesoc://pulse",
            "Launched application with com.pulsesoc.nativeapp bundle identifier.",
            "PulseSocNative.app/PulseSocNative",
            "Signal to suspend process sent to pid",
            "Sent signal to resume process sent to pid",
            "Could not start screenshotr service: Invalid service",
            "No screenshot or video file was produced.",
            "No provider-backed push notification was delivered and tapped.",
            "Can Home now be considered release-complete?",
            "NO",
        ],
    )

    require_terms(
        "progress report",
        progress,
        [
            "Native Home Manual iPhone Release QA",
            "Home remains foundation-complete",
            "manual iPhone interaction and push/tap behavior remain unproven",
            "PulseSoc Native Home Manual Screen Recording And Push Tap Proof",
        ],
    )

    require_terms(
        "app config",
        app_json,
        [
            '"scheme": "pulsesoc"',
            '"bundleIdentifier": "com.pulsesoc.nativeapp"',
            '"expo-notifications"',
        ],
    )
    require_terms(
        "push API",
        push_api,
        [
            "registerPushDevice",
            "/api/push/subscribe",
            "Push registration requires a physical device.",
        ],
    )
    require_terms(
        "notification routing",
        routing,
        [
            "setupNotificationResponseRouting",
            "routeNotificationTarget",
            "PostDetail",
            "navigateToNotifications",
        ],
    )
    require_terms(
        "Home release controls",
        home,
        [
            "RefreshControl",
            "handleHide",
            "handleMute",
        ],
    )
    require_terms(
        "comment accessibility",
        post_detail,
        [
            'accessibilityRole="button"',
            'accessibilityLabel="Submit comment"',
            'testID="post-detail-submit-comment"',
        ],
    )

    forbidden_claims = [
        "Can Home now be considered release-complete?\nYES",
        "Home physical interaction: passed",
        "manual iPhone Home QA: passed",
        "Push/tap behavior: passed",
        "notification route verified on iPhone: yes",
        "feed scroll verified on iPhone: yes",
        "text post publish verified on iPhone: yes",
        "Home release-complete",
    ]
    for claim in forbidden_claims:
        require(claim not in report, f"manual report must not claim unverified QA: {claim}")
        require(claim not in progress, f"progress report must not claim unverified QA: {claim}")

    require("PulseSoc Native Home Manual iPhone Release QA" not in bot, "manual QA report must not leak into production backend")

    forbidden_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_home_core.js",
        "static/css/pulse_home_os.css",
    ]
    for path in forbidden_paths:
        file_path = ROOT / path
        if file_path.exists():
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            require("PulseSoc Native Home Manual iPhone Release QA" not in text, f"manual QA report leaked into WebView path: {path}")

    print("PulseSoc Native Home manual iPhone release QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
