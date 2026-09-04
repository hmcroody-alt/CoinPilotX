"""The canonical owner of the Private Office DDL.

Why a new substrate rather than an evolution of an existing one
---------------------------------------------------------------
Two tables in this repository look, from a distance, like they already do this
job. Neither does, and the reasons are worth stating because "reuse what exists"
is otherwise the right instinct and is a standing rule of this mission.

``pulse_ai_truth_facts`` stores a **claim string** with a source and a
confidence. It has a live writer (``services.undx_architecture.record_fact``), a
live reader (``services.undx_brain.facts``), and two bootstrap audit scripts
assert its existence. It is UNDX's conversational memory and it is doing that
job. What it cannot do is answer "what is the estimated value of property 123",
because there is no subject, no fact type and no typed value — only a sentence.
Adding nine columns to it would leave every existing row untyped inside a table
called canonical, which is a worse lie than having two tables.

``pulse_ai_knowledge_edges`` is closer: owner, source type/id, relation, target
type/id. But it has no node table, no provenance, no temporal validity, and an
``access_policy`` column whose ``owner_user_id = 0 AND access_policy='public'``
branch in ``graph_neighbors`` deliberately returns rows belonging to nobody to
everybody. Stage 14 of this mission makes owner isolation a P0 gate in which
*existence itself must not leak*. A shared table with a public-row escape hatch
cannot be the substrate for that gate; the first traversal that forgot the
predicate would be a cross-owner read.

So: adjacent tables, prefixed ``private_``, owned entirely by this package. The
UNDX memory tables are untouched. ``PRIVATE_OFFICE_OWNERSHIP_CONTRACT.md``
records the boundary.

Why the bootstrap looks the way it does
---------------------------------------
Stage 176B of the marketplace work is the lesson this module is built from. The
reservation sweeper shipped with lifecycle columns created by
``marketplace_cart_routes._ensure_schema`` — reachable from a cart route handler
and nowhere else. The sweeper runs in ``pulse_worker``, a separate Railway
service that never serves an HTTP request, so the real dependency was::

    a buyer opens a cart  →  the columns exist  →  the sweeper works

and until a buyer did, every cycle died on ``UndefinedColumn`` and reported
``{'scanned': 0, 'candidates': 0, 'released': 0, 'failed': 1}`` — a shape
indistinguishable from a healthy sweep of an empty queue. An inventory leak and
a clean bill of health were the same three numbers.

Stage 34 forbids repeating that. Hence:

* One copy of the DDL. Routes, workers, tests and any future admin path call
  the same :func:`ensure_private_schema`.
* It never raises. A locked database, a role without ``ALTER``, a replica that
  has not caught up — all return as data so the caller degrades and the next
  interval retries.
* Failure is never cached. Only success sets the process flag, so a database
  that heals does not need a restart to be noticed.
* The three outcomes are distinguishable in the logs by name
  (``PRIVATE_SCHEMA_READY`` / ``PRIVATE_SCHEMA_MISSING`` /
  ``PRIVATE_SCHEMA_ENSURE_FAILED``), which is Stage 35, and which exists
  because "this database needs a migration" and "a row misbehaved" arriving as
  the same ``degraded, failed=1`` is what made the last outage invisible.

Portability
-----------
``services.db`` rewrites ``INTEGER PRIMARY KEY AUTOINCREMENT`` to ``SERIAL
PRIMARY KEY`` and adds ``IF NOT EXISTS`` to ``ADD COLUMN`` for PostgreSQL, so
the DDL below is written once in SQLite dialect. It also rewrites ``INSERT OR
IGNORE`` to a bare ``INSERT`` on PostgreSQL — which is why nothing in this
package relies on ``INSERT OR IGNORE`` for deduplication. Dedupe is a documented
decision made by the writer services, not a side effect of a statement that
means two different things on two engines.

Identity columns
----------------
``fact_key``, ``node_key`` and ``edge_key`` exist so uniqueness can be expressed
as a plain composite ``UNIQUE`` that behaves identically on both engines. The
alternative — a partial unique index over nullable columns — has different NULL
semantics and different syntax per engine, and would put the dedupe rule in the
schema where it cannot be read alongside the code that depends on it. The rule
lives in the writer; the column just carries its result.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger("private_office.schema")

FACTS_TABLE = "private_facts"
NODES_TABLE = "private_graph_nodes"
EDGES_TABLE = "private_graph_edges"
AUDIT_TABLE = "private_audit_events"
SECURITY_TABLE = "private_office_security"
GRANTS_TABLE = "private_office_unlock_grants"

TABLES: tuple[str, ...] = (
    FACTS_TABLE, NODES_TABLE, EDGES_TABLE, AUDIT_TABLE,
    SECURITY_TABLE, GRANTS_TABLE,
)

STATUS_READY = "ready"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"

REASON_SCHEMA_MISSING = "schema_missing"
REASON_SCHEMA_ENSURE_FAILED = "schema_ensure_failed"


# ---------------------------------------------------------------------------
# Stage 6 — the private fact store
# ---------------------------------------------------------------------------
# `subject_type` + `subject_id` is what makes this a fact *about something*
# rather than a sentence. Ordinarily the subject is a graph node
# (`subject_type='NODE'`, `subject_id` = the node id), but it is deliberately
# not a foreign key: a fact may be recorded about a platform row that has no
# node yet, and a fact store that refuses the write until the graph catches up
# would push callers into keeping their own.
#
# `valid_to` NULL means "still holds". That is the common case and it is why
# `valid_to` is nullable while `valid_from` is not: a fact with no beginning is
# a fact that cannot be placed in time, and the contradiction engine's whole
# job is comparing overlapping periods.
FACTS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {FACTS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    fact_key TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    value_type TEXT NOT NULL,
    typed_value TEXT NOT NULL,
    value_number REAL,
    provenance_type TEXT NOT NULL,
    provenance_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    observed_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    sensitivity TEXT NOT NULL,
    domain TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',
    conflict_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_user_id, fact_key)
)
"""

