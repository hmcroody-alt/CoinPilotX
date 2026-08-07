"""Behavioral tests for saved collections and saved-media resolution.

## The defect these pin

`services.db` presents Postgres through a compatibility layer. `CompatCursor.execute`
appends `RETURNING <pk>` to an INSERT — but only when the target table appears in
`AUTO_PK_TABLES`, and only when the statement is not an `INSERT OR IGNORE` (that one
gets `ON CONFLICT DO NOTHING` instead, which returns no row). `cur.lastrowid` is
populated from that RETURNING row and from nothing else, so outside those two
conditions it is permanently `None`.

`pulse_saved_collections` was not in `AUTO_PK_TABLES`. Both writers of a saved
collection ended on `int(cur.lastrowid)`. In production that was `int(None)` ->
TypeError -> HTTP 500, on *every* account. The same helper is called by the Save
write path and by `GET /api/pulse/saved`, which is why one root cause produced two
different user-visible failures. The TypeError also preceded `conn.commit()`, so the
collection was never created and the next attempt failed identically, forever.

None of this reproduces on SQLite: `services.db.connect()` hands back a raw `sqlite3`
connection there, and `sqlite3` populates `lastrowid` on its own. A test that runs
against plain SQLite therefore passes with the bug fully present and is worthless as
a regression test. So every test in `PostgresLikeCursorTest` and its subclasses runs
through `PostgresLikeCursor`, which reproduces the one property that matters:
`lastrowid` is decided by `services.db`'s own rule, read from `services.db` at import
time, rather than by SQLite.

`ReintroducingTheBugTest` is the harness's own teeth check: it runs the shipped
pre-fix implementation through the same fixtures and asserts it dies. If someone
reverts the fix and these tests keep passing, that test is where the lie shows up.

## Why bot.py's functions are lifted out with `ast`

Same reason as `test_pulse_post_save_wiring.py`, whose loaders this module reuses:
`bot.py` cannot be imported here (Flask and werkzeug are not installed, let alone
the rest of the app). `pulse_feed_engine.pulse_visibility_decision` is lifted the
same way rather than faked, because "is this saved post still visible?" is exactly
the question the fake would beg. That proves the logic, not the wiring into Flask,
and it is stated plainly rather than hidden.
"""

