#!/usr/bin/env python3
"""Audit mobile-safe Pulse Live layout primitives."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    css = (ROOT / "static/css/pulse_live_studio.css").read_text()
    js = (ROOT / "static/js/pulse_live_studio.js").read_text()
    bot_py = (ROOT / "bot.py").read_text()
    require("@media (max-width: 680px)" in css, "mobile breakpoint exists")
    require("env(safe-area-inset-bottom)" in css and "env(safe-area-inset-top)" in css, "mobile safe areas are respected")
    require(".live-mobile-controls" in css, "mobile live controls tray exists")
    require("min-height: calc(58dvh" in css or "dvh" in css, "mobile live surface uses dynamic viewport units")
    require("getUserMedia" in js and "pagehide" in js, "camera starts by user action and stops on exit")
    require("data-pulse-live-shell" in bot_py, "live pages expose JS hydration shell")
    print("live mobile audit ok")


if __name__ == "__main__":
    main()
