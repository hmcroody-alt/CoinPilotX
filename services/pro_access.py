from datetime import datetime


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


def has_pro_access(row):
    if not row:
        return False
    plan = (row.get("plan") or row.get("subscription_plan") or "free").lower()
    status = (row.get("subscription_status") or "").lower()
    is_pro_flag = int(row.get("is_pro") or 0) == 1
    if (plan == "pro" or is_pro_flag) and status in {"active", "trialing"} and _access_not_expired(row):
        return True
    # Stripe cancellations and failed payments should not remove paid access until
    # the paid-through date expires.
    if (plan == "pro" or is_pro_flag) and status in {"canceled", "past_due"} and row.get("pro_expires_at") and _access_not_expired(row):
        return True
    # Preserve access for older paid records that predate the unified status fields.
    if is_pro_flag and not status and _access_not_expired(row):
        return True
    return False


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
