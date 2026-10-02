"""Pulse Loop: the schedule, and the promise that a scheduled pair stays a pair.

Why this suite leans on one regression
--------------------------------------
PulseDrop could already publish a post and a Reel for the same product. What it
could not do was publish them *together*, and production proves it: every recent
published run is Reel-only with reason ``signal_unavailable``, because the
Signals all went out in one burst and then sat behind the fourteen-day product
cooldown while the Reel cooldowns had been set to zero. ``PAIR_EVERY_POST`` was
on the whole time and produced no pairs at all.

Nothing was broken, which is the interesting part. Every rule did exactly what
it was configured to do. The two surfaces simply had no shared unit of work, so
there was nothing in the system capable of noticing they had come apart.

So the assertions that matter most here are the ones about the *unit*:
``test_a_product_cooldown_does_not_block_its_own_scheduled_campaign`` is the
direct inverse of the production failure, and
``test_a_pair_waits_rather_than_publishing_half`` is the behaviour that replaces
it. The rest of the suite exists to stop those two being satisfied trivially —
by a loop that publishes everything twice, or one that publishes nothing.

Why the catalogue and the publisher are stubbed and the schedule is not
-----------------------------------------------------------------------
Same division as ``test_curator``: the eligibility gate, the account, the
renderer and the publisher each have their own contract and their own tests, and
the gate in particular is default-deny and would reject every fixture here.

What is real is the part under test — the ``pulsedrop_campaigns`` table, the
planner's arithmetic, the row-level claim, the state machine and the run row.
Several assertions below could not be made against a mock at all: that planning
twice does not duplicate a campaign is a statement about a UNIQUE constraint,
and that a crashed publisher's row can be reclaimed is a statement about what
survives in the table when the process does not.

Why the publisher stub records a publication row
------------------------------------------------
Because the row *is* the history that ``diversity`` measures every cooldown
against. A stub that only remembered the call in Python would let the cooldown
tests pass while reading an empty table — they would be asserting against a
rule that never fired. The stub writes the five columns ``History.load``
selects, in the state production treats as published, so the cooldowns under
test are the real ones operating on their real input.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.pulsedrop import (
    account,
    campaigns,
    config,
    curator,
    distribution,
    diversity,
    eligibility,
    ops,
    publisher,
    reel_composer,
)

#: A fixed instant. Every window in this subsystem is measured in hours, so a
#: real ``utcnow`` would make the arithmetic depend on when CI happened to run.
NOW = datetime(2026, 10, 1, 12, 0, 0)

PULSEDROP_USER_ID = 4242

#: Spacing the planner is told to use, and the number most assertions about the
#: schedule are expressed in. 3600 rather than production's 5400 only because
#: round hours make an expected timestamp readable in a failure message.
SPACING = 3600

#: When the first campaign of a queue planned at :data:`NOW` comes due.
#:
#: Not ``NOW``. The planner appends after the schedule tail, and an empty table's
#: tail is the planning instant, so the first campaign lands one spacing interval
#: out. Worth stating as a constant because the alternative reading — plan and
#: publish in the same tick — is the intuitive one and is wrong: it would make a
#: deploy, or a queue that has just drained, publish immediately.
DUE = NOW + timedelta(seconds=SPACING)


def _listing(listing_id, *, seller=10, category="gadgets", images=2, video=""):
    return {
        "id": listing_id,
        "seller_user_id": seller,
        "title": f"Product {listing_id}",
        "category": category,
        "price_label": "$19.99",
        "currency": "USD",
        "status": "published",
        "created_at": (NOW - timedelta(hours=10)).isoformat(),
        "image_urls": [f"https://cdn.test/{listing_id}-{n}.jpg" for n in range(images)],
        "video_url": video,
    }


#: Four products over three sellers. More than one seller because declustering
#: is one of the things under test and a single-seller catalogue cannot show it;
#: four rather than three so a target depth of 2 leaves something unplanned.
CATALOGUE = [
    _listing(201, video="https://cdn.test/201.mp4"),
    _listing(202, seller=10, category="gadgets", video="https://cdn.test/202.mp4"),
    _listing(203, seller=11, category="home", video="https://cdn.test/203.mp4"),
    _listing(204, seller=12, category="toys", video="https://cdn.test/204.mp4"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _reset_tables() -> None:
    """Empty PulseDrop, on its own connection.

    Deliberately not the suite's ``cursor`` fixture, which holds its connection
    open for the body of the test. The subject opens its own, and SQLite gives a
    writer the whole file: a truncation left uncommitted elsewhere makes every
    call under test fail with ``database is locked`` once the busy timeout
    expires. Learned from ``test_curator``, which says the same thing.
    """
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        for table in (
            "pulsedrop_campaigns",
            "pulsedrop_leases",
            "pulsedrop_publications",
            "pulsedrop_renders",
            "pulsedrop_runs",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _empty_pulsedrop():
    """A fresh, empty schedule before and after every test.

    The loop is stateful by construction — the horizon is a table, the cycle is
    derived from it, and the publication history is what the pacing rules are
    measured against. Letting that accumulate would make this one long narrative
    in which test seven passes only because tests one to six ran first.
    """
    _reset_tables()
    config.invalidate_cache()
    yield
    _reset_tables()
    config.invalidate_cache()


class _Publishes:
    """Records what the publisher was asked to do, and answers success.

    Also writes the ``pulsedrop_publications`` row the real publisher writes.
    See the module docstring: without it the cooldown assertions would be
    measuring an empty table.
    """

    def __init__(self):
        self.signals: list[int] = []
        self.reels: list[int] = []
        self.signal_fails: set[int] = set()
        self.reel_fails: set[int] = set()

    def _record(self, item, surface: str, now) -> None:
        # Timestamps go through the publisher's own `_now_text` rather than a
        # hand-rolled `isoformat`, and that is not tidiness. `diversity`
        # compares `published_at` against a cutoff as a *string*, so a stamp
        # written with a ' ' separator sorts below every 'T'-separated cutoff of
        # the same day and reads as older than any window -- every cooldown in
        # the module then silently fails to fire. This stub exists so the
        # cooldown tests run against real history (see the module docstring);
        # writing the wrong separator here would have let those tests pass while
        # asserting against a rule that never ran. Borrowing the production
        # helper means the format cannot drift away from the format under test.
        from services import db as platform_db

        listing = item.listing
        stamp = publisher._now_text(now)
        conn = platform_db.connect()
        try:
            conn.execute(
                """
                INSERT INTO pulsedrop_publications
                    (idempotency_key, surface, listing_id, seller_user_id, category,
                     state, published_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"test:{surface}:{listing['id']}:{stamp}",
                    surface,
                    int(listing["id"]),
                    int(listing.get("seller_user_id") or 0),
                    str(listing.get("category") or "").strip().lower(),
                    publisher.PUBLISHED,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def signal(self, item, *, now=None):
        listing_id = int(item.listing["id"])
        if listing_id in self.signal_fails:
            return publisher.Result(False, "moderation_blocked", 0, 0, 0)
        self.signals.append(listing_id)
        self._record(item, diversity.SIGNAL, now or NOW)
        return publisher.Result(True, "", 1, 900 + listing_id, 0)

    def reel(self, item, render, *, feed_visible=True, now=None):
        listing_id = int(item.listing["id"])
        if listing_id in self.reel_fails:
            return publisher.Result(False, "attach_failed", 0, 0, 0)
        self.reels.append(listing_id)
        self._record(item, diversity.REEL, now or NOW)
        return publisher.Result(True, "", 2, 7000 + listing_id, 55)

    @property
    def pairs(self) -> set[int]:
        """Products that reached *both* surfaces. The point of the feature."""
        return set(self.signals) & set(self.reels)


class _Renders:
    """A render queue whose readiness the test controls.

    Defaults to ready, because "the encode is already done" is what prewarming
    is supposed to achieve and so it is the normal case, not the happy one. Tests
    about waiting set ``ready = False`` explicitly.
    """

    def __init__(self):
        self.ready = True
        self.enqueued: list[int] = []

    def find_or_enqueue(self, listing, source_kind, *, now=None):
        listing_id = int(listing.get("id") or 0)
        self.enqueued.append(listing_id)
        if not self.ready:
            return {"id": listing_id, "state": reel_composer.PENDING, "video_url": ""}
        return {
            "id": listing_id,
            "state": reel_composer.READY,
            "video_url": f"https://cdn.test/{listing_id}.mp4",
        }


@pytest.fixture()
def loop(monkeypatch):
    """A running Pulse Loop wired to fixtures. Yields the two recorders.

    ``eligibility.rejection_reason`` is narrowed to the single rule these tests
    need live — PulseDrop must never republish its own output — rather than
    disabled, so the self-reference guard is still being exercised.
    """
    monkeypatch.setenv("PULSEDROP_ENABLED", "1")
    monkeypatch.setenv("PULSEDROP_LOOP_ENABLED", "1")
    monkeypatch.setenv("PULSEDROP_MIN_PUBLISH_INTERVAL_SECONDS", str(SPACING))
    # Caps raised out of the way. They have their own test below; leaving
    # production's 8-and-3 in place would make every other test in this file
    # depend on how many campaigns it happened to publish first.
    monkeypatch.setenv("PULSEDROP_DAILY_PUBLICATION_CAP", "100")
    monkeypatch.setenv("PULSEDROP_DAILY_REEL_CAP", "100")
    config.invalidate_cache()

    monkeypatch.setattr(
        eligibility, "fetch_candidates",
        lambda cur, limit, now=None: [dict(c) for c in CATALOGUE],
    )
    monkeypatch.setattr(
        eligibility, "rejection_reason",
        lambda listing, *, system_user_ids=(): (
            "seller_is_system"
            if int(listing.get("seller_user_id") or 0) in set(system_user_ids)
            else ""
        ),
    )
    monkeypatch.setattr(
        eligibility, "image_urls", lambda listing: list(listing.get("image_urls") or [])
    )
    monkeypatch.setattr(
        eligibility, "video_source", lambda listing: str(listing.get("video_url") or "")
    )
    # The listing the publisher re-reads inside the transaction. Keyed off the
    # fixture catalogue so a test can withdraw a product by mutating one dict.
    by_id = {int(c["id"]): c for c in CATALOGUE}
    monkeypatch.setattr(
        eligibility, "revalidate",
        lambda cur, listing_id, *, system_user_ids=(), now=None: (
            (dict(by_id[int(listing_id)]), "")
            if int(listing_id) in by_id
            else ({}, "vanished")
        ),
    )
    monkeypatch.setattr(account, "ensure_account", lambda: PULSEDROP_USER_ID)
    # ffmpeg is a deploy dependency, absent on a laptop, and its absence would
    # turn every composed-Reel case into a skip for the wrong reason.
    monkeypatch.setattr(distribution, "_render_capable", lambda: True)
    monkeypatch.setattr(publisher, "reap_stale_claims", lambda *, now=None: 0)

    renders = _Renders()
    monkeypatch.setattr(reel_composer, "find_or_enqueue", renders.find_or_enqueue)
    monkeypatch.setattr(reel_composer, "run_pending", lambda limit=1, now=None: {})

    recorder = _Publishes()
    monkeypatch.setattr(publisher, "publish_signal", recorder.signal)
    monkeypatch.setattr(publisher, "publish_reel", recorder.reel)
    return recorder, renders


def _rows(where: str = "", params: tuple = ()) -> list[dict]:
    """Campaign rows, as dicts, newest id last. For asserting on the table."""
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        cur = conn.cursor()
        clause = f" WHERE {where}" if where else ""
        cur.execute(f"SELECT * FROM pulsedrop_campaigns{clause} ORDER BY id ASC", params)
        return [dict(row) for row in cur.fetchall() or []]
    finally:
        conn.close()


def _publish_one(moment=None):
    """Plan, then run the first campaign at the moment it actually comes due.

    The planner appends after the schedule tail, and the tail of an empty table
    is ``now`` — so the first campaign of a cold start is scheduled one spacing
    interval out rather than immediately. That is deliberate: a deploy, or a
    queue that has just drained, must not answer by publishing on the spot. It
    does mean every publishing test has to step the clock to reach its own first
    campaign, which is what this helper is for.
    """
    base = moment or NOW
    campaigns.plan(now=base)
    return campaigns.execute_due(now=base + timedelta(seconds=SPACING))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


class TestPlanningTheHorizon:
    def test_fills_the_queue_to_the_target_depth(self, loop, monkeypatch):
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "3")
        config.invalidate_cache()
        result = campaigns.plan(now=NOW)
        assert result["outcome"] == campaigns.PLANNED
        assert result["planned"] == 3
        assert len(_rows()) == 3

    def test_spaces_campaigns_by_the_minimum_gap_between_posts(self, loop, monkeypatch):
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "3")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        times = [datetime.fromisoformat(r["scheduled_for"]) for r in _rows()]
        gaps = {int((b - a).total_seconds()) for a, b in zip(times, times[1:])}
        # One gap, equal to the configured floor. A schedule whose spacing drifts
        # is a schedule that eventually bursts.
        assert gaps == {SPACING}

    def test_a_second_planning_run_appends_rather_than_restacking(self, loop, monkeypatch):
        """The tail is read from the table, not recomputed from ``now``.

        Without this, two planning runs an hour apart both start counting from
        their own instant and the second one schedules on top of the first —
        producing a horizon that looks the right depth and publishes in bursts.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        first_tail = max(r["scheduled_for"] for r in _rows())

        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        campaigns.plan(now=NOW, force=True)
        later = [r for r in _rows() if r["scheduled_for"] > first_tail]
        assert len(later) == 2

    def test_one_campaign_per_product_per_cycle(self, loop, monkeypatch):
        """Planning twice cannot double-book a product.

        This is an assertion about the UNIQUE constraint on ``campaign_key``,
        which is why it is made against the table rather than against a return
        value: the planner's own bookkeeping excludes products already in the
        cycle, and the constraint is the thing that still holds when two
        instances plan at the same moment and neither saw the other's rows.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        campaigns.plan(now=NOW, force=True)
        listing_ids = [r["listing_id"] for r in _rows()]
        assert sorted(listing_ids) == sorted(listing_ids and set(listing_ids))
        assert len(listing_ids) == 4

    def test_does_not_touch_the_catalogue_while_the_queue_is_deep(self, loop, monkeypatch):
        """The low watermark, asserted by counting catalogue reads.

        Asserting on the outcome alone would not distinguish "returned
        satisfied without looking" from "read two hundred rows, ranked them all
        and then returned satisfied". The second is correct and is a query
        budget spent to do nothing, every tick, forever.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        monkeypatch.setenv("PULSEDROP_LOOP_MIN_DEPTH", "2")
        config.invalidate_cache()
        campaigns.plan(now=NOW)

        reads = []
        original = eligibility.fetch_candidates
        monkeypatch.setattr(
            eligibility, "fetch_candidates",
            lambda cur, limit, now=None: (reads.append(1), original(cur, limit, now=now))[1],
        )
        result = campaigns.plan(now=NOW)
        assert result["outcome"] == campaigns.QUEUE_SATISFIED
        assert reads == []

    def test_force_plans_through_the_watermark(self, loop, monkeypatch):
        """What enrollment needs: a new product joins without the queue draining."""
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        monkeypatch.setenv("PULSEDROP_LOOP_MIN_DEPTH", "1")
        config.invalidate_cache()
        monkeypatch.setattr(
            eligibility, "fetch_candidates",
            lambda cur, limit, now=None: [dict(CATALOGUE[0]), dict(CATALOGUE[1])],
        )
        campaigns.plan(now=NOW)
        assert len(_rows()) == 2

        monkeypatch.setattr(
            eligibility, "fetch_candidates",
            lambda cur, limit, now=None: [dict(c) for c in CATALOGUE],
        )
        assert campaigns.plan(now=NOW)["outcome"] == campaigns.QUEUE_SATISFIED
        assert campaigns.plan(now=NOW, force=True)["outcome"] == campaigns.PLANNED
        assert len(_rows()) == 4

    def test_reconcile_enrolls_a_new_product_without_disturbing_the_schedule(
        self, loop, monkeypatch
    ):
        """Auto-enrollment, asserted on the rows it must *not* touch.

        That the newcomer gets a campaign is the easy half. The half worth
        pinning is that the five already scheduled keep their release times:
        ``reconcile`` is ``plan(force=True)``, and a force that re-planned the
        products already in the cycle would rewrite a horizon an operator has
        been reading all week — the schedule would be correct and different
        every tick, which is indistinguishable from the drift this module
        exists to prevent.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        monkeypatch.setenv("PULSEDROP_LOOP_MIN_DEPTH", "1")
        config.invalidate_cache()
        catalogue = [dict(c) for c in CATALOGUE]
        monkeypatch.setattr(
            eligibility, "fetch_candidates", lambda cur, limit, now=None: list(catalogue)
        )
        campaigns.plan(now=NOW)
        before = {r["listing_id"]: r["scheduled_for"] for r in _rows()}
        tail = max(before.values())

        # A seller lists something an hour later. The queue is still deep, so
        # replenishment has nothing to say about it.
        catalogue.insert(0, _listing(205, seller=13, category="pets"))
        later = NOW + timedelta(hours=1)
        assert campaigns.plan(now=later)["outcome"] == campaigns.QUEUE_SATISFIED

        result = campaigns.reconcile(now=later)
        assert result["outcome"] == campaigns.PLANNED
        assert result["planned"] == 1
        after = {r["listing_id"]: r["scheduled_for"] for r in _rows()}
        assert set(after) == set(before) | {205}
        assert {k: v for k, v in after.items() if k in before} == before
        # Appended, not spliced: the newcomer takes the next free slot rather
        # than its rank's position among campaigns already promised.
        assert after[205] > tail

    def test_reconcile_is_a_no_op_when_the_catalogue_has_not_grown(self, loop, monkeypatch):
        """Which is what makes it safe to run on every satisfied tick.

        ``force`` skips the watermark, so nothing upstream of this is counting
        rows — the only thing standing between a per-tick enrollment pass and a
        per-tick rewrite of the horizon is that the planner excludes products
        already in the cycle.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        before = _rows()
        result = campaigns.reconcile(now=NOW + timedelta(hours=1))
        assert result["planned"] == 0
        assert result["outcome"] == campaigns.SUPPLY_LIMITED
        assert [(r["listing_id"], r["scheduled_for"]) for r in _rows()] == [
            (r["listing_id"], r["scheduled_for"]) for r in before
        ]

    def test_interleaves_sellers_rather_than_scheduling_them_in_blocks(self, loop, monkeypatch):
        """201 and 202 share a seller and a category, so they must not be adjacent.

        In the loop this round-robin *is* the seller-fairness mechanism — the
        publisher's per-candidate rules are not consulted, for the reasons
        ``execute_due`` gives — so it is load-bearing rather than cosmetic.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        order = [r["listing_id"] for r in _rows()]
        positions = {order.index(201), order.index(202)}
        assert abs(max(positions) - min(positions)) > 1

    def test_reports_supply_limited_rather_than_repeating_products(self, loop, monkeypatch):
        """§47. The outcome is the deliverable, not a fallback.

        A loop asked for 50 campaigns from a 4-product catalogue has exactly one
        honest answer. The tempting alternatives — schedule the same four
        thirteen times, or return "planned 4" and let the caller infer — are
        both worse than saying so.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "50")
        # The product cooldown is left at its default on purpose. It is the gate
        # on opening the next generation, so zeroing it here would let the second
        # call roll the cycle and schedule the same four products again — which
        # is the correct reading of "no cooldown", and so would be testing the
        # operator's override rather than §47. Exhaustion is a property of a
        # cycle, and the cooldown is what holds the cycle shut.
        config.invalidate_cache()
        first = campaigns.plan(now=NOW)
        assert first["planned"] == len(CATALOGUE)

        second = campaigns.plan(now=NOW)
        assert second["outcome"] == campaigns.SUPPLY_LIMITED
        assert second["planned"] == 0
        # And nothing was scheduled twice to paper over it.
        assert len(_rows()) == len(CATALOGUE)

    def test_a_full_cycle_waits_for_the_product_cooldown_before_the_next_one(
        self, loop, monkeypatch
    ):
        """Generations are paced by ``product_cooldown_hours``, not by a new knob.

        Reusing it is the point. "How long before members should see this product
        again" is a question the subsystem had already answered; a separate
        cycle-gap setting would be a second copy of the same policy, free to
        drift from the first.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "50")
        monkeypatch.setenv("PULSEDROP_PRODUCT_COOLDOWN_HOURS", "336")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        assert campaigns.plan(now=NOW)["outcome"] == campaigns.SUPPLY_LIMITED

        later = campaigns.plan(now=NOW + timedelta(hours=400))
        assert later["outcome"] == campaigns.PLANNED
        assert later["cycle"] == 2
        assert {r["cycle"] for r in _rows()} == {1, 2}

    def test_an_empty_catalogue_is_not_reported_as_limited_supply(self, loop, monkeypatch):
        """Distinct facts, distinct outcomes.

        "Nothing is publishable" is usually a gate or a catalogue problem an
        engineer can act on. "Everything publishable is already scheduled" is a
        business fact nobody can fix by debugging. Collapsing them would make
        the one actionable signal unreadable.
        """
        monkeypatch.setattr(eligibility, "fetch_candidates", lambda cur, limit, now=None: [])
        config.invalidate_cache()
        assert campaigns.plan(now=NOW)["outcome"] == campaigns.NO_CANDIDATES

    def test_the_loop_switch_alone_stops_planning(self, loop, monkeypatch):
        """§92: the loop must be revertible without taking PulseDrop down."""
        monkeypatch.setenv("PULSEDROP_LOOP_ENABLED", "0")
        config.invalidate_cache()
        assert campaigns.plan(now=NOW)["outcome"] == campaigns.DISABLED
        assert _rows() == []
        assert config.enabled() is True