# ---------------------------------------------------------------------------
# Stage 8 — graph nodes
# ---------------------------------------------------------------------------
# `external_ref` points at whatever already identifies this thing elsewhere —
# a marketplace listing id, a business id, a document id, or nothing at all for
# an entity the member described that the platform has no row for. It is text
# rather than an integer for exactly that reason.
#
# Note what is *not* here: no name, no label, no description. Stage 11 is
# explicit that the graph holds relationships and the fact store holds
# assertions, and a `display_name` column would be the first fact to leak back
# into the graph. A property's address is a fact about the property.
NODES_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {NODES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    node_key TEXT NOT NULL,
    node_type TEXT NOT NULL,
    external_ref TEXT NOT NULL DEFAULT '',
    lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',
    sensitivity TEXT NOT NULL,
    domain TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_user_id, node_key)
)
"""

# ---------------------------------------------------------------------------
# Stage 9 — graph edges
# ---------------------------------------------------------------------------
# `owner_user_id` is stored on the edge even though both endpoints already carry
# it. That is intentional redundancy: it means the owner predicate can be
# applied to the edge table directly, in the same WHERE clause as the traversal
# step, instead of via two joins that a future query could forget one of. The
# writer enforces that all three agree (Stage 10), so the redundancy cannot
# drift into a disagreement.
EDGES_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {EDGES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    edge_key TEXT NOT NULL,
    source_node_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    target_node_id INTEGER NOT NULL,
    provenance_type TEXT NOT NULL,
    provenance_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_user_id, edge_key)
)
"""

# ---------------------------------------------------------------------------
# Stage 18 — private audit, metadata only
# ---------------------------------------------------------------------------
# There is no `value` column, no `detail_json`, no `payload`. That is the
# design, not an omission. Stage 18 draws the line at object *identity* and
# refuses object *content*: `object_type=INSURANCE_POLICY, object_id=382` is a
# usable audit record; `policy_number=...` is a second copy of the secret,
# stored in the one table that is retained longest and read by the most people.
#
# The absence of a free-text column is what makes that guarantee checkable. A
# reviewer does not have to audit every call site to know values are not being
# logged — there is nowhere to put them.
AUDIT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER NOT NULL,
    owner_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL DEFAULT '',
    object_id TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""

