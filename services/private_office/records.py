"""Batch C — the six canonical Private Office record primitives.

What this module is
-------------------
``OBLIGATION``, ``EVENT``, ``DECISION``, ``REQUEST``, ``RISK`` and
``OPPORTUNITY`` are *shared domain primitives*, not six feature tables that
happen to live near each other. Every screen, worker, provider integration and
UNDX capability that wants to say "the member owes something", "something
happened", "a choice is open", "a person was asked for help", "something is
exposed" or "something might be worth looking at" says it here, once, in a form
that is owner-scoped, provenance-aware, auditable and retrieval-ready.

The alternative — which is what happens by default — is that the concierge
queue grows a ``concierge_requests`` table, the insurance screen grows a
``policy_alerts`` table, the briefing worker grows a ``briefing_items`` table,
and three years later "what does this member owe, and by when" is a question no
single query can answer because the answer is spread across seven tables with
four different notions of who owns a row.

So this module follows the discipline the fact store and the graph already
follow:

* one canonical writer per primitive — routes and UNDX call the writer, never
  ``INSERT``;
* ``owner_user_id`` is required on every write and is the first clause of every
  read, so cross-account access is a query that returns nothing rather than a
  check somebody remembered;
* provenance travels with anything derived from another artifact;
* every write leaves a metadata-only audit row and a privacy-safe counter;
* nothing here depends on UI state or on a client's temporary interpretation.

Six tables, one implementation
------------------------------
The six share a core: identity, owner, title, status, lifecycle, provenance,
normalized references to entities and documents, and timestamps. They differ in
a handful of columns each. That is expressed as a spec table (:data:`SPECS`)
driving generated DDL and generated SQL, rather than as six near-copies of the
same 300 lines — because the failure mode of six near-copies is that five of
them get the ``owner_user_id`` clause and the sixth gets it in the ``SELECT``
but not the ``UPDATE``.

It is deliberately *not* an ORM. There is no base class, no descriptor
protocol, no query builder. It is the package's existing style — explicit SQL,
explicit columns, keyword-only writers — with the column list read from a dict
instead of typed out six times.

Derived status is never stored
------------------------------
``DUE_SOON`` and ``OVERDUE`` are functions of ``due_at`` and the current server
time. Storing them would mean an obligation is only overdue if some sweep ran,
and a sweep that fails leaves a store that reports every obligation as ``OPEN``
— healthy-looking and wrong. The stored status is what somebody *decided*
(``OPEN`` / ``RESOLVED`` / ``DISMISSED``); the derived status is what is *true
right now*, computed at read as ``effective_status``.

History is preserved
--------------------
:func:`update_record` moves only status, closure, outcome and assignment. A
change to the substance of a record — the question a decision asks, the terms
of an obligation — goes through :func:`revise_record`, which marks the old row
``SUPERSEDED`` and writes a new ``ACTIVE`` one carrying ``supersedes_id``. The
old question is still readable, which is the whole point: a decision log whose
question is overwritten as the decision evolves is a record of the conclusion
with the reasoning deleted.

No blobs
--------
There is no ``metadata_json`` column on any of these tables, for the same
structural reason ``private_audit_events`` has no ``detail_json``: a field that
exists to hold "a bit of context" ends up holding a policy number. References
are normalized — ids of graph entities and documents, validated to be
id-shaped — and free text is confined to the named, length-capped fields the
primitive actually needs.

Schema ownership
----------------
The DDL lives here rather than in :mod:`services.private_office.schema` and is
applied by :func:`ensure_records_schema`, which follows the same contract as
``ensure_private_schema``: idempotent, never raises, caches only success.
Registering these six into ``schema.TABLES`` is the right long-term home and is
deliberately deferred — ``schema.py`` is being edited by the concurrent Private
Office security mission, and a merge conflict in the one module that decides
whether the database is usable is a worse outcome than a second ensure call.

Route and UNDX wiring are complete
----------------------------------
Both deferrals recorded here have been resolved and this section is kept only
to say so, because a reader who finds a stale ``DEFERRED`` banner in the
canonical writer has no way to tell whether the wiring is missing or the note
is.

The HTTP surface is the Private Office Operations route family in
:mod:`services.private_office_routes` — the typed record views, the status
move, and the attention read — all behind the same entry gate: authentication,
then the ``private_office.operations`` entitlement, then the Private Office
second lock.

The UNDX surface is declared in one place,
:mod:`services.private_office.undx_records_spec`, which now carries
``WIRING_COMPLETE = True``. The flag is not decoration. While it was False the
suite asserted the six capabilities were *absent* from all three authorization
surfaces; now that it is True the same suite asserts they are *present* in all
three, so the flag cannot be flipped without the registration being real, and
the registration cannot be removed without the flag failing. ``DEFERRAL_REASON``
survives in that module as a historical string, not as a live status.

There was never a temporary route and never a second executor table "just for
now", which is why resolving the deferral was three edits in the files that own
registration rather than a migration off a parallel surface.

Every write and every read of these six goes through this module and
``retrieval``, which is enforced statically by
``tests/private_office/test_private_write_boundary.py``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from services.private_office import audit as _audit
from services.private_office import facts as _facts
from services.private_office import model as _model
from services.private_office import telemetry as _telemetry

LOGGER = logging.getLogger("private_office.records")


class PrivateRecordRejected(ValueError):
    """A write that would break an invariant. Never a database failure.

    Distinct from :class:`~services.private_office.schema.PrivateSchemaMissing`
    on purpose: one means "the caller asked for something incoherent" and the
    other means "the store cannot be reached". A caller that cannot tell those
    apart will retry the first one forever.
    """


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------
TYPE_OBLIGATION = "OBLIGATION"
TYPE_EVENT = "EVENT"
TYPE_DECISION = "DECISION"
TYPE_REQUEST = "REQUEST"
TYPE_RISK = "RISK"
TYPE_OPPORTUNITY = "OPPORTUNITY"
TYPE_TASK = "TASK"
TYPE_PROJECT = "PROJECT"

RECORD_TYPES: tuple[str, ...] = (
    TYPE_OBLIGATION, TYPE_EVENT, TYPE_DECISION,
    TYPE_REQUEST, TYPE_RISK, TYPE_OPPORTUNITY,
    TYPE_TASK, TYPE_PROJECT,
)

#: Where a record came from. ``USER`` and ``SYSTEM`` are origins in their own
#: right; the rest are *derivations*, and a derivation without a provenance is
#: refused — see :data:`DERIVED_SOURCES`.
SOURCE_USER = "USER"
SOURCE_SYSTEM = "SYSTEM"
SOURCE_DOCUMENT = "DOCUMENT"
SOURCE_PROVIDER = "PROVIDER"
SOURCE_FACT = "FACT"
SOURCE_GRAPH = "GRAPH"
SOURCE_INFERENCE = "INFERENCE"
SOURCE_IMPORT = "IMPORT"

SOURCE_TYPES: tuple[str, ...] = (
    SOURCE_USER, SOURCE_SYSTEM, SOURCE_DOCUMENT, SOURCE_PROVIDER,
    SOURCE_FACT, SOURCE_GRAPH, SOURCE_INFERENCE, SOURCE_IMPORT,
)

#: Sources that mean "this record was produced from another artifact". The
#: user's Batch C rule — *whenever a risk/opportunity/obligation is derived
#: from another artifact, preserve its origin* — is enforced structurally here:
#: these sources require a ``provenance_type``, so a risk that was inferred
#: cannot be stored as though the member had stated it.
DERIVED_SOURCES: frozenset[str] = frozenset({
    SOURCE_DOCUMENT, SOURCE_PROVIDER, SOURCE_FACT,
    SOURCE_GRAPH, SOURCE_INFERENCE, SOURCE_IMPORT,
})

LIFECYCLE_ACTIVE = _model.LIFECYCLE_ACTIVE
LIFECYCLE_SUPERSEDED = _model.LIFECYCLE_SUPERSEDED

#: Derived time states. Never stored — see the module docstring. They are
#: computed from a deadline column and the server clock every time a row is
#: serialized, which is the only way they can be right: a stored ``OVERDUE``
#: depends on a sweep having run, and a sweep that silently stops leaves a store
#: reporting that everything is still OPEN — healthy-looking and wrong.
DERIVED_DUE_SOON = "DUE_SOON"
DERIVED_OVERDUE = "OVERDUE"

#: The deadline column per type, and *only* where the column is genuinely a
#: deadline. This map is the whole of Stage 5's "type-specific semantics": the
#: tempting shortcut is to treat every date-bearing record as capable of being
#: overdue, and three of the six would be wrong.
#:
#: EVENT is the clearest case. It has ``occurred_at``, which is when something
#: happened in the member's life. Every domain event ever recorded has a past
#: ``occurred_at``, so a naive rule would mark the member's entire history
#: overdue and drown every real obligation in it.
DEADLINE_FIELDS: dict[str, str] = {
    TYPE_OBLIGATION: "due_at",
    TYPE_DECISION: "deadline_at",
    TYPE_REQUEST: "deadline_at",
}

#: Why the other three have no derived time state. Kept as text rather than as
#: an implicit absence so the next reader does not "fix" the omission.
NO_DEADLINE_REASON: dict[str, str] = {
    TYPE_EVENT: "occurred_at records when something happened, not when it is due",
    TYPE_RISK: "a risk carries severity and coverage, not a date it must be done by",
    TYPE_OPPORTUNITY: "no expiry column exists on private_opportunities",
}

#: How close counts as soon, per type. Not one shared window, because "soon"
#: is a claim about the member's runway and the runway differs: a tax filing
#: fourteen days out is worth surfacing, a concierge request fourteen days out
#: is not urgent and would push genuinely urgent rows off the top of the queue.
#:
#: The obligation window is 14 days because that is what it already was. It is
#: restated here rather than changed, so generalizing the mechanism does not
#: quietly move a threshold the existing behaviour depends on.
DUE_SOON_WINDOWS: dict[str, timedelta] = {
    TYPE_OBLIGATION: timedelta(days=14),
    TYPE_DECISION: timedelta(days=7),
    TYPE_REQUEST: timedelta(days=3),
}

#: Retained under its original name because callers and tests reference it. It
#: is the obligation window, which is the one this constant always meant.
DUE_SOON_WINDOW = DUE_SOON_WINDOWS[TYPE_OBLIGATION]

SEVERITY_UNKNOWN = "UNKNOWN"
SEVERITIES: tuple[str, ...] = (
    SEVERITY_UNKNOWN, "INFO", "LOW", "MODERATE", "HIGH", "CRITICAL",
)

#: Whether anyone qualified has actually looked. ``UNKNOWN`` is the default and
#: there is deliberately no value meaning "fine". The user's rule — *do not
#: automatically call something safe because no provider data exists* — is a
#: rule about what the absence of data is allowed to mean, and the only way to
#: hold it is to make the truthful state the one you get for free.
COVERAGE_UNKNOWN = "UNKNOWN"
COVERAGE_PROVIDER_REQUIRED = "PROVIDER_REQUIRED"
COVERAGE_PROVIDER_REVIEWED = "PROVIDER_REVIEWED"
COVERAGE_SELF_ASSERTED = "SELF_ASSERTED"

COVERAGE_STATES: tuple[str, ...] = (
    COVERAGE_UNKNOWN, COVERAGE_PROVIDER_REQUIRED,
    COVERAGE_PROVIDER_REVIEWED, COVERAGE_SELF_ASSERTED,
)

PRIORITIES: tuple[str, ...] = ("LOW", "NORMAL", "HIGH", "URGENT")
CONFIDENTIALITIES: tuple[str, ...] = ("STANDARD", "SENSITIVE", "RESTRICTED")

#: A type discriminator — ``INSURANCE_PREMIUM``, ``POLICY_RENEWAL_NOTICE`` — is
#: the query surface, exactly as ``fact_type`` is in the fact store, so it gets
#: the same treatment: validated as written and never case-folded, because
#: folding would accept ``PolicyRenewal`` and store a *different* type from
#: ``POLICY_RENEWAL``, created by the store rather than by the caller.
_TYPE_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,47}$")

#: References are ids, not values. Same rule and nearly the same expression as
#: ``audit.safe_object_id``: anything with a space, a currency symbol or an @ is
#: a value wearing an id's clothes.
_REF_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")

MAX_TITLE = 200
MAX_SUMMARY = 2000
MAX_QUESTION = 500
MAX_ASSUMPTIONS = 2000
MAX_OUTCOME = 1000
MAX_REFS = 25
MAX_SOURCE_REF = 128
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

STATUS_CREATED = "created"
STATUS_EXISTING = "existing"
STATUS_UPDATED = "updated"
STATUS_REVISED = "revised"
#: The caller asked for the status the record already has, and asked for nothing
#: else. Distinct from ``updated`` for the same reason ``existing`` is distinct
#: from ``created``: an idempotent caller re-sending CANCELED on a canceled
#: request has not failed, but counting it as an update would make a queue look
#: like it was being worked when the same row is being restated.
STATUS_UNCHANGED = "unchanged"


# ---------------------------------------------------------------------------
# The spec table
# ---------------------------------------------------------------------------
# Every entry answers the same questions for one primitive: what is it called
# in the database, which statuses may it hold, which of those close it, what is
# the closure timestamp called on the wire, what extra columns does it need,
# and which fields make two of them the same record.
#
# `closed_as` deserves a note. There is exactly one stored column, `closed_at`,
# for all six. Six columns named `resolved_at` / `decided_at` / `completed_at`
# would be six code paths for "when did this stop being open", and the sixth
# would be the one nobody updated. The per-type name is a projection applied at
# serialization, where a rename is presentation and cannot desynchronise.
SPECS: dict[str, dict] = {
    TYPE_OBLIGATION: {
        "table": "private_obligations",
        "statuses": ("OPEN", "RESOLVED", "DISMISSED"),
        "default_status": "OPEN",
        "closing": ("RESOLVED", "DISMISSED"),
        "closed_as": "resolved_at",
        "summary_as": "summary",
        "audit_object": "OBLIGATION",
        "extra": (
            ("obligation_type", "TEXT NOT NULL DEFAULT ''", "token", True),
            ("due_at", "TEXT", "timestamp", False),
            ("amount_text", "TEXT NOT NULL DEFAULT ''", "internal", False),
            ("amount_number", "REAL", "internal", False),
            ("currency", "TEXT NOT NULL DEFAULT ''", "currency", False),
            ("priority", "TEXT NOT NULL DEFAULT 'NORMAL'", "enum", False),
        ),
        "required": ("title", "obligation_type"),
        "identity": ("obligation_type", "title", "due_at"),
        "indexes": ("due_at",),
        "enums": {"priority": PRIORITIES},
        "transitions": {
            "OPEN": ("RESOLVED", "DISMISSED"),
            "RESOLVED": ("OPEN", "DISMISSED"),
            "DISMISSED": ("OPEN", "RESOLVED"),
        },
    },
    TYPE_EVENT: {
        # `private_domain_events`, not `private_events`. The user's Batch C note
        # — *domain event != private_audit_events* — is a distinction worth
        # holding in the table name itself, because the two are one letter
        # apart in conversation and are opposite in kind: this one is the
        # member's own history of their affairs, that one is the access log
        # over it.
        "table": "private_domain_events",
        "statuses": ("RECORDED",),
        "default_status": "RECORDED",
        "closing": (),
        "closed_as": "",
        "summary_as": "summary",
        "audit_object": "DOMAIN_EVENT",
        "extra": (
            ("event_type", "TEXT NOT NULL DEFAULT ''", "token", True),
            ("occurred_at", "TEXT NOT NULL DEFAULT ''", "timestamp", False),
        ),
        "required": ("event_type",),
        "identity": ("event_type", "occurred_at", "title"),
        "indexes": ("occurred_at",),
        # An event is a statement that something happened. It has no lifecycle
        # to speak of, so its transition matrix is empty: RECORDED goes nowhere.
        "transitions": {"RECORDED": ()},
    },
    TYPE_DECISION: {
        "table": "private_decisions",
        "statuses": ("OPEN", "UNDER_REVIEW", "DECIDED", "ABANDONED"),
        "default_status": "OPEN",
        "closing": ("DECIDED", "ABANDONED"),
        "closed_as": "decided_at",
        "summary_as": "summary",
        "audit_object": "DECISION",
        "extra": (
            ("question", "TEXT NOT NULL DEFAULT ''", "text", True),
            ("assumptions", "TEXT NOT NULL DEFAULT ''", "text", False),
            ("deadline_at", "TEXT", "timestamp", False),
            ("outcome", "TEXT NOT NULL DEFAULT ''", "text", False),
            ("priority", "TEXT NOT NULL DEFAULT 'NORMAL'", "enum", False),
        ),
        "required": ("question",),
        # Identity is the question alone. Two rows asking the same thing are one
        # decision seen twice; the summary and assumptions around it are what
        # revision is for.
        "identity": ("question",),
        "indexes": ("deadline_at",),
        "enums": {"priority": PRIORITIES},
        "transitions": {
            "OPEN": ("UNDER_REVIEW", "DECIDED", "ABANDONED"),
            "UNDER_REVIEW": ("OPEN", "DECIDED", "ABANDONED"),
            "DECIDED": ("OPEN", "ABANDONED"),
            "ABANDONED": ("OPEN", "DECIDED"),
        },
    },
    TYPE_REQUEST: {
        "table": "private_requests",
        "statuses": (
            "OPEN", "IN_PROGRESS", "WAITING_ON_USER",
            "WAITING_ON_PROVIDER", "COMPLETED", "CANCELED",
        ),
        "default_status": "OPEN",
        "closing": ("COMPLETED", "CANCELED"),
        "closed_as": "completed_at",
        # A concierge request's long field is its description. One stored
        # column, renamed on the way out.
        "summary_as": "description",
        "audit_object": "REQUEST",
        "extra": (
            ("category", "TEXT NOT NULL DEFAULT ''", "token", True),
            ("priority", "TEXT NOT NULL DEFAULT 'NORMAL'", "enum", False),
            ("confidentiality", "TEXT NOT NULL DEFAULT 'STANDARD'", "enum", False),
            ("deadline_at", "TEXT", "timestamp", False),
            ("assigned_provider_id", "TEXT NOT NULL DEFAULT ''", "ref", False),
        ),
        "required": ("title", "category"),
        "identity": ("category", "title"),
        "indexes": ("deadline_at",),
        "enums": {"priority": PRIORITIES, "confidentiality": CONFIDENTIALITIES},
        "transitions": {
            "OPEN": ("IN_PROGRESS", "WAITING_ON_USER", "WAITING_ON_PROVIDER", "COMPLETED", "CANCELED"),
            "IN_PROGRESS": ("OPEN", "WAITING_ON_USER", "WAITING_ON_PROVIDER", "COMPLETED", "CANCELED"),
            "WAITING_ON_USER": ("OPEN", "IN_PROGRESS", "WAITING_ON_PROVIDER", "COMPLETED", "CANCELED"),
            "WAITING_ON_PROVIDER": ("OPEN", "IN_PROGRESS", "WAITING_ON_USER", "COMPLETED", "CANCELED"),
            "COMPLETED": ("OPEN", "CANCELED"),
            "CANCELED": ("OPEN", "COMPLETED"),
        },
    },
    TYPE_RISK: {
        "table": "private_risks",
        "statuses": ("OPEN", "MONITORING", "MITIGATED", "ACCEPTED", "RESOLVED", "DISMISSED"),
        "default_status": "OPEN",
        "closing": ("RESOLVED", "DISMISSED"),
        "closed_as": "resolved_at",
        "summary_as": "summary",
        "audit_object": "RISK",
        "extra": (
            ("risk_type", "TEXT NOT NULL DEFAULT ''", "token", True),
            ("severity", "TEXT NOT NULL DEFAULT 'UNKNOWN'", "enum", False),
            ("coverage_state", "TEXT NOT NULL DEFAULT 'UNKNOWN'", "enum", False),
            ("review_required", "INTEGER NOT NULL DEFAULT 0", "flag", False),
            ("priority", "TEXT NOT NULL DEFAULT 'NORMAL'", "enum", False),
        ),
        "required": ("risk_type", "summary"),
        "identity": ("risk_type", "summary"),
        "indexes": ("severity",),
        "enums": {
            "severity": SEVERITIES,
            "coverage_state": COVERAGE_STATES,
            "priority": PRIORITIES,
        },
        "transitions": {
            "OPEN": ("MONITORING", "MITIGATED", "ACCEPTED", "RESOLVED", "DISMISSED"),
            "MONITORING": ("OPEN", "MITIGATED", "ACCEPTED", "RESOLVED", "DISMISSED"),
            "MITIGATED": ("OPEN", "MONITORING", "ACCEPTED", "RESOLVED", "DISMISSED"),
            "ACCEPTED": ("OPEN", "MONITORING", "MITIGATED", "RESOLVED", "DISMISSED"),
            "RESOLVED": ("OPEN", "DISMISSED"),
            "DISMISSED": ("OPEN", "RESOLVED"),
        },
    },
    TYPE_OPPORTUNITY: {
        "table": "private_opportunities",
        "statuses": ("NEW", "REVIEWING", "INTERESTED", "PASSED", "CLOSED"),
        "default_status": "NEW",
        "closing": ("PASSED", "CLOSED"),
        "closed_as": "closed_at_projected",
        "summary_as": "summary",
        "audit_object": "OPPORTUNITY",
        "extra": (
            ("opportunity_type", "TEXT NOT NULL DEFAULT ''", "token", True),
            # Optional, and only ever what a named source supplied. There is no
            # column here for a recommendation, a rating or a suggested action:
            # this primitive records that something exists and may be relevant,
            # and stops there. Anything stronger is investment advice, which
            # this platform does not autonomously give.
            ("relevance_score", "REAL", "score", False),
            ("priority", "TEXT NOT NULL DEFAULT 'NORMAL'", "enum", False),
        ),
        "required": ("title", "opportunity_type"),
        "identity": ("opportunity_type", "title"),
        "indexes": (),
        "enums": {"priority": PRIORITIES},
        "transitions": {
            "NEW": ("REVIEWING", "INTERESTED", "PASSED", "CLOSED"),
            "REVIEWING": ("NEW", "INTERESTED", "PASSED", "CLOSED"),
            "INTERESTED": ("NEW", "REVIEWING", "PASSED", "CLOSED"),
            "PASSED": ("NEW", "REVIEWING", "CLOSED"),
            "CLOSED": ("NEW", "REVIEWING", "PASSED"),
        },
    },
    TYPE_TASK: {
        "table": "private_tasks",
        "statuses": (
            "DRAFT", "OPEN", "PLANNED", "IN_PROGRESS", "WAITING", "BLOCKED",
            "AWAITING_APPROVAL", "COMPLETED", "CANCELLED", "DEFERRED", "EXPIRED",
        ),
        "default_status": "OPEN",
        "closing": ("COMPLETED", "CANCELLED", "EXPIRED"),
        "closed_as": "completed_at",
        "summary_as": "summary",
        "audit_object": "TASK",
        "extra": (
            ("task_type", "TEXT NOT NULL DEFAULT ''", "token", False),
            ("priority", "TEXT NOT NULL DEFAULT 'NORMAL'", "enum", False),
            ("due_at", "TEXT", "timestamp", False),
            # A task may belong to a project; the reference is an id, validated
            # like every other id, and dangling is a diagnostic finding, not a
            # write-time error — the project may legitimately be created after
            # the tasks it will collect.
            ("project_ref", "TEXT NOT NULL DEFAULT ''", "ref", False),
        ),
        "required": ("title",),
        "identity": ("task_type", "title", "due_at"),
        "indexes": ("due_at",),
        "enums": {"priority": PRIORITIES},
        "transitions": {
            "DRAFT": ("OPEN", "PLANNED", "CANCELLED"),
            "OPEN": ("PLANNED", "IN_PROGRESS", "WAITING", "BLOCKED",
                     "AWAITING_APPROVAL", "COMPLETED", "CANCELLED",
                     "DEFERRED", "EXPIRED"),
            "PLANNED": ("OPEN", "IN_PROGRESS", "WAITING", "BLOCKED",
                        "AWAITING_APPROVAL", "COMPLETED", "CANCELLED",
                        "DEFERRED", "EXPIRED"),
            "IN_PROGRESS": ("OPEN", "PLANNED", "WAITING", "BLOCKED",
                            "AWAITING_APPROVAL", "COMPLETED", "CANCELLED",
                            "DEFERRED", "EXPIRED"),
            "WAITING": ("OPEN", "PLANNED", "IN_PROGRESS", "BLOCKED",
                        "AWAITING_APPROVAL", "COMPLETED", "CANCELLED",
                        "DEFERRED", "EXPIRED"),
            "BLOCKED": ("OPEN", "PLANNED", "IN_PROGRESS", "WAITING",
                        "AWAITING_APPROVAL", "COMPLETED", "CANCELLED",
                        "DEFERRED", "EXPIRED"),
            "AWAITING_APPROVAL": ("OPEN", "PLANNED", "IN_PROGRESS", "WAITING",
                                  "BLOCKED", "COMPLETED", "CANCELLED",
                                  "DEFERRED", "EXPIRED"),
            "DEFERRED": ("OPEN", "PLANNED", "IN_PROGRESS", "CANCELLED", "EXPIRED"),
            "COMPLETED": ("OPEN", "CANCELLED"),
            "CANCELLED": ("OPEN", "COMPLETED"),
            "EXPIRED": ("OPEN", "CANCELLED"),
        },
    },
    TYPE_PROJECT: {
        "table": "private_projects",
        "statuses": (
            "DRAFT", "PLANNED", "OPEN", "IN_PROGRESS", "WAITING", "BLOCKED",
            "AWAITING_APPROVAL", "COMPLETED", "CANCELLED", "DEFERRED",
        ),
        "default_status": "OPEN",
        "closing": ("COMPLETED", "CANCELLED"),
        "closed_as": "completed_at",
        "summary_as": "summary",
        "audit_object": "PROJECT",
        "extra": (
            ("project_type", "TEXT NOT NULL DEFAULT ''", "token", False),
            ("priority", "TEXT NOT NULL DEFAULT 'NORMAL'", "enum", False),
            ("due_at", "TEXT", "timestamp", False),
            ("outcome", "TEXT NOT NULL DEFAULT ''", "text", False),
        ),
        "required": ("title",),
        "identity": ("project_type", "title"),
        "indexes": ("due_at",),
        "enums": {"priority": PRIORITIES},
        "transitions": {
            "DRAFT": ("PLANNED", "OPEN", "CANCELLED"),
            "PLANNED": ("DRAFT", "OPEN", "IN_PROGRESS", "WAITING", "BLOCKED",
                        "AWAITING_APPROVAL", "COMPLETED", "CANCELLED", "DEFERRED"),
            "OPEN": ("PLANNED", "IN_PROGRESS", "WAITING", "BLOCKED",
                     "AWAITING_APPROVAL", "COMPLETED", "CANCELLED", "DEFERRED"),
            "IN_PROGRESS": ("PLANNED", "OPEN", "WAITING", "BLOCKED",
                            "AWAITING_APPROVAL", "COMPLETED", "CANCELLED", "DEFERRED"),
            "WAITING": ("PLANNED", "OPEN", "IN_PROGRESS", "BLOCKED",
                        "AWAITING_APPROVAL", "COMPLETED", "CANCELLED", "DEFERRED"),
            "BLOCKED": ("PLANNED", "OPEN", "IN_PROGRESS", "WAITING",
                        "AWAITING_APPROVAL", "COMPLETED", "CANCELLED", "DEFERRED"),
            "AWAITING_APPROVAL": ("PLANNED", "OPEN", "IN_PROGRESS", "WAITING",
                                  "BLOCKED", "COMPLETED", "CANCELLED", "DEFERRED"),
            "DEFERRED": ("PLANNED", "OPEN", "IN_PROGRESS", "CANCELLED"),
            "COMPLETED": ("OPEN", "CANCELLED"),
            "CANCELLED": ("OPEN", "COMPLETED"),
        },
    },
}

#: Columns every primitive has. Order matters only for readability; the SQL is
#: generated from names.
CORE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("owner_user_id", "INTEGER NOT NULL"),
    ("record_key", "TEXT NOT NULL"),
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("summary", "TEXT NOT NULL DEFAULT ''"),
    ("status", "TEXT NOT NULL"),
    ("lifecycle_state", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
    ("supersedes_id", "INTEGER NOT NULL DEFAULT 0"),
    ("revision", "INTEGER NOT NULL DEFAULT 1"),
    ("domain", "TEXT NOT NULL DEFAULT 'GENERAL'"),
    ("sensitivity", "TEXT NOT NULL DEFAULT 'CONFIDENTIAL'"),
    ("provenance_state", "TEXT NOT NULL DEFAULT ''"),
    ("provenance_ref", "TEXT NOT NULL DEFAULT ''"),
    ("source_type", "TEXT NOT NULL DEFAULT 'USER'"),
    ("source_ref", "TEXT NOT NULL DEFAULT ''"),
    ("related_entity_ids", "TEXT NOT NULL DEFAULT ''"),
    ("related_document_ids", "TEXT NOT NULL DEFAULT ''"),
    ("created_at", "TEXT NOT NULL"),
    ("updated_at", "TEXT NOT NULL"),
    ("closed_at", "TEXT"),
)


def private_table_for(record_type: str) -> str:
    """The table backing a primitive.

    The name is deliberately distinctive. Every write statement in this module
    interpolates this function rather than ``spec["table"]`` so that the static
    write-boundary guard, which matches table names and table-name tokens inside
    string literals, can see the writes for what they are. ``spec["table"]``
    contains none of the tokens the guard looks for, so a guard run over a
    module written that way would pass while protecting nothing — and a guard
    that cannot fail is evidence of nothing. The ``private_`` prefix keeps the
    token from colliding with ordinary English inside unrelated identifiers.
    """
    return SPECS[record_type]["table"]


def _spec(record_type: object) -> dict:
    kind = str(record_type or "").strip().upper()
    spec = SPECS.get(kind)
    if spec is None:
        raise PrivateRecordRejected(f"unknown record_type: {record_type!r}")
    return spec


def _columns(spec: dict) -> tuple[tuple[str, str], ...]:
    return CORE_COLUMNS + tuple((name, ddl) for name, ddl, _kind, _req in spec["extra"])


def _column_names(spec: dict) -> tuple[str, ...]:
    return tuple(name for name, _ddl in _columns(spec))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Written once in SQLite dialect: `services.db` rewrites
# `INTEGER PRIMARY KEY AUTOINCREMENT` to `SERIAL PRIMARY KEY` for PostgreSQL,
# which is why the DDL below can be literal and still be portable. Nothing here
# relies on `INSERT OR IGNORE`, which means two different things on the two
# engines; dedupe is a decision this module makes explicitly, in
# `create_record`, where it can be read.
def table_ddl(record_type: str) -> str:
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    body = ",\n    ".join(f"{name} {ddl}" for name, ddl in _columns(spec))
    return (
        f"CREATE TABLE IF NOT EXISTS {private_table_for(kind)} (\n"
        f"    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        f"    {body},\n"
        f"    UNIQUE(owner_user_id, record_key)\n"
        f")"
    )


def index_ddl(record_type: str) -> tuple[str, ...]:
    """Indexes for one primitive. Every one of them leads with ``owner_user_id``.

    Not a stylistic preference. Owner scope is the first clause of every query
    this module issues, so an index that does not start there cannot serve
    them, and the query that would have used it degrades to a scan of every
    member's rows — which is both the performance problem and, on a shared
    table, the shape of query you least want to be cheap.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    short = spec["table"].replace("private_", "")
    statements = [
        f"CREATE INDEX IF NOT EXISTS idx_{short}_owner_status "
        f"ON {private_table_for(kind)}(owner_user_id, lifecycle_state, status)",
        f"CREATE INDEX IF NOT EXISTS idx_{short}_owner_created "
        f"ON {private_table_for(kind)}(owner_user_id, created_at)",
    ]
    for column in spec["indexes"]:
        statements.append(
            f"CREATE INDEX IF NOT EXISTS idx_{short}_owner_{column} "
            f"ON {private_table_for(kind)}(owner_user_id, {column})"
        )
    return tuple(statements)


