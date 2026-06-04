#!/usr/bin/env python3
"""Audit mobile usability of video owner controls."""

from pathlib import Path

BOT = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")

for token in [
    "@media(max-width:680px)",
    ".video-manage-modal{{padding:0;align-items:end}}",
    "padding-bottom:calc(20px + env(safe-area-inset-bottom))",
    ".video-owner-menu",
    "max-height:94dvh",
]:
    assert token in BOT, f"missing mobile owner control rule: {token}"
    print(f"PASS: {token}")

print("pulse video mobile owner controls audit ok")
