"""Executable training evidence for directed Follow and Unfollow."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services.social_relationship_service import is_following  # noqa: E402
from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


class SocialRelationshipWritePack(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        self.fx.cur.execute("ALTER TABLE users ADD COLUMN deleted_at TEXT")
        self.fx.cur.execute(
            """CREATE TABLE pulse_follows (
              follower_user_id INTEGER, followed_user_id INTEGER,
              followed_public_player_id TEXT, created_at TEXT,
              PRIMARY KEY(follower_user_id,followed_user_id)
            )"""
        )
        self.fx.commit()
        from services import undx_agent_runtime

        self.runtime = undx_agent_runtime

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, request_id: str):
        response = self.runtime.handle(
            self.fx.cur, user_id=OWNER_ID, text=text, request_id=request_id,
        )
        self.fx.commit()
        return response

    def test_follow_is_directed_and_verified(self) -> None:
        response = self.say(f"Follow user {OTHER_ID}.", "follow-other")
        self.assertEqual(response.status, "verified_success")
        self.assertTrue(response.card["verified"])
        self.assertTrue(is_following(OWNER_ID, OTHER_ID))
        self.assertFalse(is_following(OTHER_ID, OWNER_ID))

    def test_follow_retry_is_idempotent(self) -> None:
        self.say(f"Follow user {OTHER_ID}.", "follow-first")
        replay = self.say(f"Follow user {OTHER_ID}.", "follow-retry")
        self.assertEqual(replay.status, "verified_success")
        self.fx.cur.execute(
            """SELECT COUNT(*) AS total FROM pulse_follows
               WHERE follower_user_id=? AND followed_user_id=?""",
            (OWNER_ID, OTHER_ID),
        )
        self.assertEqual(int(self.fx.cur.fetchone()["total"]), 1)

    def test_unfollow_is_verified_and_real_undo(self) -> None:
        self.say(f"Follow user {OTHER_ID}.", "before-unfollow")
        response = self.say(f"Unfollow user {OTHER_ID}.", "unfollow-other")
        self.assertEqual(response.status, "verified_success")
        self.assertFalse(is_following(OWNER_ID, OTHER_ID))
        self.assertEqual(response.card["undo_capability_id"], "social.follow")

    def test_self_follow_is_refused(self) -> None:
        response = self.say(f"Follow user {OWNER_ID}.", "follow-self")
        self.assertEqual(response.status, "permission_denied")
        self.assertIsNone(is_following(OWNER_ID, OWNER_ID))

    def test_unknown_target_is_refused_without_mutation(self) -> None:
        response = self.say("Follow user 999.", "follow-missing")
        self.assertEqual(response.status, "permission_denied")
        self.fx.cur.execute("SELECT COUNT(*) AS total FROM pulse_follows")
        self.assertEqual(int(self.fx.cur.fetchone()["total"]), 0)


if __name__ == "__main__":
    unittest.main()
