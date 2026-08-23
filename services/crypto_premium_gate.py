"""Single server-side gate for the Premium Crypto Intelligence capabilities.

Every crypto-intelligence workstream (alert engine, portfolio, UNDX, mobile
routes) imports THIS module and nothing else to decide whether a user may use a
premium crypto feature. There is exactly one entitlement system behind it: the
canonical registry in ``services.business_os.entitlements`` (with its legacy
bridge in ``services.premium_entitlement_service``). This module adds NO policy
of its own — it resolves through the existing facade so that mode flags
(``BUSINESS_OS_ENTITLEMENTS``), account holds, the Apple/Google provider bridge
and the legacy fallback all apply exactly as they do for every other premium
capability.

Contract
--------
* ``CAP_CRYPTO_ADVANCED_ALERTS`` / ``CAP_CRYPTO_PORTFOLIO`` — the canonical
  registry keys (see ``premium.PREMIUM_CAPABILITIES``). Both are conferred by
  the existing Premium plans, so ``com.pulsesoc.premium.monthly`` / ``.annual``
  purchases inherit them with no new SKU.
* ``has_crypto_capability(user_id, capability)`` — server-authoritative check.
  DENIES on ImportError or any resolution failure; never fails open.
* ``premium_required_response(capability)`` — the ONLY denial payload callers
  may return. Return it with HTTP 200, never a raw 403, so mobile clients can
  render an upsell instead of treating the response as a transport error.

Import safety: no flask/stripe (or any heavy dependency) at module top level;
everything is lazy-imported inside functions so tests and workers can import
this module in a bare stdlib environment.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("crypto_premium_gate")

# Canonical registry keys. Must match
# services.business_os.entitlements.premium.PREMIUM_CAPABILITIES exactly —
# tests/crypto_premium/test_crypto_premium_gate.py asserts this.
CAP_CRYPTO_ADVANCED_ALERTS = "premium.crypto.advanced_alerts"
CAP_CRYPTO_PORTFOLIO = "premium.crypto.portfolio_intelligence"

_CRYPTO_CAPABILITIES = frozenset({CAP_CRYPTO_ADVANCED_ALERTS, CAP_CRYPTO_PORTFOLIO})

#: Human copy per capability for the premium-required payload.
_CAPABILITY_MESSAGES = {
    CAP_CRYPTO_ADVANCED_ALERTS: (
        "Advanced crypto alerts are a PulseSoc Premium feature. "
        "Upgrade to Premium to unlock them."
    ),
    CAP_CRYPTO_PORTFOLIO: (
        "Crypto portfolio intelligence is a PulseSoc Premium feature. "
        "Upgrade to Premium to unlock it."
    ),
}
_DEFAULT_MESSAGE = "This feature requires PulseSoc Premium."


def _is_owner(user_id: Any) -> bool:
    """The EXISTING ``PULSESOC_OWNER_USER_IDS`` allowlist, via the one place it
    is defined (``premium_identity_engine.is_owner``). This is not a second
    bypass mechanism — it reuses the same helper the live-stream and feed gates
    already consult. Best-effort: a failure here simply falls through to the
    normal entitlement resolution (it never grants and never denies by itself).
    """
    try:
        from services import premium_identity_engine as _pie
        return bool(_pie.is_owner({"user_id": int(user_id)}))
    except Exception:  # noqa: BLE001 — bypass is optional, resolution is not
        return False


def has_crypto_capability(user_id: Any, capability: str) -> bool:
    """Server-authoritative: does ``user_id`` hold ``capability``?

    Resolves through ``services.business_os.entitlements.facade.check`` — the
    same migration-aware path every other premium capability uses (canonical
    grants when the flag is on, legacy premium truth including the Apple/Google
    provider bridge when it is off, account holds on top).

    Fails CLOSED: an unknown capability key, an ImportError, or any resolution
    failure returns False. Callers must then return
    :func:`premium_required_response` with HTTP 200.
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    if uid <= 0:
        return False
    if capability not in _CRYPTO_CAPABILITIES:
        _log.warning("unknown crypto capability denied: %r", capability)
        return False
    if _is_owner(uid):
        return True
    try:
        from services.business_os.entitlements import facade as _facade
        return bool(_facade.check(uid, capability))
    except ImportError:
        _log.exception("entitlement facade unavailable — denying %s", capability)
        return False
    except Exception:  # noqa: BLE001 — never fail open
        _log.exception(
            "crypto capability resolution failed user=%s cap=%s — denying",
            user_id, capability,
        )
        return False


def premium_required_response(capability: str) -> dict:
    """The canonical premium-upsell denial payload.

    Callers return this with HTTP 200 (never a raw 403) so clients distinguish
    "you need Premium" from an auth/transport failure and can show the paywall.
    """
    return {
        "ok": False,
        "code": "premium_required",
        "capability": str(capability),
        "message": _CAPABILITY_MESSAGES.get(capability, _DEFAULT_MESSAGE),
    }
