"""PulseDrop's own tables, created idempotently at first use.

## Why not ``bot.init_db()``

``init_db`` is the schema for the product. PulseDrop's tables are the schema for
one subsystem that may be switched off entirely, and putting them there would
mean every boot of every process pays for DDL on five tables nobody is reading.
The precedent for a subsystem owning its own DDL next to the code that queries
it is ``pulse_ai/automated_image_pipeline._ensure_tables``, and the reason to
prefer it is the one recorded against ``init_db``: a table added there after
``schema_guard`` existed quietly misses the guard, so the guard's coverage is a
claim about when a table was written rather than about the table.

## Why it is gated behind a module flag

``CREATE INDEX IF NOT EXISTS`` takes a ShareLock on PostgreSQL *even when the
index already exists*. Running this per request would serialise every PulseDrop
read behind a lock that is only ever needed once per process. So it runs once,
sets a flag, and the flag is per process — which is correct, because the thing
it is caching is "this deployment's database has been reconciled", and a new
process is a new chance for that to be false.

## Column conventions

Timestamps are ISO-8601 TEXT, matching every other table in this codebase, not
because that is good but because a mixed schema is worse. ``INTEGER PRIMARY KEY
AUTOINCREMENT`` is rewritten to ``SERIAL PRIMARY KEY`` by ``services/db.py``'s
translation layer, so it is safe on both engines despite reading as SQLite.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_ready = False

#: The tick coordinator. One row, key 'curator'.
#:
#: ``next_run_at`` and the lease live together because they answer the same
#: question from different sides: the lease says "an instance is inside the tick
#: right now", ``next_run_at`` says "no instance may start one yet". Separating
#: them would let an instance acquire a lease it is not allowed to use.
_LEASES = """
CREATE TABLE IF NOT EXISTS pulsedrop_leases (
    lease_key TEXT PRIMARY KEY,
    owner TEXT,
    acquired_at TEXT,
    expires_at TEXT,
    next_run_at TEXT,
    updated_at TEXT
)
"""

_SETTINGS = """
CREATE TABLE IF NOT EXISTS pulsedrop_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT,
    updated_by INTEGER DEFAULT 0,
    updated_at TEXT
)
"""

#: One row per thing PulseDrop published, or tried to.
#:
#: ``idempotency_key`` is the race guard and it is UNIQUE, which is the whole
#: mechanism: the claim is an INSERT, so two instances cannot both win it, and a
#: retry of the same intent collides with its own earlier claim instead of
#: publishing a second post. ``pulse_posts`` has no such key — ``create_post``
#: will happily create the same post twice — so the key has to live here.
#:
#: ``post_id`` is nullable because the claim is written *before* the post
#: exists. A row with a null ``post_id`` and state 'claimed' is an in-flight
#: publication; one that stays that way is a crashed one, and is reclaimable.
#:
#: ``claimed_by`` is what makes the claim readable. ``ON CONFLICT DO NOTHING``
#: leaves the caller unable to tell "I inserted this" from "it was already
#: here" without trusting ``rowcount``, whose semantics for a suppressed insert
#: differ between the two drivers. Writing the owner and reading it back is the
#: same answer on both engines, and it also names which instance is holding an
#: in-flight publication — which is the first question when one is stuck.
_PUBLICATIONS = """
CREATE TABLE IF NOT EXISTS pulsedrop_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT UNIQUE,
    claimed_by TEXT DEFAULT '',
    surface TEXT DEFAULT 'signal',
    listing_id INTEGER DEFAULT 0,
    seller_user_id INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    editorial_label TEXT DEFAULT '',
    rank_score REAL DEFAULT 0,
    ranker_version TEXT DEFAULT '',
    render_id INTEGER DEFAULT 0,
    post_id INTEGER,
    reel_id INTEGER DEFAULT 0,
    state TEXT DEFAULT 'claimed',
    failure_reason TEXT DEFAULT '',
    published_at TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

