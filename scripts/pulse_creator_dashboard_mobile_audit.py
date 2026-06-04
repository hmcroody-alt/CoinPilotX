#!/usr/bin/env python3
"""Audit Creator Studio mobile layout contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    compact = "".join(source.split())
    require("@media(max-width:1050px)" in source, "Creator Studio has mobile breakpoint")
    require(
        ".creator-studio{grid-template-columns:1fr}" in compact
        or ".creator-studio{{grid-template-columns:1fr}}" in compact,
        "Creator Studio stacks on mobile",
    )
    require("studio-tabs" in source and "flex-wrap:wrap" in source, "Creator tabs remain tappable")


if __name__ == "__main__":
    main()
