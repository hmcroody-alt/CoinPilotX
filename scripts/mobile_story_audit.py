#!/usr/bin/env python3
"""Audit mobile-first Pulse Status viewer and gestures contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(ok: bool, label: str):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pulse_status_system.css").read_text(encoding="utf-8")
    expect("data-status-story-viewer" in source, "fullscreen story viewer exists")
    expect("data-story-progress" in source, "viewer has story progress bars")
    expect("data-status-story-prev" in source and "data-status-story-next" in source, "viewer has tap left/right navigation")
    expect("data-status-story-close" in source, "viewer has close/swipe-down target")
    expect("data-status-story-reply" in source, "viewer has reply field")
    expect("data-status-story-react" in source, "viewer has reaction tray")
    expect("data-status-story-mute" in source, "viewer has mute/unmute behavior")
    expect("100dvh" in css and "env(safe-area-inset-bottom)" in css, "viewer is mobile safe-area aware")
    expect("touch-action: pan-y" in css, "viewer supports mobile gesture-friendly touch behavior")
    print("mobile story audit ok")


if __name__ == "__main__":
    main()
