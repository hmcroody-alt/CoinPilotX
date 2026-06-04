#!/usr/bin/env python3
"""Audit Pulse and Roast Battle positioning on the public homepage."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "templates/index.html").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
assert 'href="/roast-battle-preview"' in INDEX
assert '@webhook_app.route("/roast-battle-preview"' in BOT
assert "Join Pulse" in BOT and "Explore Pulse" in BOT
assert "Enter Pulse Roast Battle" in INDEX
assert "Pulse Roast Battle" in INDEX
assert 'href="/arena"' not in INDEX
assert "CoinPilotXAI Alpha Arena" not in INDEX
print("pulse public homepage positioning audit ok")
