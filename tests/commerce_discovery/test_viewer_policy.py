"""Whether a user's "no" is honoured, and how far it reaches.

This is the consent layer, and it is the only part of the engine where being
wrong is not a quality problem. Everything else in this package can degrade to
"show nothing" and the worst outcome is an empty shelf. Here, degrading the
wrong way means showing commerce to someone who switched it off.

Three properties are worth stating, because none of them is visible in the
signature:

* **Refusal is scoped, failure is not.** The master switch and the snooze are
  the user saying "stop recommending to me while I'm doing something else" —
  they govern feed, reels and messenger, and Marketplace keeps working, which
  is what the settings screen promises in as many words. An unreadable
  preferences row is a different thing entirely and stops everything.

* **The keys are a wire contract with TypeScript.** Nothing on this side writes
  ``marketplaceRecommendations``; ``mobile-native/src/settings/schema.ts`` does,
  and this module reads whatever lands in the synced document. Renaming a key
  on either side breaks no build — it silently stops a refusal being honoured.
  So the spellings are asserted literally.

* **An absent group is not a refusal.** A document written by a build that
  predates the group has no ``commerce`` key at all, and reading that as "off"
  would silently disable the feature for every user who had not yet opened
  Settings.
"""

from datetime import timedelta

import pytest

from services.commerce_discovery import preferences, subject

SOCIAL = ("feed", "reels", "messenger")


class FakeCursor:
    """Just enough cursor for ``_load_suppressions``.

    ``rows`` are returned verbatim for the one SELECT this module issues. A
    subclass raising instead is how the fail-closed paths are reached, since
    there is no legitimate input that produces them.
    """

    def __init__(self, rows=None):
        self.rows = rows or []

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return list(self.rows)


class RaisingCursor(FakeCursor):
    def execute(self, *_args, **_kwargs):
        raise RuntimeError("suppression table is unreadable")


def loader(commerce=None, *, whole_document=None):
    """Stand-in for ``pulse_settings_routes.load_preferences``.

    Injected rather than patched because the real one imports ``bot``, and a
    consent unit test should not boot a 111k-line monolith to find out whether
    a user said no.
    """
    document = whole_document if whole_document is not None else {}
    if commerce is not None:
        document = dict(document, commerce=commerce)

    def load(_cur, _user_id):
        return document, 1, None

    return load


def policy(commerce=None, *, rows=None, cur=None, whole_document=None):
    return preferences.viewer_policy(
        cur if cur is not None else FakeCursor(rows),
        7,
        load_preferences=loader(commerce, whole_document=whole_document),
    )


def future_iso(seconds=3600):
    return subject.expiry_iso(seconds)


def past_iso():
    # Built by hand rather than with `expiry_iso(-3600)`: that helper clamps
    # with `max(1, seconds)`, so a negative TTL lands one second in the *future*
    # and a test using it would assert the opposite of what it reads as.
    return subject.iso(subject.now_utc() - timedelta(hours=1))


# --------------------------------------------------------------------------- #
#                            The wire contract                                 #
# --------------------------------------------------------------------------- #


def test_reads_the_four_keys_the_client_writes():
    """The spellings, asserted literally, because a rename fails silently.

    `schema.ts` has the mirror of this test. Between them, renaming a key on
    either side turns into a red test rather than a preference that stops being
    honoured with nothing to notice.
    """
    resolved = policy(
        {
            "marketplaceRecommendations": True,
            "personalizedRecommendations": False,
            "frequency": "low",
            "snoozeUntil": "",
        }
    )
    assert resolved.allows_surface("feed") is True
    assert resolved.personalized is False
    assert resolved.frequency == "low"


def test_a_document_without_the_group_is_not_a_refusal():
    resolved = policy(whole_document={"appearance": {"theme": "dark"}})
    assert resolved.allowed is True
    assert all(resolved.allows_surface(s) for s in SOCIAL)
    assert resolved.allows_surface("marketplace") is True
    assert resolved.personalized is True
    assert resolved.frequency == "balanced"


