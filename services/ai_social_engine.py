"""AI social helper layer with local fallbacks."""

from __future__ import annotations

import re


DEFAULT_TAGS = ["Pulse", "CryptoEducation", "ScamShield"]


def suggest_hashtags(text="", topic="") -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text or "")
    tags = []
    for word in words[:30]:
        clean = word.strip().title()
        if clean.lower() in {"crypto", "bitcoin", "wallet", "scam", "market", "arena", "teacher"}:
            tags.append(clean)
    if topic:
        tags.insert(0, str(topic).strip("#").title())
    return list(dict.fromkeys(tags + DEFAULT_TAGS))[:8]


def safety_score(text="") -> dict:
    lowered = (text or "").lower()
    risky = ["seed phrase", "private key", "guaranteed profit", "airdrop claim", "send funds"]
    hits = [item for item in risky if item in lowered]
    score = max(0, 100 - len(hits) * 24)
    return {"score": score, "risk_terms": hits, "needs_review": score < 70}


def improve_caption(text="") -> dict:
    text = (text or "").strip()
    if not text:
        return {"caption": "", "summary": "", "hashtags": DEFAULT_TAGS}
    summary = text[:180] + ("..." if len(text) > 180 else "")
    return {
        "caption": text,
        "summary": summary,
        "hashtags": suggest_hashtags(text),
        "safety": safety_score(text),
    }
