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
PROVENANCE_HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
PROVENANCE_DOCUMENT_EXTRACTED = "DOCUMENT_EXTRACTED"
PROVENANCE_MEETING_DERIVED = "MEETING_DERIVED"
PROVENANCE_USER_ASSERTED = "USER_ASSERTED"
PROVENANCE_INFERRED = "INFERRED"
PROVENANCE_ESTIMATED = "ESTIMATED"
PROVENANCE_UNDX_PROPOSED = "UNDX_PROPOSED"
PROVENANCE_LEGACY_UNKNOWN = "LEGACY_UNKNOWN"
PROVENANCE_STALE = "STALE"
PROVENANCE_CONFLICTING = "CONFLICTING"

PROVENANCE_TYPES: tuple[str, ...] = (
    PROVENANCE_VERIFIED,
    PROVENANCE_PROVIDER_ASSERTED,
    PROVENANCE_HUMAN_CONFIRMED,
    PROVENANCE_DOCUMENT_EXTRACTED,
    PROVENANCE_MEETING_DERIVED,
    PROVENANCE_USER_ASSERTED,
    PROVENANCE_INFERRED,
    PROVENANCE_ESTIMATED,
    PROVENANCE_UNDX_PROPOSED,
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
    # A person deliberately said "yes, this is right". Below a system of record,
    # which does not misremember, and above a document extraction, which is a
    # machine reading a scan. Note the asymmetry with the verification axis
    # below: this is where the row *came from*, and a human confirming a value
    # they were shown is a genuine origin. Whether anyone has since checked it
    # is a different column and is not implied by this one.
    PROVENANCE_HUMAN_CONFIRMED: 70,
    PROVENANCE_DOCUMENT_EXTRACTED: 60,
    # Captured from a real interaction that happened — a call summary, meeting
    # notes. Stronger than a value typed in later from memory, weaker than a
    # document, because a transcript records what was *said* and people misstate
    # numbers out loud constantly.
    PROVENANCE_MEETING_DERIVED: 50,
    PROVENANCE_USER_ASSERTED: 40,
    PROVENANCE_INFERRED: 20,
    PROVENANCE_ESTIMATED: 10,
    # Zero, and the zero is the point. A proposal is a question, not an answer.
    # If UNDX_PROPOSED carried any weight at all there would exist some pairing
    # where a model's suggestion outranks something a member stated, and the
    # first time that decided a conflict the store would have quietly become an
    # autonomous authority that turns inference into fact.
    PROVENANCE_UNDX_PROPOSED: 0,
    # Also zero. "We do not know where this came from" is not a middling source,
    # it is the absence of one, and it must lose every argument it enters rather
    # than winning the ones against inferences.
    PROVENANCE_LEGACY_UNKNOWN: 0,
    PROVENANCE_STALE: 0,
    PROVENANCE_CONFLICTING: 0,
}

#: Provenance values that mean "this row is no longer a usable assertion".
#: Retrieval still returns them — hiding a conflict is how UNDX would end up
#: guessing — but flags them, and the contradiction engine never lets one of
#: them resolve a disagreement.
#:
#: Membership here also means "may not be the origin of a new write": the fact,
#: graph and record writers all reject these outright, because a row born
#: CONFLICTING is one that can never lose an argument it was never in.
DEGRADED_PROVENANCE: frozenset[str] = frozenset(
    {PROVENANCE_STALE, PROVENANCE_CONFLICTING}
)

#: Only the migration may write these. ``LEGACY_UNKNOWN`` is the honest label
#: for a row that predates provenance tracking, and it is deliberately made
#: hard to reach: if any caller could write it, "I would rather not say where
#: this came from" becomes an available option, and the ledger's central claim —
#: that every fact can name its origin — stops being true the first time
#: somebody takes it.
BACKFILL_ONLY_PROVENANCE: frozenset[str] = frozenset({PROVENANCE_LEGACY_UNKNOWN})

