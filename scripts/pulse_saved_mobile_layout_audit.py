#!/usr/bin/env python3
"""Audit Pulse Saved mobile layout."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("@media(max-width:840px)" in source, "Saved page has mobile breakpoint")
    require(".saved-board{grid-template-columns:1fr}" in source, "Saved board stacks on mobile")
    require("overflow-x:auto" in source and "saved-collections" in source, "Saved collections scroll horizontally")
    require("No saved items yet" in source, "Saved mobile empty state is readable")


if __name__ == "__main__":
    main()
