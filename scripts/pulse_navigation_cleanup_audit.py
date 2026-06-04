#!/usr/bin/env python3
"""Audit unified Pulse navigation."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in ['("Roast Battle", "/pulse/roast-battle")', '("Premium", "/pulse/premium")', '("Creator Studio", "/pulse/creator/dashboard")', 'grid-template-columns:repeat(5,minmax(0,1fr))', '("Create", "/pulse/create", "＋")']:
    assert token in S, token
    print("PASS:", token)
print("pulse navigation cleanup audit ok")