#: Provenance values that may never *settle* a disagreement, whatever their
#: strength arithmetic works out to. This is a superset of the degraded pair
#: because it also covers the two origins that are legitimate to record and
#: illegitimate to defer to: a model's proposal and an unknown legacy source.
#:
#: Kept separate from ``DEGRADED_PROVENANCE`` rather than folded into it,
#: because the two sets answer different questions. Degraded asks "may this be
#: written?"; this asks "may this win?". A proposal must be writable — that is
#: the entire mechanism by which UNDX suggests something — and must never win.
NON_SETTLING_PROVENANCE: frozenset[str] = DEGRADED_PROVENANCE | frozenset(
    {PROVENANCE_UNDX_PROPOSED, PROVENANCE_LEGACY_UNKNOWN}
)

# ---------------------------------------------------------------------------
# Verification — a separate axis from provenance
# ---------------------------------------------------------------------------
# Provenance answers "where did this come from". Verification answers "how
# thoroughly has anyone checked it". Those are different questions and the
# foundation map found them sharing one column: `PROVENANCE_VERIFIED` was a
# provenance value being asked to do a verification value's job.
#
# The failure that causes is specific and it is not hypothetical. With one
# column, the only way to record "a human looked at this and agreed" is to
# overwrite the provenance — which destroys the record of where the value came
# from. And the only way to record "this used to be verified and the check has
# aged out" is to move it to STALE, which loses the fact that it was ever
# checked at all. Two axes, and both of those become ordinary state transitions
# on the second axis with the first left untouched.
#
# The other thing this separation buys is the ability to refuse a false badge.
# With one column, "the source is a verified provider" and "somebody verified
# this value" are indistinguishable, so a high-confidence inference from a
# trusted feed renders identically to a passport somebody actually read. Here
# the first is `provenance=PROVIDER_ASSERTED, verification=UNVERIFIED` and it
# reads as exactly what it is.
#
# Confidence is a *third* axis and stays a float on the row. A model can be 0.97
# confident about something nobody has ever checked; that combination is
# representable, it is common, and collapsing it into either of these two
# vocabularies would lose it.
VERIFICATION_UNVERIFIED = "UNVERIFIED"
VERIFICATION_SELF_ASSERTED = "SELF_ASSERTED"
VERIFICATION_PENDING_REVIEW = "PENDING_REVIEW"
VERIFICATION_DOCUMENT_SUPPORTED = "DOCUMENT_SUPPORTED"
VERIFICATION_SYSTEM_CORROBORATED = "SYSTEM_CORROBORATED"
#: The owner personally checked this and stands behind it.
#:
#: Named for *which* human rather than for humanness, and the rename is load
#: bearing twice over. Alongside COUNTERPARTY_CONFIRMED and AUTHORITY_VERIFIED —
#: both also performed by people — "HUMAN" was never the distinguishing feature;
#: who did the checking is. And the provenance axis already spends the exact
#: string ``HUMAN_CONFIRMED`` on a different meaning: there it says a person is
#: where the value *came from*, here it would say a person *checked* it. Two
#: axes sharing one label is the overload this separation exists to prevent, and
#: a bare string must answer exactly one of those questions.
VERIFICATION_OWNER_CONFIRMED = "OWNER_CONFIRMED"
VERIFICATION_COUNTERPARTY_CONFIRMED = "COUNTERPARTY_CONFIRMED"
VERIFICATION_AUTHORITY_VERIFIED = "AUTHORITY_VERIFIED"
VERIFICATION_EXPIRED = "EXPIRED"
VERIFICATION_FAILED = "FAILED"
VERIFICATION_DISPUTED = "DISPUTED"

VERIFICATION_STATES: tuple[str, ...] = (
    VERIFICATION_UNVERIFIED,
    VERIFICATION_SELF_ASSERTED,
    VERIFICATION_PENDING_REVIEW,
    VERIFICATION_DOCUMENT_SUPPORTED,
    VERIFICATION_SYSTEM_CORROBORATED,
    VERIFICATION_OWNER_CONFIRMED,
    VERIFICATION_COUNTERPARTY_CONFIRMED,
    VERIFICATION_AUTHORITY_VERIFIED,
    VERIFICATION_EXPIRED,
    VERIFICATION_FAILED,
    VERIFICATION_DISPUTED,
)

