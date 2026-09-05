"""Stages 4, 10, 11 — the Private Office product surface, composed once.

What this module is for
-----------------------
``facts.py`` owns the store. ``feature_matrix.py`` owns what exists.
``tiers.py`` owns who may have it. None of the three owns the *product*: the
thing a member opens, which has a heading, a set of sections, and per-row copy
that has to be true.

That composition kept wanting to live in a route handler, and route handlers
are where honesty goes to die — one endpoint renders provenance, the next one
forgets, and six weeks later two screens disagree about whether a fact is
verified. So the shaping happens here, once, and the route pack does transport
only.

Two rules hold everything else up:

**Nothing here issues SQL.** Every read goes through ``facts.py``, which is the
only module allowed to name a private table. A static guard enforces that
(``tests/private_office/test_private_write_boundary.py``), and this module is
deliberately written to have nothing for that guard to find.

**Entitlement never manufactures a capability.** The entry state is computed
from the *implementation* column first and the tier second, in that order, so
a PRIVATE_OFFICE member is told the truth about an unbuilt feature rather than
being shown a door with nothing behind it. This is the same precedence
``feature_matrix.availability`` already applies; it is restated at the product
layer because the product layer is where the temptation to override it lives.

Why a row is projected rather than returned
-------------------------------------------
A stored fact carries ``owner_user_id``, ``fact_key``, ``conflict_id`` and a
provenance ``locator`` that may be an internal storage path. None of that is
for the member, and a projection that starts from "return the row minus a
denylist" leaks the next column somebody adds. :func:`project_fact` therefore
builds a new dict from an allowlist: a field that is not named here does not
reach a client, including fields that do not exist yet.

Why verification state is not provenance type
----------------------------------------------
"Why does PulseSoc know this?" is a question about trust, and the store answers
it in eight words (``VERIFIED``, ``INFERRED``, ``STALE``, …) that are precise
for a retrieval engine and wrong for a person. ``ESTIMATED`` and ``INFERRED``
are a meaningful distinction to a conflict resolver and the same sentence to a
member: we worked it out, nobody confirmed it. :func:`verification_state`
collapses the eight into five honest buckets and keeps the raw type alongside,
so the display is readable and the diagnostic is not lost.

Crucially the collapse never rounds *up*. ``USER_ASSERTED`` becomes
SELF_REPORTED, not VERIFIED — the member typed it, and telling them we verified
their own typing is the exact species of flattery that makes a fact store
useless the first time it matters.
"""

from __future__ import annotations

from typing import Optional

from services.private_office import facts as _facts
from services.private_office import feature_matrix as _fm
from services.private_office import model as _model
from services.private_office.tiers import (
    TIER_FREE,
    TIER_PREMIUM,
    TIER_PRIVATE,
    TIER_PRIVATE_OFFICE,
)

# --- the umbrella ------------------------------------------------------------

#: The product entry, not a capability. Stage 10: this describes whether the
#: room can be opened, which is a different question from whether everything
#: inside it is built. Nothing may treat this id as proof of a working feature.
OFFICE_FEATURE_ID = "private_office"

#: Children in display order. This is the room's contents, and the order is a
#: product decision rather than an alphabetisation: the capability that works
#: comes first, and the unbuilt ones follow in the order they are planned.
OFFICE_CHILD_IDS: tuple[str, ...] = (
    "private_facts",
    "capital_graph",
    "private_office.operations",
    "private_briefings",
    "relationship_intelligence",
    "private_shield",
    "private_office.document.extraction",
    "human_concierge",
)

# --- entry states ------------------------------------------------------------

# These are wire values, not internal names. They are emitted verbatim as
# ``private_office.state`` by /api/private-office/overview and are matched
# against a closed whitelist in the native client
# (mobile-native/src/api/privateOffice.ts, ENTRY_STATES). The ``ENTRY_``
# prefix is load-bearing on both sides and must stay.
#
# They were once the bare words AVAILABLE / UPGRADE_REQUIRED / UNAVAILABLE /
# UNKNOWN, which no client recognised: the parser degraded every one of them to
# ENTRY_UNKNOWN and PremiumCenterScreen renders no row for that, so the Private
# Office entry was invisible to every member at every tier regardless of
# entitlement. Nothing errored, because "absent" is the deliberate rendering of
# "unconfirmed" — the failure had no symptom other than a missing row.
#
# Deliberately *not* shared with the per-child ``reason`` vocabulary below,
# which is bare on purpose ("AVAILABLE", "UPGRADE_REQUIRED", …) and which the
# client whitelists separately as REASON_WORDS. Two vocabularies, two
# whitelists; collapsing them breaks the half you were not looking at.

