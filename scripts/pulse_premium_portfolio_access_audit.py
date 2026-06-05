#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for route in ["/pulse/portfolio", "/pulse/premium/portfolio", "/pulse/premium/intelligence/portfolio"]:
        expect(route in SOURCE, f"portfolio route/access present {route}")
    expect(SOURCE.count("Go to Portfolio") >= 3, "portfolio CTA appears repeatedly")
    print("pulse premium portfolio access audit ok")

if __name__ == "__main__":
    main()
