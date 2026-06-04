#!/usr/bin/env python3
"""Audit five-destination Pulse mobile navigation."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
assert "grid-template-columns:repeat(5,minmax(0,1fr))" in S
for token in ['("Home", "/pulse", "⌂")', '("Videos", "/pulse/videos", "▶")', '("Create", "/pulse/create", "＋")', '("Messages", "/pulse/messages", "chat")', '("Profile", "/pulse/profile", "◉")']:
    assert token in S, token
    print("PASS:", token)
print("pulse mobile nav audit ok")