#: At least one child is genuinely usable by this member right now.
ENTRY_AVAILABLE = "ENTRY_AVAILABLE"
#: Something real exists behind the door, and this member's tier does not reach
#: it. This is the only state in which an upgrade prompt is honest.
ENTRY_UPGRADE_REQUIRED = "ENTRY_UPGRADE_REQUIRED"
#: Nothing inside is built. Not for this member, not for anyone. No upgrade
#: changes it, so no upgrade may be offered.
ENTRY_UNAVAILABLE = "ENTRY_UNAVAILABLE"
#: We could not resolve the member's tier. Distinct from UNAVAILABLE on
#: purpose: "we could not look" and "we looked and there is nothing" must never
#: share a shape.
ENTRY_UNKNOWN = "ENTRY_UNKNOWN"

# --- verification vocabulary -------------------------------------------------

VERIFICATION_VERIFIED = "VERIFIED"
VERIFICATION_SOURCED = "SOURCED"
VERIFICATION_SELF_REPORTED = "SELF_REPORTED"
VERIFICATION_ESTIMATED = "ESTIMATED"
VERIFICATION_NEEDS_REVIEW = "NEEDS_REVIEW"

_VERIFICATION_BY_PROVENANCE: dict[str, str] = {
    _model.PROVENANCE_VERIFIED: VERIFICATION_VERIFIED,
    _model.PROVENANCE_PROVIDER_ASSERTED: VERIFICATION_SOURCED,
    _model.PROVENANCE_DOCUMENT_EXTRACTED: VERIFICATION_SOURCED,
    _model.PROVENANCE_USER_ASSERTED: VERIFICATION_SELF_REPORTED,
    _model.PROVENANCE_INFERRED: VERIFICATION_ESTIMATED,
    _model.PROVENANCE_ESTIMATED: VERIFICATION_ESTIMATED,
    _model.PROVENANCE_STALE: VERIFICATION_NEEDS_REVIEW,
    _model.PROVENANCE_CONFLICTING: VERIFICATION_NEEDS_REVIEW,
}


def verification_state(provenance_type: object) -> str:
    """Member-facing trust bucket for a provenance type.

    An unrecognised provenance is NEEDS_REVIEW rather than anything reassuring.
    A word this module has not been taught is, by definition, a claim nobody
    here has reasoned about, and the safe rendering of that is "look at this",
    not "trust this".
    """
    known = _model.normalize_provenance(provenance_type)
    if not known:
        return VERIFICATION_NEEDS_REVIEW
    return _VERIFICATION_BY_PROVENANCE.get(known, VERIFICATION_NEEDS_REVIEW)


# --- fact projection ---------------------------------------------------------


def project_provenance(fact: dict) -> dict:
    """The answer to "why does PulseSoc know this?", safe to show the owner.

    Carries the source *type* and the source *id* — an identifier of the record
    in the originating system, which is what makes the claim traceable — and
    deliberately drops ``locator``. The locator is an internal pointer
    (``"page=4;section=3.1"`` today, a storage key tomorrow) and it is the field
    most likely to become a path into private storage. ``has_source_document``
    preserves the only part of it a member needs: whether there is a document to
    go and look at.

    ``observed_at`` comes from the row rather than the encoded reference when
    the reference is empty, because a fact always has an observation time even
    when its provenance record is sparse, and rendering a blank date next to a
    value invites the reader to assume it is current.
    """
    ref = dict(fact.get("provenance") or {})
    observed = str(ref.get("observed_at") or fact.get("observed_at") or "")
    provenance_type = str(fact.get("provenance_type") or "")
    confidence = ref.get("confidence")
    if confidence in (None, ""):
        confidence = fact.get("confidence")

    return {
        "source_type": str(ref.get("source_type") or ""),
        "source_id": str(ref.get("source_id") or ""),
        "has_source_document": bool(str(ref.get("locator") or "").strip()),
        "provenance_type": provenance_type,
        "verification": verification_state(provenance_type),
        "observed_at": observed,
        "confidence": round(float(confidence or 0.0), 4),
    }


