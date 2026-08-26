from pathlib import Path

from services import pulse_feed_engine
from services.pulse_ai.content_policy import sanitize_automated_text
from services.pulse_ai.space_ai_engine import generate_space_post
from services.pulse_ai.space_prompt_builder import DEFAULT_FORMATS


def test_system_actor_has_explicit_non_human_identity(monkeypatch):
    monkeypatch.setattr(pulse_feed_engine, "_ensure_member_000_profile", lambda: None)
    author = pulse_feed_engine._public_author({"user_id": 0})

    assert author["display_name"] == "PulseSoc Insight"
    assert author["username"] == "pulsesoc_insight"
    assert author["account_type"] == "PULSESOC_AUTOMATED"
    assert author["automated"] is True
    assert author["official_system_account"] is True
    assert author["primary_label"] == "Official PulseSoc System Account"
    assert not author["premium_verified"]
    # The payload now carries an absolute URL -- see MEMBER_000_AVATAR_URL for
    # why -- so the on-disk check goes through the repo-relative path constant
    # rather than slicing a leading slash off whatever the payload happened to
    # contain. Both are asserted: the client gets a loadable URL, and that URL
    # resolves to an asset that is actually committed.
    assert author["avatar_url"] == pulse_feed_engine.MEMBER_000_AVATAR_URL
    assert author["avatar_url"].endswith(pulse_feed_engine.MEMBER_000_AVATAR_PATH)
    avatar = Path(__file__).resolve().parents[1] / pulse_feed_engine.MEMBER_000_AVATAR_PATH.lstrip("/")
    assert avatar.is_file()


def test_retired_label_is_removed_from_schedule_and_guarded_case_insensitively():
    assert "hot_take" not in DEFAULT_FORMATS
    assert sanitize_automated_text("HOT TAKE: verify first") == "Quick Insight: verify first"

    post = generate_space_post(
        {
            "slug": "policy-test",
            "name": "Policy Test",
            "category": "AI",
            "trust_score": 90,
            "energy_score": 70,
        },
        post_type="hot_take",
    )
    assert post["post_type"] == "quick_insight"
    assert "hot take" not in f"{post['title']} {post['body']}".lower()


def test_legacy_system_actor_can_list_and_count_its_existing_posts(monkeypatch):
    class Cursor:
        def execute(self, _sql, _params=()):
            return None

        def fetchall(self):
            return []

        def fetchone(self):
            return {"total": 3}

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(pulse_feed_engine.user_context, "connect", lambda: Connection())
    monkeypatch.setattr(pulse_feed_engine, "_ensure_home_safety_tables", lambda _cur: None)
    monkeypatch.setattr(pulse_feed_engine, "_media_for_posts", lambda _ids: {})
    monkeypatch.setattr(pulse_feed_engine, "_music_for_posts", lambda _ids: {})

    assert pulse_feed_engine.count_user_posts(0, viewer_user_id=9) == 3
    assert pulse_feed_engine.list_user_posts(0, viewer_user_id=9)["ok"] is True
