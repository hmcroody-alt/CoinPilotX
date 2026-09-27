"""Most of a large catalogue could never be fetched by anybody.

``pool._fetch`` reads a window of ``ORDER BY featured DESC, updated_at DESC,
id DESC`` at an offset, and the only thing that moves that offset is
``exposure.rotation_offset``. It returned ``(digest % rotation_slots) * batch``,
and with the shipped defaults that is one of exactly four values: 0, 60, 120,
180. ``candidate_max_batches`` allows six batches of 60 after it. So the deepest
row any request could read was 540 — a **constant**, independent of how large the
catalogue is.

Measured over 60 rotation epochs, four surfaces, impressions recorded so that
cooldowns rotate the shallow head as they are designed to:

    catalogue   distinct ever fetched   deepest reached         dark
         300         300 (100.0%)       the whole catalogue      0.0%
        2000         374 ( 18.7%)       nothing past row ~585   70.8%

The 300 row is why this survived. At fixture scale the mechanism works perfectly,
and every test in this package used a 100-listing catalogue. The module's own
documentation asserts the property the 2000 row disproves:
``config.rotation_period_seconds`` says rotation is what reaches "the deep end"
and that without it "a listing ranked 500th by the candidate ordering is never
fetched at all". Both statements are true about row 500 and false about row 600.

The consequence is not only that stock is hidden. It is that *personalisation
cannot work on a large catalogue*, because ranking can only reorder what
retrieval returned. A viewer whose only 20 clicks were all on cameras, on a
catalogue of 200 cameras and 1800 widgets where the cameras sit at the deep end,
was served **0 cameras out of 960 placements**. The affinity bonus in
``ranking`` was not broken; it had nothing to be applied to.

The fix passes a ``span`` — the eligible catalogue size, from
``pool.catalogue_span`` — into ``rotation_offset``, which then uses "enough slots
to cover the catalogue" instead of a fixed four. ``config.rotation_slots``
becomes a floor. After it, on the same 2000-listing measurement: nothing below
id 6, so 0.2% dark instead of 70.8%, and the camera viewer gets 14.1% cameras
against a 10% catalogue share.

That 14.1% is the honest limit of this change and the reason it is not the whole
chapter. Retrieval is still viewer-blind: one ordering, no reference to the
person it is for. Making the deep end *reachable* lets affinity ranking work at
all; making it *targeted* needs retrieval sources that know who is asking.
"""

from __future__ import annotations

import pytest

from services.commerce_discovery import config, eligibility, exposure, pool

SURFACES = ("feed", "marketplace", "reels", "product_detail")
LARGE = 2000


def _enlarge(market, size: int, *, reset_span: bool = True) -> None:
    """Grow the catalogue to ``size`` listings, spread over the same sellers.

    Spread deliberately: ``restock`` stacks everything on one seller, and the
    seller cooldown would then be the thing limiting what gets served, which is
    not the thing being measured.

    ``reset_span=False`` is for the cache tests, which need the catalogue to
    change *behind* a populated cache.
    """
    cur = market.conn.cursor()
    cur.execute("SELECT MAX(id) AS top FROM marketplace_listings")
    top = int(dict(cur.fetchone())["top"])
    if size <= top:
        return
    added = market.restock(0, size - top)
    for index, listing_id in enumerate(added):
        cur.execute("UPDATE marketplace_listings SET seller_user_id=? WHERE id=?",
                    (1001 + (index % 10), listing_id))
    market.conn.commit()
    if reset_span:
        pool.reset_span_cache()


def _reached(market, clock, *, epochs: int = 60) -> set[int]:
    """Every listing id fetched over many rotation epochs on every surface."""
    seen: set[int] = set()
    for _ in range(epochs):
        for surface in SURFACES:
            placements = market.serve(surface)
            market.render(placements)
            for placement in placements:
                product = placement["product"]
                seen.add(int(product.get("listing_id") or product.get("id") or 0))
        clock.advance(config.rotation_period_seconds())
    return seen