#: Rendered Reel media, keyed by what it was rendered *from*.
#:
#: Deliberately not keyed by post id — unlike ``pulse_generated_media``, which
#: enriches a post that already exists. A PulseDrop Reel cannot be posted until
#: its video exists, so the render has to be addressable before there is a post
#: to hang it on. The key is therefore the product plus the source media plus
#: the composition version, which also means: a seller who replaces their
#: product photos gets a new render, and a composition change gets a new render,
#: but a retry of the same inputs reuses the file instead of re-encoding it.
_RENDERS = """
CREATE TABLE IF NOT EXISTS pulsedrop_renders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER DEFAULT 0,
    composition_version TEXT DEFAULT '',
    source_kind TEXT DEFAULT '',
    source_fingerprint TEXT DEFAULT '',
    state TEXT DEFAULT 'pending',
    media_id INTEGER DEFAULT 0,
    video_url TEXT DEFAULT '',
    poster_url TEXT DEFAULT '',
    duration_seconds REAL DEFAULT 0,
    frame_width INTEGER DEFAULT 0,
    frame_height INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    failure_reason TEXT DEFAULT '',
    claimed_by TEXT DEFAULT '',
    claimed_at TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

#: One row per evaluation, whether or not it published.
#:
#: The counters exist because "PulseDrop has not posted in two days" has at
#: least four distinct causes — switched off, nothing eligible, everything on
#: cooldown, or crashing — and without a run log they are indistinguishable from
#: the outside. ``rejected_json`` carries the reason histogram so the answer is
#: one row, not a log search.
_RUNS = """
CREATE TABLE IF NOT EXISTS pulsedrop_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE,
    outcome TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    evaluated INTEGER DEFAULT 0,
    eligible INTEGER DEFAULT 0,
    rejected_json TEXT DEFAULT '',
    selected_listing_id INTEGER DEFAULT 0,
    decision TEXT DEFAULT '',
    post_id INTEGER DEFAULT 0,
    reel_post_id INTEGER DEFAULT 0,
    renders_started INTEGER DEFAULT 0,
    renders_succeeded INTEGER DEFAULT 0,
    renders_failed INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_pub_listing ON pulsedrop_publications(listing_id, surface, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_pub_seller ON pulsedrop_publications(seller_user_id, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_pub_post ON pulsedrop_publications(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_pub_state ON pulsedrop_publications(state, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_renders_lookup ON pulsedrop_renders(listing_id, composition_version, source_fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_renders_state ON pulsedrop_renders(state, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_runs_started ON pulsedrop_runs(started_at)",
)

_TABLES = (_LEASES, _SETTINGS, _PUBLICATIONS, _RENDERS, _RUNS)


def ensure_schema(conn=None) -> None:
    """Create PulseDrop's tables and indexes. Safe to call repeatedly.

    ``conn`` is accepted for callers that are already inside a transaction, but
    passing one hands them the commit: DDL issued on a caller's connection and
    left uncommitted rolls back at the end of their request, and a second
    connection then blocks behind the lock the first one never released. That
    failure is recorded in this repo's history; the default path opens its own
    connection and commits it, which is why ``conn`` defaults to None.
    """
    global _ready
    if conn is not None:
        _create(conn.cursor())
        return
    with _lock:
        if _ready:
            return
        from services import db as db_service

        connection = db_service.connect()
        try:
            _create(connection.cursor())
            connection.commit()
            _ready = True
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            connection.close()


def _create(cur) -> None:
    for statement in _TABLES:
        cur.execute(statement)
    for statement in _INDEXES:
        try:
            cur.execute(statement)
        except Exception:
            # An index is an optimisation. A deployment where one cannot be
            # created (a concurrent build holding the lock, most likely) still
            # has correct tables, and blocking the curator on it would turn a
            # slow query into no PulseDrop at all.
            log.warning("pulsedrop_index_skipped statement=%s", statement, exc_info=True)


def reset_ready_flag() -> None:
    """Forget that the schema was reconciled. For tests that swap databases."""
    global _ready
    with _lock:
        _ready = False