#: What a fact gets when the writer does not say. Not SELF_ASSERTED, which
#: sounds like the weakest possible option and is in fact a claim: it says the
#: owner personally stated this. A fact arriving from a provider feed has not
#: been self-asserted by anybody, and labelling it so would be a small lie that
#: the review queue would then have to reason about.
DEFAULT_VERIFICATION = VERIFICATION_UNVERIFIED

#: Ordering for display and for "is this at least as checked as that". Gaps in
#: the numbers are deliberate room to insert a state without renumbering.
#:
#: The three terminal-negative states sit at the bottom rather than off the
#: scale because they are genuinely worse than never having looked: a check that
#: failed is information, and a fact whose check failed must not sort above one
#: nobody has examined.
VERIFICATION_RANK: dict[str, int] = {
    VERIFICATION_FAILED: -20,
    VERIFICATION_DISPUTED: -10,
    VERIFICATION_EXPIRED: -5,
    VERIFICATION_UNVERIFIED: 0,
    VERIFICATION_SELF_ASSERTED: 10,
    VERIFICATION_PENDING_REVIEW: 20,
    VERIFICATION_DOCUMENT_SUPPORTED: 40,
    VERIFICATION_SYSTEM_CORROBORATED: 50,
    VERIFICATION_OWNER_CONFIRMED: 60,
    VERIFICATION_COUNTERPARTY_CONFIRMED: 70,
    VERIFICATION_AUTHORITY_VERIFIED: 100,
}

#: The states that entitle a fact to be *shown as checked*. Everything else
#: renders as some flavour of "nobody has confirmed this", including
#: SELF_ASSERTED and PENDING_REVIEW — being in a queue is not a result.
VERIFIED_STATES: frozenset[str] = frozenset({
    VERIFICATION_DOCUMENT_SUPPORTED,
    VERIFICATION_SYSTEM_CORROBORATED,
    VERIFICATION_OWNER_CONFIRMED,
    VERIFICATION_COUNTERPARTY_CONFIRMED,
    VERIFICATION_AUTHORITY_VERIFIED,
})

#: States that may not be claimed without at least one resolvable evidence link.
#:
#: This is the constraint behind "no orphan verified badge". Every state in
#: `VERIFIED_STATES` asserts that something specific was consulted — a document,
#: a second system, a named person, a counterparty, an issuing authority — and
#: an assertion of that shape with nothing behind it is not a weaker claim, it
#: is an unfalsifiable one. The writer enforces this; it is not left to the UI
#: to decide whether the badge it is about to draw has anything under it.
VERIFICATION_REQUIRES_EVIDENCE: frozenset[str] = VERIFIED_STATES

#: States that mean "somebody looked and it did not hold up". Distinct from
#: unverified in every read model: a fact that failed verification is a stronger
#: signal than a fact nobody has touched, and burying it in the same bucket is
#: how a known-bad value keeps getting quoted.
VERIFICATION_NEGATIVE: frozenset[str] = frozenset({
    VERIFICATION_FAILED,
    VERIFICATION_DISPUTED,
})

#: States that put a fact in front of a human. EXPIRED is here and FAILED is
#: not, deliberately: an expired check is an action item — go and look again —
#: whereas a failed check has already had its answer and needs a correction, not
#: a re-review.
VERIFICATION_NEEDS_ATTENTION: frozenset[str] = frozenset({
    VERIFICATION_PENDING_REVIEW,
    VERIFICATION_EXPIRED,
    VERIFICATION_DISPUTED,
})

