"""Can an operator stop commerce on one surface without a deploy?

Until this file, no. `config.enabled()` was the only switch and it is
all-or-nothing, so "turn discovery off in Reels" meant a code change, a review,
and a Railway deploy — and Railway variables only reach a container at boot, so
even the deploy is not the fast path it sounds like.

That gap shaped the whole rollout plan rather than being a footnote in it. The
delivery report's staged order exists because each stage alters what feed, reels,
post-detail, Messenger, Marketplace and product-page users see, and the only
available undo was shipping again. Every stage had to be sized against "what are
we willing to un-ship by deploying" instead of "what do we want to learn".

So the tests here are not really about a comma-separated environment variable.
They are about four properties that make a kill switch worth the name:

1. **It is scoped.** Switching `reels` off leaves the other five serving. A
   switch that is coarser than the thing going wrong gets used late or not at all.
2. **It is free.** A disabled surface costs one env read and *zero* queries —
   pinned by a cursor and a connection that raise if anything touches them. A
   kill switch you hesitate to leave on is a kill switch you turn off too early.
3. **It is silent.** An operator-requested empty list is not an incident. The
   conftest guard in this package fails any test during which a fail-safe
   swallowed an exception, and these tests run under it, so "silent" here is
   checked by the harness as well as asserted directly.
4. **A typo is loud.** `disabled_surfaces` drops names it does not recognise,
   which means a misspelling leaves the surface **serving** — a kill switch
   failing open. That direction is chosen, not defaulted into, and
   :class:`TestATypoFailsOpenLoudly` is where the choice is written down so it
   cannot be quietly reversed by someone who reads the drop as a bug.

Deliberately *not* tested here, because it is not true: that the switch stops the
feed's `commerce_suitable` annotation. It does not. The annotation is a property
of the post, it costs no query, and a client that has no placements to place has
nothing to do with it either way.
"""

from __future__ import annotations

import logging

import pytest

from services.commerce_discovery import config, engine, schema


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

#: Every attribute touched on a forbidden cursor or connection, in order.
#:
#: Raising is not enough on its own, and that is not a theoretical worry — it was
#: measured. Moving the switch below `schema.ensure_schema` and re-running this
#: file turned exactly *one* of its tests red, and not either of the two that
#: exist to pin the placement: `ensure_schema` has its own `except Exception`
#: (`schema.py:351`), so it caught the `AssertionError`, logged
#: `COMMERCE_DISCOVERY_SCHEMA_FAILED` and returned `False`, and `_serve` then
#: returned `[]` — the very answer the test was asserting. The package's conftest
#: guard did not save it either: that guard watches for
#: `COMMERCE_DISCOVERY_SERVE_FAILED`, which never fired, because the exception
#: never got as far as `serve`'s fail-safe.
#:
#: So contact is recorded somewhere no `except` between here and the assertion
#: can reach. The list is the evidence; the raise only stops the run early and
#: names the caller.
TOUCHES: list[str] = []


class ForbiddenCursor:
    """Anything at all raises, and records that it was asked. Proves a path is
    not reached.

    Not a Mock: a Mock answers every attribute with another Mock, so a path that
    *did* touch the database would keep running and fail somewhere later, or not
    at all. This fails at the first contact, and names which one.
    """

    def __getattr__(self, name):
        TOUCHES.append(f"cursor.{name}")
        raise AssertionError(
            f"the disabled-surface check ran too late: something called "
            f"cursor.{name}(). It must return before any database access."
        )


class ForbiddenConnection:
    def __getattr__(self, name):
        TOUCHES.append(f"conn.{name}")
        raise AssertionError(
            f"the disabled-surface check ran too late: something called "
            f"conn.{name}(). `schema.ensure_schema(conn)` is the likely caller, "
            f"which means the check moved below it."
        )


def serve_with_nothing(surface: str):
    """`engine.serve` with a cursor and connection that must not be used.

    Only ever called with the surface switched off — by the per-surface list or
    by the master switch — so the no-contact assertion belongs here rather than
    in each test. Every caller below therefore pins the placement, not just the
    two tests that mention it.
    """
    result = engine.serve(
        ForbiddenCursor(),
        9001,
        surface,
        conn=ForbiddenConnection(),
        parse_price=lambda label, currency=None: (100, currency or "USD"),
    )
    assert TOUCHES == [], (
        f"a disabled surface touched the database: {TOUCHES}. The switch must be "
        f"checked before `promotion.assert_unpaid`, `schema.ensure_schema` and "
        f"`preferences.viewer_policy`. Note the return value is still `[]` when "
        f"this fires — every fail-soft handler on the way out produces the same "
        f"answer as success, which is why this cannot be an assertion on the "
        f"result."
    )
    return result


