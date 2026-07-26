"""Behavioral tests for pulse_feed_engine.repost — create, undo, and idempotence.

Exercised against a real sqlite database rather than by grepping the source, for
the same reason as test_pulse_comment_pagination.py: a test that asserts a string
is present in production code proves only that the string is present, and can pin
a defect in place. The three defects this function exists to fix are behavioral,
so the tests have to be too.

The defects, for reference, since each has a test named after it below:

1. NO UNDO. The route it replaces had no delete path, so the mobile clients
   rendered a one-way button. Reposting by accident was permanent.
2. NO DEDUPE. Two taps wrote two repost rows on the same original.
3. NO STATE IN THE RESPONSE. It returned neither `reposted` nor a count, so a
   client could not reconcile with the server and had to invent both locally.

The count and the flag are checked together throughout. They come from two
different queries — `_repost_counts` groups `pulse_posts` by `repost_of_post_id`
and `_viewer_post_state` looks up the viewer's own row — and the whole point of
their shared `deleted_at IS NULL` predicate is that they can never disagree. A
test that only checked the count would miss a button stuck on "Reposted" above a
count of zero, which is precisely the state the old client invented.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import pulse_feed_engine  # noqa: E402


class FakeUserContext:
    """Minimal stand-in for services.user_context, backed by one temp file."""

    def __init__(self, path):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class RepostToggleTest(unittest.TestCase):
    AUTHOR = 11
    VIEWER = 22
    OTHER = 33
    POST = 1
    MISSING_POST = 4242

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db", prefix="pulse_repost_")
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE pulse_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                public_player_id TEXT,
                post_type TEXT,
                body TEXT,
                media_ids_json TEXT,
                title TEXT,
                tags_json TEXT,
                visibility TEXT DEFAULT 'public',
                moderation_status TEXT DEFAULT 'pending',
                repost_of_post_id INTEGER,
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT
            )
            """
        )
        cur.execute("CREATE TABLE arena_profiles (user_id INTEGER PRIMARY KEY, avatar_url TEXT, public_player_id TEXT)")
        cur.execute("CREATE TABLE pulse_post_saves (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, post_id INTEGER)")
        cur.execute("CREATE TABLE pulse_follows (id INTEGER PRIMARY KEY AUTOINCREMENT, follower_user_id INTEGER, followed_user_id INTEGER)")
        cur.execute("INSERT INTO arena_profiles (user_id, public_player_id) VALUES (?,?)", (self.AUTHOR, "@author"))
        cur.execute("INSERT INTO arena_profiles (user_id, public_player_id) VALUES (?,?)", (self.VIEWER, "@viewer"))
        cur.execute("INSERT INTO arena_profiles (user_id, public_player_id) VALUES (?,?)", (self.OTHER, "@other"))
        # Post 1: the original everything reposts. Post 2: soft-deleted original.
        cur.execute(
            "INSERT INTO pulse_posts (id, user_id, public_player_id, post_type, body, media_ids_json, title, tags_json,"
            " visibility, moderation_status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.POST, self.AUTHOR, "@author", "text", "original body", '["m1","m2"]',
                "Original Title", '["alpha"]', "public", "approved",
                "2026-07-25T00:00:00", "2026-07-25T00:00:00",
            ),
        )
        cur.execute(
            "INSERT INTO pulse_posts (id, user_id, post_type, body, created_at, deleted_at)"
            " VALUES (?,?,?,?,?,?)",
            (2, self.AUTHOR, "text", "tombstoned original", "2026-07-25T00:00:00", "2026-07-25T00:05:00"),
        )
        conn.commit()
        conn.close()

        self._real_context = pulse_feed_engine.user_context
        pulse_feed_engine.user_context = FakeUserContext(self.db_path)

    def tearDown(self):
        pulse_feed_engine.user_context = self._real_context
        os.unlink(self.db_path)

    # -- helpers -------------------------------------------------------------

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows

    def server_count(self, post_id=None):
        """The count as the feed serializer computes it."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        counts = pulse_feed_engine._repost_counts(cur, [int(post_id or self.POST)])
        conn.close()
        return int(counts.get(int(post_id or self.POST), 0))

    def server_flag(self, viewer_user_id, post_id=None):
        """The `reposted` flag as the feed serializer computes it for a viewer."""
        post_id = int(post_id or self.POST)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        state = pulse_feed_engine._viewer_post_state(
            cur, [{"id": post_id, "user_id": self.AUTHOR}], viewer_user_id=viewer_user_id
        )
        conn.close()
        return post_id in state["reposted"]

    def live_reposts(self, user_id, post_id=None):
        return self.query(
            "SELECT id FROM pulse_posts WHERE user_id=? AND repost_of_post_id=? AND deleted_at IS NULL",
            (int(user_id), int(post_id or self.POST)),
        )

    # -- defect 3: state in the response -------------------------------------

    def test_repost_returns_the_flag_and_the_count_the_client_needs(self):
        payload, status = pulse_feed_engine.repost(self.VIEWER, self.POST)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["reposted"])
        self.assertTrue(payload["is_reposted"])
        self.assertEqual(payload["repost_count"], 1)

    def test_the_returned_count_matches_what_the_feed_will_serve(self):
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        self.assertEqual(payload["repost_count"], self.server_count())

    def test_the_returned_flag_matches_what_the_feed_will_serve(self):
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        self.assertEqual(payload["reposted"], self.server_flag(self.VIEWER))

    def test_the_new_repost_id_is_returned_so_the_client_can_link_to_it(self):
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        rows = self.live_reposts(self.VIEWER)
        self.assertEqual(payload["post_id"], int(rows[0]["id"]))
        self.assertEqual(payload["original_post_id"], self.POST)
        self.assertEqual(payload["next_url"], f"/pulse/post/{payload['post_id']}")

    # -- defect 2: dedupe ----------------------------------------------------

    def test_reposting_twice_writes_only_one_row(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        self.assertEqual(len(self.live_reposts(self.VIEWER)), 1)

    def test_reposting_twice_does_not_double_the_count(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        payload, status = pulse_feed_engine.repost(self.VIEWER, self.POST)
        self.assertEqual(status, 200)
        self.assertEqual(payload["repost_count"], 1)
        self.assertEqual(self.server_count(), 1)

    def test_a_repeat_repost_reports_the_existing_row_not_a_failure(self):
        first, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        second, status = pulse_feed_engine.repost(self.VIEWER, self.POST)
        self.assertEqual(status, 200)
        self.assertTrue(second["ok"])
        self.assertTrue(second["reposted"])
        self.assertEqual(second["post_id"], first["post_id"])

    # -- defect 1: undo ------------------------------------------------------

    def test_undo_clears_the_flag_and_the_count(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        payload, status = pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["reposted"])
        self.assertFalse(payload["is_reposted"])
        self.assertEqual(payload["repost_count"], 0)

    def test_undo_leaves_the_feed_agreeing_with_the_response(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        self.assertEqual(self.server_count(), 0)
        self.assertFalse(self.server_flag(self.VIEWER))

    def test_undo_soft_deletes_rather_than_dropping_the_row(self):
        created, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        rows = self.query("SELECT deleted_at FROM pulse_posts WHERE id=?", (created["post_id"],))
        self.assertEqual(len(rows), 1, "the row must survive as a tombstone")
        self.assertTrue(rows[0]["deleted_at"])

    def test_undoing_when_nothing_is_reposted_succeeds_instead_of_404ing(self):
        """A double-tapped undo must not surface as a failure the user can see."""
        payload, status = pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["reposted"])
        self.assertEqual(payload["repost_count"], 0)

    def test_undo_is_idempotent(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        first, _ = pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        second, status = pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        self.assertEqual(status, 200)
        self.assertEqual(second["reposted"], first["reposted"])
        self.assertEqual(second["repost_count"], first["repost_count"])

    def test_undo_clears_every_duplicate_row_the_old_route_left_behind(self):
        """The migration case: two live rows from the create-only route.

        A single-row undo would leave the button stuck on "Reposted" with no way
        to clear it, because the flag only needs one surviving row to be true.
        """
        now = "2026-07-25T03:00:00"
        conn = sqlite3.connect(self.db_path)
        for _ in range(3):
            conn.execute(
                "INSERT INTO pulse_posts (user_id, post_type, body, repost_of_post_id, created_at)"
                " VALUES (?,?,?,?,?)",
                (self.VIEWER, "repost", "legacy duplicate", self.POST, now),
            )
        conn.commit()
        conn.close()
        self.assertEqual(len(self.live_reposts(self.VIEWER)), 3, "fixture must have duplicates")

        payload, status = pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["removed_post_ids"]), 3)
        self.assertEqual(self.live_reposts(self.VIEWER), [])
        self.assertEqual(payload["repost_count"], 0)
        self.assertFalse(self.server_flag(self.VIEWER))

    def test_repost_works_again_after_an_undo(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        payload, status = pulse_feed_engine.repost(self.VIEWER, self.POST)
        self.assertEqual(status, 200)
        self.assertTrue(payload["reposted"])
        self.assertEqual(payload["repost_count"], 1)
        self.assertTrue(self.server_flag(self.VIEWER))

    def test_a_full_toggle_cycle_never_leaves_flag_and_count_disagreeing(self):
        for _ in range(3):
            pulse_feed_engine.repost(self.VIEWER, self.POST)
            self.assertTrue(self.server_flag(self.VIEWER))
            self.assertEqual(self.server_count(), 1)
            pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
            self.assertFalse(self.server_flag(self.VIEWER))
            self.assertEqual(self.server_count(), 0)

    # -- isolation between viewers -------------------------------------------

    def test_two_viewers_reposting_are_counted_separately(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        payload, _ = pulse_feed_engine.repost(self.OTHER, self.POST)
        self.assertEqual(payload["repost_count"], 2)
        self.assertEqual(self.server_count(), 2)

    def test_one_viewers_undo_does_not_remove_anothers_repost(self):
        pulse_feed_engine.repost(self.VIEWER, self.POST)
        pulse_feed_engine.repost(self.OTHER, self.POST)
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST, undo=True)
        self.assertEqual(payload["repost_count"], 1)
        self.assertFalse(self.server_flag(self.VIEWER))
        self.assertTrue(self.server_flag(self.OTHER))
        self.assertEqual(len(self.live_reposts(self.OTHER)), 1)

    # -- missing originals ---------------------------------------------------

    def test_reposting_a_post_that_does_not_exist_is_404(self):
        payload, status = pulse_feed_engine.repost(self.VIEWER, self.MISSING_POST)
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])

    def test_reposting_a_deleted_original_is_404(self):
        payload, status = pulse_feed_engine.repost(self.VIEWER, 2)
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])

    def test_a_404_writes_nothing(self):
        pulse_feed_engine.repost(self.VIEWER, self.MISSING_POST)
        self.assertEqual(self.query("SELECT id FROM pulse_posts WHERE post_type='repost'"), [])

    # -- the repost row itself -----------------------------------------------

    def test_the_repost_row_is_typed_as_a_repost_and_points_at_the_original(self):
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        row = self.query("SELECT * FROM pulse_posts WHERE id=?", (payload["post_id"],))[0]
        self.assertEqual(row["post_type"], "repost")
        self.assertEqual(int(row["repost_of_post_id"]), self.POST)
        self.assertEqual(int(row["user_id"]), self.VIEWER)

    def test_the_repost_row_does_not_copy_the_originals_media(self):
        """Held by scripts/pulse_repost_media_audit.py at the route level too.

        A repost that duplicated `media_ids_json` would double-count the original's
        attachments and, worse, keep serving them after the original's media was
        removed.
        """
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        row = self.query("SELECT media_ids_json FROM pulse_posts WHERE id=?", (payload["post_id"],))[0]
        self.assertFalse(row["media_ids_json"], "repost rows must not carry their own media")

    def test_a_note_becomes_the_repost_body(self):
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST, note="  worth reading  ")
        row = self.query("SELECT body FROM pulse_posts WHERE id=?", (payload["post_id"],))[0]
        self.assertEqual(row["body"], "worth reading")

    def test_without_a_note_the_body_credits_the_original_author(self):
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        row = self.query("SELECT body FROM pulse_posts WHERE id=?", (payload["post_id"],))[0]
        self.assertIn("author", row["body"])

    def test_the_repost_is_visible_and_approved_so_it_reaches_the_feed(self):
        payload, _ = pulse_feed_engine.repost(self.VIEWER, self.POST)
        row = self.query("SELECT visibility, moderation_status FROM pulse_posts WHERE id=?", (payload["post_id"],))[0]
        self.assertEqual(row["visibility"], "public")
        self.assertEqual(row["moderation_status"], "approved")


if __name__ == "__main__":
    unittest.main()
