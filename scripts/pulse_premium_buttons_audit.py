#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for href in ["/pulse/premium/activate", "/pulse/premium/intelligence", "/pulse/portfolio", "/pulse/creator/dashboard", "/pulse/saved", "/pulse/settings/security", "/alerts", "/pulse/groups"]:
        expect(href in SOURCE, f"premium button route present {href}")
    expect("data-premium-coming-soon" in SOURCE, "safe coming-soon billing action exists")
    expect("data-premium-ai-tool" in SOURCE and "/api/pulse/creator-ai/" in SOURCE, "AI buttons call API")
    print("pulse premium buttons audit ok")

if __name__ == "__main__":
    main()