# ---------------------------------------------------------------------------
# Second lock — the office passcode record and its unlock grants
# ---------------------------------------------------------------------------
# `passcode_hash` holds a salted KDF hash and NOTHING else ever holds the
# passcode: no plaintext column exists anywhere, and the audit table (above)
# structurally cannot carry one. `hash_version` names the KDF so the scheme can
# be migrated by rehash-on-successful-verify without a big-bang migration.
# `failed_attempt_count` and `locked_until` are the server-side rate limit —
# the client's counter is UX, this row is the law. UNIQUE(user_id): one lock
# per member, by construction.
SECURITY_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {SECURITY_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    passcode_hash TEXT NOT NULL,
    hash_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    failed_attempt_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT NOT NULL DEFAULT '',
    biometric_preference TEXT NOT NULL DEFAULT 'unset',
    UNIQUE(user_id)
)
"""

# A grant row is the server's memory that this member proved the passcode on
# this session/device recently. `token_hash` — never the token itself — is
# stored, so the table cannot be read back into working unlock tokens.
# `session_binding` / `device_binding` scope the grant (Stage 14): an unlock on
# device A is meaningless presented from device B. `revoked_at` beats
# `expires_at`: lock-now, passcode change and account security events revoke
# explicitly rather than waiting out the clock.
GRANTS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {GRANTS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    session_binding TEXT NOT NULL DEFAULT '',
    device_binding TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'private_office',
    nonce TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    revoke_reason TEXT NOT NULL DEFAULT '',
    UNIQUE(token_hash)
)
"""

TABLE_DDL: dict[str, str] = {
    FACTS_TABLE: FACTS_TABLE_DDL,
    NODES_TABLE: NODES_TABLE_DDL,
    EDGES_TABLE: EDGES_TABLE_DDL,
    AUDIT_TABLE: AUDIT_TABLE_DDL,
    SECURITY_TABLE: SECURITY_TABLE_DDL,
    GRANTS_TABLE: GRANTS_TABLE_DDL,
}

#: Columns added after the first release. Empty today; the loop exists so the
#: first person who needs a column does not have to invent the mechanism, and
#: so `ensure` is already the place it goes rather than a route handler.
TABLE_ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    FACTS_TABLE: (),
    NODES_TABLE: (),
    EDGES_TABLE: (),
    AUDIT_TABLE: (),
    SECURITY_TABLE: (),
    GRANTS_TABLE: (),
}

#: Without these the store cannot answer its own questions, so their absence
#: must be reported as "could not look" rather than "looked and found nothing".
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    FACTS_TABLE: (
        "owner_user_id", "fact_key", "subject_type", "subject_id", "fact_type",
        "value_type", "typed_value", "provenance_type", "observed_at",
        "valid_from", "sensitivity", "domain", "lifecycle_state",
    ),
    NODES_TABLE: (
        "owner_user_id", "node_key", "node_type", "lifecycle_state",
        "sensitivity", "domain",
    ),
    EDGES_TABLE: (
        "owner_user_id", "edge_key", "source_node_id", "relation_type",
        "target_node_id", "provenance_type", "valid_from", "lifecycle_state",
    ),
    AUDIT_TABLE: ("actor_user_id", "owner_user_id", "action", "created_at"),
    SECURITY_TABLE: (
        "user_id", "passcode_hash", "hash_version", "created_at", "changed_at",
        "failed_attempt_count", "locked_until", "biometric_preference",
    ),
    GRANTS_TABLE: (
        "owner_user_id", "token_hash", "session_binding", "device_binding",
        "scope", "nonce", "issued_at", "expires_at", "revoked_at",
    ),
}

# ---------------------------------------------------------------------------
# Stage 37 — indexes
# ---------------------------------------------------------------------------
# Every index here leads with `owner_user_id`, which is not a performance
# accident. The owner predicate is on literally every read this package
# performs, so leading with it means the planner narrows to one member's rows
# first and a traversal step touches that member's edges rather than the table.
# It also means a query that somehow omitted the owner filter would be visibly
# slow rather than quietly correct-looking, which is a cheap second signal.
INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_private_facts_subject "
    f"ON {FACTS_TABLE} (owner_user_id, subject_type, subject_id, fact_type)",
    f"CREATE INDEX IF NOT EXISTS idx_private_facts_type "
    f"ON {FACTS_TABLE} (owner_user_id, fact_type, lifecycle_state)",
    f"CREATE INDEX IF NOT EXISTS idx_private_facts_domain "
    f"ON {FACTS_TABLE} (owner_user_id, domain, sensitivity)",
    f"CREATE INDEX IF NOT EXISTS idx_private_nodes_type "
    f"ON {NODES_TABLE} (owner_user_id, node_type, lifecycle_state)",
    f"CREATE INDEX IF NOT EXISTS idx_private_edges_source "
    f"ON {EDGES_TABLE} (owner_user_id, source_node_id, relation_type)",
    f"CREATE INDEX IF NOT EXISTS idx_private_edges_target "
    f"ON {EDGES_TABLE} (owner_user_id, target_node_id, relation_type)",
    f"CREATE INDEX IF NOT EXISTS idx_private_audit_actor "
    f"ON {AUDIT_TABLE} (actor_user_id, created_at)",
    f"CREATE INDEX IF NOT EXISTS idx_private_grants_owner "
    f"ON {GRANTS_TABLE} (owner_user_id, expires_at)",
)


