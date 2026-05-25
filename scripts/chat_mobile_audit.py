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
    expect("@media(max-width:1024px)" in bot_source, "Messenger has tablet responsive breakpoint")
    expect("@media(max-width:768px)" in bot_source, "Messenger has mobile responsive breakpoint")
    expect("body:has(.unified-messenger){max-width:100vw;overflow-x:hidden}" in bot_source or "max-width:100vw!important;overflow:hidden" in bot_source, "Messenger prevents horizontal overflow")
    expect("messenger-mode-rail" in bot_source and "display:none!important" in bot_source, "Messenger side mode rail is hidden on smaller screens")
    expect(".unified-tabs{display:flex" in bot_source and "overflow-x:auto" in bot_source, "Messenger tabs compact and horizontally scrollable")
    expect(".unified-row{min-height:54px" in bot_source and ".unified-row span{font-size:11px" in bot_source, "mobile conversation cards are compact")
    expect("white-space:nowrap;overflow:hidden;text-overflow:ellipsis" in bot_source, "conversation names and previews are clamped")
    expect(".unified-composer{grid-template-columns:34px minmax(0,1fr) 42px" in bot_source, "mobile composer is compact")
    expect(".unified-composer textarea{min-height:34px" in bot_source, "mobile composer input height stays reasonable")
    expect(".unified-attach{width:34px!important" in bot_source, "mobile chat action buttons are compact")
    expect("pulse-chat-skeleton" in recovery_js and "Loading conversation" in recovery_js, "chat recovery skeleton exists")
    expect("renderThreadCache" in recovery_js, "cached thread renderer exists")
    expect("Choose a chat first." in bot_source, "composer stays disabled until conversation is valid")
    expect("typing" in bot_source.lower(), "typing indicators remain wired")
    print("chat mobile audit ok")


if __name__ == "__main__":
    main()
