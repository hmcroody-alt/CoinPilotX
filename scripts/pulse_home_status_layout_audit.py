#!/usr/bin/env python3
"""Audit Pulse home Status tray layout."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    status_css = STATUS_CSS.read_text(encoding="utf-8")
    compact = "".join(status_css.split())

    for token in [
        "pulse_status_rail_html",
        "pulse-status-tray-only",
        "href='/pulse/status'",
        "pulse-status-mini-rail",
        "data-status-strip",
        "id=\"pulseStatusStoryViewer\"",
        "data-status-story-media",
        "openStatusViewerFeed('global'",
        "data-open-status-id",
    ]:
        expect(token in source, f"Home Status rail includes {token}")

    for forbidden in ["Stories from your Pulse world.", "Trending Status", "View Status</a>", "Quick updates, creator moments"]:
        expect(forbidden not in source, f"Home Status tray removed marketing text: {forbidden}")
    for token in ["pulse-status-tray-only", "overflow-x: auto", "grid-auto-columns: 92px", ".pulse-status-card-media", "-webkit-line-clamp: 3"]:
        expect(token in status_css, f"Home Status tray layout includes {token}")
    expect(".pulse-status-story-viewer" in status_css and ".pulse-status-story-actions" in status_css, "Homepage status viewer uses full-screen story CSS")
    expect("@media(prefers-reduced-motion:reduce)" in compact or "@media(prefers-reduced-motion:reduce)" in compact, "Reduced motion guard exists")


if __name__ == "__main__":
    main()
