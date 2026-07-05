#!/usr/bin/env python3
"""Audit PulseSoc Native manual iPhone Camera Studio QA honesty and scope."""

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
    manual = read("reports/pulsesoc_native_iphone_camera_manual_qa.md")
    physical = read("reports/pulsesoc_native_physical_camera_qa_results.md")
    progress = read("reports/pulsesoc_native_progress.md")
    bot = read("bot.py")

    require_terms(
        "manual iPhone Camera QA report",
        manual,
        [
            "# PulseSoc Native Manual iPhone Camera Studio QA",
            "Status: manual iPhone Camera Studio interaction QA was prepared, but no manual screen recording",
            "This report does not claim Camera Studio physical interaction passed.",
            "iPhone 16 Pro",
            "iOS 18.7.3",
            "com.pulsesoc.nativeapp",
            "com.pulsesoc.app",
            "No new manual evidence file was present",
            "No backend media/upload/published destination IDs were produced",
            "Could not start screenshotr service: Invalid service",
            "Do not move to Native LiveKit calls yet.",
        ],
    )

    require_terms(
        "physical Camera QA results",
        physical,
        [
            "Manual iPhone Camera Studio QA Capture Attempt",
            "manual iPhone Camera Studio QA capture remains blocked",
            "No manual recording path was produced.",
            "No backend media/upload/post/status/reel IDs were produced.",
            "remain physical-device unverified",
        ],
    )

    require_terms(
        "native progress report",
        progress,
        [
            "reports/pulsesoc_native_iphone_camera_manual_qa.md",
            "scripts/pulsesoc_native_iphone_camera_manual_qa_audit.py",
            "A manual iPhone Camera Studio QA capture pass was prepared",
            "no human-operated screen recording",
            "remain unverified on the physical iPhone",
            "before higher-risk LiveKit calls",
        ],
    )

    forbidden_claims = [
        "manual iPhone Camera Studio QA passed",
        "physical Camera Studio interaction passed",
        "photo capture verified",
        "video capture verified",
        "Feed publish verified",
        "Status publish verified",
        "Reels publish verified",
        "LiveKit calls ready",
    ]
    for claim in forbidden_claims:
        require(claim not in manual, f"manual report must not claim unverified QA: {claim}")
        require(claim not in physical, f"physical report must not claim unverified QA: {claim}")
        require(claim not in progress, f"progress report must not claim unverified QA: {claim}")

    require("PulseSoc Native Manual iPhone Camera Studio QA" not in bot, "manual QA report must not leak into production backend")

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
            require("PulseSoc Native Manual iPhone Camera Studio QA" not in text, f"manual QA report leaked into WebView path: {path}")

    print("PulseSoc native manual iPhone Camera Studio QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
