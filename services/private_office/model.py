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

# Added in the Private Facts ledger-core work. Each one names a *source* that
# the original eight could only approximate, and approximating provenance is how
# a fact ends up better-credentialled than its origin deserves.
#
#   SYSTEM_OBSERVED   PulseSoc itself watched this happen (a payment settled, a
#                     document was uploaded). Not a provider read-back, but not
#                     a human claim either — the system is the witness.
#   MEETING_DERIVED   Extracted from a meeting record. Weaker than a document
#                     because a transcript is a record of what was *said*.
#   HUMAN_CONFIRMED   A person affirmed an existing assertion. Deliberately
#                     ranked below PROVIDER_ASSERTED: confirmation raises the
#                     *verification state*, it does not turn a claim into a
#                     system of record. Verification and provenance are separate
#                     axes and this is the line where they are kept separate.
#   UNDX_PROPOSED     A model proposed it. Ranks with INFERRED at most, and a
#                     proposal is never durable fact until a human accepts it.
#   LEGACY_UNKNOWN    Written before provenance was recorded. Section 118: a row
#                     whose origin is unknown must say so. Relabelling it
#                     USER_ASSERTED would be inventing a witness.
PROVENANCE_SYSTEM_OBSERVED = "SYSTEM_OBSERVED"
PROVENANCE_MEETING_DERIVED = "MEETING_DERIVED"
PROVENANCE_HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
PROVENANCE_UNDX_PROPOSED = "UNDX_PROPOSED"
PROVENANCE_LEGACY_UNKNOWN = "LEGACY_UNKNOWN"

