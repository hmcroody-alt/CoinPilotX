#!/usr/bin/env python3
"""Audit Dashboard to Premium Intelligence migration."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in ['@webhook_app.route("/dashboard"', '"/pulse/premium/intelligence"', '@webhook_app.route("/pulse/premium/intelligence"', "Intelligence Center", "Portfolio Tracker", "Scam Shield Pro", "Saved Insights"]:
    assert token in S, token
    print("PASS:", token)
print("pulse dashboard migration audit ok")
