#!/usr/bin/env python3
"""Audit PulseSoc Native captured iPhone Camera Studio QA honesty and scope."""

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
    captured = read("reports/pulsesoc_native_iphone_camera_captured_qa.md")
    physical = read("reports/pulsesoc_native_physical_camera_qa_results.md")
    progress = read("reports/pulsesoc_native_progress.md")
    bot = read("bot.py")

    require_terms(
        "captured iPhone Camera QA report",
        captured,
        [
            "# PulseSoc Native Captured iPhone Camera Studio QA",
            "Status: machine-captured iPhone launch, bundle, deep-link, display, process, and syslog evidence was collected.",
            "Real Camera Studio interaction QA remains blocked.",
            "iPhone 16 Pro",
            "iOS 18.7.3",
            "com.pulsesoc.nativeapp",
            "iOS Bundled 584ms index.ts",
            "pulsesoc://pulse/camera/photo?target=feed",
            "Could not start screenshotr service: Invalid service",
            "fgApp: com.pulsesoc.nativeapp",
            "Camera service remained cold",
            "No backend IDs were produced",
            "Do not move to Native LiveKit calls yet.",
        ],
    )

    require_terms(
        "physical Camera QA results",
        physical,
        [
            "Captured iPhone Camera Studio QA Attempt",
            "machine-captured launch, bundle, deep-link, display, process, and syslog evidence",
            "camera service remained cold",
            "No backend media/upload/post/status/reel IDs were produced",
        ],
    )

    require_terms(
        "native progress report",
        progress,
        [
            "Native Captured iPhone Camera Studio QA Pass",
            "machine-captured launch, bundle, deep-link, display, process, and syslog evidence",
            "No screenshot/video evidence or backend media/upload/post/status/reel IDs",
            "before moving to Native LiveKit calls",
            "reports/pulsesoc_native_iphone_camera_captured_qa.md",
            "scripts/pulsesoc_native_iphone_camera_captured_qa_audit.py",
        ],
    )

    forbidden_claims = [
        "photo capture verified",
        "video capture verified",
        "Feed publish verified",
        "Status publish verified",
        "Reels publish verified",
        "real Camera Studio interaction passed",
        "LiveKit calls ready",
    ]
    for claim in forbidden_claims:
        require(claim not in captured, f"captured report must not claim unverified QA: {claim}")
        require(claim not in physical, f"physical report must not claim unverified QA: {claim}")
        require(claim not in progress, f"progress report must not claim unverified QA: {claim}")

    require("PulseSoc Native Captured iPhone Camera Studio QA" not in bot, "captured QA report must not leak into production backend")

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
            require("PulseSoc Native Captured iPhone Camera Studio QA" not in text, f"captured QA report leaked into WebView path: {path}")

    print("PulseSoc native captured iPhone Camera Studio QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
