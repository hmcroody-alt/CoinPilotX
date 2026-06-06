#!/usr/bin/env python3
"""Audit Pulse-first positioning on the public homepage."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "templates/index.html").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

required_homepage_tokens = [
    "Pulse",
    "Sign In to Pulse",
    "Join Pulse",
    "Explore Pulse",
    "Creator, video, live, messaging, roast battle, premium intelligence, and safety tools in one platform.",
    "Videos, Reels, and Live",
    "Messages",
    "Roast Battle",
    "Premium Intelligence",
    "Scam Shield and Safety",
    "Creator Studio",
    "data-scroll-top",
]
for token in required_homepage_tokens:
    assert token in INDEX, f"homepage missing {token}"

allowed_nav_tokens = [
    ">Home<",
    ">Pulse<",
    ">Premium<",
    ">Roast Battle<",
    ">Safety<",
    "Sign In to Pulse",
    "Join Pulse",
]
for token in allowed_nav_tokens:
    assert token in INDEX, f"simplified nav missing {token}"

forbidden_tokens = [
    "Live Market",
    "Sports Edge",
    "Day Signal",
    "Scam Guide",
    "/analysis BTC",
    "/whales",
    "/scamstories",
    "/portfolio_advice",
    "/countrynews",
    "/networkhealth",
    "/checktx",
    "/walletscan",
    "/btcstats",
    "/mempool",
    "CoinPilotXAI Alpha Arena",
]
for token in forbidden_tokens:
    assert token not in INDEX, f"homepage still exposes old command/link token: {token}"

assert 'href="/roast-battle-preview"' in INDEX
assert '@webhook_app.route("/roast-battle-preview"' in BOT
assert "Roast Battle is the entertainment feature" in INDEX
assert 'href="/arena"' not in INDEX
assert "redirect_www_to_apex_domain" in BOT
assert "www.coinpilotx.app" in BOT
assert "https://pulsesoc.com" in BOT
print("pulse public homepage positioning audit ok")
