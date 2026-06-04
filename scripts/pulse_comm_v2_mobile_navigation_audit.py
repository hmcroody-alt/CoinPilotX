#!/usr/bin/env python3
"""Audit mobile list/thread/create navigation state."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
js = (root / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
html = (root / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
for token in ['setMobileMode("list")', 'setMobileMode("thread")', 'setMobileMode("create")', "data-mobile-list", "Back to conversations"]:
    assert token in js or token in html, token
assert "selectFirst = !isMobile()" in js
print("pulse comm v2 mobile navigation audit ok")
