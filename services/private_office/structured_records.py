"""The structured record store — envelope, typed field projection, history.

What this is for
----------------
Private Facts today stores one assertion per row: a domain, a fact type, a
value type and a value. That shape is correct for *a fact* and wrong for *a
document*. A passport is not one assertion; it is nineteen, of five different
kinds, with four different sensitivities, three of which drive a renewal date,
one of which must never reach a language model, and two of which must never be
returned to any screen in plaintext. Recording it as nineteen loose facts loses
the one thing that makes it a passport: that they belong together, were entered
together, expire together, and are governed together.

So this module adds a layer *above* the fact store rather than replacing it.
:mod:`services.private_office.facts` keeps its job — atomic, provenance-carrying
assertions, which is what the Capital Graph, the contradiction engine and
retrieval already read. A structured record is the composite: an envelope that
names which template shaped it, plus one row per field, already typed, already
carrying its own sensitivity, already knowing whether it may be indexed.

Why not one JSON column
-----------------------
The obvious implementation is ``payload_json TEXT`` on a single table. It is
also the implementation that cannot answer any of the questions this system
exists to answer:

* *Field-level* privacy is impossible. A JSON blob has exactly one sensitivity,
  so either the whole passport is RESTRICTED — and the expiry date, which the
  calendar needs, becomes unreadable — or none of it is, and the document
  number is in every row a list query returns.
* Search has to load and parse every record to find one. Worse, the naive fix
  is to index the blob, which indexes the document number along with it.
* "Which records expire in the next 90 days" becomes a table scan and a parse,
  so the reminder sweep either gets slow or gets cached, and a cached expiry is
  a reminder that stops arriving without anything failing.
* A migration cannot find the rows it needs to change, because the field it is
  moving is not a column anywhere.
* Redaction has nothing to redact *around*. Handing UNDX "the record" means
  handing it the blob.

:data:`FIELDS_TABLE_DDL` is the answer to all five: the projection is not a
denormalized copy of the truth, it *is* the truth. There is no blob to drift
from.

Three tables
------------
``private_structured_records``
    The envelope the mission specifies — owner, office, template key and
    version, title, status, sensitivity ceiling, verification state, source and
    provenance, evidence references, effective/expiry/review dates, tags,
    revision counter, actors and timestamps. It holds no field values.

``private_record_fields``
    One row per submitted field, shaped exactly like
    :class:`~services.private_office.record_templates.FieldValue`, plus the
    three columns a reader actually consumes: ``masked_text`` (what a screen
    shows before step-up), ``search_text`` (what the index may hold, empty when
    the field is not searchable) and ``cipher_text`` (RESTRICTED values at
    rest). A RESTRICTED field stores nothing in ``value_text`` — see below.

``private_record_revisions``
    What changed, when, and who changed it. **Never what it changed from.**

Two rules that are structural here, not remembered
--------------------------------------------------
*A RESTRICTED value has no plaintext column to live in.* ``value_text`` exists
for values at CONFIDENTIAL and below; the writer puts a RESTRICTED value in
``cipher_text`` or refuses the write. That is enforced in the writer (Stage 5),
but the schema is arranged so that the mistake is visible rather than silent:
there is a ``cipher_key_id`` beside the ciphertext, so a row claiming to be
encrypted with no key id is a row a check can find.

*History stores paths, not values.* ``private_record_revisions`` records
``changed_paths`` — ``issuance.document_number`` — and no before/after values.
The tempting version of this table keeps the old value "for undo", and the
result is a second copy of every passport number the member ever corrected,
living in a table with none of the masking, none of the encryption and none of
the step-up gating that protects the first copy. An audit trail that leaks the
thing it is auditing is worse than no audit trail, because it is trusted.

Verification is never earned by machine
---------------------------------------
:data:`VERIFICATION_STATES` are the ten the mission names.
:data:`MACHINE_WRITABLE_VERIFICATION` is the subset a non-human actor may
write, and it excludes all three verified states. Document extraction and UNDX
suggestion land in ``DOCUMENT_EXTRACTED_NEEDS_REVIEW`` and
``UNDX_SUGGESTED_NEEDS_REVIEW``; nothing but a human confirmation moves a
record out of them. This is the "never auto-convert extracted or inferred data
into verified truth" rule, expressed as a set rather than as a review comment.

Schema ownership
----------------
The DDL lives here and is applied by :func:`ensure_structured_schema`, which
follows the same contract as ``schema.ensure_private_schema`` and
``records.ensure_records_schema``: idempotent, never raises, three outcomes,
caches only success. ``schema.py`` remains the owner of the six original
tables; adding these three there would put the module that decides whether the
database is usable in the path of every mission that touches records.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone

from services.private_office import audit as _audit
from services.private_office import field_crypto as _crypto
from services.private_office import model as _model
from services.private_office import record_templates as _templates
from services.private_office import records as _records

LOGGER = logging.getLogger("private_office.structured_records")


class StructuredRecordRejected(ValueError):
    """A write that would break an invariant. Never a database failure.

    Same split as :class:`~services.private_office.records.PrivateRecordRejected`
    and for the same reason: "you asked for something incoherent" and "the store
    could not be reached" must not share an exception, or a caller retries the
    first one forever.
    """


class StructuredSchemaMissing(RuntimeError):
    """The tables are not usable. Distinct from a rejected write."""


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------
#: Where the record came from. Reused rather than re-declared: the package
#: already has exactly one list of source types, and a second one would drift
#: within a release — which is the failure `model.py` was written to end.
SOURCE_TYPES: tuple[str, ...] = _records.SOURCE_TYPES
DERIVED_SOURCES: frozenset[str] = _records.DERIVED_SOURCES

#: How much anyone should believe this record. The ten states the mission
#: names, in descending order of authority.
VERIFICATION_USER_VERIFIED = "USER_VERIFIED"
VERIFICATION_SOURCE_VERIFIED = "SOURCE_VERIFIED"
VERIFICATION_PROVIDER_VERIFIED = "PROVIDER_VERIFIED"
VERIFICATION_DOCUMENT_EXTRACTED = "DOCUMENT_EXTRACTED_NEEDS_REVIEW"
VERIFICATION_UNDX_SUGGESTED = "UNDX_SUGGESTED_NEEDS_REVIEW"
VERIFICATION_INFERRED = "INFERRED"
VERIFICATION_STALE = "STALE"
VERIFICATION_DISPUTED = "DISPUTED"
VERIFICATION_REJECTED = "REJECTED"
VERIFICATION_ARCHIVED = "ARCHIVED"

VERIFICATION_STATES: tuple[str, ...] = (
    VERIFICATION_USER_VERIFIED,
    VERIFICATION_SOURCE_VERIFIED,
    VERIFICATION_PROVIDER_VERIFIED,
    VERIFICATION_DOCUMENT_EXTRACTED,
    VERIFICATION_UNDX_SUGGESTED,
    VERIFICATION_INFERRED,
    VERIFICATION_STALE,
    VERIFICATION_DISPUTED,
    VERIFICATION_REJECTED,
    VERIFICATION_ARCHIVED,
)

#: States that mean somebody with standing has actually confirmed the content.
#: Only these three may be quoted without a qualifier.
VERIFIED_STATES: frozenset[str] = frozenset({
    VERIFICATION_USER_VERIFIED,
    VERIFICATION_SOURCE_VERIFIED,
    VERIFICATION_PROVIDER_VERIFIED,
})

#: States that mean "a machine produced this and a human has not looked".
NEEDS_REVIEW_STATES: frozenset[str] = frozenset({
    VERIFICATION_DOCUMENT_EXTRACTED,
    VERIFICATION_UNDX_SUGGESTED,
    VERIFICATION_INFERRED,
})

#: States a non-human actor is permitted to write. The three verified states
#: are absent on purpose and their absence is the enforcement: an extractor
#: that "recognised the passport with high confidence" still cannot write
#: ``USER_VERIFIED``, because confidence is not the same thing as somebody
#: having looked.
MACHINE_WRITABLE_VERIFICATION: frozenset[str] = frozenset(
    VERIFICATION_STATES) - VERIFIED_STATES

#: What a record entered by hand starts as. The member typed it, so
#: ``USER_VERIFIED`` is the honest description; it claims nothing about whether
#: the underlying document says the same thing, which is what
#: ``SOURCE_VERIFIED`` and ``PROVIDER_VERIFIED`` are for.
DEFAULT_VERIFICATION = VERIFICATION_USER_VERIFIED

#: Why a revision exists. A closed vocabulary rather than free text, for the
#: reason given in the module docstring: a "note" column on a history table
#: eventually holds a policy number.
CHANGE_CREATED = "CREATED"
CHANGE_UPDATED = "UPDATED"
CHANGE_STATUS_CHANGED = "STATUS_CHANGED"
CHANGE_VERIFICATION_CHANGED = "VERIFICATION_CHANGED"
CHANGE_EVIDENCE_ATTACHED = "EVIDENCE_ATTACHED"
CHANGE_TEMPLATE_MIGRATED = "TEMPLATE_MIGRATED"
CHANGE_IMPORTED = "IMPORTED"
CHANGE_ARCHIVED = "ARCHIVED"
CHANGE_RESTORED = "RESTORED"
CHANGE_REVEALED = "REVEALED"

CHANGE_TYPES: tuple[str, ...] = (
    CHANGE_CREATED,
    CHANGE_UPDATED,
    CHANGE_STATUS_CHANGED,
    CHANGE_VERIFICATION_CHANGED,
    CHANGE_EVIDENCE_ATTACHED,
    CHANGE_TEMPLATE_MIGRATED,
    CHANGE_IMPORTED,
    CHANGE_ARCHIVED,
    CHANGE_RESTORED,
    CHANGE_REVEALED,
)

LIFECYCLE_STATES: tuple[str, ...] = _model.LIFECYCLE_STATES

MAX_TITLE = 200
MAX_SUMMARY = 400
MAX_DESCRIPTION = 4000
MAX_TAGS = 20
MAX_TAG = 40
MAX_EVIDENCE_REFS = 25
MAX_CHANGED_PATHS = 60
MAX_RECORD_KEY = 64
MAX_ACTOR = 64
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------
def normalize_verification(value: object) -> str | None:
    """Canonical verification state, or ``None``.

    Returns rather than raises, and never defaults — the same discipline as
    ``model.normalize_sensitivity``, for the sharper version of the same
    reason. Defaulting an unrecognised state downward marks a provider-confirmed
    record as a guess; defaulting it upward marks a guess as confirmed. The
    second one is how a system starts telling members things it does not know.
    """
    text = str(value or "").strip().upper()
    return text if text in VERIFICATION_STATES else None


def normalize_change_type(value: object) -> str | None:
    """Canonical revision change type, or ``None``."""
    text = str(value or "").strip().upper()
    return text if text in CHANGE_TYPES else None


def normalize_source_type(value: object) -> str | None:
    """Canonical source type, or ``None``."""
    text = str(value or "").strip().upper()
    return text if text in SOURCE_TYPES else None


def machine_may_write(verification: object) -> bool:
    """May a non-human actor write this verification state?

    ``False`` for anything unrecognised, so a typo cannot buy an extractor a
    state the rules would have denied it.
    """
    state = normalize_verification(verification)
    return bool(state) and state in MACHINE_WRITABLE_VERIFICATION


def is_verified(verification: object) -> bool:
    """Has somebody with standing confirmed this? Unknown is ``False``."""
    state = normalize_verification(verification)
    return bool(state) and state in VERIFIED_STATES


def needs_review(verification: object) -> bool:
    """Is this waiting for a human to look at it? Unknown is ``False``."""
    state = normalize_verification(verification)
    return bool(state) and state in NEEDS_REVIEW_STATES


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Written once in SQLite dialect. `services.db` rewrites
# `INTEGER PRIMARY KEY AUTOINCREMENT` to `SERIAL PRIMARY KEY` for PostgreSQL,
# which is what lets the DDL below be literal and still portable. Nothing here
# uses `INSERT OR IGNORE`, which means two different things on the two engines.
#
# Table names are spelled out rather than interpolated from constants. The
# static write-boundary guard in `tests/private_office/test_private_write_boundary.py`
# matches table-name tokens inside string literals, so a constant would make
# every statement in this module invisible to it — and a guard that cannot fail
# is evidence of nothing.

RECORDS_TABLE = "private_structured_records"
FIELDS_TABLE = "private_record_fields"
REVISIONS_TABLE = "private_record_revisions"

TABLES: tuple[str, ...] = (RECORDS_TABLE, FIELDS_TABLE, REVISIONS_TABLE)

RECORDS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS private_structured_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    office_id TEXT NOT NULL DEFAULT '',
    record_key TEXT NOT NULL,
    template_key TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    contract_version INTEGER NOT NULL,
    domain TEXT NOT NULL,
    ia_domain TEXT NOT NULL,
    title TEXT NOT NULL,
    summary_text TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',
    sensitivity TEXT NOT NULL,
    verification_state TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    provenance_type TEXT NOT NULL DEFAULT '',
    evidence_ids TEXT NOT NULL DEFAULT '',
    graph_node_id INTEGER,
    effective_date TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    review_at TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    reminder_policy TEXT NOT NULL DEFAULT '',
    undx_readable INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1,
    supersedes_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    archived_at TEXT,
    UNIQUE(owner_user_id, record_key)
)
"""

