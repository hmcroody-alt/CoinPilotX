#!/usr/bin/env python3
"""Audit mobile-first Pulse Status composer contract."""

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
    expect("data-status-create-form='dedicated'" in source, "dedicated mobile composer exists")
    expect("data-status2-preview" in source, "composer has shared preview area")
    expect("data-status2-type" in source, "composer has status type selector")
    expect("data-status2-cancel" in source, "composer has reset control")
    expect("data-status2-body" in source, "composer has text input")
    expect("data-status2-media" in source, "composer has media input")
    expect("data-status2-post" in source, "composer has post action")
    expect("100dvh" in css and "env(safe-area-inset-bottom)" in css, "composer is mobile safe-area aware")
    expect(".pulse-status2-strip" in css and "overflow-x: auto" in css, "mobile status rail scrolls horizontally")
    print("mobile story audit ok")


if __name__ == "__main__":
    main()
