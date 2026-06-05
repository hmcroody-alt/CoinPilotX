#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["Premium active", "Premium expired", "Upgrade to Premium", "Founder Premium", "subscription_status"]:
        expect(token in SOURCE, f"premium user state includes {token}")
    print("pulse premium user state audit ok")

if __name__ == "__main__":
    main()
