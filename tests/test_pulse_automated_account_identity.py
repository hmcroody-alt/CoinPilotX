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
