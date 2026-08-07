"""Behavioral tests for the post Save wiring, end to end across both tables.

## The defect these pin

A post's Saved state lives in two places. `pulse_post_saves` is what the feed
joins to decide whether a card's Save button is filled; `pulse_saved_items` is
what the Saved library lists. Nothing kept them in step.

The write path decided "is this already saved?" from `pulse_post_saves` alone and
returned early when it agreed, so it never looked at the library row and never
repaired it. Three removal paths deleted only the library row and left the flag
behind. Together those make the drift permanent and self-concealing: the feed
card renders Saved, so the next tap asks for a state the server already believes
it holds, the write is a no-op, and the post can never re-enter the library. From
the outside that is "I press Save and nothing shows up in Saved."

None of that is visible in a test that asserts on one table, which is why every
test below reads *both* — through `list_saved_items`, the same query the Saved
screen calls, and through a direct read of the flag row, the same one the feed
engine does. A fix that satisfied one and not the other would ship the bug again.

The tests are behavioral rather than source greps for the same reason as
`test_pulse_repost_toggle.py`: asserting that a line of code is present proves
only that the line is present, and can pin a defect in place.

## Why two implementations are exercised

There are two of these functions, and both had the same defect. `bot.py`'s
`pulse_apply_post_save` is what the HTTP route calls;
`services.saved_content_service.set_post_saved` is the Flask-free twin UNDX calls.
`bot.py` cannot be imported here (it pulls in `stripe` and the whole app), so its
functions are lifted out of the source with `ast` and executed against stubs.
That is not as good as importing them, and it is stated plainly rather than
hidden: it proves the logic, not the wiring into Flask.
"""

import ast
import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import saved_content_service  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PY = os.path.join(REPO_ROOT, "bot.py")


@lru_cache(maxsize=1)
def _bot_tree():
    with open(BOT_PY, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def load_bot_functions(names, namespace):
    """Execute the named top-level functions from bot.py into `namespace`.

    Decorators are stripped, so a route function is loaded as the plain callable
    underneath it. Anything the function references must already be in
    `namespace` — that is deliberate: it forces each test to say out loud which
    collaborators it is faking.
    """
    wanted = set(names)
    found = set()
    for node in _bot_tree().body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            node = ast.parse(ast.unparse(node)).body[0]
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), BOT_PY, "exec"), namespace)
            found.add(node.name)
    missing = wanted - found
    if missing:
        raise AssertionError(f"bot.py no longer defines: {sorted(missing)}")
    return namespace


SCHEMA = (
    """CREATE TABLE pulse_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_type TEXT,
        title TEXT,
        body TEXT,
        repost_of_post_id INTEGER,
        created_at TEXT,
        deleted_at TEXT
    )""",
    # The UNIQUE constraints are the production ones. They matter: the fix makes
    # the save path reachable with the flag row already present, and a bare
    # INSERT would raise here exactly as it would in production.
    """CREATE TABLE pulse_post_saves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER,
        user_id INTEGER,
        collection_name TEXT DEFAULT 'Saved',
        created_at TEXT,
        UNIQUE(post_id, user_id)
    )""",
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
    "CREATE TABLE pulse_reels (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER UNIQUE, user_id INTEGER)",
)

VIEWER = 22
AUTHOR = 11
POST = 1
REPOST = 2
REEL_ID = 7
REEL_POST = 3


