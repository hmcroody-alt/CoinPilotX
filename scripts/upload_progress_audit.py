#!/usr/bin/env python3
"""Audit the unified Pulse upload progress UX contract."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import upload_progress_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    manager = (ROOT / "static/js/pulse_upload_manager.js").read_text(encoding="utf-8")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    camera = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")
    service = (ROOT / "services/upload_progress_service.py").read_text(encoding="utf-8")
    expect("XMLHttpRequest" in manager and "xhr.upload.onprogress" in manager, "upload manager tracks real browser progress")
    expect("Uploading video..." in manager, "upload manager shows video percentage text")
    expect("Processing media..." in manager, "upload manager shows processing state")
    expect("Publishing..." in manager, "upload manager shows publishing state")
    expect("Posted successfully" in manager, "upload manager shows success confirmation")
    expect("Tap to retry" in manager, "upload manager exposes retry copy")
    expect("locks" in manager and "Upload already in progress" in manager, "upload manager prevents duplicate submits")
    expect("pulse_upload_manager.js" in bot_source, "Pulse shell loads upload manager")
    expect("data-upload-progress" in bot_source, "Pulse pages expose progress UI hooks")
    expect("PulseUploadManager.upload" in camera and "PulseUploadManager?.render" in camera, "camera publishing uses upload manager")
    expect("upload_progress_service.stage_upload" in bot_source, "Pulse media upload endpoint uses progress service")
    stages = upload_progress_service.default_stages("video")
    expect(stages[0]["stage"] == "starting", "service stage starts at upload starting")
    expect(any(s["stage"] == "uploading" for s in stages), "service includes uploading stage")
    expect(any(s["stage"] == "processing" for s in stages), "service includes processing stage")
    expect(any(s["stage"] == "publishing" for s in stages), "service includes publishing stage")
    expect(stages[-1]["message"] == "Posted successfully", "service stage ends with success confirmation")
    print("upload progress audit ok")


if __name__ == "__main__":
    main()
