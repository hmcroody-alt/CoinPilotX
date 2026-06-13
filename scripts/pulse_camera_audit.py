#!/usr/bin/env python3
"""Audit Pulse Camera capture routes, media integration, and publish targets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def table_exists(cur, table):
    try:
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def main():
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    for table in ["pulse_camera_captures", "pulse_camera_effects", "pulse_media_assets", "pulse_status", "pulse_reels"]:
        expect(table_exists(cur, table), f"{table} exists")
    conn.close()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    js = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/pulse_camera_engine.css").read_text(encoding="utf-8")
    for token in ["/pulse/camera", "pulse-camera-engine", "pulse_lens_engine", "pulse_camera_captures", "pulse_media_assets", "/api/pulse/media/upload"]:
        expect(token in source, f"camera integration token present: {token}")
    for token in ["navigator.mediaDevices.getUserMedia", "MediaRecorder", "data-preview-destination=\"status\"", "pulseCameraConfig"]:
        expect(token in source + js, f"camera engine token present: {token}")
    for token in [
        "width: { ideal: 1920 }",
        "height: { ideal: 1080 }",
        "width: { ideal: 1280 }",
        "height: { ideal: 720 }",
        "safeTrackSettings",
        "maskDeviceId",
        "Banuba Active",
        "Banuba Failed / Using Native Camera",
        "Camera HD Active",
        "video.style.objectFit = \"cover\"",
    ]:
        expect(token in js + source, f"camera quality token present: {token}")
    for token in ["100dvh", "env(safe-area-inset-bottom", "pulse-camera-capture", "pulse-camera-lenses"]:
        expect(token in css, f"camera CSS token present: {token}")
    client = bot.webhook_app.test_client()
    response = client.get("/pulse/camera?target=status")
    expect(response.status_code in {200, 302}, "camera route loads or redirects to login")
    print("pulse camera audit ok")


if __name__ == "__main__":
    main()
