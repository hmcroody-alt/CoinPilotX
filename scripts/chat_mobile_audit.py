#!/usr/bin/env python3
"""Pulse chat mobile UX resilience audit."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    recovery_js = (ROOT / "static/js/pulse_chat_recovery.js").read_text(encoding="utf-8")
    expect("100dvh" in bot_source, "mobile chat uses dynamic viewport sizing")
    expect("env(safe-area-inset-bottom)" in bot_source, "mobile chat composer respects safe area")
    expect("pulse-chat-skeleton" in recovery_js and "Loading conversation" in recovery_js, "chat recovery skeleton exists")
    expect("renderThreadCache" in recovery_js, "cached thread renderer exists")
    expect("Choose a chat first." in bot_source, "composer stays disabled until conversation is valid")
    expect("typing" in bot_source.lower(), "typing indicators remain wired")
    print("chat mobile audit ok")


if __name__ == "__main__":
    main()
