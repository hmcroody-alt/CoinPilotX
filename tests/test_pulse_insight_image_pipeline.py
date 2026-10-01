import json
import sqlite3
import struct
import zlib

import pytest

from services.pulse_ai import automated_image_pipeline as pipeline


def _png(width=1024, height=1280):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + (b"\x12\x34\x56" * width) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE pulse_posts (
          id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, body TEXT,
          tags_json TEXT, media_ids_json TEXT, post_type TEXT, updated_at TEXT
        );
        CREATE TABLE pulse_ai_posts (
          id INTEGER PRIMARY KEY, pulse_post_id INTEGER, space_slug TEXT,
          topic TEXT, metadata_json TEXT
        );
        CREATE TABLE pulse_jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT, target_type TEXT,
          target_id INTEGER, status TEXT, attempts INTEGER, max_attempts INTEGER,
          run_after TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE chat_media_uploads (
          id INTEGER PRIMARY KEY AUTOINCREMENT, uploader_user_id INTEGER,
          context_type TEXT, context_id TEXT, original_filename TEXT,
          stored_filename TEXT, media_url TEXT, thumbnail_url TEXT, poster_url TEXT,
          media_type TEXT, mime_type TEXT, file_size_bytes INTEGER, width INTEGER,
          height INTEGER, moderation_status TEXT, storage_provider TEXT,
          storage_key TEXT, object_key TEXT, cdn_url TEXT, public_url TEXT,
          is_available INTEGER, processing_status TEXT, verification_status TEXT,
          created_at TEXT, updated_at TEXT
        );
        """
    )
    body = "Learn practical sports strategy through spacing, timing, communication, and pattern recognition without fabricated scores or athlete likenesses."
    conn.execute("INSERT INTO pulse_posts VALUES (1,0,'Sports Edge',?, '[\"sports\"]','[]','text','')", (body,))
    conn.execute("INSERT INTO pulse_ai_posts VALUES (1,1,'sports','sports strategy','{}')")
    conn.commit()
    return conn


class Provider:
    def __init__(self, content=None, error=None):
        self.content = content or _png()
        self.error = error
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        if self.error:
            raise self.error
        assert "hot take" not in prompt.lower()
        return {"bytes": self.content, "provider": "test-provider", "model": "test-model"}


@pytest.fixture
def images_enabled(monkeypatch):
    """Re-enable the retired pipeline so its internals stay under test.

    Automated images are off as a product rule, and every guard below asserts
    that. But the generation code still ships, so the safety behaviour it
    encodes -- reject an unrenderable row, never leak the API key -- has to keep
    being exercised, or the day someone flips the constant back they inherit
    untested code that silently regressed.
    """
    monkeypatch.setattr(pipeline, "AUTOMATED_IMAGES_ENABLED", True)


@pytest.mark.parametrize(
    "post",
    [
        {"title": "Notice", "body": "Brief maintenance note."},
        {"title": "Sports", "body": "A" * 120, "topic": "sports strategy", "space_slug": "sports"},
        {"title": "Scam warning", "body": "Verify links and protect your accounts. " * 4, "topic": "scam awareness"},
        {"title": "Emergency", "body": "Emergency safety notice " * 8},
    ],
)
def test_every_automated_post_decides_text_only(post):
    """Stage 12.1/12.12 -- the decision is text-only for every shape of post.

    These are the exact inputs that previously returned IMAGE_RECOMMENDED and
    IMAGE_REQUIRED, so the parametrization is the regression: it fails if the
    heuristic ever starts recommending an image again.
    """
    assert pipeline.decide_image(post) == "TEXT_ONLY"
    assert pipeline.plan_for_post(post)["visual_intent"] is None


@pytest.mark.parametrize(
    "topic,category,style",
    [
        ("sports strategy", "sports", "sports illustration"),
        ("creator economy growth", "creator", "creator economy"),
        ("scam awareness", "safety", "cyber-safety"),
    ],
)
def test_prompt_is_contextual_safe_and_private(topic, category, style):
    post = {
        "title": "HOT TAKE: PLS-PRIVATE-123",
        "body": f"Educational context about {topic} for @private_user with 4111 1111 1111 1111.",
        "topic": topic,
        "space_slug": category,
    }
    prompt = pipeline.build_image_prompt(post)
    assert style in prompt
    assert "hot take" not in prompt.lower()
    assert "PLS-" not in prompt
    assert "4111" not in prompt
    assert "@private_user" not in prompt
    assert "fake documentary evidence" in prompt
    assert "real person or public figure likeness" in prompt


def test_no_image_job_is_ever_enqueued():
    """Stage 12.2 -- no job row, so nothing exists to generate media later."""
    conn = _db()
    cur = conn.cursor()
    plan = pipeline.plan_for_post({"body": "A" * 120, "topic": "technology", "space_slug": "technology"})
    assert pipeline.enqueue_for_post(cur, 1, plan) == 0
    # Also refuse a plan handed in by a caller that predates the rule -- the
    # decision field is not the only thing standing between a post and an image.
    assert pipeline.enqueue_for_post(cur, 1, {"decision": "IMAGE_REQUIRED"}) == 0
    assert cur.execute("SELECT COUNT(*) FROM pulse_jobs").fetchone()[0] == 0


def test_a_job_queued_before_the_rule_changed_drains_to_text_only(monkeypatch):
    """Stage 12.3/12.4 -- the provider is never called and nothing is attached.

    Jobs enqueued before automated images were switched off are still sitting in
    `pulse_jobs`. This is the case that would otherwise keep attaching images
    after the feature was supposedly disabled, so it is asserted against a job
    row that looks exactly like a real queued one.
    """
    conn = _db()
    cur = conn.cursor()

    def explode(*_args, **_kwargs):
        raise AssertionError("storage must not be touched for an automated post")

    monkeypatch.setattr(pipeline.media_storage, "save_public_file", explode)
    provider = Provider()
    result = pipeline.process_job(cur, {"id": 7, "target_id": 1, "attempts": 0, "max_attempts": 2}, provider=provider)
    conn.commit()

    assert result["ok"] and result["text_only"] is True
    assert result["decision"] == "TEXT_ONLY"
    assert provider.calls == 0
    assert "media_id" not in result
    post = dict(cur.execute("SELECT * FROM pulse_posts WHERE id=1").fetchone())
    assert post["post_type"] == "text"
    assert json.loads(post["media_ids_json"]) == []
    assert cur.execute("SELECT COUNT(*) FROM chat_media_uploads").fetchone()[0] == 0


def test_the_disabled_guard_runs_before_any_provenance_bookkeeping():
    """A retired job must not leave a `pulse_generated_media` row behind.

    The guard sits ahead of `_ensure_tables`, so draining the queue writes
    nothing at all -- not even a 'processing' row that a later reader would have
    to interpret.
    """
    conn = _db()
    cur = conn.cursor()
    pipeline.process_job(cur, {"id": 9, "target_id": 1, "attempts": 0, "max_attempts": 2}, provider=Provider())
    tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "pulse_generated_media" not in tables


def test_invalid_media_retries_then_falls_back_without_placeholder(images_enabled):
    conn = _db()
    cur = conn.cursor()
    provider = Provider(content=b"not an image")
    with pytest.raises(pipeline.ImagePipelineError):
        pipeline.process_job(cur, {"id": 1, "target_id": 1, "attempts": 0, "max_attempts": 2}, provider=provider)
    result = pipeline.process_job(cur, {"id": 1, "target_id": 1, "attempts": 1, "max_attempts": 2}, provider=provider)
    assert result["text_only"] is True
    assert cur.execute("SELECT COUNT(*) FROM chat_media_uploads").fetchone()[0] == 0
    assert json.loads(cur.execute("SELECT media_ids_json FROM pulse_posts WHERE id=1").fetchone()[0]) == []
    assert cur.execute("SELECT state FROM pulse_generated_media").fetchone()[0] == "failed"


def test_storage_failure_is_bounded_and_text_post_survives(monkeypatch, images_enabled):
    conn = _db()
    cur = conn.cursor()
    monkeypatch.setattr(pipeline.media_storage, "save_public_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("storage down")))
    result = pipeline.process_job(cur, {"id": 2, "target_id": 1, "attempts": 1, "max_attempts": 2}, provider=Provider())
    assert result["text_only"] is True
    assert cur.execute("SELECT post_type FROM pulse_posts WHERE id=1").fetchone()[0] == "text"


def test_provider_timeout_is_safe_and_does_not_log_or_return_secret(monkeypatch):
    provider = pipeline.OpenAIImageProvider(api_key="super-secret")
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(pipeline.ImagePipelineError, match="image_provider_timeout") as exc:
        provider.generate("safe public prompt")
    assert "super-secret" not in str(exc.value)


def test_storage_success_without_a_url_never_attaches_an_unrenderable_row(monkeypatch, images_enabled):
    """Storage can report success and still return no URL.

    That is the blank-media defect at its source: the row was inserted, the post
    was flipped to post_type='image', and the feed then had an attachment with
    nothing to draw. The post must stay text-only instead.
    """
    conn = _db()
    cur = conn.cursor()
    monkeypatch.setattr(
        pipeline.media_storage,
        "save_public_file",
        lambda upload, folder: {"provider": "local", "media_url": "  ", "storage_key": "k", "durable_uploaded": True},
    )
    monkeypatch.setattr(pipeline.media_storage, "provider", lambda: "local")
    result = pipeline.process_job(cur, {"id": 3, "target_id": 1, "attempts": 1, "max_attempts": 2}, provider=Provider())
    assert result["text_only"] is True
    assert cur.execute("SELECT COUNT(*) FROM chat_media_uploads").fetchone()[0] == 0
    assert cur.execute("SELECT post_type FROM pulse_posts WHERE id=1").fetchone()[0] == "text"
    assert json.loads(cur.execute("SELECT media_ids_json FROM pulse_posts WHERE id=1").fetchone()[0]) == []


def test_feed_omits_media_objects_that_have_no_usable_url():
    """Regression for the owner-reported giant empty media block.

    A post carrying an attachment whose URL never materialized must serialize
    with NO media entry at all. Emitting a well-shaped object with blank urls is
    what let clients count it as media and reserve a full-bleed box.
    """
    from services import pulse_feed_engine

    blank = pulse_feed_engine._canonical_media_payload({"id": 5, "media_type": "image"}, {})
    assert not blank["valid_url"] and not blank["media_url"]
    assert blank["width"] == 0 and blank["height"] == 0
    assert blank["hydration_state"] == "missing"

    usable = pulse_feed_engine._canonical_media_payload(
        {"id": 6, "media_type": "image"},
        {"media_url": "https://cdn.example/insight.png", "width": 1024, "height": 1280},
    )
    assert usable["valid_url"] and usable["aspect_ratio"] == round(1024 / 1280, 4)

    source = open("services/pulse_feed_engine.py", encoding="utf-8").read()
    assert 'if not (payload.get("valid_url") or payload.get("media_url")):' in source
    assert "pulse_media_invalid_omitted" in source


def test_feed_renderer_contract_is_reused_without_generated_url_field():
    source = open("services/pulse_ai/automated_image_pipeline.py", encoding="utf-8").read()
    assert "media_ids_json" in source and "chat_media_uploads" in source
    assert "generated_image_url" not in source
