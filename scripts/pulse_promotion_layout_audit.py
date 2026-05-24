#!/usr/bin/env python3
"""Audit Pulse desktop feed width and promotion rail primitives."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_desktop_feed.css"


def require(text, needle, label):
    if needle not in text:
        raise SystemExit(f"missing {label}: {needle}")


def main():
    bot = BOT.read_text()
    css = CSS.read_text()
    combined = bot + "\n" + css
    for needle, label in [
        ("def promotion_card", "reusable promotion_card"),
        ("def creator_promo_card", "creator promo card"),
        ("def premium_promo_card", "premium promo card"),
        ("def marketplace_promo_card", "marketplace promo card"),
        ("def intelligence_promo_card", "intelligence promo card"),
        ("data-promotion-card", "promotion render marker"),
        ("pulse-promo-card", "promotion CSS"),
        ("pulse-desktop-left", "left rail"),
        ("pulse-desktop-right", "right rail"),
        ("pulse-desktop-layout", "desktop shell"),
        ("@media (max-width: 1023px)", "mobile/tablet rail collapse"),
        ("overflow-x: hidden", "horizontal scroll guard"),
    ]:
        require(combined, needle, label)
    feed_values = [int(v) for v in re.findall(r"--pulse-feed-column:\s*clamp\((\d+)px", css)]
    if not feed_values or max(feed_values) < 700:
        raise SystemExit("desktop feed width did not increase to at least 700px")
    if "--pulse-cinematic-column" not in css or "900px" not in css:
        raise SystemExit("cinematic media width target is missing")
    print("pulse promotion layout audit ok")


if __name__ == "__main__":
    main()
