"""Executable evidence for idempotent, verified UNDX post Like and Unlike."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services.feed_intelligence_service import get_post_like, set_post_like  # noqa: E402
from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


class FeedReactionWritePack(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        self.fx.cur.execute(
            """CREATE TABLE pulse_reactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              post_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL,
              reaction_type TEXT NOT NULL,
              created_at TEXT,
              UNIQUE(post_id,user_id)
            )"""
        )
        self.fx.commit()
        self.visible = patch(
            "services.feed_intelligence_service.get_post",
            return_value={"post_id": 42, "visibility": "public"},
        )
        self.visible.start()

    def tearDown(self) -> None:
        self.visible.stop()
        self.fx.stop()

    def test_like_is_explicit_and_idempotent(self) -> None:
        first = set_post_like(OWNER_ID, 42, liked=True)
        retry = set_post_like(OWNER_ID, 42, liked=True)
        self.assertTrue(first["changed"])
        self.assertFalse(retry["changed"])
        self.assertTrue(get_post_like(OWNER_ID, 42))
        self.fx.cur.execute(
            "SELECT COUNT(*) AS total FROM pulse_reactions WHERE post_id=42 AND user_id=?",
            (OWNER_ID,),
        )
        self.assertEqual(int(self.fx.cur.fetchone()["total"]), 1)

    def test_unlike_is_explicit_and_idempotent(self) -> None:
        set_post_like(OWNER_ID, 42, liked=True)
        first = set_post_like(OWNER_ID, 42, liked=False)
        retry = set_post_like(OWNER_ID, 42, liked=False)
        self.assertTrue(first["changed"])
        self.assertFalse(retry["changed"])
        self.assertFalse(get_post_like(OWNER_ID, 42))

    def test_inaccessible_post_never_mutates(self) -> None:
        with patch("services.feed_intelligence_service.get_post", return_value=None):
            result = set_post_like(OWNER_ID, 99, liked=True)
        self.assertEqual(result, {"ok": False, "error": "post_not_found"})
        self.fx.cur.execute("SELECT COUNT(*) AS total FROM pulse_reactions")
        self.assertEqual(int(self.fx.cur.fetchone()["total"]), 0)


if __name__ == "__main__":
    unittest.main()
