from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.social_relationship_service import list_relationships


class SocialRelationshipPackTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        handle.close()
        self.path = handle.name
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE users (
              user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT,
              full_name TEXT, avatar_url TEXT, avatar_thumbnail_url TEXT,
              deleted_at TEXT
            );
            CREATE TABLE pulse_follows (
              follower_user_id INTEGER, followed_user_id INTEGER,
              followed_public_player_id TEXT, created_at TEXT,
              PRIMARY KEY(follower_user_id, followed_user_id)
            );
            INSERT INTO users VALUES
              (1,'owner','Owner','','','',''),
              (2,'alice','Alice','','','',''),
              (3,'bob','Bob','','','',''),
              (4,'other','Other','','','','');
            INSERT INTO pulse_follows VALUES
              (2,1,'','2026-07-28T01:00:00'),
              (1,3,'','2026-07-28T02:00:00'),
              (2,4,'','2026-07-28T03:00:00');
            """
        )
        conn.commit()
        conn.close()
        def connect():
            database = sqlite3.connect(self.path)
            database.row_factory = sqlite3.Row
            return database

        self.connection_patch = patch(
            "services.social_relationship_service.db_service.connect",
            side_effect=connect,
        )
        self.connection_patch.start()

    def tearDown(self) -> None:
        self.connection_patch.stop()
        os.unlink(self.path)

    def test_lists_only_callers_followers(self) -> None:
        self.assertEqual([row["username"] for row in list_relationships(1)], ["alice"])

    def test_lists_only_callers_following(self) -> None:
        rows = list_relationships(1, direction="following")
        self.assertEqual([row["username"] for row in rows], ["bob"])

    def test_search_does_not_cross_account_boundary(self) -> None:
        self.assertEqual(list_relationships(1, query="other"), [])

    def test_requires_authenticated_owner(self) -> None:
        self.assertEqual(list_relationships(0), [])


if __name__ == "__main__":
    unittest.main()