class PrivateSchemaMissing(RuntimeError):
    """The Private Office tables are not usable on this database.

    A dedicated type so a caller can tell "this database needs a migration"
    apart from "this query broke", and report the first as a migration problem
    instead of burying it in a generic failure counter.
    """

    def __init__(self, missing: dict[str, tuple[str, ...]]):
        self.missing = {table: tuple(cols) for table, cols in missing.items()}
        detail = "; ".join(
            f"{table}: {', '.join(cols) or 'table absent'}"
            for table, cols in sorted(self.missing.items())
        )
        super().__init__(f"private office schema is not ready — {detail}")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Set only on success, and deliberately never on failure. A process that could
# not migrate must retry on its next interval rather than caching a broken
# answer for its whole lifetime — the difference between an outage that heals
# when a lock clears and one that needs a restart.
_SCHEMA_READY = False
_COLUMN_CACHE: dict[str, set[str]] = {}


# ---------------------------------------------------------------------------
# Stage 34 — which process is bootstrapping
# ---------------------------------------------------------------------------
# Recorded because Stage 176B's failure was not "the schema was missing" but
# "the schema was only ever created by a process that never runs in the place
# that needed it". A ready signal that does not say *who* is ready cannot
# distinguish a worker that migrated for itself from a worker that inherited a
# migration a web request happened to perform first — and the second one is a
# system that breaks the day the web process is redeployed before the worker.
_PROCESS_ROLE = "unknown"


def set_process_role(role: str) -> str:
    """Declare this process's role for the schema signals. Returns what stuck.

    Called once at startup by whatever owns the process — a worker's main loop,
    the web app factory, a script. Unrecognised roles are ignored rather than
    stored, so a typo shows up as ``unknown`` rather than as a new
    unaggregatable value in every metric this process emits.
    """
    global _PROCESS_ROLE
    if role in ("web", "worker", "script", "test"):
        _PROCESS_ROLE = role
    return _PROCESS_ROLE


def process_role() -> str:
    return _PROCESS_ROLE


def reset_schema_cache() -> None:
    """Forget both caches. For tests, and for any caller that changed a table."""
    global _SCHEMA_READY
    _SCHEMA_READY = False
    _COLUMN_CACHE.clear()


def table_columns(cur, table: str, *, refresh: bool = False) -> set[str]:
    """Columns actually present on ``table``.

    Raises rather than swallowing. The two callers want different behaviour on
    an introspection failure — :func:`ensure_private_schema` reports it as a
    structured error, a query builder treats it as "unknown shape, do not
    guess" — and a helper returning an empty set for both would make an
    unreachable database indistinguishable from a table with no columns.
    """
    if not refresh and table in _COLUMN_CACHE:
        return _COLUMN_CACHE[table]
    from services import db as db_module

    columns = db_module.get_table_columns(cur, table)
    _COLUMN_CACHE[table] = columns
    return columns


