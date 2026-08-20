from datetime import datetime, timedelta

# Grace window before a stale 'active' subscription status is treated as
# lapsed. Covers a delayed/retried provider webhook; beyond it, a recorded
# period end in the past wins over the frozen status column.
_STALE_EXPIRY_GRACE = timedelta(days=3)


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _access_not_expired(row):
    expires_at = _parse_datetime(row.get("pro_expires_at") or row.get("subscription_expires_at"))
    if not expires_at:
        return True
    now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
    return expires_at > now


def _future(value):
    parsed = _parse_datetime(value)
    if not parsed:
        return False
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return parsed > now


def _clearly_expired(row):
    """True when the row's recorded period end is past by more than the grace
    window. No expiry recorded -> False (status remains authoritative)."""
    expires_at = _parse_datetime(row.get("pro_expires_at") or row.get("subscription_expires_at"))
    if not expires_at:
        return False
    now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
    return expires_at + _STALE_EXPIRY_GRACE < now


def _trial_not_expired(row):
    return (
        _future(row.get("trial_end_date"))
        or _future(row.get("pro_expires_at"))
        or _future(row.get("subscription_expires_at"))
    )


def pro_access_type(row):
    if not row:
        return "none"
    account_status = (row.get("account_status") or "active").lower()
    if account_status != "active":
        return "none"
    plan = (row.get("plan") or row.get("subscription_plan") or "free").lower()
    status = (row.get("subscription_status") or "").lower()
    trial_status = (row.get("trial_status") or "").lower()
    if plan == "pro" and status == "active":
        # Expiry cross-check: 'active' frozen by a missed webhook must not
        # outlive a clearly-past period end (beyond the grace window).
        if _clearly_expired(row):
            return "none"
        return "paid"
    if trial_status == "active" and _future(row.get("trial_end_date")):
        return "trial"
    if status == "trialing" and _trial_not_expired(row):
        return "trial"
    return "none"


def has_pro_access(row):
    return pro_access_type(row) != "none"


def normalize_plan(row):
    if has_pro_access(row):
        return "pro"
    return "free"


def is_pro_row(row):
    return has_pro_access(row)


def free_limit_text(text, is_pro=False, max_chars=900):
    if is_pro or len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rsplit("\n", 1)[0].strip() or text[:max_chars].strip()
    return trimmed + "\n\nFree view: shortened summary."