# ---------------------------------------------------------------------------
# Publishing — the pair
# ---------------------------------------------------------------------------


class TestPublishingAPair:
    def test_publishes_both_surfaces_for_one_product_at_one_time(self, loop):
        """The headline requirement: one campaign, two posts, same moment."""
        recorder, _ = loop
        result = _publish_one()
        assert result["outcome"] == campaigns.PUBLISHED
        listing_id = result["listing_id"]
        assert recorder.pairs == {listing_id}
        assert result["signal_post_id"] == 900 + listing_id
        assert result["reel_post_id"] == 7000 + listing_id

    def test_publishes_one_campaign_per_call_not_the_whole_horizon(self, loop, monkeypatch):
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        recorder, _ = loop
        campaigns.plan(now=NOW)
        campaigns.execute_due(now=DUE)
        assert len(recorder.signals) == 1
        still_open = _rows("state = ?", (campaigns.SCHEDULED,))
        assert len(still_open) == 3

    def test_a_published_campaign_is_not_published_again(self, loop):
        """Idempotency, §40, asserted across a repeat call at the same instant.

        The claim is conditional on the row still being ``scheduled``, so the
        second call cannot take it. This is the assertion that a retried tick —
        or a second worker that stole the lease on a clock skew — cannot put two
        posts on a seller's product.
        """
        recorder, _ = loop
        first = _publish_one()
        assert first["outcome"] == campaigns.PUBLISHED
        second = campaigns.execute_due(now=DUE)
        assert second["outcome"] == campaigns.NONE_DUE
        assert len(recorder.signals) == 1
        assert len(recorder.reels) == 1

    def test_a_pair_waits_rather_than_publishing_half(self, loop):
        """The behaviour that replaces the production failure.

        An unfinished encode defers the *whole* campaign. The old path published
        the Signal immediately and left the Reel to arrive whenever — which, in
        production, meant days later or never. Here nothing publishes until both
        halves can.
        """
        recorder, renders = loop
        renders.ready = False
        result = _publish_one()
        assert result["outcome"] == campaigns.AWAITING_RENDER
        assert recorder.signals == []
        assert recorder.reels == []
        # And it is back in the queue, not parked: `scheduled`, so the stale
        # claim reaper does not mistake polite waiting for a crash.
        row = _rows()[0]
        assert row["state"] == campaigns.SCHEDULED
        assert row["attempts"] == 1

    def test_a_pair_that_cannot_render_eventually_ships_its_working_half(
        self, loop, monkeypatch
    ):
        """Patience is bounded, and running out of it is reported as PARTIAL.

        Waiting forever would let one unencodable product hold a slot in the
        horizon indefinitely. Publishing the Signal silently would hide that the
        promise of a pair was broken. So the half ships and the state says so.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_MAX_ATTEMPTS", "2")
        config.invalidate_cache()
        recorder, renders = loop
        renders.ready = False
        campaigns.plan(now=NOW)

        first = campaigns.execute_due(now=DUE)
        assert first["outcome"] == campaigns.AWAITING_RENDER
        # Past the deferral the first attempt set, or the campaign simply is not
        # due yet and the second call would report `none_due` — which would make
        # this test pass for the wrong reason once patience was unbounded again.
        second = campaigns.execute_due(now=DUE + timedelta(seconds=SPACING))
        assert second["outcome"] == campaigns.PARTIAL
        assert len(recorder.signals) == 1
        assert recorder.reels == []
        assert _rows()[0]["state"] == campaigns.PARTIAL

    def test_a_product_the_catalogue_withdrew_is_released_not_failed(self, loop, monkeypatch):
        """A sold-out product is not a failure, and must not consume attempts.

        Released rather than failed because the distinction drives behaviour:
        a released campaign's product returns to the pool for a later cycle,
        where a failed one would be read as "this product broke the loop".
        """
        recorder, _ = loop
        campaigns.plan(now=NOW)
        monkeypatch.setattr(
            eligibility, "revalidate",
            lambda cur, listing_id, *, system_user_ids=(), now=None: ({}, "out_of_stock"),
        )
        result = campaigns.execute_due(now=DUE)
        assert result["outcome"] == campaigns.RELEASED
        assert result["reason"] == "out_of_stock"
        assert recorder.signals == []
        assert _rows()[0]["released_reason"] == "out_of_stock"

    def test_a_released_product_can_be_scheduled_again_in_the_same_cycle(
        self, loop, monkeypatch
    ):
        """Churn must not retire a product.

        ``_listings_in_cycle`` excludes released rows for this reason. Counting
        them as spoken for would hold the products most likely to come back into
        stock out of the whole generation — punishing them for the catalogue's
        volatility.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "1")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        released_id = _rows()[0]["listing_id"]
        monkeypatch.setattr(
            eligibility, "revalidate",
            lambda cur, listing_id, *, system_user_ids=(), now=None: ({}, "out_of_stock"),
        )
        campaigns.execute_due(now=DUE)

        campaigns.plan(now=NOW, force=True)
        open_ids = {r["listing_id"] for r in _rows() if r["state"] == campaigns.SCHEDULED}
        assert released_id in open_ids

    def test_both_surfaces_failing_is_failed_and_not_partial(self, loop):
        recorder, _ = loop
        campaigns.plan(now=NOW)
        listing_id = _rows()[0]["listing_id"]
        recorder.signal_fails.add(listing_id)
        recorder.reel_fails.add(listing_id)
        result = campaigns.execute_due(now=DUE)
        assert result["outcome"] == campaigns.FAILED
        assert _rows()[0]["state"] == campaigns.FAILED


