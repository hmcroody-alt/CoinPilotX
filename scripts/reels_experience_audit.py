#!/usr/bin/env python3
"""Audit the production Reels mobile shell for immersive safe-zone behavior."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")
    for token in ["data-reels-fullscreen", "100dvh", "scroll-snap-type:y mandatory", "reels-action-rail", "reels-caption-overlay"]:
        expect(token in source + css, f"Reels immersive primitive present: {token}")
    for token in ["data-frame-mode", "reel-frame-toggle"]:
        expect(token not in source, f"dev frame control removed: {token}")
    expect("--reels-tabs-height" in css and "--safe-top" in css, "safe-zone CSS variables exist")
    expect("<a class='icon-btn reel-search-button'" not in source, "Reels viewer search action is not rendered")
    for token in ["['following','Following']", "['for_you','For You']", "['trending','Trending']"]:
        expect(token in source, f"Reels viewer tab exists: {token}")
    client = bot.webhook_app.test_client()
    response = client.get("/pulse/reels")
    html = response.get_data(as_text=True)
    expect(response.status_code in {200, 302}, "Reels route loads or redirects to login")
    if response.status_code == 200:
        expect("data-reels-fullscreen" in html, "Reels HTML contains fullscreen shell")
    print("reels experience audit ok")


if __name__ == "__main__":
    main()
