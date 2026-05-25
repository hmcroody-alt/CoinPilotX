#!/usr/bin/env python3
"""Consolidated audit for Pulse Camera capture, preview, upload, and publish flows."""

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
    run("pulse_camera_audit.py")
    run("pulse_camera_engine_audit.py")
    run("pulse_camera_gesture_audit.py")
    run("pulse_camera_media_pipeline_audit.py")
    run("pulse_camera_publish_audit.py")
    source = (ROOT / "bot.py").read_text()
    require("/api/pulse/posts/create-from-camera" in source, "Camera can publish Pulse posts")
    require("/api/pulse/status" in source and "/api/pulse/reels/create" in source, "Camera can publish Status and Reels")
    require("navigator.mediaDevices.getUserMedia" in source, "Camera requests real capture permissions")
    require("data-upload-progress" in source or "PulseUploadManager" in source, "Camera publish flows expose progress/retry infrastructure")
    print("camera publish audit ok")


if __name__ == "__main__":
    main()
