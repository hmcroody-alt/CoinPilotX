#!/usr/bin/env python3
"""Audit Pulse Camera fallback picker, entry points, and upload routing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    js = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")

    for token in ["pulseCameraFile", "data-device-file-picker-fallback", "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime"]:
        expect(token in source, f"fallback picker HTML contains {token}")
    for token in ["activateFallbackPicker", "device_file_picker", "configEndpoint", "hydrateCameraConfig"]:
        expect(token in js + source, f"fallback runtime contains {token}")
    for route in ["/pulse/camera/status", "/pulse/camera/reel", "/pulse/camera/post"]:
        expect(route in source, f"camera entry route wired: {route}")
    expect("openStatusCameraCreator" in source and "capture','environment" in source, "Status creator has in-place camera capture entry")
    expect("location.href='/pulse/camera/reel'" in source, "Reels camera entry uses shared camera route")
    expect("location.href='/pulse/camera/status'" in source, "Legacy Status camera route remains compatible")
    expect("location.href = `/pulse?status=${encodeURIComponent(data.status?.id || \"\")}`" in js, "Status camera publish returns to Home deep link")
    expect("/api/pulse/media/upload" in js and "config.uploadEndpoint" in js, "camera uploads use shared media pipeline")
    expect("/api/pulse/status" in js, "Status publish path is present")
    expect("/api/pulse/reels/create" in js, "Reels publish path is present")
    expect("/api/pulse/posts/create-from-camera" in js, "feed post publish path is present")
    print("pulse camera fallback audit ok")


if __name__ == "__main__":
    main()