@pytest.fixture(autouse=True)
def forget_touches():
    """`TOUCHES` is module state, like the warn-once memo below it."""
    TOUCHES.clear()
    yield
    TOUCHES.clear()


@pytest.fixture(autouse=True)
def forget_warned_values():
    """The warn-once memo is module state; a leak would make ordering matter.

    Cleared before *and* after: before so this file's assertions do not depend on
    whether an earlier file happened to set the variable, and after so it cannot
    silence a warning a later file is asserting on.
    """
    config._SURFACE_KILL_WARNED.clear()
    yield
    config._SURFACE_KILL_WARNED.clear()


# --------------------------------------------------------------------------- #


class TestTheDefaultIsEverythingOn:
    def test_no_variable_disables_nothing(self, monkeypatch):
        monkeypatch.delenv(config.SURFACE_KILL_ENV, raising=False)
        assert config.disabled_surfaces() == frozenset()

    def test_every_surface_is_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv(config.SURFACE_KILL_ENV, raising=False)
        # Asserted over the registry rather than a written-out list, so a seventh
        # surface added to `schema.SURFACES` is covered the day it lands.
        for surface in schema.SURFACES:
            assert config.surface_enabled(surface), surface

    def test_an_empty_or_whitespace_value_disables_nothing(self, monkeypatch):
        for raw in ("", "   ", ",", " , , "):
            monkeypatch.setenv(config.SURFACE_KILL_ENV, raw)
            assert config.disabled_surfaces() == frozenset(), repr(raw)


class TestTheSwitchIsScoped:
    def test_one_surface_off_leaves_the_others_serving(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "reels")
        assert config.disabled_surfaces() == frozenset({"reels"})
        assert not config.surface_enabled("reels")
        for surface in schema.SURFACES:
            if surface != "reels":
                assert config.surface_enabled(surface), surface

    def test_every_declared_surface_can_actually_be_named(self, monkeypatch):
        """Each name in the registry survives the parser.

        `post_detail` and `product_detail` are the ones that could not: an
        underscore is not a separator here, and a parser that split on one would
        silently disable nothing while looking like it worked.
        """
        for surface in schema.SURFACES:
            monkeypatch.setenv(config.SURFACE_KILL_ENV, surface)
            assert config.disabled_surfaces() == frozenset({surface}), surface
            assert not config.surface_enabled(surface), surface

    def test_several_surfaces_at_once(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "feed,reels,post_detail")
        assert config.disabled_surfaces() == frozenset({"feed", "reels", "post_detail"})
        assert config.surface_enabled("marketplace")

    def test_the_whole_registry_can_be_named_which_equals_the_master_switch(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, ",".join(schema.SURFACES))
        for surface in schema.SURFACES:
            assert not config.surface_enabled(surface), surface


class TestTheFormatIsForgivingAboutEverythingExceptNames:
    @pytest.mark.parametrize("raw", [
        "reels",
        " reels ",
        "REELS",
        "Reels",
        "reels,",
        ",reels",
        "reels feed",          # spaces
        "reels,feed",          # commas
        "reels, feed",         # both
        "  reels ,  feed  ",   # both, untidily
        "reels\tfeed",         # a tab, which is what a copied Railway value gives you
        "reels,reels",         # duplicated
    ])
    def test_shapes_an_operator_will_actually_type(self, monkeypatch, raw):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, raw)
        disabled = config.disabled_surfaces()
        assert "reels" in disabled, raw
        assert not config.surface_enabled("reels"), raw

    def test_case_and_padding_are_normalized_on_the_query_side_too(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "reels")
        for asked in ("reels", "REELS", " Reels ", "ReElS"):
            assert not config.surface_enabled(asked), asked

    def test_a_blank_surface_name_is_not_enabled(self, monkeypatch):
        """`surface_enabled("")` must not answer True by failing to match.

        Not a real request shape — `_serve` rejects it against the registry first
        — but the function is public and a caller that passed `None` through
        should not get a yes.
        """
        monkeypatch.delenv(config.SURFACE_KILL_ENV, raising=False)
        # Empty is not in `disabled_surfaces`, so this *is* True, and that is
        # correct for this function: it answers "has this been switched off",
        # not "is this a surface". The registry check is `_serve`'s job and the
        # next test pins that the two together refuse.
        assert config.surface_enabled("") is True

    def test_an_unknown_surface_is_still_refused_by_the_engine(self):
        assert serve_with_nothing("") == []
        assert serve_with_nothing("not_a_surface") == []


