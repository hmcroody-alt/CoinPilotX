"""Premium PulseSoc identity helpers."""

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


# Expiry is evaluated at the boundary: ``expiry <= now`` is expired, full stop.
#
# This used to carry a blanket three-day grace window, on the theory that a
# provider webhook might be late and shouldn't cost a paying member their
# benefits. The theory was sound; the implementation granted three free days of
# Premium to every genuinely lapsed account, because an implicit window cannot
# tell "webhook is late" from "subscription actually ended". A late webhook is
# already covered without guessing: a member with a live entitlement row is
# admitted by ``has_entitlement`` before these columns are ever consulted, and a
# deliberate extension is recorded explicitly as ``grace_until`` on the
# canonical grant, where ``entitlements.service._grant_phase`` honours it. The
# only population an implicit window protected was accounts with no live
# entitlement and a period end already in the past — which is precisely the set
# that must lose access.
def period_ended(value, now=None):
    """True when an expiry timestamp is present and at or before ``now``.

    Missing or unparseable values return False — no recorded end means no
    evidence of expiry, and the caller's status word stays authoritative. Only
    a timestamp we can actually read is allowed to revoke anything.
    """
    parsed = _parse_dt(value)
    if not parsed:
        return False
    if now is None:
        now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return parsed <= now


def row_period_ended(row, now=None):
    """``period_ended`` over the expiry columns a user row may carry.

    One definition, shared by the identity columns (authority C) and the legacy
    reader (authority A), so the badge a member sees and the access they get
    cannot disagree about what time it is.
    """
    row = row or {}
    for field in ("premium_expires_at", "subscription_expires_at", "pro_expires_at"):
        raw = row.get(field)
        if raw:
            return period_ended(raw, now)
    return False


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
    expiry = row.get("premium_expires_at") or row.get("pro_expires_at") or row.get("subscription_expires_at")
    # A trial is Premium while its window is open, and nothing once it closes.
    # This branch used to be missing entirely: ``premium_status='trial'`` fell
    # through to a final check that only recognised 'active'/'trialing', so a
    # member inside a live trial had access (the legacy reader honours the
    # trial) and no badge. Same row, two answers — the divergence this whole
    # recovery is about, in the other direction.
    #
    # Fails closed: a trial status with no readable end confers nothing, because
    # an unbounded trial is a lifetime grant nobody approved.
    if status in {"trial", "trialing"}:
        for field in ("trial_end_date", "premium_expires_at",
                      "pro_expires_at", "subscription_expires_at"):
            if row.get(field):
                return _future(row.get(field))
        return False
    # CANCELLED IS NOT EXPIRED. Turning off auto-renew ends the *renewal*, not
    # the period already paid for. A member who cancels on day 2 of a monthly
    # term keeps Premium until day 30 — that is what they bought, and revoking
    # it early is a refund we never issued. Only the clock ends the term.
    if status in {"canceled", "cancelled"} and _future(expiry):
        return True
    if status in {"expired", "canceled", "cancelled", "past_due", "unpaid", "inactive"}:
        return False
    if _future(expiry):
        return True
    # Expiry cross-check: a status frozen at 'active' by a missed provider
    # webhook must not keep premium alive past the recorded period end. The
    # clock decides, not the status word, and it decides at the boundary.
    if status in {"active", "trialing"} and period_ended(expiry):
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