def test_a_group_that_is_not_a_dict_is_ignored_rather_than_obeyed():
    # A corrupt shape is not a user decision. It also must not throw: this runs
    # on the serve path for every request.
    assert policy("off").allowed is True


# --------------------------------------------------------------------------- #
#                      The master switch, and its scope                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("surface", SOCIAL)
def test_the_master_switch_stops_every_social_surface(surface):
    resolved = policy({"marketplaceRecommendations": False})
    assert resolved.allows_surface(surface) is False
    assert resolved.blocked_reason == "user_disabled"


def test_the_master_switch_leaves_marketplace_working():
    """The promise the settings screen makes, asserted here.

    "Marketplace keeps recommending products inside Marketplace either way" is
    printed under the switch. This resolved to a global deny before — the shop
    went empty, and the screen was telling the user it would not.
    """
    assert policy({"marketplaceRecommendations": False}).allows_surface("marketplace") is True


def test_a_refusal_still_resolves_the_rest_of_the_viewer():
    """Because Marketplace is still being served, and has to be served right.

    Returning early on the master switch would leave the shop with default
    frequency, default personalization and no suppressions — so a user who had
    switched discovery off would find "don't recommend this seller" quietly
    stop applying inside Marketplace.
    """
    resolved = policy(
        {
            "marketplaceRecommendations": False,
            "personalizedRecommendations": False,
            "frequency": "low",
        },
        rows=[{"scope": "seller", "ref": "42", "source_action": "hide_seller"}],
    )
    assert resolved.personalized is False
    assert resolved.frequency == "low"
    assert 42 in resolved.suppressed_sellers


def test_only_an_explicit_false_is_a_refusal():
    # `is False`, not falsiness: a missing key, a null, or an empty string are
    # all "not set", and must read as the default rather than as "off".
    for value in (None, "", 0):
        assert policy({"marketplaceRecommendations": value}).allows_surface("feed") is True


# --------------------------------------------------------------------------- #
#                                 The snooze                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("surface", SOCIAL)
def test_an_unexpired_snooze_stops_the_social_surfaces(surface):
    resolved = policy({"snoozeUntil": future_iso()})
    assert resolved.allows_surface(surface) is False
    assert resolved.blocked_reason == "snoozed"


def test_a_snooze_leaves_marketplace_working():
    assert policy({"snoozeUntil": future_iso()}).allows_surface("marketplace") is True


def test_a_lapsed_snooze_is_not_a_snooze():
    """The stored instant is never cleared, so expiry is decided on every read.

    `schema.ts` deliberately keeps a lapsed timestamp rather than blanking it —
    normalizing against the wall clock would turn the passage of time into a
    diff. That makes this check the thing standing between the user and a pause
    that never ends.
    """
    resolved = policy({"snoozeUntil": past_iso()})
    assert resolved.allows_surface("feed") is True
    assert resolved.blocked_reason == ""


def test_an_unreadable_snooze_does_not_become_a_permanent_pause():
    # `subject.is_expired` fails closed on a value it cannot parse, so a corrupt
    # timestamp ends the pause rather than freezing it. The client normalizer is
    # strict on write precisely because this is the reading behaviour.
    assert policy({"snoozeUntil": "sometime next month"}).allows_surface("feed") is True


def test_the_master_switch_outranks_the_snooze_in_the_reason_code():
    # Both are off; the code reports the stronger one, because "they turned it
    # off" and "they paused it" mean different things to whoever reads the log.
    resolved = policy({"marketplaceRecommendations": False, "snoozeUntil": future_iso()})
    assert resolved.blocked_reason == "user_disabled"


# --------------------------------------------------------------------------- #
#                      Suppressions, and the "all" scope                       #
# --------------------------------------------------------------------------- #


