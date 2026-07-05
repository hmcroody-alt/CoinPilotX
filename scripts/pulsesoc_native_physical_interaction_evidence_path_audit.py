#!/usr/bin/env python3
"""Audit PulseSoc Native physical interaction evidence path documentation."""

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
    report = read("reports/pulsesoc_native_physical_interaction_evidence_path.md")
    progress = read("reports/pulsesoc_native_progress.md")
    bot = read("bot.py")

    require_terms(
        "physical interaction evidence path report",
        report,
        [
            "# PulseSoc Native Physical Interaction Evidence Path",
            "Status: evidence path prepared; no new native user-facing feature was built.",
            "manual on-device screen recording plus backend ID logging",
            "iPhone built-in screen recording",
            "QuickTime Recording",
            "Xcode Screenshot Workflow",
            "idevicesyslog",
            "chat_media_uploads",
            "pulse_posts",
            "pulse_status",
            "pulse_reels",
            "media_upload_id",
            "post_id",
            "status_id",
            "reel_id",
            "No dedicated `PulseSocNativeUITests` target is currently present.",
            "Do not move to Native LiveKit calls yet.",
            "dedicated QA-only XCTest UI target",
        ],
    )

    require_terms(
        "native progress report",
        progress,
        [
            "Native Physical Interaction Evidence Path",
            "manual iPhone screen recording or QuickTime video capture plus backend ID logging",
            "no new user-facing feature",
            "before moving to Native LiveKit calls",
            "reports/pulsesoc_native_physical_interaction_evidence_path.md",
            "scripts/pulsesoc_native_physical_interaction_evidence_path_audit.py",
        ],
    )

    forbidden_claims = [
        "physical Camera Studio interaction passed",
        "photo capture verified",
        "video capture verified",
        "Feed publish verified",
        "Status publish verified",
        "Reels publish verified",
        "LiveKit calls ready",
    ]
    for claim in forbidden_claims:
        require(claim not in report, f"report must not claim unverified device QA: {claim}")
        require(claim not in progress, f"progress must not claim unverified device QA: {claim}")

    require("PulseSoc Native Physical Interaction Evidence Path" not in bot, "evidence path report must not leak into production backend")

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
        require("PulseSoc Native Physical Interaction Evidence Path" not in text, f"evidence path leaked into WebView path: {path}")

    print("PulseSoc native physical interaction evidence path audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
