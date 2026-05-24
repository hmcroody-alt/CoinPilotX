#!/usr/bin/env python3
"""End-to-end audit for global Pulse feed visibility, media delivery, and adaptive rendering."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import pulse_feed_engine  # noqa: E402


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c636000000200015d0a2db40000000049454e44ae426082"
)


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_users():
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    users = [
        (930001, "pulse_global_a", "Pulse Global A", "pulse-global-a@example.test", "PGA"),
        (930002, "pulse_global_b", "Pulse Global B", "pulse-global-b@example.test", "PGB"),
        (930003, "pulse_global_c", "Pulse Global C", "pulse-global-c@example.test", "PGC"),
    ]
    for user_id, username, display_name, email, public_id in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE users SET username=?, display_name=?, email=? WHERE user_id=?", (username, display_name, email, user_id))
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
                (user_id, username, display_name, email, now()),
            )
        cur.execute("SELECT id FROM arena_profiles WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE arena_profiles SET public_player_id=? WHERE user_id=?", (public_id, user_id))
        else:
            cur.execute(
                "INSERT INTO arena_profiles (user_id, public_player_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, public_id, now(), now()),
            )
    conn.commit()
    conn.close()
    return [u[0] for u in users], users[0][4]


def write_media_file(name, content=PNG_1X1):
    media_dir = ROOT / "static" / "uploads" / "pulse_global_audit"
    media_dir.mkdir(parents=True, exist_ok=True)
    path = media_dir / name
    path.write_bytes(content)
    return path, f"/static/uploads/pulse_global_audit/{name}"


def insert_media(user_id, name, media_type="image", mime="image/png", width=1080, height=1080, thumbnail_url=None):
    content = PNG_1X1 if media_type in {"image", "gif"} else b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    path, url = write_media_file(name, content)
    thumb = thumbnail_url if thumbnail_url is not None else (url if media_type != "video" else "")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, context_type, context_id, original_filename, stored_filename, media_url, thumbnail_url,
         media_type, mime_type, file_size_bytes, width, height, moderation_status, created_at)
        VALUES (?, 'pulse', 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)
        """,
        (int(user_id), name, name, url, thumb, media_type, mime, path.stat().st_size, width, height, now()),
    )
    media_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return media_id, url


def feed_contains(user_id, post_id, feed="for_you"):
    payload = pulse_feed_engine.list_feed(viewer_user_id=user_id, feed=feed, limit=40, offset=0)
    return any(int(post.get("id") or 0) == int(post_id) for post in payload.get("posts") or [])


def api_feed_contains(client, user_id, post_id, extra=""):
    with client.session_transaction() as sess:
        sess["account_user_id"] = int(user_id)
    response = client.get(f"/api/pulse/feed?limit=40{extra}")
    expect(response.status_code == 200, f"API feed responds for user {user_id}", response.get_data(as_text=True)[:300])
    payload = response.get_json() or {}
    return any(int(post.get("id") or 0) == int(post_id) for post in payload.get("posts") or [])


def create_public_post(user_id, media_id, title, body, post_type="image"):
    result = pulse_feed_engine.create_post(
        user_id,
        body=body,
        post_type=post_type,
        title=title,
        visibility="public",
        media_ids=[media_id],
        enqueue_background=False,
    )
    expect(result.get("ok") is True and int(result.get("post_id") or 0) > 0, f"create {title}", str(result))
    return int(result["post_id"])


def delete_post(post_id):
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("UPDATE pulse_posts SET deleted_at=?, updated_at=? WHERE id=?", (now(), now(), int(post_id)))
    conn.commit()
    conn.close()


def main():
    bot.init_db()
    user_ids, author_public_id = ensure_users()
    user_a, user_b, user_c = user_ids
    client = bot.webhook_app.test_client()

    landscape_media, landscape_url = insert_media(user_a, "landscape.png", width=1600, height=900)
    portrait_media, portrait_url = insert_media(user_a, "portrait.png", width=900, height=1600)
    square_media, square_url = insert_media(user_a, "square.png", width=1200, height=1200)
    video_media, video_url = insert_media(user_a, "video.mp4", media_type="video", mime="video/mp4", width=1080, height=1920)

    public_post = create_public_post(user_a, portrait_media, "Pulse Global Audit Portrait", "Public approved portrait Pulse media")
    landscape_post = create_public_post(user_a, landscape_media, "Pulse Global Audit Landscape", "Public approved landscape Pulse media")
    square_post = create_public_post(user_a, square_media, "Pulse Global Audit Square", "Public approved square Pulse media")
    video_post = create_public_post(user_a, video_media, "Pulse Global Audit Video", "Public approved video Pulse media", post_type="video")

    for user_id in user_ids:
        expect(feed_contains(user_id, public_post), f"user {user_id} sees public approved post")
        expect(api_feed_contains(client, user_id, public_post), f"pull refresh/API feed sees public post for user {user_id}")
        expect(api_feed_contains(client, user_id, public_post, "&offset=0"), f"new pulse counter path uses same feed for user {user_id}")

    profile_payload = pulse_feed_engine.list_feed(viewer_user_id=user_b, feed="for_you", profile_public_player_id=author_public_id, limit=40, offset=0)
    expect(any(int(p.get("id") or 0) == public_post for p in profile_payload.get("posts") or []), "profile feed uses canonical visibility")

    for post_id, url, expected_orientation in [
        (public_post, portrait_url, "portrait"),
        (landscape_post, landscape_url, "landscape"),
        (square_post, square_url, "square"),
    ]:
        hydrated = pulse_feed_engine.get_post(post_id, viewer_user_id=user_c)
        media = (hydrated or {}).get("media") or []
        expect(media and media[0].get("media_url") == url, f"media hydrates for post {post_id}", json.dumps(media))
        expect(bot.pulse_media_exists(media[0].get("media_url")), f"media file exists for {post_id}", media[0].get("media_url"))
        expect(media[0].get("orientation") == expected_orientation, f"{expected_orientation} adaptive metadata present", json.dumps(media[0]))
        expect(media[0].get("fit_mode") == "smart", "smart fit metadata present", json.dumps(media[0]))

    video = pulse_feed_engine.get_post(video_post, viewer_user_id=user_b)
    video_media_payload = ((video or {}).get("media") or [{}])[0]
    expect(video_media_payload.get("media_type") == "video", "video post carries video media metadata", json.dumps(video_media_payload))
    expect(bot.pulse_media_exists(video_media_payload.get("media_url")), "video media URL resolves locally", video_url)

    delete_post(public_post)
    for user_id in user_ids:
        expect(not feed_contains(user_id, public_post), f"deleted post hidden from service feed for user {user_id}")
        expect(not api_feed_contains(client, user_id, public_post), f"deleted post hidden from API feed for user {user_id}")
    explanation = pulse_feed_engine.explain_visibility(public_post, viewer_user_id=user_b)
    expect(explanation.get("visible") is False and explanation.get("reason") == "deleted", "deleted post visibility explanation")

    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token in [
        "Media could not load.",
        "object-fit:contain",
        "data-fit=\"smart\"",
        "loading=\"lazy\"",
        "decoding=\"async\"",
        "pulse-media-wrap",
        "frameMode='fit'",
    ]:
        expect(token in bot_source, f"mobile/PWA rendering rule present: {token}")

    print("pulse global feed audit ok")


if __name__ == "__main__":
    main()
