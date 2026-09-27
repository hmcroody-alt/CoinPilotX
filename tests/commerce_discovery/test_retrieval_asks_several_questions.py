"""Retrieval used to mention the viewer once, to exclude their own listings.

Ranking can only reorder rows retrieval already returned. So a personalisation
signal that never reaches the SQL has a ceiling, and on a large catalogue the
ceiling is low. Measured 2026-09-27 against a 2000-listing catalogue holding 200
cameras and 1800 widgets, with a viewer whose only twenty clicks were all on
cameras, over 60 rotation epochs across three surfaces:

    retrieval                       cameras served   catalogue share   lift
    one blind ordering                   11.9%            10.0%        1.19x
    one ordering + targeted sources      48.4%            10.0%        4.84x

Regenerate that table with
``scripts/protection/measure_commerce_discovery_reachability.py``, which
reconstructs the blind-ordering row by re-applying this package's own
"pre-fix" mutant in a sandbox. The row read ``14.1% / 1.41x`` until the
reproducer existed; it had been measured against a tree that also predated the
cap, fatigue and signal fixes.

The affinity term in ``ranking`` was working correctly the whole time. It had
almost nothing to work on: ``featured DESC, updated_at DESC, id DESC`` is the same
ordering for every viewer on the platform, and the 200 cameras sat below the part
of it any request could read.

The fix is not a new ranker and not a second database. It is the same eligibility
query asked several times with one extra ``AND`` — categories the viewer has
opened, sellers they follow, listings earning engagement now — each with a small
quota, and ``rotation`` filling the rest. Every accepted row carries
``candidate_source`` naming the question that found it.

What this file is defending
---------------------------

The lift is the easy half. The hard half is that four retrieval questions must not
become four ways around the controls, and must not change anything for a viewer
who has no history at all:

* A new account gets ``rotation`` alone and therefore the pre-sources pool, row
  for row. Personalised retrieval must not be what changes the cold-start
  experience.
* A targeted source can only *narrow*. It cannot admit a suppressed, ineligible,
  self-owned, at-cap or cooling-down listing, because it shares the whole query
  with ``rotation`` except for one AND-ed clause and then runs through the same
  ``_reject``.
* Quotas are fractions of the pool target, so one narrow interest cannot seal a
  viewer inside itself, and the query budget is a fraction too, so three extra
  sources cannot multiply the cost of a request by four.
* A viewer who opted out of personalised recommendations must get the untargeted
  query. Declining to *score* their interests while still retrieving on them
  would be an opt-out in name only.

The suite was blind to all of this before this file existed: all 415 tests in the
package passed unchanged both before and after the sources were added, because
every other fixture builds a 100-listing catalogue where one blind ordering
already reaches everything.
"""

from __future__ import annotations

from collections import Counter
import dataclasses

import pytest

from services.commerce_discovery import config, metrics, pool, preferences, subject

#: Ids 1..DEEP are the viewer's category. They sort *last* in the candidate
#: ordering (`l.id DESC` is the final tiebreak and every row here shares the other
#: two keys), which is where a catalogue's older stock genuinely sits.
DEEP = 200
LARGE = 2000


def _enlarge(market, size: int) -> None:
    """Grow the catalogue to ``size`` rows spread over the ten seeded sellers."""
    cur = market.conn.cursor()
    cur.execute("SELECT MAX(id) AS top FROM marketplace_listings")
    top = int(dict(cur.fetchone())["top"])
    added = market.restock(0, max(0, size - top))
    for index, listing_id in enumerate(added):
        cur.execute(
            "UPDATE marketplace_listings SET seller_user_id=? WHERE id=?",
            (1001 + (index % 10), listing_id),
        )
    market.conn.commit()
    pool.reset_span_cache()


def _split_catalogue(market, *, deep: int = DEEP) -> None:
    """Put the viewer's category at the deep end and everything else in front."""
    cur = market.conn.cursor()
    cur.execute("UPDATE marketplace_listings SET category='cameras' WHERE id <= ?", (deep,))
    cur.execute("UPDATE marketplace_listings SET category='widgets' WHERE id > ?", (deep,))
    market.conn.commit()
    for listing_id in range(1, deep + 1):
        market._categories[listing_id] = "cameras"
    cur.execute("SELECT id FROM marketplace_listings WHERE id > ?", (deep,))
    for row in cur.fetchall():
        market._categories[int(dict(row)["id"])] = "widgets"


