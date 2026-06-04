#!/usr/bin/env python3
"""Audit Pulse Saved API contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token in [
        "pulse_saved_items",
        "pulse_saved_collections",
        '@webhook_app.route("/api/pulse/saved"',
        '@webhook_app.route("/api/pulse/saved/collections"',
        '@webhook_app.route("/api/pulse/saved/<int:item_id>"',
        '@webhook_app.route("/api/pulse/saved/<int:item_id>/move"',
    ]:
        require(token in source, f"Saved API/table contract present: {token}")
    require("Users can only" not in source or "user_id=?" in source, "Saved queries are user-scoped")
    require("PULSE_SAVED_TYPES" in source and "status" in source and "reel" in source, "Saved supports multiple content types")


if __name__ == "__main__":
    main()
