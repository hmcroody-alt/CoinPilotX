#!/usr/bin/env python3
"""Audit Pulse Premium visible actions."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for href in ["/pulse/premium/activate", "/pulse/creator/dashboard", "/pulse/saved", "/pulse/teachers", "/pulse/groups"]:
        require(href in source, f"Premium action target exists: {href}")
    require("button disabled" in source, "Coming Soon Premium actions are disabled")
    require("data-premium-ai-tool" in source and "/api/pulse/creator-ai/" in source, "Premium AI buttons call working API")


if __name__ == "__main__":
    main()
