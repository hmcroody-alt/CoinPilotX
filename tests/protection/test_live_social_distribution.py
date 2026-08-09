import sqlite3
from pathlib import Path

from services import live_feed_service


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
FEED = (ROOT / "services" / "pulse_feed_engine.py").read_text(encoding="utf-8")


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE arena_profiles (user_id INTEGER, public_player_id TEXT);
        CREATE TABLE pulse_posts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, public_player_id TEXT,
          post_type TEXT, body TEXT, media_ids_json TEXT, title TEXT, tags_json TEXT,
          visibility TEXT, moderation_status TEXT, ai_summary TEXT, ai_tags_json TEXT,
          sentiment TEXT, risk_score INTEGER, engagement_score REAL,
          live_session_id INTEGER, live_status TEXT, live_viewer_count INTEGER,
          playback_url TEXT, preview_url TEXT, replay_url TEXT, status TEXT,
          deleted_at TEXT, created_at TEXT, updated_at TEXT
        );
        INSERT INTO arena_profiles(user_id, public_player_id) VALUES (7, 'creator-7');
        """
    )
    return conn


def test_one_live_feed_post_transitions_to_processing_then_replay():
    conn = _db()
    cur = conn.cursor()
    first = live_feed_service.ensure_live_feed_post(
        cur, user_id=7, live_id=41, title="Sunday conversation", category="Community",
        display_name="John Doe", visibility="followers",
    )
    second = live_feed_service.ensure_live_feed_post(
        cur, user_id=7, live_id=41, title="Sunday conversation", category="Community",
        display_name="John Doe", visibility="followers",
    )
    assert first == second
    assert cur.execute("SELECT COUNT(*) FROM pulse_posts WHERE live_session_id=41").fetchone()[0] == 1
    row = dict(cur.execute("SELECT * FROM pulse_posts WHERE id=?", (first,)).fetchone())
    assert row["body"] == "John Doe is LIVE now"
    assert row["live_status"] == "starting"
    assert row["visibility"] == "followers"

    live_feed_service.mark_live_feed_ended(cur, live_id=41)
    row = dict(cur.execute("SELECT * FROM pulse_posts WHERE id=?", (first,)).fetchone())
    assert row["live_status"] == "processing"
    assert not row["replay_url"]

    live_feed_service.mark_live_feed_replay_ready(
        cur, live_id=41, playback_url="https://stream.mux.com/replay.m3u8", preview_url="https://image.mux.com/replay.jpg",
    )
    row = dict(cur.execute("SELECT * FROM pulse_posts WHERE id=?", (first,)).fetchone())
    assert row["live_status"] == "archived"
    assert row["replay_url"].endswith("replay.m3u8")
    assert cur.execute("SELECT COUNT(*) FROM pulse_posts WHERE live_session_id=41").fetchone()[0] == 1


def test_replay_reuses_feed_post_and_feed_serializes_mux_media():
    creator = BOT[BOT.index("def pulse_live_publish_replay_reel"):BOT.index("def api_pulse_live_end")]
    assert "pulse_feed_engine.create_post" not in creator
    assert 'post_id = safe_int(live.get("feed_post_id"), 0)' in creator
    assert "mark_live_feed_replay_ready" in creator
    assert "ON CONFLICT(post_id)" in creator
    assert '"reason": "replay_post_deleted"' in creator
    assert '"content_type": "live" if live_session_id' in FEED
    assert 'f"live-replay-{live_session_id}"' in FEED


def test_live_authorization_is_applied_to_feed_and_token_paths():
    token = BOT[BOT.index("def api_pulse_live_agora_token"):BOT.index("def api_pulse_live_rtc_token")]
    assert "pulse_live_viewer_authorized" in token
    assert "This Live is not available to this account." in token
    assert "publishMicrophoneTrack" not in token
    assert "publishCameraTrack" not in token
    assert "blocked_users" in FEED
    assert "pfl.follower_user_id" in FEED
    assert 'return True, "approved_guest"' in BOT
    assert "SELECT 1 FROM pulse_follows WHERE follower_user_id=? AND followed_user_id=? LIMIT 1" in FEED
