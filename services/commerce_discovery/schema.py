"""Tables for organic and house commerce discovery.

Five tables, all prefixed ``commerce_discovery_``. The prefix is the promotion
wall made physical: nothing in here is ever joined to ``business_os_ad_*``, so a
report can be wrong about a number but cannot be wrong about *which kind of
promotion produced it*. See ``promotion.py``.

Design choices worth stating once
---------------------------------

**Text primary keys, not autoincrement.** Ids are minted in Python
(``cd_impr_<uuid4>``) rather than by the database. Two reasons: the id is handed
to a client inside a placement payload and then quoted back on the impression,
so it must exist before the row does; and ``INTEGER PRIMARY KEY AUTOINCREMENT``
is a SQLite spelling that only survives the ``_translate_sql`` dialect layer,
which is one more thing to be right about for no gain here.

**No raw ``user_id`` anywhere in this file.** A viewer is ``subject_ref``, a
salted hash (``subject.py``). The engine needs to know "has *this* viewer seen
*this* product three times", which a stable hash answers, and it never needs to
know who they are. The single exception is ``seller_user_id``, which is not a
viewer — it is the merchant being promoted, it is already public on the listing,
and a seller reading their own reach stats needs it joinable.

**Events are append-only.** No UPDATE path exists for impressions, clicks or
feedback. An analytics row that can be edited is an analytics row that has to be
audited; a suppression, which genuinely *is* mutable state, lives in its own
table precisely so the event log does not have to be.

**Every event carries ``dedup_key UNIQUE``.** A retried request collides instead
of double-counting. This matters more here than in advertising, oddly: nobody is
being billed, so a duplicate would never be caught by a finance reconciliation —
it would just quietly inflate free-reach numbers forever.

PostgreSQL note: the DDL runs behind ``run_once_per_process`` because
``CREATE ... IF NOT EXISTS`` takes a ShareLock that conflicts with the writes
these same requests perform moments later. See ``services/schema_guard.py``.
"""

from __future__ import annotations

import logging

from services.schema_guard import run_once_per_process

LOGGER = logging.getLogger(__name__)


#: Surfaces a placement may be served to. A strict allowlist rather than free
#: text: an unrecognised surface would silently escape every per-surface
#: frequency cap, because the caps are keyed by this exact string.
SURFACES = ("feed", "reels", "messenger", "marketplace")

#: What a user can tell us, and what each one suppresses.
FEEDBACK_ACTIONS = (
    "hide",            # this one card, this one time
    "not_interested",  # this product, durably
    "see_fewer",       # soften the whole surface for a while
    "hide_seller",     # never this merchant again
    "snooze",          # all discovery, for COMMERCE_DISCOVERY_HIDE_DAYS
)

#: Suppression scopes, from narrowest to widest.
SUPPRESSION_SCOPES = ("product", "seller", "surface", "all")


