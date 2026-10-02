"""PulseDrop's own tables, created idempotently at first use.

## Why not ``bot.init_db()``

``init_db`` is the schema for the product. PulseDrop's tables are the schema for
one subsystem that may be switched off entirely, and putting them there would
mean every boot of every process pays for DDL on every table in :data:`_TABLES`
whether or not anything is reading them. (That sentence used to name a count.
The count was wrong twice — once when the audio bed table landed and again when
campaigns did — which is its own small argument for not writing numbers that a
later commit has no reason to look for.)
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

#: The one place a human says "PulseDrop may put this track under a Reel".
#:
#: It exists because ``pulse_audio_tracks`` cannot answer that question, and the
#: reason is visible in its own data: every track in production carries
#: ``proof_url='artist-upload:<uid>:<timestamp>'`` and ``rights_statement="I
#: confirm that I own this music or have the legal right to upload it."`` That is
#: an uploader ticking a box. It is a fine basis for a member attaching a track
#: to their own Reel — the member carries the risk — and it is not a basis for
#: the platform's own verified account to synchronise music into commercial
#: content it earns on, which is a different licence entirely.
#:
#: So clearance is a second, explicit act, recorded apart from the upload: who
#: cleared it, when, and on what grounds. An empty table means PulseDrop's Reels
#: are silent, which is the resting state and needs no switch to reach.
#:
#: Note what is *not* stored here: the title, the artist, the URL, or any
#: licence flag. Those stay in ``pulse_audio_tracks`` and are re-read at
#: selection time, so a takedown, a deactivation or a legal hold applied over
#: there removes the bed here without anybody remembering to.
_AUDIO_BEDS = """
CREATE TABLE IF NOT EXISTS pulsedrop_audio_beds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_track_id INTEGER UNIQUE,
    active INTEGER DEFAULT 1,
    cleared_by INTEGER DEFAULT 0,
    clearance_note TEXT DEFAULT '',
    cleared_at TEXT,
    revoked_at TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

