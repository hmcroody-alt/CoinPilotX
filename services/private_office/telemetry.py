"""Stage 38 — privacy-safe telemetry for the Private Office.

The problem this module exists to solve
---------------------------------------
Telemetry is the one place in a private system where a leak is both easy and
invisible. Nobody sets out to put a member's policy number in a metric. What
happens instead is that someone debugging a retrieval bug adds
``subject_id=...`` to an event because it is the field that would have told
them what went wrong, and it does tell them, and it ships. Metrics then flow
somewhere the private tables deliberately do not: an aggregation backend, a
dashboard, a log drain, a third-party APM, a retention window measured in
years, and an audience of everyone with a login rather than the member and
nobody else. Every protection this package spent thirty-seven stages building —
owner isolation, the sensitivity ceiling, an audit table with no value column —
is bypassed the moment a value is copied into a counter.

So the guarantee here is not "we reviewed the call sites". It is that a value
**cannot be expressed** in this API:

* An event may only be one of the six declared in :data:`EVENTS`. An
  undeclared name is dropped, not passed through.
* An event may only carry the fields its spec declares. An undeclared field is
  dropped, not passed through.
* Each field declares a *kind*, and the kind is enforced at emission:

  ``COUNT``  a non-negative integer, clamped. Cannot hold text at all.
  ``FLAG``   a boolean.
  ``ENUM``   a member of a closed vocabulary fixed in this file. Anything not
             in the vocabulary becomes ``"other"`` — not the original string.

There is deliberately no ``TEXT`` kind and no ``**extra`` passthrough. A fact
value is a string, an id, or a number that means something; none of those can
survive a COUNT, a FLAG, or a vocabulary membership test. That is why
``test_private_observability.py`` can assert the property by pushing real
member data through every event and checking the emitted payloads, rather than
by grepping call sites and hoping.

Why enums are closed here rather than imported wholesale
--------------------------------------------------------
The vocabularies below are drawn from ``model`` but are re-stated as explicit
frozensets rather than referenced dynamically. If a future domain or node type
is added to ``model``, it does not silently become emittable; someone has to
decide it is safe to publish and add it here. That is the intended friction.
Node *types* and domains are categories, not content — "this member has a
PROPERTY node in the FINANCIAL domain" is a shape, not a secret. An
``external_ref`` or a ``subject_id`` is content, and neither has a kind that
would let it through.

Delivery
--------
Emission is a log line on a dedicated logger (``private_office.telemetry``) at
INFO, formatted as sorted ``key=value`` pairs. That is deliberately boring: it
needs no new infrastructure, it is greppable, it is what the existing
observability in this repository already does, and a metrics backend can be
attached later by adding a handler rather than by changing thirty call sites.
:func:`emit` never raises — telemetry that can break the operation it measures
is worse than no telemetry — and it returns the sanitised payload so callers
and tests can see exactly what left.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

LOGGER = logging.getLogger("private_office.telemetry")

# ---------------------------------------------------------------------------
# Field kinds
# ---------------------------------------------------------------------------
KIND_COUNT = "count"
KIND_FLAG = "flag"
KIND_ENUM = "enum"

#: What an ENUM field becomes when the supplied value is outside its
#: vocabulary. A single shared token, so an unrecognised input can never be
#: distinguished from any other unrecognised input — including one that was
#: unrecognised because it was a member's data.
OTHER = "other"

#: Ceiling on COUNT fields. Counts here describe bounded result sets (the
#: retrieval caps are 100 nodes, 250 edges, 400 facts), so a number far above
#: that is a bug rather than a measurement, and clamping keeps a runaway loop
#: from writing an unbounded integer into a metric name space.
MAX_COUNT = 1_000_000

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------
# Restated deliberately — see the module docstring. Categories only.
DOMAIN_VOCAB = frozenset({
    "GENERAL", "FINANCIAL", "LEGAL", "HEALTH", "FAMILY", "IDENTITY", "SECURITY",
})

SENSITIVITY_VOCAB = frozenset({
    "PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_SENSITIVE", "RESTRICTED",
})

PROVENANCE_VOCAB = frozenset({
    "VERIFIED", "PROVIDER_ASSERTED", "DOCUMENT_EXTRACTED", "USER_ASSERTED",
    "INFERRED", "ESTIMATED", "STALE", "CONFLICTING",
    # Ledger-core additions. These have to be listed or ``sanitize`` collapses
    # each of them to ``other``, and a provenance distribution in which a third
    # of the writes are ``other`` measures nothing. They pass the publishability
    # test for the same reason the original eight do: each names a *class of
    # source*, which is a shape, not a secret.
    "SYSTEM_OBSERVED", "MEETING_DERIVED", "HUMAN_CONFIRMED", "UNDX_PROPOSED",
    "LEGACY_UNKNOWN",
})

#: Verification state — a separate axis from provenance, restated here for the
#: same reason every other vocabulary is restated: telemetry does not import
#: ``model``, so adding a state there does not silently make it emittable.
#: Drift between the two is caught by the vocabulary parity test rather than by
#: a dashboard quietly filling with ``other``.
VERIFICATION_VOCAB = frozenset({
    "UNVERIFIED", "USER_CONFIRMED", "EVIDENCE_SUPPORTED", "VERIFIED",
    "PROVIDER_VERIFIED", "NEEDS_REVIEW", "CONFLICTING", "DISPUTED",
    "SUPERSEDED", "EXPIRED", "REVOKED", "LEGACY_UNKNOWN",
})

#: Lifecycle disposition of a fact row.
LIFECYCLE_VOCAB = frozenset({
    "ACTIVE", "SUPERSEDED", "ARCHIVED", "EXPIRED", "REVOKED",
})

#: The lifecycle operations the canonical writer exposes. Names the *operation*
#: only — never its subject, its key, or its value. "A fact was revoked" is a
#: process-health number; "which fact" is content.
FACT_OPERATION_VOCAB = frozenset({
    "create", "refresh", "confirm", "revise", "dispute", "archive", "revoke",
    "expire", "supersede",
    # The two conflict operations. `flag_conflict` is machine-driven — the
    # detector stamping a contested row — and `resolve` is not, which is the
    # single most useful thing this vocabulary can distinguish. A dashboard
    # showing flags rising while resolutions stay flat is a member drowning in
    # disagreements nobody is settling, and that is invisible if both arrive as
    # "a fact changed state".
    "flag_conflict", "resolve",
    # Evidence and review. `attach_evidence` and `detach_evidence` are a matched
    # pair whose *ratio* is the signal — a store where the second is catching up
    # with the first is one where support is being dismantled as fast as it is
    # added — and `flag_review` is the machine-driven third, which is why it must
    # not be counted with either.
    "attach_evidence", "detach_evidence", "flag_review",
})

#: Who performed a fact operation, as a *class* rather than an identity. The
#: distinction that matters for governance is human-versus-machine: a ledger in
#: which most confirmations came from UNDX is a different ledger from one in
#: which most came from the owner, and no user id is needed to see that.
ACTOR_TYPE_VOCAB = frozenset({
    "owner", "system", "provider", "undx", "document", "unknown",
})

#: Outcomes of a lifecycle operation. ``unchanged`` is listed separately from
#: ``applied`` on purpose: confirming a fact that was already confirmed is not a
#: failure, but counting it as an application would make a review queue look
#: like it was being worked when the same row is being re-confirmed. ``refused``
#: covers a transition the state machine forbids — out of a terminal state, for
#: instance — and is a distinct signal from ``rejected`` input validation.
LIFECYCLE_OUTCOME_VOCAB = frozenset({
    "applied", "unchanged", "refused", "rejected", "not_found",
})

NODE_TYPE_VOCAB = frozenset({
    "PERSON", "BUSINESS", "PROPERTY", "INSURANCE_POLICY", "CONTRACT",
    "DOCUMENT", "PROFESSIONAL", "ASSET", "LIABILITY",
})

RELATION_VOCAB = frozenset({
    "OWNS", "ADVISED_BY", "COVERED_BY", "SECURED_BY", "GOVERNED_BY", "DESCRIBES",
})

#: Retrieval intents. These are *policy* names chosen by this package, not
#: anything the member typed, which is why they are safe to publish and why an
#: unrecognised intent collapses to ``other`` rather than being echoed — an
#: attacker probing with a crafted intent string must not see it reflected in
#: a log line.
INTENT_VOCAB = frozenset({
    "property_portfolio", "insurance_coverage", "business_structure",
    "legal_documents", "health_context", "identity_context", "general",
})

#: Why a read was refused. Mirrors the ``denied`` reasons in ``retrieval``.
DENIAL_VOCAB = frozenset({
    "actor_is_not_owner", "domain_join_not_permitted", "unknown_intent",
    "no_owner", "unknown_sensitivity_ceiling", "isolated_domain_join",
})

#: Writer outcomes, from ``facts`` and ``graph``.
WRITE_OUTCOME_VOCAB = frozenset({
    "written", "refreshed", "rejected", "created", "existing", "superseded",
})

#: Schema bootstrap outcomes, from ``schema``. Stage 35's three states.
SCHEMA_STATE_VOCAB = frozenset({"ready", "missing", "error"})

#: Which process performed the bootstrap. Stage 34's whole point is that the
#: answer is not always "web", so the answer is recorded.
PROCESS_VOCAB = frozenset({"web", "worker", "script", "test", "unknown"})

#: Why two facts were judged incompatible, from ``contradictions``.
CONFLICT_REASON_VOCAB = frozenset({
    "values_differ_beyond_tolerance", "dates_differ",
    "boolean_values_differ", "text_values_differ",
})

#: How a conflict was settled. ``dismissed`` is not a failure mode — a member
#: concluding that two sources were never describing the same thing is a real
#: answer, and one the ledger would otherwise force them to fake by nominating
#: a winner they do not believe in.
CONFLICT_RESOLUTION_VOCAB = frozenset({"winner_chosen", "dismissed"})

#: What happened to the rows that did not win. Published because the two are
#: very different decisions wearing similar buttons: a dispute leaves the losing
#: figure live and flagged, a supersession retires it. A build where the second
#: became the default would quietly start discarding sources, and the only
#: signal before a member noticed would be this ratio moving.
CONFLICT_DISPOSITION_VOCAB = frozenset({"dispute", "supersede", "none"})

#: The kind of thing a fact was cited against. Mirrors ``model.EVIDENCE_TYPES``
#: and is safe to publish for the same reason domains are: every member is a
#: policy name this package chose, and none of them is an identifier of the
#: particular document. What it buys is the one question worth asking about a
#: sourcing effort — whether the evidence being attached is material this
#: product can open and re-show, or ``external``, which is the member's word for
#: something nobody can check. A store whose support is mostly external is
#: sourced in name only.
EVIDENCE_TYPE_VOCAB = frozenset({
    "document", "meeting", "record", "statement", "external",
})

#: Why a fact was put in front of the member. `stale` is the passage of time,
#: `evidence_removed` is a demotion caused by somebody detaching support, and
#: `owner_request` is a member asking to look at something again. Kept apart
#: because a queue made of the first is a store that needs refreshing and a
#: queue made of the second is a store that is being taken apart.
REVIEW_REASON_VOCAB = frozenset({
    "stale", "evidence_removed", "owner_request",
})

#: The six Batch C record primitives. A closed vocabulary for the same reason
#: intents are: this is a policy name chosen by the package, never anything a
#: member typed, so it is safe to publish and an unrecognised one collapses to
#: ``other`` rather than being echoed into a log line.
RECORD_TYPE_VOCAB = frozenset({
    "OBLIGATION", "EVENT", "DECISION", "REQUEST", "RISK", "OPPORTUNITY",
})

#: Record writer outcomes. Note ``existing`` and ``revised``: a store that
#: cannot distinguish a new obligation from the same obligation arriving again
#: from a nightly sweep looks busy either way.
RECORD_OUTCOME_VOCAB = frozenset({
    "created", "existing", "updated", "revised", "rejected",
})

#: Every terminal status across the six primitives. Publishing *which* closure
#: a record reached is the difference between "the concierge queue drains" and
#: "the concierge queue is cancelled", which are the same count.
RECORD_STATUS_VOCAB = frozenset({
    "RESOLVED", "DISMISSED", "DECIDED", "ABANDONED",
    "COMPLETED", "CANCELED", "PASSED", "CLOSED",
})

#: Private Meetings (mission §49). Note what these vocabularies structurally
#: cannot express: a meeting title, a chat line, a channel name, a token, a
#: meeting code, or who was in the room. A meeting metric may say that *a*
#: meeting started and ended for *a* reason — lifecycle words chosen by
#: ``meetings.py``, never anything a member typed.
MEETING_TRANSITION_VOCAB = frozenset({
    "created", "started", "ended", "cancelled", "failed",
})

#: Why a meeting reached a terminal state, restated from ``meetings``. The
#: caller passes the raw ``end_reason`` column value straight through because
#: membership is the filter: a crafted or free-text reason collapses to
#: ``other`` instead of being echoed into a log line. ``not_ended`` is the
#: explicit non-terminal marker so a "created"/"started" event never has to
#: fake a reason.
MEETING_END_REASON_VOCAB = frozenset({
    "not_ended", "ended_by_host", "cancelled_by_host", "never_started",
    "start_timed_out", "max_duration", "host_disconnected", "call_expired",
    "empty_timeout",
})

#: The only two ways a join can succeed. Refusals (locked, full, blocked,
#: over) raise before any emit and are already counted by the audit trail the
#: member controls — a platform metric on *who gets refused* would be a
#: surveillance number, not a health number.
MEETING_JOIN_OUTCOME_VOCAB = frozenset({"admitted", "waiting_room"})

MEETING_ROLE_VOCAB = frozenset({"HOST", "CO_HOST", "PARTICIPANT"})


# ---------------------------------------------------------------------------
# The events
# ---------------------------------------------------------------------------
EVENT_FACT_WRITE = "private_office.fact_write"
#: Every non-create mutation of a fact's standing: confirm, revise, dispute,
#: archive, revoke, expire, supersede. Separate from ``fact_write`` because the
#: two answer different questions — ``fact_write`` measures intake, this
#: measures whether anything is ever *checked* after intake, which is the
#: difference between a truth ledger and an append-only pile.
EVENT_FACT_LIFECYCLE = "private_office.fact_lifecycle"
EVENT_GRAPH_WRITE = "private_office.graph_write"
EVENT_CONTEXT_RETRIEVED = "private_office.context_retrieved"
EVENT_CONTEXT_DENIED = "private_office.context_denied"
EVENT_CONFLICT_DETECTED = "private_office.conflict_detected"
#: The other half of the pair. Detection without resolution is a metric that
#: only ever goes up, and a package that shipped one and not the other would be
#: reporting a growing pile of disagreements with no way to see any of them
#: being closed.
EVENT_CONFLICT_RESOLVED = "private_office.conflict_resolved"
#: Evidence was attached to or removed from a fact. One event with a direction
#: rather than two, because the pair is only meaningful as a ratio and splitting
#: it across two event names makes the ratio something a dashboard has to
#: reassemble.
EVENT_EVIDENCE_LINKED = "private_office.evidence_linked"
#: A staleness sweep ran. Reports what it scanned as well as what it flagged,
#: because the failure this package exists to prevent is a sweep that reports
#: zero flags when it could not read the table — a shape indistinguishable from
#: a store in perfect health if only the flag count is published.
EVENT_REVIEW_SWEEP = "private_office.review_sweep"
EVENT_SCHEMA_STATE = "private_office.schema_state"
EVENT_RECORD_WRITE = "private_office.record_write"
EVENT_RECORD_CLOSED = "private_office.record_closed"
EVENT_RECORDS_RETRIEVED = "private_office.records_retrieved"
EVENT_MEETING_LIFECYCLE = "private_office.meeting_lifecycle"
EVENT_MEETING_JOIN = "private_office.meeting_join"
EVENT_MEETING_SWEEP = "private_office.meeting_sweep"

#: ``event name -> {field name -> (kind, vocabulary or None)}``.
#:
#: Note what no event carries: ``owner_user_id``, ``actor_user_id``,
#: ``subject_id``, ``node_id``, ``fact_key``, ``external_ref``,
#: ``typed_value``, ``provenance_ref``. Identity of the *person* is out because
#: this is a product metric, not an audit record — ``private_audit_events``
#: already holds the per-actor trail, under the member's own control, with a
#: retention story of its own. Identity of the *object* is out because a node
#: id plus a fact type is close enough to content to be worth nothing and cost
#: something.
EVENTS: dict[str, dict[str, tuple[str, frozenset[str] | None]]] = {
    EVENT_FACT_WRITE: {
        "outcome": (KIND_ENUM, WRITE_OUTCOME_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
        "sensitivity": (KIND_ENUM, SENSITIVITY_VOCAB),
        "provenance_type": (KIND_ENUM, PROVENANCE_VOCAB),
        "verification_state": (KIND_ENUM, VERIFICATION_VOCAB),
        "superseded": (KIND_FLAG, None),
    },
    EVENT_FACT_LIFECYCLE: {
        "operation": (KIND_ENUM, FACT_OPERATION_VOCAB),
        "outcome": (KIND_ENUM, LIFECYCLE_OUTCOME_VOCAB),
        "actor_type": (KIND_ENUM, ACTOR_TYPE_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
        "sensitivity": (KIND_ENUM, SENSITIVITY_VOCAB),
        "provenance_type": (KIND_ENUM, PROVENANCE_VOCAB),
        # Both ends of the transition. One without the other is unreadable: a
        # count of facts entering DISPUTED does not say whether they came from
        # VERIFIED (a regression worth alerting on) or from NEEDS_REVIEW (the
        # queue working as designed).
        "from_state": (KIND_ENUM, VERIFICATION_VOCAB),
        "to_state": (KIND_ENUM, VERIFICATION_VOCAB),
        "lifecycle_state": (KIND_ENUM, LIFECYCLE_VOCAB),
        "chained": (KIND_FLAG, None),
    },
    EVENT_GRAPH_WRITE: {
        "outcome": (KIND_ENUM, WRITE_OUTCOME_VOCAB),
        "node_type": (KIND_ENUM, NODE_TYPE_VOCAB),
        "relation_type": (KIND_ENUM, RELATION_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
        "sensitivity": (KIND_ENUM, SENSITIVITY_VOCAB),
    },
    EVENT_CONTEXT_RETRIEVED: {
        "intent": (KIND_ENUM, INTENT_VOCAB),
        "sensitivity_ceiling": (KIND_ENUM, SENSITIVITY_VOCAB),
        "domain_count": (KIND_COUNT, None),
        "node_count": (KIND_COUNT, None),
        "edge_count": (KIND_COUNT, None),
        "fact_count": (KIND_COUNT, None),
        "conflict_count": (KIND_COUNT, None),
        "stale_count": (KIND_COUNT, None),
        "depth_reached": (KIND_COUNT, None),
        "truncated": (KIND_FLAG, None),
    },
    EVENT_CONTEXT_DENIED: {
        "intent": (KIND_ENUM, INTENT_VOCAB),
        "reason": (KIND_ENUM, DENIAL_VOCAB),
        # Not "who" — only whether the refusal was a cross-account attempt,
        # which is the part that matters for a rate that should be zero.
        "cross_account": (KIND_FLAG, None),
    },
    EVENT_CONFLICT_DETECTED: {
        "reason": (KIND_ENUM, CONFLICT_REASON_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
        "competing_count": (KIND_COUNT, None),
        "resolved": (KIND_FLAG, None),
    },
    EVENT_CONFLICT_RESOLVED: {
        "resolution": (KIND_ENUM, CONFLICT_RESOLUTION_VOCAB),
        "reason": (KIND_ENUM, CONFLICT_REASON_VOCAB),
        "loser_disposition": (KIND_ENUM, CONFLICT_DISPOSITION_VOCAB),
        "actor_type": (KIND_ENUM, ACTOR_TYPE_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
        "competing_count": (KIND_COUNT, None),
        # How many losing rows the writer actually moved. Deliberately separate
        # from `competing_count`: they differ when a row was already in the
        # target state or refused the transition, and a resolution that settled
        # three competitors by moving one of them is a bug that this pair of
        # numbers makes visible and either number alone hides.
        "losers_moved": (KIND_COUNT, None),
        # Whether the resolution replaced an earlier one for the same conflict.
        # Re-settling is legitimate — the member changed their mind, or new
        # evidence arrived — but a rate that climbs means the detector is
        # producing conflicts people cannot decide once.
        "resettled": (KIND_FLAG, None),
    },
    EVENT_EVIDENCE_LINKED: {
        "evidence_type": (KIND_ENUM, EVIDENCE_TYPE_VOCAB),
        "actor_type": (KIND_ENUM, ACTOR_TYPE_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
        # True for an attachment, false for a detachment. A flag rather than an
        # enum because there are exactly two directions and there will not be a
        # third; an enum here would invite one.
        "attached": (KIND_FLAG, None),
        # Whether the fact's verification state moved as a result. Attaching
        # evidence to a DISPUTED fact records the citation and deliberately
        # leaves the member's own judgement alone, so attachments that promote
        # and attachments that do not are different events wearing one name, and
        # this is what tells them apart. A build where this went uniformly false
        # would be a build where EVIDENCE_SUPPORTED had quietly died again.
        "promoted": (KIND_FLAG, None),
        # How many live citations the fact has after this change. The number
        # that matters on a detachment: going to zero is what demotes the fact,
        # and a detachment that left support behind is a different event from
        # one that took the last of it away.
        "live_evidence": (KIND_COUNT, None),
    },
    EVENT_REVIEW_SWEEP: {
        "reason": (KIND_ENUM, REVIEW_REASON_VOCAB),
        "actor_type": (KIND_ENUM, ACTOR_TYPE_VOCAB),
        # Both halves, always. `scanned` without `flagged` cannot show a sweep
        # that is finding nothing because there is nothing wrong; `flagged`
        # without `scanned` cannot distinguish that from a sweep that read no
        # rows at all. This package was written out of an incident where those
        # two situations reported the same numbers.
        "scanned": (KIND_COUNT, None),
        "flagged": (KIND_COUNT, None),
    },
    EVENT_SCHEMA_STATE: {
        "state": (KIND_ENUM, SCHEMA_STATE_VOCAB),
        "process": (KIND_ENUM, PROCESS_VOCAB),
        "missing_table_count": (KIND_COUNT, None),
        "added_column_count": (KIND_COUNT, None),
        "cached": (KIND_FLAG, None),
    },
    # Batch C. Deliberately absent from all three: the title of an obligation,
    # the question a decision asks, the description of a concierge request, the
    # summary of a risk. Those are the whole content of the primitive, and a
    # metric that carried any of them would be a copy of the private store in
    # the log aggregator — the one place with the loosest retention and the
    # widest read access. What is published is process health: how many were
    # written, how many closed and into which terminal state, how long a
    # retrieval took.
    EVENT_RECORD_WRITE: {
        "outcome": (KIND_ENUM, RECORD_OUTCOME_VOCAB),
        "record_type": (KIND_ENUM, RECORD_TYPE_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
        "sensitivity": (KIND_ENUM, SENSITIVITY_VOCAB),
        "provenance_type": (KIND_ENUM, PROVENANCE_VOCAB),
        "superseded": (KIND_FLAG, None),
    },
    EVENT_RECORD_CLOSED: {
        "record_type": (KIND_ENUM, RECORD_TYPE_VOCAB),
        "status": (KIND_ENUM, RECORD_STATUS_VOCAB),
        "domain": (KIND_ENUM, DOMAIN_VOCAB),
    },
    # Private Meetings. Deliberately absent: the meeting id, the meeting code,
    # the channel name, the call id, any user id, the title, and anything from
    # meeting chat. A meeting is the single most content-dense object the
    # Private Office holds — the metric answers "do meetings start, end
    # honestly, and get swept?" and nothing else (§49: no meeting content,
    # tokens, or channel names in telemetry).
    EVENT_MEETING_LIFECYCLE: {
        "transition": (KIND_ENUM, MEETING_TRANSITION_VOCAB),
        "end_reason": (KIND_ENUM, MEETING_END_REASON_VOCAB),
        "scheduled": (KIND_FLAG, None),
        "waiting_room": (KIND_FLAG, None),
        "participant_count": (KIND_COUNT, None),
    },
    EVENT_MEETING_JOIN: {
        "outcome": (KIND_ENUM, MEETING_JOIN_OUTCOME_VOCAB),
        "role": (KIND_ENUM, MEETING_ROLE_VOCAB),
        # Reconnect vs first join — the number that tells "the app rejoins the
        # same logical participant" (§38) apart from churn, without naming
        # anyone.
        "returning": (KIND_FLAG, None),
    },
    EVENT_MEETING_SWEEP: {
        "swept_count": (KIND_COUNT, None),
    },
    EVENT_RECORDS_RETRIEVED: {
        "record_type": (KIND_ENUM, RECORD_TYPE_VOCAB),
        "intent": (KIND_ENUM, INTENT_VOCAB),
        "sensitivity_ceiling": (KIND_ENUM, SENSITIVITY_VOCAB),
        "record_count": (KIND_COUNT, None),
        # Milliseconds, as a count. Clamped by `MAX_COUNT` like any other, which
        # is fine: a retrieval that took longer than a thousand seconds is a
        # timeout, and the exact figure would tell you nothing the clamp does
        # not.
        "latency_ms": (KIND_COUNT, None),
        "truncated": (KIND_FLAG, None),
    },
}

#: Field names that must never appear in any spec. Asserted by
#: :func:`spec_is_sound` and by the test suite, so a future field called
#: ``subject_id`` fails on the way in rather than after it has been shipping
#: for a month.
FORBIDDEN_FIELDS = frozenset({
    "owner_user_id", "actor_user_id", "user_id", "subject_id", "subject_type",
    "node_id", "edge_id", "fact_id", "fact_key", "node_key", "edge_key",
    "external_ref", "typed_value", "value", "value_number", "provenance_ref",
    "purpose", "detail", "message", "error", "note", "payload", "ref",
})


def spec_is_sound() -> list[str]:
    """Problems with :data:`EVENTS` itself. Empty list means the table is safe.

    Checked at import time below, and again by the test suite. The failure this
    catches is not a bad call site but a bad *schema* — a field kind that does
    not exist, an ENUM without a vocabulary, a vocabulary member that is not a
    plain string, or a field whose name is one of the identifiers this package
    has decided never to publish.
    """
    problems: list[str] = []
    for event, fields in EVENTS.items():
        if not event.startswith("private_office."):
            problems.append(f"{event}: event names must be namespaced")
        for name, spec in fields.items():
            if name in FORBIDDEN_FIELDS:
                problems.append(f"{event}.{name}: forbidden field name")
            if not isinstance(spec, tuple) or len(spec) != 2:
                problems.append(f"{event}.{name}: malformed spec")
                continue
            kind, vocab = spec
            if kind not in (KIND_COUNT, KIND_FLAG, KIND_ENUM):
                problems.append(f"{event}.{name}: unknown kind {kind!r}")
            if kind == KIND_ENUM:
                if not vocab:
                    problems.append(f"{event}.{name}: ENUM without a vocabulary")
                elif not all(isinstance(v, str) and v for v in vocab):
                    problems.append(f"{event}.{name}: vocabulary is not all strings")
                elif OTHER in vocab:
                    # Otherwise a real value and an unrecognised one would be
                    # indistinguishable in the opposite direction.
                    problems.append(f"{event}.{name}: vocabulary contains {OTHER!r}")
            elif vocab is not None:
                problems.append(f"{event}.{name}: non-ENUM field carries a vocabulary")
    return problems


def _coerce_count(value: object) -> int:
    """A non-negative, clamped integer — or 0 for anything that is not one.

    Note that a string is not parsed even when it looks numeric. ``"382"``
    becoming ``382`` would mean a text field could reach a metric as long as it
    happened to be digits, and a policy number is digits.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, min(value, MAX_COUNT))
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return 0
        return max(0, min(int(value), MAX_COUNT))
    return 0


