#!/usr/bin/env python3
"""Audit single-mode Communications V2 mobile chat layout."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
css = (root / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")
html = (root / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")
js = (root / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")

for token in ['data-mobile-mode="list"', 'data-mobile-mode="thread"', "height: 100dvh", ".composer", "position: sticky", "data-conversation-search"]:
    assert token in css or token in html or token in js, token
assert "comm-details" not in html
assert 'class="desktop-only" type="button" data-open-new-room' in html
print("pulse comm v2 mobile clean chat audit ok")
