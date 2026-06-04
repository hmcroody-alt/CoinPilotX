#!/usr/bin/env python3
"""Audit Creator Studio buttons and shortcuts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for href in ["/pulse#create", "/pulse/reels", "/pulse/status", "/pulse/live", "/pulse/marketplace/create", "/pulse/teachers"]:
        require(href in source, f"Creator shortcut target exists: {href}")
    require("data-ai-tool" in source and "/api/pulse/creator-ai/" in source, "Creator AI buttons call API")
    require("Creator AI output appears here" in source, "Creator AI has clear output state")


if __name__ == "__main__":
    main()
