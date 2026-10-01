"""Stage 5 — a safe operational verification surface for the entitlement stack.

Stage 0 could not verify production entitlement behaviour: Railway redacts
variable values and the existing health surface refused the fetch. That gap is
what this module closes, and the way it closes it matters more than the fact
that it does.

This is NOT a debug bypass, and the shape of the API is what enforces that:

* :func:`subsystem_status` takes **no user identifier**. There is no argument
  you could pass to ask "what tier is user 4127 on", so this surface cannot be
  turned into an entitlement oracle for a specific account.
* It never returns a secret, an env var value, an API key, a provider token,
  an email, a name, or any row of user data. It returns booleans, counts, and
  configuration *modes*.
* It never grants anything and has no write path. Nothing here can raise a
  user's tier, skip a check, or force a feature on.

What it does expose is exactly the four things the mission asks for:
entitlement subsystem status, provider availability, canonical resolver health,
and counts by tier.

Counts and cost
---------------
The three paid-tier counts come from ``service.resolve_all_subjects``, which is
one indexed query per umbrella key and applies the *same* precedence as the
per-user resolver, so an operator dashboard can never show a number that
disagrees with what an individual user sees.

The FREE count is deliberately opt-in. FREE is "every account without an
umbrella grant", so counting it means counting the whole users table — a
sequential scan in production. An ops surface that gets slower as the product
succeeds is an ops surface people stop calling, so the caller has to ask.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services import db
from services.business_os.entitlements import facade as _facade
from services.business_os.entitlements import service as _svc
from services.private_office import feature_matrix as _fm
from services.private_office import tiers as _tiers

_log = logging.getLogger("private_office.status")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog_coverage(conn) -> dict:
    """Which umbrella keys are actually present in the entitlement catalog.

    A tier whose umbrella key was never seeded can never be granted, so it would
    silently resolve FREE forever. That is precisely the kind of quiet failure
    Stage 0 could not see from the outside, so it gets its own boolean.
    """
    coverage = {}
    for tier, key in _tiers.UMBRELLA_KEY.items():
        try:
            cur = conn.execute(
                "SELECT 1 FROM business_os_ent_catalog "
                "WHERE entitlement_key = ? LIMIT 1",
                (key,),
            )
            coverage[tier] = {"key": key, "in_catalog": cur.fetchone() is not None}
        except Exception:  # noqa: BLE001 — table may not exist yet
            coverage[tier] = {"key": key, "in_catalog": None,
                              "error": "catalog_unavailable"}
    return coverage


def _tier_counts(include_free: bool = False) -> dict:
    """Subjects currently holding each umbrella key, plus optional FREE.

    Uses the canonical bulk resolver so these numbers cannot drift from
    per-user answers. A tier that fails to resolve reports ``None``, not 0 —
    "we could not count" and "there are none" are different facts and
    collapsing them would make an outage look like an empty product.
    """
    counts: dict = {}
    for tier in (_tiers.TIER_PREMIUM, _tiers.TIER_PRIVATE, _tiers.TIER_PRIVATE_OFFICE):
        key = _tiers.UMBRELLA_KEY[tier]
        try:
            resolved = _svc.resolve_all_subjects(key)
            counts[tier] = sum(1 for v in resolved.values() if v.get("allowed"))
        except Exception:  # noqa: BLE001
            _log.exception("tier count failed for key=%s", key)
            counts[tier] = None

    if not include_free:
        counts[_tiers.TIER_FREE] = None
        counts["free_count_note"] = (
            "not computed: counting FREE requires a full users-table scan. "
            "Call with include_free_count=True to pay for it."
        )
        return counts

    try:
        conn = db.connect()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM users")
            total = int((cur.fetchone() or [0])[0] or 0)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        _log.exception("total user count failed")
        counts[_tiers.TIER_FREE] = None
        counts["free_count_note"] = "not computed: user count unavailable"
        return counts

    # Every subject with any umbrella grant sits above FREE. Highest-tier
    # holders also hold the lower keys (tier inheritance is catalog data), so
    # the PREMIUM count is already the full set of non-FREE subjects — as long
    # as it resolved. If it did not, do not guess.
    paid = counts.get(_tiers.TIER_PREMIUM)
    if paid is None:
        counts[_tiers.TIER_FREE] = None
        counts["free_count_note"] = "not computed: PREMIUM count unavailable"
    else:
        counts[_tiers.TIER_FREE] = max(total - paid, 0)
        counts["total_subjects"] = total
    return counts


def _provider_availability() -> dict:
    """Every feature whose honest state is 'we have no provider for this'.

    Rendering this list is what stops the product from ever answering a
    question it has no source for. If ``private_shield.breach_monitoring``
    appears here, no surface anywhere may show a clean breach state.
    """
    required = {}
    for fid, spec in _fm.FEATURES.items():
        if spec.implementation == _fm.IMPL_PROVIDER_REQUIRED:
            required[fid] = {
                "state": spec.implementation,
                "minimum_tier": spec.minimum_tier,
                "reason": spec.note,
                "provider_configured": False,
            }
    return required


def _resolver_health() -> dict:
    """Can the ladder resolve at all, and is it internally consistent?

    Exercises the pure parts of the resolver — ordering, satisfaction, matrix
    lookup — without touching any account. A user id is never involved, so this
    check cannot be repurposed to probe a person.
    """
    checks = {}
    try:
        checks["ladder_monotonic"] = all(
            _tiers.rank(_tiers.TIER_ORDER[i]) < _tiers.rank(_tiers.TIER_ORDER[i + 1])
            for i in range(len(_tiers.TIER_ORDER) - 1)
        )
        checks["unknown_tier_fails_closed"] = _tiers.rank("NOT_A_TIER") == 0
        checks["free_does_not_satisfy_premium"] = not _tiers.tier_satisfies(
            _tiers.TIER_FREE, _tiers.TIER_PREMIUM
        )
        checks["private_office_satisfies_premium"] = _tiers.tier_satisfies(
            _tiers.TIER_PRIVATE_OFFICE, _tiers.TIER_PREMIUM
        )
        # No unbuilt feature may ever read ENTITLED, even at the top tier.
        # Derived from the matrix rather than naming a feature, so shipping
        # an engine can never make this probe stale.
        checks["unbuilt_never_entitled"] = all(
            not _fm.is_entitled(spec.feature_id, _tiers.TIER_PRIVATE_OFFICE)
            for spec in _fm.FEATURES.values()
            if spec.implementation != _fm.IMPL_IMPLEMENTED
        )
        checks["provider_required_never_entitled"] = not _fm.is_entitled(
            "private_shield.breach_monitoring", _tiers.TIER_PRIVATE_OFFICE
        )
        healthy = all(bool(v) for v in checks.values())
        return {"healthy": healthy, "checks": checks, "error": ""}
    except Exception as exc:  # noqa: BLE001
        _log.exception("resolver health check raised")
        return {"healthy": False, "checks": checks, "error": type(exc).__name__}


def _feature_summary() -> dict:
    """Implementation-state census. Counts only — no per-user information."""
    summary: dict = {}
    for spec in _fm.FEATURES.values():
        summary[spec.implementation] = summary.get(spec.implementation, 0) + 1
    return {
        "total": len(_fm.FEATURES),
        "by_implementation": summary,
        "live_feature_ids": list(_fm.implemented_feature_ids()),
    }


def subsystem_status(*, include_free_count: bool = False) -> dict:
    """The Stage 5 operational verification payload.

    No user identifier is accepted and none is returned. Safe to expose behind
    an operator/admin authorisation check; still not safe to expose publicly,
    because tier counts are commercially sensitive even though they are not
    personally identifiable.
    """
    schema_present = None
    catalog = {}
    try:
        conn = db.connect()
        try:
            catalog = _catalog_coverage(conn)
            cur = conn.execute("SELECT 1 FROM business_os_ent_grants LIMIT 1")
            cur.fetchone()
            schema_present = True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        _log.exception("entitlement schema probe failed")
        schema_present = False

    return {
        "generated_at": _utc_now_iso(),
        "engine": getattr(db, "ENGINE_NAME", "unknown"),
        "entitlement_subsystem": {
            # A configuration MODE, not a secret value.
            "facade_mode": _facade.get_mode(),
            "canonical_schema_present": schema_present,
            "umbrella_key_catalog": catalog,
        },
        "resolver": _resolver_health(),
        "tier_counts": _tier_counts(include_free=include_free_count),
        "providers": _provider_availability(),
        "features": _feature_summary(),
    }


__all__ = ["subsystem_status"]