#: The six primitive tables. Kept separate from :data:`TABLES` because several
#: callers iterate this in step with :data:`RECORD_TYPES` and would break on a
#: seventh entry that has no record type.
RECORD_TABLES: tuple[str, ...] = tuple(SPECS[k]["table"] for k in RECORD_TYPES)


# ---------------------------------------------------------------------------
# Dependencies (Stage G-3)
# ---------------------------------------------------------------------------
# A seventh table, and the only one in this package that holds a relationship
# between two records rather than a record.
#
# Why not the existing graph. `private_graph_edges` already stores edges, and
# reusing it was the first thing considered and the right thing to reject.
# That graph is an *estate* graph: its nodes are PERSON, BUSINESS, PROPERTY,
# ASSET, LIABILITY and its relations are OWNS / ADVISED_BY / COVERED_BY. Its
# module docstring draws the line it is built around — the graph holds
# relationships between entities, and admitting a second kind of node would
# make traversal depth meaningless, because two hops could then be one estate
# relation and one workflow link. "This filing is blocked by that decision" is
# not a statement about the member's estate, and putting it there would buy one
# fewer table at the cost of the invariant that makes the estate graph
# traversable at all.
#
# Direction. `source DEPENDS_ON target` reads "source is waiting on target".
# The dependent is always the source, so "what is blocking me" is a query on
# `source_*` and "what am I holding up" is a query on `target_*`. Stating it
# once here is cheaper than every reader inferring it from a column name, and
# an inverted edge is invisible in the data — it simply blocks the wrong record.
# Naming, and why this table breaks the module's own resolver convention. The
# six primitives are reached through `private_table_for` because the table is
# chosen at runtime from a record type, and the static write-boundary guard
# needs *some* token it can match inside the f-string. This table is fixed, so
# every statement below writes `private_record_links` literally. That is the
# more visible of the two options, not a shortcut: the guard matches the real
# table name in the real statement, with no indirection to follow and no second
# entry in its constants list that could be removed while the writes stayed.
# `RECORD_LINKS_TABLE` exists for the places that need the name as a value —
# `TABLES`, the ensure step's column probe — and `test_record_dependencies`
# asserts the constant and the DDL cannot drift apart.
RECORD_LINKS_TABLE = "private_record_links"