class TestATypoFailsOpenLoudly:
    """The deliberate, uncomfortable choice — written down so it stays deliberate.

    An unrecognised name is dropped, so the surface the operator meant to stop
    keeps serving. The alternative is treating an unknown token as "disable
    everything", which turns one typo into a platform-wide commerce outage. There
    is no third option: nothing here can know whether `post-detail` meant
    `post_detail` or was a name someone invented.

    If a future change makes an unknown token fail *closed*, these tests should
    be rewritten with an argument, not deleted.
    """

    def test_an_unknown_name_does_not_disable_anything(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "reel")   # missing the s
        assert config.disabled_surfaces() == frozenset()
        assert config.surface_enabled("reels")

    def test_an_unknown_name_does_not_take_a_valid_one_with_it(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "post-detail,reels")
        assert config.disabled_surfaces() == frozenset({"reels"})
        assert config.surface_enabled("post_detail")

    def test_it_warns_and_names_the_token_and_the_valid_set(self, monkeypatch, caplog):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "post-detail")
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.config"):
            config.disabled_surfaces()
        messages = [record.getMessage() for record in caplog.records]
        assert any("COMMERCE_DISCOVERY_UNKNOWN_DISABLED_SURFACE" in m for m in messages)
        joined = " ".join(messages)
        assert "post-detail" in joined
        # The valid set has to be in the line. An operator reading "unknown
        # surface" at 3am without the alternatives has to go and find the source.
        assert "post_detail" in joined
        assert "still_serving=1" in joined

    def test_it_warns_once_per_value_not_once_per_request(self, monkeypatch, caplog):
        """This is read on the feed path; a per-request warning is a log flood."""
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "reel")
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.config"):
            for _ in range(25):
                config.disabled_surfaces()
        hits = [r for r in caplog.records
                if "COMMERCE_DISCOVERY_UNKNOWN_DISABLED_SURFACE" in r.getMessage()]
        assert len(hits) == 1

    def test_changing_the_value_warns_again(self, monkeypatch, caplog):
        """Memoized on the raw string, so fixing the variable is observable too.

        Keying on the bad *token* instead would mean an operator who corrects
        `reel` to `reels` and then mistypes `messengr` gets no warning for the
        second mistake if the first is still in the set. Keying on the whole raw
        value makes every distinct configuration report itself exactly once.
        """
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.config"):
            monkeypatch.setenv(config.SURFACE_KILL_ENV, "reel")
            config.disabled_surfaces()
            config.disabled_surfaces()
            monkeypatch.setenv(config.SURFACE_KILL_ENV, "messengr")
            config.disabled_surfaces()
        hits = [r for r in caplog.records
                if "COMMERCE_DISCOVERY_UNKNOWN_DISABLED_SURFACE" in r.getMessage()]
        assert len(hits) == 2

    def test_a_valid_only_value_warns_about_nothing(self, monkeypatch, caplog):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "reels,feed")
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.config"):
            config.disabled_surfaces()
        assert not [r for r in caplog.records
                    if "COMMERCE_DISCOVERY_UNKNOWN_DISABLED_SURFACE" in r.getMessage()]


class TestTheMasterSwitchStillWins:
    def test_master_off_disables_every_surface(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_ENABLED", "false")
        monkeypatch.delenv(config.SURFACE_KILL_ENV, raising=False)
        for surface in schema.SURFACES:
            assert not config.surface_enabled(surface), surface

    def test_master_off_does_not_need_the_per_surface_variable_to_be_valid(self, monkeypatch):
        """Both switches in one call so a caller cannot check one and forget.

        `surface_enabled` returns before parsing, so a malformed per-surface
        value cannot resurrect a surface while the master switch is off — which
        is the ordering bug that having two switches invites.
        """
        monkeypatch.setenv("COMMERCE_DISCOVERY_ENABLED", "0")
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "total,,,nonsense")
        assert not config.surface_enabled("feed")

    def test_master_on_is_the_production_default(self, monkeypatch):
        monkeypatch.delenv("COMMERCE_DISCOVERY_ENABLED", raising=False)
        monkeypatch.delenv(config.SURFACE_KILL_ENV, raising=False)
        assert config.enabled()
        assert config.surface_enabled("feed")