def _clicked(market, listing_ids, *, action: str = "click") -> None:
    """Record engagement by *this* viewer, which is what builds their profile.

    Written on ``subject_ref`` rather than ``user_id`` because that is what the
    discovery tables hold, and what ``engine._interest_profile`` reads. Distinct
    from ``market.history``, which writes the same rows under a different subject
    to make a listing popular with *other* people.
    """
    cur = market.conn.cursor()
    policy = preferences.viewer_policy(cur, market.viewer_id)
    stamp = subject.iso(market.clock.now)
    for listing_id in listing_ids:
        key = f"own:{action}:{listing_id}"
        cur.execute(
            "INSERT OR IGNORE INTO commerce_discovery_engagement_events "
            "(event_id, placement_id, subject_ref, surface, listing_id, seller_user_id, "
            " promotion_class, ranking_version, action, event_at, dedup_key, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, f"own-pl:{listing_id}", policy.subject_ref, "feed", int(listing_id),
             1001, "organic", "seed", action, stamp, key, stamp),
        )
    market.conn.commit()


def _build(market, **kwargs):
    """``pool.build`` for this viewer, with the real policy and price parser."""
    from conftest import parse_price

    cur = market.conn.cursor()
    policy = preferences.viewer_policy(cur, market.viewer_id)
    from services.commerce_discovery import exposure as exposure_module

    state = exposure_module.load(cur, policy.subject_ref, market.viewer_id)
    kwargs.setdefault("surface", "marketplace")
    kwargs.setdefault("target", config.candidate_target_size())
    return pool.build(
        cur,
        viewer_user_id=market.viewer_id,
        policy=policy,
        exposure=state,
        parse_price=parse_price,
        **kwargs,
    )


def _served_mix(market, *, epochs: int = 60, surfaces=("feed", "marketplace", "product_detail")):
    """What the engine actually put in front of the viewer, by category."""
    got: Counter = Counter()
    for _ in range(epochs):
        for surface in surfaces:
            placements = market.serve(surface)
            market.render(placements)
            for placement in placements:
                listing_id = int(
                    placement["product"].get("listing_id") or placement["product"].get("id") or 0
                )
                got["cameras" if listing_id <= DEEP else "widgets"] += 1
        market.clock.advance(config.rotation_period_seconds())
    return got


