#!/usr/bin/env python3
"""Audit Pulse mobile composer ergonomics."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_desktop_feed.css"
PREMIUM_CSS = ROOT / "static/css/pulse_composer_premium.css"
CORE_JS = ROOT / "static/js/pulse_home_core.js"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    premium_css = PREMIUM_CSS.read_text(encoding="utf-8")
    core_js = CORE_JS.read_text(encoding="utf-8")

    for token in [
        "pulseComposer",
        "pulse-composer-command",
        "✨ PulseSoc Composer",
        "pulse-live-menu",
        "composer-type-row",
        "composer-primary-actions",
        "Share something with the PulseSoc community...",
        "What would you like to ask?",
        "Describe the scam, warning, or suspicious activity...",
        "Add a caption for your Reel...",
        "composer-publish-button",
        "data-open-media",
        "data-composer-reel",
        "data-composer-music",
        "data-composer-audience",
        "postMediaPreview",
        "data-upload-progress",
        "Write something or attach media before publishing.",
    ]:
        expect(token in source or token in core_js, f"Composer markup/logic includes {token}")

    for token in [
        ".composer-primary-actions",
        ".pulse-action-card-grid",
        ".pulse-smart-field",
        ".pulse-live-menu",
        "overflow-x: auto",
        ".composer.is-expanded .composer-advanced",
        "position:static",
        "top:calc(62px + env(safe-area-inset-top))",
        ".pulse-media-preview",
        "data-composer-media-state",
    ]:
        expect(token in css or token in premium_css or token in source or token in core_js, f"Mobile composer guard includes {token}")


if __name__ == "__main__":
    main()
