#!/usr/bin/env python3
"""Audit Pulse Premium creator platform surface."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    required = [
        "Premium Command Center",
        "Premium AI Studio",
        "Creator Intelligence",
        "Premium Learning Hub",
        "Premium Media Vault",
        "Premium Creator Academy",
        "Premium Analytics",
        "Premium Networking",
        "Premium Marketplace Tools",
        "Premium Rooms",
        "Premium Status Features",
        "Premium Profile Features",
        "Premium Resource Center",
        "Premium Rewards",
        "UNDX Premium",
    ]
    for token in required:
        require(token in source, f"{token} appears on /pulse/premium")
    require("data-premium-ai-tool" in source, "Premium AI tool buttons are wired")
    require("Coming Soon" in source, "unavailable Premium modules are clearly labeled")


if __name__ == "__main__":
    main()
