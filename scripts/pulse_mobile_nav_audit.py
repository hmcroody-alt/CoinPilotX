#!/usr/bin/env python3
"""Audit Pulse mobile navigation contracts."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
assert "grid-template-columns:repeat(5,minmax(0,1fr))" in S
assert "grid-template-columns:repeat(7,minmax(0,1fr))" in S
for token in [
    '("Home", "/pulse", "⌂")',
    '("Reels", "/pulse/reels", "▶")',
    '("Music", "/pulse/music", "♪")',
    '("Videos", "/pulse/videos", "▣")',
    '("Alerts", "/pulse/notifications", "!")',
    '("Chats", "/pulse/messages", "chat")',
    '("Profile", "/pulse/profile", "◉")',
    '("Create", "/pulse/create", "＋")',
    '("Messages", "/pulse/messages", "chat")',
]:
    assert token in S, token
    print("PASS:", token)
print("pulse mobile nav audit ok")
