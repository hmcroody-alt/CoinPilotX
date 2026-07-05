#!/usr/bin/env python3
"""Audit PulseSoc Native Camera Studio XCTest QA automation scope."""

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
    report = read("reports/pulsesoc_native_xctest_camera_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")
    test_source = read("mobile-native/ios/PulseSocNativeUITests/PulseSocNativeCameraStudioQATests.swift")
    info_plist = read("mobile-native/ios/PulseSocNativeUITests/Info.plist")
    project = read("mobile-native/ios/PulseSocNative.xcodeproj/project.pbxproj")
    scheme = read("mobile-native/ios/PulseSocNative.xcodeproj/xcshareddata/xcschemes/PulseSocNative.xcscheme")
    qa_auth = read("mobile-native/src/session/qaSimulatorAuth.ts")
    bot = read("bot.py")

    require_terms(
        "XCTest QA report",
        report,
        [
            "# PulseSoc Native XCTest Camera Studio QA",
            "QA-only",
            "com.pulsesoc.nativeapp",
            "com.pulsesoc.app",
            "No production auth weakening",
            "xcodebuild test",
            "backend media/upload/published ID capture plan",
            "Do not move to Native LiveKit calls yet.",
        ],
    )
    require_terms(
        "native progress report",
        progress,
        [
            "Native Camera Studio XCTest QA",
            "reports/pulsesoc_native_xctest_camera_qa.md",
            "scripts/pulsesoc_native_xctest_camera_qa_audit.py",
            "PulseSocNativeUITests",
            "before Native LiveKit calls",
        ],
    )
    require_terms(
        "XCTest source",
        test_source,
        [
            "final class PulseSocNativeCameraStudioQATests",
            "XCUIApplication(bundleIdentifier: \"com.pulsesoc.nativeapp\")",
            "PULSESOC_NATIVE_QA_BUNDLE_ID",
            "PULSESOC_NATIVE_QA_XCTEST",
            "PULSESOC_QA_IDENTIFIER",
            "PULSESOC_QA_CAMERA_DEEPLINK",
            "PulseSoc Camera",
            "Gallery",
            "Publish",
            "XCTAttachment",
        ],
    )
    require("CFBundleIdentifier" in info_plist, "UI test Info.plist must declare bundle identifier")
    require("PulseSocNativeUITests" in project, "Xcode project must include the UI test target name")
    require("com.apple.product-type.bundle.ui-testing" in project, "Xcode project must declare UI-testing product type")
    require("TEST_TARGET_NAME = PulseSocNative" in project, "UI test target must target PulseSocNative")
    require("com.pulsesoc.nativeapp.uitests" in project, "UI test bundle id must be separate from app identity")
    require("PulseSocNativeUITests.xctest" in scheme, "shared scheme must reference UI test bundle")
    require("isQaSimulatorAuthEnabled" in qa_auth and "__DEV__" in qa_auth and "isLocalApiBaseUrl" in qa_auth, "QA auth must remain dev/local gated")
    require("simulator-login" not in bot, "production backend must not expose QA simulator auth route")

    forbidden_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_camera_engine.js",
        "static/css/pulse_camera_engine.css",
    ]
    for path in forbidden_paths:
        file_path = ROOT / path
        if file_path.exists():
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            require("PulseSoc Native XCTest Camera Studio QA" not in text, f"XCTest QA report leaked into WebView path: {path}")
            require("PULSESOC_NATIVE_QA_XCTEST" not in text, f"XCTest QA hook leaked into WebView path: {path}")

    forbidden_claims = [
        "physical Camera Studio passed",
        "photo capture verified on iPhone",
        "video capture verified on iPhone",
        "LiveKit calls ready",
    ]
    for claim in forbidden_claims:
        require(claim not in report, f"report must not claim unverified behavior: {claim}")
        require(claim not in progress, f"progress must not claim unverified behavior: {claim}")

    print("PulseSoc native Camera Studio XCTest QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
