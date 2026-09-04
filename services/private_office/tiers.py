"""Stage 2 — the canonical, server-authoritative Private Office tier resolver.

There is exactly ONE place in the product that decides what tier a user is on,
and it is :func:`resolve_tier`. Clients (native and web) receive the resolved
answer and render it. They never inspect ``plan``, ``is_premium``,
``subscription_status``, ``lifetime``, or any other raw field to infer a tier.
That rule is enforced by guard tests, not by convention.

Why a ladder on top of entitlements
-----------------------------------
``services/business_os/entitlements`` is capability-keyed, not tier-keyed, and
that stays true — it is the canonical grant store and this module does not
duplicate it. The ladder is expressed as four *umbrella membership keys*,
following the pattern already established for ``premium.access``:

    FREE            (no umbrella key)          rank 0
    PREMIUM         premium.access             rank 1
    PRIVATE         private.access             rank 2
    PRIVATE_OFFICE  private_office.access      rank 3

A user's effective tier is the highest-ranked umbrella key they currently hold
under canonical precedence. These are catalog DATA (rows in the entitlement
catalog), not new tables and not a new grant mechanism, so every existing
durable source — StoreKit subscription, lifetime grant, admin/manual grant,
promotional grant — flows through unchanged and keeps its provenance.

Fail closed, without lying
--------------------------
The mission requires failing closed. A naive hard fail-closed resolver would
show FREE to a paying member during a database blip, which is a different lie.
So this module fails closed on *access* (a degraded resolve grants nothing)
while reporting ``resolver_state="degraded"`` so the client can render
"temporarily unavailable" instead of "you are on Free". Callers gating a
feature look at ``features``; callers rendering UI also look at
``resolver_state``.

The PREMIUM bridge
------------------
PREMIUM has one migration fallback: when no canonical ``premium.access`` grant
exists, we consult ``entitlements.premium.is_premium()``. That function lives
INSIDE the canonical entitlement package and already reconciles the legacy
table, the identity columns, and the canonical grant store. We deliberately do
not reach out to the frozen legacy deciders listed in the ownership contract.

PRIVATE and PRIVATE_OFFICE have NO fallback. They are new tiers; there is no
legacy authority that could know about them, so anything other than a canonical
grant is a bug, and inventing a fallback would be inventing access.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from services.business_os.entitlements import facade as _facade
from services.business_os.entitlements import premium as _premium
from services.business_os.entitlements import service as _svc

_log = logging.getLogger("private_office.tiers")

# --- the ladder -------------------------------------------------------------
TIER_FREE = "FREE"
TIER_PREMIUM = "PREMIUM"
TIER_PRIVATE = "PRIVATE"
TIER_PRIVATE_OFFICE = "PRIVATE_OFFICE"

#: Ordered lowest -> highest. Index is the rank.
TIER_ORDER = (TIER_FREE, TIER_PREMIUM, TIER_PRIVATE, TIER_PRIVATE_OFFICE)

TIER_RANK = {name: rank for rank, name in enumerate(TIER_ORDER)}

#: Umbrella entitlement key per tier. FREE has none by construction: every
#: authenticated user is at least FREE, so there is nothing to grant.
UMBRELLA_KEY = {
    TIER_PREMIUM: "premium.access",
    TIER_PRIVATE: "private.access",
    TIER_PRIVATE_OFFICE: "private_office.access",
}

#: Reverse map, used when scanning a user's held keys.
_KEY_TO_TIER = {key: tier for tier, key in UMBRELLA_KEY.items()}

# --- resolver status vocabulary --------------------------------------------
STATUS_NONE = "none"                  # no umbrella grant; user is FREE
STATUS_ACTIVE = "active"
STATUS_GRACE = "grace"
STATUS_GRANDFATHERED = "grandfathered"
STATUS_ACCOUNT_HOLD = "account_hold"  # a hold outranks any paid grant
STATUS_UNAVAILABLE = "unavailable"    # resolver degraded; tier is NOT known

RESOLVER_OK = "ok"
RESOLVER_DEGRADED = "degraded"

#: Provenance recorded in ``source`` when the answer did not come from a
#: canonical grant. Kept distinct from the entitlement ``_VALID_SOURCES`` set so
#: a bridged answer is never mistaken for a real grant provenance.
SOURCE_ACCOUNT_HOLD = "account_hold"
SOURCE_PREMIUM_BRIDGE = "legacy_premium_bridge"
SOURCE_NONE = ""


def rank(tier: str) -> int:
    """Rank of ``tier``; unknown tiers rank as FREE (fail closed)."""
    return TIER_RANK.get(str(tier or "").strip().upper(), 0)


def tier_satisfies(effective_tier: str, minimum_tier: str) -> bool:
    """True iff ``effective_tier`` is at or above ``minimum_tier`` on the ladder."""
    return rank(effective_tier) >= rank(minimum_tier)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_expires(value: Any) -> Optional[str]:
    """Render a grant's ``expires_at`` as an ISO string, or None.

    Grants carry either a datetime (SQLite/psycopg both hand these back) or a
    string depending on driver. Normalising here means every client sees one
    shape and no client has to parse two.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _degraded(user_id: Any, reason: str) -> dict:
    """Build the fail-closed answer: no access, and honest about not knowing."""
    return {
        "user_id": user_id,
        "effective_tier": TIER_FREE,
        "source": SOURCE_NONE,
        "status": STATUS_UNAVAILABLE,
        "expires_at": None,
        "features": {},
        "verified_at": _utc_now_iso(),
        "resolver_state": RESOLVER_DEGRADED,
        "degraded_reason": reason,
    }