class TestRetrievalReachesWhatTheViewerCaresAbout:
    """The measurement in the docstring, as assertions."""

    def test_a_viewer_who_only_clicks_one_category_is_served_it(self, market, clock):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))  # establish the subject_ref
        _clicked(market, range(1, 21))

        got = _served_mix(market)
        total = sum(got.values())
        assert total > 500, "the probe has to actually serve something to measure"
        share = got["cameras"] / total
        # The catalogue is 10% cameras. Before targeted retrieval this was 11.9%,
        # i.e. 1.19x chance for someone who had said "cameras" twenty times. The
        # floor is set at 3x rather than at the measured 4.84x so that a ranking
        # weight change does not fail this test — the claim is "retrieval now
        # reaches the category", not "it reaches it in exactly this proportion".
        assert share > 0.30, f"cameras were {share:.1%} of {total} placements"
        # And not the other failure: a viewer with one narrow interest must not be
        # sealed inside it. The affinity quota is just over a third of the pool for
        # this reason.
        assert got["widgets"] > total * 0.25, "the viewer was sealed inside one category"

    def test_the_deep_category_rows_are_credited_to_the_affinity_source(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))

        built = _build(market, interests=("cameras",))
        cameras = [row for row in built.rows if int(row["id"]) <= DEEP]
        assert cameras, "no camera was retrieved at all"
        # Provenance is a claim about which question found the row, so every camera
        # here must be credited to the question that can reach them. `rotation`
        # cannot: on a 2000-row catalogue its offsets never descend this far, which
        # is the whole finding this file records.
        assert {row["candidate_source"] for row in cameras} == {"affinity"}

    def test_the_engine_wires_the_follow_graph_into_retrieval(self, market):
        """Through :func:`engine.serve`, not through a direct ``pool.build`` call.

        Its sibling below passes ``followed_sellers`` in by hand, which proves the
        pool can use follows but says nothing about whether anything ever hands
        them over. Mutating the engine to pass ``followed_sellers=()`` left the
        whole file green — the wiring was untested even though the capability was
        not. Same shape as the header-action trap: a green suite and a dead wire.
        """
        cur = market.conn.cursor()
        _enlarge(market, LARGE)
        # One seller owns nothing but deep rows, so `rotation` cannot reach them and
        # any of their listings that surface must have come from the follow graph.
        cur.execute("UPDATE marketplace_listings SET seller_user_id=1007 WHERE id <= 40")
        cur.execute("UPDATE marketplace_listings SET seller_user_id=1002 WHERE id > 40")
        market.conn.commit()
        market.follow_seller(1007)

        served: set[int] = set()
        for _ in range(6):
            placements = market.serve("feed")
            market.render(placements)
            for placement in placements:
                served.add(int(placement["product"].get("listing_id")
                               or placement["product"].get("id") or 0))
            market.clock.advance(config.rotation_period_seconds())

        assert served & set(range(1, 41)), (
            "a followed seller's whole catalogue never reached a real surface"
        )

    def test_a_followed_sellers_listings_are_retrieved_from_the_deep_end(self, market):
        _enlarge(market, LARGE)
        cur = market.conn.cursor()
        # One seller owns nothing but deep rows, so their listings are unreachable
        # by the untargeted ordering.
        cur.execute("UPDATE marketplace_listings SET seller_user_id=1007 WHERE id <= 40")
        cur.execute("UPDATE marketplace_listings SET seller_user_id=1002 WHERE id > 40")
        market.conn.commit()
        market.follow_seller(1007)

        built = _build(market, followed_sellers=(1007,))
        theirs = [row for row in built.rows if int(row["seller_user_id"]) == 1007]
        assert theirs, "a followed seller's whole catalogue stayed unreachable"
        assert {row["candidate_source"] for row in theirs} == {"followed"}

    def test_trending_listings_are_retrieved_without_being_near_the_head(self, market):
        _enlarge(market, LARGE)
        # Engagement by *other* people, which is what "trending" means. These ids
        # are far below anything the untargeted offsets can read.
        hot = list(range(11, 31))
        market.history(hot, clicks=6, age_days=0.5)

        built = _build(market)
        found = [row for row in built.rows if int(row["id"]) in hot]
        assert found, "listings earning engagement right now were never fetched"
        assert {row["candidate_source"] for row in found} == {"trending"}

    def test_the_viewers_own_clicks_do_not_make_a_listing_trending_to_them(self, market):
        """Trending is the crowd's opinion, not a replay of your own last session.

        Found by the sibling provenance test, which expected the deep cameras to be
        credited to ``affinity`` and got ``{'affinity', 'trending'}``. The cause was
        that :func:`pool._trending_ids` counted *all* engagement rows regardless of
        ``subject_ref``, so this viewer's own twenty clicks were enough to make those
        exact twenty listings the busiest rows in the window.

        That is not a cosmetic mislabel. It made ``trending`` a fourth route back to
        the products the viewer had just engaged with — arriving with its own quota
        and its own provenance, under a name that reads like a crowd signal. The
        exposure ledger would still have spaced repeat *impressions*, but retrieval
        would have kept re-proposing the same rows, which is the ceiling the whole
        multi-source change exists to lift.

        ``affinity`` remains the right home for the viewer's own history because it
        names categories, so it widens toward siblings the viewer has not seen
        instead of returning the listings they already clicked.
        """
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))

        cur = market.conn.cursor()
        subject_ref = preferences.viewer_policy(cur, market.viewer_id).subject_ref

        # Nobody else has engaged with anything, so whatever comes back is this
        # viewer's own history and nothing else.
        _clicked(market, range(1, 21))
        assert pool._trending_ids(cur) , "the fixture failed to record any engagement"
        assert pool._trending_ids(cur, exclude_subject_ref=subject_ref) == ()

        # And with the crowd present, the crowd's rows survive while the viewer's own
        # are still removed — the exclusion is scoped, not a blanket disable.
        market.history(range(400, 420), clicks=6, age_days=0.5)
        crowd = pool._trending_ids(cur, exclude_subject_ref=subject_ref)
        assert crowd, "excluding the viewer must not empty the source"
        assert not set(crowd) & set(range(1, 21))

    def test_trending_is_bounded_to_its_window(self, market):
        _enlarge(market, LARGE)
        # The same engagement, a month old. A trending source that ignored the
        # window would retrieve these forever, which is the unfalsifiable-label
        # failure `config.trend_window_seconds` exists to prevent.
        market.history(range(11, 31), clicks=6, age_days=30.0)

        built = _build(market)
        assert "trending" not in built.sources


