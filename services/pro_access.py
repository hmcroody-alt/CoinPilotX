from datetime import datetime

from services.premium_identity_engine import period_ended as _period_ended


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


def _expired(row):
    """True when the row's recorded period end is at or before now.

    Uses the one shared clock rule in ``premium_identity_engine`` rather than a
    third copy of it. This carried a three-day implicit grace window; see the
    note on ``period_ended`` for why an implicit window could not tell a late
    webhook from a lapsed subscription and so extended both.

    No expiry recorded -> False (the status column remains authoritative).
    """
    return (
        _period_ended(row.get("pro_expires_at"))
        if row.get("pro_expires_at")
        else _period_ended(row.get("subscription_expires_at"))
    )


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
        # outlive the recorded period end.
        if _expired(row):
            return "none"
        return "paid"
    if trial_status == "active" and _future(row.get("trial_end_date")):
        return "trial"
    if status == "trialing" and _trial_not_expired(row):
        return "trial"
    return "none"


def has_pro_access(row):
    return pro_access_type(row) != "none"


# Canonical-grant sources that mean "a billing provider took money" — the
# definition of the Paid Pro counter. Documented product rule: Paid Pro counts
# users whose access resolves allowed with a PAID provenance, either from the
# legacy Stripe columns (plan=pro & subscription_status=active, expiry-checked
# above) or from a live canonical grant sourced by a billing provider. Trials
# never count as paid; admin/promotion/founder grants confer access but are
# "granted", not paid.
PAID_GRANT_SOURCES = frozenset({"stripe", "apple_app_store", "google_play"})


def merged_access_type(row, canonical):
    """Single access verdict for one user: legacy columns + canonical grant.

    ``canonical`` is the user's entry from the canonical bulk resolver
    (``{"allowed", "mode", "source"}``) or None. Legacy wins when it already
    grants (Stripe-era rows are written there); otherwise a live canonical
    grant fills the gap Apple/Google purchases leave in the users table —
    those providers write canonical-only, which is exactly why the admin
    projection showed Paid Pro = 0 while paid members existed.

    Returns "paid" | "trial" | "granted" | "none". Pure: no DB access here.
    """
    legacy = pro_access_type(row)
    if legacy != "none":
        return legacy
    if not canonical or not canonical.get("allowed"):
        return "none"
    source = str(canonical.get("source") or "")
    if source in PAID_GRANT_SOURCES:
        return "paid"
    if source == "trial":
        return "trial"
    return "granted"


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
