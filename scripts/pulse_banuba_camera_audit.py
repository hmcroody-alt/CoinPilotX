#!/usr/bin/env python3
"""Audit the safe Banuba/Pulse Camera foundation contract."""

from __future__ import annotations

import json
import os
import re
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
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    camera_js = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")
    static_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in (ROOT / "static").rglob("*") if path.is_file() and path.suffix in {".js", ".css", ".html"})

    expect('os.getenv("PULSE_CAMERA_ENABLED", "false")' in source, "PULSE_CAMERA_ENABLED defaults false")
    expect("banuba_token_present" in source, "safe Banuba token-present diagnostic exists")
    expect('os.getenv("BANUBA_TOKEN")' in source, "BANUBA_TOKEN is read only on backend")
    expect("BANUBA_TOKEN" not in camera_js and "BANUBA_TOKEN" not in static_text, "Banuba secret name is absent from frontend/static assets")
    expect("/api/pulse/camera/config" in source, "camera config endpoint exists")
    for route in ["/pulse/camera/status", "/pulse/camera/reel", "/pulse/camera/post"]:
        expect(route in source, f"{route} route exists")
    expect("public_client_token" in source and '"public_client_token": ""' in source, "public token field is empty")

    previous = os.environ.pop("PULSE_CAMERA_ENABLED", None)
    try:
        config = bot.pulse_camera_safe_public_config("status", "status")
    finally:
        if previous is not None:
            os.environ["PULSE_CAMERA_ENABLED"] = previous
    payload = json.dumps(config, sort_keys=True)
    expect(config["enabled"] is False, "camera feature is disabled by default")
    expect(isinstance(config["banuba"]["token_present"], bool), "Banuba token presence is boolean only")
    expect(config["banuba"]["public_client_token"] == "", "Banuba token is not exposed")
    expect(re.search(r"banuba[_-]?token[^_]", payload, re.I) is None, "public config contains no raw Banuba token key")
    expect(config["fallback"]["enabled"] is True and config["fallback"]["type"] == "device_file_picker", "device picker fallback is enabled")

    client = bot.webhook_app.test_client()
    response = client.get("/api/pulse/camera/config")
    expect(response.status_code in {200, 401}, "config endpoint responds safely")
    if response.status_code == 200:
        data = response.get_json()
        expect("BANUBA_TOKEN" not in json.dumps(data), "config response does not include secret env name")
    print("pulse Banuba camera audit ok")


if __name__ == "__main__":
    main()
