#!/usr/bin/env python3
"""Audit the Pulse Camera engine shell, permission policy, and publish paths."""

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


def main():
    bot.init_db()
    client = bot.webhook_app.test_client()
    response = client.get("/pulse/camera?target=status")
    expect(response.status_code in {200, 302}, "camera route responds")
    if response.status_code == 200:
        html = response.get_data(as_text=True)
        headers = response.headers
        expect("camera=(self)" in headers.get("Permissions-Policy", ""), "camera permissions are allowed on camera route")
        for token in [
            "pulse-camera-engine",
            "pulseCameraPreview",
            "pulseCameraCapture",
            "pulseCameraFlip",
            "data-publish-destination=\"status\"",
            "data-publish-destination=\"reel\"",
            "data-publish-destination=\"feed\"",
            "pulseCameraConfig",
        ]:
            expect(token in html, f"camera HTML contains {token}")
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("camera=(), microphone=()" in source, "non-camera routes deny camera/mic by default")
    expect("camera=(self), microphone=(self)" in source, "camera route grants camera/mic")
    print("pulse camera engine audit ok")


if __name__ == "__main__":
    main()
