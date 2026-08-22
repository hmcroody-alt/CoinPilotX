"""PulseSoc Insight posts are text-only, and cleaning up the old ones is safe.

Two separate guarantees are covered here:

* the generator never produces media again, and
* detaching media from the historical posts does not cost the product anything
  it cannot get back -- no post, comment, reaction or user upload.

The user-media cases are not incidental. The rule being enforced is specific to
the automated account, so every guard is paired with a real user's post proving
the same code left it alone.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from services.pulse_ai import automated_image_pipeline as pipeline
from services.pulse_ai import space_post_scheduler as scheduler

ROOT = Path(__file__).resolve().parents[1]
AUTOMATED_USER_ID = 0
HUMAN_USER_ID = 42


def _load_cleanup():
    spec = importlib.util.spec_from_file_location(
        "cleanup_automated_post_media", ROOT / "scripts" / "cleanup_automated_post_media.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleanup = _load_cleanup()


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE pulse_posts (
          id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, body TEXT,
          tags_json TEXT, media_ids_json TEXT, post_type TEXT,
          created_at TEXT, updated_at TEXT
        );
        CREATE TABLE chat_media_uploads (
          id INTEGER PRIMARY KEY AUTOINCREMENT, uploader_user_id INTEGER,
          context_type TEXT, context_id TEXT, media_url TEXT, media_type TEXT,
          moderation_status TEXT, updated_at TEXT
        );
        CREATE TABLE pulse_comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, body TEXT, deleted_at TEXT
        );
        CREATE TABLE pulse_reactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER, reaction_type TEXT
        );
        CREATE TABLE pulse_generated_media (
          id INTEGER PRIMARY KEY AUTOINCREMENT, source_post_id INTEGER, media_id INTEGER,
          generation_version TEXT, state TEXT, updated_at TEXT
        );
        """
    )
    # 1: automated post whose generated image no longer resolves -- the black box.
    conn.execute(
        "INSERT INTO pulse_posts VALUES (1,0,'Quick Insight','Verify links before you tap them.','[]','[9]','image','2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    conn.execute("INSERT INTO chat_media_uploads VALUES (9,0,'pulse','1','','image','approved','')")
    conn.execute("INSERT INTO pulse_comments VALUES (1,1,'Useful, thanks.',NULL)")
    conn.execute("INSERT INTO pulse_reactions VALUES (1,1,7,'love')")
    conn.execute(
        "INSERT INTO pulse_generated_media VALUES (1,1,9,'insight-image-v1','attached','')"
    )
    # 2: automated post whose image is intact. Still detached -- the rule is
    # text-only, not merely "remove the broken ones".
    conn.execute(
        "INSERT INTO pulse_posts VALUES (2,0,'Quick Insight','Spot a scam early.','[]','[10]','image','2026-01-02T00:00:00','2026-01-02T00:00:00')"
    )
    conn.execute("INSERT INTO chat_media_uploads VALUES (10,0,'pulse','2','https://cdn.example/ok.png','image','approved','')")
    # 3: automated post that was already text-only.
    conn.execute(
        "INSERT INTO pulse_posts VALUES (3,0,'Quick Insight','Save before you spend.','[]','[]','text','2026-01-03T00:00:00','2026-01-03T00:00:00')"
    )
    # 4/5: real users' image and video posts. Must survive untouched.
    conn.execute(
        "INSERT INTO pulse_posts VALUES (4,42,'My photo','Beach day','[]','[11]','image','2026-01-04T00:00:00','2026-01-04T00:00:00')"
    )
    conn.execute("INSERT INTO chat_media_uploads VALUES (11,42,'pulse','4','https://cdn.example/user.jpg','image','approved','')")
    conn.execute(
        "INSERT INTO pulse_posts VALUES (5,42,'My video','Clip','[]','[12]','video','2026-01-05T00:00:00','2026-01-05T00:00:00')"
    )
    conn.execute("INSERT INTO chat_media_uploads VALUES (12,42,'pulse','5','https://cdn.example/user.mp4','video','approved','')")
    conn.commit()
    return conn


def _post(cur, post_id):
    return dict(cur.execute("SELECT * FROM pulse_posts WHERE id=?", (post_id,)).fetchone())


def _media(cur, media_id):
    return dict(cur.execute("SELECT * FROM chat_media_uploads WHERE id=?", (media_id,)).fetchone())


# --------------------------------------------------------------------------
# Generation side
# --------------------------------------------------------------------------


def _scheduler_db():
    """The tables `publish_space_ai_post` touches, on the real column names.

    `pulse_ai_memory` in particular is written through `update_space_memory`'s
    `ON CONFLICT(space_slug)` upsert, so the UNIQUE constraint is load-bearing
    here and not decoration.
    """
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE pulse_ai_posts (
             id INTEGER PRIMARY KEY AUTOINCREMENT, space_slug TEXT, pulse_post_id INTEGER,
             title TEXT, body TEXT, post_type TEXT, topic TEXT, tags_json TEXT,
             quality_score INTEGER, topic_score INTEGER, trust_score INTEGER,
             energy_score INTEGER, sentiment_score INTEGER, status TEXT,
             metadata_json TEXT, schedule_slot TEXT, created_at TEXT, updated_at TEXT)"""
    )
    cur.execute(
        """CREATE TABLE pulse_ai_memory (
             id INTEGER PRIMARY KEY AUTOINCREMENT, space_slug TEXT UNIQUE,
             memory_json TEXT, recent_topics_json TEXT, recent_hooks_json TEXT,
             updated_at TEXT)"""
    )
    cur.execute(
        """CREATE TABLE pulse_jobs (
             id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT, target_type TEXT,
             target_id INTEGER, status TEXT, attempts INTEGER, max_attempts INTEGER,
             run_after TEXT, created_at TEXT, updated_at TEXT)"""
    )
    return conn, cur


def test_the_scheduler_publishes_text_only_and_queues_no_image_job():
    """Stage 12.1/12.2/12.12 -- what the generator hands the feed."""
    conn, cur = _scheduler_db()

    captured = {}

    def fake_create_post(user_id, body, post_type, title, **kwargs):
        captured.update({"user_id": user_id, "body": body, "post_type": post_type, "title": title, **kwargs})
        return {"ok": True, "post_id": 101}

    space = {"slug": "technology", "name": "Technology", "category": "AI", "trust_score": 90, "energy_score": 70}
    result = scheduler.publish_space_ai_post(cur, space, "rotation", pulse_create_post=fake_create_post)

    assert result["ok"] is True
    assert result["image_job_id"] == 0
    assert captured["post_type"] == "text"
    assert captured["media_ids"] == []
    assert captured["body"].strip()
    assert cur.execute("SELECT COUNT(*) FROM pulse_jobs").fetchone()[0] == 0

    metadata = json.loads(
        cur.execute("SELECT metadata_json FROM pulse_ai_posts WHERE id=?", (result["ai_post_id"],)).fetchone()[0]
    )
    assert metadata["automated_image"]["decision"] == "TEXT_ONLY"
    assert metadata["automated_image"]["visual_intent"] is None


def test_a_rejected_generation_never_publishes_an_empty_post(monkeypatch):
    """Stage 12.13 -- no text means no post, not a blank one."""
    conn, cur = _scheduler_db()

    def rejected(cur_, space, slot):
        return {
            "ok": False, "space_slug": space["slug"], "title": "", "body": "",
            "post_type": "quick_insight", "topic": "", "hook": "", "tags": [],
            "metadata": {}, "metadata_json": "{}",
        }

    monkeypatch.setattr(scheduler, "generate_publishable_post", rejected)
    calls = []
    result = scheduler.publish_space_ai_post(
        cur, {"slug": "technology"}, "rotation",
        pulse_create_post=lambda *a, **k: calls.append(a) or {"ok": True, "post_id": 1},
    )
    assert result["status"] == "rejected"
    assert result["pulse_post_id"] == 0
    assert calls == []


def test_the_image_provider_is_no_longer_reachable_from_this_flow(monkeypatch):
    """Stage 12.14 -- an unusable image provider cannot affect a text post.

    The provider is not merely tolerated on failure; it is never constructed, so
    a missing OPENAI_API_KEY is not a degraded path for automated posts.
    """
    def explode(*_args, **_kwargs):
        raise AssertionError("the image provider must not be constructed")

    monkeypatch.setattr(pipeline, "OpenAIImageProvider", explode)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    result = pipeline.process_job(conn.cursor(), {"id": 1, "target_id": 1, "attempts": 0, "max_attempts": 2})
    assert result["text_only"] is True
    assert result["reason"] == "automated_images_disabled"


# --------------------------------------------------------------------------
# Cleanup side
# --------------------------------------------------------------------------


def test_the_survey_is_read_only_and_counts_what_a_reader_would_see():
    """Stage 13 dry run -- classification before any write."""
    conn = _db()
    cur = conn.cursor()
    before = cur.execute("SELECT COUNT(*) FROM pulse_posts").fetchone()[0]

    report = cleanup.survey(cur)

    assert report["total"] == 3  # only the automated account's posts
    assert report["with_media"] == [1, 2]
    assert report["valid"] == 1 and report["broken"] == 1
    assert cur.execute("SELECT COUNT(*) FROM pulse_posts").fetchone()[0] == before
    assert _post(cur, 1)["post_type"] == "image"


def test_cleanup_detaches_automated_media_without_deleting_anything():
    """Stage 12.9 -- and the detach has to break every path the feed reads.

    Clearing `media_ids_json` alone is not enough: `_media_for_posts` also
    matches `context_type='pulse' AND context_id=<post id>`, so the row would
    still hydrate and the black box would survive the cleanup.
    """
    conn = _db()
    cur = conn.cursor()
    result = cleanup.apply_cleanup(cur, cleanup.survey(cur))
    conn.commit()

    assert result["posts_detached"] == 2
    assert result["media_retired"] == 2
    for post_id in (1, 2):
        post = _post(cur, post_id)
        assert post["post_type"] == "text"
        assert json.loads(post["media_ids_json"]) == []
        assert _media(cur, 9 if post_id == 1 else 10)["context_type"] == cleanup.RETIRED_CONTEXT_TYPE

    # Nothing was deleted -- every row is still addressable.
    assert cur.execute("SELECT COUNT(*) FROM pulse_posts").fetchone()[0] == 5
    assert cur.execute("SELECT COUNT(*) FROM chat_media_uploads").fetchone()[0] == 4
    assert cur.execute("SELECT state FROM pulse_generated_media WHERE source_post_id=1").fetchone()[0] == "detached"


def test_cleanup_preserves_text_identity_and_timestamps():
    """Stage 12.10 -- the post is the same post afterwards."""
    conn = _db()
    cur = conn.cursor()
    before = _post(cur, 1)
    cleanup.apply_cleanup(cur, cleanup.survey(cur))
    after = _post(cur, 1)

    assert after["id"] == before["id"]
    assert after["body"] == "Verify links before you tap them."
    assert after["title"] == before["title"]
    assert after["user_id"] == AUTOMATED_USER_ID
    assert after["created_at"] == before["created_at"]
    # updated_at is display and sort state; a cleanup must not look like activity.
    assert after["updated_at"] == before["updated_at"]


def test_cleanup_preserves_comments_and_reactions():
    """Stage 12.11 -- engagement history is not collateral."""
    conn = _db()
    cur = conn.cursor()
    cleanup.apply_cleanup(cur, cleanup.survey(cur))

    comment = dict(cur.execute("SELECT * FROM pulse_comments WHERE post_id=1").fetchone())
    assert comment["body"] == "Useful, thanks." and comment["deleted_at"] is None
    reaction = dict(cur.execute("SELECT * FROM pulse_reactions WHERE post_id=1").fetchone())
    assert reaction["reaction_type"] == "love" and reaction["user_id"] == 7


@pytest.mark.parametrize("post_id,media_id,kind", [(4, 11, "image"), (5, 12, "video")])
def test_cleanup_never_touches_a_real_users_media_post(post_id, media_id, kind):
    """Stage 12.7/12.8/15 -- the rule is scoped to the automated account."""
    conn = _db()
    cur = conn.cursor()
    cleanup.apply_cleanup(cur, cleanup.survey(cur))

    post = _post(cur, post_id)
    assert post["post_type"] == kind
    assert json.loads(post["media_ids_json"]) == [media_id]
    media = _media(cur, media_id)
    assert media["context_type"] == "pulse"
    assert media["media_url"].startswith("https://cdn.example/")
    assert media["uploader_user_id"] == HUMAN_USER_ID


def test_cleanup_is_idempotent():
    """Re-running finds nothing left to do rather than double-detaching."""
    conn = _db()
    cur = conn.cursor()
    cleanup.apply_cleanup(cur, cleanup.survey(cur))
    second = cleanup.survey(cur)
    assert second["with_media"] == []
    assert cleanup.apply_cleanup(cur, second) == {"posts_detached": 0, "media_retired": 0}


# --------------------------------------------------------------------------
# Serialization side
# --------------------------------------------------------------------------


@pytest.mark.parametrize("resolved", [{}, {"media_url": ""}])
def test_media_without_a_usable_url_is_not_renderable(resolved):
    """Stage 12.5/12.6 -- null and empty media both fail the render gate.

    The gate asserted here is the exact expression `_media_for_posts` uses to
    decide whether a row reaches a renderer, so a payload that fails it never
    becomes a media frame on web or native.
    """
    from services import pulse_feed_engine

    payload = pulse_feed_engine._canonical_media_payload({"id": 5, "media_type": "image"}, resolved)
    assert not payload.get("valid_url")
    assert not (payload.get("valid_url") or payload.get("media_url"))


def test_a_whitespace_url_survives_the_backend_gate_and_needs_the_client_guard():
    """Documents a real gap rather than asserting a fix that does not exist.

    `_canonical_media_payload` does not strip, so a whitespace-only URL is
    truthy and passes `_media_for_posts`. That is why the `<img>` error handler
    added to `pulse_home_core.js` matters: it, not the backend, is what removes
    the frame when the src fails to load. Stripping here would touch the shared
    path every normal user photo and video flows through, which this mission is
    scope-locked out of, and the automated posts that could carry such a URL are
    detached by the cleanup and can no longer be generated.
    """
    from services import pulse_feed_engine

    payload = pulse_feed_engine._canonical_media_payload({"id": 5, "media_type": "image"}, {"media_url": "   "})
    assert payload["valid_url"] == "   "
    assert payload.get("valid_url") or payload.get("media_url")


def test_a_real_media_url_stays_renderable():
    """The gate above must not be a blanket 'no media' switch."""
    from services import pulse_feed_engine

    payload = pulse_feed_engine._canonical_media_payload(
        {"id": 6, "media_type": "image"},
        {"media_url": "https://cdn.example/user.jpg", "width": 1024, "height": 1280},
    )
    assert payload["valid_url"]
