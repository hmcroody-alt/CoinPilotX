"""Executable training evidence for the owner-scoped Saved Content read skill."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


class SavedContentPack(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        self.fx.cur.execute(
            """CREATE TABLE pulse_saved_collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
                slug TEXT, is_default INTEGER, created_at TEXT, updated_at TEXT)"""
        )
        self.fx.cur.execute(
            """CREATE TABLE pulse_saved_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                collection_id INTEGER, content_type TEXT, content_id TEXT,
                title TEXT, preview_text TEXT, thumbnail_url TEXT, media_url TEXT,
                source_url TEXT, metadata_json TEXT, created_at TEXT, updated_at TEXT,
                UNIQUE(user_id, content_type, content_id))"""
        )
        self.fx.cur.execute(
            "INSERT INTO pulse_saved_collections (user_id,name,slug,is_default) VALUES (?,?,?,1)",
            (OWNER_ID, "Favorites", "favorites"),
        )
        owner_collection = int(self.fx.cur.lastrowid)
        self.fx.cur.execute(
            "INSERT INTO pulse_saved_collections (user_id,name,slug,is_default) VALUES (?,?,?,1)",
            (OTHER_ID, "Private", "private"),
        )
        other_collection = int(self.fx.cur.lastrowid)
        self.fx.cur.executemany(
            """INSERT INTO pulse_saved_items
               (user_id,collection_id,content_type,content_id,title,preview_text,source_url,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                (OWNER_ID, owner_collection, "post", "41", "Owner post", "Mine", "/pulse/post/41", "2026-01-01", "2026-01-01"),
                (OWNER_ID, owner_collection, "reel", "9", "Owner reel", "Mine", "/pulse/reels?reel=9", "2026-01-02", "2026-01-02"),
                (OTHER_ID, other_collection, "post", "88", "Other private post", "Secret", "/pulse/post/88", "2026-01-03", "2026-01-03"),
            ),
        )
        self.fx.commit()
        from services import undx_agent_runtime

        self.runtime = undx_agent_runtime

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, *, user_id: int = OWNER_ID):
        response = self.runtime.handle(self.fx.cur, user_id=user_id, text=text)
        self.fx.commit()
        return response

    def test_find_saved_posts_is_structured_and_owner_scoped(self):
        response = self.say("Find my saved posts.")
        self.assertTrue(response.handled)
        self.assertEqual(response.status, "verified_success")
        self.assertEqual(response.card["capability_id"], "saved.items.list")
        self.assertEqual([row["content_id"] for row in response.card["records"]], ["41"])
        self.assertNotIn("88", {row["content_id"] for row in response.card["records"]})
        self.assertEqual(response.receipt.native_deep_link, "/pulse/saved")

    def test_accounts_cannot_see_each_others_saved_library(self):
        mine = self.say("Show my saved items.")
        theirs = self.say("Show my saved items.", user_id=OTHER_ID)
        self.assertEqual({row["content_id"] for row in mine.card["records"]}, {"41", "9"})
        self.assertEqual({row["content_id"] for row in theirs.card["records"]}, {"88"})

    def test_saved_reels_are_narrowed_without_model_guessing(self):
        response = self.say("Find my saved Reels.")
        self.assertEqual([row["content_type"] for row in response.card["records"]], ["reel"])
        self.assertNotIn("confirmation_token", response.card)


if __name__ == "__main__":
    unittest.main()
