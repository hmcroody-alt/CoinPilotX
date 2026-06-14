#!/usr/bin/env python3
"""Audit the shared PulseSoc premium post-card renderer."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
CORE = ROOT / "static/js/pulse_home_core.js"
CSS = ROOT / "static/css/pulse_desktop_feed.css"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    core = CORE.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    for token in [
        "pulse-post-cards-20260614d",
        "pulse-home-core-20260614f",
    ]:
        expect(token in bot, f"Pulse shell serves latest asset {token}")

    for token in [
        "function renderPost(post)",
        "renderCreatorHeader(card, post, author, authorName, label)",
        "renderEngagement(card, post)",
        "renderActions(card, post)",
        "renderComposer(card, post)",
        "post-reaction-icons",
        "post-reaction-total",
        "post-summary-number",
        "Comment\", \"Comments",
        "Repost\", \"Reposts",
        "Share\", \"Shares",
        "View\", \"Views",
        "post-video-chip",
        "dataset.openPostUrl",
    ]:
        expect(token in core, f"Core renderer includes {token}")

    for forbidden in [
        "data-video-toggle",
        "data-video-sound",
        "10 comments0 reposts0 shares1 views",
    ]:
        expect(forbidden not in core, f"Core renderer omits legacy token {forbidden}")

    for token in [
        "Premium social feed card replacement",
        ".feed > .post-card-modern .post-card-follow",
        ".feed > .post-card-modern .post-card-creator",
        "display: grid",
        "display: inline-flex !important",
        ".feed > .post-card-modern .post-engagement-summary",
        ".feed > .post-card-modern .post-reaction-icons",
        ".feed > .post-card-modern .post-reaction-total",
        "grid-template-columns: minmax(max-content, 1fr) repeat(4, max-content)",
        ".feed > .post-card-modern .post-action-row",
        ".feed > .post-card-modern .post-comment-composer",
        ".feed > .post-card-modern .post-video-chip",
        "@media (max-width: 768px)",
    ]:
        expect(token in css, f"Premium card CSS includes {token}")

    print("premium post card audit ok")


if __name__ == "__main__":
    main()
