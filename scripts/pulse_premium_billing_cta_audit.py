#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["Founder checkout is being connected. Admin Founder access is available now.", "Manage Billing", "data-founder-checkout", "/api/premium/checkout", "/api/premium/billing-portal"]:
        expect(token in SOURCE, f"billing CTA includes {token}")
    print("pulse premium billing cta audit ok")

if __name__ == "__main__":
    main()
