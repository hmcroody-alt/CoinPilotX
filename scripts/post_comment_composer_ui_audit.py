#!/usr/bin/env python3
"""Audit the PulseSoc feed action and Galaxy Comment Dock UI wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "js" / "pulse_home_core.js"
CSS = ROOT / "static" / "css" / "pulse_reaction_system.css"
DESKTOP_CSS = ROOT / "static" / "css" / "pulse_desktop_feed.css"


def require(condition: bool, message: str, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{message}{': ' + detail if detail else ''}")
    print(f"PASS: {message}")


def main() -> None:
    js = JS.read_text()
    css = CSS.read_text()
    desktop_css = DESKTOP_CSS.read_text()

    require("pulse-comment-dock" in js, "Comment composer uses Galaxy Comment Dock class")
    require("commentVoice" in js, "Voice comment action exists")
    require("commentAttach" in js, "Attach comment action exists")
    require("data-unavailable" in js, "Unavailable action handler exists")
    require("updateCommentSendState" in js, "Send button state machine exists")
    require("is-loading" in js, "Comment send loading state exists")
    require("Photo comments are not enabled on feed cards yet." in js, "Photo action safely unavailable")
    require("Voice comments are coming soon." in js, "Voice action safely unavailable")
    require("Comment attachments are coming soon." in js, "Attach action safely unavailable")
    require("/api/pulse/posts/${postId}/comments" in js, "Existing comment API path preserved")
    require("data-post-comment-count" in js, "Comment count update path preserved")

    require(".pulse-comment-dock" in css or ".pulse-comment-composer-v2" in css, "Comment dock styles exist")
    require("repeat(5, 38px)" in css, "Desktop composer supports five action buttons")
    require("repeat(5, 34px)" in css, "Mobile composer supports five action buttons")
    require("repeat(5, 32px)" in css, "Narrow mobile composer supports five action buttons")
    require("repeat(4, 42px) 50px" in desktop_css, "Feed-specific desktop cascade supports five composer actions")
    require("repeat(4, 34px) 42px" in desktop_css, "Feed-specific mobile cascade supports five composer actions")
    require("repeat(4, 32px) 40px" in desktop_css, "Feed-specific narrow cascade supports five composer actions")
    require(".pulse-avatar-orb" in css, "Branded avatar orb fallback exists")
    require(".post-engagement-summary-v3" in css, "Reaction summary strip style exists")
    require('data-action="repost"' in css, "Repost active styling exists")
    require('data-action="share"' in css, "Share tap styling exists")

    print("Post comment composer UI audit completed.")


if __name__ == "__main__":
    main()
