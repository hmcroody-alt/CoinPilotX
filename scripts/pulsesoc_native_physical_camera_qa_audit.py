#!/usr/bin/env python3
"""Audit PulseSoc Native physical Camera Studio QA planning and scoped hardening."""

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
    report = read("reports/pulsesoc_native_physical_camera_qa_plan.md")
    progress = read("reports/pulsesoc_native_progress.md")
    upload = read("mobile-native/src/media/nativeMediaUpload.ts")
    camera = read("mobile-native/src/screens/CameraStudioScreen.tsx")
    bot = read("bot.py")

    require_terms(
        "physical Camera QA plan",
        report,
        [
            "# PulseSoc Native Physical Camera QA Plan",
            "Do not move to Native LiveKit calls yet",
            "large video",
            "retry/cancel",
            "upload progress",
            "compression metadata",
            "Physical iPhone QA Checklist",
            "Physical Android QA Checklist",
            "Weak Network / Retry-Cancel Plan",
            "Failure Recovery",
            "No production WebView code was changed",
            "/api/pulse/media/upload",
            "/api/pulse/camera/preview",
            "/api/pulse/posts/create-from-camera",
            "/api/pulse/reels/create-from-camera",
        ],
    )

    require_terms(
        "progress report",
        progress,
        [
            "Native Physical Camera Studio QA Plan",
            "physical iPhone and Android Camera Studio QA",
            "large-video upload",
            "retry/cancel",
            "before moving to Native LiveKit calls",
        ],
    )

    require_terms(
        "native upload hardening",
        upload,
        [
            "formatFileSize",
            "Uploading media ${Math.round((event.loaded / event.total) * 100)}%",
            "event.loaded",
            "event.total",
            "xhr.onabort",
            "compression_policy",
        ],
    )

    require("Camera, microphone, recording, compression, and large upload behavior require real-device QA" in camera, "Camera Studio keeps device QA boundary visible")
    require("PulseSoc Native Physical Camera QA Plan" not in bot, "physical QA plan must not leak into production backend")
    require("formatFileSize(event.loaded)" in upload and "formatFileSize(event.total)" in upload, "upload progress must include transferred and total sizes")

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
        require("Physical Camera QA Plan" not in text, f"physical QA plan leaked into WebView path: {path}")

    print("PulseSoc native physical Camera Studio QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
