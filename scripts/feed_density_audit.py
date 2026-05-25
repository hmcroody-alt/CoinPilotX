#!/usr/bin/env python3
"""Audit Pulse feed density: wide desktop cards, compact height, and scan-friendly rows."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static" / "css" / "pulse_desktop_feed.css"


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def first_px(css: str, name: str) -> int:
    match = re.search(rf"{re.escape(name)}:\s*(?:clamp\()?(\d+)px", css)
    return int(match.group(1)) if match else 0


def main():
    css = CSS.read_text()
    require(first_px(css, "--pulse-feed-column") >= 1280, "feed column starts at a wide desktop size")
    require(first_px(css, "--pulse-text-column") >= 1280, "text/card column is wide enough for desktop feed cards")
    require(first_px(css, "--pulse-cinematic-column") >= 1360, "media posts can become cinematic without vertical stretching")
    require("minmax(0, var(--pulse-feed-column))" in css, "center lane is flexible and avoids horizontal scroll")
    require(".pulse-desktop-center > .layout" in css and ".pulse-desktop-center > .layout > aside.side" in css, "inner desktop feed column is not squeezed by old sidebar layout")
    require(".feed" in css and "gap: 11px" in css, "feed spacing is compact")
    require(".composer.card" in css and "min-height: 72px" in css, "composer height is reduced")
    require(".post.card" in css and "padding: clamp(14px" in css, "post cards use compact vertical padding")
    require("contain-intrinsic-size: 430px" in css, "standard post intrinsic height is compact")
    require("contain-intrinsic-size: 540px" in css, "media post intrinsic height is compact")
    require("-webkit-line-clamp: 7" in css, "long text is clamped to 5-8 visible lines")
    require("max-height: min(62vh, 620px)" in css, "default media height is capped for density")
    require("max-height: 58px" in css and ".post [data-post-tags]" in css, "tag rows are capped to two compact rows")
    require(".quick-action" in css and "min-height: 36px" in css, "action row is compact")
    print("feed density audit ok")


if __name__ == "__main__":
    main()
