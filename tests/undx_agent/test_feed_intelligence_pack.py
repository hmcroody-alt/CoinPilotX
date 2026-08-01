"""Contract tests for privacy-scoped, side-effect-free Feed intelligence."""

from __future__ import annotations

import unittest
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

from services.feed_intelligence_service import (
    get_post,
    list_post_comments,
    list_posts,
    post_performance_summary,
    summarize_post_comments,
)
from services import undx_agent_tools, undx_verification
from services.undx_capability_registry import get as get_capability


PUBLIC_POST = {
    "id": 41,
    "user_id": 2,
    "body": "QA launch update",
    "visibility": "public",
    "comment_count": 1,
    "reaction_counts": {"like": 2},
    "author": {"display_name": "QA Alice"},
    "permalink": "/pulse/post/41",
}


class FeedIntelligencePack(unittest.TestCase):
    def test_canonical_delete_is_owner_scoped_and_read_back(self) -> None:
        path = tempfile.mktemp(prefix="undx_post_delete_", suffix=".db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE pulse_posts(id INTEGER PRIMARY KEY,user_id INTEGER,deleted_at TEXT,updated_at TEXT,moderation_status TEXT)"
        )
        conn.executemany(
            "INSERT INTO pulse_posts(id,user_id,moderation_status) VALUES(?,?,?)",
            [(41, 7, "approved"), (42, 8, "approved")],
        )
        conn.commit()
        conn.close()

        def connect():
            db_conn = sqlite3.connect(path)
            db_conn.row_factory = sqlite3.Row
            return db_conn

        from services import pulse_feed_engine
        with patch.object(pulse_feed_engine.user_context, "connect", side_effect=connect):
            self.assertTrue(pulse_feed_engine.delete_owned_post(7, 41)["changed"])
            self.assertTrue(pulse_feed_engine.get_owned_post_deletion_state(7, 41)["deleted"])
            self.assertEqual(pulse_feed_engine.delete_owned_post(7, 42)["error"], "not_found")
            self.assertFalse(pulse_feed_engine.get_owned_post_deletion_state(7, 42))

    def test_delete_capability_is_high_risk_confirmed_and_verifiable(self) -> None:
        spec = get_capability("feed.posts.delete")
        self.assertEqual(spec.confirmation, "always")
        self.assertEqual(spec.risk, "consequential_write")
        self.assertIn(spec.executor, undx_agent_tools.EXECUTORS)
        self.assertIn(spec.verifier, undx_verification.VERIFIERS)

    @patch("services.pulse_feed_engine.delete_owned_post")
    def test_delete_tool_passes_authenticated_owner_and_canonical_id(self, delete_post) -> None:
        delete_post.return_value = {"ok": True, "post_id": 41, "deleted": True, "changed": True}
        result = undx_agent_tools.feed_post_delete(7, {"post_id": 41})
        delete_post.assert_called_once_with(7, 41)
        self.assertTrue(result.ok)
        self.assertEqual(result.canonical_resource_id, "post:41")

    @patch("services.pulse_feed_engine.get_owned_post_deletion_state")
    def test_delete_verifier_independently_reads_soft_delete(self, read_state) -> None:
        read_state.return_value = {"post_id": 41, "deleted": True}
        tool_result = undx_agent_tools.ToolResult(
            ok=True, tool_name="pulsesoc.feed.posts.delete",
            capability_id="feed.posts.delete", canonical_resource_id="post:41",
        )
        result = undx_verification.feed_post_deleted(7, {"post_id": 41}, tool_result)
        read_state.assert_called_once_with(7, 41)
        self.assertEqual(result.state, "verified")
    @patch("services.feed_intelligence_service.pulse_feed_engine.list_feed")
    def test_feed_list_is_viewer_scoped_and_projects_safe_fields(self, list_feed) -> None:
        list_feed.return_value = {"posts": [dict(PUBLIC_POST)]}
        rows = list_posts(7, feed="following", query="launch", limit=5)
        list_feed.assert_called_once_with(
            viewer_user_id=7, feed="following", topic="launch", limit=5, offset=0,
        )
        self.assertEqual(rows[0]["post_id"], 41)
        self.assertEqual(rows[0]["reaction_count"], 2)
        self.assertNotIn("metadata_json", rows[0])

    @patch("services.feed_intelligence_service.pulse_feed_engine.get_post")
    def test_single_post_delegates_visibility_to_canonical_engine(self, get_engine_post) -> None:
        get_engine_post.return_value = dict(PUBLIC_POST)
        self.assertEqual(get_post(7, 41)["post_id"], 41)
        get_engine_post.assert_called_once_with(41, viewer_user_id=7, include_private=True)

    @patch("services.feed_intelligence_service.pulse_feed_engine.get_post")
    def test_inaccessible_post_is_indistinguishable_from_missing(self, get_engine_post) -> None:
        get_engine_post.return_value = None
        self.assertIsNone(get_post(7, 99))

    @patch("services.feed_intelligence_service.pulse_feed_engine.list_comments")
    @patch("services.feed_intelligence_service.pulse_feed_engine.get_post")
    def test_comments_require_visible_parent_before_query(self, get_engine_post, list_comments) -> None:
        get_engine_post.return_value = None
        self.assertEqual(list_post_comments(7, 99), [])
        list_comments.assert_not_called()

    @patch("services.feed_intelligence_service.pulse_feed_engine.list_comments")
    @patch("services.feed_intelligence_service.pulse_feed_engine.get_post")
    def test_comments_are_bounded_and_owner_controls_are_server_values(
        self, get_engine_post, list_comments
    ) -> None:
        get_engine_post.return_value = dict(PUBLIC_POST)
        list_comments.return_value = {
            "comments": [{
                "id": 8, "post_id": 41, "user_id": 7, "body": "Ready",
                "can_edit": True, "can_delete": True,
                "author": {"display_name": "QA User"},
            }]
        }
        rows = list_post_comments(7, 41, limit=500)
        list_comments.assert_called_once_with(41, limit=80, offset=0, viewer_user_id=7)
        self.assertTrue(rows[0]["can_delete"])
        self.assertEqual(rows[0]["source_url"], "/pulse/post/41")

    @patch("services.feed_intelligence_service.db_service.connect")
    @patch("services.feed_intelligence_service.get_post")
    def test_performance_is_owner_only_and_reports_available_metrics(self, get_visible, connect) -> None:
        get_visible.return_value = {
            **get_post.__globals__["_post_record"]({
                **PUBLIC_POST, "user_id": 7, "view_count": 12, "repost_count": 3,
            }),
        }
        row = MagicMock()
        row.__iter__.return_value = iter({"total": 4}.items())
        cursor = connect.return_value.cursor.return_value
        cursor.fetchone.side_effect = [{"owned": 1}, {"total": 4}]
        result = post_performance_summary(7, 41)
        self.assertEqual(result["views"], 12)
        self.assertEqual(result["shares"], 3)
        self.assertEqual(result["saves"], 4)

    @patch("services.feed_intelligence_service.db_service.connect")
    @patch("services.feed_intelligence_service.get_post")
    def test_performance_refuses_visible_post_owned_by_someone_else(self, get_visible, connect) -> None:
        get_visible.return_value = get_post.__globals__["_post_record"](PUBLIC_POST)
        connect.return_value.cursor.return_value.fetchone.return_value = None
        self.assertIsNone(post_performance_summary(7, 41))

    @patch("services.feed_intelligence_service.db_service.connect")
    @patch("services.feed_intelligence_service.list_post_comments")
    @patch("services.feed_intelligence_service.get_post")
    def test_comment_summary_uses_only_owner_authorized_comments(
        self, get_visible, list_comments, connect
    ) -> None:
        get_visible.return_value = get_post.__globals__["_post_record"]({**PUBLIC_POST, "user_id": 7})
        connect.return_value.cursor.return_value.fetchone.return_value = {"owned": 1}
        list_comments.return_value = [
            {"body": "Launch looks great", "author_user_id": 2},
            {"body": "Ready for Friday", "author_user_id": 3},
        ]
        result = summarize_post_comments(7, 41)
        self.assertEqual(result["comment_count"], 2)
        self.assertEqual(result["participant_count"], 2)
        self.assertIn("Ready for Friday", result["summary"])


if __name__ == "__main__":
    unittest.main()
