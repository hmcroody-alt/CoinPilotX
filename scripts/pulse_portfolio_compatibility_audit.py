#!/usr/bin/env python3
"""Audit the Pulse Premium portfolio compatibility surface."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "services" / "portfolio_service.py").read_text(encoding="utf-8")

for token in [
    "/pulse/premium/intelligence/portfolio",
    "pulse_premium_portfolio_page",
    "portfolio_service.calculate_user_portfolio",
    "portfolio_service.get_watchlist",
    "Original portfolio holding",
]:
    assert token in BOT, f"missing portfolio surface token: {token}"
    print(f"PASS: {token}")

for token in ["manual_portfolio", "watchlists", '"legacy": True', "known_symbols"]:
    assert token in SERVICE, f"missing compatibility token: {token}"
    print(f"PASS: {token}")

print("pulse portfolio compatibility audit ok")
