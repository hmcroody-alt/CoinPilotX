#!/usr/bin/env python3
"""Audit Pulse Roast Battle route and legacy redirect."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in ['@webhook_app.route("/pulse/roast-battle"', "legacy_arena_roast_battle_redirect", 'redirect("/pulse/roast-battle"', "Live Battles", "Scheduled Battles", "Battle Replays", "Hall of Fame", "Audience Scoring", "pulse_roast_battle_shell_response", "PulseSoc Roast Battle"]:
    assert token in S, token
    print("PASS:", token)
for token in ["CoinPilotXAI Pulse", "Pulse Roast Battle | CoinPilotXAI"]:
    assert token not in S, f"old public Roast Battle branding remains: {token}"
    print("PASS: old branding removed", token)
print("pulse roast battle route audit ok")
