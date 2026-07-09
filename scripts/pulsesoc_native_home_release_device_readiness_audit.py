#!/usr/bin/env python3
"""Audit PulseSoc Native Home release-device readiness reporting and guards."""

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
    report = read("reports/pulsesoc_native_home_release_device_readiness.md")
    progress = read("reports/pulsesoc_native_progress.md")
    app_json = read("mobile-native/app.json")
    push_api = read("mobile-native/src/api/push.ts")
    app = read("mobile-native/App.tsx")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    post_detail = read("mobile-native/src/screens/PostDetailScreen.tsx")

    require_terms(
        "device readiness report",
        report,
        [
            "# PulseSoc Native Home Release Device Readiness",
            "iPhone 16 Pro",
            "iOS 18.7.3",
            "com.pulsesoc.nativeapp",
            "Launched application with com.pulsesoc.nativeapp bundle identifier.",
            "pulsesoc://pulse",
            "Physical iPhone Home launch",
            "Push/Tap Readiness",
            "Background Recovery",
            "Accessibility Readiness",
            "Can Home now be considered release-complete?",
            "NO",
            "manual on-device Home interaction",
            "provider-backed push delivery",
        ],
    )

    forbidden_claims = [
        "Can Home now be considered release-complete? YES",
        "Home is release-complete",
        "Home can be considered release-complete",
        "Push/tap behavior | Passed",
        "Background recovery | Passed",
        "iPhone Home fully verified: YES",
        "physical Home interaction | Passed",
    ]
    for claim in forbidden_claims:
        require(claim not in report, f"report must not overclaim: {claim}")
        require(claim not in progress, f"progress must not overclaim: {claim}")

    require_terms(
        "progress report",
        progress,
        [
            "Native Home Release Device Readiness Sweep",
            "Physical iPhone launch and Home deep-link dispatch passed at process level",
            "Home is not yet release-complete",
            "PulseSoc Native Home Manual iPhone Release QA",
        ],
    )

    require_terms(
        "native app config",
        app_json,
        [
            '"scheme": "pulsesoc"',
            '"bundleIdentifier": "com.pulsesoc.nativeapp"',
            '"expo-notifications"',
            '"expo-camera"',
            '"expo-image-picker"',
        ],
    )
    require_terms(
        "push API",
        push_api,
        [
            "registerPushDevice",
            "getPushPermissionState",
            "Push registration requires a physical device.",
            "/api/push/subscribe",
            "provider: \"expo\"",
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
        "linking config",
        linking,
        [
            'prefixes: ["pulsesoc://", "https://pulsesoc.com"]',
            'Home: "pulse"',
            'PostDetail',
        ],
    )
    require_terms(
        "app notification setup",
        app,
        [
            "setupNotificationResponseRouting",
            "Linking.getInitialURL",
            "Linking.addEventListener",
        ],
    )
    require_terms(
        "Home release controls",
        home,
        [
            "RefreshControl",
            "onRefresh={() => refreshHome()}",
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
            'testID="post-detail-comment-input"',
        ],
    )

    print("PulseSoc Native Home release-device readiness audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
