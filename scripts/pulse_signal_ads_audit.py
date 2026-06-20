#!/usr/bin/env python3
"""Audit PulseSoc sci-fi sponsored signal placeholders.

This keeps the Home ad placeholders lightweight, wired, and privacy-safe while
the real ads system is still pending.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static" / "css" / "pulse_home_os.css"
JS = ROOT / "static" / "js" / "pulse_home_core.js"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    bot = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")

    required_bot_markers = [
        "pulse_signal_ad_card",
        "Sponsored Signal",
        "Marketplace Signal",
        "data-sponsored-signal",
        "data-hide-sponsored-signal",
        "/pulse/creator/dashboard",
        "/pulse/premium",
        "/pulse/marketplace",
        "/privacy#advertising",
        "Why this signal?",
    ]
    for marker in required_bot_markers:
        require(marker in bot, f"Missing Home sponsored signal marker: {marker}")

    required_css_markers = [
        ".pulse-signal-ad",
        ".pulse-signal-ad-visual",
        "@keyframes pulseSignalAdSweep",
        "@keyframes pulseSignalAdAura",
        "@keyframes pulseSignalNode",
        "prefers-reduced-motion: reduce",
        ".pulse-home-os .pulse-desktop-right > .pulse-signal-ad",
        ".pulse-home-os .pulse-desktop-left > .pulse-signal-ad-left",
    ]
    for marker in required_css_markers:
        require(marker in css, f"Missing sponsored signal CSS marker: {marker}")

    required_js_markers = [
        "bootSponsoredSignals",
        "[data-hide-sponsored-signal]",
        "[data-sponsored-signal]",
        "pulse_hide_sponsor_",
        "sessionStorage.setItem",
    ]
    for marker in required_js_markers:
        require(marker in js, f"Missing active Home sponsored signal JS marker: {marker}")

    forbidden = [
        "googlesyndication",
        "doubleclick",
        "adservice.google",
        "eval(",
        "innerHTML = location",
        "document.write(",
    ]
    combined = f"{bot}\n{css}\n{js}".lower()
    for marker in forbidden:
        require(marker.lower() not in combined, f"Forbidden ad/security pattern found: {marker}")

    require("sessionStorage.setItem(key, \"1\")" in js, "Dismiss action is not persisted per session.")
    require("pointer-events: none" in css, "Decorative ad animation layers must not intercept touches.")
    require("display: none;" in css and ".pulse-desktop-right" in css, "Desktop rail mobile-hide behavior missing.")

    print("Pulse signal ads audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
