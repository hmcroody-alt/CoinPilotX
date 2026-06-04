#!/usr/bin/env python3
"""Audit message reactions are menu-driven rather than permanently visible."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
js = (root / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
css = (root / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")
assert 'data-message-actions="${item.id}"' in js
assert 'data-reaction-menu="${item.id}" hidden' in js
assert ".reaction-row[hidden]" in css
assert "messageActions" in js
print("pulse comm v2 reaction menu audit ok")
