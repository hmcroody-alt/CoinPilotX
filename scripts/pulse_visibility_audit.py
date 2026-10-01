#!/usr/bin/env python3
"""Focused smoke test for Pulse feed visibility and media hydration."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import pulse_feed_engine  # noqa: E402


def _expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def _ensure_users():
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users = [
        (920001, "pulse_visibility_a", "Pulse Visibility A", "pulse-visibility-a@example.test"),
        (920002, "pulse_visibility_b", "Pulse Visibility B", "pulse-visibility-b@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE users SET username=?, display_name=?, email=? WHERE user_id=?", (username, display_name, email, user_id))
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
                (user_id, username, display_name, email, now),
            )
    conn.commit()
    conn.close()
    return users[0][0], users[1][0]


def _feed_contains(user_id, post_id):
    feed = pulse_feed_engine.list_feed(viewer_user_id=user_id, feed="for_you", limit=40, offset=0)
    return any(int(post.get("id") or 0) == int(post_id) for post in feed.get("posts") or [])


def _create_media(user_id):
    media_dir = ROOT / "static" / "uploads" / "pulse_audit"
    media_dir.mkdir(parents=True, exist_ok=True)
    path = media_dir / "pulse_visibility_audit.png"
    if not path.exists():
        path.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c636000000200015d0a2db40000000049454e44ae426082"
            )
        )
    url = "/static/uploads/pulse_audit/pulse_visibility_audit.png"
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, context_type, context_id, original_filename, stored_filename, media_url, thumbnail_url,
         media_type, mime_type, file_size_bytes, moderation_status, created_at)
        VALUES (?, 'pulse', 'draft', 'pulse_visibility_audit.png', 'pulse_visibility_audit.png', ?, ?, 'image', 'image/png', ?, 'approved', ?)
        """,
        (int(user_id), url, url, path.stat().st_size, now),
    )
    media_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return media_id, url


def main():
    bot.init_db()
    user_a, user_b = _ensure_users()
    media_id, media_url = _create_media(user_a)
    result = pulse_feed_engine.create_post(
        user_a,
        body="Pulse visibility audit public approved media post",
        post_type="image",
        title="Visibility Audit",
        visibility="public",
        media_ids=[media_id],
        enqueue_background=False,
    )
    _expect(result.get("ok") is True and int(result.get("post_id") or 0) > 0, "create public approved pulse", str(result))
    post_id = int(result["post_id"])
    _expect(_feed_contains(user_a, post_id), "author sees public approved pulse")
    _expect(_feed_contains(user_b, post_id), "other eligible user sees public approved pulse")
    hydrated = pulse_feed_engine.get_post(post_id, viewer_user_id=user_b)
    _expect(hydrated and hydrated.get("media") and hydrated["media"][0].get("media_url") == media_url, "public media hydrates for other user", str(hydrated))
    explanation = pulse_feed_engine.explain_visibility(post_id, viewer_user_id=user_b)
    _expect(explanation.get("visible") is True and explanation.get("reason") == "public_approved", "visibility explanation reports visible", str(explanation))

    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute("UPDATE pulse_posts SET deleted_at=?, updated_at=? WHERE id=?", (now, now, post_id))
    conn.commit()
    conn.close()
    _expect(not _feed_contains(user_a, post_id), "deleted pulse hidden from author feed")
    _expect(not _feed_contains(user_b, post_id), "deleted pulse hidden from other user feed")
    explanation = pulse_feed_engine.explain_visibility(post_id, viewer_user_id=user_b)
    _expect(explanation.get("visible") is False and explanation.get("reason") == "deleted", "deleted visibility explanation", str(explanation))

    private_result = pulse_feed_engine.create_post(
        user_a,
        body="Pulse visibility audit private post",
        post_type="text",
        title="Private Visibility Audit",
        visibility="private",
        enqueue_background=False,
    )
    private_post_id = int(private_result.get("post_id") or 0)
    _expect(private_post_id > 0, "create private pulse", str(private_result))
    _expect(not _feed_contains(user_b, private_post_id), "private pulse excluded from public feed")
    print("pulse visibility audit ok")


if __name__ == "__main__":
    main()
