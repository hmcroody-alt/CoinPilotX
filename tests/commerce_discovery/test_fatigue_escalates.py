"""The cooldown was flat, so fatigue never escalated — it only ever gated.

``config.product_cooldown_seconds()`` set one gap and applied it to every
sighting equally. The brief asks for *escalating* fatigue cooldowns, and the
measurement says why that matters. 300 pages a minute apart, every sighting
inside the 7-day product window, recording the page number of each sighting so
the gaps can be read off directly:

    surface         gaps between sightings (minutes)   span 1st→last
    feed                    90, 90                          180m
    marketplace             23, 23, 23, 23, 23              115m
    product_detail          23, 23, 23, 23, 23              115m

Exactly uniform. The second gap and the fifth are the same size, so a product
the viewer has now seen five times is spaced no further out than one they had
seen once. And the consequence is the line on the right: a viewer spent a
product's entire seven-day allowance inside a single sitting — three feed
sightings in three hours, six marketplace sightings in under two — and then saw
nothing of it for a week. Those are the same impressions either way. Three
reminders across three days is a better sequence than three in one afternoon,
and it costs nothing to prefer it.

After ``pool._escalated``:

    feed                    90, 180                         270m
    marketplace             23, 45, 68, 90                  226m

Two things have to hold at once here, and they pull in opposite directions.
Spacing has to *grow*, or the escalation is decorative. But the allowance also
has to stay *reachable* — the last sighting must still land inside
``config.product_window_seconds()``, because that window is what the sighting
count itself decays over. An allowance that cannot be spent inside the window is
a silently lower cap than the one an operator configured, which is a worse defect
than the one being fixed: it would be invisible, because the cap would simply
never appear to be hit. That is the whole argument for linear growth over
exponential, and
``test_every_surface_can_still_spend_its_whole_allowance_inside_the_window``
is that argument in executable form.

The third property is the one that distinguishes this from the volume cap in
``test_exposure_is_capped.py``. A cap is a statement about volume and does not
yield to scarcity; ``pool.build``'s relaxation ladder leaves it alone. Escalation
is *spacing*, so it stays in the ladder's reach and a thin catalogue still gets
served. Both facts are tested below, because getting them the wrong way round is
the easiest available mistake: escalation outside the ladder would turn a
5-product shop into a blank page, and a cap inside it would restore the 17×
repetition the cap was added to stop.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from services.commerce_discovery import config, exposure, pool, preferences, router

SURFACES = ("feed", "marketplace", "reels", "product_detail")


def _sighting_steps(market, surface: str, *, catalogue: int, steps: int = 300,
                    step_seconds: float = 60.0) -> dict[int, list[int]]:
    """Which pages each listing appeared on, over a long single session.

    One minute per page for 300 pages is five hours — long enough for several
    escalation steps on every surface, and far inside the 7-day product window,
    so nothing decays out of the count the spacing is computed from.
    """
    cur = market.conn.cursor()
    cur.execute("DELETE FROM marketplace_listings WHERE id > ?", (catalogue,))
    market.conn.commit()

    steps_of: dict[int, list[int]] = defaultdict(list)
    for step in range(steps):
        mark = market.mark()
        market.render(market.serve(surface))
        for row in market.since(mark):
            steps_of[row["listing_id"]].append(step)
        market.clock.advance(step_seconds)
    return steps_of


def _gaps(steps: list[int]) -> list[int]:
    return [b - a for a, b in zip(steps, steps[1:])]


def _most_repeated(steps_of: dict[int, list[int]]) -> list[int]:
    """The pages of whichever listing this viewer saw most often."""
    assert steps_of, "nothing was served at all; the measurement is vacuous"
    return steps_of[max(steps_of, key=lambda key: len(steps_of[key]))]


class TestSpacingGrowsWithEachSighting:
    """The gap before the next sighting depends on how many came before."""

    def test_the_gap_widens_after_each_sighting(self, market):
        # The direct replacement for the measurement in the docstring. Before
        # `_escalated` this asserted nothing: every gap was identical, so
        # `>` failed and `>=` passed vacuously.
        steps = _most_repeated(_sighting_steps(market, "marketplace", catalogue=6))
        gaps = _gaps(steps)
        assert len(gaps) >= 3, (
            f"only {len(steps)} sightings, so there is no escalation to observe; "
            "this test would pass with the escalation deleted"
        )
        assert gaps[-1] > gaps[0], f"spacing did not grow: gaps were {gaps}"

    def test_no_gap_is_ever_shorter_than_the_one_before_it(self, market):
        # Monotonicity, which is the property an operator would actually notice
        # breaking. A single growing pair could happen by luck of the ranking.
        for surface in ("marketplace", "product_detail"):
            steps = _most_repeated(_sighting_steps(market, surface, catalogue=6))
            gaps = _gaps(steps)
            assert gaps == sorted(gaps), f"{surface} spacing went backwards: {gaps}"

    def test_the_first_repeat_is_not_delayed_beyond_the_configured_cooldown(self):
        # The escalation must start at the configured value, not above it.
        # `seen` is 1 when the viewer has seen the product once, so the gap
        # before the *second* sighting is exactly what an operator configured;
        # anything else would mean the config no longer names a real gap.
        cooldown = 600
        assert pool._escalated(cooldown, 1) == cooldown

    @pytest.mark.parametrize("seen,expected", [(0, 600), (1, 600), (2, 1200), (3, 1800), (5, 3000)])
    def test_the_multiplier_is_the_number_of_previous_sightings(self, seen, expected):
        assert pool._escalated(600, seen) == expected

    def test_an_unseen_product_is_not_held_at_all(self):
        # `seen == 0` has to floor at one multiple rather than zero, because a
        # zero would make the check `seconds_since < 0` — never true — and a
        # never-true spacing check is a disabled one. The floor means a product
        # nobody has seen is governed by `seconds_since_product`'s own "never
        # seen" answer, not by arithmetic here.
        assert pool._escalated(600, 0) == 600


class TestTheEscalationIsTheReasonARowIsHeld:
    """Asked of the enforcing function directly, not inferred from an outcome."""

    def _exhausted_row(self, market, *, sightings: int):
        """A listing this viewer has seen ``sightings`` times, and its row."""
        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure.load(cur, policy.subject_ref, market.viewer_id)
        candidates = [
            listing_id for listing_id in state.recent_products
            if state.product_seen(listing_id) == sightings
            and listing_id not in state.purchased
        ]
        if not candidates:
            return None, policy, state
        cur.execute("SELECT * FROM marketplace_listings WHERE id = ?", (candidates[0],))
        row = dict(zip([c[0] for c in cur.description], cur.fetchone()))
        return row, policy, state

    def test_a_row_inside_the_escalated_gap_but_past_the_base_one_is_held(self, market):
        # The decisive unit test: a row that the *old* code would have served.
        # It is past the configured cooldown, so a flat check would pass it; it
        # is inside twice the configured cooldown, and the viewer has seen it
        # twice, so the escalated check must hold it.
        _sighting_steps(market, "marketplace", catalogue=6, steps=60)
        row, policy, state = self._exhausted_row(market, sightings=2)
        assert row is not None, "no listing was seen exactly twice; the case is vacuous"

        elapsed = state.seconds_since_product(int(row["id"]))
        cooldown = max(1, int(elapsed // 1.5))  # past 1x, inside 2x
        assert cooldown <= elapsed < cooldown * 2, "the fixture did not straddle the boundary"

        held = pool._reject(
            row, policy=policy, state=state, surface="feed", parse_price=None,
            product_cooldown=cooldown, seller_cooldown=0, product_cap=0, seen_ids=set(),
        )
        assert held == "product_cooldown", (
            f"a twice-seen row {elapsed:.0f}s old was not held by a {cooldown}s "
            f"escalating cooldown (got {held!r}); the check is still flat"
        )

        # And the same row, same age, seen once, must be served — otherwise the
        # test above would pass for a cooldown that had simply been made longer.
        state.product_counts[int(row["id"])] = 1
        assert pool._reject(
            row, policy=policy, state=state, surface="feed", parse_price=None,
            product_cooldown=cooldown, seller_cooldown=0, product_cap=0, seen_ids=set(),
        ) == "", "the same row seen once was also held, so this is not escalation"

    def test_the_sql_exclusion_never_excludes_a_row_the_enforcement_would_serve(self, market):
        # `_hard_exclusions` recomputes the escalated gap itself, so the two can
        # drift. Drift in this direction is the dangerous one and it is silent:
        # the SQL layer would remove inventory the enforcement was happy with,
        # and the only symptom would be slightly thinner pages.
        _sighting_steps(market, "marketplace", catalogue=25, steps=90)

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure.load(cur, policy.subject_ref, market.viewer_id)
        cooldown = router.policy_for("marketplace").product_cooldown_seconds
        cap = router.policy_for("marketplace").product_cap

        excluded = pool._hard_exclusions(policy, state, cooldown, frozenset(), cap)
        assert excluded, "nothing was excluded; the comparison is vacuous"

        for listing_id in excluded:
            cur.execute("SELECT * FROM marketplace_listings WHERE id = ?", (listing_id,))
            fetched = cur.fetchone()
            if fetched is None:
                continue
            row = dict(zip([c[0] for c in cur.description], fetched))
            code = pool._reject(
                row, policy=policy, state=state, surface="marketplace", parse_price=None,
                product_cooldown=cooldown, seller_cooldown=0, product_cap=cap, seen_ids=set(),
            )
            assert code, (
                f"listing {listing_id} was excluded in SQL but `_reject` would "
                "have served it; the optimisation is now removing real inventory"
            )


class TestTheAllowanceStaysReachable:
    """Escalation must not become a cap by arithmetic."""

    @pytest.mark.parametrize("surface", SURFACES)
    def test_every_surface_can_still_spend_its_whole_allowance_inside_the_window(self, surface):
        # The argument for linear growth, as a test. Spending a cap of `n`
        # requires `n - 1` gaps of 1x, 2x ... (n-1)x the cooldown, so the total
        # is `cooldown * n(n-1)/2`. That has to fit inside the window the
        # sighting count decays over, or the last sightings are unreachable and
        # the effective cap is quietly lower than the configured one.
        #
        # Exponential backoff fails exactly here: a 6-cap surface would need
        # 1+2+4+8+16 = 31 cooldowns, and on the marketplace's own numbers that
        # is past the window.
        policy = router.policy_for(surface)
        cap, cooldown = policy.product_cap, policy.product_cooldown_seconds
        needed = cooldown * (cap * (cap - 1)) // 2
        window = config.product_window_seconds()
        assert needed < window, (
            f"{surface}: spending a cap of {cap} needs {needed}s of escalating "
            f"gaps but the sighting count decays after {window}s, so the last "
            f"{cap} sighting(s) can never happen and the real cap is lower"
        )

    def test_the_cap_and_not_the_escalation_is_what_ends_the_sequence(self, market):
        # Measured with wide steps so the escalation cannot be the limiting
        # factor. The viewer must reach the cap exactly — if escalation held them
        # short of it, the cap tests in `test_exposure_is_capped.py` would still
        # pass while the configured allowance had silently shrunk.
        steps_of = _sighting_steps(market, "marketplace", catalogue=4, steps=40,
                                   step_seconds=3600.0)
        worst = max((len(v) for v in steps_of.values()), default=0)
        assert worst == router.policy_for("marketplace").product_cap, (
            f"with an hour between pages the viewer reached {worst} sightings, "
            f"not the configured {router.policy_for('marketplace').product_cap}"
        )


class TestWhatTheEscalationMustNotHaveBroken:
    """Spacing yields to scarcity. That is the difference from the cap."""

    @pytest.mark.parametrize("surface", ("feed", "marketplace"))
    def test_spacing_delays_a_sighting_but_never_withholds_it(self, market, surface):
        # The cooldown-versus-cap distinction stated as an outcome. A 5-product
        # catalogue has exactly `5 * cap` placements to give; escalating spacing
        # decides *when* they happen and must not reduce *how many*. If escalation
        # had been put outside `build`'s relaxation ladder — the mistake the cap
        # deliberately makes — some of that allowance would be unreachable and a
        # small shop would run out early.
        #
        # Ten hours at ten minutes a page, which is the shape a real returning
        # viewer has and is what it takes to observe this: the *nominal* feed
        # cooldown is 360 minutes, so a half-hour session fills 3 pages and
        # proves nothing about escalation either way. An earlier version of this
        # test asserted over 30 minutes and failed on pre-existing spacing that
        # had nothing to do with the change.
        cap = router.policy_for(surface).product_cap
        steps_of = _sighting_steps(market, surface, catalogue=5, steps=60,
                                   step_seconds=600.0)
        delivered = sum(len(steps) for steps in steps_of.values())
        assert delivered == 5 * cap, (
            f"{surface} delivered {delivered} of the {5 * cap} placements a "
            "5-product catalogue owes; escalating spacing is withholding "
            "inventory rather than spreading it out"
        )

    def test_the_relaxation_ladder_still_shortens_the_escalated_gap(self, market):
        # Directly: the same viewer, the same history, a plentiful catalogue and
        # a thin one. The thin one must produce the *shorter* gaps, because that
        # is what the ladder is for.
        thin = _gaps(_most_repeated(_sighting_steps(market, "marketplace", catalogue=3, steps=120)))
        assert thin, "the thin catalogue produced no repeat at all"
        nominal = router.policy_for("marketplace").product_cooldown_seconds / 60.0
        assert thin[0] < nominal, (
            f"the first gap on a 3-product catalogue was {thin[0]}min, no shorter "
            f"than the nominal {nominal:.0f}min; the ladder is no longer reaching "
            "the escalated spacing"
        )

    def test_escalation_is_reported_as_a_cooldown_and_not_as_exhaustion(self, market):
        # An operator reading drop codes has to be able to tell "come back later"
        # from "there is nothing left for you". Escalated spacing is still
        # spacing, and the answer to it is more time or a looser config — not
        # more inventory.
        assert "product_cooldown" in pool.DROP_CODES
        _sighting_steps(market, "marketplace", catalogue=6, steps=60)
        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure.load(cur, policy.subject_ref, market.viewer_id)
        candidates = [
            listing_id for listing_id in state.recent_products
            if state.product_seen(listing_id) >= 2 and listing_id not in state.purchased
        ]
        assert candidates, "the browse exhausted nothing; the case is vacuous"
        cur.execute("SELECT * FROM marketplace_listings WHERE id = ?", (candidates[0],))
        row = dict(zip([c[0] for c in cur.description], cur.fetchone()))
        code = pool._reject(
            row, policy=policy, state=state, surface="feed", parse_price=None,
            product_cooldown=10 ** 6, seller_cooldown=0, product_cap=0, seen_ids=set(),
        )
        assert code == "product_cooldown", f"expected a spacing drop, got {code!r}"
