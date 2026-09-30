"""The format decision, and what ``PULSEDROP_PAIR_EVERY_POST`` does to it.

Why this file exists at all
---------------------------
``tests/pulsedrop/test_curator.py`` says in its own docstring that it stubs
``distribution.decide`` "except where a test is specifically about a decision
shape". That is the right call for a suite about the tick — but it left the
decision matrix itself without direct coverage, and the matrix is where the
product policy lives. Every assertion below calls the real ``decide``.

Why nothing here touches a database
-----------------------------------
``decide`` is pure with respect to storage: the candidate and the history are
both handed in, and the only external reads are ``config`` accessors. Those go
through a 20-second memo over a settings table, so a test that wrote rows would
be testing the cache as much as the decision. Patching the accessors is both
faster and a truer unit — it also means a future settings-table change cannot
quietly turn these green for the wrong reason.

The one thing that would make this suite lie
--------------------------------------------
``_render_capable`` probes for ffmpeg, which is present in production and absent
on most development machines. Left alone it would make every composed-Reel
assertion below pass or fail depending on the host, so it is pinned per test.
Where a test is *about* a box with no encoder, it is pinned to False explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.pulsedrop import config, distribution, diversity, ranking

NOW = datetime(2026, 9, 27, 22, 0, 0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _listing(listing_id=1, *, seller=7, category="gadgets", images=1, video=""):
    """A candidate carrying only what the decision actually reads.

    Deliberately not a marketplace row. ``decide`` consults exactly three things
    about a listing -- its images, its video, and the identity fields the
    history keys on -- and a fixture that carried the other forty columns would
    suggest they mattered.
    """
    media_rows = []
    if video:
        media_rows.append({"media_type": "video", "media_url": video})
    return {
        "id": listing_id,
        "seller_user_id": seller,
        "category": category,
        "cover_image_url": "https://cdn.example/cover.jpg" if images else "",
        "media_rows": media_rows
        + [
            {"media_type": "image", "media_url": f"https://cdn.example/{i}.jpg"}
            for i in range(1, images)
        ],
    }


def _ranked(listing, score=0.5):
    return ranking.Ranked(
        listing=listing,
        score=ranking.Score(total=score, components={}),
        label=None,
    )


def _history(rows=(), *, sellers=1):
    return diversity.History(list(rows), NOW, distinct_eligible_sellers=sellers)


def _published(listing, surface, *, hours_ago=1):
    return diversity.Publication(
        listing_id=int(listing["id"]),
        seller_user_id=int(listing["seller_user_id"]),
        category=str(listing["category"]),
        surface=surface,
        published_at=(NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
    )


@pytest.fixture
def cfg(monkeypatch):
    """Config pinned to a state where one fresh product is publishable to both.

    Starting from "everything permits it" and having each test switch off the
    single thing it is about keeps the assertions readable: a test that fails
    names its own cause, instead of failing because one of nine unrelated
    defaults happened to block first.
    """

    def set(**values):
        for name, value in values.items():
            monkeypatch.setattr(config, name, lambda v=value: v)

    set(
        signals_enabled=True,
        reels_enabled=True,
        pair_every_post=False,
        reel_min_images=1,
        product_cooldown_hours=336,
        reel_product_cooldown_hours=720,
        seller_cooldown_hours=0,
        reel_seller_cooldown_hours=0,
        category_cooldown_hours=0,
        cross_format_cooldown_hours=0,
        min_publish_interval_seconds=0,
        reel_min_interval_seconds=0,
        daily_publication_cap=100,
        daily_reel_cap=100,
        max_per_seller_share_percent=100,
        seller_share_window=10,
    )
    monkeypatch.setattr(distribution, "_render_capable", lambda: True)
    return set


# ---------------------------------------------------------------------------
# The default: a Reel is earned
# ---------------------------------------------------------------------------


def test_stills_only_product_gets_a_signal_and_no_reel_by_default(cfg):
    """The resting state, and the baseline the switch is defined against.

    A dropshipped listing with photographs and no footage is the overwhelming
    majority of the production catalogue, so this is not an edge case -- it is
    what PulseDrop does almost every tick.
    """
    decision = distribution.decide(_ranked(_listing()), _history())

    assert decision.outcome == distribution.SIGNAL_ONLY
    assert decision.reason == "signal_is_the_right_format"
    assert not decision.publishes_reel


def test_seller_video_on_a_strong_product_still_earns_both(cfg):
    """The narrow pre-existing path to SIGNAL_AND_REEL must survive the switch.

    Pinned because ``pair_every_post`` short-circuits ``_earns_both``: an
    implementation that *replaced* the earned path rather than adding to it
    would pass every other test in this file.
    """
    listing = _listing(video="https://cdn.example/clip.mp4", images=2)
    decision = distribution.decide(
        _ranked(listing, score=distribution.DUAL_FORMAT_MIN_SCORE), _history()
    )

    assert decision.outcome == distribution.SIGNAL_AND_REEL
    assert decision.reason == "earned_both_formats"
    assert decision.reel_source == distribution.SOURCE_SELLER_VIDEO


def test_the_earned_path_stays_shut_while_the_cross_format_cooldown_stands(cfg):
    """Default cooldown, default switch: dual publication is unreachable."""
    cfg(cross_format_cooldown_hours=48)
    listing = _listing(video="https://cdn.example/clip.mp4", images=2)

    decision = distribution.decide(_ranked(listing, score=0.9), _history())

    assert decision.outcome == distribution.REEL_ONLY
    assert decision.reason == "seller_video_leads"


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_pairing_gives_a_stills_only_product_both_formats(cfg):
    """The switch's entire purpose, on the catalogue production actually has.

    If this were gated on seller footage the way the earned path is, turning it
    on would change nothing for almost every listing and it would read to an
    operator as broken rather than as restrained.
    """
    cfg(pair_every_post=True)

    decision = distribution.decide(_ranked(_listing()), _history())

    assert decision.outcome == distribution.SIGNAL_AND_REEL
    assert decision.reason == "paired_every_post"
    assert decision.reel_source == distribution.SOURCE_COMPOSED_IMAGES
    assert decision.publishes_signal and decision.publishes_reel


def test_pairing_ignores_the_score_floor(cfg):
    """A weak product is still paired. The operator asked for reach, not for a
    second opinion about which products deserve it."""
    cfg(pair_every_post=True)

    decision = distribution.decide(
        _ranked(_listing(), score=distribution.DUAL_FORMAT_MIN_SCORE - 0.4), _history()
    )

    assert decision.outcome == distribution.SIGNAL_AND_REEL


def test_pairing_does_not_override_a_reel_cooldown(cfg):
    """Fairness outranks the switch, and the run log says which rule won.

    This is the assertion that keeps the change honest. ``pair_every_post``
    widens what a publishable product *earns*; if it also silently suspended
    the cooldowns it would be a second, undocumented kill switch on the whole
    fairness layer.
    """
    cfg(pair_every_post=True, reel_product_cooldown_hours=720)
    listing = _listing()
    history = _history([_published(listing, diversity.REEL, hours_ago=2)])

    decision = distribution.decide(_ranked(listing), history)

    assert decision.outcome == distribution.SIGNAL_ONLY
    assert decision.reason == f"unpaired_{diversity.PRODUCT_COOLDOWN}"
    assert not decision.publishes_reel


def test_pairing_does_not_override_the_daily_reel_cap(cfg):
    """The other half of the same rule, via a cap rather than a cooldown."""
    cfg(pair_every_post=True, daily_reel_cap=1)
    listing = _listing()
    history = _history([_published(_listing(listing_id=99), diversity.REEL, hours_ago=3)])

    decision = distribution.decide(_ranked(listing), history)

    assert decision.outcome == distribution.SIGNAL_ONLY
    assert decision.reason == "unpaired_reel_daily_cap"


def test_pairing_cannot_publish_a_product_fairness_has_blocked_outright(cfg):
    """Both surfaces on cooldown stays DEFER -- the switch only ever upgrades a
    tick that was already going to publish something."""
    cfg(pair_every_post=True)
    listing = _listing()
    history = _history(
        [
            _published(listing, diversity.SIGNAL, hours_ago=2),
            _published(listing, diversity.REEL, hours_ago=2),
        ]
    )

    decision = distribution.decide(_ranked(listing), history)

    assert decision.outcome == distribution.DEFER
    assert not decision.publishes_signal and not decision.publishes_reel


def test_pairing_respects_the_reels_kill_switch(cfg):
    """Reels off means no Reel, switch or no switch. A volume preference must
    not be able to restart a surface an operator stopped."""
    cfg(pair_every_post=True, reels_enabled=False)

    decision = distribution.decide(_ranked(_listing()), _history())

    assert decision.outcome == distribution.SIGNAL_ONLY
    assert not decision.publishes_reel


def test_pairing_falls_back_to_a_signal_on_a_box_with_no_encoder(cfg, monkeypatch):
    """Path B needs ffmpeg locally. Without it the composed Reel is not merely
    blocked, it is impossible, and the tick must still publish the Signal.

    Note the reason is the plain one, not ``unpaired_*``: no encoder makes the
    Reel structurally unavailable rather than deferred, so there is no blocker
    to name and nothing that will lift on a later tick.
    """
    cfg(pair_every_post=True)
    monkeypatch.setattr(distribution, "_render_capable", lambda: False)

    decision = distribution.decide(_ranked(_listing()), _history())

    assert decision.outcome == distribution.SIGNAL_ONLY
    assert decision.reason == "signal_is_the_right_format"
    assert not decision.publishes_reel
