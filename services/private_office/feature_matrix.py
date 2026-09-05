"""Stage 4 — the one canonical Private Office feature matrix.

Every Private Office feature is declared here exactly once, with three facts:

* ``minimum_tier``    — the rung on the ladder that unlocks it.
* ``server_enforced`` — whether the server actually blocks the request, as
  opposed to the client merely hiding a button.
* ``implementation``  — whether the thing exists at all.

The third one is the point. A tier existing does not mean a capability exists.
The failure mode this matrix prevents is shipping ``human_concierge`` as a
tappable tile because PRIVATE_OFFICE is a valid tier, or telling a subscriber
"no breaches found" when nothing has ever looked. So availability is resolved
from implementation FIRST and entitlement second: an unbuilt feature reports
NOT_IMPLEMENTED to everyone, including to the person who paid the most, because
"upgrade to unlock" would be a lie about a thing that does not exist.

Implementation states (shared vocabulary with the Stage 20 service registry):

* ``IMPLEMENTED``       — real code, real data, safe to expose.
* ``SHADOW``            — code runs but its output is not user-visible; used to
  collect evidence before promoting. Reports FEATURE_DISABLED to clients.
* ``PROVIDER_REQUIRED`` — the integration point exists, the provider does not.
  This is NOT "coming soon"; it means we cannot answer the question at all.
* ``DISABLED``          — built, deliberately off (flag or rollout).
* ``NOT_IMPLEMENTED``   — declared so the surface is honest; no code behind it.

Availability states returned to clients are exactly the four the mission
specifies: ENTITLED, NOT_ENTITLED, FEATURE_DISABLED, NOT_IMPLEMENTED.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from services.private_office.tiers import (
    TIER_FREE,
    TIER_PREMIUM,
    TIER_PRIVATE,
    TIER_PRIVATE_OFFICE,
    TIER_RANK,
    tier_satisfies,
)

# --- implementation vocabulary ---------------------------------------------
IMPL_IMPLEMENTED = "IMPLEMENTED"
IMPL_SHADOW = "SHADOW"
IMPL_PROVIDER_REQUIRED = "PROVIDER_REQUIRED"
IMPL_DISABLED = "DISABLED"
IMPL_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

_IMPL_STATES = frozenset({
    IMPL_IMPLEMENTED, IMPL_SHADOW, IMPL_PROVIDER_REQUIRED,
    IMPL_DISABLED, IMPL_NOT_IMPLEMENTED,
})

# --- availability vocabulary ------------------------------------------------
AVAIL_ENTITLED = "ENTITLED"
AVAIL_NOT_ENTITLED = "NOT_ENTITLED"
AVAIL_FEATURE_DISABLED = "FEATURE_DISABLED"
AVAIL_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True)
class FeatureSpec:
    """One row of the canonical matrix. Frozen: the matrix is data, not state."""

    feature_id: str
    minimum_tier: str
    server_enforced: bool
    implementation: str
    #: Human-readable reason the feature is not fully live. Required whenever
    #: ``implementation`` is anything other than IMPLEMENTED, so the matrix can
    #: never accumulate silent placeholders.
    note: str = ""
    #: Env var that can force the feature off at runtime. When set, the value
    #: must be a truthy string for the feature to stay enabled.
    flag_env: Optional[str] = None

    def __post_init__(self) -> None:
        if self.minimum_tier not in TIER_RANK:
            raise ValueError(
                f"{self.feature_id}: unknown minimum_tier {self.minimum_tier!r}"
            )
        if self.implementation not in _IMPL_STATES:
            raise ValueError(
                f"{self.feature_id}: unknown implementation {self.implementation!r}"
            )
        if self.implementation != IMPL_IMPLEMENTED and not self.note.strip():
            raise ValueError(
                f"{self.feature_id}: implementation={self.implementation} requires a note"
            )


# --- the matrix -------------------------------------------------------------
# Ordered by tier then id. Add rows here and nowhere else; a feature that is not
# in this table has no canonical minimum tier and must not be gated ad hoc.
_FEATURES = (
    # PREMIUM ---------------------------------------------------------------
    FeatureSpec(
        feature_id="advanced_undx",
        minimum_tier=TIER_PREMIUM,
        server_enforced=True,
        implementation=IMPL_IMPLEMENTED,
    ),
    FeatureSpec(
        feature_id="market_pulse",
        minimum_tier=TIER_PREMIUM,
        server_enforced=True,
        implementation=IMPL_IMPLEMENTED,
    ),

    # PRIVATE ---------------------------------------------------------------
    FeatureSpec(
        feature_id="capital_graph",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        # Flipped when both halves the previous note required actually existed:
        # the writer is `private_office.graph` (upsert_node / record_edge, the
        # only sanctioned writers, enforced by the write-boundary guard), and the
        # owner-scoped reader is `private_office.capital_graph`, which reads
        # exclusively through `retrieval.retrieve` and so cannot produce a row
        # that skipped the owner, authorization, sensitivity, domain and purpose
        # gates. Both are exercised end to end by
        # tests/private_office/test_capital_graph.py.
        implementation=IMPL_IMPLEMENTED,
        flag_env="CAPITAL_GRAPH_ENABLED",
    ),
    FeatureSpec(
        feature_id="private_briefings",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        implementation=IMPL_NOT_IMPLEMENTED,
        note=(
            "Reuses the existing Pulse Briefings engine; the Private Office fact "
            "provider is not written yet. When it lands it ships DISABLED by "
            "default — enabling it unconditionally would change every existing "
            "briefing fingerprint and page every user on the first cycle."
        ),
    ),
    FeatureSpec(
        feature_id="private_facts",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        # Stage 9. Flipped only after the whole path existed and was proven:
        # the canonical writer (facts.record_fact, the only module permitted to
        # INSERT, enforced by tests/private_office/test_private_write_boundary.py),
        # the owner-scoped reader (facts.list_facts), the projection and
        # provenance (office.project_facts), the HTTP surface
        # (services/private_office_routes.py) and the UNDX capability
        # (private.facts.list). The kill switch below is the way to turn this
        # off — editing this row back to NOT_IMPLEMENTED would be a lie about
        # code that exists.
        implementation=IMPL_IMPLEMENTED,
        flag_env="PRIVATE_FACTS_ENABLED",
    ),
    FeatureSpec(
        feature_id="private_shield",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        implementation=IMPL_PROVIDER_REQUIRED,
        note=(
            "Personal exposure monitoring requires a breach/identity data "
            "provider that is not integrated. Sentinel does NOT cover this: it "
            "monitors platform and security intelligence feeds, not personal "
            "identity exposure."
        ),
    ),
    FeatureSpec(
        feature_id="private_shield.breach_monitoring",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        implementation=IMPL_PROVIDER_REQUIRED,
        note=(
            "No breach/dark-web provider is integrated. This must never render a "
            "clean state: 'no breaches found' when nothing has looked is a "
            "fabricated security assurance."
        ),
    ),
    FeatureSpec(
        feature_id="private_office.operations",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        # Batch C. The six record primitives (obligations, domain events,
        # decisions, requests, risks, opportunities): schema, sanctioned
        # writers (records.create_record/update_record/revise_record — the
        # only modules permitted to INSERT, enforced by
        # tests/private_office/test_private_write_boundary.py), the five-gate
        # reader (retrieval.retrieve_records), the HTTP surface
        # (services/private_office_routes.py) and the six UNDX list
        # capabilities. The kill switch below is the way to turn this off.
        implementation=IMPL_IMPLEMENTED,
        flag_env="PRIVATE_OPERATIONS_ENABLED",
    ),
    FeatureSpec(
        feature_id="private_office.document.extraction",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        # The vault, deterministic text extraction (txt/md/csv/json →
        # PROPOSED claims), member review into the canonical fact writer, and
        # owner-only content streaming are implemented
        # (services/private_office/documents.py +
        # services/private_office_documents_routes.py). OCR/PDF extraction
        # still requires a provider that is not integrated — that truth now
        # lives per-document in extraction_state=PROVIDER_REQUIRED and in the
        # payload's provider_status, where a screen can render it honestly,
        # rather than gating the whole capability. The kill switch below is
        # the way to turn this off.
        implementation=IMPL_IMPLEMENTED,
        flag_env="PRIVATE_DOCUMENTS_ENABLED",
    ),
    FeatureSpec(
        feature_id="relationship_intelligence",
        minimum_tier=TIER_PRIVATE,
        server_enforced=True,
        implementation=IMPL_NOT_IMPLEMENTED,
        note="Depends on the private graph substrate, which is not written yet.",
    ),

    # PRIVATE_OFFICE --------------------------------------------------------
    FeatureSpec(
        feature_id="human_concierge",
        minimum_tier=TIER_PRIVATE_OFFICE,
        server_enforced=True,
        implementation=IMPL_NOT_IMPLEMENTED,
        note=(
            "Requires a staffed human operations process, not just code. Must "
            "not be exposed as a tappable surface until that process exists."
        ),
    ),
)

#: Fail fast on duplicate ids at import time — two rows for one feature is the
#: exact drift this matrix exists to prevent.
FEATURES: dict = {}
for _spec in _FEATURES:
    if _spec.feature_id in FEATURES:
        raise ValueError(f"duplicate feature_id in matrix: {_spec.feature_id}")
    FEATURES[_spec.feature_id] = _spec
del _spec


def get(feature_id: str) -> Optional[FeatureSpec]:
    """The spec for ``feature_id``, or None if it is not in the matrix."""
    return FEATURES.get(str(feature_id or "").strip())


def _flag_enabled(spec: FeatureSpec) -> bool:
    """Runtime kill switch. Absent env var means 'not overridden' -> enabled."""
    if not spec.flag_env:
        return True
    raw = (os.getenv(spec.flag_env, "") or "").strip().lower()
    if raw == "":
        return True
    return raw in ("1", "true", "on", "yes")


def availability(feature_id: str, effective_tier: str) -> dict:
    """Resolve one feature's availability for a already-resolved tier.

    Implementation is checked BEFORE entitlement on purpose. See module
    docstring: telling a user to upgrade for something that does not exist is
    worse than telling them it does not exist.

    An unknown ``feature_id`` resolves to NOT_IMPLEMENTED rather than raising —
    a typo in a client build must degrade to "you can't have it", not to a 500.
    """
    spec = get(feature_id)
    if spec is None:
        return {
            "feature_id": str(feature_id),
            "availability": AVAIL_NOT_IMPLEMENTED,
            "minimum_tier": TIER_PRIVATE_OFFICE,
            "server_enforced": True,
            "implementation": IMPL_NOT_IMPLEMENTED,
            "note": "unknown feature_id",
        }

    if spec.implementation in (IMPL_NOT_IMPLEMENTED, IMPL_PROVIDER_REQUIRED):
        avail = AVAIL_NOT_IMPLEMENTED
    elif spec.implementation in (IMPL_DISABLED, IMPL_SHADOW) or not _flag_enabled(spec):
        avail = AVAIL_FEATURE_DISABLED
    elif tier_satisfies(effective_tier, spec.minimum_tier):
        avail = AVAIL_ENTITLED
    else:
        avail = AVAIL_NOT_ENTITLED

    return {
        "feature_id": spec.feature_id,
        "availability": avail,
        "minimum_tier": spec.minimum_tier,
        "server_enforced": spec.server_enforced,
        "implementation": spec.implementation,
        "note": spec.note,
    }


def availability_map(effective_tier: str) -> dict:
    """Availability for every declared feature, keyed by feature_id."""
    return {fid: availability(fid, effective_tier) for fid in FEATURES}


def is_entitled(feature_id: str, effective_tier: str) -> bool:
    """True only for ENTITLED. Every other state — including 'not built' — is False."""
    return availability(feature_id, effective_tier)["availability"] == AVAIL_ENTITLED


def implemented_feature_ids() -> tuple:
    """Feature ids that are genuinely live. This is what a client may render as
    tappable. Everything else is, at best, a disclosed placeholder."""
    return tuple(sorted(
        fid for fid, spec in FEATURES.items()
        if spec.implementation == IMPL_IMPLEMENTED and _flag_enabled(spec)
    ))


__all__ = [
    "FeatureSpec", "FEATURES", "get", "availability", "availability_map",
    "is_entitled", "implemented_feature_ids",
    "AVAIL_ENTITLED", "AVAIL_NOT_ENTITLED", "AVAIL_FEATURE_DISABLED",
    "AVAIL_NOT_IMPLEMENTED",
    "IMPL_IMPLEMENTED", "IMPL_SHADOW", "IMPL_PROVIDER_REQUIRED",
    "IMPL_DISABLED", "IMPL_NOT_IMPLEMENTED",
    "TIER_FREE",
]
