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

Route and UNDX wiring are deferred
----------------------------------
Status: **ROUTE_WIRING_DEFERRED_DUE_TO_CONCURRENT_SECURITY_WORK** and
**UNDX_WIRING_DEFERRED_DUE_TO_CONCURRENT_SECURITY_WORK**.

The service layer is complete: six primitives, one canonical writer each, typed
views behind :func:`retrieval.retrieve_records`, and a scoped test gate over all
of it. What is *not* done is the last hop to the two callers — the HTTP route
pack and the UNDX capability registry — because both are gated by the Private
Office security boundary the concurrent mission is still moving, and neither can
be wired correctly against a contract that has not settled.

What matters is the shape of the deferral. There is no temporary route and no
second executor table "just for now": a parallel surface is a surface nobody
gates, and it outlives the temporary. The UNDX side is declared in one place,
:mod:`services.private_office.undx_records_spec`, which registers nothing and
carries ``WIRING_COMPLETE = False``; while that flag is False the suite asserts
the six capabilities are *absent* from all three authorization surfaces, so
"deferred" cannot quietly become "forgotten" or "half-registered". Wiring is
then three edits in the files that own registration, plus flipping the flag —
and the same test inverts into a presence check, so the flag cannot be flipped
without the registration being real.

Until then, every write and every read of these six goes through this module and
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

RECORD_TYPES: tuple[str, ...] = (
    TYPE_OBLIGATION, TYPE_EVENT, TYPE_DECISION,
    TYPE_REQUEST, TYPE_RISK, TYPE_OPPORTUNITY,
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

#: Derived obligation states. Never stored — see the module docstring.
DERIVED_DUE_SOON = "DUE_SOON"
DERIVED_OVERDUE = "OVERDUE"
DUE_SOON_WINDOW = timedelta(days=14)

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
        ),
        "required": ("title", "obligation_type"),
        "identity": ("obligation_type", "title", "due_at"),
        "indexes": ("due_at",),
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
        ),
        "required": ("question",),
        # Identity is the question alone. Two rows asking the same thing are one
        # decision seen twice; the summary and assumptions around it are what
        # revision is for.
        "identity": ("question",),
        "indexes": ("deadline_at",),
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
        ),
        "required": ("risk_type", "summary"),
        "identity": ("risk_type", "summary"),
        "indexes": ("severity",),
        "enums": {"severity": SEVERITIES, "coverage_state": COVERAGE_STATES},
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
        ),
        "required": ("title", "opportunity_type"),
        "identity": ("opportunity_type", "title"),
        "indexes": (),
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


TABLES: tuple[str, ...] = tuple(SPECS[k]["table"] for k in RECORD_TYPES)

_SCHEMA_READY = False


def reset_records_schema_cache() -> None:
    """Forget the success cache. For tests, and for anyone who dropped a table."""
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_records_schema(cur, *, force: bool = False) -> dict:
    """Create the six tables and their indexes. Never raises.

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

    for record_type in RECORD_TYPES:
        spec = SPECS[record_type]
        try:
            present = db_module.get_table_columns(cur, spec["table"])
        except Exception as exc:
            LOGGER.exception("PRIVATE_RECORDS_ENSURE_FAILED table=%s", spec["table"])
            return {"status": "error", "tables": [], "missing": [],
                    "error": f"{spec['table']}: {str(exc)[:400]}", "cached": False}
        absent = [name for name in _column_names(spec) if name not in present]
        if absent or not present:
            missing.append(f"{spec['table']}:{','.join(absent) or 'absent'}")

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
def effective_status(record_type: str, row: dict, *, now: datetime | None = None) -> str:
    """What is true right now, as opposed to what somebody last decided.

    Only obligations have a derived state today, and only while they are open:
    a resolved obligation with a past due date is resolved, not overdue.
    """
    stored = str(row.get("status") or "")
    if record_type != TYPE_OBLIGATION or stored != "OPEN":
        return stored
    due = _iso(row.get("due_at"), default=None)
    if not due:
        return stored
    try:
        candidate = due[:-1] + "+00:00" if due.endswith("Z") else due
        moment = datetime.fromisoformat(candidate) if len(candidate) > 10 else datetime.fromisoformat(candidate + "T00:00:00")
    except ValueError:
        return stored
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    reference = now or _now()
    if moment < reference:
        return DERIVED_OVERDUE
    if moment - reference <= DUE_SOON_WINDOW:
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
    for name, _ddl, kind, required in spec["extra"]:
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
            values[name] = _enum(raw, allowed, name, allowed[0])
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


#: Fields :func:`update_record` will move. Anything else is a change to the
#: substance of the record and belongs in :func:`revise_record`, which keeps the
#: old version. This tuple is the enforcement, not a docstring: a caller passing
#: ``question=`` to ``update_record`` gets a rejection rather than a silently
#: rewritten decision log.
UPDATABLE: tuple[str, ...] = (
    "status", "outcome", "assigned_provider_id", "severity",
    "coverage_state", "review_required", "priority",
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

    enums = spec.get("enums") or {}
    assignments: list[str] = []
    params: list = []
    now_iso = _now_iso()

    if "status" in fields:
        status = _enum(fields["status"], spec["statuses"], "status", spec["default_status"])
        assignments.append("status = ?")
        params.append(status)
        if status in spec["closing"]:
            assignments.append("closed_at = ?")
            params.append(now_iso)
        else:
            # Reopening clears the closure stamp. A record that is OPEN and
            # carries a resolved_at is a row two readers will read two ways.
            assignments.append("closed_at = ?")
            params.append(None)

    extra_kinds = {name: kind_ for name, _ddl, kind_, _req in spec["extra"]}
    for name, value in fields.items():
        if name == "status":
            continue
        if name not in extra_kinds:
            raise PrivateRecordRejected(f"{name} does not exist on {kind}")
        column_kind = extra_kinds[name]
        if column_kind == "enum":
            allowed = enums[name]
            resolved: object = _enum(value, allowed, name, allowed[0])
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
        action=_audit.ACTION_RECORD_UPDATE, object_type=spec["audit_object"],
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

    if due_before and "due_at" in _column_names(spec):
        boundary = _iso(due_before, default=None)
        if boundary:
            clauses.append("due_at IS NOT NULL AND due_at <= ?")
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