def resolve_tier(user_id: Any, *, context: Optional[dict] = None,
                 include_features: bool = True) -> dict:
    """Resolve the ONE canonical effective tier for ``user_id``.

    ``context`` is an already-loaded account row (``account_status`` /
    ``access_enabled``). Passing it avoids a second query and uses the freshest
    values the caller has, exactly as ``facade.account_hold`` intends.

    Returns the Stage 2 output contract::

        {effective_tier, source, status, expires_at, features, verified_at}

    plus ``user_id``, ``resolver_state`` and (when degraded) ``degraded_reason``.

    Never raises for storage problems: a failure is reported as a degraded,
    zero-access answer. It is a resolver, and a resolver that throws inside a
    request handler turns a database blip into a 500 on every page.
    """
    # 1. Account hold outranks every grant, paid or otherwise. This is the
    #    single authoritative hold definition; we do not restate the rule.
    try:
        hold = _facade.account_hold(user_id, context)
    except Exception:  # noqa: BLE001 — hold lookup must not break the resolver
        _log.exception("account_hold failed for user=%s", user_id)
        return _degraded(user_id, "account_hold_unavailable")
    if hold.get("on_hold"):
        held = {
            "user_id": user_id,
            "effective_tier": TIER_FREE,
            "source": SOURCE_ACCOUNT_HOLD,
            "status": STATUS_ACCOUNT_HOLD,
            "expires_at": None,
            "features": {},
            "verified_at": _utc_now_iso(),
            "resolver_state": RESOLVER_OK,
            "hold_reason": str(hold.get("reason") or ""),
        }
        if include_features:
            held["features"] = _features_for(TIER_FREE)
        return held

    # 2. One canonical read of everything this subject holds. get_entitlements
    #    already applies the fixed precedence per key, so a tier derived from it
    #    can never disagree with a per-key has_entitlement() check.
    try:
        held_keys = _svc.get_entitlements(user_id)
    except Exception:  # noqa: BLE001
        _log.exception("get_entitlements failed for user=%s", user_id)
        return _degraded(user_id, "entitlement_store_unavailable")

    best_tier = TIER_FREE
    best_grant: dict = {}
    for entry in held_keys or ():
        tier = _KEY_TO_TIER.get(str(entry.get("key") or ""))
        if tier is None:
            continue  # a non-umbrella capability key; not part of the ladder
        if rank(tier) > rank(best_tier):
            best_tier = tier
            best_grant = dict(entry)

    if best_tier != TIER_FREE:
        resolved = {
            "user_id": user_id,
            "effective_tier": best_tier,
            "source": str(best_grant.get("source") or SOURCE_NONE),
            "status": str(best_grant.get("mode") or STATUS_ACTIVE),
            "expires_at": _normalise_expires(best_grant.get("expires_at")),
            "features": {},
            "verified_at": _utc_now_iso(),
            "resolver_state": RESOLVER_OK,
        }
        if include_features:
            resolved["features"] = _features_for(best_tier)
        return resolved

    # 3. PREMIUM migration bridge, and only PREMIUM. See module docstring.
    #
    # The bridge fires ONLY when canonical is *silent* — no premium.access grant
    # rows exist for this subject at all. That is the same licence the facade
    # uses for its own legacy fallback, and the distinction is load-bearing: a
    # subject whose grant was explicitly revoked or suspended has a canonical
    # answer, and it is "no". Falling back to a legacy column there would let a
    # stale ``users.lifetime_premium`` flag resurrect access an operator
    # deliberately took away.
    try:
        _canonical_allowed, canonical_silent = _premium.canonical_premium(user_id)
        bridged = (
            bool(_premium.is_premium(user_id, context=context))
            if canonical_silent else False
        )
    except Exception:  # noqa: BLE001
        _log.exception("premium bridge failed for user=%s", user_id)
        return _degraded(user_id, "premium_bridge_unavailable")

    tier = TIER_PREMIUM if bridged else TIER_FREE
    resolved = {
        "user_id": user_id,
        "effective_tier": tier,
        "source": SOURCE_PREMIUM_BRIDGE if bridged else SOURCE_NONE,
        "status": STATUS_ACTIVE if bridged else STATUS_NONE,
        "expires_at": None,
        "features": {},
        "verified_at": _utc_now_iso(),
        "resolver_state": RESOLVER_OK,
    }
    if include_features:
        resolved["features"] = _features_for(tier)
    return resolved


def _features_for(tier: str) -> dict:
    """Feature availability map for ``tier``.

    Imported lazily: ``feature_matrix`` imports the ladder from this module, so
    a module-level import here would be a cycle. The alternative — duplicating
    the ladder constants into the matrix — is exactly the drift this mission
    exists to remove, so the local import is the cheaper trade.
    """
    from services.private_office import feature_matrix as _fm

    return _fm.availability_map(tier)


def has_tier(user_id: Any, minimum_tier: str, *,
             context: Optional[dict] = None) -> bool:
    """Convenience gate: does ``user_id`` reach ``minimum_tier``?

    Fails closed — a degraded resolve returns False. Callers that need to tell
    "denied" apart from "unknown" must call :func:`resolve_tier` and read
    ``resolver_state`` themselves.
    """
    resolved = resolve_tier(user_id, context=context, include_features=False)
    if resolved.get("resolver_state") != RESOLVER_OK:
        return False
    return tier_satisfies(resolved.get("effective_tier", TIER_FREE), minimum_tier)
