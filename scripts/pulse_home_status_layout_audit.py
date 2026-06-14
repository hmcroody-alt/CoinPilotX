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
        "pulse_status_active_rows",
        "pulse_status_items_for_lane",
        "pulse_status_home_card_html",
        "pulse-status-tray-only",
        "href='/pulse/status'",
        "pulse-status-mini-rail",
        "data-status-strip",
        "/api/pulse/status/rail?lane=for_you",
        "id=\"pulseStatusStoryViewer\"",
        "data-status-story-media",
        "openStatusViewerFeed('global'",
        "data-open-status-id",
        "data-status-id",
        "data-status-open-url",
        "aria-label='Open",
        "data-pulse-instant-core",
        "tap_received",
        "visual_feedback_shown",
        "route_transition_started",
        "route_content_visible",
        "pointerdown",
        "data-status-story-react",
        "__pulseInstantMarks",
        "author_avatar_url",
    ]:
        expect(token in source, f"Home Status rail includes {token}")

    for forbidden in ["Stories from your Pulse world.", "Trending Status", "View Status</a>", "Quick updates, creator moments"]:
        expect(forbidden not in source, f"Home Status tray removed marketing text: {forbidden}")
    for token in ["pulse-status-tray-only", "overflow-x: auto", "grid-auto-columns: 92px", ".pulse-status-card-media", "-webkit-line-clamp: 3", ".pulse-status-avatar-ring img", "pulse-instant-pressed", "touch-action: manipulation", "pointer-events: none"]:
        expect(token in status_css, f"Home Status tray layout includes {token}")
    expect(".pulse-status-story-viewer" in status_css and ".pulse-status-story-actions" in status_css, "Homepage status viewer uses full-screen story CSS")
    expect("@media(prefers-reduced-motion:reduce)" in compact or "@media(prefers-reduced-motion:reduce)" in compact, "Reduced motion guard exists")


if __name__ == "__main__":
    main()