_DDL = (
    # ---- served placements -------------------------------------------------
    # One row per card the server authorized a client to render. It is the
    # server-authoritative record of what was offered: the impression and click
    # routes read the listing, seller and promotion class back from HERE rather
    # than trusting the client's copy, so a tampered request can at worst
    # re-report a placement that genuinely happened.
    """
    CREATE TABLE IF NOT EXISTS commerce_discovery_placements (
        placement_id TEXT PRIMARY KEY,
        subject_ref TEXT NOT NULL,
        surface TEXT NOT NULL,
        slot INTEGER NOT NULL DEFAULT 0,
        listing_id INTEGER NOT NULL,
        seller_user_id INTEGER,
        promotion_class TEXT NOT NULL DEFAULT 'organic',
        reason_code TEXT,
        score REAL NOT NULL DEFAULT 0,
        score_breakdown_json TEXT,
        ranking_version TEXT NOT NULL,
        session_id TEXT,
        impression_token TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cd_placement_subject "
    "ON commerce_discovery_placements (subject_ref, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_cd_placement_surface "
    "ON commerce_discovery_placements (surface, created_at)",

    # ---- impressions -------------------------------------------------------
    # `visible` distinguishes "rendered into the list" from "actually met the
    # viewability threshold". Both are recorded, on separate rows keyed by
    # different dedup prefixes, because a card that scrolled past off-screen is
    # a real fact about cadence (it consumed a slot) while only the visible one
    # is a real fact about reach.
    """
    CREATE TABLE IF NOT EXISTS commerce_discovery_impression_events (
        event_id TEXT PRIMARY KEY,
        placement_id TEXT NOT NULL,
        subject_ref TEXT NOT NULL,
        surface TEXT NOT NULL,
        slot INTEGER NOT NULL DEFAULT 0,
        listing_id INTEGER NOT NULL,
        seller_user_id INTEGER,
        promotion_class TEXT NOT NULL,
        reason_code TEXT,
        ranking_version TEXT NOT NULL,
        session_id TEXT,
        visible INTEGER NOT NULL DEFAULT 0,
        view_duration_ms INTEGER NOT NULL DEFAULT 0,
        self_view INTEGER NOT NULL DEFAULT 0,
        request_meta_json TEXT,
        event_at TEXT NOT NULL,
        dedup_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cd_impr_placement "
    "ON commerce_discovery_impression_events (placement_id)",
    # Backs the per-product frequency cap. Leading with subject_ref because
    # every cap question is asked about one viewer.
    "CREATE INDEX IF NOT EXISTS idx_cd_impr_product_freq "
    "ON commerce_discovery_impression_events (subject_ref, listing_id, event_at)",
    "CREATE INDEX IF NOT EXISTS idx_cd_impr_seller_freq "
    "ON commerce_discovery_impression_events (subject_ref, seller_user_id, event_at)",
    "CREATE INDEX IF NOT EXISTS idx_cd_impr_listing "
    "ON commerce_discovery_impression_events (listing_id, event_at)",

    # ---- clicks and downstream commerce outcomes ---------------------------
    # One table for the whole post-click funnel rather than five, with `action`
    # naming the step. The funnel is strictly ordered and sparsely populated —
    # separate tables would be four extra joins to answer "what happened after
    # this impression", which is the only question anyone asks of it.
    """
    CREATE TABLE IF NOT EXISTS commerce_discovery_engagement_events (
        event_id TEXT PRIMARY KEY,
        placement_id TEXT NOT NULL,
        impression_event_id TEXT,
        subject_ref TEXT NOT NULL,
        surface TEXT NOT NULL,
        listing_id INTEGER NOT NULL,
        seller_user_id INTEGER,
        promotion_class TEXT NOT NULL,
        reason_code TEXT,
        ranking_version TEXT NOT NULL,
        session_id TEXT,
        action TEXT NOT NULL,
        value_minor INTEGER NOT NULL DEFAULT 0,
        currency TEXT,
        order_ref TEXT,
        request_meta_json TEXT,
        event_at TEXT NOT NULL,
        dedup_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cd_engage_placement "
    "ON commerce_discovery_engagement_events (placement_id)",
    "CREATE INDEX IF NOT EXISTS idx_cd_engage_action "
    "ON commerce_discovery_engagement_events (action, event_at)",
    "CREATE INDEX IF NOT EXISTS idx_cd_engage_listing "
    "ON commerce_discovery_engagement_events (listing_id, action, event_at)",

    # ---- negative feedback -------------------------------------------------
    # Kept apart from engagement because it is read by a different consumer for
    # a different purpose: engagement feeds ranking, feedback feeds *ranking
    # suppression* and the trust-and-safety review queue. Pooling them would
    # mean every ranking read had to remember to exclude the negatives.
    """
    CREATE TABLE IF NOT EXISTS commerce_discovery_feedback_events (
        event_id TEXT PRIMARY KEY,
        placement_id TEXT,
        subject_ref TEXT NOT NULL,
        surface TEXT NOT NULL,
        listing_id INTEGER,
        seller_user_id INTEGER,
        promotion_class TEXT NOT NULL,
        reason_code TEXT,
        action TEXT NOT NULL,
        session_id TEXT,
        event_at TEXT NOT NULL,
        dedup_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cd_feedback_subject "
    "ON commerce_discovery_feedback_events (subject_ref, event_at)",
    "CREATE INDEX IF NOT EXISTS idx_cd_feedback_listing "
    "ON commerce_discovery_feedback_events (listing_id, action)",

    # ---- suppressions ------------------------------------------------------
    # The only mutable table here. A suppression is current state ("do not show
    # me this seller"), not history, and it is read on the hot path of every
    # placement request — so it is one small indexed row per rule rather than a
    # replay of the feedback log.
    #
    # `expires_at` NULL means permanent. A "hide this seller" with an expiry
    # would quietly resurface a merchant the user rejected, which is the single
    # most trust-damaging thing this engine could do.
    """
    CREATE TABLE IF NOT EXISTS commerce_discovery_suppressions (
        suppression_id TEXT PRIMARY KEY,
        subject_ref TEXT NOT NULL,
        scope TEXT NOT NULL,
        ref TEXT NOT NULL,
        source_action TEXT,
        expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(subject_ref, scope, ref)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cd_suppress_lookup "
    "ON commerce_discovery_suppressions (subject_ref, scope)",
)


@run_once_per_process
def ensure_schema(conn) -> bool:
    """Create the discovery tables. Idempotent, once per worker process.

    Takes the connection rather than a cursor, and the reason is the commit
    below — which is also why it cannot simply reach for ``cur.connection``.
    PEP 249 makes that attribute optional, ``sqlite3`` and ``psycopg2`` both
    provide it, and ``services.db.CompatCursor`` — which is what wraps every
    cursor once ``DATABASE_URL`` points at PostgreSQL — does not. Reading it
    would raise ``AttributeError``, be swallowed as a schema failure, and take
    discovery down on exactly the engine this function exists to be careful
    about, while every SQLite test in this package went on passing.

    Returns ``False`` on failure rather than raising, and the guard does not
    cache a ``False`` — a transient DDL failure must not leave this worker
    permanently convinced the tables exist, and it must also not take down the
    feed. A discovery request that finds no tables degrades to serving no
    placements, which every client renders as an ordinary quiet feed.

    The commit is load-bearing twice over, and neither reason is visible locally
    because SQLite autocommits DDL.

    Caching a DDL call is only sound if the DDL is durable. PostgreSQL DDL is
    transactional, so a ``CREATE TABLE IF NOT EXISTS`` on a request that later
    raises is rolled back with everything else — while the guard has already
    recorded success, so the retry never comes. That worker spends the rest of
    its life certain the tables exist, and every discovery query in it dies on
    ``UndefinedTable``. The engine reports that to the client as "no placements",
    so the failure presents as a permanently quiet shop rather than as an error.

    Committing here also releases the ``CREATE``'s ShareLock now instead of
    holding it until the request commits. Every caller runs this as its first
    statement on a freshly opened connection, so there is nothing else in the
    transaction to commit — but the request that follows takes a RowExclusiveLock
    on these same tables to write its impression row, and two workers
    interleaving (write, DDL) against (DDL, write) is a lock cycle. That is the
    incident this guard was factored out of, and holding the lock across a full
    ranking pass on the feed hot path is the longest possible way to hold it.
    """
    try:
        cur = conn.cursor()
        for statement in _DDL:
            cur.execute(statement)
        conn.commit()
        return True
    except Exception:
        LOGGER.exception("COMMERCE_DISCOVERY_SCHEMA_FAILED")
        return False
