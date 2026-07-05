#!/usr/bin/env python3
"""Audit PulseSoc Native physical Camera Studio QA results honesty and scope."""

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
    report = read("reports/pulsesoc_native_physical_camera_qa_results.md")
    progress = read("reports/pulsesoc_native_progress.md")
    bot = read("bot.py")

    require_terms(
        "physical Camera QA results",
        report,
        [
            "# PulseSoc Native Physical Camera Studio QA Results",
            "Status: physical install and launch verified; physical Camera Studio media interaction remains unverified.",
            "iPhone 16 Pro",
            "iOS 18.7.3",
            "developerModeStatus: enabled",
            "ddiServicesAvailable: true",
            "Build Succeeded",
            "Launched application with com.pulsesoc.nativeapp bundle identifier.",
            "PulseSoc Native   com.pulsesoc.nativeapp   0.1.0",
            "adb devices -l",
            "App install | Passed",
            "App launch | Passed",
            "Camera Studio deep link | Passed at process level",
            "Requires on-device visual/manual QA",
            "These remain simulator-verification results only.",
            "Do not move to Native LiveKit calls yet.",
            "run a manual or automated on-device Camera Studio pass",
        ],
    )

    require_terms(
        "progress report",
        progress,
        [
            "Native Physical Camera Studio QA Attempt",
            "built, signed, and installed `com.pulsesoc.nativeapp`",
            "iPhone 16 Pro",
            "Camera Studio payload URL launch",
            "Physical camera/mic/gallery/capture/upload/publish behavior remains unverified",
            "before moving to Native LiveKit calls",
        ],
    )

    forbidden_claims = [
        "Physical Camera Studio QA passed",
        "physical Camera Studio verified",
        "iPhone physical verified",
        "Android physical verified",
    ]
    for claim in forbidden_claims:
      require(claim not in report, f"report must not claim unverified physical QA: {claim}")
      require(claim not in progress, f"progress must not claim unverified physical QA: {claim}")

    require("PulseSoc Native Physical Camera Studio QA Results" not in bot, "physical QA report must not leak into production backend")

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
        require("Physical Camera Studio QA Results" not in text, f"physical QA results leaked into WebView path: {path}")

    print("PulseSoc native physical Camera Studio QA results audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
