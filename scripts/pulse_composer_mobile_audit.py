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
        "composer-type-row",
        "composer-primary-actions",
        "Optional description",
        "composer-publish-button",
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
        "position:static",
        "top:calc(62px + env(safe-area-inset-top))",
        ".pulse-media-preview",
        "data-composer-media-state",
    ]:
        expect(token in css or token in source, f"Mobile composer guard includes {token}")


if __name__ == "__main__":
    main()