PROVENANCE_TYPES: tuple[str, ...] = (
    PROVENANCE_VERIFIED,
    PROVENANCE_PROVIDER_ASSERTED,
    PROVENANCE_DOCUMENT_EXTRACTED,
    PROVENANCE_SYSTEM_OBSERVED,
    PROVENANCE_HUMAN_CONFIRMED,
    PROVENANCE_USER_ASSERTED,
    PROVENANCE_MEETING_DERIVED,
    PROVENANCE_INFERRED,
    PROVENANCE_UNDX_PROPOSED,
    PROVENANCE_ESTIMATED,
    PROVENANCE_LEGACY_UNKNOWN,
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
    PROVENANCE_SYSTEM_OBSERVED: 55,
    PROVENANCE_HUMAN_CONFIRMED: 50,
    PROVENANCE_USER_ASSERTED: 40,
    PROVENANCE_MEETING_DERIVED: 30,
    PROVENANCE_INFERRED: 20,
    PROVENANCE_UNDX_PROPOSED: 15,
    PROVENANCE_ESTIMATED: 10,
    # Unknown origin ranks *below* an estimate. An estimate at least names the
    # method that produced it; a legacy row names nothing, so it must never win
    # a contradiction against a fact that can account for itself.
    PROVENANCE_LEGACY_UNKNOWN: 5,
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
# Verification state (Private Facts, Section 16)
# ---------------------------------------------------------------------------
# Provenance answers "where did this come from". Verification answers "what has
# since been done about it". Those are different questions and the original
# schema answered them in one column, which is why VERIFIED, STALE and
# CONFLICTING all ended up filed as though they were *sources*.
#
# Keeping them fused has a specific cost: once a fact is confirmed by its owner
# there is nowhere to record that without overwriting the memory of where the
# fact came from. The confirmation destroys the provenance. Separating the axes
# means a USER_ASSERTED fact can be USER_CONFIRMED and still remember that a
# person, not a bank, is the underlying source.
#
# Note that SUPERSEDED, EXPIRED and REVOKED appear both here and in
# `LIFECYCLE_STATES`. That is intentional rather than duplication: lifecycle
# governs whether the row is *returned*, verification governs whether it is
# *believed*, and a reader that only has one of the two cannot tell a fact that
# was replaced by a better one from a fact that was withdrawn as false.
VERIFICATION_UNVERIFIED = "UNVERIFIED"
VERIFICATION_USER_CONFIRMED = "USER_CONFIRMED"
VERIFICATION_EVIDENCE_SUPPORTED = "EVIDENCE_SUPPORTED"
VERIFICATION_VERIFIED = "VERIFIED"
VERIFICATION_PROVIDER_VERIFIED = "PROVIDER_VERIFIED"
VERIFICATION_NEEDS_REVIEW = "NEEDS_REVIEW"
VERIFICATION_CONFLICTING = "CONFLICTING"
VERIFICATION_DISPUTED = "DISPUTED"
VERIFICATION_SUPERSEDED = "SUPERSEDED"
VERIFICATION_EXPIRED = "EXPIRED"
VERIFICATION_REVOKED = "REVOKED"
#: Rows written before this column existed. Section 118 forbids backfilling them
#: to anything that asserts a check that never happened.
VERIFICATION_LEGACY_UNKNOWN = "LEGACY_UNKNOWN"

VERIFICATION_STATES: tuple[str, ...] = (
    VERIFICATION_UNVERIFIED,
    VERIFICATION_USER_CONFIRMED,
    VERIFICATION_EVIDENCE_SUPPORTED,
    VERIFICATION_VERIFIED,
    VERIFICATION_PROVIDER_VERIFIED,
    VERIFICATION_NEEDS_REVIEW,
    VERIFICATION_CONFLICTING,
    VERIFICATION_DISPUTED,
    VERIFICATION_SUPERSEDED,
    VERIFICATION_EXPIRED,
    VERIFICATION_REVOKED,
    VERIFICATION_LEGACY_UNKNOWN,
)

#: New facts start here. Not UNVERIFIED-as-a-synonym-for-untrusted — it means
#: precisely "nothing has been done to check this yet", which is the honest
#: state of every fact at the instant it is written.
DEFAULT_VERIFICATION_STATE = VERIFICATION_UNVERIFIED

#: How much a verification state adds to a fact's standing. Deliberately a
#: *separate* scale from `PROVENANCE_STRENGTH` rather than a multiplier: a
#: confirmed guess is still a guess, and multiplying would let confirmation
#: manufacture authority the source never had.
VERIFICATION_RANK: dict[str, int] = {
    VERIFICATION_PROVIDER_VERIFIED: 100,
    VERIFICATION_VERIFIED: 90,
    VERIFICATION_EVIDENCE_SUPPORTED: 70,
    VERIFICATION_USER_CONFIRMED: 50,
    VERIFICATION_UNVERIFIED: 20,
    VERIFICATION_LEGACY_UNKNOWN: 10,
    VERIFICATION_NEEDS_REVIEW: 0,
    VERIFICATION_CONFLICTING: 0,
    VERIFICATION_DISPUTED: 0,
    VERIFICATION_SUPERSEDED: 0,
    VERIFICATION_EXPIRED: 0,
    VERIFICATION_REVOKED: 0,
}

#: States in which a fact must not be quoted as current truth. A briefing, an
#: UNDX answer or a projection that cites one of these is asserting something
#: the ledger does not stand behind.
UNTRUSTWORTHY_VERIFICATION: frozenset[str] = frozenset(
    {
        VERIFICATION_NEEDS_REVIEW,
        VERIFICATION_CONFLICTING,
        VERIFICATION_DISPUTED,
        VERIFICATION_SUPERSEDED,
        VERIFICATION_EXPIRED,
        VERIFICATION_REVOKED,
    }
)

#: Terminal states. A revoked fact is not disputable and an expired fact is not
#: confirmable — reviving either means writing a new fact that supersedes it,
#: which is what leaves a trail. Transitioning out of these in place would erase
#: the reason the fact stopped being true.
TERMINAL_VERIFICATION: frozenset[str] = frozenset(
    {VERIFICATION_REVOKED, VERIFICATION_SUPERSEDED}
)

# ---------------------------------------------------------------------------
# Evidence (Private Facts, Section 16 — what makes EVIDENCE_SUPPORTED reachable)
# ---------------------------------------------------------------------------
# `VERIFICATION_EVIDENCE_SUPPORTED` ranks at 70: above a member's own
# confirmation, below a system of record. That gap is the whole reason the state
# exists. A member saying "yes, that's right" is an opinion, however sincere; a
# member saying "yes, and here is the policy schedule it came from" is an
# opinion plus something a third party can go and look at. The second is
# genuinely stronger and it is genuinely not the bank's own answer, so it needs
# its own rung.
#
# The rung was unreachable until now. Nothing set the state, and there was
# nowhere to put the thing being pointed at — `ProvenanceRef` records the
# *origin* of a fact and is fixed at write time, which is a different question
# from "what has since been produced in support of it". A fact created from a
# meeting note and later backed by the signed document has one origin and one
# piece of evidence, and a schema that stores only the first cannot say so.
#
# The vocabulary is closed and deliberately short. Every entry names a thing
# that already exists somewhere in this product and can be opened: a private
# document, a meeting the member attended, a structured record, a provider
# statement, or an external artefact the member described. There is no "other"
# and no free-text kind, because the value of this list is that a reviewer can
# be shown *the actual item*, and a kind nobody can resolve to an item is a
# citation that cannot be followed — which is worse than no citation, since it
# still buys the fact a promotion to rank 70.
EVIDENCE_DOCUMENT = "DOCUMENT"
EVIDENCE_MEETING = "MEETING"
EVIDENCE_RECORD = "RECORD"
EVIDENCE_STATEMENT = "STATEMENT"
EVIDENCE_EXTERNAL = "EXTERNAL"

EVIDENCE_TYPES: tuple[str, ...] = (
    EVIDENCE_DOCUMENT,
    EVIDENCE_MEETING,
    EVIDENCE_RECORD,
    EVIDENCE_STATEMENT,
    EVIDENCE_EXTERNAL,
)

#: Evidence kinds that resolve to something inside this member's own office, so
#: a reviewer can be handed the item itself rather than a description of it.
#: `EXTERNAL` is excluded because it is the member's word for something the
#: product cannot open — still worth recording, still not the same claim.
RESOLVABLE_EVIDENCE: frozenset[str] = frozenset(
    {EVIDENCE_DOCUMENT, EVIDENCE_MEETING, EVIDENCE_RECORD}
)


def normalize_evidence_type(value: object) -> str:
    """Canonical evidence kind, or ``""`` for anything unrecognised.

    Empty rather than a fallback member, and the callers treat empty as a
    refusal. A default kind here would let a typo attach evidence of a type
    nobody chose and still promote the fact to EVIDENCE_SUPPORTED — the
    promotion being the part that matters, and the part that must never happen
    by accident.
    """
    name = str(value or "").strip().upper()
    return name if name in EVIDENCE_TYPES else ""


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
#: A fact whose stated validity window has closed. Distinct from ARCHIVED (the
#: owner put it away) and from SUPERSEDED (something replaced it): an expired
#: fact was true and simply stopped being current, with nothing taking its
#: place. Collapsing the three would lose the reason, and the reason is what a
#: reader needs to know whether to go looking for a replacement.
LIFECYCLE_EXPIRED = "EXPIRED"
#: Withdrawn as never-having-been-true. The row is retained — deleting it is how
#: an audit trail acquires a hole — but it must never be read as an assertion.
LIFECYCLE_REVOKED = "REVOKED"

LIFECYCLE_STATES: tuple[str, ...] = (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_SUPERSEDED,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_EXPIRED,
    LIFECYCLE_REVOKED,
)

#: Lifecycle states that keep a fact out of "what is true now" reads. ACTIVE is
#: the only state that does not.
INACTIVE_LIFECYCLE: frozenset[str] = frozenset(
    {
        LIFECYCLE_SUPERSEDED,
        LIFECYCLE_ARCHIVED,
        LIFECYCLE_EXPIRED,
        LIFECYCLE_REVOKED,
    }
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


def normalize_verification_state(value: object) -> str | None:
    """Canonical verification state, or ``None``.

    ``None`` rather than ``DEFAULT_VERIFICATION_STATE`` on a miss, for the same
    reason the other normalizers refuse to default: silently converting an
    unrecognised state to UNVERIFIED would turn a typo in a confirmation path
    into a downgrade nobody asked for, and the caller would never learn its
    write did the opposite of what it meant.
    """
    return _canonical(value, VERIFICATION_STATES)


def verification_rank(value: object) -> int:
    """Standing of a verification state; ``0`` for anything unrecognised.

    Zero is the safe unknown here: it is the same rank as DISPUTED, so an
    unrecognised state can never win an argument against a state the system
    understands.
    """
    state = normalize_verification_state(value)
    if state is None:
        return 0
    return VERIFICATION_RANK.get(state, 0)


def verification_is_trustworthy(value: object) -> bool:
    """Is anything known to be *wrong* with a fact in this state?

    Deliberately the weaker of the two predicates. It is true of a plain
    assertion nobody has checked, because "unexamined" is not the same as
    "doubtful" and hiding every unverified fact would empty the Office. Use
    :func:`verification_is_affirmed` for the stronger question.

    Fail-closed on an unrecognised state.
    """
    state = normalize_verification_state(value)
    if state is None:
        return False
    return state not in UNTRUSTWORTHY_VERIFICATION


def verification_is_affirmed(value: object) -> bool:
    """Has anything actually checked this fact?

    The stricter half of the pair: did a person, a document, or a system of
    record affirm it. The distinction is the point of the verification axis —
    a store that cannot tell an unexamined claim from a confirmed one has a
    column, not a ledger.

    The threshold is ``USER_CONFIRMED``, so ``UNVERIFIED`` and
    ``LEGACY_UNKNOWN`` are excluded, as is every untrustworthy state (each of
    which ranks zero). Fail-closed on an unrecognised state.
    """
    state = normalize_verification_state(value)
    if state is None:
        return False
    return VERIFICATION_RANK.get(state, 0) >= VERIFICATION_RANK[
        VERIFICATION_USER_CONFIRMED]


def verification_is_terminal(value: object) -> bool:
    """Is this state one that may not be transitioned out of in place?"""
    state = normalize_verification_state(value)
    if state is None:
        return False
    return state in TERMINAL_VERIFICATION


def lifecycle_is_active(value: object) -> bool:
    """Does this lifecycle state belong in a "what is true now" read?

    Fail-closed: an unrecognised lifecycle is not active, because showing a row
    whose disposition the code cannot name is how an archived fact reappears in
    a briefing.
    """
    return normalize_lifecycle(value) == LIFECYCLE_ACTIVE


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
