"""Prestige economy helpers for PulseSoc identity progression."""

from __future__ import annotations


PRESTIGE_LAYERS = [
    "Member",
    "Trusted",
    "Verified",
    "Elite",
    "Creator Pro",
    "Teacher Elite",
    "Marketplace Elite",
    "Arena Elite",
    "PulseSoc Legend",
    "Founder Circle",
]


def prestige_for_scores(scores=None, premium=False, owner=False):
    scores = scores or {}
    if owner:
        return {"layer": "Founder Circle", "mark": "gold founder", "ring": "founder-gold"}
    trust = int(scores.get("trust_score") or 0)
    creator = int(scores.get("creator_score") or 0)
    influence = int(scores.get("influence_score") or 0)
    combined = int(trust * 0.45 + creator * 0.35 + influence * 0.2)
    if combined >= 92:
        layer = "PulseSoc Legend"
    elif combined >= 82:
        layer = "Arena Elite"
    elif combined >= 72:
        layer = "Creator Pro"
    elif combined >= 60:
        layer = "Elite"
    elif combined >= 44:
        layer = "Verified"
    elif combined >= 28:
        layer = "Trusted"
    else:
        layer = "Member"
    return {"layer": layer, "mark": "blue glow" if premium else "", "ring": layer.lower().replace(" ", "-")}


def xp_reward(action="post"):
    return {
        "help_user": 30,
        "educational_post": 25,
        "scam_report_verified": 40,
        "attend_livestream": 15,
        "comment": 5,
        "post": 10,
    }.get(action, 5)