class TestAViewerWithNoHistoryIsUnaffected:
    """Sources must not be what changes the cold-start experience."""

    def test_the_pool_is_rotation_alone(self, market):
        _enlarge(market, LARGE)
        built = _build(market)
        assert set(built.sources) == {"rotation"}
        assert {row["candidate_source"] for row in built.rows} == {"rotation"}

    def test_the_rows_are_the_ones_the_untargeted_query_returns(self, market):
        """Row for row, not merely the same count.

        A same-length pool built out of different rows would pass a count check and
        still mean that adding sources had silently changed what a brand-new
        account sees.
        """
        _enlarge(market, LARGE)
        built = _build(market)

        from conftest import parse_price
        from services.commerce_discovery import exposure as exposure_module

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure_module.load(cur, policy.subject_ref, market.viewer_id)
        # The pre-sources code path, named explicitly: one source, the untargeted
        # one, rotating.
        direct = pool._scan(
            cur,
            viewer_user_id=market.viewer_id,
            policy=policy,
            state=state,
            surface="marketplace",
            parse_price=parse_price,
            product_cooldown=config.product_cooldown_seconds(),
            seller_cooldown=config.seller_cooldown_seconds(),
            product_cap=config.product_cap(),
            target=config.candidate_target_size(),
            rotation_offset=0,
            sources=(pool._Source(name="rotation", rotates=True),),
        )
        assert [int(r["id"]) for r in built.rows] == [int(r["id"]) for r in direct.rows]

    def test_a_small_catalogue_is_unaffected_even_with_every_source_active(self, market):
        """100 listings, and one blind ordering already reaches all of them.

        This is why the rest of the package could not see the change: on a
        catalogue this size the targeted sources retrieve rows ``rotation`` would
        have retrieved anyway, so the pool is the same set with different labels.
        """
        market.render(market.serve("feed"))
        _clicked(market, (1, 2, 3, 6, 7))
        market.follow_seller(1003)
        market.history(range(40, 60), clicks=4, age_days=0.5)

        built = _build(market)
        plain = _build(market, interests=(), followed_sellers=())
        assert {int(r["id"]) for r in built.rows} >= {int(r["id"]) for r in plain.rows}
        assert len(built.rows) == len(plain.rows)


