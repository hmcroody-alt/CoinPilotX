"""One access decision for Private Office capabilities, shared by every surface.

The HTTP routes and the UNDX capability executor both have to answer the same
question — *may this member reach this capability right now?* — and they answer
it in different vocabularies: one returns an HTTP status, the other a
``ToolResult``. What must not differ is the decision itself. Two copies of this
logic would eventually disagree, and the shape of that disagreement is
predictable: the screen would hide a row the agent would happily read out, or
the agent would refuse something the screen had already opened.

So the decision is computed once, here, and each surface renders it. This module
issues no SQL and imports no transport. It reads the resolved tier it is handed
and the canonical feature matrix, and returns a small record.

Four outcomes, and they are four rather than two on purpose:

``ALLOW``
    Built, enabled, and within the member's tier.
``UNAVAILABLE``
    The resolver did not answer. This is *not* a denial. "We could not look" and
    "we looked and you may not have this" must never share a shape — the member
    most likely to hit a degraded resolve is the one who paid for the thing, and
    telling them they lack access is by far the worse of the two mistakes.
``NOT_IMPLEMENTED`` / ``FEATURE_DISABLED``
    Nothing exists to reach. No upgrade is offered, because there is nothing to
    sell; an upgrade prompt here would be a lie told to take money.
``NOT_ENTITLED``
    A real capability, out of reach. This is the only outcome that carries a
    ``minimum_tier``.

The implementation state is consulted *before* the tier, which is what makes
"PRIVATE_OFFICE does not magically make an unbuilt feature available" a property
of the code rather than a promise in a document.
"""

from __future__ import annotations

from services.private_office import feature_matrix as _matrix
from services.private_office import tiers as _tiers

ALLOW = "ALLOW"
UNAVAILABLE = "UNAVAILABLE"
NOT_IMPLEMENTED = _matrix.AVAIL_NOT_IMPLEMENTED
FEATURE_DISABLED = _matrix.AVAIL_FEATURE_DISABLED
NOT_ENTITLED = _matrix.AVAIL_NOT_ENTITLED

#: Decisions that mean "no rows may be read". Callers branch on membership here
#: rather than on ``!= ALLOW`` so that adding a fifth outcome later forces a
#: deliberate choice at every surface instead of silently defaulting to refusal.
REFUSALS = frozenset({UNAVAILABLE, NOT_IMPLEMENTED, FEATURE_DISABLED, NOT_ENTITLED})


def decide(resolved: dict, feature_id: str) -> dict:
    """Whether ``feature_id`` is reachable given an already-resolved tier.

    ``resolved`` is the record returned by ``tiers.resolve_tier``. It is passed
    in rather than resolved here so a surface that already resolved once — the
    overview endpoint needs the tier for other reasons too — does not resolve
    twice and risk two different answers inside one response.
    """
    record = {
        "decision": ALLOW,
        "feature_id": feature_id,
        "effective_tier": "",
        "implementation": "",
        "minimum_tier": "",
    }

    if (resolved or {}).get("resolver_state") != _tiers.RESOLVER_OK:
        # No tier is reported. Naming one here would be inventing the answer the
        # resolver just failed to produce.
        record["decision"] = UNAVAILABLE
        return record

    tier = resolved.get("effective_tier") or _matrix.TIER_FREE
    available = _matrix.availability(feature_id, tier)
    record["effective_tier"] = tier
    record["implementation"] = available["implementation"]
    record["minimum_tier"] = available["minimum_tier"]

    if available["availability"] == _matrix.AVAIL_ENTITLED:
        return record

    record["decision"] = available["availability"]
    if record["decision"] in (NOT_IMPLEMENTED, FEATURE_DISABLED):
        # Nothing to sell. The minimum tier is dropped rather than carried, so a
        # surface cannot accidentally render an upgrade prompt from it.
        record["minimum_tier"] = ""
    return record


def allowed(resolved: dict, feature_id: str) -> bool:
    """Shorthand for surfaces that only need the boolean."""
    return decide(resolved, feature_id)["decision"] == ALLOW


__all__ = [
    "ALLOW", "UNAVAILABLE", "NOT_IMPLEMENTED", "FEATURE_DISABLED",
    "NOT_ENTITLED", "REFUSALS", "decide", "allowed",
]
