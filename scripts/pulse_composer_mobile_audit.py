#!/usr/bin/env python3
"""Audit Pulse mobile composer ergonomics."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_desktop_feed.css"
PREMIUM_CSS = ROOT / "static/css/pulse_composer_premium.css"
SCI_FI_CSS = ROOT / "static/css/pulse_sci_fi_system.css"
CORE_JS = ROOT / "static/js/pulse_home_core.js"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    premium_css = PREMIUM_CSS.read_text(encoding="utf-8")
    sci_fi_css = SCI_FI_CSS.read_text(encoding="utf-8")
    core_js = CORE_JS.read_text(encoding="utf-8")

    composer_markup = source.split(
        '<section class="card composer pulse-publisher-card pulse-composer-command"',
        1,
    )[1].split('<section class="card"><div class="tabs"', 1)[0]

    for token in [
        "pulseComposer",
        "pulse-composer-command",
        "✨ Pulse Composer",
        "pulse-live-menu",
        "pulse_sci_fi_system.css?v=sci-fi-system-20260617a",
        "composer-type-row",
        "composer-primary-actions",
        "What’s happening in your world?",
        "Ask the PulseSoc community…",
        "Warn the community about suspicious activity…",
        "Add a caption for your Reel...",
        "Describe your live session...",
        "data-composer-ai-suggestions",
        'data-ai-suggestion="clarity"',
        "Improve wording",
        "composer-publish-button",
        "data-open-composer-picker",
        "data-composer-reel",
        "data-composer-live",
        "data-composer-music",
        "data-composer-ai",
        "data-composer-audience",
        "postMediaPreview",
        "data-upload-progress",
        "Write something or attach media before publishing.",
    ]:
        expect(token in source or token in core_js, f"Composer markup/logic includes {token}")

    expect("data-composer-enhance" not in composer_markup, "Composer markup removes dedicated Enhance button")
    expect("data-enhance-action" not in composer_markup, "Composer markup removes Enhance action panel")

    for token in [
        ".composer-primary-actions",
        ".pulse-action-card-grid",
        ".pulse-smart-field",
        ".pulse-ai-native-suggestions",
        ".pulse-live-menu",
        "overflow-x: auto",
        ".composer.is-expanded .composer-advanced",
        "position:static",
        "top:calc(62px + env(safe-area-inset-top))",
        ".pulse-media-preview",
        "data-composer-media-state",
        "syncComposerIntelligence",
        "insertComposerText",
        ".pulse-media-lightbox-stage",
        "place-items: start center",
        ".pulse-live-now-card::before",
        ".post-action-button::before",
        ".pulse-media-wrap.is-portrait",
    ]:
        expect(
            token in css or token in premium_css or token in sci_fi_css or token in source or token in core_js,
            f"Mobile composer guard includes {token}",
        )


if __name__ == "__main__":
    main()
