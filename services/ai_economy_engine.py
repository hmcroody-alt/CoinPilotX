"""AI economy engine foundation."""

from __future__ import annotations


def dynamic_premium_hint(user=None, market=None) -> dict:
    user = user or {}
    market = market or {}
    engagement = int(user.get("engagement_score") or 0)
    creator = bool(user.get("is_creator"))
    base = int(market.get("base_price_cents") or 1900)
    discount = 0.15 if engagement >= 80 else 0.1 if creator else 0
    return {"base_price_cents": base, "suggested_price_cents": int(base * (1 - discount)), "reason": "creator_growth" if creator else "standard"}
