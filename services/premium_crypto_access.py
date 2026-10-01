"""Server-authoritative Premium gate for the crypto intelligence capabilities.

Three capabilities are sold as part of PulseSoc Premium and must be decided on
the server, never inferred by a client:

``premium.crypto.advanced_alerts``  advanced alert conditions and compound rules
``premium.crypto.portfolio``        the Premium portfolio surface
``premium.crypto.intelligence``     UNDX crypto intelligence grounded in the above

Why this module exists rather than another helper inside ``bot.py``
-------------------------------------------------------------------
``alert_worker`` runs as its own Procfile process and cannot import ``bot``. The
alert engine, the portfolio service, the UNDX tool layer and the HTTP routes all
have to reach the SAME answer, so the resolution lives in a service the worker
can import. This is a *reader*, not a fifth authority: every decision resolves
through ``business_os.entitlements.facade``, which is the module that owns the
off/shadow/canonical precedence and the account-hold rule.

Why the legacy readers matter more than the capability keys
-----------------------------------------------------------
``BUSINESS_OS_ENTITLEMENTS`` defaults to ``off`` in production, and in that mode
``facade.explain`` answers purely from ``_LEGACY_READERS``. A key that is absent
from that map has no legacy opinion, which collapses to ``False`` — for every
account, including a member who paid this morning. So these three keys are
registered against legacy premium truthiness in the facade; that registration,
not the tuple in ``premium.py``, is what makes existing subscribers inherit
crypto intelligence without repurchasing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from services import db

_log = logging.getLogger("premium.crypto_access")

ADVANCED_ALERTS = "premium.crypto.advanced_alerts"
PORTFOLIO = "premium.crypto.portfolio"
INTELLIGENCE = "premium.crypto.intelligence"

#: Every crypto capability Premium confers. Iterating this is how callers stay
#: in step with the canonical registry instead of hardcoding one key each.
CRYPTO_CAPABILITIES = (ADVANCED_ALERTS, PORTFOLIO, INTELLIGENCE)

#: Columns ``premium_visibility_engine.is_premium_user`` reads, plus the two the
#: facade needs to apply account-hold precedence without a second query.
_USER_COLUMNS = (
    "user_id", "lifetime_premium", "premium_glow_manual_grant", "premium_status",
    "subscription_plan", "subscription_status", "account_status", "access_enabled",
)

_OFF_VALUES = ("", "0", "false", "off", "no")


def _mode() -> str:
    return (os.getenv("BUSINESS_OS_ENTITLEMENTS", "") or "").strip().lower()


def load_user_row(user_id: Any) -> dict:
    """The premium-bearing columns for ``user_id``, or ``{}``.

    For the worker and any other caller that holds an id rather than the request
    user. Returns a plain dict shaped like the row ``api_account_user`` hands the
    HTTP gates, so both paths feed :func:`allowed` identical input.
    """
    try:
        conn = db.connect()
        try:
            cur = conn.execute(
                f"SELECT {', '.join(_USER_COLUMNS)} FROM users WHERE user_id = ? LIMIT 1",
                (int(user_id),),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — older schemas may lack a column
        _log.exception("premium column read failed user=%s", user_id)
        return {}
    if row is None:
        return {}
    return dict(zip(_USER_COLUMNS, tuple(row)))


def _legacy_allowed(user_row: dict) -> bool:
    try:
        from services import premium_visibility_engine as _pve
        return bool(_pve.is_premium_user(user_row))
    except Exception:  # noqa: BLE001 — legacy must never break the gate
        _log.exception("legacy premium read failed")
        return False


def allowed(user_row: Optional[dict], key: str) -> bool:
    """Is this account entitled to ``key``?

    Mirrors the strangler shape the existing premium gates use:

      * off (default) -> the legacy answer, unchanged.
      * shadow        -> serve legacy; the facade records the canonical divergence.
      * canonical     -> the facade decides, with a legacy fallback when canonical
                         is silent and an account hold overriding any grant.

    Any failure inside the entitlement path falls back to the legacy answer, so a
    problem in the new system can never revoke a paid member's access.
    """
    if not user_row:
        return False
    legacy = _legacy_allowed(user_row)
    try:
        mode = _mode()
        if mode in _OFF_VALUES:
            return legacy
        from services.business_os.entitlements import facade as _facade
        uid = int(user_row.get("user_id") or 0)
        # Hand over the account state already in memory: the facade applies
        # hold precedence from it rather than issuing a second read, and a route
        # cannot bypass the suspension rule by omitting it.
        context = {
            "account_status": user_row.get("account_status"),
            "access_enabled": user_row.get("access_enabled"),
        }
        if mode == "shadow":
            try:
                _facade.shadow_compare(uid, key, context=context)
            except Exception:  # noqa: BLE001
                _log.exception("shadow_compare failed uid=%s key=%s", uid, key)
            return legacy
        return bool(_facade.check(uid, key, context=context))
    except Exception:  # noqa: BLE001
        _log.exception("crypto entitlement check failed key=%s; using legacy", key)
        return legacy


def allowed_for_user_id(user_id: Any, key: str) -> bool:
    """:func:`allowed` for callers that hold only an id (the alert worker)."""
    return allowed(load_user_row(user_id), key)


def capabilities(user_row: Optional[dict]) -> dict:
    """``{key: bool}`` for all three capabilities, for status payloads."""
    return {key: allowed(user_row, key) for key in CRYPTO_CAPABILITIES}