class TestNoSourceEscapesTheControls:
    """A targeted source narrows. It is not a second way into the response."""

    def test_an_at_cap_listing_is_dropped_however_it_was_found(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))

        from services.commerce_discovery import exposure as exposure_module
        from conftest import parse_price

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure_module.load(cur, policy.subject_ref, market.viewer_id)
        # This viewer has exhausted every camera. The affinity source will still
        # retrieve them from the database — that is its job — and `_reject` must
        # still refuse every one.
        for listing_id in range(1, DEEP + 1):
            state.product_counts[listing_id] = 99

        built = pool.build(
            cur,
            viewer_user_id=market.viewer_id,
            policy=policy,
            exposure=state,
            parse_price=parse_price,
            surface="marketplace",
            product_cap=3,
            interests=("cameras",),
        )
        assert not [row for row in built.rows if int(row["id"]) <= DEEP]
        assert built.dropped.get("product_cap", 0) > 0, (
            "the cap has to have been the thing that refused them, not eligibility"
        )

    def test_a_source_cannot_admit_a_suppressed_listing(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))

        from services.commerce_discovery import exposure as exposure_module
        from conftest import parse_price

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        object.__setattr__(policy, "suppressed_listings", frozenset(range(1, DEEP + 1)))
        state = exposure_module.load(cur, policy.subject_ref, market.viewer_id)

        built = pool.build(
            cur,
            viewer_user_id=market.viewer_id,
            policy=policy,
            exposure=state,
            parse_price=parse_price,
            surface="marketplace",
            interests=("cameras",),
        )
        assert not [row for row in built.rows if int(row["id"]) <= DEEP]

    def test_a_source_cannot_admit_an_ineligible_listing(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        cur = market.conn.cursor()
        # Unpublished. `eligibility.candidate_sql` is shared by every source, so
        # this is a property of the query they all run, not of a check each one
        # remembers to make.
        cur.execute("UPDATE marketplace_listings SET status='draft' WHERE id <= ?", (DEEP,))
        market.conn.commit()
        market.render(market.serve("feed"))

        built = _build(market, interests=("cameras",))
        assert not [row for row in built.rows if int(row["id"]) <= DEEP]

    def test_a_source_cannot_return_the_viewers_own_listing(self, market):
        _enlarge(market, LARGE)
        cur = market.conn.cursor()
        cur.execute(
            "UPDATE marketplace_listings SET seller_user_id=? WHERE id <= 40",
            (market.viewer_id,),
        )
        market.conn.commit()
        # Following yourself is a thing the follow graph permits.
        market.follow_seller(market.viewer_id)

        built = _build(market, followed_sellers=(market.viewer_id,))
        assert not [
            row for row in built.rows if int(row["seller_user_id"]) == market.viewer_id
        ]

    def test_a_cooling_down_listing_is_dropped_however_it_was_found(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))

        from services.commerce_discovery import exposure as exposure_module
        from conftest import parse_price

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure_module.load(cur, policy.subject_ref, market.viewer_id)
        # `product_age` is "seconds since last shown", so zero is "just now" — the
        # deepest point of the cooldown. `product_counts` at 1 keeps the escalation
        # multiplier at its base, so this asserts the plain cooldown and not the
        # escalated one, which has its own file.
        for listing_id in range(1, DEEP + 1):
            state.product_counts[listing_id] = 1
            state.product_age[listing_id] = 0.0

        built = pool.build(
            cur,
            viewer_user_id=market.viewer_id,
            policy=policy,
            exposure=state,
            parse_price=parse_price,
            surface="marketplace",
            product_cooldown=3600,
            seller_cooldown=0,
            interests=("cameras",),
        )
        # The property under test is that *no* cooled-down row survives, whichever
        # question found it. It is deliberately not asserted through
        # `dropped["product_cooldown"]`: `_hard_exclusions` removes most of these
        # rows in SQL before `_reject` can count them, so the counter systematically
        # under-reports the control that works best, and a `> 0` there would be
        # asserting how many leaked past the optimisation rather than whether the
        # rule held.
        assert not [row for row in built.rows if int(row["id"]) <= DEEP]
        assert built.rows, "the shallow end should still have filled the pool"