class TestTheDeepEndIsReachable:
    """The measurement the chapter exists for."""

    def test_no_region_of_a_large_catalogue_is_permanently_dark(self, market, clock):
        # The decisive test. Before the span, nothing below id 1416 of 2000 was
        # ever fetched, by anybody, on any surface, at any time.
        _enlarge(market, LARGE)
        reached = _reached(market, clock)
        assert reached, "nothing was served at all"
        dark = min(reached) - 1
        assert dark <= LARGE * 0.02, (
            f"ids 1..{dark} ({100.0 * dark / LARGE:.1f}% of the catalogue) were "
            "never fetched over 60 rotation epochs on four surfaces; the offset "
            "space is not reaching the deep end"
        )

    def test_the_deepest_rows_are_reachable_and_not_merely_the_middle(self, market, clock):
        # A weaker fix — say, doubling `rotation_slots` — would move the dark
        # region without removing it. So this asks specifically about the oldest
        # stock, which is the tail of `id DESC` and the part that had no chance.
        _enlarge(market, LARGE)
        reached = _reached(market, clock)
        oldest = {listing_id for listing_id in reached if listing_id <= 50}
        assert oldest, (
            "not one of the 50 oldest listings was ever fetched; the reachable "
            "window moved but is still a window"
        )

    def test_a_small_catalogue_is_unaffected(self, market, clock):
        # The change must be a no-op where the old mechanism already worked, or
        # it is not a fix but a different set of trade-offs. At 100 listings the
        # span implies 2 slots and `config.rotation_slots`'s floor of 4 wins, so
        # the offsets are the same four values as before.
        reached = _reached(market, clock, epochs=20)
        assert len(reached) == 100, (
            f"a 100-listing catalogue served {len(reached)} distinct listings; "
            "the span has changed behaviour where nothing was wrong"
        )


class TestTheOffsetSpaceFollowsTheCatalogue:
    """``rotation_offset`` arithmetic, without a database in the way."""

    def test_without_a_span_the_offsets_are_the_old_fixed_set(self):
        # The fallback, and it has to be exact: `catalogue_span` returns 0 when
        # the count fails, and 0 must mean "behave as before".
        batch = config.candidate_batch_size()
        produced = {exposure.rotation_offset(f"ref-{index}") for index in range(2000)}
        assert produced == {slot * batch for slot in range(config.rotation_slots())}

    def test_a_zero_span_is_the_same_as_no_span(self):
        # `catalogue_span` returns 0 both for "the count failed" and for "the
        # catalogue is empty", and neither may be read as "rotate over 0 rows"
        # — that would collapse every viewer onto offset 0.
        no_span = {exposure.rotation_offset(f"ref-{index}") for index in range(500)}
        zero = {exposure.rotation_offset(f"ref-{index}", span=0) for index in range(500)}
        assert zero == no_span

    def test_the_offset_space_grows_with_the_span(self):
        batch = config.candidate_batch_size()
        produced = {
            exposure.rotation_offset(f"ref-{index}", span=6000) for index in range(4000)
        }
        # 6000 rows at 60 a batch is 100 slots. Sampling 4000 refs will not hit
        # every one, but it must hit far more than the four the old code allowed.
        assert len(produced) > 50, (
            f"a 6000-row span produced only {len(produced)} distinct offsets"
        )
        assert max(produced) >= 6000 - batch, (
            f"the deepest offset reachable on a 6000-row catalogue was "
            f"{max(produced)}, so the tail is still unreachable"
        )

    def test_the_tail_of_the_catalogue_gets_a_slot(self):
        # Ceiling division, not floor. With floor division a catalogue of
        # `n * batch + 1` rows gets `n` slots and the final partial batch — the
        # very oldest stock — is exactly what stays dark.
        batch = config.candidate_batch_size()
        produced = {
            exposure.rotation_offset(f"ref-{index}", span=batch * 10 + 1)
            for index in range(4000)
        }
        assert max(produced) >= batch * 10, (
            "a catalogue one row past a batch boundary did not get a slot for "
            "that row"
        )

    def test_the_configured_slots_remain_a_floor(self):
        # A catalogue smaller than one batch must not collapse to a single
        # offset: cooldown-driven rotation of the head is what a small shop
        # relies on, and it still wants viewers starting in different places.
        produced = {exposure.rotation_offset(f"ref-{index}", span=10) for index in range(500)}
        assert len(produced) == config.rotation_slots()

    def test_the_offset_is_still_stable_inside_one_epoch(self, clock):
        # The property the whole mechanism is built around: the two requests of
        # one pull-to-refresh must agree, or they duplicate each other's products
        # rather than avoiding them. Making the offset span the catalogue must not
        # have made it jittery.
        first = exposure.rotation_offset("stable-ref", span=LARGE)
        clock.advance(config.rotation_period_seconds() / 4.0)
        assert exposure.rotation_offset("stable-ref", span=LARGE) == first

    def test_the_offset_still_moves_between_epochs(self, clock):
        # And the converse, over enough epochs that a single unlucky repeat does
        # not read as a failure.
        offsets = set()
        for _ in range(40):
            offsets.add(exposure.rotation_offset("stable-ref", span=LARGE))
            clock.advance(config.rotation_period_seconds())
        assert len(offsets) > 4, (
            f"one viewer saw only {len(offsets)} distinct offsets over 40 epochs"
        )

    def test_rotation_can_still_be_switched_off(self, monkeypatch):
        # A span must not resurrect rotation for an operator who disabled it.
        monkeypatch.setenv("COMMERCE_DISCOVERY_ROTATION_PERIOD", "0")
        assert exposure.rotation_offset("ref", span=LARGE) == 0