#: How long a positive verification stands before it reads EXPIRED, in days, by
#: the state that was reached. Same shape and same reasoning as the provenance
#: freshness horizons: an authority check on an identity document is good for a
#: year, a human saying "yes that's still right" is good for a quarter, and one
#: global TTL over both would be wrong in both directions.
#:
#: Nothing writes EXPIRED on a schedule. Expiry is computed at read time from
#: `verified_at` plus this horizon, for the same reason staleness is: a stored
#: expiry flag is only as current as the last sweeper run, and the window where
#: the database says "verified" because the sweeper has not fired yet is exactly
#: the window in which somebody acts on it.
VERIFICATION_HORIZON_DAYS: dict[str, int] = {
    VERIFICATION_AUTHORITY_VERIFIED: 365,
    VERIFICATION_COUNTERPARTY_CONFIRMED: 180,
    VERIFICATION_OWNER_CONFIRMED: 90,
    VERIFICATION_SYSTEM_CORROBORATED: 90,
    VERIFICATION_DOCUMENT_SUPPORTED: 365,
}

# ---------------------------------------------------------------------------
# Evidence relations
# ---------------------------------------------------------------------------
# How a source relates to the fact it is attached to. Three values, and the
# middle one is the reason this is a column rather than an assumption.
#
# The tempting design is a plain link table where every row means "supports",
# because that is what evidence usually means. It cannot survive first contact
# with reality: a member reads a statement that disagrees with what they had
# recorded, and the honest thing to do with that document is attach it. If the
# only available relation were supportive, attaching it would *raise* the fact's
# apparent support while being the strongest reason to doubt it. Refusing to
# store it is no better — the member would have looked at the contradiction and
# the store would have no record they did.
EVIDENCE_SUPPORTS = "SUPPORTS"
EVIDENCE_CONTRADICTS = "CONTRADICTS"
#: Related and neither confirming nor denying — the deed for the property a
#: valuation is about. Worth showing on the evidence tab, worth nothing as
#: support, and the state that stops CONTEXT being mislabelled as SUPPORTS
#: because those were the only two options.
EVIDENCE_CONTEXT = "CONTEXT"

EVIDENCE_RELATIONS: tuple[str, ...] = (
    EVIDENCE_SUPPORTS,
    EVIDENCE_CONTRADICTS,
    EVIDENCE_CONTEXT,
)

DEFAULT_EVIDENCE_RELATION = EVIDENCE_SUPPORTS

#: The only relation that counts toward a verification state. A set of one, held
#: as a set because the question "does this link support the badge" is asked in
#: several places and each of them must ask it the same way.
EVIDENCE_SUPPORTING: frozenset[str] = frozenset({EVIDENCE_SUPPORTS})

#: Whether a source a link names can still be read back.
#:
#: Computed at read time from the reference resolver, never stored. A stored
#: availability flag would be a cached answer to a question about *another*
#: table, and it would go wrong silently the moment a member deleted the
#: document — leaving a fact citing evidence the store believed was there. §51.
SOURCE_AVAILABLE = "AVAILABLE"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

# ---------------------------------------------------------------------------
# Conflict resolution outcomes
# ---------------------------------------------------------------------------
# What a member did about a contradiction, recorded durably. The engine detects
# and never chooses; these are the choices only a person makes.
#
# `SEPARATED` is the one that is easy to leave out and expensive to lack. Two
# facts flagged as contradictory are quite often not contradictory at all — they
# are two different things the detector could not tell apart, a work address and
# a home address, two policies on two cars. Without this outcome the member's
# only options are to delete a true fact or to leave a permanent false alarm in
# the queue, and both of those teach them to ignore the queue.
RESOLUTION_KEPT = "KEPT"
RESOLUTION_SEPARATED = "SEPARATED"
RESOLUTION_ALL_REJECTED = "ALL_REJECTED"
RESOLUTION_DEFERRED = "DEFERRED"

RESOLUTION_OUTCOMES: tuple[str, ...] = (
    RESOLUTION_KEPT,
    RESOLUTION_SEPARATED,
    RESOLUTION_ALL_REJECTED,
    RESOLUTION_DEFERRED,
)

#: Outcomes that close a conflict. DEFERRED does not: it records that a person
#: looked, so the queue can stop ranking it as untouched, without pretending the
#: disagreement went away.
RESOLUTION_CLOSES: frozenset[str] = frozenset({
    RESOLUTION_KEPT,
    RESOLUTION_SEPARATED,
    RESOLUTION_ALL_REJECTED,
})

