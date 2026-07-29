"""Executable privacy and state tests for UNDX content graph intelligence."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from services import content_graph_intelligence_service as graph
from services.undx_agent_runtime import match_capability


class _ConnectionProxy:
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return self.connection.cursor()

    def commit(self):
        return self.connection.commit()

    def rollback(self):
        return self.connection.rollback()

    def close(self):
        return None


class ContentGraphIntelligencePack(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE users(user_id INTEGER PRIMARY KEY,username TEXT,display_name TEXT,bio TEXT,
          avatar_url TEXT,profile_visibility TEXT,preferred_language TEXT,created_at TEXT,last_seen_at TEXT,updated_at TEXT,
          deleted_at TEXT,account_status TEXT);
        CREATE TABLE pulse_posts(id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,body TEXT,
          post_type TEXT,repost_of_post_id INTEGER,visibility TEXT,deleted_at TEXT,status TEXT);
        CREATE TABLE pulse_reels(id INTEGER PRIMARY KEY,post_id INTEGER,user_id INTEGER,caption TEXT,
          category TEXT,created_at TEXT,share_count INTEGER,completion_rate REAL,replay_count INTEGER,
          status TEXT,moderation_status TEXT);
        CREATE TABLE pulse_reactions(id INTEGER PRIMARY KEY,post_id INTEGER,user_id INTEGER,
          reaction_type TEXT,created_at TEXT);
        CREATE TABLE pulse_comments(id INTEGER PRIMARY KEY,post_id INTEGER,user_id INTEGER,body TEXT,
          created_at TEXT,deleted_at TEXT);
        CREATE TABLE pulse_post_saves(id INTEGER PRIMARY KEY,post_id INTEGER,user_id INTEGER,
          collection_name TEXT,created_at TEXT);
        CREATE TABLE pulse_saved_collections(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT,
          slug TEXT,is_default INTEGER,created_at TEXT,updated_at TEXT);
        CREATE TABLE pulse_saved_items(id INTEGER PRIMARY KEY,user_id INTEGER,collection_id INTEGER,
          content_type TEXT,content_id TEXT,title TEXT,preview_text TEXT,thumbnail_url TEXT,
          media_url TEXT,source_url TEXT,metadata_json TEXT,created_at TEXT,updated_at TEXT,
          UNIQUE(user_id,content_type,content_id));
        CREATE TABLE pulse_statuses(id INTEGER PRIMARY KEY,user_id INTEGER,status_type TEXT,body TEXT,
          visibility TEXT,created_at TEXT,expires_at TEXT,deleted_at TEXT);
        CREATE TABLE pulse_status_views(id INTEGER PRIMARY KEY,status_id INTEGER,viewer_user_id INTEGER,
          viewed_at TEXT,completion_ratio REAL);
        CREATE TABLE pulse_status_reactions(id INTEGER PRIMARY KEY,status_id INTEGER,user_id INTEGER,
          reaction_type TEXT,created_at TEXT);
        CREATE TABLE pulse_follows(follower_user_id INTEGER,followed_user_id INTEGER);
        """)
        self.conn.executescript("""
        INSERT INTO users VALUES(1,'owner','Owner','bio','','public','en','now','now','now',NULL,'active');
        INSERT INTO users VALUES(2,'other','Other','','','private','en','now','now','now',NULL,'active');
        INSERT INTO pulse_posts VALUES(10,1,'Owner Reel','','video',NULL,'public',NULL,'published');
        INSERT INTO pulse_posts VALUES(20,2,'Private Reel','','video',NULL,'private',NULL,'published');
        INSERT INTO pulse_reels VALUES(1,10,1,'Launch Reel','Community','now',3,.75,2,'active','approved');
        INSERT INTO pulse_reels VALUES(2,20,2,'Secret Reel','Community','now',0,0,0,'active','approved');
        INSERT INTO pulse_comments VALUES(1,10,2,'Looks ready','now',NULL);
        INSERT INTO pulse_statuses VALUES(1,1,'text','Owner status','public','now','2099-01-01',NULL);
        INSERT INTO pulse_statuses VALUES(2,2,'text','Private status','private','now','2099-01-01',NULL);
        INSERT INTO pulse_status_views VALUES(1,1,2,'now',1.0);
        INSERT INTO pulse_status_reactions VALUES(1,1,2,'love','now');
        """)
        self.patch = patch.object(graph.db_service, "connect", side_effect=lambda: _ConnectionProxy(self.conn))
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.conn.close()

    def test_reels_enforce_visibility_and_owner_analytics(self) -> None:
        self.assertEqual([row["reel_id"] for row in graph.list_reels(1)], [1])
        self.assertIsNone(graph.get_reel(1, 2))
        self.assertEqual(graph.reel_performance(1, 1)["shares"], 3)
        self.assertIsNone(graph.reel_performance(1, 2))
        self.assertIn("Looks ready", graph.reel_comment_summary(1, 1)["summary"])

    def test_reel_edges_are_explicit_idempotent_and_readable(self) -> None:
        self.assertTrue(graph.set_reel_saved(1, 1, saved=True)["changed"])
        self.assertFalse(graph.set_reel_saved(1, 1, saved=True)["changed"])
        self.assertTrue(graph.get_reel(1, 1)["saved"])
        self.assertTrue(graph.set_reel_liked(1, 1, liked=True)["changed"])
        self.assertFalse(graph.set_reel_liked(1, 1, liked=True)["changed"])
        self.assertTrue(graph.get_reel(1, 1)["liked"])

    def test_status_analytics_are_owner_only(self) -> None:
        self.assertEqual([row["status_id"] for row in graph.list_statuses(1)], [1])
        self.assertIsNone(graph.get_status(1, 2))
        self.assertEqual(graph.status_viewer_summary(1, 1)["viewer_count"], 1)
        self.assertEqual(graph.status_reaction_summary(1, 1)["reaction_counts"], {"love": 1})

    def test_profile_privacy_and_summaries(self) -> None:
        self.assertEqual(graph.get_profile(1)["username"], "owner")
        self.assertIsNone(graph.get_profile(1, 2))
        self.assertEqual(graph.profile_activity_summary(1)["reels"], 1)
        self.assertEqual(graph.profile_relationship_summary(1), {
            "user_id": 1, "followers": 0, "following": 0, "source_url": "/pulse/profile/1",
        })

    def test_natural_commands_select_content_graph_capabilities(self) -> None:
        commands = {
            "Find my recent reels": "reels.search",
            "Explain reel 1": "reels.get",
            "How did my reel 1 perform": "reels.performance.summary",
            "Summarize reel comments": "reels.comments.summary",
            "Save reel 1": "reels.save",
            "Unsave reel 1": "reels.unsave",
            "Like reel 1": "reels.like",
            "Unlike reel 1": "reels.unlike",
            "Show active statuses": "status.list",
            "Show status 1": "status.get",
            "Who viewed my status 1": "status.viewer.summary",
            "How did my status 1 perform": "status.reaction.summary",
            "Summarize my account": "profile.get",
            "Show my recent activity": "profile.activity.summary",
            "How many followers do I have": "profile.relationship.summary",
            "Set my preferred language to Spanish": "profile.preferences.update",
        }
        for command, expected in commands.items():
            with self.subTest(command=command):
                self.assertEqual(match_capability(command).capability_id, expected)

    def test_profile_preference_write_is_bounded_and_readable(self) -> None:
        self.assertTrue(graph.update_profile_preferences(1, preferred_language="es")["changed"])
        self.assertEqual(graph.get_profile_preferences(1)["preferred_language"], "es")
        self.assertFalse(graph.update_profile_preferences(1, preferred_language="es")["changed"])
        self.assertEqual(graph.update_profile_preferences(1, preferred_language="de")["error"],
                         "unsupported_language")


if __name__ == "__main__":
    unittest.main()