class TestQuotasAndCost:
    """No source can crowd out the others, and none can multiply the query bill."""

    def test_no_targeted_source_fills_the_whole_pool(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))

        built = _build(market, interests=("cameras",), target=40)
        assert len(built.rows) == 40

        # Each targeted source is bounded by its *own* share of the target, and
        # rotation gets whatever the targeted shares leave. Stated per-source on
        # purpose: an earlier version of this test asserted
        # `rotation >= 40 - 14`, which quietly assumed affinity was the only
        # source competing with rotation. It is not — `trending` and `followed`
        # take their own slices, so that assertion failed on correct code. The
        # property worth defending is that no *one* source can take the pool.
        quotas = {
            source.name: min(40, max(1, int(40 * source.share)))
            for source in pool._sources(
                market.conn.cursor(),
                interests=("cameras",),
                followed_sellers=(),
                subject_ref=preferences.viewer_policy(
                    market.conn.cursor(), market.viewer_id
                ).subject_ref,
            )
            if source.share > 0
        }
        assert quotas, "the affinity source should be active for this viewer"
        for name, quota in quotas.items():
            assert built.sources.get(name, 0) <= quota, f"{name} exceeded its quota"
        # And the untargeted question keeps the remainder, so a viewer with a
        # narrow history still sees most of the catalogue.
        assert built.sources["rotation"] >= 40 - sum(quotas.values())

    def test_rotation_absorbs_a_quota_a_targeted_source_could_not_spend(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market, deep=3)
        market.render(market.serve("feed"))
        _clicked(market, (1, 2, 3))

        built = _build(market, interests=("cameras",), target=40)
        # Only three cameras exist, so affinity cannot fill its fourteen slots.
        assert built.sources["affinity"] <= 3
        assert len(built.rows) == 40, "the unspent quota has to be filled by rotation"

    def test_the_query_count_stays_bounded_with_every_source_active(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))
        market.follow_seller(1003)
        market.history(range(400, 460), clicks=5, age_days=0.5)

        built = _build(market, interests=("cameras",), followed_sellers=(1003,))
        # `candidate_max_batches` is 6 and a targeted source gets a third of that.
        # Three extra sources must cost at most three extra pairs of queries, not
        # three extra full budgets.
        ceiling = config.candidate_max_batches() + 3 * max(1, config.candidate_max_batches() // 3)
        assert built.batches <= ceiling

    def test_an_unreadable_event_table_removes_trending_and_nothing_else(self, market):
        """The *real* ``try/except``, reached by breaking the real query.

        An earlier version of this test monkeypatched ``_trending_ids`` itself to
        return ``()``, which asserted that the stub returned what the stub was told
        to return. Mutating the real ``except Exception: return ()`` to ``raise``
        left it green. So the table is genuinely made unreadable here, and the
        property is that a source which cannot answer *vanishes* — ``rotation``
        absorbs the freed quota and the surface stays full, rather than the whole
        feed going blank because one optional signal is down.
        """
        _enlarge(market, LARGE)
        market.history(range(11, 31), clicks=6, age_days=0.5)

        cur = market.conn.cursor()
        cur.execute("DROP TABLE commerce_discovery_engagement_events")
        market.conn.commit()

        assert pool._trending_ids(cur) == ()
        assert pool._trending_ids(cur, exclude_subject_ref="anything") == ()

        built = _build(market)
        assert "trending" not in built.sources
        assert len(built.rows) == config.candidate_target_size(), (
            "one broken optional source emptied the pool"
        )

    def test_a_long_history_cannot_become_a_huge_statement(self, market):
        """``MAX_SOURCE_TERMS`` is a real bound, not a comment.

        The profile reads that feed these already ``LIMIT``, so this is the second
        of two bounds — but it is the one that holds if a future profile read
        forgets to. Unbounded, a viewer with a thousand distinct categories turns a
        single retrieval question into a thousand-parameter statement, which on
        Postgres is a planning cost paid on every request.
        """
        many = tuple(f"cat-{index}" for index in range(pool.MAX_SOURCE_TERMS * 10))
        assert len(pool._terms(many, str)) == pool.MAX_SOURCE_TERMS

        sources = pool._sources(market.conn.cursor(), interests=many, followed_sellers=many)
        affinity = next(source for source in sources if source.name == "affinity")
        assert len(affinity.params) == pool.MAX_SOURCE_TERMS
        assert affinity.clause.count("?") == pool.MAX_SOURCE_TERMS

    def test_a_pool_that_cannot_fill_still_cannot_exceed_the_budget(self, market):
        """The only shape in which the query budget is the binding constraint.

        The sibling test above asserts the ceiling on a catalogue rich enough that
        every source fills its quota in one or two queries, so the budget is never
        reached and raising it changes nothing. Scarcity does not help either — the
        ``len(fetched) < batch_size`` short-circuit ends a source after one query
        when there is little to read.

        The budget only binds when the query keeps returning *full* batches and the
        rows keep being *rejected*: plenty to read, nothing to accept. So here every
        seller is inside their cooldown, which `_reject` enforces in Python and
        `_hard_exclusions` does not remove in SQL. Every source then burns its whole
        budget, and the query count becomes a direct reading of the arithmetic.

        ``_scan`` is called rather than ``build`` because ``build``'s relaxation
        ladder would drop the seller cooldown on its second rung and dissolve the
        very condition under test.
        """
        from conftest import parse_price
        from services.commerce_discovery import exposure as exposure_module

        _enlarge(market, LARGE)
        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        state = exposure_module.load(cur, policy.subject_ref, market.viewer_id)
        _split_catalogue(market)

        # Every seller was shown a moment ago, and the cooldown is long. Rows come
        # back full-batch and every one of them is refused.
        cur.execute("SELECT DISTINCT seller_user_id AS sid FROM marketplace_listings")
        for row in cur.fetchall():
            seller = int(dict(row)["sid"] or 0)
            if seller:
                state.seller_age[seller] = 0.0

        sources = pool._sources(
            cur, interests=("cameras", "widgets"), followed_sellers=(1002, 1003),
            subject_ref=policy.subject_ref,
        )
        assert len(sources) >= 3, "this test needs several sources to be meaningful"

        result = pool._scan(
            cur,
            viewer_user_id=market.viewer_id,
            policy=policy,
            state=state,
            surface="feed",
            parse_price=parse_price,
            product_cooldown=config.product_cooldown_seconds(),
            seller_cooldown=86400,
            product_cap=config.product_cap(),
            target=config.candidate_target_size(),
            rotation_offset=0,
            sources=sources,
        )
        assert not result.rows, "the cooldown should have refused everything"

        max_batches = config.candidate_max_batches()
        targeted = max(1, max_batches // 3)
        ceiling = max_batches + 3 * targeted

        # First: the budget was actually *reached*. Without this the ceiling
        # assertion below is vacuous — a scenario that stops after two queries
        # satisfies any ceiling, which is exactly how the earlier version of this
        # test let two budget mutants through.
        assert result.batches > max_batches, (
            f"only {result.batches} queries ran, so no budget was ever the "
            "binding constraint and the ceiling below proves nothing"
        )
        assert result.batches <= ceiling, (
            f"{result.batches} queries against a ceiling of {ceiling}: "
            "extra retrieval questions are multiplying the query bill"
        )


class TestProvenanceIsReported:
    """``candidate_source`` has to be a fact, not a label."""

    def test_every_accepted_row_carries_a_source(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))
        market.follow_seller(1003)

        built = _build(market, interests=("cameras",), followed_sellers=(1003,))
        assert built.rows
        for row in built.rows:
            assert row.get("candidate_source") in pool.CANDIDATE_SOURCES

    def test_the_counts_sum_to_the_pool(self, market):
        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))
        market.follow_seller(1003)
        market.history(range(400, 460), clicks=5, age_days=0.5)

        built = _build(market, interests=("cameras",), followed_sellers=(1003,))
        assert sum(built.sources.values()) == len(built.rows)
        assert Counter(row["candidate_source"] for row in built.rows) == Counter(
            {name: count for name, count in built.sources.items() if count}
        )

    def test_a_source_that_was_asked_and_found_nothing_reports_zero(self, market):
        """Zero and absent are different operational problems.

        A source that ran and found nothing eligible is an inventory question. A
        source that was never asked is a cold-start question. A provenance map that
        only counted successes would report both as silence.
        """
        _enlarge(market, LARGE)
        market.render(market.serve("feed"))
        # A category the catalogue does not stock: the source is built and asked,
        # and comes back empty.
        built = _build(market, interests=("nothing-is-in-this-category",))
        assert built.sources.get("affinity") == 0
        assert "affinity" in built.sources

    def test_a_source_the_viewer_cannot_drive_is_absent_rather_than_zero(self, market):
        _enlarge(market, LARGE)
        built = _build(market)
        assert "affinity" not in built.sources
        assert "followed" not in built.sources

    def test_the_mix_is_reported_even_when_the_pool_came_back_empty(self, market, caplog):
        """The moment an operator most needs to know which questions were asked."""
        cur = market.conn.cursor()
        cur.execute("UPDATE marketplace_listings SET status='draft'")
        market.conn.commit()

        built = _build(market, interests=("cameras",))
        assert not built.rows
        assert built.sources.get("affinity") == 0
        assert metrics.observe_sources(built.sources, surface="feed") == "affinity=0,rotation=0"

    def test_an_empty_mix_is_not_logged_as_one(self, market, caplog):
        # The return value *and* the absence of a log line. Asserting only the
        # return left `if not mix: return ""` mutable to `if False:` — the function
        # kept returning "" while emitting `mix=` on every empty pool, which reads
        # in a log search as a pool that was built from no sources at all rather
        # than one that was never asked.
        import logging

        with caplog.at_level(logging.DEBUG, logger=metrics.LOGGER.name):
            assert metrics.observe_sources({}, surface="feed") == ""
        assert not [
            record for record in caplog.records
            if "COMMERCE_DISCOVERY_POOL_SOURCES" in record.getMessage()
        ]

    def test_the_engine_reports_the_mix_on_every_serve(self, market, monkeypatch):
        """Through :func:`engine.serve`, including the empty-pool path.

        Its sibling calls ``metrics.observe_sources`` directly, which proves the
        renderer works but not that anything calls it. Deleting the engine's call
        outright left the file green.
        """
        seen: list = []
        real = metrics.observe_sources

        def record(sources, *, surface):
            seen.append((surface, dict(sources or {})))
            return real(sources, surface=surface)

        monkeypatch.setattr(metrics, "observe_sources", record)

        _enlarge(market, LARGE)
        market.render(market.serve("feed"))
        assert seen, "a healthy serve never reported its mix"
        assert seen[-1][1], "the mix was reported but empty on a full pool"

        # And on the path where it matters most: a pool that came back with nothing
        # is exactly when an operator needs to know which questions were asked.
        seen.clear()
        cur = market.conn.cursor()
        cur.execute("UPDATE marketplace_listings SET status='sold'")
        market.conn.commit()
        pool.reset_span_cache()
        assert market.serve("feed") == []
        assert seen, "an empty pool reported no mix at all"