# `summary_text` is the one-line description a list renders — "United States •
# ending 1234 • expires Dec 2030". It is written by the writer from *masked*
# field values, never from raw ones, which is why it is a stored column rather
# than something a list query assembles: assembling it at read time means every
# list query has to load the RESTRICTED fields in order to mask them, and a
# query that loads a secret in order not to show it is one bug away from
# showing it.
#
# `evidence_ids` and `tags` are comma-joined normalized tokens, matching
# `records.related_document_ids`. They are ids and labels, never values; the
# writer validates them id-shaped for the same reason `audit.safe_object_id`
# does, because a "reference" field with no shape check becomes a place to put
# a policy number.

FIELDS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS private_record_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    record_id INTEGER NOT NULL,
    record_key TEXT NOT NULL,
    template_key TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    field_path TEXT NOT NULL,
    field_kind TEXT NOT NULL,
    value_type TEXT NOT NULL,
    value_text TEXT NOT NULL DEFAULT '',
    value_number REAL,
    value_date TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT '',
    sensitivity TEXT NOT NULL,
    mask TEXT NOT NULL,
    searchable INTEGER NOT NULL DEFAULT 0,
    masked_text TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT '',
    is_encrypted INTEGER NOT NULL DEFAULT 0,
    cipher_text TEXT NOT NULL DEFAULT '',
    cipher_key_id TEXT NOT NULL DEFAULT '',
    identity_field INTEGER NOT NULL DEFAULT 0,
    expires_record INTEGER NOT NULL DEFAULT 0,
    undx_readable INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_user_id, record_id, field_path)
)
"""

# `owner_user_id` is denormalized onto every field row deliberately. The
# alternative is a join to the envelope on every read, which means the owner
# predicate lives in the join rather than in the field query — and the first
# time somebody writes a field query without the join, it returns every
# member's fields and looks like it worked. Here the owner clause is available
# to, and required by, every statement that touches the table, and every index
# below leads with it.
#
# The three read columns are separate on purpose:
#   `value_text`   the real value. Absent for RESTRICTED fields.
#   `masked_text`  what a screen may render before step-up.
#   `search_text`  what the index may hold; '' when `searchable` is 0.
# They are not derivable from one another at read time without the template,
# and a reader that has to consult the template to know whether it is allowed
# to show what it just loaded has already loaded it.

REVISIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS private_record_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    record_id INTEGER NOT NULL,
    record_key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    changed_paths TEXT NOT NULL DEFAULT '',
    template_key TEXT NOT NULL,
    template_version_from INTEGER,
    template_version_to INTEGER,
    status_from TEXT NOT NULL DEFAULT '',
    status_to TEXT NOT NULL DEFAULT '',
    verification_from TEXT NOT NULL DEFAULT '',
    verification_to TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    reason_code TEXT NOT NULL DEFAULT '',
    actor_user_id INTEGER,
    actor_kind TEXT NOT NULL DEFAULT '',
    audit_event_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(owner_user_id, record_id, revision)
)
"""

# There is no `value_before` column and there will not be one. See the module
# docstring: an undo history of a passport number is a second, unprotected copy
# of the passport number. `changed_paths` names the fields; the current value
# lives in `private_record_fields` under the protections that belong to it.
#
# `private_record_revisions.revision` is a per-record *history sequence* and is
# deliberately not the same number as the envelope's `revision`, which counts
# only changes to content. The two diverge the first time a member reveals a
# masked field: that advances the history — it is the entry a member most needs
# to see when asking "who has looked at this" — without changing the record, so
# an envelope counter would have had to either lie or skip. One column meaning
# two things is how a history stops being trustworthy, so they are two columns.

TABLE_DDL: dict[str, str] = {
    RECORDS_TABLE: RECORDS_TABLE_DDL,
    FIELDS_TABLE: FIELDS_TABLE_DDL,
    REVISIONS_TABLE: REVISIONS_TABLE_DDL,
}

#: Columns added after the first release. Empty today; the loop in
#: :func:`ensure_structured_schema` exists so the first person who needs a
#: column does not have to invent the mechanism, and so it lands in ``ensure``
#: rather than in a route handler.
TABLE_ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    RECORDS_TABLE: (),
    FIELDS_TABLE: (),
    REVISIONS_TABLE: (),
}

#: Without these the store cannot answer its own questions, so their absence is
#: reported as "could not look" rather than "looked and found nothing".
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    RECORDS_TABLE: (
        "owner_user_id", "office_id", "record_key", "template_key",
        "template_version", "contract_version", "domain", "ia_domain", "title",
        "status", "lifecycle_state", "sensitivity", "verification_state",
        "source_type", "expires_at", "revision", "created_at", "updated_at",
    ),
    FIELDS_TABLE: (
        "owner_user_id", "record_id", "record_key", "template_key",
        "template_version", "field_path", "field_kind", "value_type",
        "value_text", "value_number", "value_date", "sensitivity", "mask",
        "searchable", "masked_text", "search_text", "is_encrypted",
        "cipher_text", "cipher_key_id", "created_at",
    ),
    REVISIONS_TABLE: (
        "owner_user_id", "record_id", "record_key", "revision", "change_type",
        "changed_paths", "template_key", "created_at",
    ),
}

# Every index leads with `owner_user_id`. Not a stylistic preference: the owner
# predicate is the first clause of every read this module will issue, so an
# index that does not start there cannot serve them, and the query degrades to
# a scan of every member's rows — which is both the performance problem and,
# on a shared table, the shape of query you least want to be cheap.
INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_structured_records_owner_template "
    "ON private_structured_records (owner_user_id, template_key, lifecycle_state)",
    # "What is in this domain" — the Private Facts home's category drill-down.
    "CREATE INDEX IF NOT EXISTS idx_structured_records_owner_domain "
    "ON private_structured_records (owner_user_id, ia_domain, lifecycle_state)",
    # "What expires soon" — the reminder sweep and the home screen's expiring
    # section. Without this the sweep is a full scan on every interval, which
    # is the pressure that leads somebody to cache expiry dates, and a cached
    # expiry is a reminder that stops arriving with nothing visibly failing.
    "CREATE INDEX IF NOT EXISTS idx_structured_records_owner_expiry "
    "ON private_structured_records (owner_user_id, expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_structured_records_owner_review "
    "ON private_structured_records (owner_user_id, review_at)",
    # "What needs review" — the queue that keeps extracted and suggested
    # records from silently becoming truth.
    "CREATE INDEX IF NOT EXISTS idx_structured_records_owner_verification "
    "ON private_structured_records (owner_user_id, verification_state)",
    "CREATE INDEX IF NOT EXISTS idx_structured_records_owner_updated "
    "ON private_structured_records (owner_user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_record_fields_owner_record "
    "ON private_record_fields (owner_user_id, record_id, field_path)",
    # Search reads this one. It leads with owner and includes `searchable` so a
    # non-indexable field is excluded by the index itself rather than by a
    # filter the query author has to remember.
    "CREATE INDEX IF NOT EXISTS idx_record_fields_owner_search "
    "ON private_record_fields (owner_user_id, searchable, template_key, field_path)",
    # Duplicate detection: "does this member already have a passport whose
    # number ends 1234". Asked against `masked_text`, never against the value.
    "CREATE INDEX IF NOT EXISTS idx_record_fields_owner_identity "
    "ON private_record_fields (owner_user_id, template_key, identity_field, masked_text)",
    "CREATE INDEX IF NOT EXISTS idx_record_revisions_owner_record "
    "ON private_record_revisions (owner_user_id, record_id, revision)",
    "CREATE INDEX IF NOT EXISTS idx_record_revisions_owner_created "
    "ON private_record_revisions (owner_user_id, created_at)",
)


