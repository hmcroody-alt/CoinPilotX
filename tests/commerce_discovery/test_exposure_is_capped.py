"""``config.product_cap()`` named a ceiling that nothing enforced.

It was referenced in exactly one place: as the denominator of
``ranking.repetition_penalty``. So it set the *slope* of a de-ranking term and
nothing else. Past the cap the penalty saturated, which meant a fourth sighting
and a fortieth scored identically — and since a saturated penalty is a constant,
fatigue stopped escalating at precisely the point it needed to start.

``repetition_penalty``'s own docstring asserted that "the frequency filter in
``engine.py`` has removed the listing anyway". There was no frequency filter in
``engine.py``. The docstring described a design that had never been built, and
because it read as a statement of fact it also explained away the missing half
as somebody else's responsibility.

Measured before the fix, 200 pages two minutes apart, all inside the 7-day
product window, against a configured cap of 3:

    surface         catalogue=2   catalogue=6   catalogue=25
    feed                3x            4x            3x
    marketplace        17x           17x           17x
    product_detail     10x           10x            7x
    reels               1x            1x            1x

Seventeen sightings of one product against a cap of three. The shopping surfaces
are worst because they are where the relevance floor is lowest
(``marketplace`` −0.15) *and* where ``pool.build``'s relaxation ladder cuts the
cooldown hardest. Reels never came close, because its 0.55 floor and long
cooldown bind long before any volume limit would.

That distribution is the whole argument for where the fix goes. Spacing
controls — cooldowns — are preferences, and ``pool.build`` is right to trade them
away on a thin catalogue: a product shown sooner than preferred is still a
product the viewer has not exhausted. Volume is a different claim. "How many
times in total" does not become negotiable because the catalogue is small; a
small catalogue is an argument for showing *fewer* placements, not for showing
the same one seventeen times. So the cap is enforced in ``pool._reject`` as a
drop, it is absent from the relaxation ladder, and it is not a penalty.

The tests below are in three groups: that the ceiling exists and binds, that it
is per-surface, and — the group most likely to catch a future regression — that
it did not quietly break the things the ladder was protecting.
"""

from __future__ import annotations

from collections import Counter

import pytest

from services.commerce_discovery import config, exposure, pool, preferences, router

SURFACES = ("feed", "marketplace", "reels", "product_detail")


def _worst(market, surface: str, *, catalogue: int, steps: int = 200) -> Counter:
    """Sightings per listing after a long session on a squeezed catalogue.

    Two minutes per step keeps the whole run inside
    ``config.product_window_seconds()`` (7 days), so nothing decays out of the
    exposure count and the count the pool reads is the count this returns.
    """
    cur = market.conn.cursor()
    cur.execute("DELETE FROM marketplace_listings WHERE id > ?", (catalogue,))
    market.conn.commit()

    start = len(market.log)
    for _ in range(steps):
        market.render(market.serve(surface))
        market.clock.advance(120.0)
    return Counter(row["listing_id"] for row in market.log[start:])