class TestTheOptOutReachesRetrieval:
    """Opting out of personalised recommendations has to stop the *fetch*."""

    def test_a_viewer_who_opted_out_gets_the_untargeted_query(self, market, monkeypatch):
        from services.commerce_discovery import engine

        _enlarge(market, LARGE)
        _split_catalogue(market)
        market.render(market.serve("feed"))
        _clicked(market, range(1, 21))

        # The opt-out is injected by replacing the resolved policy's one field
        # rather than by feeding preference JSON in. The `market` fixture has
        # already wrapped `viewer_policy` with its own stub-preferences lambda that
        # swallows a second `load_preferences` kwarg, so passing preferences here
        # silently changed nothing and the test failed against correct code.
        # Replacing the field asserts the thing that matters — that the engine
        # honours `policy.personalized` at *retrieval* — without fighting the
        # fixture over how the flag got there.
        real_policy = preferences.viewer_policy

        def opted_out(cursor, user_id, **kwargs):
            return dataclasses.replace(
                real_policy(cursor, user_id, **kwargs), personalized=False
            )

        monkeypatch.setattr(preferences, "viewer_policy", opted_out)

        seen: list = []
        real_build = pool.build

        def record(cur, **kwargs):
            seen.append((tuple(kwargs.get("interests") or ()),
                         tuple(kwargs.get("followed_sellers") or ())))
            return real_build(cur, **kwargs)

        monkeypatch.setattr(pool, "build", record)
        market.serve("feed")

        assert seen, "the engine never reached the pool"
        # Not merely "the interests were not scored" — they were not *fetched on*.
        assert seen[-1] == ((), ())
        assert engine is not None  # the import is the point: this is the real path
