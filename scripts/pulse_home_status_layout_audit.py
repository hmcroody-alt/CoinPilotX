#!/usr/bin/env python3
"""Audit Pulse home Status rail layout and hero animation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_desktop_feed.css"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    compact = "".join(css.split())

    for token in [
        "pulse_status_rail_html",
        "data-status-home-create",
        "href='/pulse/status'",
        "href='/pulse/status?lane=trending'",
        "pulse-status-mini-rail",
        "data-status-strip",
    ]:
        expect(token in source, f"Home Status rail includes {token}")

    for token in [
        ".pulse-home-status-rail .pulse-status-mini-rail",
        "overflow-x: auto",
        "grid-auto-columns",
        ".pulse-home-status-rail .pulse-status-home-actions .button",
        "white-space: nowrap",
    ]:
        expect(token in css, f"Home Status rail layout includes {token}")

    for token in ["pulseHomeDotOne", "pulseHomeDotTwo", "pulseHomeDotThree"]:
        expect(token in css, f"Pulse hero orbit dot animation exists: {token}")
    expect("@media(prefers-reduced-motion:reduce)" in compact or "@media(prefers-reduced-motion:reduce)" in compact, "Reduced motion guard exists")


if __name__ == "__main__":
    main()
