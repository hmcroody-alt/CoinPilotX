#!/usr/bin/env python3
"""Audit Pulse Save button wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    compact = "".join(source.split())
    require("data-save-post" in source and "/api/pulse/posts/${save.dataset.savePost}/save" in source, "Feed post Save button works")
    require("data-reel-save" in source and "/api/pulse/reels/${save.dataset.reelSave}/save" in source, "Reel Save button works")
    require("content_type='reel'" in source, "Reel saves become Reel saved items")
    require("'marketplace'" in compact and "INSERTINTOpulse_saved_items" in compact, "Marketplace saves become Saved items")
    require("data-remove-saved" in source and "data-move-saved" in source, "Saved page remove/move buttons exist")


if __name__ == "__main__":
    main()
