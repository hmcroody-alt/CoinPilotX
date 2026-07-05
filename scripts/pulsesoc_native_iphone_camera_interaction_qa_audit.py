#!/usr/bin/env python3
"""Audit PulseSoc Native iPhone Camera Studio interaction QA evidence boundaries."""

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
    report = read("reports/pulsesoc_native_iphone_camera_interaction_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")
    bot = read("bot.py")

    require_terms(
        "iPhone Camera Studio interaction QA report",
        report,
        [
            "# PulseSoc Native iPhone Camera Studio Interaction QA",
            "Status: physical iPhone app launch, bundle load, Camera Studio deep-link launch, and process-level suspend/resume were verified.",
            "Real on-device Camera Studio interaction remains unverified.",
            "iPhone 16 Pro",
            "iOS 18.7.3",
            "com.pulsesoc.nativeapp",
            "Launched application with com.pulsesoc.nativeapp bundle identifier.",
            "iOS Bundled",
            "pulsesoc://pulse/camera/photo?target=feed",
            "Signal to suspend process sent to pid 879",
            "Sent signal to resume process sent to pid 879",
            "Could not start screenshotr service: Invalid service",
            "No backend media IDs, upload IDs, post IDs, status IDs, or reel IDs were captured",
            "Do not move to Native LiveKit calls yet.",
            "dedicated QA-only XCTest UI test target",
        ],
    )

    require_terms(
        "native progress report",
        progress,
        [
            "Native iPhone Camera Studio Interaction QA",
            "physical iPhone app launch, bundle load, Camera Studio payload launch, and process-level suspend/resume",
            "real camera/mic/gallery/capture/upload/publish behavior remains unverified",
            "before moving to Native LiveKit calls",
        ],
    )

    forbidden_claims = [
        "real on-device Camera Studio interaction passed",
        "physical iPhone Camera Studio verified",
        "photo capture verified",
        "video capture verified",
        "Feed publish verified",
        "Status publish verified",
        "Reels publish verified",
    ]
    for claim in forbidden_claims:
        require(claim not in report, f"report must not claim unverified interaction: {claim}")
        require(claim not in progress, f"progress must not claim unverified interaction: {claim}")

    require("PulseSoc Native iPhone Camera Studio Interaction QA" not in bot, "iPhone QA report must not leak into production backend")

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
            require("PulseSoc Native iPhone Camera Studio Interaction QA" not in text, f"iPhone QA report leaked into WebView path: {path}")

    print("PulseSoc native iPhone Camera Studio interaction QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
