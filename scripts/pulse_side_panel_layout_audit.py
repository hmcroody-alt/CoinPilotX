#!/usr/bin/env python3
"""Audit Pulse desktop side-panel and mobile clutter controls."""

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
    for token in [
        "desktop_left_rail_html",
        "desktop_right_rail_html",
        "desktop-module-link",
        "desktop-rail-card",
        "pulse_social_shell",
        "mobile-bottom-nav",
    ]:
        expect(token in source or token in css, f"Side/navigation surface includes {token}")

    for token in [
        ".side{position:static!important}",
        ".layout{grid-template-columns:1fr!important",
        "overflow-x:hidden",
        ".pulse-actions .button,.pulse-actions button{width:100%",
    ]:
        expect(token in source or token in css, f"Mobile side-panel guard includes {token}")

    expect("Coming Soon" not in source[source.find('def pulse_messages_v2_page') : source.find('@webhook_app.route(\"/api/pulse/communications/v2/health\"') if '@webhook_app.route(\"/api/pulse/communications/v2/health\"' in source else len(source)], "Messages V2 page does not render placeholder Coming Soon panels")


if __name__ == "__main__":
    main()