#: Every scalar field of a stored fact that may reach a client, named once.
#: Allowlist, not denylist — see the module docstring.
_PROJECTED_FIELDS: tuple[str, ...] = (
    "fact_type",
    "value_type",
    "domain",
    "sensitivity",
    "subject_type",
    "observed_at",
    "valid_from",
    "valid_to",
    "lifecycle_state",
)


def project_fact(fact: dict) -> dict:
    """One stored fact, shaped for display. Never returns the raw row.

    ``id`` is included because the client needs a stable handle, and it is safe
    to include precisely because every read is owner-scoped: an id the caller
    was not given is an id that returns nothing, so it is a handle rather than
    an address.

    ``subject_id`` is *not* projected. It is an internal node handle, it is not
    meaningful to a member, and it is the one field in the row that could let a
    client start correlating facts to graph objects it was never shown.
    """
    if not isinstance(fact, dict):
        return {}

    out: dict = {"id": int(fact.get("id") or 0)}
    for name in _PROJECTED_FIELDS:
        out[name] = "" if fact.get(name) is None else str(fact.get(name))

    out["value"] = str(fact.get("typed_value") or "")
    number = fact.get("value_number")
    out["value_number"] = None if number is None else float(number)

    out["provenance"] = project_provenance(fact)

    freshness = dict(fact.get("freshness") or {})
    out["freshness"] = {
        "stale": bool(freshness.get("stale")),
        "age_days": freshness.get("age_days"),
        "horizon_days": freshness.get("horizon_days"),
    }
    return out


def project_facts(rows) -> list:
    """Project a list of stored facts, dropping anything that is not a row."""
    return [project_fact(row) for row in (rows or ()) if isinstance(row, dict)]


# --- domain summary ----------------------------------------------------------


def domain_summary(cur, *, owner_user_id: int, sensitivity_ceiling: Optional[str] = None) -> list:
    """Every domain, with this owner's active fact count. Complete, in order.

    Returns all seven domains including the empty ones so the screen can say
    "LEGAL — no information yet" from data rather than from a hardcoded list of
    headings it invented. A client that has to enumerate the domains itself has
    become a second authority on the vocabulary, and the two will drift the
    first time a domain is added.

    ``empty`` is carried explicitly rather than left as ``count == 0`` for the
    caller to infer, because "no information yet" and "0 results" are different
    sentences and the copy layer should not be re-deriving which one it is.
    """
    ceiling = sensitivity_ceiling or _model.SENSITIVITY_RESTRICTED
    counts = _facts.count_facts_by_domain(
        cur, owner_user_id=owner_user_id, sensitivity_ceiling=ceiling
    )
    return [
        {"domain": name, "count": int(counts.get(name, 0)), "empty": int(counts.get(name, 0)) == 0}
        for name in _model.DOMAINS
    ]


# --- product state -----------------------------------------------------------


def _child_state(feature_id: str, effective_tier: str) -> dict:
    """One child row, with enough state for the client to render it honestly.

    ``opens`` is the single question a tile should ask. It is true only for
    ENTITLED, so a NOT_ENTITLED, FEATURE_DISABLED, NOT_IMPLEMENTED or
    PROVIDER_REQUIRED row cannot be made tappable by a client reading a
    different field and drawing its own conclusion.

    ``reason`` distinguishes the two flavours of "not yet" that the availability
    vocabulary collapses into one word. NOT_IMPLEMENTED and PROVIDER_REQUIRED
    both surface as ``NOT_IMPLEMENTED`` in ``availability`` — the difference
    survives only in ``implementation`` — and they are different promises:
    "we have not built this" versus "we cannot answer this at all until an
    outside provider is connected".
    """
    resolved = _fm.availability(feature_id, effective_tier)
    availability = resolved["availability"]
    implementation = resolved["implementation"]

    if availability == _fm.AVAIL_ENTITLED:
        reason = "AVAILABLE"
    elif implementation == _fm.IMPL_PROVIDER_REQUIRED:
        reason = "PROVIDER_REQUIRED"
    elif availability == _fm.AVAIL_NOT_IMPLEMENTED:
        reason = "NOT_IMPLEMENTED"
    elif availability == _fm.AVAIL_FEATURE_DISABLED:
        reason = "TEMPORARILY_DISABLED"
    else:
        reason = "UPGRADE_REQUIRED"

    return {
        "feature_id": resolved["feature_id"],
        "availability": availability,
        "implementation": implementation,
        "minimum_tier": resolved["minimum_tier"],
        "reason": reason,
        "opens": availability == _fm.AVAIL_ENTITLED,
    }


