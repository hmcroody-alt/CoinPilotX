"""Safety checks for the CoinPilotXAI Pulse Feed."""

from __future__ import annotations

import re

from . import scam_shield_engine


BLOCK_TERMS = {
    "kill yourself",
    "kys",
    "dox",
    "doxx",
}

REVIEW_TERMS = {
    "guaranteed profit",
    "double your money",
    "send funds",
    "private key",
    "seed phrase",
    "recovery phrase",
    "free airdrop",
    "connect wallet",
    "urgent",
}


def extract_tags(text):
    tags = []
    for tag in re.findall(r"#([A-Za-z0-9_]{2,32})", text or ""):
        clean = tag.strip("_").lower()
        if clean and clean not in tags:
            tags.append(clean)
    return tags[:12]


def moderate_text(text, post_type="text"):
    body = (text or "").strip()
    lowered = body.lower()
    reasons = []
    if not body and post_type in {"text", "poll"}:
        return {
            "status": "blocked",
            "risk_score": 80,
            "sentiment": "empty",
            "reasons": ["Post text is required."],
            "message": "Your post needs changes before it can be published.",
            "tags": [],
            "ai_summary": "",
        }
    for term in BLOCK_TERMS:
        if term in lowered:
            reasons.append("Unsafe harassment or doxxing language.")
            return {
                "status": "blocked",
                "risk_score": 100,
                "sentiment": "unsafe",
                "reasons": reasons,
                "message": "Your post needs changes before it can be published.",
                "tags": extract_tags(body),
                "ai_summary": "Blocked by safety moderation.",
            }
    scam_result = scam_shield_engine.analyze(body, "pulse_post") if body else {"risk_score": 0, "risk_level": "Low", "red_flags": []}
    risk_score = int(scam_result.get("risk_score") or 0)
    if risk_score >= 85:
        status = "needs_review"
        reasons.extend((scam_result.get("red_flags") or [])[:4])
    elif any(term in lowered for term in REVIEW_TERMS):
        status = "needs_review"
        reasons.append("Scam Shield detected pressure, wallet, or giveaway language.")
        risk_score = max(risk_score, 55)
    else:
        status = "approved"
    positive = sum(1 for term in ("learn", "help", "safe", "smart", "great", "win", "protect") if term in lowered)
    negative = sum(1 for term in ("scam", "risk", "danger", "bad", "lost", "warning") if term in lowered)
    sentiment = "positive" if positive > negative else "caution" if negative > positive else "neutral"
    summary = body[:180] + ("..." if len(body) > 180 else "")
    if not summary and post_type != "text":
        summary = f"{post_type.replace('_', ' ').title()} shared on Pulse."
    tags = extract_tags(body)
    for tag in ("scamalert", "alphaarena", "roastbattle", "btc", "eth", "sol"):
        if tag in lowered.replace(" ", "") and tag not in tags:
            tags.append(tag)
    return {
        "status": status,
        "risk_score": risk_score,
        "sentiment": sentiment,
        "reasons": reasons,
        "message": "Post ready." if status == "approved" else "Your post needs review before wider distribution.",
        "tags": tags[:12],
        "ai_summary": summary,
        "scam_shield": scam_result,
    }


def moderate_comment(text):
    result = moderate_text(text, "comment")
    if result["status"] == "needs_review" and result["risk_score"] < 85:
        result["status"] = "approved"
    return result
