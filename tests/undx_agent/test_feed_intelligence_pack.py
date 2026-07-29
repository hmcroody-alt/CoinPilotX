"""Contract tests for privacy-scoped, side-effect-free Feed intelligence."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from services.feed_intelligence_service import get_post, list_post_comments, list_posts


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


if __name__ == "__main__":
    unittest.main()
