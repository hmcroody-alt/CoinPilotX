#!/usr/bin/env python3
"""Audit Pulse Premium mobile responsiveness contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("@media(max-width:900px)" in source and "premium-command-hero" in source, "Premium has mobile breakpoint")
    require("grid-template-columns:1fr" in source, "Premium stacks columns on mobile")
    require("premium-tool-grid" in source and "auto-fit" in source, "Premium tool grid is responsive")


if __name__ == "__main__":
    main()