def _coerce_enum(value: object, vocab: frozenset[str] | None) -> str:
    """Membership, not normalisation.

    Case folding and stripping are applied first so ``" property "`` still
    matches, but the *returned* string is always the canonical vocabulary
    member or :data:`OTHER`. The caller's string is never echoed back, which is
    what stops a crafted value from being reflected into a log line.
    """
    if not vocab:
        return OTHER
    if isinstance(value, str):
        candidate = value.strip()
        if candidate in vocab:
            return candidate
        upper = candidate.upper()
        if upper in vocab:
            return upper
        lower = candidate.lower()
        if lower in vocab:
            return lower
    return OTHER


def sanitize(event: str, fields: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The declared fields of ``event``, coerced to their kinds.

    ``None`` when ``event`` is not one of the six. Fields the spec does not
    declare are dropped silently — silently because the alternative is logging
    the rejected field name and value, which is the leak, arriving through the
    error path.

    Every declared field is always present in the output, with its kind's zero
    value if the caller omitted it. A metric whose fields come and go is
    unusable for the counting it exists to support.
    """
    spec = EVENTS.get(event)
    if spec is None:
        return None
    supplied = fields or {}
    out: dict[str, Any] = {}
    for name, (kind, vocab) in spec.items():
        raw = supplied.get(name)
        if kind == KIND_COUNT:
            out[name] = _coerce_count(raw)
        elif kind == KIND_FLAG:
            out[name] = bool(raw)
        else:
            out[name] = _coerce_enum(raw, vocab)
    return out


def emit(event: str, **fields: Any) -> dict[str, Any] | None:
    """Record one telemetry event. Never raises.

    Returns the sanitised payload, or ``None`` if the event was undeclared and
    therefore dropped. Callers ignore the return value; the test suite does not.
    """
    try:
        payload = sanitize(event, fields)
        if payload is None:
            # Warned rather than logged with its fields: the fields of an
            # undeclared event have been through no filter at all, so they are
            # exactly the thing not to write down.
            LOGGER.warning("PRIVATE_TELEMETRY_UNKNOWN_EVENT event=%s", str(event)[:64])
            return None
        LOGGER.info(
            "%s %s", event,
            " ".join(f"{k}={payload[k]}" for k in sorted(payload)),
        )
        return payload
    except Exception:  # noqa: BLE001 — see the module docstring on delivery.
        LOGGER.exception("PRIVATE_TELEMETRY_EMIT_FAILED event=%s", str(event)[:64])
        return None


_SPEC_PROBLEMS = spec_is_sound()
if _SPEC_PROBLEMS:  # pragma: no cover — import-time guard, must never fire.
    LOGGER.error("PRIVATE_TELEMETRY_SPEC_UNSOUND problems=%s", "; ".join(_SPEC_PROBLEMS))


__all__ = [
    "EVENTS",
    "EVENT_FACT_WRITE",
    "EVENT_GRAPH_WRITE",
    "EVENT_CONTEXT_RETRIEVED",
    "EVENT_CONTEXT_DENIED",
    "EVENT_CONFLICT_DETECTED",
    "EVENT_CONFLICT_RESOLVED",
    "EVENT_EVIDENCE_LINKED",
    "EVENT_REVIEW_SWEEP",
    "EVENT_SCHEMA_STATE",
    "FORBIDDEN_FIELDS",
    "OTHER",
    "emit",
    "sanitize",
    "spec_is_sound",
]
