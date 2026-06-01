#!/usr/bin/env python3
"""Audit the Pulse Reels mobile shell contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_reels_experience.css"


def require(text, needle, label):
    if needle not in text:
        raise SystemExit(f"missing {label}: {needle}")


def main():
    bot = BOT.read_text()
    css = CSS.read_text()
    combined = bot + "\n" + css
    checks = [
        ("data-reels-mobile-shell", "mobile shell marker"),
        ("data-reels-topbar", "topbar marker"),
        ("data-reels-tabs", "tabs marker"),
        ("data-reels-media-stage", "media stage marker"),
        ("data-reels-action-rail", "action rail marker"),
        ("data-reels-caption-overlay", "caption overlay marker"),
        ("data-reels-desktop-sidebar", "desktop sidebar marker"),
        ("data-reel-details-panel", "desktop details panel marker"),
        ("reel-details-panel", "desktop details panel class"),
        ("reel-comments-preview", "desktop comments preview"),
        ("reel-inline-comment", "desktop inline comment input"),
        ("reel-center-play", "custom play control"),
        ("reel-sound-float", "custom sound control"),
        ("100dvh", "dynamic viewport height"),
        ("--safe-top", "safe top variable"),
        ("--reels-topbar-height", "topbar height variable"),
        ("--reels-tabs-height", "tabs height variable"),
        ("--reels-bottom-safe", "bottom safe variable"),
        ("scroll-snap-type:y mandatory", "snap scrolling"),
        ("data-orientation", "adaptive orientation metadata"),
        ("object-fit: contain", "adaptive fit contain"),
        ("object-fit: cover", "adaptive fit cover"),
        ("reels-desktop-intel", "desktop separation"),
        ("grid-template-columns:minmax(320px", "desktop stage/details grid"),
        ("overflow-x: hidden", "horizontal overflow safeguard"),
    ]
    for needle, label in checks:
        require(combined, needle, label)
    print("reels layout audit ok")


if __name__ == "__main__":
    main()
