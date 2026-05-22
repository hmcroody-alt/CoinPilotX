"""Governance foundation for spaces and communities."""

from __future__ import annotations


def council_eligibility(user=None) -> dict:
    user = user or {}
    reputation = int(user.get("reputation_score") or user.get("trust_score") or 0)
    strikes = int(user.get("moderation_strikes") or 0)
    eligible = reputation >= 70 and strikes == 0
    return {"eligible": eligible, "reason": "Eligible for council review." if eligible else "Build trust and keep a clean moderation record."}


def governance_snapshot(space=None, members=None, proposals=None) -> dict:
    members = members or []
    proposals = proposals or []
    eligible = [m for m in members if council_eligibility(m)["eligible"]]
    return {
        "space": space or {},
        "members": len(members),
        "council_candidates": len(eligible),
        "open_proposals": sum(1 for p in proposals if p.get("status") == "open"),
        "health": "stable" if len(eligible) >= 2 else "needs_trusted_members",
    }