_SCHEMA_READY = False


def reset_structured_schema_cache() -> None:
    """Forget the success cache. For tests, and for anyone who dropped a table."""
    global _SCHEMA_READY
    _SCHEMA_READY = False


def _empty_result(status: str, **extra) -> dict:
    result = {
        "status": status,
        "tables": list(TABLES),
        "missing": [],
        "added": [],
        "error": None,
        "cached": False,
    }
    result.update(extra)
    return result


def ensure_structured_schema(cur, *, force: bool = False) -> dict:
    """Create the three tables, their added columns and their indexes.

    Never raises. ``status`` is one of:

    ``ready``    every required column on every table is present.
    ``missing``  the ensure completed and required columns are still absent —
                 e.g. the role cannot ``ALTER``. Callers must not read or write.
    ``error``    the ensure itself failed: locked, unreachable, no permission.

    Only success is cached, so a database that heals is noticed without a
    restart. A cached failure would turn a transient lock into an outage
    lasting until somebody redeployed.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return _empty_result("ready", cached=True)

    from services import db as db_module

    for table, ddl in TABLE_DDL.items():
        try:
            cur.execute(ddl)
        except Exception as exc:
            # Not fatal on its own: the overwhelmingly common case is that the
            # table already exists and this is a no-op, and a
            # `CREATE TABLE IF NOT EXISTS` that raises anyway — a concurrent
            # creator, a role with ALTER but not CREATE — should still let the
            # column check below decide whether the schema is usable.
            LOGGER.warning(
                "PRIVATE_STRUCTURED_TABLE_DDL_FAILED table=%s error=%s", table, exc)

    present: dict[str, set[str]] = {}
    for table in TABLES:
        try:
            present[table] = set(db_module.get_table_columns(cur, table))
        except Exception as exc:
            LOGGER.exception(
                "PRIVATE_STRUCTURED_ENSURE_FAILED stage=introspect table=%s", table)
            return _empty_result("error", error=f"{table}: {str(exc)[:400]}")

    added: list[str] = []
    for table, columns in TABLE_ADDED_COLUMNS.items():
        for column, definition in columns:
            if column in present.get(table, set()):
                continue
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                added.append(f"{table}.{column}")
            except Exception:
                # A concurrent process may have added it between the
                # introspection and here — the web process and a worker can
                # ensure at the same instant, and losing that race is the
                # correct outcome. Anything else shows up below as a column
                # that is still missing.
                LOGGER.exception(
                    "PRIVATE_STRUCTURED_COLUMN_ADD_FAILED table=%s column=%s", table, column)

    for statement in INDEX_DDL:
        try:
            cur.execute(statement)
        except Exception as exc:
            # A missing index makes a read slow; a missing column makes it
            # impossible. Only the second one blocks.
            LOGGER.warning("PRIVATE_STRUCTURED_INDEX_FAILED error=%s", exc)

    if added:
        for table in TABLES:
            try:
                present[table] = set(db_module.get_table_columns(cur, table))
            except Exception as exc:
                LOGGER.exception(
                    "PRIVATE_STRUCTURED_ENSURE_FAILED stage=verify table=%s", table)
                return _empty_result(
                    "error", added=added, error=f"{table}: {str(exc)[:400]}")

    missing: list[str] = []
    for table in TABLES:
        columns = present.get(table, set())
        absent = [name for name in REQUIRED_COLUMNS[table] if name not in columns]
        if absent or not columns:
            missing.append(f"{table}:{','.join(absent) or 'absent'}")

    if missing:
        LOGGER.error("PRIVATE_STRUCTURED_SCHEMA_MISSING tables=%s", ";".join(missing))
        return _empty_result("missing", missing=missing, added=added)

    _SCHEMA_READY = True
    LOGGER.info(
        "PRIVATE_STRUCTURED_SCHEMA_READY tables=%s added=%s",
        ",".join(f"{t}({len(present[t])})" for t in TABLES),
        ",".join(added) or "-",
    )
    return _empty_result("ready", added=added)


def require_structured_schema(cur, *, force: bool = False) -> dict:
    """:func:`ensure_structured_schema`, but raise when the result is unusable.

    The writers and readers call this. They have no honest degraded answer:
    returning ``[]`` from a store that could not be reached would make "you have
    no passport on file" and "we could not look" the same response — and the
    first of those is what a member acts on.
    """
    result = ensure_structured_schema(cur, force=force)
    if result["status"] == "ready":
        return result
    detail = result.get("error") or ";".join(result.get("missing") or ())
    raise StructuredSchemaMissing(
        f"structured record schema unusable: {result['status']} {detail}".strip()
    )


# ---------------------------------------------------------------------------
# Contract check
# ---------------------------------------------------------------------------
def field_row_columns() -> tuple[str, ...]:
    """The ``private_record_fields`` columns a
    :class:`~services.private_office.record_templates.FieldValue` supplies.

    Exists so the coupling between the validated value object and the table it
    is written to is a thing a test can assert rather than a thing a reviewer
    has to notice. The table was designed around ``FieldValue``; if a field is
    added there and not here, the write silently drops it.
    """
    return (
        "field_path", "field_kind", "value_type", "value_text", "value_number",
        "value_date", "currency", "sensitivity", "mask", "searchable",
    )


def field_value_attributes() -> tuple[str, ...]:
    """The ``FieldValue`` attribute names matching :func:`field_row_columns`."""
    return (
        "path", "kind", "value_type", "value_text", "value_number",
        "value_date", "currency", "sensitivity", "mask", "searchable",
    )


#: The template contract version this store was built against. A record carries
#: the version that wrote it (``contract_version``) so a later migration can
#: find the rows written before a field moved — the manifest format and the
#: per-template version answer two different questions and neither substitutes
#: for the other.
CONTRACT_VERSION = _templates.CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------
#: Who is writing. Not decoration: :func:`_check_verification` uses it to decide
#: whether the claimed verification state is one this actor is allowed to make,
#: and the whole "never auto-convert extracted data into verified truth" rule
#: reduces to that one check.
ACTOR_USER = "user"
ACTOR_UNDX = "undx"
ACTOR_EXTRACTION = "extraction"
ACTOR_PROVIDER = "provider"
ACTOR_IMPORT = "import"
ACTOR_SYSTEM = "system"

ACTOR_KINDS: tuple[str, ...] = (
    ACTOR_USER, ACTOR_UNDX, ACTOR_EXTRACTION, ACTOR_PROVIDER, ACTOR_IMPORT,
    ACTOR_SYSTEM,
)

#: The only actor whose assertion of a verified state is accepted. Note that
#: ``provider`` is *not* here. A provider integration that has genuinely
#: confirmed a credential should be able to write ``PROVIDER_VERIFIED``, but the
#: thing that makes it genuine is the attestation it received, not the string it
#: passed to this function — so that path needs to arrive with the attestation
#: and does not exist yet. Until it does, the honest state for a
#: provider-supplied record is one of the needs-review states, and a member
#: confirms it. Widening this set is the change that would let an integration
#: bug mark a whole account's records as confirmed.
HUMAN_ACTORS: frozenset[str] = frozenset({ACTOR_USER})

#: Write outcomes. ``duplicate`` is a refusal to write, not a failure: the
#: record looked like one the member already has, so the decision goes back to
#: them rather than being made here in either direction.
STATUS_CREATED = "created"
STATUS_EXISTING = "existing"
STATUS_UPDATED = "updated"
STATUS_UNCHANGED = "unchanged"
STATUS_DUPLICATE = "duplicate"
#: Validation failed. A returned status rather than a raised exception because
#: the caller needs the per-path errors to render beside the fields that
#: produced them, and an exception carrying a list of structured errors is an
#: exception being used as a return value.
STATUS_INVALID = "invalid"


class StructuredRecordConflict(RuntimeError):
    """The record moved under the caller. Optimistic concurrency, not an error.

    Separate from :class:`StructuredRecordRejected` because the correct client
    response is different: a rejection means "fix what you sent", a conflict
    means "reload and look at what changed". A client that cannot tell them
    apart either retries a bad payload forever or silently overwrites somebody's
    edit, and the second one is how a member's correction disappears.
    """


class StructuredRecordDenied(PermissionError):
    """A read or reveal the caller is not entitled to.

    Raised without saying whether the record exists. See :func:`_load_envelope`.
    """


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,39}$")
_KEY_RE = re.compile(r"^[a-f0-9]{48}$")
_ACTOR_REF_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_dict(row) -> dict:
    """One database row as a plain dict, whatever the driver returned."""
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):  # pragma: no cover - driver without keys()
        return {}


def _text(value: object, cap: int) -> str:
    return str(value if value is not None else "").strip()[:cap]


def _owner(value: object) -> int:
    owner = int(value or 0)
    if owner <= 0:
        raise StructuredRecordRejected("owner_user_id is required")
    return owner


def _tags(value: object) -> str:
    """Canonical, deduped, sorted, comma-joined tags.

    Anything that is not tag-shaped is dropped rather than truncated, on the
    same reasoning as ``records.safe_ref``: a tag field with no shape check is
    the most convenient place in the schema to put a value, and it is one that
    no masking, no encryption and no step-up covers.
    """
    if value is None or value == "":
        return ""
    items = str(value).split(",") if isinstance(value, (str, bytes)) else list(value)
    tags = {t for t in (str(i or "").strip().lower() for i in items) if _TAG_RE.match(t)}
    return ",".join(sorted(tags)[:MAX_TAGS])


def _refs(value: object) -> str:
    """Comma-joined evidence references. Reuses the existing shape check."""
    if value is None or value == "":
        return ""
    items = str(value).split(",") if isinstance(value, (str, bytes)) else list(value)
    refs = {_records.safe_ref(item) for item in items}
    refs.discard("")
    return ",".join(sorted(refs)[:MAX_EVIDENCE_REFS])


def _split(value: object) -> list[str]:
    return [p for p in str(value or "").split(",") if p]


def _actor_kind(value: object) -> str:
    kind = str(value or ACTOR_USER).strip().lower()
    if kind not in ACTOR_KINDS:
        raise StructuredRecordRejected(f"unknown actor kind {value!r}")
    return kind


def record_key(
    *, owner_user_id: int, template_key: str, idempotency_key: object = "",
) -> str:
    """The record's stable public handle.

    Two shapes, one format. With an ``idempotency_key`` the handle is a hash of
    the owner, the template and that key, so a create replayed after a dropped
    response finds the row it already wrote instead of writing a second one.
    Without one it is random.

    Both are 48 hex characters, which matters: a caller cannot tell from the
    handle which path produced it, so nothing downstream can grow a behaviour
    that depends on that. And neither shape is derived from field values, so the
    handle — which appears in URLs, in logs and in the audit table — never
    carries a fragment of a passport number the way a "slug" would.
    """
    owner = _owner(owner_user_id)
    token = str(idempotency_key or "").strip()
    if token:
        material = "\x1f".join(("v1", str(owner), str(template_key or ""), token))
    else:
        material = "\x1f".join(("v1", str(owner), uuid.uuid4().hex, _now_iso()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]


def _resolve_template(template_key: object, template_version: object = None):
    template = _templates.get_template(template_key, template_version)
    if template is None:
        # Named separately from a validation error because the remedies differ:
        # an unknown template usually means a client built against a newer or
        # older manifest, and the fix is to refetch the manifest rather than to
        # correct a field.
        raise StructuredRecordRejected(
            f"unknown record template {str(template_key)[:64]!r}"
            + (f" at version {template_version}" if template_version else "")
        )
    return template


def _check_verification(state: str, actor_kind: str) -> str:
    """The verification state this actor may actually write.

    Refuses rather than downgrades. Silently storing ``INFERRED`` when an
    extractor asked for ``USER_VERIFIED`` would leave the extractor believing it
    had done something it had not, and the next version of it would "fix" the
    discrepancy by writing somewhere else.
    """
    verification = normalize_verification(state)
    if verification is None:
        raise StructuredRecordRejected(f"unknown verification state {state!r}")
    if actor_kind not in HUMAN_ACTORS and verification not in MACHINE_WRITABLE_VERIFICATION:
        raise StructuredRecordRejected(
            f"a {actor_kind} actor may not write verification state {verification}; "
            f"use one of {', '.join(sorted(MACHINE_WRITABLE_VERIFICATION))}"
        )
    return verification


# ---------------------------------------------------------------------------
# Field projection
# ---------------------------------------------------------------------------
def _project_field(
    value: _templates.FieldValue,
    *,
    owner_user_id: int,
    record_key_value: str,
    template,
) -> dict:
    """One :class:`FieldValue` as the columns of ``private_record_fields``.

    This function is the whole field-level privacy model in one place, which is
    the point of it existing rather than the writer doing this inline. Three
    decisions are made here and nowhere else:

    *What a screen may show.* ``masked_text`` is always populated — it equals
    the value for an unmasked field and the masked form otherwise — so every
    read path reads one column and no reader has to remember to branch. A reader
    that branches is a reader that will one day branch wrong, and the wrong
    branch prints the number.

    *What the index may hold.* ``search_text`` comes from
    :func:`~services.private_office.record_templates.search_index_text`, which
    already refuses non-searchable fields and already indexes a masked field on
    its masked form. Not re-derived here, so there is exactly one answer.

    *Where a RESTRICTED value lives.* In ``cipher_text``, encrypted and bound to
    this owner, record and path — and nowhere else. ``value_text`` is emptied,
    and so are ``value_number`` and ``value_date``: an encrypted amount whose
    magnitude is still sitting in a numeric column is not encrypted, it is
    merely inconvenient to read. If no key is configured the write is refused
    outright rather than downgraded, which is the one behaviour that keeps the
    word "encrypted" honest everywhere else it appears.
    """
    spec = template.field_map.get(_templates.template_path(value.path))
    if spec is None:  # pragma: no cover - validate_payload rejects these first
        raise StructuredRecordRejected(f"{value.path} is not a field of {template.key}")

    masked = value.mask != _templates.MASK_NONE
    masked_text = (
        _templates.mask_value(value.mask, value.kind, value.value_text)
        if masked else value.value_text
    )
    search_text = _templates.search_index_text(value)

    row = {
        "field_path": value.path,
        "field_kind": value.kind,
        "value_type": value.value_type,
        "value_text": value.value_text,
        "value_number": value.value_number,
        "value_date": value.value_date or "",
        "currency": value.currency or "",
        "sensitivity": value.sensitivity,
        "mask": value.mask,
        "searchable": 1 if value.searchable else 0,
        "masked_text": masked_text,
        # Lowercased because every query against this column is a
        # case-insensitive prefix match, and doing the fold once at write time
        # is what keeps that query able to use the index it was given.
        "search_text": search_text.lower(),
        "is_encrypted": 0,
        "cipher_text": "",
        "cipher_key_id": "",
        "identity_field": 1 if spec.identity else 0,
        "expires_record": 1 if spec.expires_record else 0,
        "undx_readable": 1 if (spec.undx_readable and template.undx_readable) else 0,
    }

    if value.sensitivity == _model.SENSITIVITY_RESTRICTED:
        if not _crypto.available():
            raise StructuredRecordRejected(
                f"{value.path} is a restricted field and no encryption key is "
                "configured, so it cannot be stored"
            )
        try:
            cipher_text, key_id = _crypto.encrypt(
                value.value_text,
                owner_user_id=owner_user_id,
                record_key=record_key_value,
                field_path=value.path,
            )
        except _crypto.FieldCryptoUnavailable as exc:
            raise StructuredRecordRejected(
                f"{value.path} is a restricted field and cannot be stored: {exc}"
            ) from None
        row.update({
            "value_text": "",
            "value_number": None,
            "value_date": "",
            "is_encrypted": 1,
            "cipher_text": cipher_text,
            "cipher_key_id": key_id,
        })

    return row


def _summary_text(template, rows: list[dict]) -> str:
    """The one line a list renders.

    Built only from fields the template already marked ``searchable``, and from
    their ``masked_text`` rather than their value. That reuses a safety decision
    somebody already made per field instead of inventing a second one here — and
    the second one is the one that would be wrong, because it would be made by
    whoever was writing the list screen.

    A template with no searchable fields gets an empty summary, and the reader
    falls back to the record's title. ``medical_condition`` is exactly that
    case: nothing about a diagnosis is safe in a list preview, so nothing is
    there. That is the correct outcome and it arrives with no special case.
    """
    order = {spec.path: i for i, spec in enumerate(template.fields)}
    parts: list[str] = []
    for row in sorted(rows, key=lambda r: order.get(
            _templates.template_path(r["field_path"]), 999)):
        if not row["searchable"]:
            continue
        text = str(row["masked_text"] or "").strip()
        if text:
            parts.append(text)
    return " • ".join(parts)[:MAX_SUMMARY]


def _expiry_of(rows: list[dict]) -> str:
    for row in rows:
        if row["expires_record"] and row["value_date"]:
            return row["value_date"]
    return ""


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
def find_duplicates(
    cur, *, owner_user_id: int, template_key: str, rows: list[dict],
    exclude_record_id: int = 0,
) -> list[int]:
    """Records of the same template whose identity fields all match.

    Matched on ``masked_text``, never on the value — which is both the private
    answer and the fast one, since it is precisely what
    ``idx_record_fields_owner_identity`` indexes. Two passports whose numbers
    differ only beyond the last four digits will look like duplicates and the
    member will be asked; that is the right way round, because the cost of the
    question is one tap and the cost of the alternative is either a silent
    second passport record or a comparison performed against plaintext.
    """
    owner = _owner(owner_user_id)
    identity = {r["field_path"]: str(r["masked_text"] or "") for r in rows
                if r["identity_field"]}
    if not identity:
        return []

    cur.execute(
        """SELECT record_id, field_path, masked_text FROM private_record_fields
        WHERE owner_user_id = ? AND template_key = ? AND identity_field = 1""",
        (owner, str(template_key)),
    )
    seen: dict[int, dict[str, str]] = {}
    for raw in cur.fetchall() or ():
        row = _row_dict(raw)
        record_id = int(row.get("record_id") or 0)
        if record_id == int(exclude_record_id or 0):
            continue
        seen.setdefault(record_id, {})[str(row.get("field_path") or "")] = str(
            row.get("masked_text") or "")

    return sorted(
        record_id for record_id, fields in seen.items() if fields == identity
    )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def _next_history_seq(cur, owner: int, record_id: int) -> int:
    cur.execute(
        """SELECT MAX(revision) AS seq FROM private_record_revisions
        WHERE owner_user_id = ? AND record_id = ?""",
        (owner, int(record_id)),
    )
    row = _row_dict(cur.fetchone())
    return int(row.get("seq") or 0) + 1


def _write_revision(
    cur, *, owner: int, record_id: int, record_key_value: str, change_type: str,
    template_key: str, changed_paths: list[str] | tuple[str, ...] = (),
    template_version_from: int | None = None, template_version_to: int | None = None,
    status_from: str = "", status_to: str = "", verification_from: str = "",
    verification_to: str = "", source_type: str = "", reason_code: str = "",
    actor_user_id: int | None = None, actor_kind: str = "",
) -> int:
    """Append one history entry. Paths, never values.

    ``changed_paths`` is capped and sorted. The cap is not a performance
    measure: an unbounded joined string is a text column that grows without a
    limit anybody stated, and the shape of thing that ends up holding something
    other than paths.
    """
    change = normalize_change_type(change_type)
    if change is None:
        raise StructuredRecordRejected(f"unknown change type {change_type!r}")
    seq = _next_history_seq(cur, owner, record_id)
    paths = ",".join(sorted({str(p) for p in changed_paths})[:MAX_CHANGED_PATHS])
    cur.execute(
        """INSERT INTO private_record_revisions
        (owner_user_id, record_id, record_key, revision, change_type,
         changed_paths, template_key, template_version_from, template_version_to,
         status_from, status_to, verification_from, verification_to,
         source_type, reason_code, actor_user_id, actor_kind, audit_event_id,
         created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            owner, int(record_id), str(record_key_value), seq, change, paths,
            str(template_key), template_version_from, template_version_to,
            str(status_from or ""), str(status_to or ""),
            str(verification_from or ""), str(verification_to or ""),
            str(source_type or ""), _text(reason_code, 64),
            int(actor_user_id) if actor_user_id else None, str(actor_kind or ""),
            None, _now_iso(),
        ),
    )
    return seq


