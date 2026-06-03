#!/usr/bin/env python3
"""Audit visible Pulse buttons for working handlers, links, or clear states."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    required_links = [
        "/pulse/status",
        "/pulse/status?lane=trending",
        "/pulse/live",
        "/pulse/profile/edit",
        "/pulse/messages-v2",
        "/pulse/reels",
    ]
    for token in required_links:
        expect(token in source, f"visible Pulse link exists: {token}")

    required_handlers = [
        "data-open-media",
        "data-composer-reel",
        "data-composer-music",
        "data-composer-audience",
        "id=\"aiBtn\"",
        "id=\"publishBtn\"",
        "data-status2-pick-media",
        "data-status-viewer-close",
        "data-status-viewer-prev",
        "data-status-viewer-next",
        "data-status-full-tab",
        "data-open-status-id",
        "data-reel-react",
        "data-open-comments",
        "data-share-reel",
    ]
    for token in required_handlers:
        expect(token in source, f"visible Pulse control has active wiring: {token}")

    expect("toast('Audience is public for this composer" in source, "Audience button has a clear user-facing state")
    expect("toast('Music tools are available" in source, "Music button has a clear user-facing state")
    expect("Write something or attach media before publishing." in source, "Publish button reports missing content clearly")
    expect("Choose a video before publishing your Reel." in source, "Reel publish button is guarded until video is selected")
    expect("Status opened." not in source, "Status buttons do not use toast-only open behavior")


if __name__ == "__main__":
    main()
