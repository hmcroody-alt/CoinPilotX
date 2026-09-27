"""``metrics`` used to have no caller outside a test suite.

That is a specific and unusually costly kind of dead code. The module computes
the only numbers that can see this pipeline's defining failure — click-through
rate cannot, because CTR scores a placement and repetition is a property of the
sequence — and it computed them exclusively for assertions. Production enforced
six repetition controls and reported on none of them.

The cost was not hypothetical. A diversity term sat at a constant ``1.0`` for
every candidate on every surface for the whole life of the module, applied after
the score it was meant to influence had already been frozen. Tests asserted it
was *computed*. Nothing asserted it reached the output, and no number in
production could have told anyone apart. Measurement that only runs under test
measures the test.

So the cases here are mostly not about arithmetic. The arithmetic is checked by
``test_distribution_pipeline.py``, which drives real scrolls. These check
**wiring**: that serving a surface emits the observation, that it still does so
when the surface returns nothing, that a fault in the metric cannot take the
surface down with it, and that the cheap derivation the serve path uses agrees
with the authoritative read from the log. The last class is the one that would
fail if somebody deleted the call site — which is the state this file exists to
make impossible to return to.
"""

from __future__ import annotations

import logging

import pytest

from services.commerce_discovery import config, engine, exposure, metrics, preferences

_LOGGER_NAME = "services.commerce_discovery.metrics"
_LINE = "COMMERCE_DISCOVERY_REPETITION"


def _lines(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage().startswith(_LINE)]


def _state_for(market) -> exposure.ExposureState:
    cur = market.conn.cursor()
    policy = preferences.viewer_policy(cur, market.viewer_id)
    return exposure.load(cur, policy.subject_ref, market.viewer_id)


class TestTheServePathEmitsTheObservation:
    """The wiring. Every case here fails if the call site is removed."""

    def test_serving_a_surface_logs_a_repetition_line(self, market, caplog):
        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            market.browse("feed", steps=3, seconds=30.0)

        emitted = _lines(caplog)
        assert len(emitted) == 3, "one observation per served request, not per placement"
        assert all("surface=feed" in record.getMessage() for record in emitted)

    def test_the_observation_carries_the_numbers_and_not_a_verdict(self, market, caplog):
        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            market.browse("feed", steps=6, seconds=30.0)

        message = _lines(caplog)[-1].getMessage()
        # An operator reading one line must be able to tell *which* of the four
        # repetition failures is happening. A composite score would not let them.
        for key in (
            "repeat_product_rate",
            "seller_concentration",
            "category_concentration",
            "segment_concentration",
            "cross_surface_repeat_rate",
            "immediate_product_repeats",
        ):
            assert key in message

    def test_a_healthy_window_logs_at_debug_and_not_at_warning(self, market, caplog):
        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            market.browse("feed", steps=8, seconds=30.0)

        emitted = _lines(caplog)
        assert emitted, "the healthy line has to exist"
        # The baseline matters as much as the alert: the first question about
        # "category concentration 0.81" is always what it was yesterday.
        assert all(record.levelno == logging.DEBUG for record in emitted)
        assert all("alerts=none" in record.getMessage() for record in emitted)

    def test_a_surface_that_serves_nothing_is_still_measured(self, market, caplog, monkeypatch):
        """A stuck feed and an empty one look identical from outside."""
        market.browse("feed", steps=4, seconds=30.0)
        # Everything in the pool now scores below an unreachable floor, so the
        # engine returns [] from the branch *after* the exposure read.
        monkeypatch.setenv("COMMERCE_DISCOVERY_MIN_SCORE", "0.99")

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            served = market.serve("feed")

        assert served == [], "the premise: this request placed nothing"
        assert len(_lines(caplog)) == 1, "the numbers are how you tell stuck from empty"

    def test_a_broken_metric_costs_a_log_line_and_not_the_surface(self, market, monkeypatch, caplog):
        def explode(_state):
            raise RuntimeError("from_state is broken")

        monkeypatch.setattr(metrics, "from_state", explode)

        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.engine"):
            served = market.serve("feed")

        # `serve` has a blanket fail-safe that turns any exception into an empty
        # surface. Instrumentation must be caught *before* it reaches that, or
        # adding a metric becomes a way to withdraw the feature it measures.
        assert served, "a broken metric must not empty the feed"
        assert any("REPETITION_OBSERVE_FAILED" in record.getMessage() for record in caplog.records)

    def test_the_call_site_exists_and_runs_once_per_request(self, market, monkeypatch):
        calls: list[dict] = []

        # `**_rest` rather than a fixed parameter list. A stub that restates the
        # signature turns every future argument to the real function into a
        # TypeError here, and `_observe_repetition` catches locally by design — so
        # the failure presents as a *missing log line*, not as a broken stub. It
        # did exactly that when `product_cap` was added to `observe`.
        def recorder(report, *, surface, subject_ref="", **_rest):
            calls.append({"surface": surface, "ref": subject_ref, "impressions": report.impressions})
            return ()

        monkeypatch.setattr(metrics, "observe", recorder)
        market.browse("feed", steps=2, seconds=30.0)

        assert len(calls) == 2
        assert [call["surface"] for call in calls] == ["feed", "feed"]
        assert calls[0]["ref"], "the observation is attributed to a subject, not anonymous"
        # Second request sees the first request's impressions. Without this the
        # measurement would be of an empty window forever.
        assert calls[1]["impressions"] > calls[0]["impressions"]


