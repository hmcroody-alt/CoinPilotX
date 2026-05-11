"""Shared billing helpers for CoinPilotXAI.

The Flask app remains the Stripe webhook entry point, but this module keeps the
business rules importable by web routes, admin tools, and future workers.
"""

from datetime import datetime

from . import pro_access


VALID_PRO_STATUSES = {"active", "trialing"}


def has_billable_pro_access(user):
    return pro_access.has_pro_access(user or {})


def normalize_subscription_status(status):
    value = (status or "").strip().lower()
    if value in VALID_PRO_STATUSES:
        return value
    if value in {"past_due", "unpaid"}:
        return "past_due"
    if value in {"canceled", "incomplete_expired"}:
        return "canceled"
    return value or "inactive"


def stripe_timestamp_to_iso(value):
    if not value:
        return None
    try:
        return datetime.utcfromtimestamp(int(value)).isoformat()
    except Exception:
        return None

