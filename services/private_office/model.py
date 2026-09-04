"""The vocabulary every Private Office module agrees on.

One place, because the alternative is already documented
--------------------------------------------------------
Stage 3 of this mission removed six independent client-side deciders that used
four mutually inconsistent arrays to answer one question, and the observable
result was a lifetime member with a badge on their profile and none in the
navigation drawer. The cause was not that any one array was wrong. It was that
there were four of them.

The Private Office substrate has five vocabularies — domains, sensitivities,
provenance types, node types, relation types — read by the fact writer, the
graph writer, the retrieval layer, the contradiction engine, the audit log and
the route pack. Six readers times five vocabularies is thirty opportunities to
write the list out a second time and have it drift. So the lists live here, in
one module with no dependencies, and every validator is a function rather than
a literal a caller can copy.

Why validation returns rather than raises
-----------------------------------------
:func:`normalize_domain` and its siblings return a canonical value or ``None``.
They do not raise, and they do not fall back to a default. A default is the
dangerous option in both directions:

* Defaulting an unrecognised sensitivity to ``PUBLIC`` publishes whatever the
  caller misspelled.
* Defaulting it to ``RESTRICTED`` hides data the owner is entitled to see, and
  hides it silently, which is how a store quietly stops answering.

``None`` forces the caller to decide, and every caller here decides the same
way: reject the write. A fact whose sensitivity nobody can name is a fact the
retrieval layer cannot bound, and storing it would mean the boundary is only as
good as the next reader's guess.

Scope note
----------
Stage 8 caps node types at nine and Stage 9 caps relations at six. That cap is
deliberate and it is a *foundation* cap, not a product cap: a graph with forty
node types and no proven traversal is a schema, not a capability. Nothing here
is intended to be the final vocabulary; it is intended to be the vocabulary the
foundation can actually prove end to end.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Domains (Stage 6)
# ---------------------------------------------------------------------------
# A domain answers "what part of a life is this about". It is the unit the
# cross-domain join policy in Stage 17 reasons over: PROPERTY facts may be
# joined to INSURANCE facts to answer a coverage question; HEALTH facts may not
# be joined to a social or public context to answer anything.
DOMAIN_GENERAL = "GENERAL"
DOMAIN_FINANCIAL = "FINANCIAL"
DOMAIN_LEGAL = "LEGAL"
DOMAIN_HEALTH = "HEALTH"
DOMAIN_FAMILY = "FAMILY"
DOMAIN_IDENTITY = "IDENTITY"
DOMAIN_SECURITY = "SECURITY"

DOMAINS: tuple[str, ...] = (
    DOMAIN_GENERAL,
    DOMAIN_FINANCIAL,
    DOMAIN_LEGAL,
    DOMAIN_HEALTH,
    DOMAIN_FAMILY,
    DOMAIN_IDENTITY,
    DOMAIN_SECURITY,
)

# ---------------------------------------------------------------------------
# Sensitivity (Stage 6)
# ---------------------------------------------------------------------------
# Sensitivity answers "how much damage does disclosure do", which is a different
# axis from domain and must stay separate. A property address is FINANCIAL and
# CONFIDENTIAL; a diagnosis is HEALTH and RESTRICTED; the name a user chose for
# their own business is FINANCIAL and INTERNAL. Collapsing the two axes is how a
# system ends up treating "it is about money" as "it is a secret", or worse, the
# reverse.
SENSITIVITY_PUBLIC = "PUBLIC"
SENSITIVITY_INTERNAL = "INTERNAL"
SENSITIVITY_CONFIDENTIAL = "CONFIDENTIAL"
SENSITIVITY_HIGHLY_SENSITIVE = "HIGHLY_SENSITIVE"
SENSITIVITY_RESTRICTED = "RESTRICTED"

SENSITIVITIES: tuple[str, ...] = (
    SENSITIVITY_PUBLIC,
    SENSITIVITY_INTERNAL,
    SENSITIVITY_CONFIDENTIAL,
    SENSITIVITY_HIGHLY_SENSITIVE,
    SENSITIVITY_RESTRICTED,
)

#: Rank, low to high. Retrieval carries a *ceiling*: a caller allowed up to
#: CONFIDENTIAL sees PUBLIC, INTERNAL and CONFIDENTIAL rows and never learns
#: that the other two exist.
SENSITIVITY_RANK: dict[str, int] = {
    name: index for index, name in enumerate(SENSITIVITIES)
}

#: What a PRIVATE-tier fact gets when the writer does not say. Stage 6 requires
#: private facts to default non-public, and CONFIDENTIAL rather than INTERNAL
#: because the store's whole reason to exist is the material a member would not
#: post: what they own, who advises them, what covers it.
DEFAULT_SENSITIVITY = SENSITIVITY_CONFIDENTIAL
DEFAULT_DOMAIN = DOMAIN_GENERAL

# ---------------------------------------------------------------------------
# Provenance (Stage 12)
# ---------------------------------------------------------------------------
# Provenance is the answer to "why should anyone believe this", and it is the
# field that makes the difference between a fact store and a pile of assertions.
# The ordering below is not arbitrary — `PROVENANCE_STRENGTH` is what decides a
# contradiction, and the rule it encodes is that a reading taken back from a
# system of record outranks a number a person typed, which outranks a number a
# model inferred.
PROVENANCE_VERIFIED = "VERIFIED"
PROVENANCE_PROVIDER_ASSERTED = "PROVIDER_ASSERTED"
PROVENANCE_DOCUMENT_EXTRACTED = "DOCUMENT_EXTRACTED"
PROVENANCE_USER_ASSERTED = "USER_ASSERTED"
PROVENANCE_INFERRED = "INFERRED"
PROVENANCE_ESTIMATED = "ESTIMATED"
PROVENANCE_STALE = "STALE"
PROVENANCE_CONFLICTING = "CONFLICTING"

PROVENANCE_TYPES: tuple[str, ...] = (
    PROVENANCE_VERIFIED,
    PROVENANCE_PROVIDER_ASSERTED,
    PROVENANCE_DOCUMENT_EXTRACTED,
    PROVENANCE_USER_ASSERTED,
    PROVENANCE_INFERRED,
    PROVENANCE_ESTIMATED,
    PROVENANCE_STALE,
    PROVENANCE_CONFLICTING,
)

#: How much weight a provenance carries when two facts disagree. STALE and
#: CONFLICTING are *states a fact has been moved into*, not sources, so they
#: rank at zero: neither may win an argument, and a fact marked CONFLICTING
#: must never be quoted as though the conflict were settled.
PROVENANCE_STRENGTH: dict[str, int] = {
    PROVENANCE_VERIFIED: 100,
    PROVENANCE_PROVIDER_ASSERTED: 80,
    PROVENANCE_DOCUMENT_EXTRACTED: 60,
    PROVENANCE_USER_ASSERTED: 40,
    PROVENANCE_INFERRED: 20,
    PROVENANCE_ESTIMATED: 10,
    PROVENANCE_STALE: 0,
    PROVENANCE_CONFLICTING: 0,
}

#: Provenance values that mean "this row is no longer a usable assertion".
#: Retrieval still returns them — hiding a conflict is how UNDX would end up
#: guessing — but flags them, and the contradiction engine never lets one of
#: them resolve a disagreement.
DEGRADED_PROVENANCE: frozenset[str] = frozenset(
    {PROVENANCE_STALE, PROVENANCE_CONFLICTING}
)

# ---------------------------------------------------------------------------
# Value types (Stage 6 `typed_value`)
# ---------------------------------------------------------------------------
# `typed_value` is stored as text alongside a discriminator, and numerically
# comparable types are *also* stored as a number. That second column is the
# whole point.
#
# `services/undx_brain/facts.py` documents, at length, what happens without it:
# the existing UNDX contradiction check compares claim *strings*, so recording
# "btc alert threshold is 50000" twice from two sources is flagged as a conflict
# (it is corroboration) while "50000" and "60000" from the same source are both
# filed active with nothing marking either (that is the conflict). Two claims
# that disagree are by construction different strings, so a string comparison
# detects agreement and lets disagreement through.
#
# Stage 13 requires the opposite behaviour, and requires it to distinguish an
# ownership share of 35% from 40% in the same period. That is a numeric
# question. It is answerable here and unanswerable over text.
VALUE_STRING = "STRING"
VALUE_NUMBER = "NUMBER"
VALUE_MONEY = "MONEY"
VALUE_PERCENT = "PERCENT"
VALUE_DATE = "DATE"
VALUE_BOOLEAN = "BOOLEAN"

VALUE_TYPES: tuple[str, ...] = (
    VALUE_STRING,
    VALUE_NUMBER,
    VALUE_MONEY,
    VALUE_PERCENT,
    VALUE_DATE,
    VALUE_BOOLEAN,
)

#: Types whose disagreement is a magnitude, so "materially incompatible" can be
#: asked as a tolerance rather than an equality. DATE is excluded on purpose:
#: two renewal dates a day apart are not "nearly the same date", they are two
#: different answers to a question with one answer.
NUMERIC_VALUE_TYPES: frozenset[str] = frozenset(
    {VALUE_NUMBER, VALUE_MONEY, VALUE_PERCENT}
)

# ---------------------------------------------------------------------------
# Graph node types (Stage 8)
# ---------------------------------------------------------------------------
NODE_PERSON = "PERSON"
NODE_BUSINESS = "BUSINESS"
NODE_PROPERTY = "PROPERTY"
NODE_INSURANCE_POLICY = "INSURANCE_POLICY"
NODE_CONTRACT = "CONTRACT"
NODE_DOCUMENT = "DOCUMENT"
NODE_PROFESSIONAL = "PROFESSIONAL"
NODE_ASSET = "ASSET"
NODE_LIABILITY = "LIABILITY"

NODE_TYPES: tuple[str, ...] = (
    NODE_PERSON,
    NODE_BUSINESS,
    NODE_PROPERTY,
    NODE_INSURANCE_POLICY,
    NODE_CONTRACT,
    NODE_DOCUMENT,
    NODE_PROFESSIONAL,
    NODE_ASSET,
    NODE_LIABILITY,
)

# ---------------------------------------------------------------------------
# Lifecycle (Stage 8, Stage 10)
# ---------------------------------------------------------------------------
# A node is never deleted by the writer. `SUPERSEDED` and `ARCHIVED` exist so a
# property that was sold stops being an answer without the edges that referenced
# it becoming dangling — an edge pointing at a row that no longer exists is how
# a traversal starts returning ids it cannot describe.
LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_SUPERSEDED = "SUPERSEDED"
LIFECYCLE_ARCHIVED = "ARCHIVED"

LIFECYCLE_STATES: tuple[str, ...] = (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_SUPERSEDED,
    LIFECYCLE_ARCHIVED,
)

# ---------------------------------------------------------------------------
# Relations (Stage 9)
# ---------------------------------------------------------------------------
RELATION_OWNS = "OWNS"
RELATION_ADVISED_BY = "ADVISED_BY"
RELATION_COVERED_BY = "COVERED_BY"
RELATION_SECURED_BY = "SECURED_BY"
RELATION_GOVERNED_BY = "GOVERNED_BY"
RELATION_DESCRIBES = "DESCRIBES"

RELATION_TYPES: tuple[str, ...] = (
    RELATION_OWNS,
    RELATION_ADVISED_BY,
    RELATION_COVERED_BY,
    RELATION_SECURED_BY,
    RELATION_GOVERNED_BY,
    RELATION_DESCRIBES,
)

#: Which node types each relation may connect, as ``(source, target)`` pairs.
#:
#: This is the difference between a graph and a bag of strings. Without it
#: nothing stops ``INSURANCE_POLICY OWNS PERSON``, and once one such edge exists
#: every traversal that follows OWNS has to defend against it — which in
#: practice means every traversal quietly stops trusting its own edges. The
#: constraint is enforced once, at write time, by :func:`relation_permits`.
RELATION_ENDPOINTS: dict[str, tuple[tuple[str, str], ...]] = {
    RELATION_OWNS: (
        (NODE_PERSON, NODE_BUSINESS),
        (NODE_PERSON, NODE_PROPERTY),
        (NODE_PERSON, NODE_ASSET),
        (NODE_PERSON, NODE_LIABILITY),
        (NODE_BUSINESS, NODE_BUSINESS),
        (NODE_BUSINESS, NODE_PROPERTY),
        (NODE_BUSINESS, NODE_ASSET),
        (NODE_BUSINESS, NODE_LIABILITY),
    ),
    RELATION_ADVISED_BY: (
        (NODE_PERSON, NODE_PROFESSIONAL),
        (NODE_BUSINESS, NODE_PROFESSIONAL),
    ),
    RELATION_COVERED_BY: (
        (NODE_PROPERTY, NODE_INSURANCE_POLICY),
        (NODE_ASSET, NODE_INSURANCE_POLICY),
        (NODE_BUSINESS, NODE_INSURANCE_POLICY),
        (NODE_PERSON, NODE_INSURANCE_POLICY),
    ),
    RELATION_SECURED_BY: (
        (NODE_LIABILITY, NODE_PROPERTY),
        (NODE_LIABILITY, NODE_ASSET),
    ),
    RELATION_GOVERNED_BY: (
        (NODE_BUSINESS, NODE_CONTRACT),
        (NODE_PROPERTY, NODE_CONTRACT),
        (NODE_ASSET, NODE_CONTRACT),
        (NODE_PERSON, NODE_CONTRACT),
    ),
    # DESCRIBES points *from* the document at the thing it is about, so a
    # traversal answering "what do I know about this property" reaches the
    # document by following DESCRIBES backwards, and a traversal answering
    # "what does this document cover" follows it forwards. One direction, two
    # readings — storing both would double every document edge for no gain.
    RELATION_DESCRIBES: (
        (NODE_DOCUMENT, NODE_PROPERTY),
        (NODE_DOCUMENT, NODE_BUSINESS),
        (NODE_DOCUMENT, NODE_INSURANCE_POLICY),
        (NODE_DOCUMENT, NODE_CONTRACT),
        (NODE_DOCUMENT, NODE_ASSET),
        (NODE_DOCUMENT, NODE_LIABILITY),
        (NODE_DOCUMENT, NODE_PERSON),
    ),
}


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------
def _canonical(value: object, allowed: tuple[str, ...]) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in allowed else None


def normalize_domain(value: object) -> str | None:
    """Canonical domain, or ``None`` if the caller named something else."""
    return _canonical(value, DOMAINS)


def normalize_sensitivity(value: object) -> str | None:
    """Canonical sensitivity, or ``None``."""
    return _canonical(value, SENSITIVITIES)


def normalize_provenance(value: object) -> str | None:
    """Canonical provenance type, or ``None``."""
    return _canonical(value, PROVENANCE_TYPES)


def normalize_value_type(value: object) -> str | None:
    """Canonical value type, or ``None``."""
    return _canonical(value, VALUE_TYPES)


def normalize_node_type(value: object) -> str | None:
    """Canonical node type, or ``None``."""
    return _canonical(value, NODE_TYPES)


def normalize_relation(value: object) -> str | None:
    """Canonical relation type, or ``None``."""
    return _canonical(value, RELATION_TYPES)


def normalize_lifecycle(value: object) -> str | None:
    """Canonical lifecycle state, or ``None``."""
    return _canonical(value, LIFECYCLE_STATES)


def relation_permits(relation: object, source_type: object, target_type: object) -> bool:
    """May ``relation`` connect these two node types?

    False for an unknown relation or an unknown endpoint type, so a typo is a
    rejected write rather than an edge nothing can interpret.
    """
    name = normalize_relation(relation)
    source = normalize_node_type(source_type)
    target = normalize_node_type(target_type)
    if not name or not source or not target:
        return False
    return (source, target) in RELATION_ENDPOINTS.get(name, ())


def sensitivity_within(value: object, ceiling: object) -> bool:
    """Is a row at ``value`` releasable to a caller allowed up to ``ceiling``?

    Unknown on either side is False. A ceiling nobody can name is not a licence
    to release everything, and a row whose sensitivity nobody can name is not
    safe to hand over — both are the fail-closed reading.
    """
    row = normalize_sensitivity(value)
    limit = normalize_sensitivity(ceiling)
    if not row or not limit:
        return False
    return SENSITIVITY_RANK[row] <= SENSITIVITY_RANK[limit]


def provenance_strength(value: object) -> int:
    """Weight of a provenance in a disagreement. Unknown ranks at zero."""
    return PROVENANCE_STRENGTH.get(normalize_provenance(value) or "", 0)