# ---------------------------------------------------------------------------
# Pacing — which of diversity's rules the loop honours
# ---------------------------------------------------------------------------


class TestWhichRulesStillApply:
    def test_a_product_cooldown_does_not_block_its_own_scheduled_campaign(
        self, loop, monkeypatch
    ):
        """The direct inverse of the production failure. Read the module docstring.

        Production's Signals went out in a burst and then sat behind a 336-hour
        product cooldown while the Reel cooldowns were zero, so every later run
        published a Reel alone with reason ``signal_unavailable``. Pairing was on
        the whole time and produced no pairs.

        A schedule that re-asked that cooldown would reproduce it exactly: the
        planner writes a row *because* the product is due, and a cooldown
        evaluated at publish time would then release it. So the cycle supersedes
        it — ``campaign_key`` guarantees one campaign per product per generation
        and ``_maybe_roll_cycle`` paces the generations — and this test fails if
        anyone reintroduces the per-candidate check.
        """
        monkeypatch.setenv("PULSEDROP_PRODUCT_COOLDOWN_HOURS", "336")
        monkeypatch.setenv("PULSEDROP_CROSS_FORMAT_COOLDOWN_HOURS", "48")
        monkeypatch.setenv("PULSEDROP_SELLER_COOLDOWN_HOURS", "24")
        monkeypatch.setenv("PULSEDROP_CATEGORY_COOLDOWN_HOURS", "12")
        monkeypatch.setenv("PULSEDROP_REEL_MIN_INTERVAL_SECONDS", "21600")
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        recorder, _ = loop
        campaigns.plan(now=NOW)

        # Four campaigns, one per hour, all with every cooldown above in force.
        for index in range(4):
            moment = NOW + timedelta(seconds=SPACING * (index + 1))
            result = campaigns.execute_due(now=moment)
            assert result["outcome"] == campaigns.PUBLISHED, result

        # Every one of them a pair. This is the number that was zero in prod.
        assert len(recorder.pairs) == 4

    def test_the_daily_cap_still_stops_the_loop(self, loop, monkeypatch):
        """The circuit breaker is kept, because a schedule cannot see live volume.

        Per-candidate cooldowns are superseded by the cycle; account-level caps
        are not, and must not be. If a reaper misfires or a queue is replanned,
        this is the only thing standing between that and a burst on a real feed.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        monkeypatch.setenv("PULSEDROP_DAILY_PUBLICATION_CAP", "1")
        config.invalidate_cache()
        recorder, _ = loop
        campaigns.plan(now=NOW)
        assert campaigns.execute_due(now=DUE)["outcome"] == campaigns.PUBLISHED

        paced = campaigns.execute_due(now=NOW + timedelta(seconds=SPACING * 2))
        assert paced["outcome"] == campaigns.PACED
        assert paced["reason"] == "daily_cap"
        assert len(recorder.signals) == 1

    def test_being_paced_does_not_consume_an_attempt(self, loop, monkeypatch):
        """Attempts retire campaigns that cannot work. A full day is not that.

        Letting the cap spend a campaign's three tries would park a perfectly
        healthy pair as failed for the crime of being scheduled on a busy day —
        and it would do it silently, because the row would look like any other
        exhausted one.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        monkeypatch.setenv("PULSEDROP_DAILY_PUBLICATION_CAP", "0")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        for index in range(4):
            result = campaigns.execute_due(now=NOW + timedelta(seconds=SPACING * (index + 1)))
            assert result["outcome"] == campaigns.PACED
        assert all(row["attempts"] == 0 for row in _rows())
        assert all(row["state"] == campaigns.SCHEDULED for row in _rows())

    def test_the_minimum_gap_between_posts_still_applies(self, loop, monkeypatch):
        """The floor only bites on a backed-up queue, so that is what this builds.

        Worth being explicit about, because the obvious version of this test
        cannot fail: the planner spaces campaigns by ``min_publish_interval``
        itself, so a schedule running on time agrees with the floor by
        construction and no amount of stepping the clock forward will produce a
        conflict.

        The floor earns its keep in the other case — a worker that was down for
        hours comes back to four campaigns whose due times have all passed.
        Every one of them is due, and releasing them would empty the horizon
        into a single burst, which is precisely the posting pattern PulseDrop
        exists to avoid. So the clock jumps past the whole queue here, and the
        assertion is that the second campaign waits even though the schedule
        says it is late.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        campaigns.plan(now=NOW)

        caught_up = NOW + timedelta(seconds=SPACING * 5)
        assert campaigns.execute_due(now=caught_up)["outcome"] == campaigns.PUBLISHED

        soon = campaigns.execute_due(now=caught_up + timedelta(seconds=60))
        assert soon["outcome"] == campaigns.PACED
        assert soon["reason"] == "min_interval"


# ---------------------------------------------------------------------------
# Preparation and self-healing
# ---------------------------------------------------------------------------


class TestPreparationAndRecovery:
    def test_prewarm_enqueues_renders_before_the_due_time(self, loop, monkeypatch):
        """Why a pair is possible at all.

        An encode takes minutes and a campaign has an exact due time. Asking for
        the render ahead of that time is the difference between "both publish
        together" and "the Signal publishes and the Reel follows whenever".
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        monkeypatch.setenv("PULSEDROP_LOOP_RENDER_LEAD_SECONDS", str(SPACING * 2))
        config.invalidate_cache()
        _, renders = loop
        campaigns.plan(now=NOW)
        campaigns.prewarm(now=NOW)
        assert renders.enqueued, "nothing was queued ahead of the due time"

    def test_prewarm_does_not_release_a_campaign_it_cannot_read(self, loop, monkeypatch):
        """Prewarming is an optimisation and must not make editorial decisions.

        A transient failure to read a listing would otherwise silently drop a
        scheduled pair. ``execute_due`` is the only caller allowed to conclude
        that a product has gone away, and it will reach the same answer a minute
        later with the authority to act on it.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        monkeypatch.setenv("PULSEDROP_LOOP_RENDER_LEAD_SECONDS", str(SPACING * 2))
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        monkeypatch.setattr(
            eligibility, "revalidate",
            lambda cur, listing_id, *, system_user_ids=(), now=None: ({}, "vanished"),
        )
        campaigns.prewarm(now=NOW)
        assert all(row["state"] == campaigns.SCHEDULED for row in _rows())

    def test_a_crashed_publisher_leaves_a_campaign_that_can_be_reclaimed(
        self, loop, monkeypatch
    ):
        """Crash recovery, asserted on what survives in the table.

        A campaign left ``publishing`` by a killed process is indistinguishable
        from one being published right now, which is why the reaper keys on the
        claim's age rather than on its existence. Without it the horizon leaks a
        slot per crash — permanently, and invisibly.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "1")
        monkeypatch.setenv("PULSEDROP_LEASE_SECONDS", "300")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        campaign_id = _rows()[0]["id"]

        from services import db as platform_db

        conn = platform_db.connect()
        try:
            conn.execute(
                "UPDATE pulsedrop_campaigns SET state=?, claimed_by=?, claimed_at=? WHERE id=?",
                (campaigns.PUBLISHING, "dead-host:1", NOW.isoformat(sep=" ",
                 timespec="seconds"), campaign_id),
            )
            conn.commit()
        finally:
            conn.close()

        # Inside two lease lengths it is still presumed alive.
        assert campaigns.reap_stale_claims(now=NOW + timedelta(seconds=120)) == 0
        assert campaigns.reap_stale_claims(now=NOW + timedelta(hours=2)) == 1
        row = _rows()[0]
        assert row["state"] == campaigns.SCHEDULED
        assert row["claimed_by"] == ""


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class TestWhatAnOperatorCanSee:
    def test_health_reports_depth_cycle_and_the_next_due_time(self, loop, monkeypatch):
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "3")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        state = campaigns.health(now=NOW)
        assert state["readable"] is True
        assert state["enabled"] is True
        assert state["depth"] == 3
        assert state["cycle"] == 1
        assert state["next_scheduled_for"]
        assert len(state["upcoming"]) == 3

    def test_health_warns_when_the_render_lead_cannot_beat_the_encode(
        self, loop, monkeypatch
    ):
        """A settings pair that is individually valid and jointly wrong.

        No single-field clamp can catch this, and its symptom is not an error —
        it is pairs quietly separating, which is the exact failure the loop
        exists to prevent. So it is reported rather than enforced.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_RENDER_LEAD_SECONDS", "60")
        monkeypatch.setenv("PULSEDROP_REEL_RENDER_TIMEOUT_SECONDS", "600")
        config.invalidate_cache()
        warnings = " ".join(campaigns.health(now=NOW).get("warnings") or [])
        assert "render lead" in warnings.lower()

    def test_the_tick_wakes_for_the_next_due_campaign_not_the_sampling_interval(
        self, loop, monkeypatch
    ):
        """Two hours is a sensible rate for reading a catalogue and the wrong
        clock for a schedule. Sampling every two hours for events an hour apart
        publishes everything late by up to the difference.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        monkeypatch.setenv("PULSEDROP_EVALUATION_INTERVAL_SECONDS", "7200")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        due_in = campaigns.seconds_until_next_due(now=NOW)
        assert due_in is not None and due_in <= SPACING
        assert curator._next_run_seconds({"outcome": curator.NOT_DUE}) <= SPACING

    def test_an_empty_schedule_falls_back_to_the_interval(self, loop, monkeypatch):
        """``None`` must not become "wake every minute forever"."""
        monkeypatch.setenv("PULSEDROP_EVALUATION_INTERVAL_SECONDS", "7200")
        config.invalidate_cache()
        assert campaigns.seconds_until_next_due(now=NOW) is None
        assert curator._next_run_seconds({"outcome": curator.NOT_DUE}) == 7200


