"""The @pulsedrop profile, as the native app receives it.

Why this suite needs the monolith
---------------------------------
``pulse_native_profile_payload`` is the one function that answers "what is this
profile" for the native app, and it lives in ``bot``. It reads ``users``,
``arena_profiles``, ``pulse_follows``, ``pulse_user_badges``,
``pulse_profile_themes`` and the viewer-permission tables, so a stub schema
cannot host it — see the ``monolith`` fixture in this package's conftest for how
that import is made safe.

What is actually being defended
-------------------------------
Two facts about PulseDrop that the app has to be *told*, because until now it
inferred both:

``brand_cover_*`` — the cover is a designed banner with a centred wordmark, so
it is laid out whole rather than cropped to fill the hero. The app used to reach
that conclusion by matching the cover's **filename**. Brand assets are
cache-busted by re-dating the filename, so shipping new artwork would have
silently reverted the account to a crop with nothing failing; and a second
official account could not be given the treatment without an app release.

``has_social_graph`` — the app hid the follower and following counts for any
automated account. That was right for the only automated account that existed,
PulseSoc Insight, which lives at ``user_id=0`` where no ``pulse_follows`` row can
point at it: its follower count is undefined, not zero. It is wrong for
PulseDrop, which is an ordinary ``users`` row precisely so that following it
works, and whose counts are real.

The two are asserted together in :class:`TestTheTwoAutomatedAccountsDiffer`,
because the bug being prevented is not "PulseDrop gets the wrong value" but "the
two accounts get collapsed back into one rule".
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from services.pulsedrop import account

#: Keys the overlay adds. An ordinary member must carry none of them, and must
#: carry them *absent* rather than false — the client defaults a missing
#: ``has_social_graph`` to true, so shipping ``False`` on every human profile
#: would erase the follower count platform-wide.
OVERLAY_KEYS = frozenset(account.profile_overlay())

REPO_ROOT = Path(__file__).resolve().parents[2]

VIEWER_ID = 4101
OTHER_ID = 4102


def _png_aspect_ratio(path: Path) -> float:
    """Width over height, read from the PNG's IHDR.

    Eight bytes of signature, then a 4-byte length and the ``IHDR`` tag, then
    width and height as big-endian 32-bit integers. Parsed by hand so that the
    check costs nothing and depends on nothing: the point is to compare the
    declared shape against the *file*, and a comparison that needed Pillow would
    have been skipped on any machine without it, which is the machine where it
    would have mattered.
    """
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    width, height = struct.unpack(">II", header[16:24])
    assert height, f"{path} declares zero height"
    return width / height


@pytest.fixture(scope="module")
def pulsedrop_user(monolith):
    """PulseDrop, provisioned once, plus two ordinary members to compare against.

    ``ensure_account`` is production's own provisioning path rather than an
    INSERT written here, so the row under test is the row the worker creates —
    including the verified badge and the ``arena_profiles`` row that gives the
    account its ``public_player_id``.
    """
    from services import db as platform_db

    account.reset_cache()
    user_id = account.ensure_account()
    assert user_id, "provisioning failed; every assertion below would be vacuous"

    conn = platform_db.connect()
    try:
        for member, name in ((VIEWER_ID, "viewer"), (OTHER_ID, "other")):
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, username, display_name, account_status)"
                " VALUES (?, ?, ?, 'active')",
                (member, f"member_{member}", name),
            )
        # A real follower, so ``follower_count`` is a number that could only have
        # come from the table. Asserting against 0 would pass just as well if the
        # overlay had overwritten the count with a hard-coded zero.
        conn.execute(
            "INSERT OR IGNORE INTO pulse_follows (follower_user_id, followed_user_id) VALUES (?, ?)",
            (VIEWER_ID, user_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO pulse_follows (follower_user_id, followed_user_id) VALUES (?, ?)",
            (OTHER_ID, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id


@pytest.fixture()
def payload(monolith, cursor, pulsedrop_user):
    return monolith.pulse_native_profile_payload(cursor, pulsedrop_user, VIEWER_ID)


class TestTheOverlayReachesTheClient:
    def test_declares_itself_automated_and_official(self, payload):
        assert payload["automated"] is True
        assert payload["official_system_account"] is True
        assert payload["account_type"] == "PULSESOC_AUTOMATED"
        assert payload["system_account_label"] == account.SYSTEM_LABEL

    def test_carries_both_disclosures_verbatim(self, payload):
        # Verbatim against the module constants, not against a paraphrase: these
        # strings are the account's answer to "is this a person", and an app
        # review rejection is the failure mode for getting them wrong.
        assert payload["automation_disclosure"] == account.AUTOMATION_DISCLOSURE
        assert payload["transparency_disclosure"] == account.TRANSPARENCY_DISCLOSURE

    def test_declares_how_to_lay_the_banner_out(self, payload):
        assert payload["brand_cover_fit"] == "contain"
        assert payload["brand_cover_aspect_ratio"] == pytest.approx(2000 / 750)

    def test_the_declared_shape_matches_the_file_that_ships(self, payload):
        # The whole defect class here was the app's idea of the artwork drifting
        # from the artwork. Redating the cover to a differently-shaped file now
        # fails here rather than letterboxing it on every device.
        asset = REPO_ROOT / account.COVER_PATH.lstrip("/")
        assert asset.exists(), f"{asset} is missing; the cover URL points at nothing"
        assert payload["brand_cover_aspect_ratio"] == pytest.approx(_png_aspect_ratio(asset))

    def test_is_verified_by_a_real_badge(self, payload):
        # Not part of the overlay — it comes from ``pulse_user_badges`` via the
        # ordinary path, which is the point. Premium no longer lights this up.
        assert payload["verified_badge"] is True


class TestTheOverlayDoesNotReplaceTheProfile:
    """It is merged onto the real payload, and merged last. Both matter."""

    def test_keeps_the_real_follower_count(self, payload):
        assert payload["follower_count"] == 2
        assert payload["following_count"] == 0

    def test_keeps_this_viewer_s_real_follow_state(self, monolith, cursor, pulsedrop_user):
        assert monolith.pulse_native_profile_payload(cursor, pulsedrop_user, VIEWER_ID)["viewer_follows"] is True
        # A viewer who does not follow gets False from the same code path, so the
        # True above is a lookup and not a constant.
        stranger = monolith.pulse_native_profile_payload(cursor, pulsedrop_user, 999_001)
        assert stranger["viewer_follows"] is False

    def test_keeps_the_identity_the_account_module_provisioned(self, payload):
        assert payload["username"] == account.USERNAME
        assert payload["public_player_id"] == account.USERNAME
        assert payload["display_name"] == account.DISPLAY_NAME

    def test_serves_the_brand_media_as_absolute_urls(self, payload):
        # React Native's Image cannot resolve a site-relative URI, so a
        # ``/static/...`` cover renders as nothing at all. See account.py.
        for key in ("avatar_url", "cover_url"):
            assert payload[key].startswith("http"), f"{key} is not absolute: {payload[key]!r}"
        assert payload["cover_url"].endswith(account.COVER_PATH)

    def test_still_answers_the_viewer_permission_question(self, payload):
        # Merging last must not clobber the server's authoritative answer to
        # what this viewer may see; every Profile OS destination gates on it.
        assert isinstance(payload["viewer_permissions"], dict)
        assert payload["viewer_permissions"], "empty permissions would lock the profile's own surfaces"


class TestAnOrdinaryMember:
    @pytest.fixture()
    def human(self, monolith, cursor, pulsedrop_user):
        return monolith.pulse_native_profile_payload(cursor, VIEWER_ID, OTHER_ID)

    def test_carries_none_of_the_overlay(self, human):
        assert OVERLAY_KEYS.isdisjoint(human)

    def test_omits_the_social_graph_flag_rather_than_denying_it(self, human):
        # Absent, specifically. The client reads ``!== false``, so a human
        # profile that shipped ``False`` here would lose its follower count.
        assert "has_social_graph" not in human

    def test_is_not_automated(self, human):
        assert not human.get("automated")


class TestTheTwoAutomatedAccountsDiffer:
    """PulseSoc Insight cannot be followed; PulseDrop is built to be.

    Asserted side by side because the failure being prevented is the two rules
    being collapsed back into one. Either direction of that collapse turns one
    of these two tests red.
    """

    @pytest.fixture()
    def insight(self, monolith, cursor, pulsedrop_user):
        # user_id 0 is MEMBER_000: a hand-written payload returned before the
        # function ever reaches the database.
        return monolith.pulse_native_profile_payload(cursor, 0, VIEWER_ID)

    def test_both_are_automated(self, insight, payload):
        assert insight["automated"] is payload["automated"] is True

    def test_only_pulsedrop_has_a_social_graph(self, insight, payload):
        assert insight["has_social_graph"] is False
        assert payload["has_social_graph"] is True

    def test_insight_omits_the_counts_it_cannot_have(self, insight):
        # It is not that they are zero. No ``pulse_follows`` row can reference
        # ``user_id=0``, so there is no number to report, and the flag above is
        # what tells the client to render nothing rather than "0".
        assert "follower_count" not in insight
        assert "following_count" not in insight

    def test_both_declare_a_fitted_cover_with_their_own_shape(self, insight, payload):
        assert insight["brand_cover_fit"] == payload["brand_cover_fit"] == "contain"
        assert insight["brand_cover_aspect_ratio"] == pytest.approx(1600 / 640)
        # Different files, different shapes. The app used to hold one ratio in a
        # stylesheet, which is why this inequality is worth stating out loud.
        assert insight["brand_cover_aspect_ratio"] != payload["brand_cover_aspect_ratio"]

    def test_insight_s_declared_shape_also_matches_its_file(self, insight, monolith):
        asset = REPO_ROOT / monolith.pulse_feed_engine.MEMBER_000_COVER_PATH.lstrip("/")
        assert asset.exists(), f"{asset} is missing; the cover URL points at nothing"
        assert insight["brand_cover_aspect_ratio"] == pytest.approx(_png_aspect_ratio(asset))


class TestWhenTheOverlayCannotBeBuilt:
    def test_the_profile_still_answers(self, monolith, cursor, pulsedrop_user, monkeypatch):
        """A broken disclosure must not 404 the account.

        The overlay is additive, so losing it costs the badge and the banner
        treatment — the profile underneath is complete, followable and public
        without it. Raising here instead would take out the whole profile
        endpoint for one bad import.
        """
        def explode():
            raise RuntimeError("overlay exploded")

        monkeypatch.setattr(account, "profile_overlay", explode)
        degraded = monolith.pulse_native_profile_payload(cursor, pulsedrop_user, VIEWER_ID)
        assert degraded["username"] == account.USERNAME
        assert degraded["follower_count"] == 2
        # And it is genuinely degraded, not silently fine — otherwise this test
        # would pass against a version that never consulted the overlay at all.
        assert "automation_disclosure" not in degraded
