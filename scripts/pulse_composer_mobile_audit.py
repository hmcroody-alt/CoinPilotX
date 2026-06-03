#!/usr/bin/env python3
"""Audit Pulse mobile composer ergonomics."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_desktop_feed.css"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    for token in [
        "pulseComposer",
        "composer-primary-actions",
        "data-open-media",
        "data-composer-reel",
        "data-composer-music",
        "data-composer-audience",
        "postMediaPreview",
        "data-upload-progress",
        "Write something or attach media before publishing.",
    ]:
        expect(token in source, f"Composer markup/logic includes {token}")

    for token in [
        ".composer-primary-actions",
        "overflow-x: auto",
        ".composer.is-expanded .composer-advanced",
        "bottom:calc(76px + env(safe-area-inset-bottom))",
        ".pulse-media-preview",
        "data-composer-media-state",
    ]:
        expect(token in css or token in source, f"Mobile composer guard includes {token}")


if __name__ == "__main__":
    main()
