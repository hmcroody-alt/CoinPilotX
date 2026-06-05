#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["Premium command center", "Premium quick links", "Premium AI Studio", "Saved Research / Vault", "Security Center", "Portfolio Empty State"]:
        expect(token in SOURCE, f"premium page includes {token}")
    print("pulse premium page audit ok")

if __name__ == "__main__":
    main()