# ---------------------------------------------------------------------------
# The operator surface
# ---------------------------------------------------------------------------


class TestTheScheduleIsInspectable:
    def test_the_horizon_is_readable_as_rows_in_publish_order(self, loop, monkeypatch):
        """The admin read, asserted on order because order is the whole point.

        A depth count cannot distinguish a varied ten-day schedule from ten days
        of the same product, which is the one thing someone is checking before
        they trust the loop to publish unattended.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        campaigns.plan(now=NOW)

        view = ops.loop_view(limit=10, now=NOW)
        assert view["readable"] is True
        assert view["shown"] == 4
        stamps = [row["scheduled_for"] for row in view["upcoming"]]
        assert stamps == sorted(stamps)
        assert all(row["surfaces"] == "signal+reel" for row in view["upcoming"])
        assert all(row["due_in"] for row in view["upcoming"])

    def test_a_campaign_whose_product_vanished_is_still_listed(self, loop, monkeypatch):
        """The LEFT JOIN, asserted directly.

        These fixtures never write ``marketplace_listings`` at all, so every
        campaign here has a missing product — which makes this the default case
        rather than an edge one, and an inner join would render the page empty
        while the schedule was full.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        view = ops.loop_view(limit=10, now=NOW)
        assert view["shown"] == 2
        assert all(not row.get("listing_title") for row in view["upcoming"])
        assert {row["listing_id"] for row in view["upcoming"]} <= {201, 202, 203, 204}

    def test_published_campaigns_leave_the_horizon_view(self, loop, monkeypatch):
        """Only open states occupy the schedule, so only they are shown.

        Otherwise the view grows forever and stops answering "what is next",
        which is the single question it exists for.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        _publish_one()
        view = ops.loop_view(limit=10, now=DUE)
        assert view["shown"] == 3
        assert campaigns.PUBLISHED not in {row["state"] for row in view["upcoming"]}

    def test_the_contradiction_warnings_reach_the_page(self, loop, monkeypatch):
        """Settings that are each valid and jointly wrong.

        Production's defaults are one of these — a Reel cap of 3 under a
        publication cap of 8 — so this is not hypothetical, and the symptom is
        not an error but pairs that quietly stop being pairs.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        monkeypatch.setenv("PULSEDROP_DAILY_REEL_CAP", "3")
        monkeypatch.setenv("PULSEDROP_DAILY_PUBLICATION_CAP", "8")
        config.invalidate_cache()
        notes = ops.loop_view(limit=5, now=NOW)["warnings"]
        assert any("Reels per day (3)" in note for note in notes)

    def test_unreadable_counts_still_show_the_schedule(self, loop, monkeypatch):
        """An unreadable loop must not take down the page that switches it off.

        The module's standing rule applied to the newest reader: the page whose
        reason for existing is the kill switch cannot be the page that 500s when
        the loop's own storage is what is broken.

        The rows are asserted present, not absent, and that is the interesting
        half. The counts and the schedule are two different reads, so one
        failing is not grounds for discarding the other — and the surviving half
        is the one that tells an operator what is about to publish, which is
        what they would otherwise go to production's database for.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "2")
        config.invalidate_cache()
        campaigns.plan(now=NOW)
        monkeypatch.setattr(
            campaigns, "health", lambda **kw: (_ for _ in ()).throw(RuntimeError("no table"))
        )
        view = ops.loop_view(limit=5, now=NOW)
        assert view["readable"] is False
        assert view["shown"] == 2
        assert [row["listing_id"] for row in view["upcoming"]]

    def test_the_whole_dashboard_survives_an_unreadable_loop(self, loop, monkeypatch):
        """And the page above it still renders. Asserted through ``dashboard``.

        ``loop_view`` degrading is only useful if the caller is not the thing
        that raises, and ``dashboard`` is what the route actually calls.
        """
        monkeypatch.setattr(
            campaigns, "health", lambda **kw: (_ for _ in ()).throw(RuntimeError("no table"))
        )
        data = ops.dashboard(limit=5, schedule_limit=5, now=NOW)
        assert data["loop"]["readable"] is False
        assert "settings" in data and "runs" in data


# ---------------------------------------------------------------------------
# The curator integration
# ---------------------------------------------------------------------------


class TestTheTickRunsTheLoop:
    def test_a_tick_publishes_a_pair_and_writes_one_run_row(self, loop, monkeypatch):
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        config.invalidate_cache()
        recorder, _ = loop
        # Planning happens inside the tick, so the first one has nothing due.
        assert curator.tick(now=NOW)["outcome"] == curator.NOT_DUE
        result = curator.tick(now=NOW + timedelta(seconds=SPACING * 2))
        assert result["outcome"] == curator.PUBLISHED
        assert result["decision"] == "signal+reel"
        assert len(recorder.pairs) == 1
        assert len(curator.recent_runs(limit=10)) == 2

    def test_the_run_log_names_catalogue_supply_limited(self, loop, monkeypatch):
        """§47 reaches the operator, not just the return value.

        The run row is where "why has PulseDrop not posted" gets answered a week
        later, so the honest answer has to be countable with a ``GROUP BY
        outcome`` rather than inferable from a reason string.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "50")
        monkeypatch.setenv("PULSEDROP_PRODUCT_COOLDOWN_HOURS", "336")
        config.invalidate_cache()
        curator.tick(now=NOW)  # plans the whole catalogue
        # Lease expires, nothing is due yet, and the catalogue is exhausted.
        result = curator.tick(now=NOW + timedelta(seconds=60))
        assert result["outcome"] == curator.SUPPLY_LIMITED
        assert curator.recent_runs(limit=1)[0]["outcome"] == curator.SUPPLY_LIMITED

    def test_a_satisfied_tick_still_enrolls_a_newly_listed_product(
        self, loop, monkeypatch
    ):
        """Phase 4's second question. Depth cannot answer "is this enrolled".

        Without this the watermark is the only thing deciding whether the
        catalogue gets read, and a product listed into a deep queue waits for
        the whole horizon to drain before it is ever considered — on a ten-day
        horizon, ten days, with every tick reporting a healthy schedule.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        monkeypatch.setenv("PULSEDROP_LOOP_MIN_DEPTH", "1")
        config.invalidate_cache()
        catalogue = [dict(c) for c in CATALOGUE]
        monkeypatch.setattr(
            eligibility, "fetch_candidates", lambda cur, limit, now=None: list(catalogue)
        )
        curator.tick(now=NOW)
        assert {r["listing_id"] for r in _rows()} == {201, 202, 203, 204}

        catalogue.append(_listing(205, seller=13, category="pets"))
        curator.tick(now=NOW + timedelta(seconds=60))
        assert {r["listing_id"] for r in _rows()} == {201, 202, 203, 204, 205}

    def test_a_satisfied_tick_over_an_unchanged_catalogue_is_not_supply_limited(
        self, loop, monkeypatch
    ):
        """The cost of asking the narrower question must not be a false alarm.

        ``reconcile`` over a catalogue with nothing new answers
        ``SUPPLY_LIMITED`` — truthfully, since it found no product to enroll.
        But that is the answer to "can I enroll anything", not to "is PulseDrop
        healthy", and a deep queue publishing on schedule is the healthiest
        state this system has. Reporting §47 from it would train an operator to
        ignore the one outcome that means the catalogue has actually run dry.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_TARGET_DEPTH", "4")
        monkeypatch.setenv("PULSEDROP_LOOP_MIN_DEPTH", "1")
        config.invalidate_cache()
        curator.tick(now=NOW)
        result = curator.tick(now=NOW + timedelta(seconds=60))
        assert result["outcome"] != curator.SUPPLY_LIMITED
        assert curator.recent_runs(limit=1)[0]["outcome"] != curator.SUPPLY_LIMITED

    def test_with_the_loop_off_the_tick_uses_the_opportunistic_path(
        self, loop, monkeypatch
    ):
        """The fallback is real, which is what makes enabling the loop reversible.

        Asserted by the absence of campaign rows rather than by the outcome: the
        two paths can both answer ``published``, and the distinguishing fact is
        that one of them schedules and the other does not.
        """
        monkeypatch.setenv("PULSEDROP_LOOP_ENABLED", "0")
        config.invalidate_cache()
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.PUBLISHED
        assert _rows() == []
