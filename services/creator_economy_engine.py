"""Creator nation economy foundation."""

from __future__ import annotations


def creator_revenue_readiness(creator=None) -> dict:
    creator = creator or {}
    checks = {
        "verified": bool(creator.get("verified") or creator.get("verification_status") == "verified"),
        "trust_ready": int(creator.get("trust_score") or 0) >= 70,
        "policy_ready": bool(creator.get("policy_accepted")),
        "payout_ready": bool(creator.get("payout_ready")),
    }
    return {"ready": all(checks.values()), "checks": checks}


def platform_fee(amount_cents: int, rate: float = 0.15) -> dict:
    amount = max(0, int(amount_cents or 0))
    fee = int(amount * rate)
    return {"amount_cents": amount, "platform_fee_cents": fee, "creator_net_cents": amount - fee}