LINK_DEPENDS_ON = "DEPENDS_ON"
LINK_TYPES: tuple[str, ...] = (LINK_DEPENDS_ON,)

#: Ceilings on a dependency walk. Cycle detection traverses user-controlled
#: data, so it needs a bound that does not depend on the data being sane: a
#: chain built before this code existed, or one produced by a future writer with
#: a defect, must make the walk stop rather than hang the request holding a
#: database connection.
MAX_DEPENDENCY_DEPTH = 32
MAX_DEPENDENCY_VISITS = 512

#: How many blockers one record may declare. A bound rather than none, for the
#: same reason every list in this module has one.
MAX_DEPENDENCIES_PER_RECORD = 64


def linkable_types() -> tuple[str, ...]:
    """The types that may take part in a dependency.

    Derived from ``spec["closing"]`` rather than enumerated, so the rule cannot
    drift from the vocabulary it depends on. A dependency means "this cannot
    proceed until that has ended", which requires the far end to be *capable* of
    ending. EVENT is the one type with no closing status — it has exactly one
    status, RECORDED — so an edge pointing at an event would never be satisfied
    and would mark its dependent blocked forever, while an edge *from* an event
    would claim something that already happened is still waiting.

    If EVENT ever gains a closing status this function admits it automatically,
    which is the point of deriving it.
    """
    return tuple(k for k in RECORD_TYPES if SPECS[k]["closing"])


#: Why a type is excluded, kept as text so the omission reads as a decision.
NO_LINK_REASON: dict[str, str] = {
    TYPE_EVENT: "an event has no closing status, so a dependency on one could "
                "never be satisfied and a dependency from one would say "
                "something already recorded is still waiting",
}


def links_table_ddl() -> str:
    """DDL for the dependency table.

    The UNIQUE constraint carries a real invariant rather than tidiness: it is
    what makes "A depends on B" idempotent at the storage layer, so a retried
    request cannot accumulate duplicate edges that would then each have to be
    removed separately before the dependent unblocks.
    """
    return (
        f"CREATE TABLE IF NOT EXISTS private_record_links (\n"
        f"    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        f"    owner_user_id INTEGER NOT NULL,\n"
        f"    link_type TEXT NOT NULL DEFAULT '{LINK_DEPENDS_ON}',\n"
        f"    source_type TEXT NOT NULL,\n"
        f"    source_id INTEGER NOT NULL,\n"
        f"    target_type TEXT NOT NULL,\n"
        f"    target_id INTEGER NOT NULL,\n"
        f"    note TEXT NOT NULL DEFAULT '',\n"
        f"    created_at TEXT NOT NULL,\n"
        f"    updated_at TEXT NOT NULL,\n"
        f"    UNIQUE(owner_user_id, link_type, source_type, source_id,"
        f" target_type, target_id)\n"
        f")"
    )


#: Column names the ensure step verifies. Restated rather than parsed out of the
#: DDL above, so a column silently dropped from the DDL is a missing-schema
#: report instead of an ensure that checks nothing.
LINK_COLUMNS: tuple[str, ...] = (
    "owner_user_id", "link_type", "source_type", "source_id",
    "target_type", "target_id", "note", "created_at", "updated_at",
)


def links_index_ddl() -> tuple[str, ...]:
    """Both directions, both led by ``owner_user_id``.

    Two indexes rather than one because both directions are hot: resolving
    "what blocks this" walks the source side and cycle detection walks the
    target side, and the cycle walk runs inside the write path where a scan of
    every member's edges would be paid on every link.
    """
    return (
        "CREATE INDEX IF NOT EXISTS idx_record_links_owner_source "
        "ON private_record_links(owner_user_id, source_type, source_id)",
        "CREATE INDEX IF NOT EXISTS idx_record_links_owner_target "
        "ON private_record_links(owner_user_id, target_type, target_id)",
    )


#: Every table this module owns, primitives and dependency edges together. This
#: is the collision and ownership surface — the write-boundary guard and the
#: cross-batch coexistence check both read it, so a table missing from here is a
#: table nothing is defending.
TABLES: tuple[str, ...] = RECORD_TABLES + (RECORD_LINKS_TABLE,)

_SCHEMA_READY = False