# ---------------------------------------------------------------------------
# Field row IO
# ---------------------------------------------------------------------------
_FIELD_COLUMNS: tuple[str, ...] = (
    "field_path", "field_kind", "value_type", "value_text", "value_number",
    "value_date", "currency", "sensitivity", "mask", "searchable",
    "masked_text", "search_text", "is_encrypted", "cipher_text",
    "cipher_key_id", "identity_field", "expires_record", "undx_readable",
)


def _insert_fields(cur, *, owner: int, record_id: int, record_key_value: str,
                   template, rows: list[dict], now_iso: str) -> None:
    columns = ("owner_user_id", "record_id", "record_key", "template_key",
               "template_version") + _FIELD_COLUMNS + ("created_at", "updated_at")
    placeholders = ", ".join("?" for _ in columns)
    for row in rows:
        params = [owner, int(record_id), record_key_value, template.key,
                  int(template.version)]
        params.extend(row[name] for name in _FIELD_COLUMNS)
        params.extend([now_iso, now_iso])
        cur.execute(
            f"INSERT INTO private_record_fields ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            tuple(params),
        )


def _delete_fields(cur, *, owner: int, record_id: int, paths) -> None:
    for path in paths:
        cur.execute(
            """DELETE FROM private_record_fields
            WHERE owner_user_id = ? AND record_id = ? AND field_path = ?""",
            (owner, int(record_id), str(path)),
        )


