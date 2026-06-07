#!/usr/bin/env python3
"""Audit PulseSoc PWA manifest installability basics."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    manifest_path = ROOT / "static" / "manifest.json"
    require(manifest_path.exists(), "manifest.json exists")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("name") == "PulseSoc", "manifest uses PulseSoc name")
    require(manifest.get("short_name") == "PulseSoc", "manifest uses PulseSoc short name")
    require(manifest.get("start_url"), "manifest has start_url")
    require(manifest.get("scope") == "/", "manifest scope covers app")
    require(manifest.get("display") in {"standalone", "fullscreen"}, "manifest display is standalone or fullscreen")
    require(manifest.get("theme_color"), "manifest has theme color")
    icons = manifest.get("icons") or []
    require(any(icon.get("sizes") == "192x192" for icon in icons), "manifest has 192 icon")
    require(any(icon.get("sizes") == "512x512" for icon in icons), "manifest has 512 icon")
    require(any("maskable" in str(icon.get("purpose", "")) for icon in icons), "manifest has maskable icon")
    for icon in icons:
        src = str(icon.get("src") or "")
        if src.startswith("/static/"):
            require((ROOT / src.lstrip("/")).exists(), f"manifest icon exists: {src}")
    print("pulsesoc PWA manifest audit ok")


if __name__ == "__main__":
    main()