def reset_records_schema_cache() -> None:
    """Forget the success cache. For tests, and for anyone who dropped a table."""
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_records_schema(cur, *, force: bool = False) -> dict:
    """Create the record tables and their indexes, and evolve them. Never raises.

    Same three-outcome contract as ``schema.ensure_private_schema`` — ``ready``,
    ``missing``, ``error`` — and the same rule about caching: only success is
    remembered, so a database that heals is noticed without a restart. A failed
    ensure that were cached would turn a transient lock into an outage lasting
    until somebody redeployed.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return {"status": "ready", "tables": list(TABLES), "missing": [], "error": None, "cached": True}

    from services import db as db_module

    missing: list[str] = []
    for record_type in RECORD_TYPES:
        spec = SPECS[record_type]
        try:
            cur.execute(table_ddl(record_type))
        except Exception as exc:
            # Not fatal on its own: the overwhelmingly common case is that the
            # table already exists and this is a no-op. The column check below
            # is what decides whether the schema is usable.
            LOGGER.warning(
                "PRIVATE_RECORDS_TABLE_DDL_FAILED table=%s error=%s", spec["table"], exc)
        for statement in index_ddl(record_type):
            try:
                cur.execute(statement)
            except Exception as exc:
                # A missing index makes a read slow; a missing column makes it
                # impossible. Only the second one blocks.
                LOGGER.warning("PRIVATE_RECORDS_INDEX_FAILED error=%s", exc)

    try:
        cur.execute(links_table_ddl())
    except Exception as exc:
        LOGGER.warning(
            "PRIVATE_RECORDS_TABLE_DDL_FAILED table=%s error=%s",
            RECORD_LINKS_TABLE, exc)
    for statement in links_index_ddl():
        try:
            cur.execute(statement)
        except Exception as exc:
            LOGGER.warning("PRIVATE_RECORDS_INDEX_FAILED error=%s", exc)

    for record_type in RECORD_TYPES:
        spec = SPECS[record_type]
        try:
            present = db_module.get_table_columns(cur, spec["table"])
        except Exception as exc:
            LOGGER.exception("PRIVATE_RECORDS_ENSURE_FAILED table=%s", spec["table"])
            return {"status": "error", "tables": [], "missing": [],
                    "error": f"{spec['table']}: {str(exc)[:400]}", "cached": False}
        absent = [name for name in _column_names(spec) if name not in present]
        if absent and present:
            # The table exists but predates a column the spec now declares.
            # Evolve it in place: `ADD COLUMN` with a constant default is the
            # one ALTER both engines accept identically, and every column this
            # module declares carries one. Without this, adding a column to a
            # spec would turn every existing deployment's ensure into
            # "missing", and `require_records_schema` would turn that into an
            # outage on the first write after deploy.
            ddl_by_name = dict(_columns(spec))
            for name in absent:
                try:
                    cur.execute(
                        f"ALTER TABLE {spec['table']} ADD COLUMN {name} {ddl_by_name[name]}")
                    LOGGER.info(
                        "PRIVATE_RECORDS_COLUMN_ADDED table=%s column=%s",
                        spec["table"], name)
                except Exception as exc:
                    # The re-introspection below is the judge; a failure here
                    # (concurrent worker won the race, engine quirk) is only
                    # fatal if the column is still absent afterwards.
                    LOGGER.warning(
                        "PRIVATE_RECORDS_COLUMN_ADD_FAILED table=%s column=%s error=%s",
                        spec["table"], name, exc)
            try:
                present = db_module.get_table_columns(cur, spec["table"])
            except Exception as exc:
                LOGGER.exception("PRIVATE_RECORDS_ENSURE_FAILED table=%s", spec["table"])
                return {"status": "error", "tables": [], "missing": [],
                        "error": f"{spec['table']}: {str(exc)[:400]}", "cached": False}
            absent = [name for name in _column_names(spec) if name not in present]
        if absent or not present:
            missing.append(f"{spec['table']}:{','.join(absent) or 'absent'}")

    # The dependency table is verified on the same terms as the six. It is
    # reported as missing rather than skipped, because a link writer running
    # against an absent table would refuse every dependency as though the member
    # had none — which reads on screen as "nothing is blocked".
    try:
        present = db_module.get_table_columns(cur, RECORD_LINKS_TABLE)
    except Exception as exc:
        LOGGER.exception(
            "PRIVATE_RECORDS_ENSURE_FAILED table=%s", RECORD_LINKS_TABLE)
        return {"status": "error", "tables": [], "missing": [],
                "error": f"private_record_links: {str(exc)[:400]}",
                "cached": False}
    absent = [name for name in LINK_COLUMNS if name not in present]
    if absent or not present:
        missing.append(f"private_record_links:{','.join(absent) or 'absent'}")

    if missing:
        LOGGER.error("PRIVATE_RECORDS_SCHEMA_MISSING tables=%s", ";".join(missing))
        return {"status": "missing", "tables": list(TABLES), "missing": missing,
                "error": None, "cached": False}

    _SCHEMA_READY = True
    LOGGER.info("PRIVATE_RECORDS_SCHEMA_READY tables=%s", ",".join(TABLES))
    return {"status": "ready", "tables": list(TABLES), "missing": [],
            "error": None, "cached": False}


def require_records_schema(cur, *, force: bool = False) -> dict:
    """:func:`ensure_records_schema`, but raise when the result is unusable.

    The writers and readers call this. They have no honest degraded answer:
    returning ``[]`` from a store that could not be reached would make "you owe
    nothing" and "we could not look" the same response, which is the one thing
    an obligations list must never do.
    """
    result = ensure_records_schema(cur, force=force)
    if result["status"] == "ready":
        return result
    raise PrivateRecordRejected(
        f"records schema unusable: {result['status']} {result.get('error') or ''}".strip()
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _iso(value: object, *, default: str | None = None) -> str | None:
    """An ISO-8601 string, or ``default``.

    Deliberately lenient about the input's shape and strict about the output's:
    a datetime, a date, or a string that parses. A string that does not parse
    is dropped rather than stored, because an unparseable ``due_at`` would make
    an obligation permanently not-yet-due — invisible in exactly the list it
    exists to appear in.
    """
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    text = str(value).strip()
    if not text:
        return default
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        try:
            datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return default
        return text[:10]
    return text


def _token(value: object, field: str, *, required: bool) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise PrivateRecordRejected(f"{field} is required")
        return ""
    if not _TYPE_TOKEN_RE.match(text):
        raise PrivateRecordRejected(
            f"{field} must match {_TYPE_TOKEN_RE.pattern}: {value!r}")
    return text


def safe_ref(value: object) -> str:
    """An id-shaped string, or ``""``.

    Empty rather than truncated, for the same reason ``audit.safe_object_id``
    returns empty: half an identifier that came from a value is still part of a
    value, and it looks sanitised.
    """
    text = str(value if value is not None else "").strip()
    return text if _REF_RE.match(text) else ""


def normalize_refs(value: object) -> str:
    """A canonical, deduped, sorted, comma-joined list of id-shaped references.

    Stored as text rather than as a child table on purpose: these are pointers
    for retrieval to follow, always read whole and never joined on, and a
    junction table per primitive would be six more tables to owner-scope. What
    the text column must not become is a blob, which is why every element goes
    through :func:`safe_ref` and anything that is not id-shaped is dropped.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, (str, bytes)):
        items = str(value).split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        items = [value]
    refs = {safe_ref(item) for item in items}
    refs.discard("")
    return ",".join(sorted(refs)[:MAX_REFS])


def _text(value: object, cap: int) -> str:
    return str(value if value is not None else "").strip()[:cap]