def _load_fields(cur, *, owner: int, record_id: int) -> list[dict]:
    cur.execute(
        """SELECT * FROM private_record_fields
        WHERE owner_user_id = ? AND record_id = ? ORDER BY field_path""",
        (owner, int(record_id)),
    )
    return [_row_dict(r) for r in cur.fetchall() or ()]


# ---------------------------------------------------------------------------
# Envelope IO
# ---------------------------------------------------------------------------
def _load_envelope(cur, *, owner: int, record_id: int = 0,
                   record_key_value: str = "") -> dict:
    """One envelope, or ``{}``.

    Owner is a predicate here, not a check performed afterwards on a row that
    was already loaded. The difference matters: an ownership check after the
    fact is a line somebody can delete while the query still returns the row,
    and every caller above assumes an empty result means "not yours or not
    there" without being able to tell which — which is the property Stage 14 of
    the isolation work asked for.
    """
    if record_id:
        cur.execute(
            """SELECT * FROM private_structured_records
            WHERE owner_user_id = ? AND id = ?""",
            (owner, int(record_id)),
        )
    elif record_key_value:
        cur.execute(
            """SELECT * FROM private_structured_records
            WHERE owner_user_id = ? AND record_key = ?""",
            (owner, str(record_key_value)),
        )
    else:
        raise StructuredRecordRejected("record_id or record_key is required")
    return _row_dict(cur.fetchone())


def _serialize_envelope(row: dict) -> dict:
    """The envelope as callers see it. No owner id, no internal row ids.

    ``record_key`` *is* exposed, unlike ``records._serialize``, because it is
    this store's public handle — the thing a client puts in a URL and an
    idempotent retry re-sends — and it is designed to carry no information (see
    :func:`record_key`). The integer ``id`` goes out too, because the reveal and
    history endpoints address a record by it; the owner id never does.
    """
    verification = str(row.get("verification_state") or "")
    return {
        "id": int(row.get("id") or 0),
        "record_key": str(row.get("record_key") or ""),
        "template_key": str(row.get("template_key") or ""),
        "template_version": int(row.get("template_version") or 1),
        "contract_version": int(row.get("contract_version") or CONTRACT_VERSION),
        "schema_key": f"{row.get('template_key')}@{row.get('template_version')}",
        "domain": str(row.get("domain") or ""),
        "ia_domain": str(row.get("ia_domain") or ""),
        "title": str(row.get("title") or ""),
        "summary": str(row.get("summary_text") or ""),
        "description": str(row.get("description") or ""),
        "status": str(row.get("status") or ""),
        "lifecycle_state": str(row.get("lifecycle_state") or _model.LIFECYCLE_ACTIVE),
        "sensitivity": str(row.get("sensitivity") or _model.DEFAULT_SENSITIVITY),
        "verification_state": verification,
        "verified": is_verified(verification),
        "needs_review": needs_review(verification),
        "source_type": str(row.get("source_type") or ""),
        "source_ref": str(row.get("source_ref") or ""),
        "evidence_ids": _split(row.get("evidence_ids")),
        "tags": _split(row.get("tags")),
        "effective_date": str(row.get("effective_date") or ""),
        "expires_at": str(row.get("expires_at") or ""),
        "review_at": str(row.get("review_at") or ""),
        "revision": int(row.get("revision") or 1),
        "undx_readable": bool(row.get("undx_readable")),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "archived_at": row.get("archived_at") or None,
    }