class TestADisabledSurfaceCostsNothing:
    """The check is above `ensure_schema` and above `viewer_policy`.

    Proved by handing `engine.serve` a cursor and a connection that record and
    then raise on any attribute access, and asserting on the record rather than
    on the raise. The distinction is the whole point: this package is three
    layers of fail-soft by design (§82), so an exception raised inside `_serve`
    is caught by *something* and converted into an empty list, which is exactly
    what a working disabled surface returns. The first version of these tests
    asserted only the empty list and stayed green when the switch was moved below
    `ensure_schema`. See `TOUCHES`.
    """

    def test_a_disabled_surface_opens_no_connection_and_runs_no_query(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "feed")
        assert serve_with_nothing("feed") == []

    def test_every_surface_is_free_when_disabled(self, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, ",".join(schema.SURFACES))
        for surface in schema.SURFACES:
            assert serve_with_nothing(surface) == [], surface

    def test_the_master_switch_is_free_too_now(self, monkeypatch):
        """It was not, before this change.

        `config.enabled()` is checked inside `preferences.viewer_policy`, which
        runs after `ensure_schema(conn)` and takes a cursor. So with discovery
        globally off, every request still ran the schema guard and opened a
        cursor to be told no. Checking both switches at the top of `_serve`
        fixes that for the master switch as a side effect, and this test is here
        so the side effect does not get lost: it fails if `surface_enabled` is
        ever narrowed to only consult the per-surface list.
        """
        monkeypatch.setenv("COMMERCE_DISCOVERY_ENABLED", "false")
        assert serve_with_nothing("feed") == []


class TestADisabledSurfaceIsSilent:
    """An operator-requested empty list is not an incident.

    These tests also run under the package's `swallowed_serve_failures` guard,
    so a disabled surface that reached the fail-safe would fail them even if the
    assertions below passed — the guard reports on any test during which
    `COMMERCE_DISCOVERY_SERVE_FAILED` or `..._SERVE_ROUTE_FAILED` was logged.
    """

    def test_nothing_is_logged_at_warning_or_above(self, monkeypatch, caplog):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "feed")
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.engine"):
            assert serve_with_nothing("feed") == []
        assert caplog.records == []

    def test_the_fail_safe_did_not_fire(self, monkeypatch, caplog):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "reels")
        with caplog.at_level(logging.DEBUG, logger="services.commerce_discovery.engine"):
            assert serve_with_nothing("reels") == []
        assert not [r for r in caplog.records
                    if "COMMERCE_DISCOVERY_SERVE_FAILED" in r.getMessage()]


class TestTheRealEngineAgrees:
    """Against the simulated marketplace, not a stub — the switch has to work on
    a surface that would otherwise return products, or it proves nothing.

    The `market` fixture seeds its own catalogue (100 listings, 10 sellers) and
    raises the per-session caps, so these tests do not call `seed` themselves:
    a second seed collides on `users.user_id` rather than adding to the first.
    """

    def test_a_surface_that_serves_stops_serving(self, market, monkeypatch):
        monkeypatch.delenv(config.SURFACE_KILL_ENV, raising=False)
        before = market.serve("feed")
        assert before, "fixture problem: feed served nothing even with the switch on"

        monkeypatch.setenv(config.SURFACE_KILL_ENV, "feed")
        assert market.serve("feed") == []

    def test_switching_one_surface_off_does_not_touch_another(self, market, monkeypatch):
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "feed")
        assert market.serve("feed") == []
        assert market.serve("marketplace"), (
            "marketplace stopped serving when only feed was disabled — the switch "
            "is being read as global"
        )

    def test_turning_it_back_on_needs_no_restart(self, market, monkeypatch):
        """The whole point: no deploy, no client release, no process restart.

        The flag is read per request, so this is three `serve` calls in one
        process with the variable changing between them.
        """
        monkeypatch.setenv(config.SURFACE_KILL_ENV, "feed")
        assert market.serve("feed") == []

        monkeypatch.delenv(config.SURFACE_KILL_ENV, raising=False)
        assert market.serve("feed"), "the surface did not come back within the process"