def test_a_card_snooze_and_the_settings_snooze_reach_the_same_surfaces():
    """The two "hide for 30 days" controls, compared directly.

    One writes a ``scope="all"`` row from the card's ••• menu; the other writes
    ``commerce.snoozeUntil`` from Settings. They were resolved by different code
    paths with different scopes — the row spared Marketplace, the preference did
    not. A user pausing from a card and from Settings got two different results
    from one promise.
    """
    from_card = policy(rows=[{"scope": "all", "ref": "1", "source_action": "snooze"}])
    from_settings = policy({"snoozeUntil": future_iso()})

    for surface in SOCIAL + ("marketplace",):
        assert from_card.allows_surface(surface) is from_settings.allows_surface(surface)


def test_a_surface_suppression_stays_narrow():
    resolved = policy(rows=[{"scope": "surface", "ref": "reels", "source_action": "hide"}])
    assert resolved.allows_surface("reels") is False
    assert resolved.allows_surface("feed") is True
    assert resolved.allows_surface("marketplace") is True


def test_see_fewer_biases_rather_than_bans():
    resolved = policy(rows=[{"scope": "product", "ref": "sneakers", "source_action": "see_fewer"}])
    assert resolved.allows_surface("feed") is True
    assert resolved.suppressed_listings == frozenset()
    assert resolved.hidden_strength({"category": "Sneakers"}) == pytest.approx(
        preferences.SEE_FEWER_WEIGHT
    )


# --------------------------------------------------------------------------- #
#                             Failure direction                                #
# --------------------------------------------------------------------------- #


def test_an_unreadable_preferences_document_stops_everything():
    """Not scoped. This is the asymmetry the module docstring claims.

    A refusal is the user's decision and applies where they meant it. A failed
    read is not a decision at all, and the only safe reading of "we do not know
    what this user consented to" is to show nothing anywhere.
    """

    def explode(_cur, _user_id):
        raise RuntimeError("settings row is corrupt")

    resolved = preferences.viewer_policy(FakeCursor(), 7, load_preferences=explode)
    assert resolved.allowed is False
    assert resolved.blocked_reason == "preferences_unreadable"
    assert not any(resolved.allows_surface(s) for s in SOCIAL + ("marketplace",))


def test_an_unreadable_suppression_table_stops_everything():
    resolved = policy({"marketplaceRecommendations": True}, cur=RaisingCursor())
    assert resolved.allowed is False
    assert resolved.blocked_reason == "suppressions_unreadable"
    assert resolved.allows_surface("marketplace") is False


def test_the_engine_switch_stops_everything(monkeypatch):
    monkeypatch.setattr(preferences.config, "enabled", lambda: False)
    resolved = policy({"marketplaceRecommendations": True})
    assert resolved.allowed is False
    assert resolved.blocked_reason == "engine_disabled"
    assert resolved.allows_surface("marketplace") is False


# --------------------------------------------------------------------------- #
#                          Frequency and personalization                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "frequency,expected",
    [("low", 0.4), ("balanced", 1.0), ("more", 1.6)],
)
def test_frequency_shapes_how_much_never_whether(frequency, expected):
    resolved = policy({"frequency": frequency})
    assert resolved.allows_surface("feed") is True
    assert resolved.budget_multiplier() == pytest.approx(expected)


def test_an_unknown_frequency_falls_back_rather_than_zeroing_the_budget():
    # A multiplier of 0 would be indistinguishable from the feature being
    # broken, for a value the client cannot even produce.
    assert policy({"frequency": "occasionally"}).budget_multiplier() == pytest.approx(1.0)


def test_turning_off_personalization_is_not_turning_off_discovery():
    # Two different objections. "Don't use my history" still gets suggestions,
    # ranked on popularity; answering it by serving nothing would be answering
    # a question the user did not ask.
    resolved = policy({"personalizedRecommendations": False})
    assert resolved.allows_surface("feed") is True
    assert resolved.personalized is False


def test_the_server_can_switch_personalization_off_for_everyone(monkeypatch):
    monkeypatch.setattr(preferences.config, "personalization_enabled", lambda: False)
    resolved = policy({"personalizedRecommendations": True})
    assert resolved.allows_surface("feed") is True
    assert resolved.personalized is False
