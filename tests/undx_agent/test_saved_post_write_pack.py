"""Executable training evidence for idempotent Saved-post writes."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services.saved_content_service import get_post_saved  # noqa: E402
from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


class SavedPostWritePack(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        self.fx.cur.executescript(
            """
            CREATE TABLE pulse_posts (
              id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, body TEXT,
              post_type TEXT, repost_of_post_id INTEGER, deleted_at TEXT
            );
            CREATE TABLE pulse_post_saves (
              id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER,
              collection_name TEXT, created_at TEXT,
              UNIQUE(post_id,user_id)
            );
            CREATE TABLE pulse_saved_collections (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
              slug TEXT, is_default INTEGER, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE pulse_saved_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
              collection_id INTEGER, content_type TEXT, content_id TEXT,
              title TEXT, preview_text TEXT, thumbnail_url TEXT, media_url TEXT,
              source_url TEXT, metadata_json TEXT, created_at TEXT, updated_at TEXT,
              UNIQUE(user_id,content_type,content_id)
            );
            INSERT INTO pulse_posts
              (id,user_id,title,body,post_type,repost_of_post_id,deleted_at)
              VALUES (41,2,'QA post','Saved write test','post',NULL,NULL);
            """
        )
        self.fx.commit()
        from services import undx_agent_runtime

        self.runtime = undx_agent_runtime

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, *, user_id: int = OWNER_ID, request_id: str = ""):
        response = self.runtime.handle(
            self.fx.cur,
            user_id=user_id,
            text=text,
            request_id=request_id,
        )
        self.fx.commit()
        return response

    def test_save_is_verified_and_writes_both_canonical_tables(self) -> None:
        response = self.say("Save post 41.", request_id="save-41")
        self.assertEqual(response.status, "verified_success")
        self.assertTrue(response.card["verified"])
        self.assertEqual(get_post_saved(OWNER_ID, 41), {"post_id": 41, "saved": True})
        self.fx.cur.execute(
            """SELECT COUNT(*) AS total FROM pulse_saved_items
               WHERE user_id=? AND content_type='post' AND content_id='41'""",
            (OWNER_ID,),
        )
        self.assertEqual(int(self.fx.cur.fetchone()["total"]), 1)

    def test_retry_never_toggles_the_saved_state(self) -> None:
        self.say("Save post 41.", request_id="save-first")
        replay = self.say("Save post 41.", request_id="save-retry")
        self.assertEqual(replay.status, "verified_success")
        self.assertTrue(get_post_saved(OWNER_ID, 41)["saved"])

    def test_unsave_is_verified_and_is_the_real_inverse(self) -> None:
        self.say("Save post 41.", request_id="save-before-undo")
        response = self.say("Unsave post 41.", request_id="unsave-41")
        self.assertEqual(response.status, "verified_success")
        self.assertFalse(get_post_saved(OWNER_ID, 41)["saved"])

    def test_account_saved_state_is_isolated(self) -> None:
        self.say("Save post 41.", request_id="owner-save")
        self.assertTrue(get_post_saved(OWNER_ID, 41)["saved"])
        self.assertFalse(get_post_saved(OTHER_ID, 41)["saved"])

    def test_missing_post_fails_without_a_success_receipt(self) -> None:
        response = self.say("Save post 999.", request_id="missing-save")
        self.assertNotEqual(response.status, "verified_success")
        self.assertFalse(response.card["verified"])


if __name__ == "__main__":
    unittest.main()