class TestTheFreeDerivationAgreesWithTheLog:
    """``from_state`` costs no query. That is only worth having if it is right."""

    def test_it_matches_the_authoritative_read_over_the_same_window(self, market):
        market.browse("feed", steps=20, seconds=30.0)

        cur = market.conn.cursor()
        policy = preferences.viewer_policy(cur, market.viewer_id)
        cheap = metrics.from_state(exposure.load(cur, policy.subject_ref, market.viewer_id))
        authoritative = metrics.from_events(cur, subject_ref=policy.subject_ref)

        assert cheap.impressions == authoritative.impressions
        assert cheap.unique_products_shown == authoritative.unique_products_shown
        assert cheap.unique_sellers_shown == authoritative.unique_sellers_shown
        assert cheap.top_seller_count == authoritative.top_seller_count
        assert cheap.repeat_product_rate == pytest.approx(authoritative.repeat_product_rate)
        assert cheap.seller_concentration == pytest.approx(authoritative.seller_concentration)
        assert cheap.category_concentration == pytest.approx(authoritative.category_concentration)
        assert cheap.segment_concentration == pytest.approx(authoritative.segment_concentration)

    def test_it_reports_what_it_cannot_know_as_unmeasured_rather_than_as_zero_traffic(self, market):
        market.browse("feed", steps=5, seconds=30.0)
        report = metrics.from_state(_state_for(market))

        assert report.impressions > 0, "the window is not empty"
        # `by_surface` would have to be distinct product-surface pairs, which is
        # not impressions. A wrong number is worse than a missing one: the
        # missing one reads as unmeasured, and this one sits beside a non-zero
        # impression count that says the sequence existed.
        assert report.by_surface == {}
        assert report.new_product_exposure_rate == 0.0

    def test_an_empty_window_is_the_shared_empty_report(self):
        assert metrics.from_state(exposure.EMPTY) is metrics.EMPTY

    def test_a_state_missing_the_attributes_entirely_does_not_raise(self):
        class NotAState:
            pass

        assert metrics.from_state(NotAState()) is metrics.EMPTY


