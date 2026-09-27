"""The tick: what PulseDrop decides, and — mostly — what it declines to decide.

Why this suite is shaped as a set of refusals
---------------------------------------------
``curator.tick`` has exactly one interesting success path and nine interesting
ways of doing nothing, and the nine are the product. A curator that publishes
something every two hours is easy; the brief's requirement is a curator that can
look at the whole catalogue and conclude *not this time*, and say why in a row
an operator can read a week later.

So the assertions here are almost all about an outcome code, a reason string and
a call count. The call counts matter as much as the outcomes: "an account-level
deferral stops the walk" and "a product cooldown advances it" produce the *same*
observable result — nothing published — and differ only in how much work was
done to get there. On a 200-candidate page that difference is 200 comparisons
against a pre-loaded history versus one. Asserting the outcome alone would let
the distinction rot silently.

Why the collaborators are replaced and the tick is not
-------------------------------------------------------
Everything around the decision is stubbed: the catalogue read, the eligibility
gate, the account, the publisher, the renderer. That is deliberate and it is not
a shortcut. Each of those has its own contract and its own tests — the gate's
default-deny behaviour in particular would reject every fixture here, because
these dicts carry none of the marketplace columns it reads, and the tick would
report ``none_eligible`` for every single case below.

What is *not* stubbed is the part being tested: the lease, the ranking, the
walk, the run row, and (except where a test is specifically about a decision
shape) ``distribution.decide`` itself.

Why every test resets the tables
--------------------------------
The curator is stateful by construction — the lease row says when the next tick
may start, and the publication history is what every cooldown is measured
against. A suite that let that state accumulate would be one long narrative
where test seven only passes because tests one to six ran first, and a single
insertion in the middle would move every later assertion. Each test therefore
starts from an empty PulseDrop, picks its own instant, and is readable alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.pulsedrop import (
    account,
    config,
    curator,
    distribution,
    diversity,
    eligibility,
    publisher,
    reel_composer,
)

#: A fixed instant. Real ``utcnow`` would make the cooldown arithmetic in
#: ``diversity`` depend on the wall clock, and every window in this subsystem is
#: measured in hours.
NOW = datetime(2026, 9, 26, 12, 0, 0)

#: PulseDrop's own user id under test. Its only job is to be a number the
#: curator can exclude itself by.
PULSEDROP_USER_ID = 4242


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


#: Three products, three sellers, three categories — so no diversity rule fires
#: for a reason the test did not ask for. Ranking breaks the score tie by id
#: descending, so 103 is evaluated first and 101 last; several tests below
#: depend on that order and say so.
CANDIDATES = [
    _listing(101),
    _listing(102, seller=11, category="home"),
    _listing(103, seller=12, category="toys"),
]


@pytest.fixture(autouse=True)
def _empty_pulsedrop():
    """An empty PulseDrop before every test: no lease, no history, no log.

    This deliberately does *not* use the suite's ``cursor`` fixture, which holds
    its connection open for the body of the test. ``curator.tick()`` opens its
    own connection, and SQLite gives a writer the whole file: a truncation left
    uncommitted on a second connection makes every ``tick`` in the suite fail
    with ``database is locked`` after the busy timeout expires. So the reset
    owns its connection and closes it before the subject runs.
    """
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        for table in (
            "pulsedrop_leases",
            "pulsedrop_publications",
            "pulsedrop_runs",
            "pulsedrop_renders",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    config.invalidate_cache()
    yield
    config.invalidate_cache()


class _Publishes:
    """Records what the publisher was asked to do, and answers success.

    It also writes the ``pulsedrop_publications`` row that the real publisher
    writes. That is not padding: the row *is* the history, and the next tick
    loads it to decide what is still on cooldown. A stub that only remembers the
    call in Python would let PulseDrop publish the same product on every tick
    forever and every test here would still be green, because the subject would
    be reading an empty table rather than the consequences of its own last run.
    Only the five columns ``diversity.History.load`` selects are written, and
    the state is one production actually treats as published.
    """

    def __init__(self):
        self.signals: list[int] = []
        self.reels: list[int] = []

    def _record(self, item, surface: str, now) -> None:
        from services import db as platform_db

        listing = item.listing
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
                    f"test:{surface}:{listing['id']}:{now.isoformat()}",
                    surface,
                    int(listing["id"]),
                    int(listing.get("seller_user_id") or 0),
                    str(listing.get("category") or "").strip().lower(),
                    publisher.PUBLISHED,
                    now.isoformat(sep=" ", timespec="seconds"),
                    now.isoformat(sep=" ", timespec="seconds"),
                    now.isoformat(sep=" ", timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def signal(self, item, *, now=None):
        listing_id = int(item.listing["id"])
        self.signals.append(listing_id)
        self._record(item, diversity.SIGNAL, now or NOW)
        return publisher.Result(True, "", 1, 900 + listing_id, 0)

    def reel(self, item, render, *, feed_visible=True, now=None):
        listing_id = int(item.listing["id"])
        self.reels.append(listing_id)
        self._record(item, diversity.REEL, now or NOW)
        return publisher.Result(True, "", 2, 7000 + listing_id, 55)


@pytest.fixture()
def published(monkeypatch):
    """Wire the tick to fixtures and hand back the publication recorder.

    ``eligibility.rejection_reason`` is replaced with the one rule these tests
    actually care about — PulseDrop must never republish its own output — rather
    than disabled outright, so the self-reference guard is still live here.
    """
    monkeypatch.setenv("PULSEDROP_ENABLED", "1")
    config.invalidate_cache()

    monkeypatch.setattr(
        eligibility, "fetch_candidates", lambda cur, limit, now=None: [dict(c) for c in CANDIDATES]
    )
    monkeypatch.setattr(
        eligibility,
        "rejection_reason",
        lambda listing, *, system_user_ids=(): (
            "seller_is_system"
            if int(listing.get("seller_user_id") or 0) in set(system_user_ids)
            else ""
        ),
    )
    monkeypatch.setattr(eligibility, "image_urls", lambda listing: list(listing.get("image_urls") or []))
    monkeypatch.setattr(eligibility, "video_source", lambda listing: str(listing.get("video_url") or ""))
    monkeypatch.setattr(account, "ensure_account", lambda: PULSEDROP_USER_ID)
    # ffmpeg is a deploy dependency and absent on a developer's laptop, which
    # would turn every composed-Reel case into SKIP for the wrong reason.
    monkeypatch.setattr(distribution, "_render_capable", lambda: True)
    monkeypatch.setattr(publisher, "reap_stale_claims", lambda *, now=None: 0)

    recorder = _Publishes()
    monkeypatch.setattr(publisher, "publish_signal", recorder.signal)
    monkeypatch.setattr(publisher, "publish_reel", recorder.reel)
    return recorder


def _decide_always(outcome, reason, source=""):
    return lambda item, history: distribution.Decision(outcome, source, reason)


class TestANormalTick:
    def test_publishes_exactly_one_product(self, published):
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.PUBLISHED
        # One. Not one per eligible candidate, and not one per enabled surface:
        # a tick that published the whole page would empty the catalogue into
        # the feed in a single pass.
        assert len(published.signals) == 1
        assert result["selected_listing_id"] == published.signals[0]
        assert result["post_id"] == 900 + published.signals[0]

    def test_counts_what_it_looked_at_not_just_what_it_chose(self, published):
        result = curator.tick(now=NOW)
        assert result["evaluated"] == len(CANDIDATES)
        assert result["eligible"] == len(CANDIDATES)

    def test_chooses_the_top_of_the_ranking(self, published):
        # Equal scores, so the tie-break is id descending. Pinning this is not
        # about 103 specifically — it is that the walk starts at the top of the
        # ranking rather than at whatever order the catalogue read returned.
        curator.tick(now=NOW)
        assert published.signals == [103]

    def test_stills_alone_publish_a_signal_rather_than_a_composed_reel(self, published):
        # ``_prefers_reel`` is false without the seller's own footage. A Reel
        # composed from photographs is a thing PulseDrop can do, not a thing it
        # does by default — the Signal shows the seller's actual photographs at
        # the size they were taken, with no motion invented on top.
        result = curator.tick(now=NOW)
        assert result["decision"] == distribution.SIGNAL_ONLY
        assert published.reels == []


class TestTheTicksThatWriteNothing:
    def test_a_second_tick_inside_the_interval_is_not_due(self, published):
        assert curator.tick(now=NOW)["outcome"] == curator.PUBLISHED
        second = curator.tick(now=NOW)
        assert second["outcome"] == curator.NOT_DUE
        # And it published nothing a second time, which is the property the
        # lease exists for — the worker loop calls this every few seconds.
        assert len(published.signals) == 1

    def test_not_due_writes_no_run_row(self, published):
        curator.tick(now=NOW)
        curator.tick(now=NOW)
        # One row for the publication, none for the no-op. A row per loop
        # iteration would be ~100k rows a day describing a system at rest, and
        # would bury the handful that describe it doing something.
        assert len(curator.recent_runs(50)) == 1

    def test_the_kill_switch_stops_the_tick_before_the_lease(self, published, monkeypatch):
        monkeypatch.setenv("PULSEDROP_ENABLED", "0")
        config.invalidate_cache()
        assert curator.tick(now=NOW)["outcome"] == curator.DISABLED
        assert published.signals == []
        assert curator.recent_runs(50) == []

        # Switching back on must act immediately. If the disabled path had taken
        # the lease it would also have pushed ``next_run_at`` two hours out, and
        # an operator turning PulseDrop back on would watch it do nothing until
        # an interval they cannot see had elapsed.
        monkeypatch.setenv("PULSEDROP_ENABLED", "1")
        config.invalidate_cache()
        assert curator.tick(now=NOW)["outcome"] == curator.PUBLISHED

    def test_an_empty_catalogue_is_reported_as_no_candidates(self, published, monkeypatch):
        monkeypatch.setattr(eligibility, "fetch_candidates", lambda cur, limit, now=None: [])
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.NO_CANDIDATES
        assert result["evaluated"] == 0

    def test_a_catalogue_the_gate_rejects_is_reported_differently(self, published, monkeypatch):
        # "Nothing in the shop" and "the shop is full of things nobody may see"
        # are different operational problems with different fixes, so they are
        # different outcome codes even though both publish nothing.
        monkeypatch.setattr(
            eligibility, "rejection_reason", lambda listing, *, system_user_ids=(): "not_approved"
        )
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.NONE_ELIGIBLE
        assert result["evaluated"] == len(CANDIDATES)
        assert result["eligible"] == 0
        assert "gate:not_approved" in result["rejected_json"]


class TestHowFarTheWalkGoes:
    """The difference between one comparison and two hundred."""

    @staticmethod
    def _counting(monkeypatch, decide):
        calls: list[int] = []

        def counted(item, history):
            calls.append(int(item.listing["id"]))
            return decide(item, history)

        monkeypatch.setattr(distribution, "decide", counted)
        return calls

    def test_an_account_level_deferral_stops_after_one_comparison(self, published, monkeypatch):
        # ``min_publish_interval`` is a property of PulseDrop, not of a product.
        # When it is true no candidate can publish, so the other 199 comparisons
        # are work whose answer cannot change.
        calls = self._counting(
            monkeypatch, _decide_always(distribution.DEFER, "min_publish_interval")
        )
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.DEFERRED
        assert result["reason"] == "min_publish_interval"
        assert len(calls) == 1

    def test_a_per_product_cooldown_advances_to_the_next_candidate(self, published, monkeypatch):
        # Refuses 103 and 102 — the two the ranking reaches *first*. Refusing
        # only candidates the walk never gets to would pass without testing
        # anything, which is the trap this parametrisation avoids.
        def decide(item, history):
            if int(item.listing["id"]) == 101:
                return distribution.Decision(distribution.SIGNAL_ONLY, "", "signal_is_the_right_format")
            return distribution.Decision(distribution.DEFER, "", "product_cooldown")

        calls = self._counting(monkeypatch, decide)
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.PUBLISHED
        assert result["selected_listing_id"] == 101
        assert calls == [103, 102, 101]

    def test_the_walk_stops_at_the_first_yes(self, published, monkeypatch):
        calls = self._counting(
            monkeypatch, _decide_always(distribution.SIGNAL_ONLY, "signal_is_the_right_format")
        )
        curator.tick(now=NOW)
        assert calls == [103]


class TestWhyNothingWasPublished:
    def test_structural_refusals_report_skipped_rather_than_deferred(self, published, monkeypatch):
        # A DEFER will lift on its own; a SKIP will keep being true until a
        # listing or the deployment changes. An operator reading "deferred" goes
        # away and waits, which is the wrong response to the second one.
        monkeypatch.setattr(
            distribution, "decide", _decide_always(distribution.SKIP, "no_publishable_format")
        )
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.SKIPPED
        assert result["reason"] == "no_publishable_format"
        assert '"decide:no_publishable_format": 3' in result["rejected_json"]

    def test_a_mixed_run_of_refusals_is_not_called_structural(self, published, monkeypatch):
        # One temporary reason in the histogram is enough: the run as a whole
        # will produce a different answer on the next tick.
        def decide(item, history):
            if int(item.listing["id"]) == 103:
                return distribution.Decision(distribution.DEFER, "", "seller_cooldown")
            return distribution.Decision(distribution.SKIP, "", "no_publishable_format")

        monkeypatch.setattr(distribution, "decide", decide)
        assert curator.tick(now=NOW)["outcome"] == curator.DEFERRED

    def test_the_gate_and_the_decision_histograms_are_not_summed(self, published, monkeypatch):
        # They count different populations — listings that were never eligible,
        # and eligible listings no format wanted. Adding them would produce a
        # total that means nothing, so they are merged under distinct prefixes.
        monkeypatch.setattr(
            eligibility,
            "rejection_reason",
            lambda listing, *, system_user_ids=(): (
                "not_approved" if int(listing.get("id")) == 103 else ""
            ),
        )
        monkeypatch.setattr(
            distribution, "decide", _decide_always(distribution.SKIP, "no_publishable_format")
        )
        histogram = curator.tick(now=NOW)["rejected_json"]
        assert '"gate:not_approved": 1' in histogram
        assert '"decide:no_publishable_format": 2' in histogram


class TestAReelThatHasToBeRendered:
    @staticmethod
    def _wants_a_reel(monkeypatch):
        monkeypatch.setattr(
            distribution,
            "decide",
            _decide_always(
                distribution.REEL_ONLY, "seller_video_leads", distribution.SOURCE_COMPOSED_IMAGES
            ),
        )

    def test_a_pending_render_defers_and_is_counted_as_started(self, published, monkeypatch):
        self._wants_a_reel(monkeypatch)
        monkeypatch.setattr(
            reel_composer,
            "find_or_enqueue",
            lambda listing, kind, now=None: {
                "id": 1, "state": reel_composer.PENDING, "attempts": 0, "video_url": ""
            },
        )
        result = curator.tick(now=NOW)
        # Not a failure. An encode is minutes and the lease is five, so the tick
        # enqueues and returns rather than rendering inline and outliving the
        # lease that is keeping a second instance off the same work.
        assert result["outcome"] == curator.RENDER_PENDING
        assert result["reason"] == f"render_{reel_composer.PENDING}"
        assert result["renders_started"] == 1
        assert published.reels == []

    def test_waiting_on_an_encode_shortens_the_next_run(self, published, monkeypatch):
        self._wants_a_reel(monkeypatch)
        monkeypatch.setattr(
            reel_composer,
            "find_or_enqueue",
            lambda listing, kind, now=None: {
                "id": 1, "state": reel_composer.PENDING, "attempts": 0, "video_url": ""
            },
        )
        result = curator.tick(now=NOW)
        # The render finishes in a minute or two; sitting on a ready Reel for
        # two hours is latency with no purpose. Coming back early is safe
        # because it is not a licence to publish — the interval and cap rules
        # are measured against the publication history, not the tick counter.
        assert curator._next_run_seconds(result) < config.evaluation_interval_seconds()
        assert curator._next_run_seconds(result) >= 60

    def test_a_ready_render_publishes_the_reel(self, published, monkeypatch):
        self._wants_a_reel(monkeypatch)
        monkeypatch.setattr(
            reel_composer,
            "find_or_enqueue",
            lambda listing, kind, now=None: {
                "id": 1,
                "state": reel_composer.READY,
                "attempts": 1,
                "video_url": "https://cdn.test/r.mp4",
                "poster_url": "https://cdn.test/r.jpg",
            },
        )
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.PUBLISHED
        assert result["decision"] == distribution.REEL_ONLY
        assert result["reel_post_id"] == 7000 + result["selected_listing_id"]
        # A Reel-only decision publishes a Reel and nothing else. "Never
        # mechanically both" is the brief's words and this is where it shows.
        assert result["post_id"] == 0
        assert published.signals == []

    def test_a_render_that_is_already_queued_is_not_counted_as_started_again(
        self, published, monkeypatch
    ):
        self._wants_a_reel(monkeypatch)
        monkeypatch.setattr(
            reel_composer,
            "find_or_enqueue",
            lambda listing, kind, now=None: {
                "id": 1, "state": reel_composer.PENDING, "attempts": 2, "video_url": ""
            },
        )
        # Otherwise the "renders started" counter would climb by one on every
        # tick that waited, and would measure ticks rather than encodes.
        assert curator.tick(now=NOW)["renders_started"] == 0


class TestWhenPublishingItselfFails:
    def test_a_refused_publication_is_recorded_as_failed(self, published, monkeypatch):
        monkeypatch.setattr(
            publisher,
            "publish_signal",
            lambda item, *, now=None: publisher.Result(False, "listing_vanished", 3, 0, 0),
        )
        result = curator.tick(now=NOW)
        assert result["outcome"] == curator.FAILED
        assert result["reason"] == "signal_listing_vanished"
        assert result["post_id"] == 0

    def test_a_collaborator_that_raises_does_not_propagate(self, published, monkeypatch):
        # ``tick`` is called from a worker loop that has no error handling of
        # its own, and a raise here stops PulseDrop until the next deploy. The
        # lease still has to be released, which is the part that would actually
        # wedge the subsystem.
        healthy = distribution.decide

        def explode(item, history):
            raise RuntimeError("ranker exploded")

        monkeypatch.setattr(distribution, "decide", explode)
        assert curator.tick(now=NOW)["outcome"] == curator.ERROR

        # Restored before the second tick, because the claim under test is that
        # the *lease* survived a crash, not that a permanently broken ranker
        # eventually recovers. Leaving the fault installed would assert nothing:
        # the second tick would report ``error`` whether the lease was released
        # or wedged, and the test would pass for the wrong reason if inverted.
        monkeypatch.setattr(distribution, "decide", healthy)
        assert curator.tick(now=NOW + timedelta(hours=3))["outcome"] == curator.PUBLISHED


class TestTheRunLog:
    def test_records_one_row_per_tick_that_did_something(self, published):
        for hours in (0, 3, 6):
            curator.tick(now=NOW + timedelta(hours=hours))
        rows = curator.recent_runs(20)
        assert len(rows) == 3
        assert [row["outcome"] for row in rows] == [curator.PUBLISHED] * 3
        # Newest first, because the admin surface reads the top of it.
        assert [row["selected_listing_id"] for row in rows] == [101, 102, 103]

    def test_carries_the_evidence_an_operator_would_ask_for(self, published):
        curator.tick(now=NOW)
        row = curator.recent_runs(1)[0]
        for column in (
            "run_id", "outcome", "reason", "evaluated", "eligible", "rejected_json",
            "selected_listing_id", "decision", "post_id", "reel_post_id",
            "renders_started", "duration_ms", "started_at", "finished_at",
        ):
            assert column in row, f"the run row cannot answer for {column}"
        assert row["duration_ms"] >= 0

    def test_the_row_and_the_return_value_are_the_same_account_of_the_tick(self, published):
        # Built from one dict on purpose. A caller that logs something the table
        # does not contain produces two accounts of one tick, and the one an
        # operator reads a week later is the table.
        returned = curator.tick(now=NOW)
        stored = curator.recent_runs(1)[0]
        for key, value in returned.items():
            assert stored[key] == value, f"{key} differs between the return value and the row"
