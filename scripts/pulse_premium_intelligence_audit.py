#!/usr/bin/env python3
"""Audit Premium Intelligence modules."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in ["Premium Intelligence", "Portfolio Tracker", "Watchlist", "Alerts", "Scam Shield Pro", "AI Memory", "Market Pulse", "Risk Shield Score", "Education Journey", "Saved Insights"]:
    assert token in S, token
    print("PASS:", token)
print("pulse premium intelligence audit ok")