class TestTheCeilingBinds:
    """No amount of scrolling gets a viewer past their allowance."""

    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("catalogue", [2, 6, 25])
    def test_no_product_is_shown_more_than_its_surface_allows(self, market, surface, catalogue):
        cap = router.policy_for(surface).product_cap
        seen = _worst(market, surface, catalogue=catalogue)
        worst = max(seen.values(), default=0)
        assert worst <= cap, (
            f"{surface} showed one product {worst} times against a cap of {cap} "
            f"on a catalogue of {catalogue}"
        )

    def test_a_two_product_catalogue_cannot_be_stretched_by_scrolling(self, market):
        # The sharpest form of the original defect. With two listings and 200
        # pages, the only ways to fill a page are to repeat or to serve nothing —
        # and serving nothing is the correct answer.
        seen = _worst(market, "marketplace", catalogue=2)
        cap = router.policy_for("marketplace").product_cap
        assert sum(seen.values()) <= 2 * cap
        assert max(seen.values(), default=0) == cap, (
            "the cap should be reached — a test where it is never approached "
            "would pass with the enforcement deleted"
        )

    def test_an_at_cap_row_is_rejected_under_its_own_code(self, market):
        # `_reject` is the enforcement — `_hard_exclusions` is documented in the
        # module as "an optimisation, never the enforcement". So this asks the
        # enforcing function directly, with spacing switched off, so that volume
        # is the only possible reason for the answer.
        # Exhausted on the *marketplace* and then asked about the **feed**, which
        # makes this a cross-surface case as well: `product_seen` counts every
        # surface, so a shopper who spends their allowance in the shop goes quiet
        # in the feed too. That falls out of the design rather than needing its
        # own mechanism, and it is the behaviour the brief asks for.
        _worst(market, "marketplace", catalogue=6)

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure.load(cur, policy.subject_ref, market.viewer_id)

        exhausted = [
            listing_id for listing_id in state.recent_products
            if state.product_seen(listing_id) >= 3 and listing_id not in state.purchased
        ]
        assert exhausted, "the browse did not exhaust anything; the case is vacuous"

        cur.execute("SELECT * FROM marketplace_listings WHERE id = ?", (exhausted[0],))
        row = dict(zip([c[0] for c in cur.description], cur.fetchone()))

        code = pool._reject(
            row,
            policy=policy,
            state=state,
            surface="feed",
            parse_price=None,
            product_cooldown=0,  # spacing off
            seller_cooldown=0,
            product_cap=3,
            seen_ids=set(),
        )
        assert code == "product_cap", f"expected a product_cap drop, got {code!r}"

    @pytest.mark.parametrize("sightings,expected", [(2, ""), (3, "product_cap"), (4, "product_cap")])
    def test_the_boundary_is_the_cap_itself_and_not_one_past_it(self, market, sightings, expected):
        # The count is set exactly rather than browsed for, because browsing
        # cannot hit a chosen number. The test above asks about a row the viewer
        # has seen *six* times against a cap of three, so `>= cap` and `> cap`
        # give the same answer and an off-by-one is invisible to it — which is
        # what happened: a `>=` to `>` mutant that used to die started surviving
        # the moment escalating spacing stopped sessions from running so far past
        # the cap. The boundary needs pinning at the boundary.
        #
        # `sightings == cap` is the case that matters. The cap is a count of
        # sightings the viewer is *allowed*, so having had exactly that many
        # means the allowance is spent, not that one more is due.
        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure.load(cur, policy.subject_ref, market.viewer_id)
        cur.execute("SELECT * FROM marketplace_listings LIMIT 1")
        row = dict(zip([c[0] for c in cur.description], cur.fetchone()))

        state.product_counts[int(row["id"])] = sightings
        code = pool._reject(
            row,
            policy=policy,
            state=state,
            surface="feed",
            parse_price=None,
            product_cooldown=0,  # spacing off, so volume is the only answer
            seller_cooldown=0,
            product_cap=3,
            seen_ids=set(),
        )
        assert code == expected, (
            f"a listing seen {sightings}x against a cap of 3 answered {code!r}, "
            f"expected {expected!r}"
        )

    def test_the_sql_exclusion_is_an_optimisation_and_not_the_enforcement(self, market, monkeypatch):
        # `_hard_exclusions` truncates at MAX_SQL_EXCLUSIONS, so on a viewer with
        # a long history it *cannot* be the guarantee. Disabling it entirely must
        # change performance and nothing else.
        monkeypatch.setattr(pool, "_hard_exclusions", lambda *a, **kw: ())
        seen = _worst(market, "marketplace", catalogue=6)
        cap = router.policy_for("marketplace").product_cap
        assert max(seen.values(), default=0) <= cap, (
            "with the SQL shortcut removed, the Python enforcement must still hold"
        )

    def test_product_cap_is_a_recognised_drop_code(self):
        # The drop codes are what an operator reads. A code the metrics layer
        # does not know about is a silent drop.
        assert "product_cap" in pool.DROP_CODES

    def test_the_enforcing_function_cannot_be_called_without_a_cap(self):
        # A source-level check, because there is no behaviour to observe: every
        # call site passes the argument explicitly, so a default here would be
        # unreachable *today* and load-bearing the moment somebody adds a caller.
        # `_reject` is the enforcement, so a caller that forgets the cap must not
        # silently get "no cap" — it must fail to call.
        import inspect

        parameter = inspect.signature(pool._reject).parameters["product_cap"]
        assert parameter.default is inspect.Parameter.empty, (
            "_reject gained a default for product_cap; a forgotten argument now "
            "disables the cap instead of raising"
        )

    def test_a_caller_that_omits_the_cap_gets_the_configured_one(self, market):
        # The other half of that contract. `pool.build` *does* take a default,
        # because it is the public entry point and the safe answer there is the
        # configured cap rather than a refusal. `None` must mean "the config's
        # value", never "unlimited".
        _worst(market, "marketplace", catalogue=6)

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure.load(cur, policy.subject_ref, market.viewer_id)
        built = pool.build(
            cur,
            viewer_user_id=market.viewer_id,
            policy=policy,
            exposure=state,
            parse_price=None,
            surface="feed",
            product_cooldown=0,
            seller_cooldown=0,
            target=10,
            rotation_offset=0,
            # product_cap deliberately omitted
        )
        assert built.rows == (), (
            "omitting product_cap fell back to no cap at all; every listing had "
            f"already been seen {config.product_cap()}+ times"
        )


