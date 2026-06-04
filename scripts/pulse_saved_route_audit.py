#!/usr/bin/env python3
"""Audit /pulse/saved route."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require('@webhook_app.route("/pulse/saved"' in source, "/pulse/saved route exists")
    require("data-pulse-saved-page" in source, "Saved page has UI root")
    require("Saved" in source and "Pulse Library" in source, "Saved page has header")
    require("saved-filters" in source and "saved-collections" in source, "Saved page has filters and collections")


if __name__ == "__main__":
    main()
