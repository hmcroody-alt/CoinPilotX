#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["Premium billing setup is coming soon", "Manage Subscription", "data-premium-coming-soon", "/pulse/premium/activate"]:
        expect(token in SOURCE, f"billing CTA includes {token}")
    print("pulse premium billing cta audit ok")

if __name__ == "__main__":
    main()
