"""Behavioral tests for offset-based pagination of PulseSoc post comments.

These exercise pulse_feed_engine.list_comments against a real sqlite database
rather than asserting on the shape of the source. The reason is the standing
lesson from this repo's Reels preload defect: a test that greps production code
proves only that a string is present, and can pin a defect in place. A comment
pager either returns the right rows for the right window or it does not, and the
only way to know is to insert rows and ask for windows.
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


class CommentPaginationTest(unittest.TestCase):
    TOTAL = 25
    VIEWER = 7
    OTHER = 9

    @classmethod
    def setUpClass(cls):
        handle, cls.db_path = tempfile.mkstemp(suffix=".db", prefix="pulse_comments_")
        os.close(handle)
        conn = sqlite3.connect(cls.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE pulse_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                user_id INTEGER,
                parent_comment_id INTEGER,
                body TEXT,
                created_at TEXT,
                updated_at TEXT,
                edited_at TEXT,
                deleted_at TEXT,
                moderation_status TEXT
            )
            """
        )
        cur.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, email TEXT, full_name TEXT, display_name TEXT, avatar_url TEXT, plan TEXT, subscription_plan TEXT, subscription_status TEXT, is_pro INTEGER, pro_active INTEGER, pro_expires_at TEXT, subscription_expires_at TEXT)")
        cur.execute("CREATE TABLE arena_profiles (user_id INTEGER PRIMARY KEY, avatar_url TEXT, public_player_id TEXT)")
        cur.execute("INSERT INTO users (user_id, username) VALUES (?,?)", (cls.VIEWER, "viewer"))
        cur.execute("INSERT INTO users (user_id, username) VALUES (?,?)", (cls.OTHER, "other"))

        # 25 visible comments on post 1, ordered by created_at then id.
        for index in range(cls.TOTAL):
            author = cls.VIEWER if index % 2 == 0 else cls.OTHER
            parent = 1 if index in (1, 2) else None
            cur.execute(
                "INSERT INTO pulse_comments (post_id, user_id, parent_comment_id, body, created_at, moderation_status)"
                " VALUES (?,?,?,?,?,?)",
                (1, author, parent, f"comment {index}", f"2026-07-25T00:{index:02d}:00", "approved"),
            )
        # Rows that must never appear in any page or in the total.
        cur.execute(
            "INSERT INTO pulse_comments (post_id, user_id, body, created_at, deleted_at, moderation_status)"
            " VALUES (?,?,?,?,?,?)",
            (1, cls.OTHER, "tombstoned", "2026-07-25T01:00:00", "2026-07-25T01:01:00", "approved"),
        )
        cur.execute(
            "INSERT INTO pulse_comments (post_id, user_id, body, created_at, moderation_status)"
            " VALUES (?,?,?,?,?)",
            (1, cls.OTHER, "blocked", "2026-07-25T01:02:00", "blocked"),
        )
        # A comment on a different post, to prove the post_id filter.
        cur.execute(
            "INSERT INTO pulse_comments (post_id, user_id, body, created_at, moderation_status)"
            " VALUES (?,?,?,?,?)",
            (2, cls.OTHER, "other post", "2026-07-25T02:00:00", "approved"),
        )
        conn.commit()
        conn.close()

        cls._real_context = pulse_feed_engine.user_context
        pulse_feed_engine.user_context = FakeUserContext(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        pulse_feed_engine.user_context = cls._real_context
        os.unlink(cls.db_path)

    def bodies(self, **kwargs):
        return [item["body"] for item in pulse_feed_engine.list_comments(1, **kwargs)["comments"]]

    # -- total ---------------------------------------------------------------

    def test_total_counts_only_visible_comments_on_this_post(self):
        result = pulse_feed_engine.list_comments(1)
        self.assertEqual(result["total"], self.TOTAL)

    def test_deleted_and_blocked_comments_are_excluded_from_pages(self):
        bodies = self.bodies(limit=120)
        self.assertNotIn("tombstoned", bodies)
        self.assertNotIn("blocked", bodies)

    def test_comments_from_another_post_are_excluded(self):
        self.assertNotIn("other post", self.bodies(limit=120))

    # -- windowing -----------------------------------------------------------

    def test_first_page_returns_the_oldest_comments_in_order(self):
        self.assertEqual(self.bodies(limit=5, offset=0), [f"comment {i}" for i in range(5)])

    def test_offset_advances_the_window_by_exactly_offset_rows(self):
        self.assertEqual(self.bodies(limit=5, offset=5), [f"comment {i}" for i in range(5, 10)])

    def test_consecutive_pages_neither_skip_nor_repeat_a_comment(self):
        collected = []
        offset = 0
        while True:
            page = pulse_feed_engine.list_comments(1, limit=7, offset=offset)
            collected.extend(item["body"] for item in page["comments"])
            if not page["has_more"]:
                break
            offset += page["limit"]
        self.assertEqual(collected, [f"comment {i}" for i in range(self.TOTAL)])
        self.assertEqual(len(collected), len(set(collected)))

    def test_final_partial_page_returns_the_remainder(self):
        page = pulse_feed_engine.list_comments(1, limit=10, offset=20)
        self.assertEqual(len(page["comments"]), 5)

    def test_offset_past_the_end_returns_an_empty_page_not_an_error(self):
        page = pulse_feed_engine.list_comments(1, limit=10, offset=999)
        self.assertEqual(page["comments"], [])
        self.assertTrue(page["ok"])
        self.assertEqual(page["total"], self.TOTAL)

    # -- has_more ------------------------------------------------------------

    def test_has_more_is_true_while_comments_remain(self):
        self.assertTrue(pulse_feed_engine.list_comments(1, limit=10, offset=0)["has_more"])
        self.assertTrue(pulse_feed_engine.list_comments(1, limit=10, offset=10)["has_more"])

    def test_has_more_is_false_on_the_last_page(self):
        self.assertFalse(pulse_feed_engine.list_comments(1, limit=10, offset=20)["has_more"])

    def test_has_more_is_false_when_a_full_page_exactly_exhausts_the_total(self):
        """The case a client cannot infer from page length alone."""
        page = pulse_feed_engine.list_comments(1, limit=5, offset=20)
        self.assertEqual(len(page["comments"]), 5)
        self.assertFalse(page["has_more"])

    # -- clamping ------------------------------------------------------------

    def test_limit_is_clamped_to_the_documented_maximum(self):
        page = pulse_feed_engine.list_comments(1, limit=10_000)
        self.assertEqual(page["limit"], pulse_feed_engine.COMMENT_PAGE_LIMIT_MAX)

    def test_a_zero_or_negative_limit_falls_back_to_a_usable_page(self):
        self.assertGreaterEqual(pulse_feed_engine.list_comments(1, limit=0)["limit"], 1)
        self.assertGreaterEqual(pulse_feed_engine.list_comments(1, limit=-5)["limit"], 1)

    def test_a_negative_offset_is_treated_as_the_first_page(self):
        page = pulse_feed_engine.list_comments(1, limit=3, offset=-10)
        self.assertEqual(page["offset"], 0)
        self.assertEqual([item["body"] for item in page["comments"]], ["comment 0", "comment 1", "comment 2"])

    def test_default_call_is_backward_compatible(self):
        """bot.py has four callers that pass only post_id."""
        result = pulse_feed_engine.list_comments(1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["offset"], 0)
        self.assertEqual(result["limit"], pulse_feed_engine.COMMENT_PAGE_LIMIT_DEFAULT)
        self.assertEqual(len(result["comments"]), self.TOTAL)

    # -- viewer permissions --------------------------------------------------

    def test_viewer_may_edit_and_delete_only_their_own_comments(self):
        comments = pulse_feed_engine.list_comments(1, limit=120, viewer_user_id=self.VIEWER)["comments"]
        mine = [item for item in comments if item["user_id"] == self.VIEWER]
        theirs = [item for item in comments if item["user_id"] == self.OTHER]
        self.assertTrue(mine and theirs, "fixture must contain both authors")
        self.assertTrue(all(item["can_edit"] and item["can_delete"] for item in mine))
        self.assertTrue(all(not item["can_edit"] and not item["can_delete"] for item in theirs))

    def test_without_a_viewer_no_comment_claims_to_be_editable(self):
        comments = pulse_feed_engine.list_comments(1, limit=120)["comments"]
        self.assertTrue(all(not item["can_edit"] and not item["can_delete"] for item in comments))

    # -- reply metadata ------------------------------------------------------

    def test_parent_comment_id_is_returned_so_a_client_can_nest_replies(self):
        comments = pulse_feed_engine.list_comments(1, limit=120)["comments"]
        replies = [item for item in comments if item["parent_comment_id"]]
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(item["parent_comment_id"] == 1 for item in replies))

    def test_replies_are_paged_alongside_their_parents_not_separately(self):
        """The flat page is the contract; nesting is the client's transform."""
        page = pulse_feed_engine.list_comments(1, limit=3, offset=0)
        self.assertEqual([item["parent_comment_id"] for item in page["comments"]], [None, 1, 1])


if __name__ == "__main__":
    unittest.main()
