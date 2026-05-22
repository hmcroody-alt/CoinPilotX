"""AI moderation core fallbacks for Pulse safety."""

from __future__ import annotations

import re


BLOCK_TERMS = ["seed phrase", "private key", "doxx", "guaranteed profit"]
REVIEW_TERMS = ["airdrop", "claim now", "double your", "pump group", "wallet validate"]


def moderate_text(text="", context="pulse"):
    lowered = (text or "").lower()
    blocked = [term for term in BLOCK_TERMS if term in lowered]
    review = [term for term in REVIEW_TERMS if term in lowered]
    urls = re.findall(r"https?://\\S+", text or "")
    status = "blocked" if blocked else "needs_review" if review else "approved"
    return {
        "status": status,
        "risk_score": min(100, len(blocked) * 40 + len(review) * 18 + len(urls) * 4),
        "blocked_terms": blocked,
        "review_terms": review,
        "urls": urls,
        "context": context,
    }


def fake_engagement_risk(events=None):
    events = events or []
    unique_users = len({item.get("user_id") for item in events if item.get("user_id")})
    total = len(events)
    risk = total > 30 and unique_users <= 3
    return {"risk": risk, "risk_score": 80 if risk else 10, "unique_users": unique_users, "total_events": total}