import ast
import contextlib
import io
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as db_service  # noqa: E402
from services import saved_content_service  # noqa: E402
from tests.test_pulse_post_save_wiring import (  # noqa: E402
    FakeDbService,
    load_bot_constants,
    load_bot_functions,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED_ENGINE_PY = os.path.join(REPO_ROOT, "services", "pulse_feed_engine.py")


def load_module_functions(path, names, namespace):
    """`load_bot_functions`, pointed at some module other than bot.py."""
    wanted = set(names)
    found = set()
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            node = ast.parse(ast.unparse(node)).body[0]
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
            found.add(node.name)
    missing = wanted - found
    if missing:
        raise AssertionError(f"{path} no longer defines: {sorted(missing)}")
    return namespace


# ---------------------------------------------------------------------------
# The production cursor's `lastrowid` rule, reproduced over SQLite
# ---------------------------------------------------------------------------

_INSERT_TABLE_RE = re.compile(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
_INSERT_OR_IGNORE_RE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I)


def compat_populates_lastrowid(sql):
    """Would `services.db.CompatCursor` leave a usable `lastrowid` for this SQL?

    Mirrors the two conditions in `CompatCursor.execute`, and reads the table list
    from `services.db` rather than restating it, so narrowing `AUTO_PK_TABLES`
    changes what this harness simulates instead of silently diverging from it.
    """
    match = _INSERT_TABLE_RE.search(str(sql))
    if not match:
        return False
    if match.group(1) not in db_service.AUTO_PK_TABLES:
        return False
    if _INSERT_OR_IGNORE_RE.search(str(sql)):
        # Becomes `ON CONFLICT DO NOTHING`, never `RETURNING`. So even a table on
        # the list yields no row to read the pk out of.
        return False
    return True


class PostgresLikeCursor:
    """A SQLite cursor whose `lastrowid` behaves the way Postgres' does here.

    Everything else is real SQLite — real UNIQUE constraints, real `INSERT OR
    IGNORE` semantics, real reads. Only `lastrowid` is overridden, because that is
    the single production behaviour SQLite cannot reproduce and the single one the
    outage turned on.
    """

    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append(str(sql))
        self._cursor.execute(sql, tuple(params or ()))
        self.lastrowid = self._cursor.lastrowid if compat_populates_lastrowid(sql) else None
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        return self._cursor.close()


class RacingCursor(PostgresLikeCursor):
    """A `PostgresLikeCursor` with a competitor committing underneath it.

    `hook` fires once, immediately after the helper's first lookup has run and
    found nothing — the exact instant at which a second request can create the row
    this one is about to insert. That is the window `UNIQUE(user_id, slug)` plus
    `INSERT OR IGNORE` plus the re-SELECT exist to close.
    """

    def __init__(self, cursor, hook):
        super().__init__(cursor)
        self._hook = hook
        self._fired = False

    def execute(self, sql, params=()):
        result = super().execute(sql, params)
        if not self._fired and str(sql).lstrip().upper().startswith("SELECT") and "pulse_saved_collections" in str(sql):
            self._fired = True
            self._hook()
        return result


SCHEMA = (
    """CREATE TABLE pulse_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_type TEXT,
        title TEXT,
        body TEXT,
        visibility TEXT DEFAULT 'public',
        moderation_status TEXT DEFAULT 'approved',
        status TEXT DEFAULT 'published',
        media_ids_json TEXT,
        repost_of_post_id INTEGER,
        created_at TEXT,
        deleted_at TEXT
    )""",
    """CREATE TABLE pulse_post_saves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER,
        user_id INTEGER,
        collection_name TEXT DEFAULT 'Saved',
        created_at TEXT,
        UNIQUE(post_id, user_id)
    )""",
    # UNIQUE(user_id, slug) is the production constraint and is what makes the
    # concurrent-creation branch of the fix reachable at all.
    """CREATE TABLE pulse_saved_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        slug TEXT,
        description TEXT,
        is_default INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(user_id, slug)
    )""",
    """CREATE TABLE pulse_saved_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        collection_id INTEGER,
        content_type TEXT,
        content_id TEXT,
        title TEXT,
        preview_text TEXT,
        thumbnail_url TEXT,
        media_url TEXT,
        source_url TEXT,
        metadata_json TEXT,
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(user_id, content_type, content_id)
    )""",
    "CREATE TABLE pulse_reels (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER UNIQUE, user_id INTEGER, caption TEXT)",
    """CREATE TABLE pulse_videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        description TEXT,
        mux_playback_id TEXT,
        mux_asset_id TEXT,
        mux_status TEXT,
        playback_url TEXT,
        media_url TEXT,
        thumbnail_url TEXT,
        status TEXT DEFAULT 'active'
    )""",
    # The media table `pulse_feed_engine` resolves playback from. Saving must never
    # add a row to it.
    """CREATE TABLE chat_media_uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        context_type TEXT,
        context_id TEXT,
        media_type TEXT,
        storage_key TEXT,
        moderation_status TEXT DEFAULT 'approved'
    )""",
)

VIEWER = 22
AUTHOR = 11
TEXT_POST = 1
VIDEO_POST = 2
PRIVATE_POST = 3
DELETED_POST = 4
REEL_POST = 5
REEL_ID = 7
VIDEO_ID = 31
MEDIA_ID = 501


def stub_namespace(**extra):
    """The collaborators bot.py's saved helpers reach for, smallest honest form."""
    namespace = {
        "re": re,
        "json": json,
        "logging": logging,
        "datetime": datetime,
        "clean_html": lambda value: str(value or ""),
        "safe_int": lambda value, default=0: _safe_int(value, default),
    }
    namespace.update(extra)
    return namespace


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class SavedCollectionsCase(unittest.TestCase):
    """One temp database plus the saved helpers lifted out of bot.py."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db", prefix="pulse_saved_collections_")
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        for statement in SCHEMA:
            cur.execute(statement)
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,title,body,media_ids_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (TEXT_POST, AUTHOR, "text", "A text post", "body", "[]", "2026-08-01T00:00:00"),
        )
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,title,body,media_ids_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (VIDEO_POST, AUTHOR, "video", "A video post", "body", json.dumps([MEDIA_ID]), "2026-08-02T00:00:00"),
        )
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,title,body,visibility,created_at) VALUES (?,?,?,?,?,?,?)",
            (PRIVATE_POST, AUTHOR, "text", "A private post", "body", "private", "2026-08-03T00:00:00"),
        )
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,title,body,created_at,deleted_at) VALUES (?,?,?,?,?,?,?)",
            (DELETED_POST, AUTHOR, "text", "A deleted post", "body", "2026-08-04T00:00:00", "2026-08-05T00:00:00"),
        )
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,title,body,created_at) VALUES (?,?,?,?,?,?)",
            (REEL_POST, AUTHOR, "video", "A reel", "reel body", "2026-08-04T00:00:00"),
        )
        cur.execute("INSERT INTO pulse_reels (id,post_id,user_id,caption) VALUES (?,?,?,?)", (REEL_ID, REEL_POST, AUTHOR, "cap"))
        cur.execute(
            "INSERT INTO pulse_videos (id,user_id,title,mux_playback_id,mux_asset_id,mux_status,playback_url,thumbnail_url)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (VIDEO_ID, AUTHOR, "A video", "pbid123", "asset123", "ready", "https://stale.example/old.m3u8", "https://img/1.jpg"),
        )
        cur.execute(
            "INSERT INTO chat_media_uploads (id,context_type,context_id,media_type,storage_key) VALUES (?,?,?,?,?)",
            (MEDIA_ID, "pulse_post", str(VIDEO_POST), "video", "r2/key/1.mp4"),
        )
        conn.commit()
        conn.close()

        self.ns = load_bot_constants(
            ["PULSE_WATCH_LATER_NAME", "PULSE_WATCH_LATER_TYPES"],
            load_bot_functions(
                [
                    "pulse_saved_slug",
                    "ensure_pulse_saved_collection",
                    "pulse_heal_saved_constraints",
                    "pulse_saved_collection_for",
                    "pulse_ensure_saved_system_collections",
                    "pulse_saved_post_target",
                ],
                stub_namespace(),
            ),
        )

    def tearDown(self):
        os.unlink(self.db_path)

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def cursor(self, conn=None, cls=PostgresLikeCursor, **kwargs):
        conn = conn or self.connect()
        # Closed on the way out even when the call under test raises. Without
        # this, a helper that dies mid-write leaves the transaction open and the
        # *next* connection blocks on SQLite's write lock until it times out — so
        # a broken implementation would make this suite stall for minutes instead
        # of failing in seconds. That is precisely the production shape (the
        # TypeError preceded `conn.commit()`), and the suite has to report it as a
        # failure rather than reproduce it as a hang.
        self.addCleanup(conn.close)
        return conn, cls(conn.cursor(), **kwargs)

    def with_cursor(self, call, cls=PostgresLikeCursor, **kwargs):
        """Run `call(cur)` on a fresh Postgres-like cursor, committing on success."""
        conn = self.connect()
        cur = cls(conn.cursor(), **kwargs)
        try:
            result = call(cur)
            conn.commit()
            return result
        finally:
            conn.close()

    def collections(self, user_id=VIEWER):
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM pulse_saved_collections WHERE user_id=? ORDER BY id", (user_id,)
        ).fetchall()
        conn.close()
        return rows

    def slugs(self, user_id=VIEWER):
        return [row["slug"] for row in self.collections(user_id)]

    def table_count(self, table):
        conn = self.connect()
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        conn.close()
        return count


# ---------------------------------------------------------------------------
# 1. The regression itself: a cursor that never populates `lastrowid`
# ---------------------------------------------------------------------------


class LastRowIdIsNeverAvailableTest(SavedCollectionsCase):
    """Both writers must produce a real collection id without `lastrowid`."""

    def test_the_harness_really_does_withhold_lastrowid(self):
        """Precondition. Without this, every test below is vacuous.

        Asserted against a live insert into `pulse_saved_collections` rather than
        against the rule in the abstract, so it is the same statement the helper
        issues.
        """
        conn, cur = self.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO pulse_saved_collections (user_id,name,slug,is_default,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (VIEWER, "Favorites", "favorites", 1, "now", "now"),
        )
        self.assertIsNone(cur.lastrowid, "production reads None here; the fixture must too")
        conn.close()

    def test_adding_the_table_to_auto_pk_tables_would_not_have_been_enough(self):
        """The list fix and the SELECT fix are not alternatives.

        `pulse_saved_collections` is on the list now, yet both writers insert with
        `INSERT OR IGNORE`, which `CompatCursor` turns into `ON CONFLICT DO
        NOTHING` and never decorates with `RETURNING`. So `lastrowid` stays None
        even after the list was corrected. Anyone tempted to revert the SELECT
        because "the table is registered now" is wrong, and this says why.
        """
        self.assertIn("pulse_saved_collections", db_service.AUTO_PK_TABLES)
        self.assertFalse(
            compat_populates_lastrowid(
                "INSERT OR IGNORE INTO pulse_saved_collections (user_id,slug) VALUES (?,?)"
            )
        )
        self.assertTrue(
            compat_populates_lastrowid("INSERT INTO pulse_saved_collections (user_id,slug) VALUES (?,?)"),
            "a plain INSERT does get RETURNING, which is why the list matters too",
        )

    def test_the_bot_helper_returns_a_real_id(self):
        conn, cur = self.cursor()
        collection_id = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        conn.commit()
        self.assertIsInstance(collection_id, int)
        self.assertGreater(collection_id, 0, "the Save button's 500 was int(None) here")
        conn.close()
        self.assertEqual(self.slugs(), ["favorites"])

    def test_the_service_helper_returns_a_real_id(self):
        conn, cur = self.cursor()
        collection_id = saved_content_service._ensure_default_collection(cur, VIEWER, "2026-08-06T00:00:00")
        conn.commit()
        self.assertIsInstance(collection_id, int)
        self.assertGreater(collection_id, 0)
        conn.close()
        self.assertEqual(self.slugs(), ["favorites"])

    def test_the_returned_id_points_at_a_row_that_was_really_committed(self):
        """The outage's second half: the TypeError preceded `conn.commit()`.

        So the account had no collection either, and every retry took the same
        path and failed the same way. An id that is merely non-zero is not enough;
        the row has to be there afterwards.
        """
        conn, cur = self.cursor()
        collection_id = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        conn.commit()
        conn.close()
        rows = self.collections()
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["id"]), collection_id)
        self.assertEqual(rows[0]["slug"], "favorites")
        self.assertEqual(int(rows[0]["is_default"] or 0), 1)

    def test_the_second_call_returns_the_same_id_from_a_fresh_connection(self):
        """The read path calls this too. It must find, not re-create."""
        conn, cur = self.cursor()
        first = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        conn.commit()
        conn.close()
        conn, cur = self.cursor()
        second = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        conn.commit()
        conn.close()
        self.assertEqual(first, second)
        self.assertEqual(len(self.collections()), 1)

    def test_both_writers_resolve_to_the_one_collection(self):
        """`bot` and `saved_content_service` must not each create their own.

        They key on the same slug for exactly this reason; if one drifted to a
        different name the two would disagree about where a save went.
        """
        conn, cur = self.cursor()
        from_bot = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        from_service = saved_content_service._ensure_default_collection(cur, VIEWER, "2026-08-06T00:00:00")
        conn.commit()
        conn.close()
        self.assertEqual(from_bot, from_service)
        self.assertEqual(len(self.collections()), 1)

    def test_the_service_helper_adopts_the_collection_bot_created(self):
        conn, cur = self.cursor()
        from_bot = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        conn.commit()
        conn.close()
        conn, cur = self.cursor()
        from_service = saved_content_service._ensure_default_collection(cur, VIEWER, "2026-08-06T00:00:00")
        conn.commit()
        conn.close()
        self.assertEqual(from_bot, from_service)
        self.assertEqual(len(self.collections()), 1)

    def test_two_accounts_get_two_collections(self):
        conn, cur = self.cursor()
        mine = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        theirs = self.ns["ensure_pulse_saved_collection"](cur, AUTHOR)
        conn.commit()
        conn.close()
        self.assertNotEqual(mine, theirs)
        self.assertEqual(self.slugs(VIEWER), ["favorites"])
        self.assertEqual(self.slugs(AUTHOR), ["favorites"])

    def test_a_collection_that_cannot_be_created_raises_rather_than_returning_zero(self):
        """Returning 0 would write saved items into a collection that is not there.

        Simulated by dropping the table, which is the only way this branch is
        reachable: it means the INSERT did not happen on either dialect.
        """
        conn = self.connect()
        conn.execute("DROP TABLE pulse_saved_collections")
        conn.commit()
        cur = PostgresLikeCursor(conn.cursor())
        with self.assertRaises(Exception) as caught:
            self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        self.assertNotIsInstance(caught.exception, TypeError, "not the int(None) failure again")
        conn.close()


class ReintroducingTheBugTest(SavedCollectionsCase):
    """Does this harness actually have teeth?

    The implementation below is the one that shipped and caused the outage. It is
    reproduced here, not imported, because the point is to run the *old* code
    through the *new* fixtures. If this passes, the fixtures do not reproduce
    production and every other test in this file is decoration.
    """

    def pre_fix_ensure_pulse_saved_collection(self, cur, user_id, name="Favorites"):
        now = datetime.utcnow().isoformat(timespec="seconds")
        slug = self.ns["pulse_saved_slug"](name)
        cur.execute(
            "INSERT OR IGNORE INTO pulse_saved_collections (user_id, name, slug, description, is_default, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(user_id), str(name)[:120], slug, "", 1 if slug == "favorites" else 0, now, now),
        )
        return int(cur.lastrowid)

    def test_the_shipped_bug_dies_under_this_harness(self):
        conn, cur = self.cursor()
        with self.assertRaises(TypeError) as caught:
            self.pre_fix_ensure_pulse_saved_collection(cur, VIEWER)
        self.assertIn("NoneType", str(caught.exception))
        conn.close()

    def test_the_shipped_bug_survives_plain_sqlite_which_is_why_it_reached_users(self):
        """The same code, on a raw sqlite3 cursor, is fine. That is the whole story."""
        conn = self.connect()
        cur = conn.cursor()
        self.assertGreater(self.pre_fix_ensure_pulse_saved_collection(cur, VIEWER), 0)
        conn.close()


# ---------------------------------------------------------------------------
# 2. The table list itself
# ---------------------------------------------------------------------------


class AutoPkTablesRegistrationTest(unittest.TestCase):
    """Every saved-path table whose surrogate key is read back must be listed.

    Parsed from `services.db` rather than restated, so this notices a removal
    instead of agreeing with a stale copy.
    """

    # Tables the saved/collections code inserts into and then needs the new id of,
    # either through `lastrowid` or through a read-back that a future caller may
    # well write as `lastrowid`.
    REQUIRED = ("pulse_saved_collections", "pulse_saved_items", "pulse_post_saves", "pulse_videos")

    def test_every_saved_table_is_registered(self):
        for table in self.REQUIRED:
            with self.subTest(table=table):
                self.assertIn(
                    table,
                    db_service.AUTO_PK_TABLES,
                    f"{table} is absent from AUTO_PK_TABLES; that is the outage",
                )

    def test_each_one_is_registered_under_its_real_primary_key(self):
        """A wrong pk name is worse than absence: `RETURNING nope` raises."""
        for table in self.REQUIRED:
            with self.subTest(table=table):
                self.assertEqual(db_service.AUTO_PK_TABLES[table], "id")

    def test_the_source_dict_and_the_imported_dict_agree(self):
        """Guards against a second `AUTO_PK_TABLES` assignment shadowing the first.

        `bot.py` already has one shadowed `webhook_app = Flask(...)`; the same
        mistake here would make the imported value diverge from the one a reader
        finds in the file.
        """
        with open(os.path.join(REPO_ROOT, "services", "db.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        literals = [
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id == "AUTO_PK_TABLES"
        ]
        self.assertEqual(len(literals), 1, "AUTO_PK_TABLES is assigned more than once")
        self.assertEqual(literals[0], db_service.AUTO_PK_TABLES)


# ---------------------------------------------------------------------------
# 3 & 4. Idempotency and Watch Later routing
# ---------------------------------------------------------------------------


class CollectionRoutingTest(SavedCollectionsCase):
    def ensure(self, name=None, user_id=VIEWER):
        if name is None:
            return self.with_cursor(lambda cur: self.ns["ensure_pulse_saved_collection"](cur, user_id))
        return self.with_cursor(lambda cur: self.ns["ensure_pulse_saved_collection"](cur, user_id, name))

    def route(self, content_type, post_type="", user_id=VIEWER):
        return self.with_cursor(
            lambda cur: self.ns["pulse_saved_collection_for"](cur, user_id, content_type, post_type)
        )

    def row_for(self, collection_id):
        conn = self.connect()
        row = conn.execute("SELECT * FROM pulse_saved_collections WHERE id=?", (collection_id,)).fetchone()
        conn.close()
        return row

    # -- idempotency ---------------------------------------------------------

    def test_ten_calls_leave_one_favorites_row(self):
        ids = {self.ensure() for _ in range(10)}
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.slugs(), ["favorites"])

    def test_ten_calls_leave_one_watch_later_row(self):
        ids = {self.ensure(self.ns["PULSE_WATCH_LATER_NAME"]) for _ in range(10)}
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.slugs(), ["watch-later"])

    def test_the_two_writers_interleaved_still_leave_one_row(self):
        for _ in range(5):
            self.ensure()
            self.with_cursor(
                lambda cur: saved_content_service._ensure_default_collection(cur, VIEWER, "2026-08-06T00:00:00")
            )
        self.assertEqual(self.slugs(), ["favorites"])

    def test_the_system_collection_bootstrap_is_idempotent(self):
        """It runs on every library read, so a duplicate would appear within a day."""
        first = second = None
        for index in range(6):
            result = self.with_cursor(lambda cur: self.ns["pulse_ensure_saved_system_collections"](cur, VIEWER))
            if index == 0:
                first = result
            second = result
        self.assertEqual(first, second)
        self.assertEqual(sorted(self.slugs()), ["favorites", "watch-later"])

    def test_a_hundred_video_saves_do_not_accumulate_watch_later_rows(self):
        ids = {self.route("reel") for _ in range(50)} | {self.route("video") for _ in range(50)}
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.slugs(), ["watch-later"])

    # -- routing -------------------------------------------------------------

    def test_watchable_content_types_route_to_watch_later(self):
        """The set is read from bot.py, so narrowing it there fails here."""
        for content_type in sorted(self.ns["PULSE_WATCH_LATER_TYPES"]):
            with self.subTest(content_type=content_type):
                row = self.row_for(self.route(content_type))
                self.assertEqual(row["slug"], "watch-later")
                self.assertEqual(row["name"], self.ns["PULSE_WATCH_LATER_NAME"])

    def test_a_post_whose_type_is_video_routes_to_watch_later(self):
        """A video arrives as content_type='post'; only `post_type` distinguishes it."""
        row = self.row_for(self.route("post", "video"))
        self.assertEqual(row["slug"], "watch-later")

    def test_everything_else_routes_to_favorites(self):
        for content_type in ("post", "status", "image", "marketplace", "room", "group", "teacher", "comment", "thread", "learning"):
            with self.subTest(content_type=content_type):
                row = self.row_for(self.route(content_type))
                self.assertEqual(row["slug"], "favorites")

    def test_an_unknown_or_empty_content_type_falls_back_to_favorites(self):
        for content_type in ("", None, "wat", "  POST  "):
            with self.subTest(content_type=content_type):
                self.assertEqual(self.row_for(self.route(content_type))["slug"], "favorites")

    def test_routing_is_case_and_whitespace_insensitive(self):
        for content_type in ("REEL", " Reel ", "Video"):
            with self.subTest(content_type=content_type):
                self.assertEqual(self.row_for(self.route(content_type))["slug"], "watch-later")

    def test_watch_later_is_not_a_second_default_collection(self):
        """Two `is_default=1` rows would make the service helper's lookup ambiguous.

        `_ensure_default_collection` selects `is_default=1 ORDER BY id LIMIT 1`, so a
        second default is not a cosmetic problem: the two writers would start
        disagreeing about which collection a save landed in.
        """
        self.route("reel")
        self.route("post")
        defaults = [row["slug"] for row in self.collections() if int(row["is_default"] or 0) == 1]
        self.assertEqual(defaults, ["favorites"])

    def test_the_service_helper_never_adopts_watch_later_as_the_default(self):
        self.route("reel")
        conn, cur = self.cursor()
        default_id = saved_content_service._ensure_default_collection(cur, VIEWER, "2026-08-06T00:00:00")
        conn.commit()
        conn.close()
        self.assertEqual(self.row_for(default_id)["slug"], "favorites")
        self.assertEqual(sorted(self.slugs()), ["favorites", "watch-later"])

    def test_mixed_saves_produce_exactly_two_collections(self):
        for content_type, post_type in (("reel", ""), ("post", ""), ("video", ""), ("post", "video"), ("marketplace", "")):
            self.route(content_type, post_type)
        self.assertEqual(sorted(self.slugs()), ["favorites", "watch-later"])

    # -- the race ------------------------------------------------------------

    def racing_ensure(self, name, competitor):
        def call(cur):
            if name:
                return self.ns["ensure_pulse_saved_collection"](cur, VIEWER, name)
            return self.ns["ensure_pulse_saved_collection"](cur, VIEWER)

        return self.with_cursor(call, cls=RacingCursor, hook=competitor)

    def commit_competing(self, name, slug, is_default=0):
        def hook():
            other = sqlite3.connect(self.db_path)
            other.execute(
                "INSERT OR IGNORE INTO pulse_saved_collections (user_id,name,slug,description,is_default,created_at,updated_at)"
                " VALUES (?,?,?,'',?,?,?)",
                (VIEWER, name, slug, is_default, "race", "race"),
            )
            other.commit()
            other.close()
        return hook

    def test_a_concurrent_creation_yields_one_watch_later_and_the_winners_id(self):
        """Two requests, one collection.

        The competitor commits in the window between this call's lookup and its
        insert. `INSERT OR IGNORE` makes the collision a no-op instead of an
        IntegrityError, and the re-SELECT returns the winner's id — which is what
        the caller needs, because the saved item it is about to write must point at
        the row that actually exists.
        """
        name = self.ns["PULSE_WATCH_LATER_NAME"]
        returned = self.racing_ensure(name, self.commit_competing(name, "watch-later"))
        rows = self.collections()
        self.assertEqual(len(rows), 1, "the loser did not create a second Watch Later")
        self.assertEqual(returned, int(rows[0]["id"]), "the loser returned the winner's id")

    def test_a_concurrent_creation_of_favorites_is_equally_harmless(self):
        returned = self.racing_ensure(None, self.commit_competing("Favorites", "favorites", is_default=1))
        rows = self.collections()
        self.assertEqual(len(rows), 1)
        self.assertEqual(returned, int(rows[0]["id"]))

    def test_the_two_writers_racing_on_one_account_leave_one_favorites_row(self):
        """`bot` loses the race to `saved_content_service`, or the other way round."""
        def hook():
            other = sqlite3.connect(self.db_path)
            other.row_factory = sqlite3.Row
            cur = PostgresLikeCursor(other.cursor())
            saved_content_service._ensure_default_collection(cur, VIEWER, "2026-08-06T00:00:00")
            other.commit()
            other.close()

        returned = self.racing_ensure(None, hook)
        rows = self.collections()
        self.assertEqual(len(rows), 1)
        self.assertEqual(returned, int(rows[0]["id"]))


# ---------------------------------------------------------------------------
# 5. Saved media resolved at read time
# ---------------------------------------------------------------------------


class SavedMediaResolutionTest(SavedCollectionsCase):
    """`pulse_attach_saved_media`, with the real visibility rule.

    `pulse_visibility_decision` is lifted from `services/pulse_feed_engine.py` and
    is the production function, not a fake — "can this viewer still see it?" is the
    question the whole test turns on. `media_for_posts` is stubbed because it opens
    its own connection through `user_context`, but it records what it was asked
    for, so "did the library resolve from the canonical post?" stays answerable.
    """

    def setUp(self):
        super().setUp()
        self.media_calls = []

        def media_for_posts(post_ids):
            self.media_calls.append(list(post_ids))
            return {
                # Shaped like `media_service.resolve_media` output: a Mux
                # playback id alongside the R2 `storage_key` the bytes live
                # under. The library re-derives the HLS URL from the id, so the
                # id — not the URL — is what has to be here.
                int(post_id): [{
                    "type": "video",
                    "media_type": "video",
                    "media_id": MEDIA_ID,
                    "mux_playback_id": f"post{int(post_id)}",
                    "playback_url": f"https://stream.mux.com/post{int(post_id)}.m3u8",
                    "storage_key": "r2/key/1.mp4",
                }]
                for post_id in post_ids
            }

        feed_engine = load_module_functions(FEED_ENGINE_PY, ["pulse_visibility_decision"], {})
        self.feed_engine = SimpleNamespace(
            pulse_visibility_decision=feed_engine["pulse_visibility_decision"],
            media_for_posts=media_for_posts,
        )
        self.ns["pulse_feed_engine"] = self.feed_engine
        load_bot_functions(
            ["pulse_saved_post_target", "pulse_attach_saved_media", "pulse_saved_media_mux_only", "pulse_savable_post_id", "pulse_apply_post_save"],
            self.ns,
        )
        self.ns["pulse_notify_post_owner"] = lambda *args, **kwargs: None
        self.ns["pulse_actor_display_name"] = lambda user: "Viewer"

    def attach(self, items, viewer_user_id=VIEWER):
        return self.with_cursor(lambda cur: self.ns["pulse_attach_saved_media"](cur, items, viewer_user_id))

    def item(self, content_type, content_id, title="Saved item"):
        return {"content_type": content_type, "content_id": str(content_id), "title": title}

    def save_post(self, post_id, user_id=VIEWER):
        def call(cur):
            cur.execute("SELECT * FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1", (post_id,))
            post = cur.fetchone()
            return self.ns["pulse_apply_post_save"](cur, {"user_id": user_id}, post, True, "2026-08-06T00:00:00")

        return self.with_cursor(call)

    def saved_rows(self, user_id=VIEWER):
        conn = self.connect()
        rows = conn.execute("SELECT * FROM pulse_saved_items WHERE user_id=?", (user_id,)).fetchall()
        conn.close()
        return rows

    # -- unavailable content -------------------------------------------------

    def test_a_deleted_post_keeps_its_title_but_loses_its_media(self):
        """A vanished entry is worse than a disabled one: the user saved it."""
        [item] = self.attach([self.item("post", DELETED_POST, "A deleted post")])
        self.assertEqual(item["title"], "A deleted post")
        self.assertEqual(item["media"], [])
        self.assertTrue(item["unavailable"])

    def test_a_post_the_viewer_may_not_see_is_marked_unavailable(self):
        [item] = self.attach([self.item("post", PRIVATE_POST, "A private post")])
        self.assertEqual(item["title"], "A private post")
        self.assertEqual(item["media"], [])
        self.assertTrue(item["unavailable"])

    def test_the_author_still_sees_their_own_private_post(self):
        """`include_private=True` is passed for a reason; this pins it."""
        [item] = self.attach([self.item("post", PRIVATE_POST)], viewer_user_id=AUTHOR)
        self.assertFalse(item["unavailable"])
        self.assertTrue(item["media"])

    def test_an_unavailable_post_is_never_sent_to_the_media_resolver(self):
        self.attach([self.item("post", DELETED_POST), self.item("post", PRIVATE_POST)])
        self.assertEqual(self.media_calls, [], "no media lookup for content nobody may see")

    def test_a_post_that_no_longer_exists_at_all_is_unavailable(self):
        [item] = self.attach([self.item("post", 9999, "Gone")])
        self.assertEqual(item["title"], "Gone")
        self.assertEqual(item["media"], [])
        self.assertTrue(item["unavailable"])

    def test_one_unavailable_row_does_not_take_the_rest_of_the_library_down(self):
        items = self.attach([
            self.item("post", DELETED_POST),
            self.item("post", TEXT_POST),
            self.item("video", VIDEO_ID),
        ])
        self.assertEqual([bool(i["unavailable"]) for i in items], [True, False, False])

    def test_a_media_outage_degrades_to_no_media_rather_than_no_library(self):
        def boom(post_ids):
            raise RuntimeError("mux is down")

        self.feed_engine.media_for_posts = boom
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        [item] = self.attach([self.item("post", TEXT_POST, "A text post")])
        self.assertEqual(item["title"], "A text post")
        self.assertEqual(item["media"], [])
        self.assertFalse(item["unavailable"], "the post is fine; the resolver was not")

    # -- Mux resolution ------------------------------------------------------

    def test_a_saved_video_resolves_its_mux_playback_url_from_the_playback_id(self):
        [item] = self.attach([self.item("video", VIDEO_ID)])
        self.assertFalse(item["unavailable"])
        [media] = item["media"]
        self.assertEqual(media["mux_playback_id"], "pbid123")
        self.assertEqual(media["playback_url"], "https://stream.mux.com/pbid123.m3u8")

    def test_the_derived_url_beats_a_stale_stored_one(self):
        """`pulse_videos.playback_url` holds a stale value in the fixture on purpose.

        Every other read path re-derives from the playback id; a stored copy can
        only ever be an older version of the same string.
        """
        [item] = self.attach([self.item("video", VIDEO_ID)])
        self.assertNotIn("stale.example", item["media"][0]["playback_url"])

    def test_a_video_with_no_playback_id_yet_reports_processing_not_an_r2_object(self):
        """Mux is the only playback source the saved library points at.

        `pulse_videos.playback_url` / `media_url` hold whatever object the upload
        landed on in R2. Handing that back as a video's playback source would
        make the library the one place in the app that streams a storage object
        directly, so a video still ingesting surrenders the URL and says so
        instead. The row, the title and the poster all survive.
        """
        conn = self.connect()
        conn.execute("UPDATE pulse_videos SET mux_playback_id='' WHERE id=?", (VIDEO_ID,))
        conn.commit()
        conn.close()
        [item] = self.attach([self.item("video", VIDEO_ID)])
        media = item["media"][0]
        self.assertEqual(media["playback_url"], "")
        self.assertEqual(media["processing_status"], "mux_processing")
        self.assertFalse(item["unavailable"])

    def test_post_backed_video_never_plays_from_an_r2_object_url(self):
        """The same rule, applied to media resolved through a post.

        `media_service.resolve_media` deliberately falls back to the CDN object
        URL for a video with no Mux asset — right for the feed, which shows a
        clip the instant it uploads. The saved library filters that back out.
        """
        mux_only = self.ns["pulse_saved_media_mux_only"]
        [ready] = mux_only([{"media_type": "video", "mux_playback_id": "abc", "playback_url": "https://cdn.example/r2/raw.mp4"}])
        self.assertEqual(ready["playback_url"], "https://stream.mux.com/abc.m3u8")
        [pending] = mux_only([{"media_type": "video", "mux_playback_id": "", "playback_url": "https://cdn.example/r2/raw.mp4"}])
        self.assertEqual(pending["playback_url"], "")
        self.assertEqual(pending["processing_status"], "mux_processing")
        # Images are delivered from R2 and are not playback. Untouched.
        [image] = mux_only([{"media_type": "image", "media_url": "https://cdn.example/r2/pic.jpg"}])
        self.assertEqual(image["media_url"], "https://cdn.example/r2/pic.jpg")

    def test_a_saved_video_row_that_is_gone_is_unavailable_not_a_crash(self):
        [item] = self.attach([self.item("video", 4242, "Removed video")])
        self.assertEqual(item["title"], "Removed video")
        self.assertEqual(item["media"], [])
        self.assertTrue(item["unavailable"])

    def test_a_non_numeric_content_id_does_not_blow_up_the_library(self):
        items = self.attach([self.item("video", "abc"), self.item("post", ""), self.item("reel", None)])
        self.assertEqual([i["media"] for i in items], [[], [], []])

    # -- resolution comes from the canonical content -------------------------

    def test_a_visible_post_resolves_media_through_the_feeds_own_resolver(self):
        [item] = self.attach([self.item("post", VIDEO_POST)])
        self.assertEqual(self.media_calls, [[VIDEO_POST]])
        self.assertEqual(item["post_id"], VIDEO_POST)
        self.assertEqual(item["media"][0]["playback_url"], f"https://stream.mux.com/post{VIDEO_POST}.m3u8")

    def test_a_saved_reel_resolves_through_the_post_underneath_it(self):
        [item] = self.attach([self.item("reel", REEL_ID)])
        self.assertEqual(item["post_id"], REEL_POST)
        self.assertEqual(self.media_calls, [[REEL_POST]])

    def test_many_saved_rows_cost_one_media_round_trip(self):
        self.attach([self.item("post", TEXT_POST), self.item("post", VIDEO_POST), self.item("reel", REEL_ID)])
        self.assertEqual(len(self.media_calls), 1)
        self.assertEqual(self.media_calls[0], sorted([TEXT_POST, VIDEO_POST, REEL_POST]))

    def test_content_with_no_post_behind_it_is_left_alone(self):
        """Marketplace listings, rooms, groups and teachers have no post at all."""
        for content_type in ("marketplace", "room", "group", "teacher", "learning"):
            with self.subTest(content_type=content_type):
                [item] = self.attach([self.item(content_type, 1)])
                self.assertEqual(item["media"], [])
                self.assertFalse(item["unavailable"], "no post is not the same as unavailable")

    # -- the architecture rule: saving references, never copies ---------------

    def test_saving_does_not_add_a_row_to_the_media_table(self):
        """Saving stores a reference. It must not copy or re-upload the bytes."""
        before = self.table_count("chat_media_uploads")
        self.save_post(VIDEO_POST)
        self.save_post(TEXT_POST)
        self.save_post(REEL_POST)
        self.assertEqual(self.table_count("chat_media_uploads"), before)

    def test_saving_does_not_add_a_row_to_the_video_table(self):
        before = self.table_count("pulse_videos")
        self.save_post(VIDEO_POST)
        self.assertEqual(self.table_count("pulse_videos"), before)

    def test_the_saved_row_stores_no_media_url_of_its_own(self):
        """A cached URL is what let a saved item outlive the post it came from."""
        self.save_post(VIDEO_POST)
        [row] = self.saved_rows()
        self.assertEqual(row["media_url"] or "", "")
        self.assertEqual(row["thumbnail_url"] or "", "")

    def test_playback_still_works_despite_the_empty_column(self):
        """Which is the point: resolution happens at read time, from the post."""
        self.save_post(VIDEO_POST)
        [row] = self.saved_rows()
        [item] = self.attach([self.item(row["content_type"], row["content_id"], row["title"])])
        self.assertTrue(item["media"], "resolved from the post, not from the stored copy")
        self.assertFalse(item["unavailable"])

    def test_a_save_then_a_delete_of_the_post_stops_serving_the_media(self):
        """The saved row must not outlive the author's decision to delete."""
        self.save_post(VIDEO_POST)
        conn = self.connect()
        conn.execute("UPDATE pulse_posts SET deleted_at='2026-08-07T00:00:00' WHERE id=?", (VIDEO_POST,))
        conn.commit()
        conn.close()
        [row] = self.saved_rows()
        [item] = self.attach([self.item(row["content_type"], row["content_id"], row["title"])])
        self.assertEqual(item["media"], [])
        self.assertTrue(item["unavailable"])
        self.assertEqual(item["title"], "A video post", "but the entry itself is still listed")

    def test_the_whole_save_path_works_without_lastrowid(self):
        """End to end on the Postgres-like cursor: the 500 the users reported.

        `pulse_apply_post_save` calls `pulse_saved_collection_for`, which calls
        `ensure_pulse_saved_collection`. Before the fix this raised TypeError here
        and nothing was written.
        """
        saved, changed = self.save_post(VIDEO_POST)
        self.assertTrue(saved)
        self.assertTrue(changed)
        [row] = self.saved_rows()
        self.assertGreater(int(row["collection_id"]), 0)
        self.assertEqual(self.slugs(), ["watch-later"], "a video post lands in Watch Later")


# ---------------------------------------------------------------------------
# The read path: the Saved screen's own query, through the fixed helper
# ---------------------------------------------------------------------------


class SavedLibraryReadPathTest(SavedCollectionsCase):
    """`GET /api/pulse/saved` failed with the same TypeError as the Save button."""

    def setUp(self):
        super().setUp()
        self._real_db = saved_content_service.db_service
        saved_content_service.db_service = FakeDbService(self.db_path)

    def tearDown(self):
        saved_content_service.db_service = self._real_db
        super().tearDown()

    def test_the_library_lists_after_the_collection_is_ensured(self):
        conn, cur = self.cursor()
        collection_id = self.ns["ensure_pulse_saved_collection"](cur, VIEWER)
        cur.execute(
            "INSERT INTO pulse_saved_items (user_id,collection_id,content_type,content_id,title,created_at,updated_at)"
            " VALUES (?,?,'post',?,?,?,?)",
            (VIEWER, collection_id, str(TEXT_POST), "A text post", "now", "now"),
        )
        conn.commit()
        conn.close()
        items = saved_content_service.list_saved_items(VIEWER)
        self.assertEqual([i["content_id"] for i in items], [str(TEXT_POST)])
        self.assertEqual(items[0]["collection_name"], "Favorites")

    def test_a_watch_later_item_reports_its_collection_name(self):
        conn, cur = self.cursor()
        collection_id = self.ns["pulse_saved_collection_for"](cur, VIEWER, "reel")
        cur.execute(
            "INSERT INTO pulse_saved_items (user_id,collection_id,content_type,content_id,title,created_at,updated_at)"
            " VALUES (?,?,'reel',?,?,?,?)",
            (VIEWER, collection_id, str(REEL_ID), "A reel", "now", "now"),
        )
        conn.commit()
        conn.close()
        [item] = saved_content_service.list_saved_items(VIEWER)
        self.assertEqual(item["collection_name"], self.ns["PULSE_WATCH_LATER_NAME"])


# ---------------------------------------------------------------------------
# 6. The second production-only defect: the constraint that was never applied
# ---------------------------------------------------------------------------

# The saved tables as an older deployment actually has them: same columns, no
# uniqueness. Both constraints are declared inline inside `CREATE TABLE IF NOT
# EXISTS`, so on any database where the tables predate the constraint the CREATE
# is a no-op and the constraint never arrives. `add_columns_if_missing` back-fills
# columns and only columns, and there is no migration framework in this repo, so
# nothing else ever notices. A fresh SQLite file gets the constraints on the first
# boot, which is why this never reproduced locally — hence these tables, built
# deliberately without them.
LEGACY_SAVED_SCHEMA = (
    """CREATE TABLE pulse_saved_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        slug TEXT,
        description TEXT,
        is_default INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE pulse_saved_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        collection_id INTEGER,
        content_type TEXT,
        content_id TEXT,
        title TEXT,
        preview_text TEXT,
        thumbnail_url TEXT,
        media_url TEXT,
        source_url TEXT,
        metadata_json TEXT,
        created_at TEXT,
        updated_at TEXT
    )""",
)

# The save route's own statement, reduced to the part that matters. A *targeted*
# ON CONFLICT names the columns of a unique index; if no such index exists,
# Postgres raises rather than degrading to a plain insert, and so does SQLite.
# That is why the missing constraint shows up as a 500 on every Save rather than
# as a stray duplicate row.
ROUTE_UPSERT = (
    "INSERT INTO pulse_saved_items (user_id, collection_id, content_type, content_id, title, created_at, updated_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(user_id, content_type, content_id) DO UPDATE SET"
    " collection_id=excluded.collection_id, title=excluded.title, updated_at=excluded.updated_at"
)


class LegacySavedSchemaCase(SavedCollectionsCase):
    """`SavedCollectionsCase`, but with the two saved tables as prod has them.

    Dropping and recreating is not cosmetic: an inline `UNIQUE(...)` is backed by
    a `sqlite_autoindex_*`, and only dropping the table gets rid of it. What
    remains is a database in which nothing stops a duplicate — the state
    `pulse_heal_saved_constraints` exists to find and repair.
    """

    def setUp(self):
        super().setUp()
        conn = self.connect()
        conn.execute("DROP TABLE pulse_saved_collections")
        conn.execute("DROP TABLE pulse_saved_items")
        for statement in LEGACY_SAVED_SCHEMA:
            conn.execute(statement)
        conn.commit()
        conn.close()

    # -- helpers -------------------------------------------------------------

    def heal(self):
        """Run the healer the way `init_db` does: cursor plus connection.

        stdout is captured because the failure branch prints, and a test suite
        that prints on the failure path teaches people to ignore its output.
        """
        conn = self.connect()
        cur = PostgresLikeCursor(conn.cursor())
        try:
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                result = self.ns["pulse_heal_saved_constraints"](cur, conn)
            self.heal_stdout = captured.getvalue()
            return result
        finally:
            conn.close()

    def add_collection(self, collection_id, name, slug, user_id=VIEWER, is_default=0):
        conn = self.connect()
        conn.execute(
            "INSERT INTO pulse_saved_collections (id,user_id,name,slug,description,is_default,created_at,updated_at)"
            " VALUES (?,?,?,?,'',?,?,?)",
            (collection_id, user_id, name, slug, is_default, "legacy", "legacy"),
        )
        conn.commit()
        conn.close()
        return collection_id

    def add_item(self, item_id, collection_id, content_type="post", content_id="1", user_id=VIEWER, title="Saved"):
        conn = self.connect()
        conn.execute(
            "INSERT INTO pulse_saved_items (id,user_id,collection_id,content_type,content_id,title,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (item_id, user_id, collection_id, content_type, str(content_id), title, "legacy", "legacy"),
        )
        conn.commit()
        conn.close()
        return item_id

    def items(self, user_id=VIEWER):
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM pulse_saved_items WHERE user_id=? ORDER BY id", (user_id,)
        ).fetchall()
        conn.close()
        return rows

    def snapshot(self):
        """Everything both saved tables contain, plus the indexes on them."""
        conn = self.connect()
        state = (
            [tuple(row) for row in conn.execute("SELECT * FROM pulse_saved_collections ORDER BY id")],
            [tuple(row) for row in conn.execute("SELECT * FROM pulse_saved_items ORDER BY id")],
            sorted(
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name LIKE 'pulse_saved_%'"
                )
                if row["name"]
            ),
        )
        conn.close()
        return state


