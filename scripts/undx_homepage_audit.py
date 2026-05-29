#!/usr/bin/env python3
"""Audit the UNDX premium homepage section."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition, message, detail=""):
    if not condition:
        raise AssertionError(f"{message}: {detail}")
    print(f"ok - {message}")


def main():
    bot.init_db()
    html = bot.pulse_undx_core_homepage_html()
    css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")
    source = (ROOT / "bot.py").read_text(encoding="utf-8")

    for token in [
        "UNDX Core",
        "Build Beyond the Known",
        "Unknown Destination X",
        "Coming Soon: Premium Intelligence Layer",
        "Unlock UNDX",
        "Recursive Builder Intelligence",
        "Autonomous Code Evolution",
        "Crypto Security Expansion",
        "AI Research Engine",
        "Product Growth Intelligence",
        "Mission Control Automation",
    ]:
        require(token in html, f"UNDX section contains {token}")

    for token in [
        "pulse-undx-core",
        "undx-feature-grid",
        "undx-premium-badge",
        "undx-orbit",
        "@media (max-width: 760px)",
        "prefers-reduced-motion",
    ]:
        require(token in css, f"UNDX responsive styling contains {token}")

    require("__UNDX_CORE__" in source, "Pulse homepage includes UNDX placeholder")
    require(".replace(\"__UNDX_CORE__\", undx_core_html)" in source, "Pulse homepage renders UNDX section")
    require("href='/pulse/premium'" in html, "UNDX CTA routes to Pulse Premium")
    require("data-undx-core" in html, "UNDX section exposes stable audit hook")
    print("undx homepage audit ok")


if __name__ == "__main__":
    main()
