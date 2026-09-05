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

import logging

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
