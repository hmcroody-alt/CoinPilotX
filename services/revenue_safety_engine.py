"""Trust-first revenue safety checks for creator monetization.

This module intentionally avoids financial claims. It only flags unsafe,
misleading, or compliance-sensitive monetization patterns for human review.
"""

from __future__ import annotations


UNSAFE_CLAIMS = {
    "guaranteed profit",
    "risk free",
    "100x",
    "get rich",
    "insider signal",
    "sure win",
    "no loss",
    "pump",
    "private key",
    "seed phrase",
}

RISKY_CATEGORIES = {"trading signals", "investment advice", "token launch", "airdrops"}


def score_text(title="", description="", category="") -> dict:
    text = f"{title} {description} {category}".lower()
    hits = sorted(term for term in UNSAFE_CLAIMS if term in text)
    category_risk = str(category or "").strip().lower() in RISKY_CATEGORIES
    risk_score = min(100, len(hits) * 22 + (35 if category_risk else 0))
    status = "blocked_review" if risk_score >= 70 else "needs_review" if risk_score >= 30 else "review_ready"
    return {
        "risk_score": risk_score,
        "status": status,
        "flags": hits + (["risky_category"] if category_risk else []),
        "summary": "Revenue item needs human review." if risk_score else "No obvious unsafe revenue claims detected.",
    }


def marketplace_listing_review(listing=None) -> dict:
    listing = listing or {}
    return score_text(listing.get("title"), listing.get("description"), listing.get("category"))


def sponsor_review(sponsor=None) -> dict:
    sponsor = sponsor or {}
    result = score_text(sponsor.get("campaign_name") or sponsor.get("sponsor_name"), sponsor.get("landing_page") or sponsor.get("notes"), sponsor.get("category"))
    if "crypto" in str(sponsor.get("category") or "").lower():
        result["risk_score"] = min(100, result["risk_score"] + 10)
        if result["status"] == "review_ready":
            result["status"] = "needs_review"
    return result


def creator_readiness(metrics=None) -> dict:
    metrics = metrics or {}
    trust = int(metrics.get("trust_score") or 0)
    posts = int(metrics.get("posts") or 0)
    reports = int(metrics.get("reports") or 0)
    score = max(0, min(100, trust * 0.55 + min(posts, 50) * 0.8 - reports * 8))
    return {
        "readiness_score": round(score, 1),
        "ready": score >= 65,
        "next_steps": [
            "Complete profile",
            "Keep listings educational and transparent",
            "Build trust with helpful posts",
        ] if score < 65 else ["Prepare a reviewed product, course, or livestream offer."],
    }
