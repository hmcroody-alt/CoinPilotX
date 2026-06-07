#!/usr/bin/env python3
"""Guard PulseSoc public-facing branding cleanup."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = [
    ROOT / "bot.py",
    ROOT / "templates" / "index.html",
    ROOT / "static" / "manifest.json",
    ROOT / "static" / "site.webmanifest",
]

for path in PUBLIC_FILES:
    text = path.read_text(encoding="utf-8")
    for token in [
        "CoinPilotXAI Pulse",
        "| CoinPilotXAI Pulse",
        "Pulse Roast Battle | CoinPilotXAI",
        "Pulse - Roast Battle | CoinPilotXAI",
        "CoinPilotXAI Platform",
    ]:
        assert token not in text, f"{path.relative_to(ROOT)} still contains old public brand token: {token}"
        print("PASS: absent", path.relative_to(ROOT), token)

index = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
for token in [
    "<title>PulseSoc | Creator, Video, Live, Messaging, Premium Intelligence</title>",
    '<meta property="og:title" content="PulseSoc" />',
    '<meta name="twitter:title" content="PulseSoc" />',
    '"name": "PulseSoc"',
    "PulseSoc.com",
]:
    assert token in index, f"homepage missing PulseSoc branding token: {token}"
    print("PASS: homepage", token)

bot = (ROOT / "bot.py").read_text(encoding="utf-8")
for token in [
    "PulseSoc Roast Battle",
    "<span>PulseSoc</span></a><div class='actions'>",
    "PulseSoc Live",
    "PulseSoc Post",
    "PulseSoc is a community discussion space",
]:
    assert token in bot, f"bot.py missing PulseSoc token: {token}"
    print("PASS: bot", token)

print("pulsesoc branding audit ok")
