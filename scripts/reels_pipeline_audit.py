#!/usr/bin/env python3
"""Consolidated audit for Reels loading, upload, media, layout, and playback."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(script: str):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="")
        raise AssertionError(f"{script} failed")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    run("reels_mobile_audit.py")
    run("reels_layout_audit.py")
    run("reels_media_load_audit.py")
    run("reels_experience_audit.py")
    run("pulse_video_upload_audit.py")
    source = (ROOT / "bot.py").read_text()
    require("/api/pulse/reels/create" in source, "Reels publish endpoint is wired")
    require("PulseUploadManager.upload" in source, "Reels upload uses progress manager")
    require("playsinline" in source and "preload=\"metadata\"" in source or "preload='metadata'" in source, "Reels/live video markup is mobile playback safe")
    print("reels pipeline audit ok")


if __name__ == "__main__":
    main()
