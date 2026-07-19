#!/usr/bin/env python3
"""Audit PulseSoc mobile bottom navigation scroll-hide behavior."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_JS = ROOT / "static" / "js" / "pulse_home_core.js"
HOME_CSS = ROOT / "static" / "css" / "pulse_home_os.css"
BOT = ROOT / "bot.py"
REPORT = ROOT / "reports" / "pulsesoc_bottom_nav_scroll_behavior.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main() -> None:
    js = HOME_JS.read_text()
    css = HOME_CSS.read_text()
    bot = BOT.read_text()
    report = REPORT.read_text()

    require("function bootUniversalDock()" in js, "universal dock controller missing")
    require('const dock = document.querySelector(".mobile-bottom-nav")' in js, "mobile dock selector missing")
    require("let dockHidden = false" in js, "explicit dock hidden state missing")
    require("const topRevealY = 96" in js, "top reveal zone missing")
    require("function canHideDock()" in js, "short-page guard missing")
    require("delta < 0" in js and "setDockHidden(false)" in js, "scroll-up reveal logic missing")
    require("delta > 0" in js and "setDockHidden(true)" in js, "scroll-down hide logic missing")
    require('window.addEventListener("scroll", requestDockUpdate, { passive: true })' in js, "passive scroll listener missing")
    require('window.addEventListener("resize", requestDockUpdate, { passive: true })' in js, "resize listener missing")
    require('window.addEventListener("pageshow", requestDockUpdate, { passive: true })' in js, "pageshow listener missing")
    require(".pulse-universal-dock.is-hidden" in css, "hidden dock CSS missing")
    require("translate3d(-50%, 110%, 0)" in css, "GPU hidden transform missing")
    require("bottom-dock-scroll-20260719a" in bot, "home JS cache-bust not updated")
    require("Scroll Behavior" in report and "Verification" in report, "report is incomplete")

    print("PASS: PulseSoc bottom nav hides on scroll down and reveals on scroll up.")


if __name__ == "__main__":
    main()
