#!/usr/bin/env python3
"""Audit mobile/PWA shell, safe-area, service worker, and touch-first layout contracts."""

from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot_source = (ROOT / "bot.py").read_text()
    manifest_path = ROOT / "static" / "manifest.json"
    sw_path = ROOT / "static" / "service-worker.js"
    mobile_css = (ROOT / "static" / "css" / "pulse_mobile_system.css").read_text()
    live_css = (ROOT / "static" / "css" / "pulse_live_studio.css").read_text()
    reels_css = (ROOT / "static" / "css" / "pulse_reels_experience.css").read_text()
    require(manifest_path.exists(), "PWA manifest exists")
    manifest = json.loads(manifest_path.read_text())
    require(manifest.get("display") in {"standalone", "fullscreen", "minimal-ui"}, "PWA display mode is mobile-app friendly")
    require(sw_path.exists() and "self.addEventListener" in sw_path.read_text(), "service worker exists and registers lifecycle handlers")
    require("env(safe-area-inset-bottom)" in bot_source + mobile_css + live_css + reels_css, "mobile safe-area bottom handling exists")
    require("env(safe-area-inset-top)" in bot_source + mobile_css + live_css + reels_css, "mobile safe-area top handling exists")
    require("100dvh" in bot_source + mobile_css + reels_css, "dynamic viewport units are used for mobile shells")
    require("mobile-bottom-nav" in bot_source, "mobile bottom navigation exists")
    require("data-live-unmute" in bot_source, "mobile Safari audio unlock exists for live playback")
    require("data-upload-progress" in bot_source, "mobile upload progress hooks exist")
    require("Something needs attention. Please try again." not in bot_source, "generic dead mobile error copy is removed")
    print("mobile PWA audit ok")


if __name__ == "__main__":
    main()