class TestTheCeilingIsPerSurface:
    """One global number, scaled by what the surface is for."""

    def test_the_shopping_surfaces_allow_more_than_the_social_ones(self):
        caps = {surface: router.policy_for(surface).product_cap for surface in SURFACES}
        assert caps["marketplace"] > caps["feed"] > caps["reels"], caps
        assert caps["product_detail"] == caps["marketplace"], (
            "both are surfaces the shopper arrived at to shop"
        )

    @pytest.mark.parametrize("global_cap", [1, 2, 3, 10])
    def test_every_surface_allows_at_least_one_sighting(self, monkeypatch, global_cap):
        monkeypatch.setattr(config, "product_cap", lambda: global_cap)
        for surface in SURFACES + ("post_detail", "messenger", "nonexistent_surface"):
            cap = router.policy_for(surface).product_cap
            assert cap >= 1, f"{surface} was capped at {cap} with a global cap of {global_cap}"

    def test_the_configured_cap_is_itself_floored_at_one(self, monkeypatch):
        # This is the floor that is actually load-bearing today. `router` scales
        # the configured value, so if the configured value could be 0 or negative
        # every surface would scale to 0 — and a cap of 0 is not a strict cap, it
        # is a total withdrawal of commerce dressed up as anti-repetition.
        for hostile in ("0", "-5", "", "not a number"):
            monkeypatch.setenv("COMMERCE_DISCOVERY_PRODUCT_CAP", hostile)
            assert config.product_cap() >= 1, hostile

    def test_a_surface_factor_that_rounds_to_zero_is_floored(self, monkeypatch):
        # The `minimum=1` in `router.policy_for`, tested at a boundary that can
        # actually move. It cannot bind at present — the configured cap is floored
        # at 1 above and the smallest factor is 0.67, so `round(1 * 0.67)` is 1 —
        # which means it is defence in depth for a factor somebody lowers later.
        # Monkeypatching the factor is what makes that defence observable rather
        # than merely asserted; without this case the floor is untested code that
        # reads as if it were tested.
        monkeypatch.setattr(config, "product_cap", lambda: 1)
        monkeypatch.setitem(router._PRODUCT_CAP_FACTORS, "reels", 0.1)
        assert router.policy_for("reels").product_cap >= 1, (
            "a low surface factor silently turned Reels commerce off entirely"
        )

    def test_the_global_cap_moves_every_surface(self, monkeypatch):
        monkeypatch.setattr(config, "product_cap", lambda: 10)
        caps = {surface: router.policy_for(surface).product_cap for surface in SURFACES}
        assert min(caps.values()) > 3, (
            f"raising the global cap must raise the per-surface caps; got {caps}"
        )

    def test_an_unknown_surface_gets_the_feed_allowance(self):
        # Fail-safe direction: an unrecognised surface should get the stricter
        # social allowance, not the permissive shopping one.
        assert router.policy_for("something_new").product_cap == router.policy_for("feed").product_cap