class TestAlertsFireOnTheRightShapes:
    def test_a_degraded_read_is_silent(self):
        # A failed exposure read resolves soft to a partial state, so its numbers
        # describe how much of the window was readable. Alerting on them makes
        # database weather look like a repetition incident.
        loud = exposure.ExposureState(
            product_counts={1: 40}, seller_counts={7: 40},
            category_counts={"home": 40}, recent_products=(1, 1, 1),
        )
        assert metrics.alerts(metrics.from_state(loud)), "the premise: this shape does alert"

        degraded = exposure.ExposureState(
            product_counts={1: 40}, seller_counts={7: 40},
            category_counts={"home": 40}, recent_products=(1, 1, 1),
            degraded=True,
        )
        assert metrics.alerts(metrics.from_state(degraded)) == ()

    def test_a_small_window_suppresses_the_rates(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MIN_SAMPLE", "12")
        # Three impressions, one seller, one category: every rate is pinned at
        # 1.0 and none of them is a finding.
        state = exposure.ExposureState(
            product_counts={1: 1, 2: 1, 3: 1}, seller_counts={7: 3},
            category_counts={"home": 3},
        )
        report = metrics.from_state(state)
        assert report.seller_concentration == 1.0, "the premise"
        assert metrics.alerts(report) == ()

    def test_the_immediate_repeat_invariant_ignores_the_sample_gate(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MIN_SAMPLE", "1000")
        state = exposure.ExposureState(
            product_counts={1: 2}, seller_counts={7: 2}, recent_products=(1, 1),
        )
        codes = [alert.code for alert in metrics.alerts(metrics.from_state(state))]
        # The same product twice running is wrong at any sample size. It is the
        # one measure in the module with a correct value, and the value is zero.
        assert codes == [metrics.IMMEDIATE_PRODUCT_REPEAT]

    def test_the_cap_breach_alert_is_off_unless_a_cap_is_supplied(self):
        # `alerts` is called directly by tests and by anything reading the report
        # outside a serve, neither of which knows a surface. Defaulting to "check
        # nothing" is right there; defaulting to `config.product_cap()` would make
        # a caller who never mentioned a surface alert against the feed's number.
        state = exposure.ExposureState(product_counts={1: 40}, seller_counts={7: 40})
        report = metrics.from_state(state)
        assert metrics.PRODUCT_CAP_EXCEEDED not in {a.code for a in metrics.alerts(report)}
        breached = metrics.alerts(report, product_cap=3)
        assert metrics.PRODUCT_CAP_EXCEEDED in {a.code for a in breached}

    def test_the_cap_breach_ignores_the_sample_gate(self, monkeypatch):
        # A cap that has been exceeded is wrong at any sample size, so it sits
        # above the gate with the immediate-repeat invariant. The smallest breach
        # is also the most obvious one — a viewer shown one product four times and
        # nothing else — and it is precisely the case a sample gate would hide.
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MIN_SAMPLE", "1000")
        state = exposure.ExposureState(product_counts={1: 4}, seller_counts={7: 4})
        codes = {a.code for a in metrics.alerts(metrics.from_state(state), product_cap=3)}
        assert metrics.PRODUCT_CAP_EXCEEDED in codes

    def test_a_window_exactly_at_the_cap_is_not_a_breach(self):
        # The enforcement is `>= cap` is a drop, so a count *equal* to the cap is
        # the expected steady state of a healthy exhausted viewer. Alerting on it
        # would fire on every viewer who ever finished a small catalogue.
        state = exposure.ExposureState(product_counts={1: 3}, seller_counts={7: 3})
        codes = {a.code for a in metrics.alerts(metrics.from_state(state), product_cap=3)}
        assert metrics.PRODUCT_CAP_EXCEEDED not in codes

    def test_a_degraded_read_does_not_report_a_cap_breach(self):
        # A partial exposure read undercounts, so it cannot show a breach anyway —
        # but if it ever did, the cause would be the failed read and not the
        # pipeline. Same rule as every other alert here.
        state = exposure.ExposureState(product_counts={1: 40}, seller_counts={7: 40}, degraded=True)
        assert metrics.alerts(metrics.from_state(state), product_cap=3) == ()

    def test_the_serve_path_supplies_the_surface_cap(self, market, monkeypatch):
        # The wiring. Without this the alert is unreachable in production, which
        # is the state `metrics` as a whole was in before it had a caller at all.
        seen: list[dict] = []
        real = metrics.observe
        monkeypatch.setattr(
            metrics, "observe",
            lambda report, **kwargs: (seen.append(kwargs), real(report, **kwargs))[1],
        )
        market.serve("reels")
        market.serve("marketplace")
        caps = [call.get("product_cap") for call in seen]
        assert all(cap and cap >= 1 for cap in caps), caps
        assert caps[0] != caps[1], (
            "every surface was observed against the same cap; the per-surface "
            f"allowance is not reaching the observer ({caps})"
        )

    def test_a_dominated_window_names_every_measure_that_crossed(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MIN_SAMPLE", "4")
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MAX_CONCENTRATION", "0.5")
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MAX_REPEAT_RATE", "0.5")
        state = exposure.ExposureState(
            product_counts={1: 9, 2: 1}, seller_counts={7: 10},
            category_counts={"women > clothing": 10},
        )
        codes = {alert.code for alert in metrics.alerts(metrics.from_state(state))}
        # Four separate faults, named separately, because each has a different
        # fix. A single composite "diversity score" would move without saying why.
        assert codes == {
            metrics.REPEAT_PRODUCT_RATE,
            metrics.SELLER_CONCENTRATION,
            metrics.CATEGORY_CONCENTRATION,
            metrics.SEGMENT_CONCENTRATION,
        }

    def test_an_alert_carries_the_threshold_it_crossed(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MIN_SAMPLE", "4")
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MAX_CONCENTRATION", "0.6")
        state = exposure.ExposureState(
            product_counts={n: 1 for n in range(1, 11)}, seller_counts={7: 10},
            category_counts={"home": 10},
        )
        alert = next(
            item for item in metrics.alerts(metrics.from_state(state))
            if item.code == metrics.SELLER_CONCENTRATION
        )
        # Without the threshold in the line, an operator reading "0.81" cannot
        # tell whether it breached a bound or merely got logged.
        assert alert.threshold == pytest.approx(0.6)
        assert alert.observed == pytest.approx(1.0)
        assert "0.6" in str(alert)

    def test_the_kill_switch_stops_the_line_without_stopping_the_maths(self, monkeypatch, caplog):
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_ALERTS", "false")
        state = exposure.ExposureState(
            product_counts={1: 40}, seller_counts={7: 40}, recent_products=(1, 1),
        )
        report = metrics.from_state(state)

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            assert metrics.observe(report, surface="feed") == ()
        assert _lines(caplog) == []
        # The switch is about the log volume, not about the measurement — which
        # is free, so there would be nothing to save by disabling it.
        assert metrics.alerts(report), "alerts() is unaffected by the emit switch"

    def test_a_malformed_threshold_falls_back_instead_of_raising(self, monkeypatch):
        monkeypatch.setenv("COMMERCE_DISCOVERY_REPETITION_MAX_CONCENTRATION", "three quarters")
        # A typo'd tuning knob on a fail-safe subsystem must not be able to raise
        # on the serve path.
        assert config.repetition_max_concentration() == pytest.approx(0.75)


class TestFoldingMakesConcentrationHonest:
    def test_two_spellings_of_one_aisle_are_one_bucket(self):
        events = [
            {"listing_id": 1, "category": "Jewelry & Watches > Rings"},
            {"listing_id": 2, "category": "jewelry watches / rings"},
            {"listing_id": 3, "category": "Jewelry & Watches > Rings"},
            {"listing_id": 4, "category": "home"},
        ]
        report = metrics.summarize(events)
        # Lower-casing alone gives 3 categories and a concentration of 0.5, which
        # reads as a varied window. It is three quarters one shelf.
        assert report.unique_categories_shown == 2
        assert report.category_concentration == pytest.approx(0.75)
        assert report.top_category_count == 3

    def test_an_uncategorised_row_does_not_invent_a_bucket(self):
        events = [
            {"listing_id": 1, "category": None},
            {"listing_id": 2, "category": ""},
            {"listing_id": 3, "category": "home"},
        ]
        report = metrics.summarize(events)
        # Three impressions, one category. An empty-string bucket holding two of
        # them would report 0.67 concentration in "", which is not a place.
        assert report.impressions == 3
        assert report.unique_categories_shown == 1
        assert report.top_category_count == 1

    def test_leaves_and_aisles_are_reported_separately(self):
        events = [
            {"listing_id": 1, "category": "women > dresses"},
            {"listing_id": 2, "category": "women > shoes"},
            {"listing_id": 3, "category": "women > bags"},
            {"listing_id": 4, "category": "home > lamps"},
        ]
        report = metrics.summarize(events)
        # Four leaves, low leaf concentration — and three quarters of the window
        # is one aisle. The second number is the one that matches what the viewer
        # actually sees, which is why both are reported.
        assert report.unique_categories_shown == 4
        assert report.category_concentration == pytest.approx(0.25)
        assert report.unique_segments_shown == 2
        assert report.segment_concentration == pytest.approx(0.75)
