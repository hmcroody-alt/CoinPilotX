"""Premium Pulse identity helpers."""

from __future__ import annotations

import os
from datetime import datetime


PREMIUM_STAR = "premium_verified_star"
PREMIUM_CHECK = "premium_verified_check"
PREMIUM_BADGES = {PREMIUM_STAR, PREMIUM_CHECK}


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _future(value):
    parsed = _parse_dt(value)
    if not parsed:
        return False
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return parsed > now


def is_owner(row):
    row = row or {}
    owner_email = os.getenv("OWNER_ADMIN_EMAIL", "coinpilotxai@gmail.com").strip().lower()
    owner_name = os.getenv("OWNER_ADMIN_FULL_NAME", "Roody Cherie").strip().lower()
    email = str(row.get("email") or "").strip().lower()
    display = str(row.get("display_name") or row.get("full_name") or "").strip().lower()
    return bool((owner_email and email == owner_email) or (owner_name and display == owner_name))


def has_active_premium(row):
    row = row or {}
    if is_owner(row):
        return True
    plan = str(row.get("plan") or row.get("subscription_plan") or "").lower()
    status = str(row.get("subscription_status") or "").lower()
    if status in {"expired", "canceled", "cancelled", "past_due", "unpaid", "inactive"}:
        return False
    if _future(row.get("pro_expires_at") or row.get("subscription_expires_at")):
        return True
    return bool(int(row.get("is_pro") or row.get("pro_active") or 0)) or plan in {"pro", "premium"} or status in {"active", "trialing"}


def identity_mark(row=None, badge_keys=None):
    badge_keys = {str(key) for key in (badge_keys or [])}
    if PREMIUM_STAR in badge_keys:
        return {"type": "star", "badge_key": PREMIUM_STAR, "symbol": "✦", "title": "Premium Verified"}
    if PREMIUM_CHECK in badge_keys:
        return {"type": "check", "badge_key": PREMIUM_CHECK, "symbol": "✓", "title": "Premium Verified"}
    if has_active_premium(row or {}):
        return {"type": "star", "badge_key": PREMIUM_STAR, "symbol": "✦", "title": "Premium Verified"}
    return None
