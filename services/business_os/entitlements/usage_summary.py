"""Premium usage summary — REAL counts only ("Your Premium this month").

Feeds the native Premium Command Center's "usage" and "recommended" modules.

Design rules (mission golden rule: DO NOT FAKE PREMIUM VALUE):

* Every number is a live query against the canonical domain table that the
  feature itself reads (``alert_rules``, ``alert_events``, ``portfolio_items``,
  ``pulse_profile_themes``, ``pulse_premium_profiles``, ``verification_requests``,
  ``business_os_ent_usage``). Nothing is estimated, cached, or interpolated.
* A signal whose source query fails (missing table/column on an older DB) is
  OMITTED from the payload, never zero-filled. Absence of data is shown as
  absence, not as a fabricated count.
* Recommendations ("unused benefits") are derived exclusively from the signals
  actually collected, and only for capabilities the readiness registry says are
  sellable — the same advertising authority the benefits list uses.
* This module never decides membership. Callers pass the membership decision
  resolved by ``premium.resolve()``; recommendations are only produced for
  members (a non-member gets an empty list, not an upsell disguised as usage).

Framework-agnostic: returns plain dicts; bot.py owns auth and HTTP.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from services import db
from services.business_os.entitlements import readiness as _readiness

_log = logging.getLogger("business_os.entitlements.usage_summary")

#: Free portfolio ceiling. Mirrors ``portfolio_service.FREE_LIMITS`` — kept as a
#: literal here (not an import) so a portfolio_service import failure cannot
#: break the whole summary; drift is guarded by tests.
_PORTFOLIO_FREE_LIMIT = 3


def _month_key(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _query_one(sql: str, params: tuple) -> Any:
    conn = db.connect()
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return row
    finally:
        conn.close()


def _query_all(sql: str, params: tuple) -> list:
    conn = db.connect()
    try:
        return list(conn.execute(sql, params).fetchall())
    finally:
        conn.close()


# --- individual signals ------------------------------------------------------
# Each returns a signal dict or None (None -> omitted). All are defensive: any
# exception is logged and the signal is dropped rather than faked.

def _sig_advanced_alert_rules(user_id: int) -> Optional[dict]:
    row = _query_one(
        "SELECT COUNT(*) FROM alert_rules WHERE user_id=? AND COALESCE(active,1)=1 "
        "AND deleted_at IS NULL AND advanced_conditions IS NOT NULL "
        "AND TRIM(advanced_conditions) NOT IN ('', 'null', '[]', '{}')",
        (user_id,),
    )
    return {
        "key": "advanced_alert_rules",
        "capability": "premium.crypto.advanced_alerts",
        "label": "Advanced alert rules active",
        "kind": "count",
        "scope": "current",
        "value": int(row[0] if row else 0),
    }


def _sig_alerts_triggered_month(user_id: int, month: str) -> Optional[dict]:
    row = _query_one(
        "SELECT COUNT(*) FROM alert_events WHERE user_id=? AND created_at >= ?",
        (user_id, f"{month}-01"),
    )
    return {
        "key": "alerts_triggered_month",
        "capability": "premium.crypto.advanced_alerts",
        "label": "Alerts triggered this month",
        "kind": "count",
        "scope": "month",
        "value": int(row[0] if row else 0),
    }


def _sig_portfolio_holdings(user_id: int) -> Optional[dict]:
    row = _query_one(
        "SELECT COUNT(*) FROM portfolio_items WHERE user_id=?", (user_id,)
    )
    count = int(row[0] if row else 0)
    return {
        "key": "portfolio_holdings",
        "capability": "premium.crypto.portfolio",
        "label": "Portfolio holdings tracked",
        "kind": "count",
        "scope": "current",
        "value": count,
        "free_limit": _PORTFOLIO_FREE_LIMIT,
        "beyond_free_limit": count > _PORTFOLIO_FREE_LIMIT,
    }


def _sig_profile_theme(user_id: int) -> Optional[dict]:
    row = _query_one(
        "SELECT theme_key FROM pulse_profile_themes "
        "WHERE user_id=? AND active=1 ORDER BY updated_at DESC LIMIT 1",
        (user_id,),
    )
    return {
        "key": "profile_theme",
        "capability": "premium.profile.customization",
        "label": "Premium profile theme",
        "kind": "state",
        "scope": "current",
        "value": str(row[0]) if row and row[0] else None,
        "in_use": bool(row and row[0]),
    }


def _sig_identity_effect(user_id: int) -> Optional[dict]:
    row = _query_one(
        "SELECT aura_style FROM pulse_premium_profiles "
        "WHERE user_id=? AND status='active' LIMIT 1",
        (user_id,),
    )
    return {
        "key": "identity_effect",
        "capability": "premium.identity.effects",
        "label": "Identity effect (aura)",
        "kind": "state",
        "scope": "current",
        "value": str(row[0]) if row and row[0] else None,
        "in_use": bool(row and row[0]),
    }


def _sig_blue_check_application(user_id: int) -> Optional[dict]:
    row = _query_one(
        "SELECT status FROM verification_requests "
        "WHERE user_id=? AND verification_type='blue_check' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    return {
        "key": "blue_check_application",
        "capability": "premium.verification.blue_check.apply",
        "label": "Blue Check application",
        "kind": "state",
        "scope": "current",
        "value": str(row[0]) if row and row[0] else None,
        "in_use": bool(row and row[0]),
    }


def _sig_metered_usage(user_id: int, month: str) -> list[dict]:
    """Real rows from the atomic quota engine, current month/day buckets only.

    Today no premium.* capability is metered (check_and_consume has no premium
    call sites), so this is normally empty — and an empty list is the honest
    answer, not a placeholder.
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = _query_all(
        "SELECT entitlement_key, period_key, used FROM business_os_ent_usage "
        "WHERE subject_type='user' AND subject_id=? AND period_key IN (?, ?) "
        "AND entitlement_key LIKE 'premium.%'",
        (str(user_id), month, day),
    )
    return [
        {
            "key": f"metered:{r[0]}:{r[1]}",
            "capability": str(r[0]),
            "label": f"{r[0]} used",
            "kind": "count",
            "scope": "month" if r[1] == month else "day",
            "value": int(r[2] or 0),
        }
        for r in rows
    ]


