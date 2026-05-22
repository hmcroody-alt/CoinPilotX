"""Digital sovereign identity architecture."""

from __future__ import annotations


def trust_passport(identity=None, reputation=None, certifications=None) -> dict:
    identity = identity or {}
    reputation = reputation or {}
    certifications = certifications or []
    return {
        "display_name": identity.get("display_name"),
        "mode": identity.get("identity_mode") or "pseudonymous",
        "trust_score": reputation.get("trust_score") or 0,
        "reputation_tier": reputation.get("tier") or "member",
        "certifications": certifications,
        "portable_reputation_ready": True,
    }