# ---------------------------------------------------------------------------
# History change types
# ---------------------------------------------------------------------------
# What a row in the fact history records. Every entry is something that happened
# *to* a fact, in the past tense, and the pairs are deliberate: a supersession
# writes CORRECTED on the old row and CORRECTS on the new one, so reading either
# end of the chain finds the event without having to join to the other.
#
# There is no UPDATED. A fact's value is never edited in place — a correction is
# a new fact and a link — so there is no event for "the value changed" because
# that transition does not exist in this store.
CHANGE_CREATED = "CREATED"
CHANGE_REFRESHED = "REFRESHED"
CHANGE_CORRECTED = "CORRECTED"
CHANGE_CORRECTS = "CORRECTS"
CHANGE_VERIFICATION_SET = "VERIFICATION_SET"
CHANGE_EVIDENCE_LINKED = "EVIDENCE_LINKED"
CHANGE_EVIDENCE_UNLINKED = "EVIDENCE_UNLINKED"
CHANGE_CONFLICT_MARKED = "CONFLICT_MARKED"
CHANGE_CONFLICT_RESOLVED = "CONFLICT_RESOLVED"
CHANGE_ARCHIVED = "ARCHIVED"
#: The legacy backfill repaired a *label* on this row — its provenance,
#: sensitivity or domain — because the stored one was not a word this package
#: recognises.
#:
#: Deliberately not ``CORRECTED``, which is the other candidate and would be a
#: lie in the specific way this vocabulary exists to prevent. ``CORRECTED``
#: means the member's claim turned out to be wrong and a truer one replaced it;
#: it is written on the old row of a supersession and it is paired with
#: ``CORRECTS``. A backfill changes none of that. The value, its type, its
#: window and its evidence are all untouched — what changed is that a column
#: describing the row became readable. Filing that under ``CORRECTED`` would
#: put "your accountant revised this figure" and "our migration could not read
#: the sensitivity column" in the same bucket on the member's own timeline.
CHANGE_BACKFILLED = "BACKFILLED"

HISTORY_CHANGE_TYPES: tuple[str, ...] = (
    CHANGE_CREATED,
    CHANGE_REFRESHED,
    CHANGE_CORRECTED,
    CHANGE_CORRECTS,
    CHANGE_VERIFICATION_SET,
    CHANGE_EVIDENCE_LINKED,
    CHANGE_EVIDENCE_UNLINKED,
    CHANGE_CONFLICT_MARKED,
    CHANGE_CONFLICT_RESOLVED,
    CHANGE_ARCHIVED,
    CHANGE_BACKFILLED,
)

#: Closed set of reasons a history entry may carry, so `note_key` is a key and
#: never a sentence. A free-text note on a history row would be the one field in
#: this package that a caller could write arbitrary member data into, and it
#: would sit in a table with no sensitivity column to govern who may read it.
#:
#: Upper case like every other closed vocabulary in this module, and not as a
#: style preference: :func:`_canonical` upper-cases before comparing, so a
#: lowercase member of a vocabulary can never match itself. It normalizes to
#: ``None``, the writer treats that as an unrecognised note, and the annotation
#: is dropped from every history row while the row itself still lands — a
#: failure that shows up as blank notes rather than as an error.
NOTE_LEGACY_BACKFILL = "LEGACY_BACKFILL"
NOTE_CONFLICT_RESOLUTION = "CONFLICT_RESOLUTION"
NOTE_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
NOTE_VERIFICATION_EXPIRED = "VERIFICATION_EXPIRED"
NOTE_OWNER_ACTION = "OWNER_ACTION"
NOTE_SYSTEM = "SYSTEM"

HISTORY_NOTE_KEYS: tuple[str, ...] = (
    NOTE_LEGACY_BACKFILL,
    NOTE_CONFLICT_RESOLUTION,
    NOTE_SOURCE_UNAVAILABLE,
    NOTE_VERIFICATION_EXPIRED,
    NOTE_OWNER_ACTION,
    NOTE_SYSTEM,
)