#: One row per product per cycle: the unit Pulse Loop plans, schedules and
#: publishes.
#:
#: ## Why a campaign exists at all, given ``pulsedrop_publications``
#:
#: A publication is a record of something that *has happened*, written at the
#: moment it happens. The whole of PulseDrop before Pulse Loop was that: decide
#: now, publish now, record now. A campaign is the opposite tense — a row that
#: says what *will* happen, written before it does — and the two cannot be the
#: same table, because a publication's identity is its ``idempotency_key`` of
#: surface plus listing plus *today's date*, which is unknowable for an intent
#: that will not be acted on until Thursday.
#:
#: The campaign is also what makes a Signal and a Reel one act rather than two.
#: Production shows why that matters: with the two surfaces claiming
#: independently, the Signals all published in one burst and then sat on a
#: 14-day product cooldown while the Reels carried on alone, so the account
#: emitted nothing but ``REEL_ONLY`` for days while an operator who had asked for
#: pairs believed they were getting them. Both publication ids hang off one
#: campaign row here, so "did this product get its pair" is a column rather than
#: an inference across two surfaces and a date range.
#:
#: ## Why ``scheduled_for`` is a time and not a position
#:
#: A queue position would have to be rewritten every time anything was inserted,
#: released or reordered, and a crash halfway through that rewrite leaves a
#: schedule with two sevenths and no sixth. An absolute time needs no
#: renumbering: planning appends, releasing deletes, and "what is next" is
#: ``ORDER BY scheduled_for`` over rows nobody else had to touch.
#:
#: ## Why the state machine has ``released`` as well as ``failed``
#:
#: These are the two different ways a campaign can end without publishing, and
#: collapsing them would hide the one that matters. ``failed`` is ours — the
#: encode died, the post call raised, we are out of attempts. ``released`` is the
#: catalog's: the product went out of stock, lost its price, was unpublished or
#: was moderated between the moment it was planned and the moment it came due.
#:
#: A released campaign is not an error and must not alert, but it *is* a slot
#: that the replenisher has to refill, and the product has to become re-plannable
#: later without being treated as recently published — because it was not
#: published at all. A single ``failed`` state would have made the alert on
#: failures fire on ordinary catalog churn, which is the fastest way to teach an
#: operator to ignore it.
#:
#: ## Why ``cycle`` is on the row
#:
#: "Do not repeat a product until you have worked through the others" is a
#: statement about generations, and the cheap version of it — order by last
#: published time — silently degrades into a 37-product carousel the moment the
#: catalog is smaller than the horizon. Stamping the generation makes exhaustion
#: *visible*: a cycle with no plannable products left is the signal to either
#: open the next one or report that supply is the constraint, and those are very
#: different operational answers that a timestamp cannot distinguish.
_CAMPAIGNS = """
CREATE TABLE IF NOT EXISTS pulsedrop_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_key TEXT UNIQUE,
    cycle INTEGER DEFAULT 1,
    listing_id INTEGER DEFAULT 0,
    seller_user_id INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    state TEXT DEFAULT 'scheduled',
    scheduled_for TEXT,
    rank_score REAL DEFAULT 0,
    want_signal INTEGER DEFAULT 1,
    want_reel INTEGER DEFAULT 1,
    render_id INTEGER DEFAULT 0,
    signal_post_id INTEGER DEFAULT 0,
    reel_post_id INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    claimed_by TEXT DEFAULT '',
    claimed_at TEXT,
    failure_reason TEXT DEFAULT '',
    released_reason TEXT DEFAULT '',
    planned_at TEXT,
    published_at TEXT,
    created_at TEXT,
    updated_at TEXT
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
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_beds_active ON pulsedrop_audio_beds(active, audio_track_id)",
    # The hot path: "what is due now". Every tick runs it, so it is a covering
    # order rather than a filter -- `state` first because it is the selective
    # column (a horizon holds hundreds of 'scheduled' rows and a handful of
    # anything else), `scheduled_for` second because that is the sort.
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_campaigns_due ON pulsedrop_campaigns(state, scheduled_for)",
    # Queue depth and cycle exhaustion, both of which are counts grouped by
    # cycle, and the per-cycle enrollment anti-join.
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_campaigns_cycle ON pulsedrop_campaigns(cycle, state)",
    # "has this product ever been in a campaign, and how did it end" -- asked
    # once per candidate by the planner's cooldown check.
    "CREATE INDEX IF NOT EXISTS idx_pulsedrop_campaigns_listing ON pulsedrop_campaigns(listing_id, cycle)",
)

_TABLES = (_LEASES, _SETTINGS, _PUBLICATIONS, _RENDERS, _RUNS, _AUDIO_BEDS, _CAMPAIGNS)

#: Columns added to a table that already exists somewhere.
#:
#: Every statement above is ``CREATE TABLE IF NOT EXISTS``, which means editing
#: a column into one of those definitions has no effect on any database where
#: the table has already been created -- i.e. on production. There is no
#: migration framework here, so the additive case needs its own pass, and it has
#: to be driven by what the table actually has rather than by a version number
#: nobody updates.
_ADDED_COLUMNS = (
    # Mux ingest, added when it was found that PulseDrop was the only producer
    # of reels on the platform publishing a bucket URL instead of a Mux
    # playback id. Nullable with an empty default: a render created before this
    # existed is still a valid render, it simply has no asset recorded.
    ("pulsedrop_renders", "mux_asset_id", "TEXT DEFAULT ''"),
    ("pulsedrop_renders", "mux_playback_id", "TEXT DEFAULT ''"),
)


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


def _columns(cur, table: str) -> set[str]:
    """The column names ``table`` currently has, lowercased.

    ``SELECT * ... LIMIT 0`` rather than ``PRAGMA table_info`` or a query against
    ``information_schema``, because those are the SQLite and Postgres answers to
    the same question and this module runs on both. A zero-row select fills
    ``cursor.description`` on either engine and fetches nothing.
    """
    try:
        cur.execute(f"SELECT * FROM {table} LIMIT 0")
        return {str(item[0]).lower() for item in (cur.description or ())}
    except Exception:
        log.warning("pulsedrop_columns_unreadable table=%s", table, exc_info=True)
        return set()


def _add_columns(cur) -> None:
    """Apply :data:`_ADDED_COLUMNS`, skipping the ones already there.

    Guarded by a read rather than by catching the duplicate-column error,
    because on Postgres a failed statement aborts the surrounding transaction:
    the second ``ALTER`` and every ``CREATE INDEX`` after it would fail too, and
    ``ensure_schema`` would roll back the whole reconciliation on a database
    whose only problem was being already correct.
    """
    for table in {name for name, _, _ in _ADDED_COLUMNS}:
        have = _columns(cur, table)
        if not have:
            continue
        for candidate, column, spec in _ADDED_COLUMNS:
            if candidate != table or column.lower() in have:
                continue
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
                log.info("pulsedrop_column_added table=%s column=%s", table, column)
            except Exception:
                log.warning(
                    "pulsedrop_column_add_failed table=%s column=%s", table, column,
                    exc_info=True,
                )


def _create(cur) -> None:
    for statement in _TABLES:
        cur.execute(statement)
    _add_columns(cur)
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