def _serialize_field(row: dict) -> dict:
    """One stored field as a screen may see it.

    ``masked_text`` and only ``masked_text``. There is no branch here that could
    read ``value_text``, and no argument that could ask it to — a caller wanting
    the real value calls :func:`reveal_field`, which requires a step-up and
    writes an audit row. The absence of the branch is the enforcement; a flag
    like ``include_values=True`` would be one default away from a list endpoint
    returning every passport number a member has.
    """
    masked = str(row.get("mask") or _templates.MASK_NONE) != _templates.MASK_NONE
    return {
        "path": str(row.get("field_path") or ""),
        "kind": str(row.get("field_kind") or ""),
        "value": str(row.get("masked_text") or ""),
        "value_date": str(row.get("value_date") or ""),
        "currency": str(row.get("currency") or ""),
        "masked": masked,
        "revealable": masked,
        "encrypted": bool(row.get("is_encrypted")),
        "sensitivity": str(row.get("sensitivity") or ""),
    }


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def create_record(
    cur,
    *,
    owner_user_id: int,
    template_key: str,
    payload: dict,
    template_version: object = None,
    title: str = "",
    description: str = "",
    office_id: str = "",
    status: str = "",
    verification_state: str = "",
    source_type: str = "",
    source_ref: str = "",
    provenance_type: str = "",
    evidence_ids: object = (),
    tags: object = (),
    effective_date: object = "",
    review_at: object = "",
    reminder_policy: str = "",
    idempotency_key: object = "",
    allow_duplicate: bool = False,
    actor_user_id: int | None = None,
    actor_kind: str = ACTOR_USER,
    purpose: str = "user_request",
) -> dict:
    """Write one structured record: envelope, fields and first history entry.

    Returns ``{"status", "record_id", "record", "duplicates", "errors"}``.
    ``status`` is one of:

    ``created``    a new record.
    ``existing``   ``idempotency_key`` matched a record already written. The
                   stored record is returned unchanged — a replayed create does
                   not become an update, because the two have different
                   authority and the caller asked for the first one.
    ``duplicate``  the identity fields match a record the member already has,
                   and ``allow_duplicate`` was not set. **Nothing was written.**
                   The decision goes back to the member rather than being made
                   here, in either direction: silently deduping loses a genuine
                   second passport, and silently writing produces two records
                   that will disagree the first time one is corrected.

    Raises :class:`StructuredRecordRejected` for anything incoherent, including
    a restricted field with no key configured. It does not fall back to storing
    that field in the clear, and there is no argument that makes it.
    """
    owner = _owner(owner_user_id)
    kind = _actor_kind(actor_kind)
    template = _resolve_template(template_key, template_version)

    result = _templates.validate_payload(template, payload or {})
    if not result.ok:
        return {"status": STATUS_INVALID, "record_id": 0, "record": None,
                "duplicates": [], "errors": result.errors_as_list()}

    verification = _check_verification(
        verification_state or DEFAULT_VERIFICATION, kind)
    source = normalize_source_type(source_type or _records.SOURCE_USER)
    if source is None:
        raise StructuredRecordRejected(f"unknown source type {source_type!r}")
    provenance = _text(provenance_type, 64)
    if source in DERIVED_SOURCES and not provenance:
        # Same rule the fact store enforces, for the same reason: a record that
        # came from a document or an inference and cannot say so is a record
        # that will eventually be quoted as if a member had typed it.
        raise StructuredRecordRejected(
            f"a record from source {source} must carry a provenance_type")

    record_status = _text(status, 64) or template.default_status
    if record_status not in template.statuses:
        raise StructuredRecordRejected(
            f"{record_status!r} is not a status of {template.key}; "
            f"expected one of {', '.join(template.statuses)}")

    require_structured_schema(cur)

    key = record_key(owner_user_id=owner, template_key=template.key,
                     idempotency_key=idempotency_key)
    if idempotency_key:
        existing = _load_envelope(cur, owner=owner, record_key_value=key)
        if existing:
            return {"status": STATUS_EXISTING, "record_id": int(existing["id"]),
                    "record": get_record(cur, owner_user_id=owner,
                                         record_id=int(existing["id"]), audit=False),
                    "duplicates": [], "errors": []}

    rows = [
        _project_field(value, owner_user_id=owner, record_key_value=key,
                       template=template)
        for value in result.values
    ]

    duplicates = find_duplicates(
        cur, owner_user_id=owner, template_key=template.key, rows=rows)
    if duplicates and not allow_duplicate:
        return {"status": STATUS_DUPLICATE, "record_id": 0, "record": None,
                "duplicates": duplicates, "errors": []}

    now_iso = _now_iso()
    summary = _summary_text(template, rows)
    record_title = _text(title, MAX_TITLE) or template.display_fallback

    cur.execute(
        """INSERT INTO private_structured_records
        (owner_user_id, office_id, record_key, template_key, template_version,
         contract_version, domain, ia_domain, title, summary_text, description,
         status, lifecycle_state, sensitivity, verification_state, source_type,
         source_ref, provenance_type, evidence_ids, graph_node_id,
         effective_date, expires_at, review_at, tags, reminder_policy,
         undx_readable, revision, supersedes_id, created_at, updated_at,
         created_by, updated_by, archived_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            owner, _text(office_id, 64), key, template.key, int(template.version),
            int(CONTRACT_VERSION), template.domain, template.ia_domain,
            record_title, summary, _text(description, MAX_DESCRIPTION),
            record_status, _model.LIFECYCLE_ACTIVE, template.sensitivity,
            verification, source, _records.safe_ref(source_ref), provenance,
            _refs(evidence_ids), None,
            _records._iso(effective_date, default="") or "",
            _expiry_of(rows),
            _records._iso(review_at, default="") or "",
            _tags(tags), _text(reminder_policy, 200),
            1 if template.undx_readable else 0, 1, None, now_iso, now_iso,
            _text(kind, MAX_ACTOR), _text(kind, MAX_ACTOR), None,
        ),
    )

    # `lastrowid` is None on PostgreSQL for these tables, so the id comes back
    # through the unique key the row was written with — the same approach
    # `records._insert` takes, for the same reason.
    envelope = _load_envelope(cur, owner=owner, record_key_value=key)
    if not envelope:  # pragma: no cover - insert succeeded but row is absent
        raise StructuredSchemaMissing("record was written but could not be read back")
    record_id = int(envelope["id"])

    _insert_fields(cur, owner=owner, record_id=record_id, record_key_value=key,
                   template=template, rows=rows, now_iso=now_iso)

    _write_revision(
        cur, owner=owner, record_id=record_id, record_key_value=key,
        change_type=CHANGE_CREATED, template_key=template.key,
        changed_paths=[r["field_path"] for r in rows],
        template_version_to=int(template.version), status_to=record_status,
        verification_to=verification, source_type=source,
        actor_user_id=actor_user_id or owner, actor_kind=kind,
    )
    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_RECORD_CREATE, object_type="private_structured_record",
        object_id=record_id, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )

    return {"status": STATUS_CREATED, "record_id": record_id,
            "record": get_record(cur, owner_user_id=owner, record_id=record_id,
                                 audit=False),
            "duplicates": duplicates, "errors": []}


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
def update_record(
    cur,
    *,
    owner_user_id: int,
    record_id: int,
    payload: dict | None = None,
    title: object = None,
    description: object = None,
    evidence_ids: object = None,
    tags: object = None,
    effective_date: object = None,
    review_at: object = None,
    reminder_policy: object = None,
    verification_state: object = None,
    status: object = None,
    expected_revision: object = None,
    reason_code: str = "",
    actor_user_id: int | None = None,
    actor_kind: str = ACTOR_USER,
    purpose: str = "user_request",
) -> dict:
    """Change a record's fields or envelope, in place, with history.

    ``payload`` is a patch, validated with ``partial=True``: a path that is
    present is replaced, a path that is present and empty is *cleared*, and a
    path that is absent is untouched. The distinction between the second and
    third is why the patch is read from the submitted keys rather than from the
    validated values — a cleared field produces no ``FieldValue``, and a writer
    that only looked at the values would silently ignore every deletion a member
    made.

    ``expected_revision`` is optimistic concurrency. When supplied and stale the
    write is refused with :class:`StructuredRecordConflict` rather than applied,
    because the alternative is that the later of two people editing the same
    record wins by accident and the earlier one never learns.

    A verification change is *not* free-form. Moving to a verified state
    requires a human actor, exactly as it does at create.
    """
    owner = _owner(owner_user_id)
    kind = _actor_kind(actor_kind)
    require_structured_schema(cur)

    current = _load_envelope(cur, owner=owner, record_id=int(record_id or 0))
    if not current:
        raise StructuredRecordDenied("no such record")
    if current.get("lifecycle_state") == _model.LIFECYCLE_ARCHIVED:
        raise StructuredRecordRejected(
            "an archived record cannot be edited; restore it first")

    if expected_revision is not None and str(expected_revision).strip() != "":
        if int(expected_revision) != int(current.get("revision") or 1):
            raise StructuredRecordConflict(
                f"record is at revision {int(current.get('revision') or 1)}, "
                f"not {int(expected_revision)}")

    template = _resolve_template(current["template_key"], current["template_version"])
    key = str(current["record_key"])
    changed: list[str] = []
    sets: dict[str, object] = {}

    # -- fields -------------------------------------------------------------
    field_rows: list[dict] = []
    cleared: list[str] = []
    written: set[str] = set()
    if payload:
        submitted = [str(p).strip() for p in payload if str(p or "").strip()]
        result = _templates.validate_payload(template, payload, partial=True)
        if not result.ok:
            return {"status": STATUS_INVALID, "record_id": int(record_id),
                    "record": None, "errors": result.errors_as_list()}
        field_rows = [
            _project_field(value, owner_user_id=owner, record_key_value=key,
                           template=template)
            for value in result.values
        ]
        written = {r["field_path"] for r in field_rows}
        cleared = [p for p in submitted if p not in written]
        changed.extend(sorted(written | set(cleared)))

    now_iso = _now_iso()
    if field_rows or cleared:
        _delete_fields(cur, owner=owner, record_id=int(record_id),
                       paths=sorted(written) + cleared)
        if field_rows:
            _insert_fields(cur, owner=owner, record_id=int(record_id),
                           record_key_value=key, template=template,
                           rows=field_rows, now_iso=now_iso)
        # Summary and expiry are properties of the whole record, so they are
        # recomputed from every stored field rather than from the patch. A patch
        # that clears the expiry date must clear the envelope's `expires_at`,
        # and a reader that recomputed from the patch alone would leave the old
        # date in place — a reminder for a document that no longer expires then.
        stored = _load_fields(cur, owner=owner, record_id=int(record_id))
        sets["summary_text"] = _summary_text(template, stored)
        sets["expires_at"] = _expiry_of(stored)

    # -- envelope -----------------------------------------------------------
    if title is not None:
        sets["title"] = _text(title, MAX_TITLE) or template.display_fallback
        changed.append("title")
    if description is not None:
        sets["description"] = _text(description, MAX_DESCRIPTION)
        changed.append("description")
    if evidence_ids is not None:
        sets["evidence_ids"] = _refs(evidence_ids)
        changed.append("evidence_ids")
    if tags is not None:
        sets["tags"] = _tags(tags)
        changed.append("tags")
    if effective_date is not None:
        sets["effective_date"] = _records._iso(effective_date, default="") or ""
        changed.append("effective_date")
    if review_at is not None:
        sets["review_at"] = _records._iso(review_at, default="") or ""
        changed.append("review_at")
    if reminder_policy is not None:
        sets["reminder_policy"] = _text(reminder_policy, 200)
        changed.append("reminder_policy")

    status_to = ""
    if status is not None:
        status_to = _text(status, 64)
        if status_to not in template.statuses:
            raise StructuredRecordRejected(
                f"{status_to!r} is not a status of {template.key}")
        sets["status"] = status_to
        changed.append("status")

    verification_to = ""
    if verification_state is not None:
        verification_to = _check_verification(str(verification_state), kind)
        sets["verification_state"] = verification_to
        changed.append("verification_state")

    if not sets:
        return {"status": STATUS_UNCHANGED, "record_id": int(record_id),
                "record": get_record(cur, owner_user_id=owner,
                                     record_id=int(record_id), audit=False),
                "errors": []}

    revision = int(current.get("revision") or 1) + 1
    sets["revision"] = revision
    sets["updated_at"] = now_iso
    sets["updated_by"] = _text(kind, MAX_ACTOR)

    assignments = ", ".join(f"{name} = ?" for name in sets)
    cur.execute(
        f"UPDATE private_structured_records SET {assignments} "
        "WHERE owner_user_id = ? AND id = ?",
        tuple(sets.values()) + (owner, int(record_id)),
    )

    change_type = CHANGE_UPDATED
    if verification_to and not payload and not status_to:
        change_type = CHANGE_VERIFICATION_CHANGED
    elif status_to and not payload and not verification_to:
        change_type = CHANGE_STATUS_CHANGED

    _write_revision(
        cur, owner=owner, record_id=int(record_id), record_key_value=key,
        change_type=change_type, template_key=template.key, changed_paths=changed,
        template_version_from=int(current["template_version"]),
        template_version_to=int(current["template_version"]),
        status_from=str(current.get("status") or "") if status_to else "",
        status_to=status_to,
        verification_from=(
            str(current.get("verification_state") or "") if verification_to else ""),
        verification_to=verification_to,
        source_type=str(current.get("source_type") or ""), reason_code=reason_code,
        actor_user_id=actor_user_id or owner, actor_kind=kind,
    )
    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_RECORD_UPDATE, object_type="private_structured_record",
        object_id=int(record_id), purpose=purpose, outcome=_audit.OUTCOME_OK,
    )

    return {"status": STATUS_UPDATED, "record_id": int(record_id),
            "record": get_record(cur, owner_user_id=owner, record_id=int(record_id),
                                 audit=False),
            "errors": []}


def archive_record(
    cur, *, owner_user_id: int, record_id: int, reason_code: str = "",
    actor_user_id: int | None = None, actor_kind: str = ACTOR_USER,
    purpose: str = "user_request",
) -> dict:
    """Retire a record without destroying it.

    There is no delete in this module and that is deliberate. A member who
    archives a passport they have replaced still wants the history, the
    evidence and the audit trail of who looked at it; a member who wants the
    data gone is asking for an account-level erasure, which is a different
    operation with different authority, different scope and a different record
    of having happened. Offering a per-record delete here would let the second
    thing be done accidentally while doing the first.
    """
    owner = _owner(owner_user_id)
    kind = _actor_kind(actor_kind)
    require_structured_schema(cur)

    current = _load_envelope(cur, owner=owner, record_id=int(record_id or 0))
    if not current:
        raise StructuredRecordDenied("no such record")
    if current.get("lifecycle_state") == _model.LIFECYCLE_ARCHIVED:
        return {"status": STATUS_UNCHANGED, "record_id": int(record_id),
                "record": _serialize_envelope(current)}

    now_iso = _now_iso()
    cur.execute(
        """UPDATE private_structured_records
        SET lifecycle_state = ?, verification_state = ?, archived_at = ?,
            revision = ?, updated_at = ?, updated_by = ?
        WHERE owner_user_id = ? AND id = ?""",
        (_model.LIFECYCLE_ARCHIVED, VERIFICATION_ARCHIVED, now_iso,
         int(current.get("revision") or 1) + 1, now_iso, _text(kind, MAX_ACTOR),
         owner, int(record_id)),
    )
    _write_revision(
        cur, owner=owner, record_id=int(record_id),
        record_key_value=str(current["record_key"]), change_type=CHANGE_ARCHIVED,
        template_key=str(current["template_key"]),
        verification_from=str(current.get("verification_state") or ""),
        verification_to=VERIFICATION_ARCHIVED, reason_code=reason_code,
        actor_user_id=actor_user_id or owner, actor_kind=kind,
    )
    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_RECORD_UPDATE, object_type="private_structured_record",
        object_id=int(record_id), purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return {"status": STATUS_UPDATED, "record_id": int(record_id),
            "record": get_record(cur, owner_user_id=owner, record_id=int(record_id),
                                 audit=False)}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def get_record(
    cur, *, owner_user_id: int, record_id: int = 0, record_key_value: str = "",
    audit: bool = True, actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict | None:
    """One record with its fields, masked.

    **This function never decrypts anything.** Every field it returns comes from
    ``masked_text``, which for an unmasked field is the value and for a masked
    one is the masked form. That is not a policy the function follows, it is the
    only data it loads a column for — and it is what makes "opening a record
    does not expose a passport number" true of the code rather than of the
    caller. :func:`reveal_field` is the one path that decrypts, and it demands a
    step-up and writes an audit row to do it.
    """
    owner = _owner(owner_user_id)
    require_structured_schema(cur)
    row = _load_envelope(cur, owner=owner, record_id=int(record_id or 0),
                         record_key_value=str(record_key_value or ""))
    if not row:
        return None

    fields = _load_fields(cur, owner=owner, record_id=int(row["id"]))
    template = _templates.get_template(row["template_key"], row["template_version"])
    order = ({spec.path: i for i, spec in enumerate(template.fields)}
             if template is not None else {})
    fields.sort(key=lambda r: order.get(
        _templates.template_path(str(r.get("field_path") or "")), 999))

    out = _serialize_envelope(row)
    out["fields"] = [_serialize_field(f) for f in fields]
    # A record written against a template version this process does not have is
    # readable but not editable, and it says so rather than pretending. The
    # alternative — rendering it against the nearest version we do have — moves
    # values under labels that were written for different questions.
    out["template_known"] = template is not None

    if audit:
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_RECORD_READ, object_type="private_structured_record",
            object_id=int(row["id"]), purpose=purpose, outcome=_audit.OUTCOME_OK,
            result_count=1,
        )
    return out


def list_records(
    cur,
    *,
    owner_user_id: int,
    template_key: object = None,
    ia_domain: object = None,
    lifecycle_state: object = _model.LIFECYCLE_ACTIVE,
    verification_state: object = None,
    needs_review_only: bool = False,
    expiring_before: object = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    actor_user_id: int | None = None,
    audit: bool = True,
    purpose: str = "user_request",
) -> dict:
    """Envelopes only, owner-scoped, paginated.

    No field values of any kind, masked or otherwise — the list line is the
    stored ``summary_text``, which was assembled at write time from fields the
    template had already marked searchable. That is what keeps a list query from
    having to load a restricted value in order to decide not to show it, and a
    query that never loads the value is a query that cannot leak it through a
    log line, an error message or a serializer somebody adds later.
    """
    owner = _owner(owner_user_id)
    require_structured_schema(cur)

    where = ["owner_user_id = ?"]
    params: list = [owner]
    if template_key:
        where.append("template_key = ?")
        params.append(str(template_key))
    if ia_domain:
        where.append("ia_domain = ?")
        params.append(str(ia_domain))
    if lifecycle_state:
        where.append("lifecycle_state = ?")
        params.append(str(lifecycle_state))
    if verification_state:
        state = normalize_verification(verification_state)
        if state is None:
            raise StructuredRecordRejected(
                f"unknown verification state {verification_state!r}")
        where.append("verification_state = ?")
        params.append(state)
    if needs_review_only:
        placeholders = ", ".join("?" for _ in sorted(NEEDS_REVIEW_STATES))
        where.append(f"verification_state IN ({placeholders})")
        params.extend(sorted(NEEDS_REVIEW_STATES))
    if expiring_before:
        cutoff = _records._iso(expiring_before, default="")
        if not cutoff:
            raise StructuredRecordRejected(
                f"expiring_before is not a date: {expiring_before!r}")
        # `expires_at <> ''` keeps records that never expire out of the result
        # rather than letting the empty string sort below every cutoff, which
        # would put every permanent record in the "expiring soon" list.
        where.append("expires_at <> '' AND expires_at <= ?")
        params.append(cutoff)

    size = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    start = max(0, int(offset or 0))
    cur.execute(
        f"SELECT * FROM private_structured_records WHERE {' AND '.join(where)} "
        "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        tuple(params) + (size + 1, start),
    )
    rows = [_row_dict(r) for r in cur.fetchall() or ()]
    has_more = len(rows) > size
    rows = rows[:size]

    if audit:
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_RECORD_READ, object_type="private_structured_record",
            purpose=purpose, outcome=_audit.OUTCOME_OK, result_count=len(rows),
        )

    records = [_serialize_envelope(r) for r in rows]
    return {
        "records": records,
        # The size of *this page*, not a total. There is deliberately no total:
        # answering "how many records do you have" means a second COUNT(*) over
        # the same predicate on every page turn, and the number it returns is
        # already stale by the time it renders. `has_more` is what a list needs
        # to decide whether to keep scrolling.
        "count": len(records),
        "limit": size,
        "offset": start,
        "has_more": has_more,
    }


def search_records(
    cur, *, owner_user_id: int, query: str, limit: int = DEFAULT_LIMIT,
    actor_user_id: int | None = None, audit: bool = True,
    purpose: str = "user_request",
) -> dict:
    """Search the field index. Matches masked text, returns masked text.

    The index this reads was populated by
    :func:`~services.private_office.record_templates.search_index_text`, so a
    masked field is in it under its masked form and a non-searchable field is
    not in it at all. That is what makes a suggestion line like
    ``Passport • United States • ending 1234`` possible with the index never
    having held the number — and it means a match on ``1234`` is a match on the
    four digits the member is already shown, not on the value.

    Health records surface here by title and by nothing else, because
    ``medical_condition`` marks no field searchable. That is deliberate: a
    search suggestion is rendered in a list, over a member's shoulder, on a
    lock screen preview, and a diagnosis does not belong in any of them.
    """
    owner = _owner(owner_user_id)
    require_structured_schema(cur)
    text = str(query or "").strip().lower()
    if len(text) < 2:
        # One character matches most of the index, which makes the first
        # keystroke of every search a full read of every record a member has.
        return {"results": [], "query": text, "limit": int(limit or DEFAULT_LIMIT)}

    size = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    pattern = f"%{text.replace('%', '').replace('_', '')}%"

    cur.execute(
        """SELECT record_id, field_path, masked_text FROM private_record_fields
        WHERE owner_user_id = ? AND searchable = 1 AND search_text LIKE ?
        ORDER BY record_id LIMIT ?""",
        (owner, pattern, size * 8),
    )
    hits: dict[int, list[dict]] = {}
    for raw in cur.fetchall() or ():
        row = _row_dict(raw)
        hits.setdefault(int(row.get("record_id") or 0), []).append({
            "path": str(row.get("field_path") or ""),
            "value": str(row.get("masked_text") or ""),
        })

    cur.execute(
        """SELECT id, title, summary_text, template_key, template_version,
                  ia_domain, record_key, expires_at, verification_state,
                  lifecycle_state
        FROM private_structured_records
        WHERE owner_user_id = ? AND (title LIKE ? OR summary_text LIKE ?)
        ORDER BY updated_at DESC LIMIT ?""",
        (owner, pattern, pattern, size * 4),
    )
    envelopes: dict[int, dict] = {}
    for raw in cur.fetchall() or ():
        row = _row_dict(raw)
        envelopes[int(row.get("id") or 0)] = row

    missing = [rid for rid in hits if rid not in envelopes]
    for rid in missing[:size * 4]:
        row = _load_envelope(cur, owner=owner, record_id=rid)
        if row:
            envelopes[rid] = row

    results = []
    for rid, row in envelopes.items():
        if str(row.get("lifecycle_state") or _model.LIFECYCLE_ACTIVE) != _model.LIFECYCLE_ACTIVE:
            continue
        results.append({
            "record_id": rid,
            "record_key": str(row.get("record_key") or ""),
            "template_key": str(row.get("template_key") or ""),
            "ia_domain": str(row.get("ia_domain") or ""),
            "title": str(row.get("title") or ""),
            "summary": str(row.get("summary_text") or ""),
            "expires_at": str(row.get("expires_at") or ""),
            "verification_state": str(row.get("verification_state") or ""),
            "matched": hits.get(rid, [])[:4],
        })
    results.sort(key=lambda r: (not r["matched"], r["title"].lower()))
    results = results[:size]

    if audit:
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_RECORD_READ, object_type="private_structured_record",
            purpose=purpose, outcome=_audit.OUTCOME_OK, result_count=len(results),
        )
    return {"results": results, "query": text, "limit": size}


def domain_counts(
    cur, *, owner_user_id: int,
    lifecycle_state: object = _model.LIFECYCLE_ACTIVE,
) -> dict[str, int]:
    """``{ia_domain: count}`` for one member. Counts rows, reads no values.

    The home screen needs a number under each of thirteen headings, and the
    honest ways to get it are this or thirteen list calls. Thirteen list calls
    would load thirteen pages of envelopes to display thirteen integers, and —
    worse — would each write an audit row, burying the reads that matter under
    a screen render.

    Domains with no records are absent from the mapping rather than present as
    zero. The caller renders from the full domain vocabulary and looks each one
    up, so a domain this store has never heard of still gets a heading, and a
    domain that lost its last record still shows zero rather than vanishing.
    """
    owner = _owner(owner_user_id)
    require_structured_schema(cur)

    where = ["owner_user_id = ?"]
    params: list = [owner]
    if lifecycle_state:
        where.append("lifecycle_state = ?")
        params.append(str(lifecycle_state))

    cur.execute(
        "SELECT ia_domain, COUNT(*) AS n FROM private_structured_records "
        f"WHERE {' AND '.join(where)} GROUP BY ia_domain",
        tuple(params),
    )
    out: dict[str, int] = {}
    for raw in cur.fetchall() or ():
        row = _row_dict(raw)
        domain = str(row.get("ia_domain") or "")
        if domain:
            out[domain] = int(row.get("n") or 0)
    return out


def expiring_records(
    cur, *, owner_user_id: int, before: object, limit: int = MAX_LIMIT,
) -> list[dict]:
    """Envelopes expiring on or before ``before``. For the reminder sweep.

    No audit row: this runs on a timer with no member present, and an audit
    table whose dominant content is a background job's reads is one in which the
    rows that matter are impossible to find.
    """
    return list_records(
        cur, owner_user_id=owner_user_id, expiring_before=before, limit=limit,
        audit=False,
    )["records"]


def record_history(
    cur, *, owner_user_id: int, record_id: int, limit: int = MAX_LIMIT,
) -> list[dict]:
    """This record's history: what changed, when, by whom. Never what it was."""
    owner = _owner(owner_user_id)
    require_structured_schema(cur)
    if not _load_envelope(cur, owner=owner, record_id=int(record_id or 0)):
        raise StructuredRecordDenied("no such record")
    cur.execute(
        """SELECT * FROM private_record_revisions
        WHERE owner_user_id = ? AND record_id = ?
        ORDER BY revision DESC LIMIT ?""",
        (owner, int(record_id), max(1, min(int(limit or MAX_LIMIT), MAX_LIMIT))),
    )
    out = []
    for raw in cur.fetchall() or ():
        row = _row_dict(raw)
        out.append({
            "sequence": int(row.get("revision") or 0),
            "change_type": str(row.get("change_type") or ""),
            "changed_paths": _split(row.get("changed_paths")),
            "status_from": str(row.get("status_from") or ""),
            "status_to": str(row.get("status_to") or ""),
            "verification_from": str(row.get("verification_from") or ""),
            "verification_to": str(row.get("verification_to") or ""),
            "reason_code": str(row.get("reason_code") or ""),
            "actor_kind": str(row.get("actor_kind") or ""),
            "created_at": str(row.get("created_at") or ""),
        })
    return out


# ---------------------------------------------------------------------------
# Reveal
# ---------------------------------------------------------------------------
def reveal_field(
    cur,
    *,
    owner_user_id: int,
    record_id: int,
    field_path: str,
    step_up_verified: bool,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict:
    """Return one masked field's real value. The only path that decrypts.

    ``step_up_verified`` is a required keyword with no default, and it is the
    caller's assertion that a fresh biometric or passcode check just succeeded —
    the transport layer is the only place that can know it, so it is the only
    place that can say it. Making it required and defaultless is the point: a
    parameter with ``= False`` is a parameter somebody omits, and a parameter
    with ``= True`` is a hole. A caller that has not done the step-up gets a
    refusal *and* a denial row in the audit table, because an attempt is the
    single most interesting thing this table can hold.

    Returns ``{"path", "value", "record_id", "revealed_at"}``. The value is in
    the response and nowhere else: not logged, not in the exception messages
    below, and never written back to any column.
    """
    owner = _owner(owner_user_id)
    require_structured_schema(cur)
    actor = int(actor_user_id or owner)
    path = str(field_path or "").strip()

    if not step_up_verified:
        _audit.record_denied(
            cur, actor_user_id=actor, owner_user_id=owner,
            object_type="private_record_field", object_id=int(record_id or 0),
            purpose=purpose,
        )
        raise StructuredRecordDenied("a fresh step-up is required to reveal this field")

    envelope = _load_envelope(cur, owner=owner, record_id=int(record_id or 0))
    if not envelope:
        _audit.record_denied(
            cur, actor_user_id=actor, owner_user_id=owner,
            object_type="private_record_field", object_id=int(record_id or 0),
            purpose=purpose,
        )
        raise StructuredRecordDenied("no such record")

    cur.execute(
        """SELECT * FROM private_record_fields
        WHERE owner_user_id = ? AND record_id = ? AND field_path = ?""",
        (owner, int(record_id), path),
    )
    row = _row_dict(cur.fetchone())
    if not row:
        raise StructuredRecordDenied("no such field")

    if str(row.get("mask") or _templates.MASK_NONE) == _templates.MASK_NONE:
        # Nothing was hidden, so nothing is revealed and no reveal is recorded.
        # Logging this as a reveal would fill the one table a member checks to
        # see who looked at their passport with entries about their city.
        return {"record_id": int(record_id), "path": path,
                "value": str(row.get("value_text") or ""), "revealed_at": ""}

    if row.get("is_encrypted"):
        try:
            value = _crypto.decrypt(
                str(row.get("cipher_text") or ""), owner_user_id=owner,
                record_key=str(row.get("record_key") or ""), field_path=path,
            )
        except _crypto.FieldCryptoError:
            # Deliberately not distinguished from any other decrypt failure —
            # see `field_crypto.decrypt`. What the member is told is that the
            # value cannot be read, which is true whether the key was retired,
            # the ciphertext was tampered with, or the row was moved.
            LOGGER.error(
                "PRIVATE_RECORD_REVEAL_UNREADABLE record_id=%s key_id=%s",
                int(record_id), str(row.get("cipher_key_id") or "")[:32])
            raise StructuredRecordRejected(
                "this value cannot be read with the keys this server has") from None
    else:
        value = str(row.get("value_text") or "")

    now_iso = _now_iso()
    _write_revision(
        cur, owner=owner, record_id=int(record_id),
        record_key_value=str(envelope["record_key"]), change_type=CHANGE_REVEALED,
        template_key=str(envelope["template_key"]), changed_paths=[path],
        actor_user_id=actor, actor_kind=ACTOR_USER,
    )
    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_RECORD_FIELD_REVEAL,
        object_type="private_record_field", object_id=int(record_id),
        purpose=purpose, outcome=_audit.OUTCOME_OK, result_count=1,
    )
    return {"record_id": int(record_id), "path": path, "value": value,
            "revealed_at": now_iso}


# ---------------------------------------------------------------------------
# UNDX projection
# ---------------------------------------------------------------------------
def undx_record(cur, *, owner_user_id: int, record_id: int) -> dict | None:
    """The subset of a record UNDX may read. Four gates, all of them here.

    The template must be readable, the field must be readable, the field must
    not be masked, and the field must not be encrypted. The third gate is the
    one that is easy to argue away and must not be: a model handed
    ``•••• 1234`` will reason about "the passport ending 1234" and put it in a
    summary that goes to a screen, a notification and a log, which is the leak
    the mask existed to prevent, laundered through an assistant. So masked
    fields are dropped entirely rather than passed through masked.

    Returns metadata plus readable fields. It is a projection, not the record:
    the caller cannot ask this function for more by passing an argument, because
    there is no argument.
    """
    owner = _owner(owner_user_id)
    require_structured_schema(cur)
    envelope = _load_envelope(cur, owner=owner, record_id=int(record_id or 0))
    if not envelope:
        return None
    if not envelope.get("undx_readable"):
        return None

    fields = []
    for row in _load_fields(cur, owner=owner, record_id=int(record_id)):
        if not row.get("undx_readable"):
            continue
        if str(row.get("mask") or _templates.MASK_NONE) != _templates.MASK_NONE:
            continue
        if row.get("is_encrypted"):
            continue
        fields.append({
            "path": str(row.get("field_path") or ""),
            "kind": str(row.get("field_kind") or ""),
            "value": str(row.get("value_text") or ""),
        })

    verification = str(envelope.get("verification_state") or "")
    return {
        "record_id": int(envelope["id"]),
        "template_key": str(envelope.get("template_key") or ""),
        "title": str(envelope.get("title") or ""),
        "status": str(envelope.get("status") or ""),
        "expires_at": str(envelope.get("expires_at") or ""),
        # Carried, not stripped. An assistant that cannot tell a member's typed
        # answer from a document extraction nobody has checked will present both
        # with the same confidence, and the mission's requirement to separate
        # fact from inference starts with the projection knowing the difference.
        "verification_state": verification,
        "verified": is_verified(verification),
        "needs_review": needs_review(verification),
        "fields": fields,
    }
