#!/usr/bin/env python3
"""Audit mobile Pulse feed density stays compact and touch-safe."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static" / "css" / "pulse_desktop_feed.css"


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def main():
    css = CSS.read_text()
    require("@media (max-width: 1023px)" in css, "mobile/tablet feed density breakpoint exists")
    require(".composer.card" in css and "padding: 13px !important" in css, "mobile composer avoids giant vertical padding")
    require(".pulse-publisher-card .smart-composer-bar textarea" in css and "min-height: 68px" in css, "mobile composer textarea is compact")
    require(".composer-primary-actions" in css and "overflow-x: auto" in css, "mobile action toolbar can scroll instead of stacking tall")
    require(".post.card" in css and "contain-intrinsic-size: 360px" in css, "mobile cards reserve compact height")
    require("-webkit-line-clamp: 6" in css, "mobile long text is clamped")
    require(".quick-action" in css and "min-height: 36px" in css, "mobile actions remain touch-safe but compact")
    require("width: 100% !important" in css and "max-width: 100% !important" in css, "mobile cards stay edge-to-edge without horizontal scroll")
    print("mobile feed density audit ok")


if __name__ == "__main__":
    main()
