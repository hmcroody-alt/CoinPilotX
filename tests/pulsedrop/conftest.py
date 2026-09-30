"""Shared fixtures for the PulseDrop suites.

Why the database is pinned here rather than in each module
----------------------------------------------------------
``services.db.connect()`` falls back to the module constant
``LOCAL_SQLITE_FILE`` whenever ``DATABASE_URL`` is unset, and that constant is a
*relative* string. The root ``tests/conftest.py`` already redirects it away from
the developer's own ``coinpilotx.db`` — but it does so in a session-scoped
autouse fixture, which runs **after** every conftest in the tree has been
imported. A module-scope assignment here would therefore be overwritten a moment
later by the root fixture, and the PulseDrop tables would land in the shared
per-process fallback alongside every other suite's.

So this fixture declares the root one as a dependency. Pytest then orders it
second, our path wins, and PulseDrop gets a file of its own. The path is
absolute because the working directory is not stable across this repo's tooling.

Why ``DATABASE_URL`` is pinned as well, and not only the constant
-----------------------------------------------------------------
Patching ``LOCAL_SQLITE_FILE`` is **not sufficient on its own**, and finding out
why cost this suite a debugging session and wrote rows into the developer's real
database. ``connect()`` consults ``DATABASE_URL`` *first* and only falls back to
the constant; the root fixture patches the constant alone, on the reasonable
assumption that a variable which is unset at session start stays unset.

It does not. ``hydration._price_minor`` imports ``bot`` lazily, on the first
overlay that has to parse a price, and ``bot`` runs ``_load_local_environment()``
at module scope -- which reads ``.env.local``, finds ``DATABASE_URL`` absent from
the environment, and sets it to ``sqlite:///coinpilotx.db``. That string is
*relative*, so from that moment every ``connect()`` in the process opens the
developer's own database in the repo root, and the root fixture's protection is
silently gone. Nothing fails loudly: the tables all exist over there, because
importing ``bot`` also ran ``init_db()`` against it.

Pinning the variable to the same temp file closes the path completely -- the
fallback is never consulted, so it no longer matters which of the two wins. It
is set inside the fixture rather than at module scope for the same ordering
reason as above, and restored exactly, including back to *absent*, on teardown.

This is a suite-local fix for a repo-wide hole: any test anywhere in this tree
that imports ``bot`` and then calls ``connect()`` has the same problem.

Why the schema is rebuilt per session
-------------------------------------
``services.pulsedrop.schema`` memoizes "the tables exist" in a process global so
that a worker loop does not re-run DDL on every tick. A test process that
inherits a set flag from an earlier import would skip creation against a brand
new database and fail on the first read, so the flag is reset before the tables
are created rather than trusted.

Why the marketplace tables are declared here too
------------------------------------------------
``schema.ensure_schema()`` creates the PulseDrop tables and nothing else, by
design — PulseDrop does not own ``marketplace_listings``. But the hydration read
is one statement that LEFT JOINs ``marketplace_listings``, ``users`` and
``marketplace_sellers``, so a suite that exercises it against a real database
needs those three to exist.

The canonical definitions live in ``bot.init_db()``, and importing ``bot`` would
in fact create them — it runs ``init_db()`` at module scope. That is how the
throwaway harnesses this suite was ported from got them. It is the wrong way to
get them here: importing ``bot`` is slow, it connects, and it drags ~1,500 route
registrations into a process that wants three tables.

So they are declared additively instead, following ``tests/business_os``: create
the table with its primary key if absent, then add any missing column. A suite
that later needs a column nobody listed gets a clear ``no such column`` rather
than a shape fight with whichever module ran first. The column *names* are the
production ones on purpose — a fixture keyed on ``id`` where production uses
``user_id`` would be testing a schema that does not exist.

Why one suite still imports the monolith, and why it goes through a fixture
---------------------------------------------------------------------------
``tests/pulsedrop/test_read_path.py`` is an integration suite over the feed
engine, and the feed engine selects columns nobody would ever think to list
above — ``u.email``, ``u.subscription_status``, ``ap.public_player_id``. It
needs the real schema, so it needs ``init_db()``, so it needs ``bot``.

That import cannot happen at module scope: it would run during *collection*,
before the fixture below has pinned ``DATABASE_URL``, and land ~550 tables in
the developer's own database — the exact accident this file exists to prevent.
Hence :func:`monolith`, which is ordered after the pin.

It also has to drop the three stub tables first. ``init_db()`` creates
everything with ``CREATE TABLE IF NOT EXISTS``, so a three-column ``users``
sitting there is not upgraded — it is silently accepted, and the first feed read
fails with ``no such column: u.email``. Dropping them hands the naming decision
back to production, which is where it belongs; the additive declarations are
then re-applied over the real tables, where they are all no-ops, so that a suite
running after this one still finds every column it was promised.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# (table, primary key, ((column, SQL type), ...)).
#
# The listing columns are exactly ``hydration._LISTING_COLUMNS`` plus the two
# the seller join reads. Nothing here is a guess: every one of them is named in
# the projection of ``hydration._select()``, and a column missing from this list
# does not fail loudly — the LEFT JOIN would simply never match, and a rule
# reading it would answer ``None`` and fall through to ``passes_when_unknown``.
_JOINED_TABLES = (
    (
        "users",
        "user_id",
        (
            ("username", "TEXT"),
            ("display_name", "TEXT"),
        ),
    ),
    (
        "marketplace_sellers",
        "user_id",
        (
            # ``store_name_sql`` coalesces these two, in this order.
            ("status", "TEXT"),
            ("display_name", "TEXT"),
            ("business_name", "TEXT"),
        ),
    ),
    (
        "marketplace_listings",
        "id",
        (
            ("seller_user_id", "INTEGER"),
            ("title", "TEXT"),
            # Not a number: the marketplace stores a formatted label and a
            # currency, and there is no ``price_cents`` column anywhere.
            ("price_label", "TEXT"),
            ("currency", "TEXT"),
            ("quantity", "INTEGER"),
            ("product_type", "TEXT"),
            ("listing_type", "TEXT"),
            ("status", "TEXT"),
            ("approval_status", "TEXT"),
            ("cover_image_url", "TEXT"),
            ("category", "TEXT"),
        ),
    ),
    (
        # Read in a second batched statement, not in the join: a listing has many
        # variants, so joining them would multiply the page. This is where the
        # real price lives — ``price_label`` is empty on most production rows —
        # and a suite without the table exercises only the degradation path.
        "marketplace_listing_variants",
        "id",
        (
            ("listing_id", "INTEGER"),
            ("price_cents", "INTEGER"),
            ("currency", "TEXT"),
            ("status", "TEXT"),
        ),
    ),
)


def _ensure_joined_tables(conn) -> None:
    """Create the three tables the hydration join reads, additively."""
    for table, key, columns in _JOINED_TABLES:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({key} INTEGER PRIMARY KEY)")
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, sql_type in columns:
            if column in present:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


@pytest.fixture(autouse=True, scope="session")
def _pulsedrop_database(_never_write_to_the_developers_database):
    """Give the PulseDrop suites their own SQLite file and a real schema."""
    from services import db as platform_db
    from services.pulsedrop import schema

    directory = tempfile.TemporaryDirectory(prefix="pulsedrop-tests-")
    path = os.path.join(directory.name, "pulsedrop.db")

    previous = platform_db.LOCAL_SQLITE_FILE
    previous_url = os.environ.get("DATABASE_URL")
    platform_db.LOCAL_SQLITE_FILE = path
    # Both doors, not one. See the module docstring: an import of ``bot`` part
    # way through the session sets this from ``.env.local`` if it is absent, and
    # it outranks the constant above.
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    schema.reset_ready_flag()
    schema.ensure_schema()
    conn = platform_db.connect()
    try:
        _ensure_joined_tables(conn)
        conn.commit()
    finally:
        conn.close()
    try:
        yield path
    finally:
        platform_db.LOCAL_SQLITE_FILE = previous
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        schema.reset_ready_flag()
        directory.cleanup()


@pytest.fixture(scope="session")
def monolith(_pulsedrop_database):
    """``bot``, imported safely, with the real schema underneath it.

    See the module docstring. Ordered after the database pin by depending on it,
    and it replaces the stub tables with production's own before importing so
    that ``init_db()`` is the one that defines them.
    """
    from services import db as platform_db
    from services.pulsedrop import schema

    conn = platform_db.connect()
    try:
        for table, _, _ in _JOINED_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()

    import bot

    # Called explicitly, not left to the import. Another suite in this process
    # may already have imported ``bot`` — ``hydration._price_minor`` does it
    # lazily, on the first overlay that has to parse a price — in which case the
    # import above is a no-op and the tables just dropped would never come back.
    # That failure only appears when the whole package runs, and not when this
    # file runs alone, which is the worst shape a test bug can have.
    #
    # And the flag has to be cleared first: ``init_db`` returns immediately once
    # ``INIT_DB_COMPLETED`` is set, which is the right behaviour for a process
    # that boots once and the wrong one for a fixture that has just dropped
    # three of the tables it creates.
    bot.INIT_DB_COMPLETED = False
    bot.init_db()

    conn = platform_db.connect()
    try:
        # No-ops against the real tables; insurance for any suite that runs
        # after this one and still expects the additive guarantees above.
        _ensure_joined_tables(conn)
        conn.commit()
    finally:
        conn.close()

    # ``init_db()`` connected on its own, so the memoised "PulseDrop tables
    # exist" flag may have been set against a different database than the one
    # the tables are actually in.
    schema.reset_ready_flag()
    schema.ensure_schema()
    return bot


@pytest.fixture()
def cursor():
    """A cursor on the suite database, committed and closed for the caller.

    Yielding the cursor rather than the connection keeps the tests reading like
    the production call sites, which are handed a cursor by the feed engine and
    never own the connection they are writing through.
    """
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()