class TestTheSpanIsCheapAndFailsSafe:
    """One COUNT per process per rotation period, and never a hard failure."""

    def test_the_span_counts_the_eligible_catalogue(self, market):
        pool.reset_span_cache()
        cur = market.conn.cursor()
        assert pool.catalogue_span(cur) == 100

    def test_the_span_is_cached_within_a_rotation_period(self, market, clock):
        # The expiry is whatever `catalogue_span` itself wrote. An earlier version
        # of this test assigned `_SPAN_CACHE[0]` by hand, which meant it never
        # exercised the TTL at all: a mutant setting the TTL to a billion seconds
        # survived it, and so would one setting it to zero.
        pool.reset_span_cache()
        cur = market.conn.cursor()
        first = pool.catalogue_span(cur)
        _enlarge(market, 400, reset_span=False)
        clock.advance(config.rotation_period_seconds() / 2.0)
        assert pool.catalogue_span(cur) == first, (
            "the span was recomputed inside one rotation period; that is a COUNT "
            "per request on a table the brief expects to hold a million rows"
        )

    def test_the_cache_expires_on_the_packages_own_clock(self, market, clock):
        # Not on `time.time()`. Every other clock read in this package goes
        # through `subject.now_utc` so that a test advancing its own clock is
        # observing the same time the code is. This test only has teeth because it
        # advances the *fake* clock and nothing else: a wall-clock expiry would
        # not have moved, so the stale 100 would still be returned.
        pool.reset_span_cache()
        cur = market.conn.cursor()
        assert pool.catalogue_span(cur) == 100
        _enlarge(market, 400, reset_span=False)
        clock.advance(config.rotation_period_seconds() + 1)
        assert pool.catalogue_span(cur) == 400, (
            "the cached span outlived a rotation period of the package's own "
            "clock; a catalogue that grows can never be seen to have grown"
        )

    def test_a_failed_count_degrades_to_the_old_behaviour(self, market, monkeypatch):
        # Graceful degradation, and specifically in the right direction: a
        # catalogue whose size cannot be read must rotate over the fixed slots,
        # not over zero rows.
        pool.reset_span_cache()

        class Exploding:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError("no")

        assert pool.catalogue_span(Exploding()) == 0
        assert exposure.rotation_offset("ref", span=0) == exposure.rotation_offset("ref")

    def test_a_failed_count_is_not_cached(self, market, monkeypatch):
        # Otherwise one transient failure blinds the deep end for a whole
        # rotation period, and the symptom — a shop that quietly serves only its
        # newest stock for an hour — looks nothing like a database error.
        pool.reset_span_cache()

        class Exploding:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError("no")

        assert pool.catalogue_span(Exploding()) == 0
        assert pool.catalogue_span(market.conn.cursor()) == 100

    def test_the_span_is_an_over_estimate_and_never_an_under_one(self, market):
        # It counts what `eligibility.candidate_sql` expresses and skips
        # `eligibility.gate`, so it can exceed the number of genuinely servable
        # rows. That is the safe direction — an offset landing in a thin region
        # wraps, which `_scan` already does — but an *under*-estimate would leave
        # the tail unreachable again, silently.
        pool.reset_span_cache()
        cur = market.conn.cursor()
        cur.execute("UPDATE marketplace_listings SET safety_score=99 WHERE id <= 50")
        market.conn.commit()
        pool.reset_span_cache()
        span = pool.catalogue_span(cur)

        cur.execute("SELECT * FROM marketplace_listings")
        columns = [column[0] for column in cur.description]
        servable = sum(
            0 if eligibility.gate(dict(zip(columns, row)), None) else 1
            for row in cur.fetchall()
        )
        assert span >= servable, (
            f"the span ({span}) is below the number of servable rows "
            f"({servable}); the tail of the catalogue is unreachable"
        )