def missing_columns(present: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    """Required columns absent per table. Empty dict means the schema is usable."""
    gaps: dict[str, tuple[str, ...]] = {}
    for table, required in REQUIRED_COLUMNS.items():
        have = present.get(table) or set()
        absent = tuple(name for name in required if name not in have)
        if absent or not have:
            gaps[table] = absent
    return gaps


def _result(status: str, *, present=None, missing=None, added=None,
            error: str | None = None, cached: bool = False) -> dict:
    """Build the ensure result and emit the Stage 38 metric for it.

    The metric is emitted here rather than at each return site for the same
    reason the refusal metric in ``retrieval`` is centralised: every exit from
    :func:`ensure_private_schema` already goes through this function, so a
    future sixth outcome is counted without anyone remembering to count it.

    ``error`` carries a database message and so is never published — only
    whether there was one, via the ``state`` enum. The count of missing tables
    is published because "three tables absent" and "one column absent" call for
    different responses and neither reveals anything about a member.
    """
    from . import telemetry as _telemetry

    _telemetry.emit(
        _telemetry.EVENT_SCHEMA_STATE,
        state=status,
        process=_PROCESS_ROLE,
        missing_table_count=len(missing or {}),
        added_column_count=len(added or ()),
        cached=cached,
    )
    return {
        "status": status,
        "tables": {t: sorted(c) for t, c in (present or {}).items()},
        "missing": {t: list(c) for t, c in (missing or {}).items()},
        "added": list(added or ()),
        "error": error,
    }


def ensure_private_schema(cur, *, force: bool = False) -> dict:
    """Create the Private Office tables, columns and indexes. Never raises.

    ``status`` is one of:

    ``ready``    every required column on every table is present.
    ``missing``  the ensure completed and required columns are still absent —
                 e.g. the role cannot ``ALTER``. Callers must not read or write.
    ``error``    the ensure itself failed: locked, unreachable, no permission.
                 Callers must not read or write; the next interval retries.

    ``force`` bypasses the process cache, for tests and for any caller with
    reason to believe the tables changed underneath it.

    On cost: after the first success this is a dictionary read. Before it, four
    ``CREATE TABLE IF NOT EXISTS``, four introspections and seven
    ``CREATE INDEX IF NOT EXISTS`` — cheap enough to leave in a worker's path
    rather than only at boot, which is the property Stage 34 actually asks for.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return _result(STATUS_READY, present=dict(_COLUMN_CACHE), cached=True)

    for table, ddl in TABLE_DDL.items():
        try:
            cur.execute(ddl)
        except Exception as exc:
            # Not fatal on its own. The overwhelmingly common production case is
            # that the table already exists and this is a no-op, and a
            # `CREATE TABLE IF NOT EXISTS` that raises anyway — a concurrent
            # creator, a role with ALTER but not CREATE — should still let the
            # column check below decide whether the schema is usable.
            LOGGER.warning("PRIVATE_SCHEMA_TABLE_DDL_FAILED table=%s error=%s", table, exc)

    present: dict[str, set[str]] = {}
    for table in TABLES:
        try:
            present[table] = table_columns(cur, table, refresh=True)
        except Exception as exc:
            LOGGER.exception("PRIVATE_SCHEMA_ENSURE_FAILED stage=introspect table=%s", table)
            return _result(STATUS_ERROR, error=f"{table}: {str(exc)[:400]}")

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
                # correct outcome, not an error. Anything else (no ALTER grant,
                # a lock) shows up below as a column that is still missing.
                LOGGER.exception("PRIVATE_COLUMN_ADD_FAILED table=%s column=%s", table, column)

    for statement in INDEX_DDL:
        try:
            cur.execute(statement)
        except Exception as exc:
            # Non-fatal by design: a missing index makes retrieval slow, a
            # missing column makes it impossible. Only the second one blocks.
            LOGGER.warning("PRIVATE_INDEX_CREATE_FAILED error=%s", exc)

    if added:
        for table in TABLES:
            try:
                present[table] = table_columns(cur, table, refresh=True)
            except Exception as exc:
                LOGGER.exception("PRIVATE_SCHEMA_ENSURE_FAILED stage=verify table=%s", table)
                return _result(STATUS_ERROR, added=added, error=f"{table}: {str(exc)[:400]}")

    gaps = missing_columns(present)
    if gaps:
        LOGGER.error(
            "PRIVATE_SCHEMA_MISSING tables=%s added=%s",
            ";".join(f"{t}:{','.join(c) or 'absent'}" for t, c in sorted(gaps.items())),
            ",".join(added) or "-",
        )
        return _result(STATUS_MISSING, present=present, missing=gaps, added=added)

    _SCHEMA_READY = True
    LOGGER.info(
        "PRIVATE_SCHEMA_READY tables=%s added=%s",
        ",".join(f"{t}({len(present[t])})" for t in TABLES),
        ",".join(added) or "-",
    )
    return _result(STATUS_READY, present=present, added=added)


def require_private_schema(cur, *, force: bool = False) -> dict:
    """:func:`ensure_private_schema`, but raise when the result is unusable.

    The read and write services call this: they have no honest degraded answer
    to give, and returning an empty list from a store that could not be reached
    is the exact failure Stage 176B was about — "found nothing" and "could not
    look" must not share a shape. Loop callers that need to survive should keep
    calling :func:`ensure_private_schema` and branch on ``status``.
    """
    result = ensure_private_schema(cur, force=force)
    if result["status"] == STATUS_READY:
        return result
    if result["status"] == STATUS_ERROR:
        raise PrivateSchemaMissing({"__ensure__": (result.get("error") or "error",)})
    raise PrivateSchemaMissing(result["missing"])