_SIGNAL_SOURCES: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("advanced_alert_rules", _sig_advanced_alert_rules),
    ("portfolio_holdings", _sig_portfolio_holdings),
    ("profile_theme", _sig_profile_theme),
    ("identity_effect", _sig_identity_effect),
    ("blue_check_application", _sig_blue_check_application),
)


# --- recommendations ---------------------------------------------------------
def _recommendations(signals: dict[str, dict], is_member: bool) -> list[dict]:
    """Unused-benefit discovery. Members only; sellable capabilities only;
    every item points at a signal that was actually collected."""
    if not is_member:
        return []
    recs: list[dict] = []

    def _sellable(key: str) -> bool:
        try:
            return bool(_readiness.sellable(key))
        except Exception:  # noqa: BLE001 — fail closed: don't recommend
            return False

    s = signals.get("advanced_alert_rules")
    if s and s["value"] == 0 and _sellable("premium.crypto.advanced_alerts"):
        recs.append({
            "capability": "premium.crypto.advanced_alerts",
            "signal": "advanced_alert_rules",
            "reason": "no_advanced_rules",
            "title": "You haven't used advanced alerts yet",
            "body": "Compound and windowed alert rules are included in your membership.",
        })
    s = signals.get("portfolio_holdings")
    if s and not s.get("beyond_free_limit") and _sellable("premium.crypto.portfolio"):
        recs.append({
            "capability": "premium.crypto.portfolio",
            "signal": "portfolio_holdings",
            "reason": "within_free_ceiling",
            "title": "Your portfolio fits the free tier",
            "body": f"Membership removes the {_PORTFOLIO_FREE_LIMIT}-holding ceiling — track everything.",
        })
    s = signals.get("profile_theme")
    if s and not s.get("in_use") and _sellable("premium.profile.customization"):
        recs.append({
            "capability": "premium.profile.customization",
            "signal": "profile_theme",
            "reason": "no_theme_set",
            "title": "No Premium profile theme set",
            "body": "Profile themes and layouts are included in your membership.",
        })
    s = signals.get("identity_effect")
    if s and not s.get("in_use") and _sellable("premium.identity.effects"):
        recs.append({
            "capability": "premium.identity.effects",
            "signal": "identity_effect",
            "reason": "no_effect_set",
            "title": "No identity effect active",
            "body": "Auras and identity effects are included in your membership.",
        })
    s = signals.get("blue_check_application")
    if s and not s.get("in_use") and _sellable("premium.verification.blue_check.apply"):
        recs.append({
            "capability": "premium.verification.blue_check.apply",
            "signal": "blue_check_application",
            "reason": "not_applied",
            "title": "You can apply for Blue Check",
            "body": "Membership includes access to the application — approval is "
                    "reviewed and never purchasable.",
        })
    return recs


# --- public API --------------------------------------------------------------
def summarize(user_id: Any, *, is_member: bool) -> dict:
    """Collect real usage signals + honest recommendations for one user.

    ``is_member`` must come from the canonical resolver
    (``premium.resolve()['is_premium']``); this module takes it as input rather
    than becoming another membership authority.
    """
    uid = int(user_id)
    month = _month_key()
    signals: dict[str, dict] = {}
    omitted: list[str] = []

    for name, fn in _SIGNAL_SOURCES:
        try:
            sig = fn(uid)
            if sig is not None:
                signals[name] = sig
        except Exception:  # noqa: BLE001 — omit, never fabricate
            _log.exception("usage signal %s unavailable for user=%s", name, uid)
            omitted.append(name)

    try:
        signals["alerts_triggered_month"] = _sig_alerts_triggered_month(uid, month)
    except Exception:  # noqa: BLE001
        _log.exception("usage signal alerts_triggered_month unavailable user=%s", uid)
        omitted.append("alerts_triggered_month")

    metered: list[dict] = []
    try:
        metered = _sig_metered_usage(uid, month)
    except Exception:  # noqa: BLE001
        _log.exception("metered usage unavailable user=%s", uid)
        omitted.append("metered_usage")

    return {
        "month": month,
        "signals": list(signals.values()) + metered,
        "omitted": omitted,  # sources that could not be measured (shown as absent)
        "recommendations": _recommendations(signals, is_member),
        "provenance": "live_counts",  # every value queried at request time
    }