class LegacySavedSchemaHealingTest(LegacySavedSchemaCase):
    """What `pulse_heal_saved_constraints` has to be true of a live database."""

    # -- 1. the index really exists afterwards -------------------------------

    def test_a_duplicate_slug_is_accepted_before_the_heal_and_rejected_after(self):
        """The load-bearing assertion: the constraint is present, not just intended.

        The first half is not decoration — it proves the fixture reproduces the
        legacy schema. If the duplicate were rejected up here, the second half
        would pass for a reason that has nothing to do with the fix.
        """
        self.add_collection(10, "Watch Later", "watch-later")
        self.add_collection(20, "Watch Later", "watch-later")
        self.assertEqual(len(self.collections()), 2, "legacy prod accepts this; the fixture must too")

        self.assertTrue(self.heal())

        conn = self.connect()
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pulse_saved_collections (user_id,name,slug,created_at,updated_at)"
                " VALUES (?,?,?,?,?)",
                (VIEWER, "Watch Later", "watch-later", "after", "after"),
            )

    def test_the_index_is_per_user_not_global(self):
        """Two accounts must still each get their own Watch Later."""
        self.add_collection(10, "Watch Later", "watch-later", user_id=VIEWER)
        self.assertTrue(self.heal())
        conn = self.connect()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO pulse_saved_collections (user_id,name,slug,created_at,updated_at) VALUES (?,?,?,?,?)",
            (AUTHOR, "Watch Later", "watch-later", "after", "after"),
        )
        conn.commit()
        self.assertEqual(self.slugs(VIEWER), ["watch-later"])
        self.assertEqual(self.slugs(AUTHOR), ["watch-later"])

    # -- 2. duplicates are merged, and nothing saved is lost ------------------

    def test_two_watch_later_rows_become_one_and_every_item_survives(self):
        """The brief forbids a duplicate Watch Later. Deleting one is not enough.

        A user's saved items hang off the row that gets deleted. Healing the
        schema by dropping the loser would silently empty part of somebody's
        library — so the items are repointed at the survivor first, and the count
        before and after has to match exactly.
        """
        self.add_collection(10, "Watch Later", "watch-later")
        self.add_collection(20, "Watch Later", "watch-later")
        self.add_item(1, 10, content_type="reel", content_id="101")
        self.add_item(2, 20, content_type="reel", content_id="202")
        self.add_item(3, 20, content_type="video", content_id="303")
        before = self.table_count("pulse_saved_items")

        self.assertTrue(self.heal())

        rows = self.collections()
        self.assertEqual(len(rows), 1, "a second Watch Later is the duplicate the brief forbids")
        self.assertEqual(int(rows[0]["id"]), 10, "the survivor is the lower id, so existing links stay valid")
        self.assertEqual(self.table_count("pulse_saved_items"), before, "healing must not drop saved content")
        self.assertEqual([int(row["collection_id"]) for row in self.items()], [10, 10, 10])
        self.assertEqual(
            sorted(row["content_id"] for row in self.items()),
            ["101", "202", "303"],
            "every item the user saved is still there, under the survivor",
        )

    def test_the_merge_does_not_reach_across_users_or_across_slugs(self):
        self.add_collection(10, "Watch Later", "watch-later", user_id=VIEWER)
        self.add_collection(11, "Watch Later", "watch-later", user_id=VIEWER)
        self.add_collection(12, "Favorites", "favorites", user_id=VIEWER, is_default=1)
        self.add_collection(13, "Watch Later", "watch-later", user_id=AUTHOR)
        self.assertTrue(self.heal())
        self.assertEqual([int(row["id"]) for row in self.collections(VIEWER)], [10, 12])
        self.assertEqual([int(row["id"]) for row in self.collections(AUTHOR)], [13])

    def test_after_the_merge_the_helper_returns_the_survivor_rather_than_making_a_third(self):
        """The merge is only worth anything if the running code then agrees with it."""
        self.add_collection(10, "Watch Later", "watch-later")
        self.add_collection(20, "Watch Later", "watch-later")
        self.assertTrue(self.heal())
        returned = self.with_cursor(
            lambda cur: self.ns["ensure_pulse_saved_collection"](cur, VIEWER, self.ns["PULSE_WATCH_LATER_NAME"])
        )
        self.assertEqual(returned, 10)
        self.assertEqual(len(self.collections()), 1)

    # -- 3. legacy rows with no slug -----------------------------------------

    def test_a_null_or_empty_slug_is_backfilled_from_the_name(self):
        """A NULL slug collides with nothing, so it would survive the index and
        stay invisible to a lookup keyed on (user_id, slug) forever."""
        self.add_collection(10, "Watch Later", None)
        self.add_collection(20, "Favorites", "", is_default=1)
        self.assertTrue(self.heal())
        self.assertEqual(
            {int(row["id"]): row["slug"] for row in self.collections()},
            {10: "watch-later", 20: "favorites"},
        )

    def test_the_backfilled_slug_is_what_lets_the_helper_find_the_existing_row(self):
        self.add_collection(10, "Watch Later", None)
        self.assertTrue(self.heal())
        returned = self.with_cursor(
            lambda cur: self.ns["ensure_pulse_saved_collection"](cur, VIEWER, self.ns["PULSE_WATCH_LATER_NAME"])
        )
        self.assertEqual(returned, 10, "the legacy row was adopted, not shadowed")
        self.assertEqual(len(self.collections()), 1)

    def test_without_the_heal_a_null_slug_row_becomes_a_duplicate_watch_later(self):
        """States the defect rather than implying it.

        This is the same account, the same name, and two rows — which is exactly
        what the brief says must never happen.
        """
        self.add_collection(10, "Watch Later", None)
        self.with_cursor(
            lambda cur: self.ns["ensure_pulse_saved_collection"](cur, VIEWER, self.ns["PULSE_WATCH_LATER_NAME"])
        )
        names = [row["name"] for row in self.collections()]
        self.assertEqual(names, ["Watch Later", "Watch Later"], "no constraint, no slug, two collections")

    # -- 4. duplicate items, and the route's targeted ON CONFLICT -------------

    def test_duplicate_saved_items_collapse_to_the_oldest(self):
        self.add_item(1, 10, content_type="post", content_id="5", title="first save")
        self.add_item(2, 10, content_type="post", content_id="5", title="second save")
        self.add_item(3, 10, content_type="post", content_id="5", title="third save")
        self.add_item(4, 10, content_type="post", content_id="6", title="a different post")
        self.assertTrue(self.heal())
        rows = self.items()
        self.assertEqual([int(row["id"]) for row in rows], [1, 4])
        self.assertEqual(rows[0]["title"], "first save", "the oldest row is the one the user first saved")

    def test_the_routes_targeted_on_conflict_is_illegal_before_the_heal_and_legal_after(self):
        """This is the 500 itself.

        `ON CONFLICT(user_id, content_type, content_id)` names an index. Without
        one the statement is rejected outright — Postgres raises, SQLite raises,
        and neither falls back to a plain INSERT. So every Save fails, on every
        account, until the index exists.
        """
        self.add_item(1, 10, content_type="post", content_id="5", title="already saved")
        conn = self.connect()
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError) as caught:
            conn.execute(ROUTE_UPSERT, (VIEWER, 10, "post", "5", "re-saved", "now", "now"))
        self.assertIn("ON CONFLICT", str(caught.exception))

        self.assertTrue(self.heal())

        conn2 = self.connect()
        self.addCleanup(conn2.close)
        conn2.execute(ROUTE_UPSERT, (VIEWER, 10, "post", "5", "re-saved", "now", "now"))
        conn2.commit()
        [row] = self.items()
        self.assertEqual(int(row["id"]), 1, "re-saving updated the row it already had")
        self.assertEqual(row["title"], "re-saved")

    def test_after_the_heal_a_duplicate_item_is_rejected_outright(self):
        self.add_item(1, 10, content_type="post", content_id="5")
        self.assertTrue(self.heal())
        conn = self.connect()
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pulse_saved_items (user_id,collection_id,content_type,content_id,title,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (VIEWER, 10, "post", "5", "again", "after", "after"),
            )

    def test_the_same_content_id_under_a_different_type_is_not_a_duplicate(self):
        """`content_type` is part of the key for a reason: post 5 and reel 5 are
        different things and a user may save both."""
        self.add_item(1, 10, content_type="post", content_id="5")
        self.assertTrue(self.heal())
        conn = self.connect()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO pulse_saved_items (user_id,collection_id,content_type,content_id,title,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (VIEWER, 10, "reel", "5", "a reel", "after", "after"),
        )
        conn.commit()
        self.assertEqual(len(self.items()), 2)

    # -- 5. idempotency ------------------------------------------------------

    def test_running_it_twice_changes_nothing_the_second_time(self):
        """It runs on every boot, and boots are frequent.

        A healer that were destructive on the second pass would eat a row per
        deploy, which is a far worse failure than the one it fixes.
        """
        self.add_collection(10, "Watch Later", None)
        self.add_collection(20, "Watch Later", "watch-later")
        self.add_item(1, 10, content_type="reel", content_id="101")
        self.add_item(2, 20, content_type="reel", content_id="101")
        self.add_item(3, 20, content_type="video", content_id="303")

        self.assertTrue(self.heal())
        after_first = self.snapshot()
        self.assertTrue(self.heal(), "a second boot must not report failure")
        self.assertEqual(self.snapshot(), after_first)
        self.assertTrue(self.heal())
        self.assertEqual(self.snapshot(), after_first)

    def test_it_is_a_no_op_on_a_database_that_already_has_the_constraints(self):
        """The healthy case: a fresh deployment, where the inline UNIQUE applied.

        `CREATE UNIQUE INDEX IF NOT EXISTS` has to be harmless there, or the fix
        would break the environments that were never broken.
        """
        conn = self.connect()
        conn.execute("DROP TABLE pulse_saved_collections")
        conn.execute("DROP TABLE pulse_saved_items")
        for statement in SCHEMA:
            if "pulse_saved_collections" in statement or "pulse_saved_items" in statement:
                conn.execute(statement)
        conn.commit()
        conn.close()
        self.with_cursor(lambda cur: self.ns["pulse_ensure_saved_system_collections"](cur, VIEWER))
        before = self.snapshot()
        self.assertTrue(self.heal())
        self.assertEqual(
            (self.snapshot()[0], self.snapshot()[1]),
            (before[0], before[1]),
            "no row was touched on a database that was already correct",
        )

    # -- 6. it must not be able to stop the app booting ----------------------

    def test_missing_tables_return_false_instead_of_raising(self):
        """It is called from `init_db`. An exception there is not a degraded
        feature, it is an app that does not start."""
        conn = self.connect()
        conn.execute("DROP TABLE pulse_saved_collections")
        conn.execute("DROP TABLE pulse_saved_items")
        conn.commit()
        conn.close()
        self.assertIs(self.heal(), False)
        self.assertIn("could not heal", self.heal_stdout, "and it says so, rather than failing silently")

    def test_a_broken_saved_items_table_still_returns_false(self):
        """Half-migrated is the realistic shape of a legacy database."""
        conn = self.connect()
        conn.execute("DROP TABLE pulse_saved_items")
        conn.commit()
        conn.close()
        self.assertIs(self.heal(), False)

    def test_the_healer_is_wired_into_init_db(self):
        """A healer nobody calls is a comment.

        Read out of `bot.py`'s source because `init_db` cannot be executed here;
        the assertion is that the call exists and sits with the saved tables.
        """
        with open(os.path.join(REPO_ROOT, "bot.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("pulse_heal_saved_constraints(cur, conn)", source)
        call_at = source.index("pulse_heal_saved_constraints(cur, conn)")
        preceding = source[max(0, call_at - 4000):call_at]
        self.assertIn("pulse_saved_items", preceding, "it must run after the saved tables are created")


class RemovingTheHealWouldBeNoticedTest(unittest.TestCase):
    """The teeth check, in the style of `ReintroducingTheBugTest`.

    Every test above is only worth having if it fails when the fix is gone. So
    run the same tests with `pulse_heal_saved_constraints` neutered — replaced,
    in this process only, by a stub that does nothing and claims success, which
    is exactly what reverting the fix would look like from the caller's side.
    `bot.py` is not touched.
    """

    NEUTERED_MUST_FAIL = (
        "test_a_duplicate_slug_is_accepted_before_the_heal_and_rejected_after",
        "test_two_watch_later_rows_become_one_and_every_item_survives",
        "test_a_null_or_empty_slug_is_backfilled_from_the_name",
        "test_duplicate_saved_items_collapse_to_the_oldest",
        "test_the_routes_targeted_on_conflict_is_illegal_before_the_heal_and_legal_after",
        "test_after_the_heal_a_duplicate_item_is_rejected_outright",
    )

    def neutered(self, name):
        # Defined here rather than at module level so the loader never collects
        # it as a test class in its own right.
        class Neutered(LegacySavedSchemaHealingTest):
            def setUp(inner):
                LegacySavedSchemaHealingTest.setUp(inner)
                inner.ns["pulse_heal_saved_constraints"] = lambda cur, conn=None: True

        return Neutered(name)

    def test_each_of_these_fails_without_the_fix(self):
        for name in self.NEUTERED_MUST_FAIL:
            with self.subTest(test=name):
                result = unittest.TestResult()
                self.neutered(name).run(result)
                self.assertTrue(
                    result.failures or result.errors,
                    f"{name} passes with the heal removed, so it is not testing the heal",
                )

    def test_the_teeth_check_would_notice_a_test_that_does_not_belong_on_the_list(self):
        """The inverse: a test that passes with the fix present must pass here too,
        or the neutering harness is failing everything for some unrelated reason."""
        result = unittest.TestResult()
        LegacySavedSchemaHealingTest("test_two_watch_later_rows_become_one_and_every_item_survives").run(result)
        self.assertEqual((result.failures, result.errors), ([], []))


if __name__ == "__main__":
    unittest.main()
