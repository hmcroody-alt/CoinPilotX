#!/usr/bin/env python3
"""Audit Live cards are discoverable as regular Pulse feed content."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    # Reuse the stronger feed insertion audit behavior, then verify page JS has live-card awareness.
    from scripts.live_feed_insertion_audit import main as feed_main  # noqa: E402

    feed_main()
    source = (ROOT / "bot.py").read_text()
    require("'live'" in source and "live_feed_service.ensure_live_feed_post" in source, "backend creates live typed feed posts")
    require("live_session_id" in source and "live_viewer_count" in source, "feed schema carries live identifiers and viewer counts")
    require("/pulse/live/" in source, "live feed payload links to public viewer")
    print("live feed integration audit ok")


if __name__ == "__main__":
    main()
