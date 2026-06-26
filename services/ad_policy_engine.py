"""Ethical contextual ad review helpers."""

from __future__ import annotations

ALLOWED_CATEGORIES = {
    "cybersecurity",
    "hardware_wallets",
    "crypto_education",
    "ai_tools",
    "productivity",
    "creator_sponsorship",
    "approved_exchange",
    "financial_education",
    "radio",
}

BLOCKED_CATEGORIES = {
    "scam_token",
    "pump_and_dump",
    "guaranteed_profit",
    "high_risk_leverage",
    "fake_giveaway",
    "impersonation",
    "seed_phrase_tool",
    "unverified_investment",
    "gambling_crypto_promise",
}

BLOCKED_PHRASES = [
    "guaranteed profit",
    "100x guaranteed",
    "send seed phrase",
    "recovery phrase tool",
    "risk free crypto",
    "double your crypto",
    "airdrop claim urgent",
]


def evaluate_ad(payload):
    payload = dict(payload or {})
    category = (payload.get("category") or "").strip().lower()
    text = " ".join(str(payload.get(k) or "") for k in ("headline", "body", "destination_url")).lower()
    reasons = []
    if category in BLOCKED_CATEGORIES:
        reasons.append(f"Blocked category: {category}")
    for phrase in BLOCKED_PHRASES:
        if phrase in text:
            reasons.append(f"Blocked phrase: {phrase}")
    if category and category not in ALLOWED_CATEGORIES:
        reasons.append("Category requires manual review.")
    status = "blocked" if any(r.startswith("Blocked") for r in reasons) else "review" if reasons else "approved"
    return {
        "ok": status != "blocked",
        "status": status,
        "label": "Sponsored",
        "quality_score": 0 if status == "blocked" else 70 if status == "review" else 92,
        "reasons": reasons,
        "disclaimer": "Sponsored placements are reviewed for safety but do not represent financial advice or endorsement.",
    }


def contextual_slot(page_context):
    context = (page_context or "homepage").strip().lower()
    mapping = {
        "scam_shield": ("cybersecurity", "Security sponsor slot"),
        "arena": ("crypto_education", "Arena education sponsor"),
        "roast_battle": ("creator_sponsorship", "Creator event sponsor"),
        "learn": ("financial_education", "Learning sponsor"),
        "pulse": ("cybersecurity", "Community safety sponsor"),
    }
    category, headline = mapping.get(context, ("crypto_education", "CoinPilotXAI trusted sponsor"))
    return {"label": "Sponsored", "category": category, "headline": headline, "status": "available"}
