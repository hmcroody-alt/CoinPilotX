#!/usr/bin/env python3
"""Audit owner-only controls on the Pulse Videos surface."""

from pathlib import Path

BOT = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")

for token in [
    "video-owner-trigger",
    "data-edit-video",
    "data-delete-video",
    "data-copy-video",
    "Delete this video? This cannot be undone.",
    "v.is_owner",
    'video["is_owner"]',
]:
    assert token in BOT, f"missing owner control: {token}"
    print(f"PASS: {token}")

assert "Thumbnail changes will be available" in BOT
print("pulse video owner controls audit ok")