class SavedWiringCase(unittest.TestCase):
    """One temp database, seeded the same way for both implementations."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db", prefix="pulse_saved_")
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        for statement in SCHEMA:
            cur.execute(statement)
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,title,body,created_at) VALUES (?,?,?,?,?,?)",
            (POST, AUTHOR, "text", "Original Title", "original body", "2026-08-01T00:00:00"),
        )
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,body,repost_of_post_id,created_at) VALUES (?,?,?,?,?,?)",
            (REPOST, AUTHOR, "text", "", POST, "2026-08-02T00:00:00"),
        )
        cur.execute(
            "INSERT INTO pulse_posts (id,user_id,post_type,title,body,created_at) VALUES (?,?,?,?,?,?)",
            (REEL_POST, AUTHOR, "video", "A reel", "reel body", "2026-08-03T00:00:00"),
        )
        cur.execute("INSERT INTO pulse_reels (id,post_id,user_id) VALUES (?,?,?)", (REEL_ID, REEL_POST, AUTHOR))
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    # -- reads, deliberately mirroring the two production readers -------------

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def feed_flag(self, post_id=POST, user_id=VIEWER):
        """What the feed card shows: a row in `pulse_post_saves`."""
        conn = self.connect()
        row = conn.execute(
            "SELECT 1 FROM pulse_post_saves WHERE post_id=? AND user_id=?", (post_id, user_id)
        ).fetchone()
        conn.close()
        return bool(row)

    def library_rows(self, content_type="post", content_id=str(POST), user_id=VIEWER):
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM pulse_saved_items WHERE user_id=? AND content_type=? AND content_id=?",
            (user_id, content_type, content_id),
        ).fetchall()
        conn.close()
        return rows

    def flag_rows(self, post_id=POST, user_id=VIEWER):
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM pulse_post_saves WHERE post_id=? AND user_id=?", (post_id, user_id)
        ).fetchall()
        conn.close()
        return rows

    def drop_library_row(self, content_id=str(POST), user_id=VIEWER, content_type="post"):
        """Reproduce the drift: the library forgets, the flag remains.

        This is what `DELETE /api/pulse/saved/<item_id>` and the content-keyed
        unsave used to leave behind, and what an older build shipped for months.
        """
        conn = self.connect()
        conn.execute(
            "DELETE FROM pulse_saved_items WHERE user_id=? AND content_type=? AND content_id=?",
            (user_id, content_type, content_id),
        )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# The Flask-free twin: services.saved_content_service
# ---------------------------------------------------------------------------


class FakeDbService:
    def __init__(self, path):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class SavedContentServiceTest(SavedWiringCase):
    def setUp(self):
        super().setUp()
        self._real_db = saved_content_service.db_service
        saved_content_service.db_service = FakeDbService(self.db_path)

    def tearDown(self):
        saved_content_service.db_service = self._real_db
        super().tearDown()

    def saved_library_ids(self, content_type="all"):
        """The Saved screen's own query, not a hand-rolled SELECT."""
        return [
            item["content_id"]
            for item in saved_content_service.list_saved_items(VIEWER, content_type=content_type)
        ]

    def test_a_save_writes_both_tables(self):
        result = saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["saved"])
        self.assertTrue(result["changed"])
        self.assertTrue(self.feed_flag())
        self.assertEqual(len(self.library_rows()), 1)

    def test_the_saved_post_appears_under_posts_and_under_all(self):
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        self.assertIn(str(POST), self.saved_library_ids("post"))
        self.assertIn(str(POST), self.saved_library_ids("all"))

    def test_saving_again_is_idempotent_and_reports_no_change(self):
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        again = saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        self.assertTrue(again["saved"])
        self.assertFalse(again["changed"])
        self.assertEqual(len(self.flag_rows()), 1)
        self.assertEqual(len(self.library_rows()), 1)

    def test_a_save_repairs_a_library_row_that_went_missing(self):
        """The defect, stated as behaviour.

        Flag present, library row gone. The old code read only the flag, agreed
        with itself, and returned `changed=False` forever — the post could never
        get back into the library no matter how many times it was tapped.
        """
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        self.drop_library_row()
        self.assertTrue(self.feed_flag(), "precondition: the drift leaves the flag behind")

        result = saved_content_service.set_post_saved(VIEWER, POST, saved=True)

        self.assertTrue(result["changed"], "a half-saved post is not already saved")
        self.assertEqual(len(self.library_rows()), 1, "the library row is restored")
        self.assertIn(str(POST), self.saved_library_ids("post"))
        self.assertEqual(len(self.flag_rows()), 1, "and no duplicate flag row is written")

    def test_unsave_clears_both_tables(self):
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        result = saved_content_service.set_post_saved(VIEWER, POST, saved=False)
        self.assertFalse(result["saved"])
        self.assertTrue(result["changed"])
        self.assertFalse(self.feed_flag())
        self.assertEqual(self.library_rows(), [])
        self.assertEqual(self.saved_library_ids("post"), [])

    def test_unsave_of_a_half_saved_post_clears_the_leftover_flag(self):
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        self.drop_library_row()
        result = saved_content_service.set_post_saved(VIEWER, POST, saved=False)
        self.assertTrue(result["changed"], "there was something to remove")
        self.assertFalse(self.feed_flag())

    def test_unsave_of_a_post_that_was_never_saved_reports_no_change(self):
        result = saved_content_service.set_post_saved(VIEWER, POST, saved=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["changed"])

    def test_saving_a_repost_saves_the_original(self):
        """Canonical ids only. A repost wrapper is not a separate saveable thing."""
        result = saved_content_service.set_post_saved(VIEWER, REPOST, saved=True)
        self.assertEqual(result["post_id"], POST)
        self.assertTrue(self.feed_flag(POST))
        self.assertFalse(self.feed_flag(REPOST))
        self.assertEqual(self.saved_library_ids("post"), [str(POST)])

    def test_saving_the_post_then_its_repost_does_not_duplicate(self):
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        saved_content_service.set_post_saved(VIEWER, REPOST, saved=True)
        self.assertEqual(len(self.flag_rows(POST)), 1)
        self.assertEqual(len(self.library_rows()), 1)

    def test_two_different_posts_both_land_in_the_library(self):
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        saved_content_service.set_post_saved(VIEWER, REEL_POST, saved=True)
        self.assertEqual(sorted(self.saved_library_ids("post")), sorted([str(POST), str(REEL_POST)]))

    def test_a_missing_post_is_refused_rather_than_half_written(self):
        result = saved_content_service.set_post_saved(VIEWER, 4242, saved=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "post_not_found")
        self.assertEqual(self.library_rows(content_id="4242"), [])

    def test_an_unauthenticated_caller_is_refused(self):
        result = saved_content_service.set_post_saved(0, POST, saved=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_target")

    def test_one_viewers_save_is_not_another_viewers(self):
        saved_content_service.set_post_saved(VIEWER, POST, saved=True)
        self.assertFalse(self.feed_flag(POST, user_id=99))
        self.assertEqual(saved_content_service.list_saved_items(99), [])


# ---------------------------------------------------------------------------
# The HTTP path's implementation, lifted out of bot.py
# ---------------------------------------------------------------------------


class BotPostSaveTest(SavedWiringCase):
    """`pulse_apply_post_save` and `pulse_clear_post_save_mirror` from bot.py.

    The stubs below are the collaborators the functions reach for. Each one is
    the smallest thing that is still honest: `ensure_pulse_saved_collection`
    really creates a collection row, because the library insert has a foreign
    key into it; `pulse_notify_post_owner` records its calls, because "does a
    repair re-notify the author?" is a question with a right answer.
    """

    def setUp(self):
        super().setUp()
        self.notifications = []

        def ensure_pulse_saved_collection(cur, user_id, name="Saved"):
            cur.execute(
                "INSERT OR IGNORE INTO pulse_saved_collections (user_id,name,slug,is_default,created_at)"
                " VALUES (?,?,?,1,'2026-08-01T00:00:00')",
                (user_id, name, name.lower()),
            )
            cur.execute(
                "SELECT id FROM pulse_saved_collections WHERE user_id=? AND slug=? LIMIT 1",
                (user_id, name.lower()),
            )
            return int(cur.fetchone()["id"])

        def pulse_notify_post_owner(cur, post_id, user, kind, title, body, metadata=None):
            self.notifications.append((post_id, user["user_id"], kind))

        def safe_int(value, default=0):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        self.ns = load_bot_functions(
            ["pulse_savable_post_id", "pulse_apply_post_save", "pulse_clear_post_save_mirror"],
            {
                "json": json,
                "clean_html": lambda value: str(value or ""),
                "ensure_pulse_saved_collection": ensure_pulse_saved_collection,
                "pulse_notify_post_owner": pulse_notify_post_owner,
                "pulse_actor_display_name": lambda user: "Viewer",
                "safe_int": safe_int,
            },
        )
        self.user = {"user_id": VIEWER}
        self.now = "2026-08-06T00:00:00"

    def apply(self, post_id, want_saved):
        conn = self.connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1", (post_id,))
        post = cur.fetchone()
        result = self.ns["pulse_apply_post_save"](cur, self.user, post, want_saved, self.now)
        conn.commit()
        conn.close()
        return result

    def clear_mirror(self, content_type, content_id):
        conn = self.connect()
        cur = conn.cursor()
        cleared = self.ns["pulse_clear_post_save_mirror"](cur, VIEWER, content_type, content_id)
        conn.commit()
        conn.close()
        return cleared

    def test_a_save_writes_both_tables(self):
        saved, changed = self.apply(POST, True)
        self.assertTrue(saved)
        self.assertTrue(changed)
        self.assertTrue(self.feed_flag())
        self.assertEqual(len(self.library_rows()), 1)

    def test_saving_twice_neither_duplicates_nor_raises(self):
        self.apply(POST, True)
        saved, changed = self.apply(POST, True)
        self.assertTrue(saved)
        self.assertFalse(changed)
        self.assertEqual(len(self.flag_rows()), 1)
        self.assertEqual(len(self.library_rows()), 1)

    def test_a_save_repairs_a_library_row_that_went_missing(self):
        self.apply(POST, True)
        self.drop_library_row()
        saved, changed = self.apply(POST, True)
        self.assertTrue(saved)
        self.assertTrue(changed, "half-saved is not saved")
        self.assertEqual(len(self.library_rows()), 1)
        self.assertEqual(len(self.flag_rows()), 1, "UNIQUE(post_id,user_id) survived the repair")

    def test_a_repair_does_not_notify_the_author_a_second_time(self):
        self.apply(POST, True)
        self.assertEqual(len(self.notifications), 1)
        self.drop_library_row()
        self.apply(POST, True)
        self.assertEqual(len(self.notifications), 1, "the author was not pinged twice for one save")

    def test_a_save_repairs_a_flag_row_that_went_missing(self):
        """The mirror image: library row survives, feed flag was cleared."""
        self.apply(POST, True)
        conn = self.connect()
        conn.execute("DELETE FROM pulse_post_saves WHERE post_id=? AND user_id=?", (POST, VIEWER))
        conn.commit()
        conn.close()
        saved, changed = self.apply(POST, True)
        self.assertTrue(saved)
        self.assertTrue(changed)
        self.assertTrue(self.feed_flag(), "the feed agrees with the library again")

    def test_unsave_clears_both_tables(self):
        self.apply(POST, True)
        saved, changed = self.apply(POST, False)
        self.assertFalse(saved)
        self.assertTrue(changed)
        self.assertFalse(self.feed_flag())
        self.assertEqual(self.library_rows(), [])

    def test_unsave_of_nothing_reports_no_change(self):
        saved, changed = self.apply(POST, False)
        self.assertFalse(saved)
        self.assertFalse(changed)

    def test_an_omitted_intent_still_toggles(self):
        """The web templates POST an empty body and rely on this."""
        self.assertEqual(self.apply(POST, None), (True, True))
        self.assertEqual(self.apply(POST, None), (False, True))
        self.assertEqual(self.apply(POST, None), (True, True))

    def test_a_toggle_on_a_half_saved_post_completes_the_save(self):
        """Drift used to invert the toggle: the next tap unsaved instead of saving."""
        self.apply(POST, True)
        self.drop_library_row()
        saved, changed = self.apply(POST, None)
        self.assertTrue(saved, "the tap that follows a drift saves, it does not unsave")
        self.assertEqual(len(self.library_rows()), 1)

    def test_saving_a_repost_saves_the_original(self):
        self.apply(REPOST, True)
        self.assertTrue(self.feed_flag(POST))
        self.assertFalse(self.feed_flag(REPOST))
        self.assertEqual(len(self.library_rows(content_id=str(POST))), 1)

    def test_the_library_row_carries_the_originals_title_not_the_reposts(self):
        self.apply(REPOST, True)
        self.assertEqual(self.library_rows(content_id=str(POST))[0]["title"], "Original Title")

    def test_clearing_the_mirror_removes_a_posts_feed_flag(self):
        self.apply(POST, True)
        cleared = self.clear_mirror("post", str(POST))
        self.assertEqual(cleared, POST)
        self.assertFalse(self.feed_flag(), "a library removal no longer leaves the card filled")

    def test_clearing_the_mirror_for_a_reel_finds_the_post_underneath(self):
        self.apply(REEL_POST, True)
        cleared = self.clear_mirror("reel", str(REEL_ID))
        self.assertEqual(cleared, REEL_POST)
        self.assertFalse(self.feed_flag(REEL_POST))

    def test_clearing_the_mirror_leaves_types_that_have_no_flag_row_alone(self):
        """Rooms, groups and teachers live only in the library. Nothing to mirror."""
        self.apply(POST, True)
        for content_type in ("room", "group", "teacher", "learning", "marketplace"):
            self.assertEqual(self.clear_mirror(content_type, str(POST)), 0)
        self.assertTrue(self.feed_flag(), "an unrelated type did not touch this post's flag")

    def test_clearing_the_mirror_survives_a_non_numeric_content_id(self):
        self.assertEqual(self.clear_mirror("post", "abc"), 0)
        self.assertEqual(self.clear_mirror("post", ""), 0)
        self.assertEqual(self.clear_mirror("post", None), 0)


# ---------------------------------------------------------------------------
# The error copy the user actually reported
# ---------------------------------------------------------------------------


class FriendlyInternalErrorTest(unittest.TestCase):
    """A 500 must describe the endpoint that failed, not always an upload.

    The reported symptom was "Upload failed. Please retry or contact support with
    this trace ID." on the Saved screen — a read of the saved library, which
    uploads nothing. The copy came from the catch-all 500 handler, which said
    that to every JSON API path there is.
    """

    def setUp(self):
        # The handler calls `logging.exception`, which is correct and is left
        # alone — it is just muted so a passing run does not print tracebacks
        # that look like failures.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    class FakeRequest:
        def __init__(self, path, method="GET"):
            self.path = path
            self.method = method
            self.headers = {}
            self.remote_addr = ""

    def message_for(self, path, method="GET"):
        namespace = {
            "secrets": __import__("secrets"),
            "logging": __import__("logging"),
            "jsonify": lambda payload: payload,
            "request_expects_json_response": lambda: True,
            "request": self.FakeRequest(path, method),
        }
        load_bot_functions(["friendly_internal_error"], namespace)
        payload, status = namespace["friendly_internal_error"](RuntimeError("boom"))
        self.assertEqual(status, 500)
        return payload

    def test_the_saved_library_no_longer_blames_an_upload(self):
        payload = self.message_for("/api/pulse/saved")
        self.assertNotIn("Upload failed", payload["message"])
        self.assertIn("temporary service issue", payload["message"])

    def test_the_save_write_no_longer_blames_an_upload(self):
        payload = self.message_for("/api/pulse/posts/1/save", "POST")
        self.assertNotIn("Upload failed", payload["message"])

    def test_an_actual_upload_still_says_upload(self):
        """The copy was not wrong everywhere — only everywhere else."""
        for path in ("/api/uploads/direct", "/api/media/finish", "/api/messages/media/start"):
            self.assertIn("Upload failed", self.message_for(path, "POST")["message"], path)

    def test_a_call_endpoint_keeps_its_own_copy(self):
        self.assertIn("call", self.message_for("/api/calls/start", "POST")["message"])

    def test_the_trace_id_survives_on_every_path(self):
        """Support asks for this. It is the one thing the old copy got right."""
        for path in ("/api/pulse/saved", "/api/uploads/direct", "/api/calls/start"):
            payload = self.message_for(path)
            self.assertTrue(payload["trace_id"], path)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["error"], "server_error")

    def test_no_message_leaks_the_underlying_exception(self):
        for path in ("/api/pulse/saved", "/api/uploads/direct", "/api/calls/start"):
            self.assertNotIn("boom", self.message_for(path)["message"], path)


if __name__ == "__main__":
    unittest.main()