def product_state(effective_tier: object, *, resolver_ok: bool = True) -> dict:
    """The Private Office entry state for one resolved tier.

    ``resolver_ok=False`` short-circuits to ENTRY_UNKNOWN with an empty child
    list. A degraded resolve must not be rendered as "you do not have this" —
    that is a confident answer to a question we did not manage to ask, told to
    exactly the person who may have paid for it.

    The entry opens when at least one child is ENTITLED. It offers an upgrade
    only when at least one child is *built* and out of reach, which is the only
    circumstance in which paying more would actually change what the member
    sees. When nothing inside is built, the entry is UNAVAILABLE at every tier
    including PRIVATE_OFFICE — the top of the ladder does not conjure code.
    """
    if not resolver_ok:
        return {
            "feature_id": OFFICE_FEATURE_ID,
            "state": ENTRY_UNKNOWN,
            "effective_tier": "",
            "available": [],
            "unavailable": [],
            "upgrade_tier": None,
        }

    tier = str(effective_tier or TIER_FREE).strip().upper()
    children = [_child_state(fid, tier) for fid in OFFICE_CHILD_IDS]

    available = [child for child in children if child["opens"]]
    unavailable = [child for child in children if not child["opens"]]

    # A child worth upgrading for: real code behind it, blocked only by rank.
    reachable = [
        child for child in unavailable
        if child["reason"] == "UPGRADE_REQUIRED"
    ]

    if available:
        state = ENTRY_AVAILABLE
        upgrade_tier = None
    elif reachable:
        state = ENTRY_UPGRADE_REQUIRED
        upgrade_tier = _lowest_tier(child["minimum_tier"] for child in reachable)
    else:
        state = ENTRY_UNAVAILABLE
        upgrade_tier = None

    return {
        "feature_id": OFFICE_FEATURE_ID,
        "state": state,
        "effective_tier": tier,
        "available": available,
        "unavailable": unavailable,
        "upgrade_tier": upgrade_tier,
    }


_TIER_LADDER: tuple[str, ...] = (TIER_FREE, TIER_PREMIUM, TIER_PRIVATE, TIER_PRIVATE_OFFICE)


def _lowest_tier(names) -> Optional[str]:
    """The cheapest tier in ``names``. The upgrade we quote is the smallest one
    that actually unlocks something, not the highest one we could ask for."""
    # Materialise once. Building the set inside the comprehension re-evaluates
    # it per ladder position, which quietly empties a generator argument after
    # the first one — FREE would match, PREMIUM upward would not, and the
    # entry would offer an upgrade without naming a tier to upgrade to.
    wanted = set(names or ())
    ranked = [name for name in _TIER_LADDER if name in wanted]
    return ranked[0] if ranked else None


def entry_visible(effective_tier: object, *, resolver_ok: bool = True) -> bool:
    """Whether Premium should show a Private Office entry at all.

    True for AVAILABLE and UPGRADE_REQUIRED — both of those are true things to
    say. False for UNAVAILABLE (there is nothing behind the door) and for
    UNKNOWN (we do not know, and a tile that appears and disappears between
    resolves is worse than one that waits).
    """
    return product_state(effective_tier, resolver_ok=resolver_ok)["state"] in (
        ENTRY_AVAILABLE,
        ENTRY_UPGRADE_REQUIRED,
    )


__all__ = [
    "OFFICE_FEATURE_ID", "OFFICE_CHILD_IDS",
    "ENTRY_AVAILABLE", "ENTRY_UPGRADE_REQUIRED", "ENTRY_UNAVAILABLE", "ENTRY_UNKNOWN",
    "VERIFICATION_VERIFIED", "VERIFICATION_SOURCED", "VERIFICATION_SELF_REPORTED",
    "VERIFICATION_ESTIMATED", "VERIFICATION_NEEDS_REVIEW",
    "verification_state", "project_provenance", "project_fact", "project_facts",
    "domain_summary", "product_state", "entry_visible",
]