class TestTheAllowanceIsSpentOnceAcrossAllSurfaces:
    """Cross-surface fatigue, which the brief asks for as a separate subsystem.

    It is not one here, and deliberately. ``ExposureState.product_seen`` has
    always counted impressions on *every* surface inside the product window, so a
    per-surface cap read against a cross-surface count gives cross-surface
    fatigue by construction. A second mechanism keyed on the same events would
    be two places to keep in agreement for no additional behaviour.
    """

    def test_a_shopper_who_exhausts_the_shop_goes_quiet_in_the_feed(self, market):
        # marketplace allows 6, feed allows 3. Spend all six in the shop and the
        # feed has nothing left to offer — the shop's allowance is not a separate
        # budget from the feed's, it is the same budget spent elsewhere.
        _worst(market, "marketplace", catalogue=6)
        assert market.serve("feed") == [], (
            "the feed re-served products the viewer had already seen six times "
            "in the marketplace"
        )

    def test_spending_the_feed_allowance_does_not_close_the_shop(self, market):
        # The converse must *not* hold. The feed's smaller allowance is a
        # statement about the feed, and a viewer who has used it up has spent 3
        # of the shop's 6 — so the shop still has something to show. A cap that
        # locked the shop after three feed impressions would be a revenue bug
        # wearing anti-repetition clothing.
        _worst(market, "feed", catalogue=6)
        assert market.serve("marketplace"), (
            "three feed impressions must not empty a marketplace that allows six"
        )


class TestWhatTheCeilingMustNotHaveBroken:
    """The cap sits upstream of everything. These are the collateral checks."""

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_healthy_catalogue_still_fills_pages(self, market, surface):
        # If the cap were too aggressive, or applied against the wrong count, the
        # symptom would be an empty surface rather than an exception.
        seen = _worst(market, surface, catalogue=25, steps=10)
        assert sum(seen.values()) > 0, f"{surface} served nothing at all"

    def test_the_cap_does_not_reduce_how_much_of_the_catalogue_is_reached(self, market):
        # The cap removes repeats, and removing repeats should *widen* coverage,
        # never narrow it. This is the property that would break if the cap were
        # accidentally excluding by seller or by category.
        seen = _worst(market, "feed", catalogue=25)
        assert len(seen) >= 20, f"only {len(seen)} of 25 listings were ever reached"

    def test_an_exhausted_viewer_is_served_nothing_rather_than_a_repeat(self, market):
        seen = _worst(market, "marketplace", catalogue=2)
        cap = router.policy_for("marketplace").product_cap
        # Everything is at cap now. One more page must be empty.
        assert market.serve("marketplace") == [], (
            "a viewer who has exhausted the whole catalogue must be served "
            "nothing; anything else is the defect this file exists for"
        )
        assert all(count <= cap for count in seen.values())

    def test_the_cap_survives_a_degraded_exposure_read(self, market, monkeypatch):
        # `ExposureState.degraded` means the memory read failed. The pipeline is
        # documented as continuing with weaker anti-repetition in that case —
        # weaker, but it must not raise, and the cap must not be the thing that
        # turns a degraded read into an outage.
        real_load = exposure.load

        def degraded(cur, subject_ref, user_id):
            state = real_load(cur, subject_ref, user_id)
            return state.__class__(**{**state.__dict__, "degraded": True})

        monkeypatch.setattr(exposure, "load", degraded)
        assert market.serve("feed") is not None
