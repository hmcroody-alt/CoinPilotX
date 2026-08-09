import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import pulse_feed_engine  # noqa: E402


class FakeUserContext:
    def __init__(self, path):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class PulseNewUserProfileFlowTest(unittest.TestCase):
    VIEWER = 11
    NEW_USER = 902

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db", prefix="pulse_new_user_profile_")
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                email TEXT,
                full_name TEXT,
                display_name TEXT,
                avatar_url TEXT,
                plan TEXT,
                subscription_plan TEXT,
                subscription_status TEXT,
                is_pro INTEGER,
                pro_active INTEGER,
                pro_expires_at TEXT,
                subscription_expires_at TEXT,
                premium_status TEXT,
                premium_expires_at TEXT,
                lifetime_premium INTEGER,
                premium_glow_manual_grant INTEGER,
                premium_mark_override TEXT,
                premium_mark_type TEXT
            )
            """
        )
        cur.execute("CREATE TABLE arena_profiles (user_id INTEGER PRIMARY KEY, avatar_url TEXT, public_player_id TEXT)")
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
                visibility TEXT,
                moderation_status TEXT,
                ai_summary TEXT,
                ai_tags_json TEXT,
                sentiment TEXT,
                risk_score INTEGER,
                engagement_score REAL,
                status TEXT,
                deleted_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                repost_of_post_id INTEGER
            )
            """
        )
        cur.execute("CREATE TABLE pulse_reactions (post_id INTEGER, user_id INTEGER, reaction_type TEXT)")
        cur.execute("CREATE TABLE pulse_comments (post_id INTEGER, deleted_at TEXT, moderation_status TEXT)")
        cur.execute("CREATE TABLE pulse_post_views (post_id INTEGER)")
        cur.execute("CREATE TABLE pulse_post_saves (user_id INTEGER, post_id INTEGER)")
        cur.execute("CREATE TABLE pulse_follows (follower_user_id INTEGER, followed_user_id INTEGER)")
        cur.execute("CREATE TABLE pulse_friends (user_id INTEGER, friend_user_id INTEGER, status TEXT)")
        cur.execute("CREATE TABLE blocked_users (blocker_user_id INTEGER, blocked_user_id INTEGER)")
        cur.execute("INSERT INTO users (user_id, username, display_name) VALUES (?,?,?)", (self.VIEWER, "viewer", "Viewer"))
        cur.execute("INSERT INTO users (user_id, username, display_name) VALUES (?,?,?)", (self.NEW_USER, "fresh902", "Fresh Member"))
        cur.execute(
            """
            INSERT INTO pulse_posts
              (user_id, public_player_id, post_type, body, media_ids_json, title, tags_json, visibility,
               moderation_status, ai_summary, ai_tags_json, sentiment, risk_score, engagement_score, status, created_at, updated_at)
            VALUES (?, NULL, 'text', 'first canonical post', '[]', '', '[]', 'public',
                    'approved', '', '[]', 'neutral', 0, 0, 'published', '2026-08-08T01:00:00', '2026-08-08T01:00:00')
            """,
            (self.NEW_USER,),
        )
        conn.commit()
        conn.close()
        self.real_context = pulse_feed_engine.user_context
        pulse_feed_engine.user_context = FakeUserContext(self.db_path)

    def tearDown(self):
        pulse_feed_engine.user_context = self.real_context
        os.unlink(self.db_path)

    def test_numeric_profile_key_returns_new_user_posts_without_legacy_profile(self):
        result = pulse_feed_engine.list_feed(self.VIEWER, "for_you", profile_public_player_id=str(self.NEW_USER))

        self.assertEqual(len(result["posts"]), 1)
        post = result["posts"][0]
        self.assertEqual(post["user_id"], self.NEW_USER)
        self.assertEqual(post["author"]["user_id"], self.NEW_USER)
        self.assertEqual(post["author"]["id"], self.NEW_USER)
        self.assertEqual(post["author"]["profile_url"], f"/pulse/id/{self.NEW_USER}")
        self.assertEqual(post["body"], "first canonical post")

    def test_username_profile_key_uses_same_author_identity(self):
        result = pulse_feed_engine.list_feed(self.VIEWER, "for_you", profile_public_player_id="fresh902")

        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(result["posts"][0]["author"]["user_id"], self.NEW_USER)

    def test_profile_post_listing_uses_same_user_id_as_profile_count(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pulse_posts WHERE user_id=? AND deleted_at IS NULL", (self.NEW_USER,))
        profile_count = int(cur.fetchone()[0])
        conn.close()

        result = pulse_feed_engine.list_user_posts(self.NEW_USER, viewer_user_id=self.VIEWER)

        self.assertEqual(profile_count, 1)
        self.assertEqual(len(result["posts"]), profile_count)
        self.assertEqual(result["posts"][0]["user_id"], self.NEW_USER)

    def test_native_profile_posts_route_is_not_shadowed_by_profile_catchall(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py"), "r", encoding="utf-8") as source:
            text = source.read()

        posts_route = text.index('@webhook_app.route("/api/pulse/profile/<path:profile_key>/posts", methods=["GET"])')
        public_route = text.index('@webhook_app.route("/api/pulse/profile/<path:profile_key>", methods=["GET"])')

        self.assertLess(posts_route, public_route)


if __name__ == "__main__":
    unittest.main()
