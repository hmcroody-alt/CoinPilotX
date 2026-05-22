"""Central Pulse social identity helpers.

Normal Pulse surfaces use display name, username, avatar, social labels, and
premium identity. Arena/Roast call signs should stay in their own competitive
surfaces.
"""

from __future__ import annotations

from . import premium_identity_engine


def primary_label(row=None, badge_keys=None, badge_labels=None) -> str:
    row = row or {}
    badge_key_set = {str(key) for key in (badge_keys or [])}
    label_set = {str(label).strip().lower() for label in (badge_labels or [])}
    if premium_identity_engine.is_owner(row) or {"owner", "founder"} & badge_key_set:
        return "Founder · CoinPilotXAI Pulse"
    if {"creator", "verified", "partner_creator"} & badge_key_set or {"creator", "verified creator"} & label_set:
        return "Verified Creator"
    if "teacher" in badge_key_set or "teacher" in label_set:
        return "Teacher"
    if "marketplace_seller" in badge_key_set or "marketplace seller" in label_set:
        return "Marketplace Seller"
    if "livestream_eligible" in badge_key_set or "livestream eligible" in label_set:
        return "Livestream Eligible"
    try:
        trust_score = int(row.get("trust_score") or 0)
    except Exception:
        trust_score = 0
    if "trusted_member" in badge_key_set or "trusted member" in label_set or trust_score >= 70:
        return "Trusted Member"
    return "Member"


def build_identity(row=None, badge_keys=None, badge_labels=None) -> dict:
    row = dict(row or {})
    badge_keys = list(badge_keys or [])
    badge_labels = list(badge_labels or [])
    user_id = int(row.get("user_id") or 0)
    public_id = row.get("public_player_id") or f"pilot-{str(user_id)[-6:]}"
    name = (
        row.get("user_display_name")
        or row.get("display_name")
        or row.get("full_name")
        or row.get("username")
        or f"Pulse Member #{str(public_id)[-4:]}"
    )
    mark = premium_identity_engine.identity_mark(row, badge_keys)
    label = primary_label(row, badge_keys, badge_labels)
    badge_set = set(badge_keys)
    return {
        "user_id": user_id,
        "public_player_id": public_id,
        "display_name": str(name)[:80],
        "name": str(name)[:80],
        "username": str(row.get("username") or "")[:80],
        "avatar_url": row.get("user_avatar_url") or row.get("avatar_url") or "",
        "banner_url": row.get("banner_url") or "",
        "bio": row.get("bio") or "",
        "primary_label": label,
        "rank": label,
        "badges": badge_labels or [label],
        "badge_keys": badge_keys,
        "premium_mark_type": (mark or {}).get("type") if mark else "",
        "premium_mark": mark,
        "premium_verified": bool(mark),
        "is_owner": bool(premium_identity_engine.is_owner(row) or {"owner", "founder"} & badge_set),
        "is_premium": bool(mark),
        "is_verified": bool({"verified", "creator", "teacher", "safety_verified"} & badge_set),
        "is_creator": bool({"creator", "partner_creator"} & badge_set),
        "is_teacher": "teacher" in badge_set,
        "is_seller": "marketplace_seller" in badge_set,
        "is_live_eligible": "livestream_eligible" in badge_set or str(row.get("livestream_status") or "") in {"eligible", "approved"},
    }


def get_pulse_identity(user_id, loader=None) -> dict:
    if loader:
        row, badge_keys, badge_labels = loader(user_id)
        return build_identity(row, badge_keys, badge_labels)
    return build_identity({"user_id": user_id}, [], [])