def _enum(value: object, allowed: tuple[str, ...], field: str, default: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return default
    if text not in allowed:
        raise PrivateRecordRejected(
            f"{field} must be one of {', '.join(allowed)}: {value!r}")
    return text


_DDL_DEFAULT_RE = re.compile(r"DEFAULT\s+'([^']*)'", re.IGNORECASE)


def _enum_default(ddl: str, allowed: tuple[str, ...]) -> str:
    """The value an omitted enum field takes: the one the column DDL declares.

    Read out of the DDL rather than written down a second time, because two
    copies of a default is one desynchronisation waiting to be found in
    production. (It had already happened once: ``PRIORITIES[0]`` is ``LOW``
    while the declared column default is ``NORMAL``, so an omitted priority
    stored a different value than the schema promised.) Falls back to
    ``allowed[0]`` for a column whose DDL declares nothing.
    """
    match = _DDL_DEFAULT_RE.search(ddl)
    if match and match.group(1) in allowed:
        return match.group(1)
    return allowed[0]


def _score(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PrivateRecordRejected(f"relevance_score is not a number: {value!r}")
    if number != number or number in (float("inf"), float("-inf")):
        raise PrivateRecordRejected("relevance_score is not finite")
    return max(0.0, min(number, 1.0))


def record_key(
    *, record_type: str, revision: int, identity: tuple[str, ...],
) -> str:
    """Deterministic identity for one revision of one record.

    Two purposes, both structural. It makes a repeated create idempotent — the
    same obligation arriving twice from the same nightly sweep is one row, not
    two — and it is how the new row's id is recovered after the ``INSERT``,
    since ``cur.lastrowid`` is ``None`` on PostgreSQL for these tables and this
    package therefore never uses it.

    ``revision`` is part of the key so that a revision of a record can carry the
    same identity as the row it supersedes without colliding with it. Without
    it, preserving history and enforcing uniqueness would be the same conflict.
    """
    material = "\x1f".join([record_type, str(int(revision))] + [str(part or "") for part in identity])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]


# ---------------------------------------------------------------------------
# Derived state
# ---------------------------------------------------------------------------
def deadline_moment(record_type: str, row: dict) -> datetime | None:
    """The record's deadline as an aware datetime, or ``None``.

    ``None`` covers four different situations on purpose — the type has no
    deadline concept, the column is empty, the stored text will not parse, or
    the type is not one of the six — because every one of them means the same
    thing to a caller: there is no date here to reason about. A caller that
    needs to tell "no deadline" from "unparseable deadline" apart is asking a
    data-quality question, which belongs in a health check rather than in the
    read path where the answer would become a user-visible state.
    """
    field = DEADLINE_FIELDS.get(str(record_type).strip().upper())
    if not field:
        return None
    raw = _iso(row.get(field), default=None)
    if not raw:
        return None
    try:
        candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        moment = (datetime.fromisoformat(candidate) if len(candidate) > 10
                  else datetime.fromisoformat(candidate + "T00:00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def effective_status(record_type: str, row: dict, *, now: datetime | None = None) -> str:
    """What is true right now, as opposed to what somebody last decided.

    Derived states are layered *over* the stored status and never replace it in
    storage — the serialized record carries both, under ``status`` and
    ``effective_status``, so a reader can always recover what the member or the
    desk actually decided.

    Two rules make this trustworthy, and both are about what the function
    declines to do:

    A closed record is never derived. A resolved obligation whose due date has
    passed is resolved; a canceled request past its deadline is canceled. The
    check is ``stored in spec["closing"]``, so it follows the type's own
    definition of an ending rather than a list of status names kept in step by
    hand — which is how the sixth primitive ends up reporting completed work as
    overdue.

    A record whose type has no deadline is never derived. See
    :data:`NO_DEADLINE_REASON`; the failure this avoids is marking the member's
    entire recorded history overdue because domain events happened in the past.
    """
    kind = str(record_type).strip().upper()
    stored = str(row.get("status") or "")
    spec = SPECS.get(kind)
    if spec is None or not stored:
        return stored
    # Time does not close a record and it does not reopen one. Both directions
    # matter: the first stops completed work being reported as overdue, the
    # second stops a derived state from making a closed record look actionable.
    if stored in spec["closing"]:
        return stored
    moment = deadline_moment(kind, row)
    if moment is None:
        return stored
    # Server time. A client-supplied "now" would let a device with a wrong clock
    # decide what the member owes.
    reference = now or _now()
    if moment < reference:
        return DERIVED_OVERDUE
    if moment - reference <= DUE_SOON_WINDOWS.get(kind, DUE_SOON_WINDOW):
        return DERIVED_DUE_SOON
    return stored


def _serialize(record_type: str, row, *, now: datetime | None = None) -> dict:
    """One record as the rest of the platform sees it.

    Note what is absent: ``owner_user_id`` and ``record_key``. Neither is
    useful to a caller that already had to prove ownership to get here, and
    both are exactly the kind of internal identifier that ends up on a wire and
    then in a bug report. The internal money columns are collapsed into a
    single ``amount``, and ``closed_at`` is renamed to the name this primitive
    uses for closure.
    """
    spec = SPECS[record_type]
    data = dict(row)
    out: dict = {
        "id": int(data.get("id") or 0),
        "record_type": record_type,
        "title": data.get("title") or "",
        "status": data.get("status") or "",
        "effective_status": effective_status(record_type, data, now=now),
        "lifecycle_state": data.get("lifecycle_state") or LIFECYCLE_ACTIVE,
        "revision": int(data.get("revision") or 1),
        "supersedes_id": int(data.get("supersedes_id") or 0),
        "domain": data.get("domain") or _model.DEFAULT_DOMAIN,
        "sensitivity": data.get("sensitivity") or _model.DEFAULT_SENSITIVITY,
        "source_type": data.get("source_type") or SOURCE_USER,
        "source_ref": data.get("source_ref") or "",
        "provenance_state": data.get("provenance_state") or "",
        "provenance": _facts.decode_provenance_ref(data.get("provenance_ref")).__dict__.copy(),
        "related_entity_ids": [r for r in str(data.get("related_entity_ids") or "").split(",") if r],
        "related_document_ids": [r for r in str(data.get("related_document_ids") or "").split(",") if r],
        "created_at": data.get("created_at") or "",
        "updated_at": data.get("updated_at") or "",
    }
    out[spec["summary_as"]] = data.get("summary") or ""
    if spec["closed_as"]:
        name = "closed_at" if spec["closed_as"] == "closed_at_projected" else spec["closed_as"]
        out[name] = data.get("closed_at") or None
    for name, _ddl, kind, _req in spec["extra"]:
        if kind == "internal":
            continue
        if kind == "flag":
            out[name] = bool(data.get(name))
        elif kind == "score":
            value = data.get(name)
            out[name] = None if value is None else float(value)
        else:
            out[name] = data.get(name) if data.get(name) is not None else ""
    if record_type == TYPE_OBLIGATION:
        out["amount"] = data.get("amount_text") or ""
        out["amount_number"] = (
            None if data.get("amount_number") is None else float(data["amount_number"]))
    return out


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def _prepare(record_type: str, spec: dict, fields: dict, *, revision: int) -> dict:
    """Validate and normalise one record's columns. Raises, never guesses."""
    values: dict = {}

    values["title"] = _text(fields.get("title"), MAX_TITLE)
    summary_input = fields.get("summary")
    if summary_input is None:
        summary_input = fields.get(spec["summary_as"])
    values["summary"] = _text(summary_input, MAX_SUMMARY)

    values["status"] = _enum(
        fields.get("status"), spec["statuses"], "status", spec["default_status"])

    values["domain"] = _model.normalize_domain(
        fields.get("domain") or _model.DEFAULT_DOMAIN)
    if not values["domain"]:
        raise PrivateRecordRejected(f"unknown domain: {fields.get('domain')!r}")
    values["sensitivity"] = _model.normalize_sensitivity(
        fields.get("sensitivity") or _model.DEFAULT_SENSITIVITY)
    if not values["sensitivity"]:
        raise PrivateRecordRejected(f"unknown sensitivity: {fields.get('sensitivity')!r}")

    source = _enum(fields.get("source_type"), SOURCE_TYPES, "source_type", SOURCE_USER)
    values["source_type"] = source
    values["source_ref"] = _text(fields.get("source_ref"), MAX_SOURCE_REF)

    provenance_state = ""
    if fields.get("provenance_type"):
        provenance_state = _model.normalize_provenance(fields["provenance_type"]) or ""
        if not provenance_state:
            raise PrivateRecordRejected(
                f"unknown provenance_type: {fields['provenance_type']!r}")
        if provenance_state in _model.DEGRADED_PROVENANCE:
            # STALE and CONFLICTING are states a record is moved *into*, never
            # born in. A record born STALE is one that can never win an
            # argument it was never in.
            raise PrivateRecordRejected(
                f"{provenance_state} is a derived state, not a source of a new record")
    if source in DERIVED_SOURCES and not provenance_state:
        raise PrivateRecordRejected(
            f"source_type={source} is a derivation; provenance_type is required")
    values["provenance_state"] = provenance_state
    values["provenance_ref"] = (
        fields.get("provenance") or _facts.ProvenanceRef()).encoded()

    values["related_entity_ids"] = normalize_refs(
        fields.get("related_entity_ids") if fields.get("related_entity_ids") is not None
        else fields.get("related_entity_id"))
    values["related_document_ids"] = normalize_refs(
        fields.get("related_document_ids") if fields.get("related_document_ids") is not None
        else fields.get("related_document_id"))

    enums = spec.get("enums") or {}
    for name, ddl, kind, required in spec["extra"]:
        raw = fields.get(name)
        if kind == "token":
            values[name] = _token(raw, name, required=required)
        elif kind == "timestamp":
            resolved = _iso(raw, default=None)
            if required and not resolved:
                raise PrivateRecordRejected(f"{name} is required")
            values[name] = resolved
        elif kind == "enum":
            allowed = enums[name]
            values[name] = _enum(raw, allowed, name, _enum_default(ddl, allowed))
        elif kind == "flag":
            values[name] = 1 if raw else 0
        elif kind == "score":
            values[name] = _score(raw)
        elif kind == "ref":
            values[name] = safe_ref(raw)
        elif kind == "text":
            cap = MAX_QUESTION if name == "question" else (
                MAX_ASSUMPTIONS if name == "assumptions" else MAX_OUTCOME)
            values[name] = _text(raw, cap)
            if required and not values[name]:
                raise PrivateRecordRejected(f"{name} is required")
        elif kind == "internal":
            values[name] = None if name.endswith("_number") else ""
        elif kind == "currency":
            values[name] = _text(raw, 8).upper()

    for name in spec["required"]:
        if name in ("title", "summary") and not values.get(name):
            raise PrivateRecordRejected(f"{name} is required")

    if record_type == TYPE_OBLIGATION and fields.get("amount") not in (None, ""):
        normalized = _facts.normalize_value(fields["amount"], _model.VALUE_MONEY)
        if normalized is None:
            # Rejected rather than coerced to text: an obligation whose amount
            # is "about 400k" is one that no total, no sort and no
            # due-soon-by-value view can ever include, while still counting as
            # present.
            raise PrivateRecordRejected(f"amount does not normalize as money: {fields['amount']!r}")
        values["amount_text"], values["amount_number"] = normalized

    if record_type == TYPE_EVENT and not values.get("occurred_at"):
        # An event with no time is not placeable in the member's history, which
        # is the only thing an event is for. Default to now rather than reject:
        # the caller who knows the time passes it, and the caller who observed
        # it just now is telling the truth by omission.
        values["occurred_at"] = _now_iso()

    values["revision"] = int(revision)
    return values


def _insert(cur, spec: dict, record_type: str, owner: int, values: dict,
            *, key: str, supersedes_id: int, now_iso: str) -> int:
    columns = ["owner_user_id", "record_key", "created_at", "updated_at",
               "lifecycle_state", "supersedes_id", "closed_at"]
    params: list = [owner, key, now_iso, now_iso, LIFECYCLE_ACTIVE, int(supersedes_id), None]
    for name in _column_names(spec):
        if name in columns:
            continue
        columns.append(name)
        params.append(values.get(name))
    placeholders = ", ".join("?" for _ in columns)
    # Interpolated through `private_table_for` rather than through `spec["table"]` so
    # that the static write-boundary guard can see this statement for what it
    # is. A write whose table name reaches the SQL by a route no regex can
    # follow is a write the guard silently stops protecting, and the guard
    # passing is then evidence of nothing.
    cur.execute(
        f"INSERT INTO {private_table_for(record_type)} ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(params),
    )
    cur.execute(
        f"SELECT id FROM {spec['table']} WHERE owner_user_id = ? AND record_key = ?",
        (owner, key),
    )
    row = cur.fetchone()
    if row is None:
        return 0
    return int(row["id"] if hasattr(row, "keys") else row[0])


def create_record(
    cur,
    *,
    record_type: str,
    owner_user_id: int,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    **fields,
) -> dict:
    """Write one record. The only supported way to create any of the six.

    Returns ``{"status", "record_id", "record_type", "record"}`` where status is
    ``created`` for a new row or ``existing`` when an identical record from an
    identical source was already present — deduped here, explicitly, rather than
    by an ``INSERT OR IGNORE`` that means two different things on the two
    engines this platform runs on.

    Raises :class:`PrivateRecordRejected` when the write would break an
    invariant. The rejection counter is emitted once here rather than at each of
    the twenty raise sites inside :func:`_prepare`, for the same reason
    ``facts.record_fact`` wraps ``_record_fact``: a counter that has to be
    remembered at every raise site is a counter that is wrong.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRecordRejected("owner_user_id is required")

    try:
        values = _prepare(kind, spec, fields, revision=1)
    except PrivateRecordRejected:
        _telemetry.emit(
            _telemetry.EVENT_RECORD_WRITE, outcome="rejected", record_type=kind,
            domain=_model.DEFAULT_DOMAIN, sensitivity=_model.DEFAULT_SENSITIVITY,
            provenance_type="", superseded=False)
        raise

    require_records_schema(cur)

    identity = tuple(str(values.get(name) or "") for name in spec["identity"])
    identity = identity + (values["source_type"], values["source_ref"])
    key = record_key(record_type=kind, revision=1, identity=identity)

    cur.execute(
        f"SELECT * FROM {spec['table']} WHERE owner_user_id = ? AND record_key = ?",
        (owner, key),
    )
    existing = cur.fetchone()
    if existing is not None:
        record = _serialize(kind, existing)
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_RECORD_CREATE, object_type=spec["audit_object"],
            object_id=record["id"], purpose=purpose, outcome=_audit.OUTCOME_OK,
        )
        _telemetry.emit(
            _telemetry.EVENT_RECORD_WRITE, outcome=STATUS_EXISTING, record_type=kind,
            domain=values["domain"], sensitivity=values["sensitivity"],
            provenance_type=values["provenance_state"], superseded=False)
        return {"status": STATUS_EXISTING, "record_id": record["id"],
                "record_type": kind, "record": record}

    now_iso = _now_iso()
    record_id = _insert(cur, spec, kind, owner, values,
                        key=key, supersedes_id=0, now_iso=now_iso)

    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_RECORD_CREATE, object_type=spec["audit_object"],
        object_id=record_id, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    _telemetry.emit(
        _telemetry.EVENT_RECORD_WRITE, outcome=STATUS_CREATED, record_type=kind,
        domain=values["domain"], sensitivity=values["sensitivity"],
        provenance_type=values["provenance_state"], superseded=False)

    return {"status": STATUS_CREATED, "record_id": record_id, "record_type": kind,
            "record": get_record(cur, record_type=kind, owner_user_id=owner,
                                 record_id=record_id, audit=False)}


# ---------------------------------------------------------------------------
# The transition contract
# ---------------------------------------------------------------------------
# Before this section existed, `update_record` accepted any status in the type's
# vocabulary, which made ``RESOLVED -> OPEN`` and ``COMPLETED -> CANCELED`` legal
# moves that left no trace of what they overwrote. The vocabulary was never the
# problem and is not changed here: every status below is one the six specs
# already declared. What was missing was the *edge* set — which of the pairs the
# vocabulary makes expressible are actually meaningful — and that is derived from
# the spec structure rather than restated, so a status added to a spec cannot
# arrive without a transition rule.
#
# Three classes of state, all read off ``spec["statuses"]`` and ``spec["closing"]``:
#
#   working   a status not in ``closing`` — the matter is live
#   closing   a status in ``closing`` — the matter ended, and ``closed_at`` says when
#   (none)    EVENT has one status and no closing set, so every move is a STAY
#
# and four classes of move:
#
#   STAY      old == new. Not a transition. Restating CANCELED on a canceled
#             request is how an idempotent caller behaves, not an error.
#   MOVE      working -> working. Unrestricted within the type. A request going
#             OPEN -> WAITING_ON_PROVIDER -> IN_PROGRESS is the concierge desk
#             working; ordering that sequence would be inventing a workflow the
#             product does not have.
#   CLOSE     working -> closing. Always legal. Ending a live matter is the
#             normal terminus of every primitive that has one.
#   REOPEN    closing -> working. Legal only for the pairs in `REOPENABLE`, only
#             onto the type's default status, and only when the caller says so.
#
# Everything else is forbidden, and one case deserves naming because it looks
# harmless: closing -> a *different* closing. "Cancel this completed request"
# reads like tidying and is actually a rewrite of how the matter ended, with the
# original ending unrecoverable — the same class of loss `revise_record` exists
# to prevent on the substance side. A caller who means it reopens first, which
# leaves two audit rows instead of none.

#: Which closed statuses may return to the working state, per type. Deliberately
#: enumerated rather than derived from ``closing``, because "can this end be
#: undone" is a product question with a different answer for each pair and no
#: structural tell.
#:
#: * OBLIGATION — both. A dismissed obligation that turns out to be real, and a
#:   resolved one that bounced, are ordinary events in anyone's affairs.
#: * DECISION — ABANDONED only. A question set aside can be picked back up.
#:   DECIDED is excluded on purpose: a decision whose conclusion is reopened and
#:   re-decided in place is a decision log that records the latest answer and
#:   destroys the one before it, which is precisely what this primitive's
#:   revision rule exists to prevent. Revisiting a decided question is a new
#:   decision, and the old one stays legible next to it.
#: * REQUEST — CANCELED only. A member who withdraws a request may want it back.
#:   COMPLETED is excluded: work the desk reported as finished is not un-finished
#:   by an update, and "it wasn't actually done" is a new request that can cite
#:   the old one.
#: * RISK — both. Recurrence is the defining behaviour of a risk; a store that
#:   cannot reopen one would push the member into filing duplicates and lose the
#:   history that made the risk worth watching.
#: * OPPORTUNITY — PASSED only. Passing is a judgement and judgements change.
#:   CLOSED is the window shutting, which is not a judgement and cannot be
#:   revisited by changing a row.
#: * EVENT — absent. It has no closing statuses to return from.
REOPENABLE: dict[str, tuple[str, ...]] = {
    TYPE_OBLIGATION: ("RESOLVED", "DISMISSED"),
    TYPE_DECISION: ("ABANDONED",),
    TYPE_REQUEST: ("CANCELED",),
    TYPE_RISK: ("RESOLVED", "DISMISSED"),
    TYPE_OPPORTUNITY: ("PASSED",),
}

TRANSITION_STAY = "STAY"
TRANSITION_MOVE = "MOVE"
TRANSITION_CLOSE = "CLOSE"
TRANSITION_REOPEN = "REOPEN"


def working_statuses(record_type: str) -> tuple[str, ...]:
    """The statuses of *record_type* that mean the matter is still live."""
    spec = _spec(record_type)
    closing = set(spec["closing"])
    return tuple(s for s in spec["statuses"] if s not in closing)


def allowed_transitions(record_type: str, current: str) -> tuple[str, ...]:
    """Every status reachable from *current*, including *current* itself.

    Read-only and side-effect free, so a caller — a route rendering the buttons
    a member may press, a test asserting the contract — can ask what is legal
    without attempting it. The UI drawing a control the writer will refuse is a
    lie told in advance; this is how it avoids telling it.
    """
    spec = _spec(record_type)
    here = str(current or "").strip().upper()
    if here not in spec["statuses"]:
        return ()
    live = working_statuses(record_type)
    if here not in spec["closing"]:
        # Working: anywhere else that is working, plus every ending.
        return tuple(dict.fromkeys(live + tuple(spec["closing"])))
    if here in REOPENABLE.get(str(record_type).strip().upper(), ()):
        return (here, spec["default_status"])
    return (here,)


def check_transition(
    record_type: str, current: str, target: str, *, reopen: bool = False,
) -> str:
    """Classify ``current -> target``, or raise :class:`PrivateRecordRejected`.

    Returns one of the ``TRANSITION_*`` constants. Raising rather than returning
    a falsey verdict is the fail-closed half of the contract: there is no way to
    call this and carry on past a refusal by forgetting to check the result.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    here = str(current or "").strip().upper()
    there = str(target or "").strip().upper()

    if there not in spec["statuses"]:
        raise PrivateRecordRejected(f"{there or '(empty)'} is not a {kind} status")
    if here not in spec["statuses"]:
        # A row carrying a status the spec no longer declares. Refusing is the
        # only safe reading: the transition rules were written against a
        # vocabulary this row predates, so none of them are known to apply.
        raise PrivateRecordRejected(
            f"{kind} record is in unrecognised state {here or '(empty)'}")
    if here == there:
        return TRANSITION_STAY

    closing = set(spec["closing"])
    if here not in closing:
        return TRANSITION_CLOSE if there in closing else TRANSITION_MOVE

    # Closed. The only way out is a reopen the type allows, onto the default
    # status, asked for explicitly.
    if there in closing:
        raise PrivateRecordRejected(
            f"{kind} is already closed as {here}; changing it to {there} would "
            f"overwrite how the record ended — reopen it first")
    if here not in REOPENABLE.get(kind, ()):
        raise PrivateRecordRejected(f"{kind} cannot be reopened from {here}")
    if there != spec["default_status"]:
        raise PrivateRecordRejected(
            f"reopening a {kind} returns it to {spec['default_status']}, not {there}")
    if not reopen:
        # The status alone is not consent. A caller that means to undo a closure
        # says so; one that arrived here by passing a stale status through from
        # a form gets a refusal instead of a silently discarded resolved_at.
        raise PrivateRecordRejected(
            f"{kind} is closed as {here}; pass reopen=True to return it to "
            f"{spec['default_status']}")
    return TRANSITION_REOPEN


#: Fields :func:`update_record` will move. Anything else is a change to the
#: substance of the record and belongs in :func:`revise_record`, which keeps the
#: old version. This tuple is the enforcement, not a docstring: a caller passing
#: ``question=`` to ``update_record`` gets a rejection rather than a silently
#: rewritten decision log.
UPDATABLE: tuple[str, ...] = (
    "status", "outcome", "assigned_provider_id", "severity",
    "coverage_state", "review_required", "priority", "project_ref",
)


def update_record(
    cur,
    *,
    record_type: str,
    owner_user_id: int,
    record_id: int,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    **fields,
) -> dict:
    """Move a record's status, closure, outcome or assignment.

    Deliberately narrow. The substance of a record — the question a decision
    asks, the terms of an obligation, the description of a request — is not
    reachable from here; see :func:`revise_record`. That split is what makes
    "preserve decision history" a property of the code rather than a rule people
    remember, because the function that could destroy history does not exist.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRecordRejected("owner_user_id is required")

    # `reopen` is intent, not data. It is pulled out before the UPDATABLE check
    # so it never reaches the column loop and never appears in an assignment.
    reopen = bool(fields.pop("reopen", False))

    unknown = [name for name in fields if name not in UPDATABLE]
    if unknown:
        raise PrivateRecordRejected(
            f"not updatable in place: {', '.join(sorted(unknown))}; use revise_record")

    require_records_schema(cur)
    current = _fetch(cur, spec, owner, int(record_id or 0))
    if current is None:
        # Not "not found" as an exception with a distinguishable message. A
        # caller who may not see a record and a caller asking about a record
        # that never existed get the same answer, which is the property Stage 14
        # asks for and which an update path is just as capable of breaking as a
        # read path.
        return {"status": "absent", "record_id": 0, "record_type": kind, "record": None}

    # `_fetch` returns whatever the driver's row type is — ``sqlite3.Row``
    # locally, something else on Postgres — and only ``dict(row)`` is common to
    # all of them. `_serialize` already relies on that; doing the same here
    # keeps the transition check from being the one place that assumes SQLite.
    current_row = dict(current)

    enums = spec.get("enums") or {}
    assignments: list[str] = []
    params: list = []
    now_iso = _now_iso()

    move = ""
    if "status" in fields:
        # Not `_enum`, which coerces an unrecognised value to the default. A
        # typo'd status must not silently reopen a resolved obligation, so the
        # raw value goes to `check_transition`, which rejects what it does not
        # recognise instead of substituting something plausible.
        status = str(fields["status"] or "").strip().upper()
        try:
            move = check_transition(
                kind, str(current_row.get("status") or ""), status, reopen=reopen)
        except PrivateRecordRejected:
            # Fail closed, and leave a trace. No column has been assigned yet,
            # so nothing is written but this row: the refusal is the only
            # evidence the attempt happened.
            _audit.record(
                cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
                action=_audit.ACTION_RECORD_TRANSITION_DENIED,
                object_type=spec["audit_object"], object_id=int(record_id),
                purpose=purpose, outcome=_audit.OUTCOME_DENIED,
            )
            raise
        if move != TRANSITION_STAY:
            assignments.append("status = ?")
            params.append(status)
            if move == TRANSITION_CLOSE:
                assignments.append("closed_at = ?")
                params.append(now_iso)
            elif move == TRANSITION_REOPEN:
                # Reopening clears the closure stamp. A record that is OPEN and
                # carries a resolved_at is a row two readers will read two ways.
                assignments.append("closed_at = ?")
                params.append(None)

    extra_cols = {name: (kind_, ddl) for name, ddl, kind_, _req in spec["extra"]}
    for name, value in fields.items():
        if name == "status":
            continue
        if name not in extra_cols:
            raise PrivateRecordRejected(f"{name} does not exist on {kind}")
        column_kind, column_ddl = extra_cols[name]
        if column_kind == "enum":
            allowed = enums[name]
            resolved: object = _enum(value, allowed, name, _enum_default(column_ddl, allowed))
        elif column_kind == "flag":
            resolved = 1 if value else 0
        elif column_kind == "ref":
            resolved = safe_ref(value)
        elif column_kind == "text":
            resolved = _text(value, MAX_OUTCOME)
        else:
            raise PrivateRecordRejected(f"{name} is not updatable in place")
        assignments.append(f"{name} = ?")
        params.append(resolved)

    if not assignments:
        if move == TRANSITION_STAY:
            # A legal restatement of the status the record already holds, with
            # nothing else to move. Not an error and not a write: touching
            # `updated_at` here would make "when did this last change" mean
            # "when was it last mentioned", which is what makes a recent-activity
            # feed fill with rows that did not change.
            return {"status": STATUS_UNCHANGED, "record_id": int(record_id),
                    "record_type": kind, "record": _serialize(kind, current)}
        raise PrivateRecordRejected("nothing to update")

    assignments.append("updated_at = ?")
    params.append(now_iso)
    params.extend([owner, int(record_id)])
    # Interpolated through `private_table_for` for the same reason as the INSERT
    # in `_insert`: the static write-boundary guard matches on the table name
    # reaching the SQL by a route its regex can follow.
    cur.execute(
        f"UPDATE {private_table_for(kind)} SET {', '.join(assignments)} "
        f"WHERE owner_user_id = ? AND id = ?",
        tuple(params),
    )

    updated = _fetch(cur, spec, owner, int(record_id))
    record = _serialize(kind, updated) if updated is not None else None
    closed = bool(record and record.get("status") in spec["closing"])

    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=(_audit.ACTION_RECORD_REOPEN if move == TRANSITION_REOPEN
                else _audit.ACTION_RECORD_UPDATE),
        object_type=spec["audit_object"],
        object_id=int(record_id), purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    _telemetry.emit(
        _telemetry.EVENT_RECORD_WRITE, outcome=STATUS_UPDATED, record_type=kind,
        domain=(record or {}).get("domain") or _model.DEFAULT_DOMAIN,
        sensitivity=(record or {}).get("sensitivity") or _model.DEFAULT_SENSITIVITY,
        provenance_type=(record or {}).get("provenance_state") or "",
        superseded=False)
    if closed:
        _telemetry.emit(
            _telemetry.EVENT_RECORD_CLOSED, record_type=kind,
            status=(record or {}).get("status") or "",
            domain=(record or {}).get("domain") or _model.DEFAULT_DOMAIN)

    return {"status": STATUS_UPDATED, "record_id": int(record_id),
            "record_type": kind, "record": record}


def revise_record(
    cur,
    *,
    record_type: str,
    owner_user_id: int,
    record_id: int,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    **fields,
) -> dict:
    """Supersede a record with a new version, keeping the old one readable.

    The old row becomes ``SUPERSEDED`` and keeps every value it had; the new row
    is ``ACTIVE`` and carries ``supersedes_id`` back to it. Fields the caller
    does not mention are inherited, so a revision that only changes the summary
    does not silently blank the due date.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRecordRejected("owner_user_id is required")

    require_records_schema(cur)
    current = _fetch(cur, spec, owner, int(record_id or 0))
    if current is None:
        return {"status": "absent", "record_id": 0, "record_type": kind, "record": None}

    prior = dict(current)
    if str(prior.get("lifecycle_state") or "") != LIFECYCLE_ACTIVE:
        raise PrivateRecordRejected("only an ACTIVE record can be revised")

    merged: dict = {}
    for name in _column_names(spec):
        if name in ("owner_user_id", "record_key", "created_at", "updated_at",
                    "lifecycle_state", "supersedes_id", "closed_at", "revision"):
            continue
        merged[name] = prior.get(name)
    merged["provenance_type"] = prior.get("provenance_state") or ""
    merged[spec["summary_as"]] = prior.get("summary") or ""
    if kind == TYPE_OBLIGATION and prior.get("amount_text"):
        merged["amount"] = prior["amount_text"]
    merged.update(fields)
    if "provenance" not in merged:
        merged["provenance"] = _facts.decode_provenance_ref(prior.get("provenance_ref"))

    revision = int(prior.get("revision") or 1) + 1
    values = _prepare(kind, spec, merged, revision=revision)

    identity = tuple(str(values.get(name) or "") for name in spec["identity"])
    identity = identity + (values["source_type"], values["source_ref"])
    key = record_key(record_type=kind, revision=revision, identity=identity)

    now_iso = _now_iso()
    cur.execute(
        f"UPDATE {private_table_for(kind)} SET lifecycle_state = ?, updated_at = ? "
        f"WHERE owner_user_id = ? AND id = ?",
        (LIFECYCLE_SUPERSEDED, now_iso, owner, int(record_id)),
    )
    new_id = _insert(cur, spec, kind, owner, values,
                     key=key, supersedes_id=int(record_id), now_iso=now_iso)

    # A revision is the same thing continuing under a new id, so its
    # dependencies come with it. See `_repoint_links` in the dependencies
    # section for why this cannot be left to the read side.
    _repoint_links(cur, owner, kind, int(record_id), new_id, now_iso=now_iso)

    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_RECORD_REVISE, object_type=spec["audit_object"],
        object_id=new_id, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    _telemetry.emit(
        _telemetry.EVENT_RECORD_WRITE, outcome=STATUS_REVISED, record_type=kind,
        domain=values["domain"], sensitivity=values["sensitivity"],
        provenance_type=values["provenance_state"], superseded=True)

    return {"status": STATUS_REVISED, "record_id": new_id, "record_type": kind,
            "supersedes_id": int(record_id),
            "record": get_record(cur, record_type=kind, owner_user_id=owner,
                                 record_id=new_id, audit=False)}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def _fetch(cur, spec: dict, owner: int, record_id: int):
    """One row, owner-scoped. ``owner_user_id`` leads the WHERE clause.

    Every read in this module goes through here or through
    :func:`list_records`, and both put owner first. That is the isolation
    mechanism: a foreign id does not fail a check, it matches no row.
    """
    if owner <= 0 or record_id <= 0:
        return None
    cur.execute(
        f"SELECT * FROM {spec['table']} WHERE owner_user_id = ? AND id = ?",
        (owner, record_id),
    )
    return cur.fetchone()


def get_record(
    cur,
    *,
    record_type: str,
    owner_user_id: int,
    record_id: int,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    audit: bool = True,
) -> dict | None:
    """One record, or ``None``.

    ``None`` for "not yours" and ``None`` for "never existed" — the same answer,
    deliberately, so this is not an existence oracle for another member's data.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    owner = int(owner_user_id or 0)
    require_records_schema(cur)
    row = _fetch(cur, spec, owner, int(record_id or 0))
    if audit:
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner or 0), owner_user_id=owner,
            action=_audit.ACTION_RECORD_READ, object_type=spec["audit_object"],
            object_id=int(record_id or 0), purpose=purpose,
            outcome=_audit.OUTCOME_OK if row is not None else _audit.OUTCOME_DENIED,
            result_count=1 if row is not None else 0,
        )
    if row is None:
        return None
    return _serialize(kind, row)


def list_records(
    cur,
    *,
    record_type: str,
    owner_user_id: int,
    statuses: object = None,
    include_superseded: bool = False,
    domains: object = None,
    sensitivity_ceiling: object = None,
    due_before: object = None,
    limit: int = DEFAULT_LIMIT,
    before_id: int = 0,
) -> list[dict]:
    """Records for one owner, newest first, bounded.

    The bound is not negotiable and not a parameter a caller can raise past
    :data:`MAX_LIMIT`. An unbounded list over a shared table is the query that
    is fine for four years and then is not.

    A caller who names only unrecognised domains gets ``[]`` rather than
    everything — the same rule the fact store follows, because the alternative
    means a typo widens a filter.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    require_records_schema(cur)

    clauses = ["owner_user_id = ?"]
    params: list = [owner]

    if not include_superseded:
        clauses.append("lifecycle_state = ?")
        params.append(LIFECYCLE_ACTIVE)

    wanted = _as_tuple(statuses)
    if wanted:
        valid = [s for s in (str(x).strip().upper() for x in wanted) if s in spec["statuses"]]
        if not valid:
            return []
        clauses.append(f"status IN ({', '.join('?' for _ in valid)})")
        params.extend(valid)

    named_domains = _as_tuple(domains)
    if named_domains:
        valid_domains = [d for d in (_model.normalize_domain(x) for x in named_domains) if d]
        if not valid_domains:
            return []
        clauses.append(f"domain IN ({', '.join('?' for _ in valid_domains)})")
        params.extend(valid_domains)

    ceiling = _model.normalize_sensitivity(sensitivity_ceiling) if sensitivity_ceiling else None
    if sensitivity_ceiling and not ceiling:
        return []
    if ceiling:
        permitted = [s for s in _model.SENSITIVITIES if _model.sensitivity_within(s, ceiling)]
        if not permitted:
            return []
        clauses.append(f"sensitivity IN ({', '.join('?' for _ in permitted)})")
        params.extend(permitted)

    # Resolved through `DEADLINE_FIELDS` rather than hardcoded to `due_at`, so
    # "due before X" means the same thing on a decision's `deadline_at` as on an
    # obligation's `due_at`. A type with no deadline concept ignores the filter
    # instead of matching everything, which is the safe direction: EVENT would
    # otherwise return the member's whole history for any boundary in the past.
    deadline_column = DEADLINE_FIELDS.get(kind)
    if due_before:
        if not deadline_column:
            return []
        boundary = _iso(due_before, default=None)
        if boundary:
            clauses.append(f"{deadline_column} IS NOT NULL AND {deadline_column} <= ?")
            params.append(boundary)

    if before_id and int(before_id) > 0:
        clauses.append("id < ?")
        params.append(int(before_id))

    bounded = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    cur.execute(
        f"SELECT * FROM {spec['table']} WHERE {' AND '.join(clauses)} "
        f"ORDER BY id DESC LIMIT {bounded}",
        tuple(params),
    )
    now = _now()
    return [_serialize(kind, row, now=now) for row in cur.fetchall()]


def _as_tuple(value: object) -> tuple:
    if value is None or value == "":
        return ()
    if isinstance(value, (str, bytes)):
        return tuple(part for part in str(value).split(",") if part.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def count_records(
    cur,
    *,
    record_type: str,
    owner_user_id: int,
    statuses: object = None,
    open_only: bool = False,
    include_superseded: bool = False,
    due_before: object = None,
    due_after: object = None,
    closed_since: object = None,
    severities: object = None,
) -> int:
    """A bounded ``COUNT`` for one owner and one type. Owner-scoped.

    This exists so the overview can count in SQL rather than in Python over a
    truncated list. The tempting shortcut — pull the first two hundred rows and
    ``len`` the ones that match — produces an overview that is exactly right for
    small accounts and silently understates every large one, and the accounts it
    lies to are the ones with the most at stake.

    ``include_superseded`` defaults to False, so a revised record counts once.
    A superseded row is the *old version* of something the member still has;
    counting both is how "3 open obligations" becomes 7 after a few edits.
    """
    spec = _spec(record_type)
    kind = str(record_type).strip().upper()
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return 0
    require_records_schema(cur)

    clauses = ["owner_user_id = ?"]
    params: list = [owner]

    if not include_superseded:
        clauses.append("lifecycle_state = ?")
        params.append(LIFECYCLE_ACTIVE)

    wanted = _as_tuple(statuses)
    if wanted:
        valid = [s for s in (str(x).strip().upper() for x in wanted) if s in spec["statuses"]]
        # Same rule as `list_records`: an unrecognised filter narrows to nothing
        # rather than widening to everything.
        if not valid:
            return 0
        clauses.append(f"status IN ({', '.join('?' for _ in valid)})")
        params.extend(valid)
    elif open_only:
        live = [s for s in spec["statuses"] if s not in spec["closing"]] or list(spec["statuses"])
        clauses.append(f"status IN ({', '.join('?' for _ in live)})")
        params.extend(live)

    named_severities = _as_tuple(severities)
    if named_severities:
        # Only the types that declare a `severity` enum can answer this. The
        # others return 0 rather than ignoring the filter, so "how many critical
        # opportunities" cannot come back as "all of them".
        if "severity" not in (spec.get("enums") or {}):
            return 0
        valid = [s for s in (str(x).strip().upper() for x in named_severities)
                 if s in SEVERITIES]
        if not valid:
            return 0
        clauses.append(f"severity IN ({', '.join('?' for _ in valid)})")
        params.extend(valid)

    deadline_column = DEADLINE_FIELDS.get(kind)
    if due_before is not None or due_after is not None:
        if not deadline_column:
            return 0
        clauses.append(f"{deadline_column} IS NOT NULL")
        if due_after is not None:
            boundary = _iso(due_after, default=None)
            if boundary:
                clauses.append(f"{deadline_column} >= ?")
                params.append(boundary)
        if due_before is not None:
            boundary = _iso(due_before, default=None)
            if boundary:
                clauses.append(f"{deadline_column} < ?")
                params.append(boundary)

    if closed_since is not None:
        if not spec["closing"]:
            return 0
        boundary = _iso(closed_since, default=None)
        if boundary:
            clauses.append("closed_at IS NOT NULL AND closed_at >= ?")
            params.append(boundary)

    cur.execute(
        f"SELECT COUNT(*) AS n FROM {spec['table']} WHERE {' AND '.join(clauses)}",
        tuple(params),
    )
    row = cur.fetchone()
    if row is None:
        return 0
    return int(row["n"] if hasattr(row, "keys") else row[0])


def _link_endpoint(cur, owner: int, record_type: object, record_id: object,
                   *, role: str) -> tuple[str, int, dict]:
    """Resolve one end of a dependency, or refuse.

    Every refusal below is deliberate about *which* refusal it is, because two
    of them leak and one does not:

    * An unknown type or an unlinkable type is a shape error. The caller named
      something that could never be an endpoint for anybody, so saying so
      reveals nothing about this member's data.
    * An id that does not resolve **under this owner** is reported as not found,
      with no hint that the row exists for someone else. This is the same rule
      the graph module applies to nodes and the same rule ``update_record``
      applies to a missing record: "you may not see that" and "that was never
      issued" must be indistinguishable, or the error message becomes an
      existence oracle an attacker can enumerate.

    Cross-owner rejection therefore needs no separate branch. It falls out of
    resolving the endpoint through an owner-scoped query, which is the only way
    to make it hold — a check written as ``if row["owner_user_id"] != owner``
    requires having already read another member's row.
    """
    kind = str(record_type or "").strip().upper()
    if kind not in SPECS:
        raise PrivateRecordRejected(f"unknown record_type: {record_type!r}")
    if kind not in linkable_types():
        reason = NO_LINK_REASON.get(kind, "this type cannot take part in a dependency")
        raise PrivateRecordRejected(f"{kind} cannot be a dependency {role}: {reason}")
    try:
        ident = int(record_id or 0)
    except (TypeError, ValueError):
        ident = 0
    if ident <= 0:
        raise PrivateRecordRejected(f"a dependency {role} needs a record id")
    row = _fetch(cur, SPECS[kind], owner, ident)
    if row is None:
        raise PrivateRecordRejected(f"no such {kind} record")
    return kind, ident, dict(row)


def _repoint_links(cur, owner: int, kind: str, old_id: int, new_id: int,
                   *, now_iso: str) -> int:
    """Move every edge touching ``old_id`` onto ``new_id`` after a revision.

    Without this the store is quietly wrong in three separate ways, all of them
    invisible to a test that only links and reads:

    * ``dependencies_for`` resolves the endpoint with :func:`_fetch`, which does
      not filter lifecycle, so it keeps reading the superseded row and reports
      the status that row was frozen at. ``blocked_record_ids`` *does* filter to
      ACTIVE, so it drops the edge entirely. The same question gets two answers
      depending on which reader a screen happens to call.
    * Closing the live blocker no longer unblocks anything, because the edge is
      still watching the version nobody is working on.
    * Revising the *dependent* makes its dependencies vanish from the new
      version, which is the worst of the three: the record silently reports
      itself ready to start.

    Fixing this on the read side would mean walking the supersession chain on
    every endpoint of every edge, bounded, in both readers — two more traversals
    to keep in step with each other. Re-pointing at revision time is one write in
    the one place that already holds both ids.

    Safe by construction rather than by check: ``new_id`` was inserted moments
    ago, so no edge can already reference it, and no UNIQUE collision or
    self-loop is reachable. Relabelling a single node cannot create or destroy a
    cycle either, so the acyclicity invariant carries over untouched.
    """
    if old_id <= 0 or new_id <= 0 or old_id == new_id:
        return 0
    moved = 0
    for column in ("source", "target"):
        cur.execute(
            f"UPDATE private_record_links "
            f"SET {column}_id = ?, updated_at = ? "
            f"WHERE owner_user_id = ? AND {column}_type = ? AND {column}_id = ?",
            (new_id, now_iso, owner, kind, old_id),
        )
        moved += int(getattr(cur, "rowcount", 0) or 0)
    return moved


def _outgoing(cur, owner: int, kind: str, ident: int) -> list[tuple[str, int]]:
    """What ``(kind, ident)`` is waiting on. One hop, owner-scoped."""
    cur.execute(
        f"SELECT target_type, target_id FROM private_record_links "
        f"WHERE owner_user_id = ? AND link_type = ? "
        f"AND source_type = ? AND source_id = ?",
        (owner, LINK_DEPENDS_ON, kind, ident),
    )
    out: list[tuple[str, int]] = []
    for row in cur.fetchall() or ():
        data = dict(row)
        out.append((str(data["target_type"]), int(data["target_id"])))
    return out


def _reaches(cur, owner: int, start: tuple[str, int],
             goal: tuple[str, int]) -> bool:
    """Can ``start`` reach ``goal`` by following DEPENDS_ON edges?

    This is the whole of cycle detection. Adding ``A DEPENDS_ON B`` closes a
    loop exactly when B already reaches A, so the check runs *before* the insert
    and the store never holds a cycle even briefly — which matters because the
    read side walks these edges too, and a cycle that exists for the duration of
    a transaction is still a cycle a concurrent reader can walk forever.

    Bounded twice, by depth and by total visits. An unbounded walk over
    user-controlled data is a denial of service that arrives looking like a slow
    page. Exhausting a bound returns ``True`` — the safe direction: refusing a
    legitimate link is recoverable and visible, admitting one that closes a loop
    is neither.
    """
    seen: set[tuple[str, int]] = {start}
    frontier = [(start, 0)]
    visits = 0
    while frontier:
        (node, depth) = frontier.pop()
        if node == goal:
            return True
        if depth >= MAX_DEPENDENCY_DEPTH:
            return True
        visits += 1
        if visits > MAX_DEPENDENCY_VISITS:
            return True
        for nxt in _outgoing(cur, owner, node[0], node[1]):
            if nxt in seen:
                continue
            seen.add(nxt)
            frontier.append((nxt, depth + 1))
    return False


def link_records(
    cur,
    *,
    owner_user_id: int,
    source_type: str,
    source_id: int,
    target_type: str,
    target_id: int,
    actor_user_id: int | None = None,
    note: str = "",
    purpose: str = "user_request",
) -> dict:
    """Record that ``source`` is waiting on ``target``.

    Refuses, in this order: an endpoint that is not a linkable type or does not
    resolve under this owner; a self-dependency; a link that would close a
    cycle; and a source that already carries the maximum number of blockers.
    Re-linking an existing pair is not an error — it reports ``existing`` and
    writes nothing, so a retried request converges instead of duplicating.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRecordRejected("owner_user_id is required")
    require_records_schema(cur)

    src_kind, src_id, _src = _link_endpoint(
        cur, owner, source_type, source_id, role="source")
    tgt_kind, tgt_id, _tgt = _link_endpoint(
        cur, owner, target_type, target_id, role="target")

    actor = int(actor_user_id or owner)

    def _deny(message: str) -> PrivateRecordRejected:
        # Every refusal is audited, and audited before it is raised. A denied
        # link is the interesting event — an accepted one is ordinary — and a
        # refusal that leaves no trace makes repeated probing invisible.
        _audit.record(
            cur, actor_user_id=actor, owner_user_id=owner,
            action=_audit.ACTION_RECORD_LINK_DENIED,
            object_type=SPECS[src_kind]["audit_object"], object_id=src_id,
            purpose=purpose, outcome=_audit.OUTCOME_DENIED,
        )
        return PrivateRecordRejected(message)

    if (src_kind, src_id) == (tgt_kind, tgt_id):
        # The degenerate cycle, checked separately because `_reaches` starting
        # and ending at the same node would report the trivial path and give a
        # message about loops that hides what actually happened.
        raise _deny("a record cannot depend on itself")

    if _reaches(cur, owner, (tgt_kind, tgt_id), (src_kind, src_id)):
        raise _deny(
            f"{tgt_kind} {tgt_id} already depends on {src_kind} {src_id}, so "
            f"this link would create a circular dependency")

    cur.execute(
        f"SELECT id FROM private_record_links "
        f"WHERE owner_user_id = ? AND link_type = ? AND source_type = ? "
        f"AND source_id = ? AND target_type = ? AND target_id = ?",
        (owner, LINK_DEPENDS_ON, src_kind, src_id, tgt_kind, tgt_id),
    )
    found = cur.fetchone()
    if found is not None:
        return {"status": STATUS_EXISTING, "link_id": int(dict(found)["id"]),
                "source": {"record_type": src_kind, "record_id": src_id},
                "target": {"record_type": tgt_kind, "record_id": tgt_id}}

    cur.execute(
        f"SELECT COUNT(*) AS n FROM private_record_links "
        f"WHERE owner_user_id = ? AND link_type = ? "
        f"AND source_type = ? AND source_id = ?",
        (owner, LINK_DEPENDS_ON, src_kind, src_id),
    )
    row = cur.fetchone()
    existing_count = int(dict(row)["n"]) if row is not None else 0
    if existing_count >= MAX_DEPENDENCIES_PER_RECORD:
        raise _deny(
            f"{src_kind} {src_id} already has {MAX_DEPENDENCIES_PER_RECORD} "
            f"dependencies")

    now_iso = _now_iso()
    cur.execute(
        f"INSERT INTO private_record_links "
        f"(owner_user_id, link_type, source_type, source_id, target_type, "
        f" target_id, note, created_at, updated_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (owner, LINK_DEPENDS_ON, src_kind, src_id, tgt_kind, tgt_id,
         _text(note, MAX_OUTCOME), now_iso, now_iso),
    )
    cur.execute(
        f"SELECT id FROM private_record_links "
        f"WHERE owner_user_id = ? AND link_type = ? AND source_type = ? "
        f"AND source_id = ? AND target_type = ? AND target_id = ?",
        (owner, LINK_DEPENDS_ON, src_kind, src_id, tgt_kind, tgt_id),
    )
    created = cur.fetchone()
    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_RECORD_LINK,
        object_type=SPECS[src_kind]["audit_object"], object_id=src_id,
        purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return {"status": STATUS_CREATED,
            "link_id": int(dict(created)["id"]) if created is not None else 0,
            "source": {"record_type": src_kind, "record_id": src_id},
            "target": {"record_type": tgt_kind, "record_id": tgt_id}}


def unlink_records(
    cur,
    *,
    owner_user_id: int,
    source_type: str,
    source_id: int,
    target_type: str,
    target_id: int,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict:
    """Remove a dependency.

    A real DELETE, and the one place this package deletes rather than
    superseding. The justification is that an edge is not a claim about the
    member's life — it is a working annotation about sequencing, and a
    tombstoned edge would have to be excluded from every traversal, which is
    exactly the kind of filter a later query forgets and then reports a
    resolved blocker as still blocking. The audit row is the history.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRecordRejected("owner_user_id is required")
    require_records_schema(cur)
    src_kind, src_id, _ = _link_endpoint(
        cur, owner, source_type, source_id, role="source")
    tgt_kind, tgt_id, _ = _link_endpoint(
        cur, owner, target_type, target_id, role="target")

    cur.execute(
        f"SELECT id FROM private_record_links "
        f"WHERE owner_user_id = ? AND link_type = ? AND source_type = ? "
        f"AND source_id = ? AND target_type = ? AND target_id = ?",
        (owner, LINK_DEPENDS_ON, src_kind, src_id, tgt_kind, tgt_id),
    )
    found = cur.fetchone()
    if found is None:
        return {"status": "absent", "link_id": 0}
    link_id = int(dict(found)["id"])
    cur.execute(
        f"DELETE FROM private_record_links "
        f"WHERE owner_user_id = ? AND id = ?",
        (owner, link_id),
    )
    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_RECORD_UNLINK,
        object_type=SPECS[src_kind]["audit_object"], object_id=src_id,
        purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return {"status": "removed", "link_id": link_id}


def _describe_endpoint(cur, owner: int, kind: str, ident: int,
                       *, now: datetime | None = None) -> dict:
    """A blocker or dependent as a reader needs it: enough to act, no more.

    Carries ``open`` rather than leaving the caller to compare ``status``
    against ``closing`` itself. That comparison is the whole meaning of the edge
    and having two implementations of it is how a screen ends up saying a record
    is blocked by something that finished last week.
    """
    row = _fetch(cur, SPECS[kind], owner, ident)
    if row is None:
        # An endpoint that no longer resolves. Reported rather than hidden: the
        # alternative is an edge that silently stops blocking, which looks
        # exactly like the blocker having been completed.
        return {"record_type": kind, "record_id": ident, "found": False,
                "open": False, "status": "", "title": ""}
    data = dict(row)
    status = str(data.get("status") or "")
    return {
        "record_type": kind,
        "record_id": ident,
        "found": True,
        "open": status not in SPECS[kind]["closing"],
        "status": status,
        "effective_status": effective_status(kind, data, now=now),
        "title": str(data.get("title") or ""),
    }


def dependencies_for(
    cur,
    *,
    owner_user_id: int,
    record_type: str,
    record_id: int,
    now: datetime | None = None,
) -> dict:
    """Both directions for one record, plus whether it is blocked.

    ``blocked`` is derived here and never stored, for the same reason
    ``OVERDUE`` is derived in :func:`effective_status`: a stored flag depends on
    something having run when the blocker closed, and a sweep that stops leaves
    every dependent reporting itself ready to start.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRecordRejected("owner_user_id is required")
    require_records_schema(cur)
    kind, ident, _ = _link_endpoint(
        cur, owner, record_type, record_id, role="source")

    cur.execute(
        f"SELECT target_type, target_id FROM private_record_links "
        f"WHERE owner_user_id = ? AND link_type = ? "
        f"AND source_type = ? AND source_id = ? ORDER BY id",
        (owner, LINK_DEPENDS_ON, kind, ident),
    )
    blockers = [
        _describe_endpoint(cur, owner, str(d["target_type"]),
                           int(d["target_id"]), now=now)
        for d in (dict(r) for r in (cur.fetchall() or ()))
    ]

    cur.execute(
        f"SELECT source_type, source_id FROM private_record_links "
        f"WHERE owner_user_id = ? AND link_type = ? "
        f"AND target_type = ? AND target_id = ? ORDER BY id",
        (owner, LINK_DEPENDS_ON, kind, ident),
    )
    dependents = [
        _describe_endpoint(cur, owner, str(d["source_type"]),
                           int(d["source_id"]), now=now)
        for d in (dict(r) for r in (cur.fetchall() or ()))
    ]

    open_blockers = [b for b in blockers if b["open"]]
    return {
        "record_type": kind,
        "record_id": ident,
        "depends_on": blockers,
        "blocks": dependents,
        "blocked": bool(open_blockers),
        "open_blocker_count": len(open_blockers),
    }


def _blocker_counts(cur, owner: int, source_kind: str | None) -> dict[str, dict[int, int]]:
    """Open-blocker counts, keyed by source type then source id.

    One query per *target* type — five, fixed — whether the caller wants one
    source type or all of them. The obvious shape is a query per
    (source, target) pair, which is twenty-five, and the read model calls this
    once per type, so that shape costs thirty queries on a dashboard that is
    otherwise thirty in total. Grouping by ``source_type`` in SQL and bucketing
    in Python answers the same question for a fifth of the round trips.

    A target type has to be joined separately because each primitive lives in
    its own table with its own closing vocabulary; that is the irreducible five.

    Not ``DISTINCT``: a record waiting on three separate requests is waiting on
    three things, and collapsing that to "blocked" throws away the number the
    member most wants — whether clearing one blocker will actually free it.
    """
    if owner <= 0:
        return {}
    require_records_schema(cur)
    counts: dict[str, dict[int, int]] = {}
    for target_kind in linkable_types():
        closing = SPECS[target_kind]["closing"]
        if not closing:
            continue
        placeholders = ", ".join("?" for _ in closing)
        # The optional clause and its parameter are built together, and both go
        # in ahead of the variable-length status list. An optional placeholder
        # appended after a `NOT IN (?, ?)` silently shifts every status by one
        # and the query still runs, just against the wrong values.
        clause = "" if source_kind is None else "AND l.source_type = ? "
        params: list = [owner, LINK_DEPENDS_ON, target_kind]
        if source_kind is not None:
            params.append(source_kind)
        params.append(LIFECYCLE_ACTIVE)
        params.extend(closing)
        cur.execute(
            f"SELECT l.source_type AS source_type, l.source_id AS source_id, "
            f"       COUNT(*) AS n "
            f"FROM private_record_links l "
            f"JOIN {SPECS[target_kind]['table']} t "
            f"  ON t.id = l.target_id AND t.owner_user_id = l.owner_user_id "
            f"WHERE l.owner_user_id = ? AND l.link_type = ? "
            f"AND l.target_type = ? "
            f"{clause}"
            f"AND t.lifecycle_state = ? "
            f"AND t.status NOT IN ({placeholders}) "
            f"GROUP BY l.source_type, l.source_id",
            tuple(params),
        )
        for row in cur.fetchall() or ():
            data = dict(row)
            bucket = counts.setdefault(str(data["source_type"]), {})
            source = int(data["source_id"])
            bucket[source] = bucket.get(source, 0) + int(data["n"])
    return counts


def open_blocker_counts(cur, *, owner_user_id: int, record_type: str) -> dict[int, int]:
    """How many open blockers each of this owner's ``record_type`` rows has."""
    kind = str(record_type or "").strip().upper()
    if kind not in SPECS:
        return {}
    return _blocker_counts(cur, int(owner_user_id or 0), kind).get(kind, {})


def open_blocker_counts_all(cur, *, owner_user_id: int) -> dict[str, dict[int, int]]:
    """The same, for every source type at once, in the same five queries.

    The read model classifies all six primitives in one pass, so asking per type
    would multiply a fixed cost by six for an answer the single call already
    contains.
    """
    return _blocker_counts(cur, int(owner_user_id or 0), None)


def blocked_record_ids(cur, *, owner_user_id: int, record_type: str) -> set[int]:
    """Ids of this owner's ``record_type`` rows that have an open blocker.

    Defined in terms of :func:`open_blocker_counts` rather than as a second
    query. Two readers answering the same question by different routes is
    exactly how the revision bug got in — ``dependencies_for`` and this function
    disagreed about superseded rows because one filtered on lifecycle and the
    other did not. A set that is literally the keys of the count map cannot
    drift from it.
    """
    return set(open_blocker_counts(
        cur, owner_user_id=owner_user_id, record_type=record_type))


def count_open(cur, *, record_type: str, owner_user_id: int) -> int:
    """How many active, non-closing records this owner has. Owner-scoped."""
    spec = _spec(record_type)
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return 0
    require_records_schema(cur)
    open_statuses = [s for s in spec["statuses"] if s not in spec["closing"]]
    if not open_statuses:
        open_statuses = list(spec["statuses"])
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {spec['table']} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ? "
        f"AND status IN ({', '.join('?' for _ in open_statuses)})",
        tuple([owner, LIFECYCLE_ACTIVE] + open_statuses),
    )
    row = cur.fetchone()
    if row is None:
        return 0
    return int(row["n"] if hasattr(row, "keys") else row[0])
