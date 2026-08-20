"""Premium PulseSoc identity helpers."""

from __future__ import annotations

import os
from datetime import datetime, timedelta


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


# Grace window applied before a stale 'active' status is treated as lapsed.
# Covers a briefly delayed or retried provider webhook without leaving premium
# alive forever when the webhook never arrives.
_STALE_EXPIRY_GRACE = timedelta(days=3)


def _clearly_expired(value):
    """True when an expiry timestamp is present and past by more than the
    grace window. Missing/unparseable values return False (no opinion)."""
    parsed = _parse_dt(value)
    if not parsed:
        return False
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return parsed + _STALE_EXPIRY_GRACE < now


def _owner_user_ids():
    """Owner allowlist from ``PULSESOC_OWNER_USER_IDS`` (comma-separated user
    ids). Read per call so an operator change takes effect without a restart.
    Empty or unset means NOBODY holds owner identity."""
    raw = os.getenv("PULSESOC_OWNER_USER_IDS", "") or ""
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


def is_owner(row):
    """Owner identity by allowlisted user id ONLY.

    The previous implementation also matched the account's DISPLAY NAME (and a
    hardcoded email) against owner constants. Display names are user-editable:
    any user could rename themselves to the owner's name and inherit permanent
    premium plus every owner bypass (live-stream gates, feed labels, ...).
    Identity must come from something the platform controls — the immutable
    user id — allowlisted explicitly via env. Default (unset) grants nobody.
    """
    row = row or {}
    try:
        user_id = int(row.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    return bool(user_id and user_id in _owner_user_ids())


def has_active_premium(row):
    row = row or {}
    if is_owner(row):
        return True
    if str(row.get("founder_status") or "").lower() == "active" or int(row.get("founder_number") or 0):
        return True
    if int(row.get("premium_mark_override") or row.get("premium_glow_manual_grant") or 0):
        return True
    plan = str(row.get("plan") or row.get("subscription_plan") or "").lower()
    status = str(row.get("premium_status") or row.get("subscription_status") or "").lower()
    if status in {"expired", "canceled", "cancelled", "past_due", "unpaid", "inactive"}:
        return False
    expiry = row.get("premium_expires_at") or row.get("pro_expires_at") or row.get("subscription_expires_at")
    if _future(expiry):
        return True
    # Expiry cross-check: a status frozen at 'active' by a missed provider
    # webhook must not keep premium alive once the recorded period end is
    # clearly in the past (beyond the grace window).
    if status in {"active", "trialing"} and _clearly_expired(expiry):
        return False
    return status in {"active", "trialing"} and (bool(int(row.get("is_pro") or row.get("pro_active") or 0)) or plan in {"pro", "premium"})


def identity_mark(row=None, badge_keys=None):
    row = row or {}
    founder_number = int(row.get("founder_number") or 0)
    if str(row.get("founder_status") or "").lower() == "active" or founder_number:
        number_label = f" #{founder_number}" if founder_number else ""
        return {
            "type": "founder",
            "badge_key": "founder_badge",
            "symbol": "F",
            "title": f"PulseSoc Founder{number_label}",
            "founder_number": founder_number,
        }
    if has_active_premium(row):
        # A Premium subscription is NOT identity verification. The badge title
        # used to read "Premium Verified", and the "check" variant rendered a ✓ —
        # the same affordance platforms use to signal a verified identity. That
        # tells a viewer this account's identity was confirmed when all that
        # happened is someone paid. Verification is a separate, evidence-based
        # status; conflating the two misleads viewers and devalues real
        # verification. Title and symbol now describe the subscription only.
        mark_type = str(row.get("premium_mark_type") or "").lower()
        if mark_type == "check":
            return {"type": "check", "badge_key": PREMIUM_CHECK, "symbol": "✦", "title": "PulseSoc Premium"}
        return {"type": "star", "badge_key": PREMIUM_STAR, "symbol": "✦", "title": "PulseSoc Premium"}
    return None


def user_has_premium_mark(user_or_row, loader=None):
    if isinstance(user_or_row, dict):
        return bool(identity_mark(user_or_row))
    if loader:
        return bool(identity_mark(loader(user_or_row)))
    return False


def get_premium_mark_type(user_or_row, loader=None):
    row = user_or_row if isinstance(user_or_row, dict) else (loader(user_or_row) if loader else {})
    mark = identity_mark(row)
    return (mark or {}).get("type") or ""


def grant_premium_override(user_id, mark_type="star", admin_id=0, executor=None):
    mark_type = "check" if str(mark_type).lower() == "check" else "star"
    if executor:
        return executor(
            int(user_id or 0),
            {
                "premium_mark_override": 1,
                "premium_glow_manual_grant": 1,
                "premium_mark_type": mark_type,
                "admin_id": int(admin_id or 0),
            },
        )
    return {"ok": True, "user_id": int(user_id or 0), "premium_mark_type": mark_type, "dry_run": True}


def revoke_premium_override(user_id, admin_id=0, executor=None):
    if executor:
        return executor(
            int(user_id or 0),
            {
                "premium_mark_override": 0,
                "premium_glow_manual_grant": 0,
                "admin_id": int(admin_id or 0),
            },
        )
    return {"ok": True, "user_id": int(user_id or 0), "dry_run": True}