# ---------------------------------------------------------------------------
# Review reasons
# ---------------------------------------------------------------------------
# Why a fact is in front of the member. The queue orders by weight, and the
# weight is attached to the *reason* rather than computed from a date, so the
# answer to "why is this first" is a sentence rather than an arithmetic
# coincidence. A member who cannot tell why the queue chose an item learns to
# distrust the order and reads the whole list, which is the same as having no
# queue.
REVIEW_CONTRADICTED = "CONTRADICTED"
REVIEW_VERIFICATION_FAILED = "VERIFICATION_FAILED"
REVIEW_DISPUTED = "DISPUTED"
REVIEW_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
REVIEW_VERIFICATION_EXPIRED = "VERIFICATION_EXPIRED"
REVIEW_VALIDITY_ENDING = "VALIDITY_ENDING"
REVIEW_PROPOSED = "PROPOSED"
REVIEW_STALE = "STALE"
REVIEW_UNKNOWN_ORIGIN = "UNKNOWN_ORIGIN"

REVIEW_REASONS: tuple[str, ...] = (
    REVIEW_CONTRADICTED,
    REVIEW_VERIFICATION_FAILED,
    REVIEW_DISPUTED,
    REVIEW_SOURCE_UNAVAILABLE,
    REVIEW_VERIFICATION_EXPIRED,
    REVIEW_VALIDITY_ENDING,
    REVIEW_PROPOSED,
    REVIEW_STALE,
    REVIEW_UNKNOWN_ORIGIN,
)

#: Priority weight per reason. Two things are true of this table and both
#: matter:
#:
#: Contradiction outranks everything, because it is the only reason where the
#: store is currently holding two answers and cannot tell which one it would
#: give if asked.
#:
#: Staleness ranks last, below unknown origin, because "this is old" is the
#: reason a member is most likely to already know about and least likely to be
#: harmed by. A queue that leads with age is a queue that leads with whatever is
#: oldest, which is a sort, not a priority.
REVIEW_WEIGHT: dict[str, int] = {
    REVIEW_CONTRADICTED: 100,
    REVIEW_VERIFICATION_FAILED: 80,
    REVIEW_DISPUTED: 70,
    REVIEW_SOURCE_UNAVAILABLE: 60,
    REVIEW_VERIFICATION_EXPIRED: 50,
    REVIEW_VALIDITY_ENDING: 40,
    REVIEW_PROPOSED: 30,
    REVIEW_UNKNOWN_ORIGIN: 20,
    REVIEW_STALE: 10,
}

# ---------------------------------------------------------------------------
# Integrity findings
# ---------------------------------------------------------------------------
# What a structural sweep of the store can find. Distinct from `REVIEW_REASONS`
# in a way worth stating plainly, because the two lists look similar and answer
# different questions.
#
# A review reason is about the *content* of a fact: it is old, it is contested,
# nobody has checked it. Those are things a member decides about, and every one
# of them can be true of a perfectly well-formed store.
#
# An integrity finding is about the *store itself* being in a state its own
# writer cannot produce. A supersession cycle is not a fact the member should
# reconsider — it is a shape that means some walk of the chain will return the
# wrong answer. Mixing the two would put "you should look at this again" and
# "this database is lying to you" in one queue with one weight scale, and the
# second would be triaged behind the first for the rest of time.
#
# Everything here is therefore expected to have a count of zero on a healthy
# store, which is the strongest thing that can be said about a diagnostic: a
# non-zero count is *always* worth explaining, and never routine.
INTEGRITY_ACTIVE_WITH_SUCCESSOR = "ACTIVE_WITH_SUCCESSOR"
INTEGRITY_SUPERSESSION_CYCLE = "SUPERSESSION_CYCLE"
INTEGRITY_VERIFIED_WITHOUT_EVIDENCE = "VERIFIED_WITHOUT_EVIDENCE"
INTEGRITY_VERIFIED_EVIDENCE_UNRESOLVABLE = "VERIFIED_EVIDENCE_UNRESOLVABLE"
INTEGRITY_SUPERSESSION_DANGLING = "SUPERSESSION_DANGLING"
INTEGRITY_SUPERSESSION_ASYMMETRIC = "SUPERSESSION_ASYMMETRIC"
INTEGRITY_SUPERSEDED_WITHOUT_SUCCESSOR = "SUPERSEDED_WITHOUT_SUCCESSOR"
INTEGRITY_PROVENANCE_ORPHANED = "PROVENANCE_ORPHANED"
INTEGRITY_PROVENANCE_UNREADABLE = "PROVENANCE_UNREADABLE"
INTEGRITY_DUPLICATE_VALUE = "DUPLICATE_VALUE"

