#!/usr/bin/env python3
"""Audit Premium value/retention language."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"PASS: {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    value_tokens = ["Create, learn, grow, earn", "monthly creator operating system", "Monthly tools ready", "Premium Retention Promise"]
    for token in value_tokens:
        require(token in source, f"Premium retention value present: {token}")
    require(source.count("premium-command-card") >= 8, "Premium has substantial card density")


if __name__ == "__main__":
    main()
