"""World simulation engine for platform dynamics."""

from __future__ import annotations


def simulate_trust_shift(current=None, action=None) -> dict:
    current = current or {}
    action = action or {}
    trust = int(current.get("trust_score") or 50)
    delta = int(action.get("trust_delta") or 0)
    reach_delta = int(action.get("reach_delta") or 0)
    return {"projected_trust": max(0, min(100, trust + delta)), "projected_reach_change": reach_delta, "confidence": 62}