INTEGRITY_FINDINGS: tuple[str, ...] = (
    INTEGRITY_ACTIVE_WITH_SUCCESSOR,
    INTEGRITY_SUPERSESSION_CYCLE,
    INTEGRITY_VERIFIED_WITHOUT_EVIDENCE,
    INTEGRITY_VERIFIED_EVIDENCE_UNRESOLVABLE,
    INTEGRITY_SUPERSESSION_DANGLING,
    INTEGRITY_SUPERSESSION_ASYMMETRIC,
    INTEGRITY_SUPERSEDED_WITHOUT_SUCCESSOR,
    INTEGRITY_PROVENANCE_ORPHANED,
    INTEGRITY_PROVENANCE_UNREADABLE,
    INTEGRITY_DUPLICATE_VALUE,
)

#: Severity per finding, ordered by *what the store does wrong while the finding
#: stands*, not by how hard it looks to fix.
#:
#: ACTIVE_WITH_SUCCESSOR leads, above even a cycle. A cycle makes a chain walk
#: return a truncated answer, which is visibly wrong; an ACTIVE row that already
#: has a successor is invisibly wrong — it is a value the member corrected,
#: still filed as current, still eligible for every read and every projection.
#: The correction happened and the store kept quoting the old number anyway.
#:
#: VERIFIED_WITHOUT_EVIDENCE sits above the structural link findings because it
#: is the one that reaches the member as a *claim*. A broken pointer degrades a
#: history view; a badge with nothing under it is an assertion the store cannot
#: substantiate, rendered as though it could.
#:
#: DUPLICATE_VALUE ranks last and deliberately so. See `integrity.py` for why
#: the honest definition of a duplicate here is narrow: two rows agreeing from
#: two different sources are corroboration, and a sweep that reported those as
#: duplicates would be advising the member to delete their second source.
INTEGRITY_SEVERITY: dict[str, int] = {
    INTEGRITY_ACTIVE_WITH_SUCCESSOR: 100,
    INTEGRITY_SUPERSESSION_CYCLE: 90,
    INTEGRITY_VERIFIED_WITHOUT_EVIDENCE: 80,
    INTEGRITY_VERIFIED_EVIDENCE_UNRESOLVABLE: 70,
    INTEGRITY_SUPERSESSION_DANGLING: 60,
    INTEGRITY_SUPERSESSION_ASYMMETRIC: 50,
    INTEGRITY_SUPERSEDED_WITHOUT_SUCCESSOR: 40,
    INTEGRITY_PROVENANCE_ORPHANED: 30,
    INTEGRITY_PROVENANCE_UNREADABLE: 20,
    INTEGRITY_DUPLICATE_VALUE: 10,
}

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


def normalize_verification(value: object) -> str | None:
    """Canonical verification state, or ``None``.

    Returns ``None`` rather than defaulting for the same reason the others do,
    and the stakes here are the highest of the set: silently reading an
    unrecognised word as UNVERIFIED buries a state somebody meant to record,
    and silently reading it as anything positive draws a badge nobody earned.
    """
    return _canonical(value, VERIFICATION_STATES)


def normalize_resolution(value: object) -> str | None:
    """Canonical conflict-resolution outcome, or ``None``."""
    return _canonical(value, RESOLUTION_OUTCOMES)


