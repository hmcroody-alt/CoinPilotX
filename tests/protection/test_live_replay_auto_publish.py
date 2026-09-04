"""An ended livestream must become normal, reusable PulseSoc content.

One canonical identity -- a single ``pulse_posts`` row with ``post_type='live'`` plus the
``pulse_reels`` row that shares its ``post_id`` -- has to surface on all three consumer
surfaces: Reels, Home feed, and the creator's profile Media tab. It previously surfaced on
only one of them, because the replay carries its video on ``replay_url``/``playback_url``
rather than as a ``chat_media_uploads`` asset, and the Reels and profile-media SQL
predicates both keyed off ``media_ids_json``. These tests pin the corrected contract.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import pulse_feed_engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

REPLAY_URL = "https://stream.mux.com/replay-abc.m3u8"
POSTER_URL = "https://image.mux.com/replay-abc/thumbnail.jpg"


class FakeUserContext:
    def __init__(self, path):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class LiveReplayAutoPublishTest(unittest.TestCase):
    VIEWER = 11
    CREATOR = 902
    OTHER_CREATOR = 903

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db", prefix="pulse_live_replay_")
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, pulse_id TEXT, username TEXT, email TEXT,
                full_name TEXT, display_name TEXT, avatar_url TEXT, plan TEXT,
                subscription_plan TEXT, subscription_status TEXT, is_pro INTEGER,
                pro_active INTEGER, pro_expires_at TEXT, subscription_expires_at TEXT,
                premium_status TEXT, premium_expires_at TEXT, lifetime_premium INTEGER,
                premium_glow_manual_grant INTEGER, premium_mark_override TEXT,
                premium_mark_type TEXT, hidden_from_discovery INTEGER DEFAULT 0,
                shadow_banned INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
                account_status TEXT DEFAULT 'active'
            )
            """
        )
        cur.execute("CREATE TABLE arena_profiles (user_id INTEGER PRIMARY KEY, avatar_url TEXT, public_player_id TEXT)")
        cur.execute(
            """
            CREATE TABLE pulse_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, public_player_id TEXT,
                post_type TEXT, body TEXT, media_ids_json TEXT, title TEXT, tags_json TEXT,
                visibility TEXT, moderation_status TEXT, ai_summary TEXT, ai_tags_json TEXT,
                sentiment TEXT, risk_score INTEGER, engagement_score REAL,
                live_session_id INTEGER, live_status TEXT, live_viewer_count INTEGER,
                playback_url TEXT, preview_url TEXT, replay_url TEXT,
                status TEXT, deleted_at TEXT, created_at TEXT, updated_at TEXT,
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
        cur.execute("CREATE TABLE pulse_post_hides (user_id INTEGER, post_id INTEGER, reason TEXT, created_at TEXT)")
        cur.execute(
            "CREATE TABLE pulse_user_mutes (user_id INTEGER, muted_user_id INTEGER, muted_until TEXT, created_at TEXT)"
        )
        cur.execute(
            "CREATE TABLE pulse_reels (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER UNIQUE, "
            "user_id INTEGER, video_url TEXT, thumbnail_url TEXT, status TEXT, source_live_id INTEGER)"
        )
        for uid, pulse_id, username, name in (
            (self.VIEWER, "PLS-000011", "viewer", "Viewer"),
            (self.CREATOR, "PLS-000902", "maria", "Maria"),
            (self.OTHER_CREATOR, "PLS-000903", "sam", "Sam"),
        ):
            cur.execute(
                "INSERT INTO users (user_id, pulse_id, username, display_name) VALUES (?,?,?,?)",
                (uid, pulse_id, username, name),
            )
        conn.commit()
        conn.close()
        self.real_context = pulse_feed_engine.user_context
        pulse_feed_engine.user_context = FakeUserContext(self.db_path)

    def tearDown(self):
        pulse_feed_engine.user_context = self.real_context
        os.unlink(self.db_path)

    # -- helpers -----------------------------------------------------------------

    def _insert_post(self, **over):
        row = {
            "user_id": self.CREATOR,
            "post_type": "live",
            "body": "Maria is LIVE now",
            "media_ids_json": "[]",
            "title": "Sunday session",
            "tags_json": '["live","pulse-live"]',
            "visibility": "public",
            "moderation_status": "approved",
            "live_session_id": 41,
            "live_status": "archived",
            "playback_url": REPLAY_URL,
            "preview_url": POSTER_URL,
            "replay_url": REPLAY_URL,
            "status": "published",
            "deleted_at": None,
            "created_at": "2026-09-01T10:00:00",
            "updated_at": "2026-09-01T10:00:00",
        }
        row.update(over)
        cols = ",".join(row)
        marks = ",".join("?" * len(row))
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO pulse_posts ({cols}) VALUES ({marks})", tuple(row.values()))
        post_id = cur.lastrowid
        cur.execute(
            "INSERT INTO pulse_reels (post_id, user_id, video_url, thumbnail_url, status, source_live_id) "
            "VALUES (?,?,?,?,'active',?)",
            (post_id, row["user_id"], row["playback_url"], row["preview_url"], row["live_session_id"]),
        )
        conn.commit()
        conn.close()
        return post_id

    def _reels_ids(self, viewer=None):
        result = pulse_feed_engine.list_feed(
            viewer if viewer is not None else self.VIEWER, "reels", limit=50
        )
        return [int(p["id"]) for p in (result.get("posts") or [])]

    def _home_ids(self, viewer=None):
        result = pulse_feed_engine.list_feed(
            viewer if viewer is not None else self.VIEWER, "for_you", limit=50
        )
        return [int(p["id"]) for p in (result.get("posts") or [])]

    def _profile_media(self, target, viewer):
        result = pulse_feed_engine.list_user_posts(target, viewer_user_id=viewer, limit=50)
        posts = result.get("posts") or []
        return [p for p in posts if p.get("media") or p.get("media_assets") or p.get("attachments")]

    # -- the three surfaces ------------------------------------------------------

    def test_archived_replay_appears_in_reels_feed_and_profile_media(self):
        post_id = self._insert_post()

        self.assertIn(post_id, self._reels_ids(), "replay missing from the Reels lane")
        self.assertIn(post_id, self._home_ids(), "replay missing from the Home feed")

        media_posts = self._profile_media(self.CREATOR, self.VIEWER)
        self.assertEqual([int(p["id"]) for p in media_posts], [post_id])
        self.assertEqual(
            pulse_feed_engine.count_user_posts(self.CREATOR, viewer_user_id=self.VIEWER, media_only=True),
            1,
            "profile Media badge must agree with the Media listing",
        )

    def test_all_three_surfaces_reference_one_canonical_identity(self):
        post_id = self._insert_post()

        reels = self._reels_ids()
        home = self._home_ids()
        profile = [int(p["id"]) for p in self._profile_media(self.CREATOR, self.VIEWER)]

        self.assertEqual({post_id}, set(reels) & set(home) & set(profile))
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM pulse_posts WHERE live_session_id=41").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM pulse_reels WHERE post_id=?", (post_id,)).fetchone()[0], 1)
        conn.close()

    def test_replay_carries_the_real_recording_url_and_thumbnail(self):
        post_id = self._insert_post()
        media = self._profile_media(self.CREATOR, self.VIEWER)[0]["media"][0]

        self.assertEqual(media["media_url"], REPLAY_URL)
        self.assertEqual(media["thumbnail_url"], POSTER_URL)
        self.assertEqual(media["mime_type"], "application/vnd.apple.mpegurl")
        self.assertEqual(media["id"], "live-replay-41")
        self.assertTrue(media["is_available"])
        self.assertEqual(post_id, int(self._profile_media(self.CREATOR, self.VIEWER)[0]["id"]))

    # -- states that must NOT publish --------------------------------------------

    def test_live_still_in_progress_is_not_offered_as_a_reel(self):
        post_id = self._insert_post(live_status="starting", replay_url=None, playback_url="")
        self.assertNotIn(post_id, self._reels_ids())
        self.assertEqual(
            pulse_feed_engine.count_user_posts(self.CREATOR, viewer_user_id=self.VIEWER, media_only=True), 0
        )

    def test_processing_recording_is_not_offered_as_a_reel(self):
        post_id = self._insert_post(live_status="processing", replay_url=None, playback_url="")
        self.assertNotIn(post_id, self._reels_ids())

    def test_ended_live_without_a_recording_url_is_never_published_as_media(self):
        post_id = self._insert_post(live_status="archived", replay_url="", playback_url="")
        self.assertNotIn(post_id, self._reels_ids(), "a zero-recording live must not become a reel")
        self.assertEqual(self._profile_media(self.CREATOR, self.VIEWER), [])
        self.assertEqual(
            pulse_feed_engine.count_user_posts(self.CREATOR, viewer_user_id=self.VIEWER, media_only=True), 0
        )

    # -- privacy, ownership, deletion --------------------------------------------

    def test_private_live_does_not_become_public_after_ending(self):
        post_id = self._insert_post(visibility="private")
        self.assertNotIn(post_id, self._reels_ids())
        self.assertEqual(self._profile_media(self.CREATOR, self.VIEWER), [])
        self.assertEqual(
            pulse_feed_engine.count_user_posts(self.CREATOR, viewer_user_id=self.VIEWER, media_only=True), 0
        )
        # The owner still sees their own archive.
        self.assertEqual([int(p["id"]) for p in self._profile_media(self.CREATOR, self.CREATOR)], [post_id])

    def test_followers_only_replay_is_not_shown_to_a_stranger_in_reels(self):
        post_id = self._insert_post(visibility="followers")
        self.assertNotIn(post_id, self._reels_ids())

    def test_profile_media_belongs_to_the_viewed_creator_not_the_viewer(self):
        maria_post = self._insert_post(user_id=self.CREATOR, live_session_id=41)
        sam_post = self._insert_post(user_id=self.OTHER_CREATOR, live_session_id=42)

        viewed_as_sam = [int(p["id"]) for p in self._profile_media(self.CREATOR, self.OTHER_CREATOR)]
        self.assertEqual(viewed_as_sam, [maria_post])
        self.assertNotIn(sam_post, viewed_as_sam)
        self.assertEqual(
            pulse_feed_engine.count_user_posts(self.CREATOR, viewer_user_id=self.OTHER_CREATOR, media_only=True), 1
        )

    def test_deleting_the_replay_leaves_no_orphan_on_any_surface(self):
        post_id = self._insert_post()
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE pulse_posts SET deleted_at=?, status='deleted' WHERE id=?", ("2026-09-02T00:00:00", post_id))
        conn.commit()
        conn.close()

        self.assertNotIn(post_id, self._reels_ids())
        self.assertNotIn(post_id, self._home_ids())
        self.assertEqual(self._profile_media(self.CREATOR, self.VIEWER), [])
        self.assertEqual(
            pulse_feed_engine.count_user_posts(self.CREATOR, viewer_user_id=self.VIEWER, media_only=True), 0
        )


class LiveReplayPublisherContractTest(unittest.TestCase):
    """The publisher itself is a single named function; these pin its guarantees."""

    def setUp(self):
        self.publisher = BOT[
            BOT.index("def pulse_live_publish_replay_reel") : BOT.index("def api_pulse_live_end")
        ]

    def test_publish_is_idempotent_on_the_live_session_claim(self):
        # The reel id is claimed with a conditional UPDATE, so a retried finalize callback
        # or a duplicate Mux webhook loses the race rather than creating a second reel.
        self.assertIn("UPDATE pulse_live_sessions", self.publisher)
        self.assertIn("replay_reel_id", self.publisher)
        self.assertIn("COALESCE(replay_reel_id,0)=0", self.publisher)
        self.assertIn('"created": False', self.publisher)
        self.assertIn("ON CONFLICT(post_id)", self.publisher)

    def test_publish_refuses_unfinished_unplayable_or_unmoderated_recordings(self):
        self.assertIn('"reason": "live_not_ended"', self.publisher)
        self.assertIn('"reason": "replay_not_playable"', self.publisher)
        self.assertIn("reel_media_source_is_playable", self.publisher)
        self.assertIn('"reason": "replay_blocked_by_moderation"', self.publisher)

    def test_publish_does_not_resurrect_a_deleted_replay(self):
        self.assertIn('"reason": "replay_reel_deleted"', self.publisher)
        self.assertIn('"reason": "replay_post_deleted"', self.publisher)

    def test_publish_is_fail_closed_on_visibility(self):
        self.assertIn('if visibility not in {"public", "followers", "private"}', self.publisher)
        self.assertIn('visibility = "private"', self.publisher)

    def test_publish_reuses_the_existing_post_and_reel_tables(self):
        # No parallel replay storage: one pulse_posts row, one pulse_reels row.
        self.assertIn("ensure_live_feed_post", self.publisher)
        self.assertIn("INSERT INTO pulse_reels", self.publisher)
        self.assertNotIn("CREATE TABLE", self.publisher)

    def test_publisher_never_touches_the_realtime_audio_session(self):
        for forbidden in ("setAudioModeAsync", "AVAudioSession", "setCategory"):
            self.assertNotIn(forbidden, self.publisher)


if __name__ == "__main__":
    unittest.main()
