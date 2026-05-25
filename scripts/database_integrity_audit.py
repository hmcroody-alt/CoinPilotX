#!/usr/bin/env python3
"""Audit Pulse database schema, migrations, and critical indexes."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


REQUIRED_TABLES = {
    "pulse_posts",
    "chat_media_uploads",
    "pulse_conversations",
    "pulse_conversation_participants",
    "pulse_messages",
    "pulse_chat_rooms",
    "pulse_status",
    "pulse_status_media",
    "pulse_reels",
    "pulse_live_sessions",
    "pulse_live_streams",
    "pulse_live_chat",
    "pulse_live_destinations",
    "pulse_live_restream_targets",
    "pulse_live_archive_shares",
    "pulse_live_scene_presets",
    "pulse_live_audio_profiles",
}

REQUIRED_COLUMNS = {
    "pulse_posts": {"deleted_at", "moderation_status", "visibility", "live_session_id", "live_status", "playback_url", "preview_url", "replay_url"},
    "pulse_live_sessions": {"feed_post_id", "playback_url", "preview_url", "replay_url", "publish_state", "audio_tracks", "video_tracks", "recording_status", "active_scene", "audio_chain_json", "destinations_json"},
    "pulse_messages": {"conversation_id", "sender_user_id", "body", "message_type", "created_at", "deleted_at"},
    "chat_media_uploads": {"media_url", "storage_provider", "storage_key", "mime_type", "width", "height", "thumbnail_url", "is_available"},
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def table_columns(cur, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cur.fetchall()}


def main():
    bot.init_db()
    bot.init_db()
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cur.fetchall()}
    missing = sorted(REQUIRED_TABLES - tables)
    require(not missing, f"required Pulse tables exist ({', '.join(missing) if missing else 'none missing'})")
    for table, columns in REQUIRED_COLUMNS.items():
        existing = table_columns(cur, table)
        absent = sorted(columns - existing)
        require(not absent, f"{table} required columns exist ({', '.join(absent) if absent else 'none missing'})")
    cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = {row["name"] for row in cur.fetchall()}
    expected_index_fragments = ["pulse_live_sessions", "pulse_live_restream", "pulse_live_audio", "pulse_live_scene", "pulse_messages", "pulse_posts"]
    for fragment in expected_index_fragments:
        require(any(fragment in index for index in indexes), f"index coverage exists for {fragment}")
    conn.close()
    print("database integrity audit ok")


if __name__ == "__main__":
    main()