def normalize_review_reason(value: object) -> str | None:
    """Canonical review reason, or ``None``."""
    return _canonical(value, REVIEW_REASONS)


def normalize_integrity_finding(value: object) -> str | None:
    """Canonical integrity finding kind, or ``None``."""
    return _canonical(value, INTEGRITY_FINDINGS)


def normalize_evidence_relation(value: object) -> str | None:
    """Canonical evidence relation, or ``None``."""
    return _canonical(value, EVIDENCE_RELATIONS)


def evidence_supports(value: object) -> bool:
    """Whether a link with this relation counts toward verification.

    ``False`` for anything unrecognised, which is the direction that matters: an
    unreadable relation must not be able to hold a verified badge up.
    """
    return normalize_evidence_relation(value) in EVIDENCE_SUPPORTING


def normalize_change_type(value: object) -> str | None:
    """Canonical history change type, or ``None``."""
    return _canonical(value, HISTORY_CHANGE_TYPES)


def normalize_note_key(value: object) -> str | None:
    """Canonical history note key, or ``None``.

    An empty note is legitimate — most history entries need no annotation — so
    the caller distinguishes "no note" from "a note I could not parse" before
    calling: this returns ``None`` for both, and the writer treats ``None`` from
    a non-empty input as a rejection rather than dropping it silently.
    """
    return _canonical(value, HISTORY_NOTE_KEYS)


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


def provenance_may_settle(value: object) -> bool:
    """May a fact with this provenance decide a contradiction in its favour?

    False for anything unrecognised, for the two degraded states, and for the
    two origins that are honest to record and never authoritative — a model's
    proposal and an unknown legacy source. Strength alone would nearly get this
    right, since all four rank at zero, but "nearly" is doing dangerous work in
    that sentence: a zero-versus-zero comparison has to break its tie somehow,
    and without this predicate the tiebreak is whatever the sort was stable on.
    """
    source = normalize_provenance(value)
    if not source:
        return False
    return source not in NON_SETTLING_PROVENANCE


def verification_rank(value: object) -> int:
    """Position of a verification state on the checked-ness scale.

    An unrecognised state ranks with FAILED rather than with UNVERIFIED. A word
    this module has not been taught is not a neutral absence of checking — it is
    a claim nobody here can interpret, and the only safe place to sort a claim
    nobody can interpret is below the ones that can.
    """
    known = normalize_verification(value)
    if not known:
        return VERIFICATION_RANK[VERIFICATION_FAILED]
    return VERIFICATION_RANK[known]


def verification_is_positive(value: object) -> bool:
    """Does this state entitle the fact to read as checked? Unknown is False."""
    return (normalize_verification(value) or "") in VERIFIED_STATES


def verification_needs_evidence(value: object) -> bool:
    """Must a fact in this state carry at least one evidence link?

    False for an unrecognised state, because the writer rejects those outright
    and a True here would turn "unknown verification state" into the confusing
    error "evidence required" instead of the accurate one.
    """
    return (normalize_verification(value) or "") in VERIFICATION_REQUIRES_EVIDENCE


def review_weight(value: object) -> int:
    """Priority weight of a review reason. Unknown ranks at zero.

    Zero rather than the maximum: a reason this module cannot name is a bug in
    whatever produced it, and letting a bug jump the queue to the top means the
    first thing the member sees is the thing the system understands least.
    """
    return REVIEW_WEIGHT.get(normalize_review_reason(value) or "", 0)


def resolution_closes(value: object) -> bool:
    """Does this outcome end the conflict? Unknown is False."""
    return (normalize_resolution(value) or "") in RESOLUTION_CLOSES


def integrity_severity(value: object) -> int:
    """Severity of an integrity finding. Unknown ranks at zero.

    Zero for the same reason :func:`review_weight` uses zero: a finding kind
    this module cannot name came from something that is itself broken, and
    letting it sort to the top would put the least trustworthy diagnostic in
    front of the most serious real one.
    """
    return INTEGRITY_SEVERITY.get(normalize_integrity_finding(value) or "", 0)
