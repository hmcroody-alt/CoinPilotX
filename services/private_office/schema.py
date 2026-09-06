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
FACT_HISTORY_TABLE = "private_fact_history"
FACT_EVIDENCE_TABLE = "private_fact_evidence"
FACT_CONFLICTS_TABLE = "private_fact_conflicts"
NODES_TABLE = "private_graph_nodes"
EDGES_TABLE = "private_graph_edges"
AUDIT_TABLE = "private_audit_events"
SECURITY_TABLE = "private_office_security"
GRANTS_TABLE = "private_office_unlock_grants"

TABLES: tuple[str, ...] = (
    FACTS_TABLE, FACT_HISTORY_TABLE, FACT_EVIDENCE_TABLE,
    FACT_CONFLICTS_TABLE, NODES_TABLE,
    EDGES_TABLE, AUDIT_TABLE, SECURITY_TABLE, GRANTS_TABLE,
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
    verification_state TEXT NOT NULL DEFAULT 'UNVERIFIED',
    verified_at TEXT NOT NULL DEFAULT '',
    verified_by INTEGER NOT NULL DEFAULT 0,
    supersedes_id INTEGER NOT NULL DEFAULT 0,
    superseded_by_id INTEGER NOT NULL DEFAULT 0,
    superseded_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_user_id, fact_key)
)
"""
# `verification_state` is a column of its own and not a value of
# `provenance_type`, which is the single most consequential shape decision in
# this table. Provenance says where the value came from; verification says what
# anyone has done to check it. Sharing one column means recording a check
# destroys the record of the origin, and it means a trusted feed's output is
# indistinguishable from a document somebody actually read.
#
# `verified_at` and `verified_by` are stored while expiry is *not*. The first
# two are facts about an event that happened — a check occurred, at a time, by
# an actor — and no later reading can recover them. Expiry is arithmetic over
# `verified_at` and a horizon, so storing it would create a window in which the
# row says "verified" because nothing has swept it yet, and that window is
# precisely when somebody acts on it.
#
# `verified_by` is 0 rather than NULL for a system-performed check, so the
# column never has to be read as tri-state. It names an actor, not an owner:
# owner is already on the row and a verification performed by the owner
# themselves is a weaker claim than one performed by anyone else, which is a
# distinction the review queue needs to keep.
#
# `supersedes_id` / `superseded_by_id` are integers rather than a join table
# because supersession is a chain and a chain has exactly one predecessor and
# one successor per link. A join table would permit a row to be superseded by
# two different corrections at once, which is not a supersession — it is a
# contradiction, and it already has a home in `conflict_id`. Both default to 0
# rather than NULL so a cycle check is integer comparison with no NULL
# handling, and 0 is not a valid `id` in this table, so it cannot collide with
# a real link.

# ---------------------------------------------------------------------------
# Fact history — the transition log
# ---------------------------------------------------------------------------
# Append-only. Nothing in this package updates or deletes a row here, and the
# writer exposes no function that could.
#
# **There is no value column, and that is a design decision rather than an
# omission.** The obvious shape for a history table is "old value, new value",
# and it is the wrong one here for two reasons that compound.
#
# The first is that supersession already preserves the old value: correcting a
# fact writes a *new row* and links the old one, so the previous value is still
# sitting in `private_facts` with its own sensitivity, its own domain and its own
# owner predicate. Copying it here would put the same private value in a second
# table with a second set of rules, and the second set is always the one that
# gets forgotten when someone adds a read.
#
# The second is that this table is the thing a member reads to understand what
# happened, which means it is the thing most likely to be rendered in a list, and
# a list of every value a fact has ever held is a far more sensitive object than
# the fact itself. A history of "what changed and when" is answerable without it.
#
# `note_key` is a vocabulary key, never free text and never a value. It exists so
# a transition can carry *why* — `conflict_resolution`, `legacy_backfill` — in a
# form that translates and that cannot be turned into a smuggling channel by a
# caller who passes a string.
FACT_HISTORY_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {FACT_HISTORY_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    fact_id INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL DEFAULT 0,
    from_state TEXT NOT NULL DEFAULT '',
    to_state TEXT NOT NULL DEFAULT '',
    related_fact_id INTEGER NOT NULL DEFAULT 0,
    note_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

# ---------------------------------------------------------------------------
# What a fact is believed *on the basis of*.
#
# The claim this table has to make good on is the one a badge makes: if a fact
# reads AUTHORITY_VERIFIED, a member must be able to tap it and see what it was
# verified against. A verification state with nothing behind it is worse than an
# unverified fact, because the member acts on it.
#
# `source_ref` is a canonical `kind:id` reference in the `evidence` module's
# vocabulary — `document:7`, `record:12`, `event:88`. Identity, never content:
# the row it names is read back through its own owning module's gated reader,
# and this table stores no copy of what the source said. That is what keeps a
# document's contents governed by the document module's rules rather than by
# whatever rules the fact detail screen happens to apply.
#
# **Detached, never deleted.** `detached_at` is a timestamp rather than an
# absence of a row, because unlinking evidence is exactly the operation that
# would otherwise erase the answer to "what was this verified against, back when
# it was verified". A fact whose supporting document was later unlinked still
# has to be able to explain the badge it was wearing at the time. Live evidence
# is `detached_at = ''`; everything else is history and reads as history.
#
# `relation` matters more than it looks. Evidence that *contradicts* a fact is
# still evidence and still belongs on the record — but it must never count
# toward verification, and it would if this column did not exist and every link
# were assumed supportive.
FACT_EVIDENCE_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {FACT_EVIDENCE_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    fact_id INTEGER NOT NULL,
    source_ref TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'SUPPORTS',
    note_key TEXT NOT NULL DEFAULT '',
    linked_by INTEGER NOT NULL DEFAULT 0,
    linked_at TEXT NOT NULL,
    detached_at TEXT NOT NULL DEFAULT '',
    detached_by INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""

# ---------------------------------------------------------------------------
# What a member decided about a contradiction.
#
# The detector is deliberately incapable of closing a conflict — it reports and
# stops. That leaves a gap this table fills: without somewhere durable to record
# "I looked at this, they are two different policies", every detection pass
# re-raises a disagreement the member already settled, and a queue that keeps
# returning answered questions is a queue they stop opening.
#
# **Append-only, one row per decision.** Not one row per conflict updated in
# place. A member who defers a conflict in March and settles it in June has made
# two decisions, and the first one is the reason the second took until June —
# that is exactly the kind of thing an audit of "why did this sit unresolved"
# needs, and an UPDATE would erase it. The live answer is the newest row.
#
# `conflict_id` is the detector's deterministic hash of the *competing fact
# keys*, which gives this table a property worth stating plainly: a resolution
# binds to the precise set of claims it was made about. If a third source later
# disagrees, the competing set changes, the hash changes, and the conflict
# re-raises as a new one rather than inheriting a decision made without
# knowledge of the new claim. Settling a two-way disagreement is not consent to
# a three-way one.
#
# `competing_fact_ids` duplicates what the hash already encodes, and is stored
# anyway: the hash proves two sets are the same set but cannot say *which* rows
# they were, and "what exactly did I agree to" is unanswerable from a digest.
#
# `kept_fact_id` is 0 for every outcome except KEPT. A KEPT that did not name
# which fact was kept would record that the member chose without recording what
# they chose, which is not a resolution — it is the appearance of one.
#
# There is no `note` column, only `note_key`. Same reason as the history table:
# a free-text field here would be the one place a caller could write arbitrary
# member prose into a table with no sensitivity column to govern who reads it.
FACT_CONFLICTS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {FACT_CONFLICTS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    conflict_id TEXT NOT NULL,
    subject_type TEXT NOT NULL DEFAULT '',
    subject_id TEXT NOT NULL DEFAULT '',
    fact_type TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    competing_fact_ids TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL,
    kept_fact_id INTEGER NOT NULL DEFAULT 0,
    note_key TEXT NOT NULL DEFAULT '',
    resolved_by INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT NOT NULL,
    created_at TEXT NOT NULL
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
    FACT_HISTORY_TABLE: FACT_HISTORY_TABLE_DDL,
    FACT_EVIDENCE_TABLE: FACT_EVIDENCE_TABLE_DDL,
    FACT_CONFLICTS_TABLE: FACT_CONFLICTS_TABLE_DDL,
    NODES_TABLE: NODES_TABLE_DDL,
    EDGES_TABLE: EDGES_TABLE_DDL,
    AUDIT_TABLE: AUDIT_TABLE_DDL,
    SECURITY_TABLE: SECURITY_TABLE_DDL,
    GRANTS_TABLE: GRANTS_TABLE_DDL,
}

#: Columns added after the first release, applied by ``ensure`` as idempotent
#: ALTERs. Every definition here must carry a DEFAULT, because the ALTER runs
#: against a table that already holds rows and a NOT NULL column with no default
#: cannot be added to one.
#:
#: The defaults are also the backfill, and they were chosen to be the honest
#: reading of an existing row rather than a convenient one. A fact written
#: before this column existed has not been verified — nobody checked it, because
#: there was no mechanism to record a check — so 'UNVERIFIED' is not a
#: placeholder, it is correct. Defaulting to SELF_ASSERTED would have been the
#: tempting choice and it would have been a fabrication: it asserts the owner
#: personally stated the value, which is unknown for every legacy row.
TABLE_ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    FACTS_TABLE: (
        ("verification_state", "TEXT NOT NULL DEFAULT 'UNVERIFIED'"),
        ("verified_at", "TEXT NOT NULL DEFAULT ''"),
        ("verified_by", "INTEGER NOT NULL DEFAULT 0"),
        ("supersedes_id", "INTEGER NOT NULL DEFAULT 0"),
        ("superseded_by_id", "INTEGER NOT NULL DEFAULT 0"),
        ("superseded_at", "TEXT NOT NULL DEFAULT ''"),
    ),
    FACT_HISTORY_TABLE: (),
    FACT_EVIDENCE_TABLE: (),
    FACT_CONFLICTS_TABLE: (),
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
        # Required rather than optional. Every read model below asks what a
        # fact's verification state is and where its supersession chain leads,
        # so a database missing these cannot answer the store's own questions —
        # and the fail-closed reading of that is "could not look", not a page
        # that renders every fact as unverified because the column is absent.
        "verification_state", "supersedes_id", "superseded_by_id",
    ),
    FACT_HISTORY_TABLE: (
        "owner_user_id", "fact_id", "change_type", "created_at",
    ),
    FACT_EVIDENCE_TABLE: (
        # `detached_at` is required, not optional. Every live-evidence query
        # filters on it, and a database missing it would answer "is this fact
        # supported" by counting links the member had already withdrawn.
        "owner_user_id", "fact_id", "source_ref", "relation", "detached_at",
    ),
    FACT_CONFLICTS_TABLE: (
        # `outcome` decides whether a conflict is closed, so a database missing
        # it cannot tell a settled disagreement from an open one — and the
        # failure would be silent re-raising of questions already answered.
        "owner_user_id", "conflict_id", "outcome", "resolved_at",
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
    # The review queue's predicate. Verification state leads because the queue
    # is built by asking which states need attention, and lifecycle follows
    # because a superseded fact needing review is not a task — it has already
    # been answered by the row that replaced it.
    f"CREATE INDEX IF NOT EXISTS idx_private_facts_verification "
    f"ON {FACTS_TABLE} (owner_user_id, verification_state, lifecycle_state)",
    # Chain walks in both directions. `superseded_by_id` is the forward walk —
    # "what replaced this" — and it is the one a detail view runs, so it gets
    # the index; the backward walk is bounded by the same chain and reads the
    # same rows.
    f"CREATE INDEX IF NOT EXISTS idx_private_facts_supersession "
    f"ON {FACTS_TABLE} (owner_user_id, superseded_by_id)",
    # One fact's history, newest first. `fact_id` follows the owner rather than
    # standing alone because every read of this table is already scoped to an
    # owner and an index that could serve an unscoped read is an invitation to
    # write one.
    f"CREATE INDEX IF NOT EXISTS idx_private_fact_history_fact "
    f"ON {FACT_HISTORY_TABLE} (owner_user_id, fact_id, id)",
    # One fact's evidence. `detached_at` is in the index rather than left to a
    # filter because the question this table is asked most often is "what
    # supports this *now*", and that read runs on the verification path where a
    # wrong answer is a badge with nothing behind it.
    f"CREATE INDEX IF NOT EXISTS idx_private_fact_evidence_fact "
    f"ON {FACT_EVIDENCE_TABLE} (owner_user_id, fact_id, detached_at)",
    # The other direction: "what does this document support". The SOURCES tab
    # groups by source, and the integrity sweep walks every ref of one kind.
    f"CREATE INDEX IF NOT EXISTS idx_private_fact_evidence_source "
    f"ON {FACT_EVIDENCE_TABLE} (owner_user_id, source_ref, detached_at)",
    # "How was this conflict settled" — asked once per conflict on every
    # detection pass, so that a decision already made is not re-raised. `id`
    # trails the key because the table is append-only and the live answer is the
    # newest row, which makes this an index-ordered lookup rather than a sort.
    f"CREATE INDEX IF NOT EXISTS idx_private_fact_conflicts_conflict "
    f"ON {FACT_CONFLICTS_TABLE} (owner_user_id, conflict_id, id)",
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
