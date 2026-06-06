#!/usr/bin/env python3
"""Audit Pulse music license safety rules."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import music_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    service = (ROOT / "services" / "music_service.py").read_text(encoding="utf-8")
    for token in [
        "license_type",
        "commercial_use_allowed",
        "remix_edit_allowed",
        "attribution_required",
        "proof_url",
        "approved_by_admin",
        "active",
    ]:
        require(token in source and token in service, f"license field exists: {token}")
    require("noncommercial" in service and "no-derivatives" in service, "unsafe license types are blocked")
    require("Music track is not approved for Pulse use" in source, "unapproved attachments fail closed")
    inventory = music_service.license_inventory()
    require(inventory and all(item.get("is_creator_safe") for item in inventory), "license inventory exposes only safe tracks")
    print("pulse music license audit ok")


if __name__ == "__main__":
    main()
