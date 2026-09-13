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


# ---------------------------------------------------------------------------
# §22 — image generation is AI spend and must appear in the month's ledger
# ---------------------------------------------------------------------------
#
# The gap these tests close is LATENT, not active. `AUTOMATED_IMAGES_ENABLED`
# is False as a product rule, so `OpenAIImageProvider.generate` does not run in
# production today and no image dollars are currently going unrecorded. They are
# here for the same reason the `images_enabled` fixture above exists: the code
# still ships, and the day someone flips the constant back they should not
# inherit a paid provider that bills silently.


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    """Point the cost ledger at a throwaway database.

    Not boilerplate. `services.db` falls back to the *relative* path
    `coinpilotx.db` when `DATABASE_URL` is unset, so a ledger assertion without
    this is really an assertion about how many times the suite has been run on
    this machine — and it leaves rows behind in the developer's dev database.
    """
    from services import undx_cost

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "ledger.db"))
    undx_cost.reset_for_tests()
    yield undx_cost
    undx_cost.reset_for_tests()


def _image_response(b64="aGVsbG8="):
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return json.dumps({"data": [{"b64_json": b64}]}).encode("utf-8")

    return lambda *_a, **_k: _Ctx()


def _image_row(undx_cost):
    snapshot = undx_cost.month_snapshot()
    assert snapshot["source"] == "ledger"
    return snapshot


def test_a_generated_image_is_metered_under_its_own_kind(monkeypatch, ledger):
    """The laundering §22 forbids: this must not arrive in the chat bucket, which
    is the only number anyone reads, nor vanish entirely."""
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", _image_response())
    provider = pipeline.OpenAIImageProvider(api_key="k", model="gpt-image-1")
    assert provider.generate("a prompt")["provider"] == "openai"

    snapshot = _image_row(ledger)
    row = snapshot["kinds"]["image"]
    assert row["calls"] == 1
    assert "chat" not in snapshot["kinds"]
    assert "openai" in snapshot["providers"]


def test_an_image_with_no_published_price_is_uncosted_not_free(monkeypatch, ledger):
    """§34: unknown is not $0.00.

    `gpt-image-1` has no entry in the capability price table, so the honest
    record is "one call happened, we do not know what it cost". Recording it at
    zero would make the month's dollar total look complete while being wrong by
    whatever the real per-image price is, and `unpriced_providers()` — which
    still names this provider — is what enumerates the remaining work.
    """
    from services import undx_capabilities as cap

    assert (cap.CALL_KIND_IMAGE, "openai") in cap.unpriced_providers()

    monkeypatch.setattr(pipeline.urllib.request, "urlopen", _image_response())
    pipeline.OpenAIImageProvider(api_key="k").generate("a prompt")

    row = _image_row(ledger)["kinds"]["image"]
    assert (row["calls"], row["cost_micro_usd"], row["uncosted_calls"]) == (1, 0, 1)


def test_the_model_priced_is_the_effective_model_not_the_default(monkeypatch, ledger):
    """An env override changes what OpenAI bills for, so it has to be the model
    the pricing lookup sees.

    Asserted on the arguments handed to `record_spend` rather than on a ledger
    row, because `undx_cost_ledger` has no model column — it keys on
    (month, provider, call_kind) only. So per-model attribution does not survive
    into the durable record at all, and the call site's arguments are the only
    place the distinction is observable. That is a gap, noted in the census;
    pinning it here at least means the day a price is published for one image
    model and not another, the lookup is already being given the right name
    instead of the default.
    """
    monkeypatch.setenv("PULSE_INSIGHT_IMAGE_MODEL", "gpt-image-1-mini")
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", _image_response())

    seen = []
    real = pipeline.undx_capabilities.record_spend
    monkeypatch.setattr(
        pipeline.undx_capabilities,
        "record_spend",
        lambda *a, **k: (seen.append((a, k)), real(*a, **k))[1],
    )
    result = pipeline.OpenAIImageProvider(api_key="k").generate("a prompt")

    assert result["model"] == "gpt-image-1-mini"
    assert seen == [(("image", "openai"), {"units": 1, "model": "gpt-image-1-mini"})]
    assert _image_row(ledger)["kinds"]["image"]["calls"] == 1


@pytest.mark.parametrize(
    "urlopen, code",
    [
        (lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()), "image_provider_timeout"),
        (_image_response(b64=""), "image_provider_empty_result"),
        (_image_response(b64="!!! not base64 !!!"), "image_provider_invalid_base64"),
    ],
)
def test_a_failed_generation_is_not_recorded_as_an_image_received(monkeypatch, ledger, urlopen, code):
    """Spend is metered after the bytes are known good, so the ledger's image
    count stays a count of pictures we actually got.

    The undecodable-base64 case is the uncomfortable one: OpenAI may well have
    billed for it. That is a named gap in the census rather than a reason to
    count every attempt, because inflating the received-image count to cover a
    billing edge would corrupt the number that is checkable.
    """
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", urlopen)
    with pytest.raises(pipeline.ImagePipelineError, match=code):
        pipeline.OpenAIImageProvider(api_key="k").generate("a prompt")

    assert "image" not in _image_row(ledger)["kinds"]


def test_an_unconfigured_provider_records_nothing(ledger):
    """No key means no request, so there is nothing to bill and nothing to log.
    Asserted because a metering call placed at the top of `generate` would count
    a call that never left the process."""
    with pytest.raises(pipeline.ImagePipelineError, match="image_provider_not_configured"):
        pipeline.OpenAIImageProvider(api_key="").generate("a prompt")

    assert _image_row(ledger)["kinds"] == {}


def test_an_unreachable_ledger_does_not_cost_the_caller_its_image(monkeypatch, ledger):
    """Metering is observation, not a precondition.

    The database really does go away sometimes, and when it does the image still
    has to come back — otherwise adding accounting made the feature less reliable
    than it was before. Asserted by breaking the connection rather than by
    stubbing `record_spend` to throw: `record_spend` is documented never to
    raise, so a test that fakes it raising would be exercising an impossible
    state and could never fail for a real reason. The in-process mirror still
    answers, which is the distinction between "degraded" and "silent".
    """
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", _image_response())
    monkeypatch.setattr(
        ledger, "_connect", lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("gone"))
    )
    assert pipeline.OpenAIImageProvider(api_key="k").generate("a prompt")["bytes"] == b"hello"

    snapshot = ledger.month_snapshot()
    assert snapshot["source"] != "ledger"
    assert snapshot["kinds"]["image"]["calls"] == 1
