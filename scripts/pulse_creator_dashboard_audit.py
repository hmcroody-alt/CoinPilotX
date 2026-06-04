#!/usr/bin/env python3
"""Audit Pulse Creator Studio sections."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token in ["Creator Studio", "Content Studio", "Creator AI Tools", "Analytics", "Monetization / Marketplace", "Live Studio", "Media Library", "Growth Tools", "Safety / Trust", "Resources"]:
        require(token in source, f"Creator Studio includes {token}")
    for metric in ["Total Posts", "Total Reels", "Total Statuses", "Followers", "Engagement Rate", "Views Today", "Comments Today", "Saves", "Shares"]:
        require(metric in source, f"Creator metric exists: {metric}")


if __name__ == "__main__":
    main()
