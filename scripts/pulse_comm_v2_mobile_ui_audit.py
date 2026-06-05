#!/usr/bin/env python3
"""Audit Communications V2 mobile chat polish."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")
HTML = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


mobile = CSS[CSS.rfind("@media (max-width: 768px)") :]
require("height: 100dvh" in mobile, "mobile uses full viewport chat height")
require('data-mobile-mode="list"' in CSS and 'data-mobile-mode="thread"' in CSS, "mobile list/thread modes are mutually exclusive")
require(".comm-filter" in mobile and "display: none" in mobile, "mobile hides conversation filters in chat flow")
require(".comm-actions .desktop-only" in CSS, "mobile hides New Room desktop-only action")
require("bottom: 0" in CSS and "env(safe-area-inset-bottom)" in CSS, "mobile composer is bottom/safe-area aware")
require("grid-template-columns: 42px minmax(0, 1fr) 42px 46px" in mobile, "mobile composer uses plus/input/mic/send layout")
require("overflow-x: hidden" in CSS, "mobile layout prevents horizontal overflow")
require('data-open-new-room' in HTML and 'desktop-only' in HTML, "New Room remains desktop-only")

print("pulse comm v2 mobile ui audit ok")
