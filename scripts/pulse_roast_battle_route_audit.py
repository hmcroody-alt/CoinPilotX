#!/usr/bin/env python3
"""Audit Pulse Roast Battle route and legacy redirect."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in ['@webhook_app.route("/pulse/roast-battle"', "legacy_arena_roast_battle_redirect", 'redirect("/pulse/roast-battle"', "Live Battles", "Scheduled Battles", "Battle Replays", "Hall of Fame", "Audience Scoring", "pulse_roast_battle_shell_response", "CoinPilotXAI Pulse"]:
    assert token in S, token
    print("PASS:", token)
print("pulse roast battle route audit ok")
