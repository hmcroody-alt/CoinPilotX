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
    "written", "refreshed", "rejected", "created", "existing",
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

# ---------------------------------------------------------------------------
# The six events
# ---------------------------------------------------------------------------
EVENT_FACT_WRITE = "private_office.fact_write"
EVENT_GRAPH_WRITE = "private_office.graph_write"
EVENT_CONTEXT_RETRIEVED = "private_office.context_retrieved"
EVENT_CONTEXT_DENIED = "private_office.context_denied"
EVENT_CONFLICT_DETECTED = "private_office.conflict_detected"
EVENT_SCHEMA_STATE = "private_office.schema_state"

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
        "superseded": (KIND_FLAG, None),
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
    EVENT_SCHEMA_STATE: {
        "state": (KIND_ENUM, SCHEMA_STATE_VOCAB),
        "process": (KIND_ENUM, PROCESS_VOCAB),
        "missing_table_count": (KIND_COUNT, None),
        "added_column_count": (KIND_COUNT, None),
        "cached": (KIND_FLAG, None),
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
    "EVENT_SCHEMA_STATE",
    "FORBIDDEN_FIELDS",
    "OTHER",
    "emit",
    "sanitize",
    "spec_is_sound",
]
