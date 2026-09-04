"""The five acceptance cases, and the properties that keep them honest.

`services.market_intelligence` is deliberately pure — no network, no cache, no
database — which means it can be tested by handing it a price series and reading
the verdict back. That is the point of the constraint, so this file exercises it
directly rather than through the route.

The shapes here are synthetic on purpose. A test that fetched a real coin would
pass or fail depending on what Bitcoin did this morning, and a market-analysis
test that is green on Tuesday and red on Wednesday teaches nobody anything. Each
series below is constructed to *be* the situation under test: the overextended
one really is vertical, the pullback one really does come back to support.
"""

from __future__ import annotations

import math

import pytest

from services import market_intelligence as mi


# ---------------------------------------------------------------------------
# Series builders
#
# Every builder returns hours, oldest first — the same orientation
# `market_data.hourly_series` hands over, because a test that fed the series
# backwards would exercise a code path production never reaches.
# ---------------------------------------------------------------------------

def _rising(points: int = 168, start: float = 100.0, per_hour: float = 0.25) -> list[float]:
    """A clean, steady uptrend. Aligned on every timeframe, not yet extended."""
    return [start * (1.0 + per_hour / 100.0) ** i for i in range(points)]


def _overextended(points: int = 168) -> list[float]:
    """A long grind, then a near-vertical final day.

    This is acceptance case 1. The trend is real — so opportunity should be high
    — and the last stretch has gone parabolic, so entry should not be.
    """
    base = _rising(points - 24, 100.0, 0.18)
    last = base[-1]
    return base + [last * (1.0 + 0.9 / 100.0) ** i for i in range(1, 25)]


def _pullback_to_support(points: int = 168) -> list[float]:
    """A rally, a retrace onto the level it broke from, then a turn up.

    Acceptance case 2. The structure is intact, price is near support rather
    than far above it, and the last few hours are rising again — which is the
    confirmation the entry score is supposed to reward.
    """
    series: list[float] = []
    price = 100.0
    for _ in range(90):                       # the advance
        price *= 1.0035
        series.append(price)
    peak = price
    for _ in range(50):                       # the retrace
        price *= 0.9965
        series.append(price)
    for _ in range(points - len(series)):     # the turn
        price *= 1.0015
        series.append(price)
    assert price < peak
    return series


def _asset(symbol: str = "TEST", price: float | None = None, change24h: float = 4.0,
           **extra) -> dict:
    row = {
        "symbol": symbol,
        "name": symbol.title(),
        "price": price,
        "change24h": change24h,
        "marketCap": 4_000_000_000,
        "volume24h": 900_000_000
    }
    row.update(extra)
    return row


def _context(median: float = 1.0, btc: float = 1.5) -> dict:
    return {"medianChangePct": median, "btcChange24hPct": btc, "regime": None, "rotation": None}


def _volumes(points: int = 60, level: float = 800_000_000.0) -> list[float]:
    return [level] * points


# ---------------------------------------------------------------------------
# Acceptance case 1 — bullish but overextended
# ---------------------------------------------------------------------------

def test_a_strong_trend_that_has_already_run_scores_high_opportunity_and_low_entry():
    series = _overextended()
    verdict = mi.assess(
        _asset(price=series[-1], change24h=18.0),
        series,
        _context(),
        _volumes(),
        None,
        "full"
    )

    opportunity = verdict["opportunity"]["score"]
    entry = verdict["entry"]["score"]
    assert opportunity is not None and entry is not None
    assert opportunity > entry, (
        "an asset in a clean uptrend that has just gone vertical is a good asset "
        f"at a bad moment; got opportunity={opportunity} entry={entry}"
    )
    assert entry < 50.0
    # The whole point of keeping the two scores apart is that this case gets a
    # verdict of "not now" rather than a mediocre average of "quite good".
    assert verdict["action"]["state"] in {
        mi.WAIT, mi.WAIT_FOR_PULLBACK, mi.DO_NOT_CHASE, mi.HIGH_RISK, mi.PULLBACK_WATCH
    }


def test_the_overextended_verdict_says_why_rather_than_just_what():
    series = _overextended()
    verdict = mi.assess(_asset(price=series[-1], change24h=18.0), series, _context(),
                        _volumes(), None, "full")
    reasons = verdict["why"]["action"] + verdict["why"]["entry"]
    assert reasons, "a verdict with no stated reason is the black box this layer exists to avoid"
    for reason in reasons:
        assert reason["text"].strip()
        assert reason["confidence"] in {mi.KNOWN, mi.INFERRED, mi.UNAVAILABLE}


# ---------------------------------------------------------------------------
# Acceptance case 2 — pullback into support with confirmation
# ---------------------------------------------------------------------------

def test_a_pullback_to_support_scores_better_entry_than_the_same_asset_extended():
    extended = _overextended()
    pullback = _pullback_to_support()

    extended_entry = mi.assess(_asset(price=extended[-1], change24h=18.0), extended,
                               _context(), _volumes(), None, "full")["entry"]["score"]
    pullback_entry = mi.assess(_asset(price=pullback[-1], change24h=-1.5), pullback,
                               _context(), _volumes(), None, "full")["entry"]["score"]

    assert extended_entry is not None and pullback_entry is not None
    assert pullback_entry > extended_entry, (
        "entry quality is a claim about the moment, so buying into a retrace must "
        f"read better than buying into a spike; got {pullback_entry} vs {extended_entry}"
    )


def test_a_pullback_setup_states_a_trigger_an_invalidation_and_a_reward_to_risk():
    series = _pullback_to_support()
    verdict = mi.assess(_asset(price=series[-1], change24h=-1.5), series, _context(),
                        _volumes(), None, "full")
    setup = verdict["setup"]
    if not setup.get("type"):
        pytest.skip("no setup was identified for this series; nothing to assert about one")

    # A setup that names an entry but not an invalidation is a suggestion with no
    # way to be wrong, which is the shape of advice this product must not give.
    assert setup.get("invalidation") is not None
    assert setup.get("trigger")
    rr = setup.get("rewardToRisk")
    if rr is not None:
        assert rr > 0.0
        assert rr < 100.0, "a reward:risk in the hundreds means the risk leg collapsed to noise"


# ---------------------------------------------------------------------------
# Acceptance cases 3 and 4 — the same asset, two readers
# ---------------------------------------------------------------------------

def test_a_holder_sitting_on_a_large_gain_is_told_about_the_position_not_the_entry():
    series = _overextended()
    holding = {
        "known": True,
        "holding": True,
        "quantity": 10.0,
        "unrealizedPnlPct": 82.0,
        "portfolioSharePct": 22.0,
        "note": None
    }
    verdict = mi.assess(_asset(price=series[-1], change24h=18.0), series, _context(),
                        _volumes(), holding, "full")

    assert verdict["action"]["perspective"] == "holder"
    assert verdict["action"]["state"] in {
        mi.HOLD, mi.TAKE_PARTIAL_PROFIT, mi.REDUCE, mi.EXIT, mi.HIGH_RISK
    }


def test_the_same_asset_reads_differently_for_someone_who_does_not_hold_it():
    series = _overextended()
    asset = _asset(price=series[-1], change24h=18.0)
    holder = mi.assess(asset, series, _context(), _volumes(),
                       {"known": True, "holding": True, "quantity": 10.0,
                        "unrealizedPnlPct": 82.0, "portfolioSharePct": 22.0, "note": None},
                       "full")
    stranger = mi.assess(asset, series, _context(), _volumes(),
                         {"known": True, "holding": False, "quantity": 0.0,
                          "unrealizedPnlPct": None, "portfolioSharePct": 0.0, "note": None},
                         "full")

    assert holder["action"]["perspective"] == "holder"
    assert stranger["action"]["perspective"] == "non_holder"
    assert stranger["action"]["state"] in {
        mi.WAIT, mi.WAIT_FOR_PULLBACK, mi.WAIT_FOR_CONFIRMATION, mi.DO_NOT_CHASE,
        mi.ACCUMULATE, mi.BREAKOUT_WATCH, mi.PULLBACK_WATCH, mi.AVOID, mi.HIGH_RISK
    }
    # "Sell what you hold" and "do not buy what you do not hold" are different
    # sentences, and a screen that gave the second reader the first one would be
    # telling them to sell something they never bought.
    assert stranger["action"]["state"] not in {mi.TAKE_PARTIAL_PROFIT, mi.REDUCE, mi.EXIT, mi.HOLD}


def test_without_portfolio_data_the_verdict_claims_no_knowledge_of_ownership():
    series = _rising()
    verdict = mi.assess(_asset(price=series[-1]), series, _context(), _volumes(), None, "full")
    assert verdict["holding"]["known"] is False
    assert verdict["action"]["perspective"] != "holder", (
        "not knowing whether someone holds an asset is not the same as knowing they do not"
    )


# ---------------------------------------------------------------------------
# Acceptance case 5 — missing data
# ---------------------------------------------------------------------------

def test_no_series_produces_data_unavailable_and_not_a_cautious_opinion():
    verdict = mi.assess(_asset(price=None, change24h=0.0), [], _context(), None, None, "full")
    assert verdict["action"]["state"] == mi.DATA_UNAVAILABLE
    assert verdict["opportunity"]["score"] is None
    assert verdict["entry"]["score"] is None, (
        "a score of 0 and 'we could not score this' look identical on a screen; "
        "only one of them is true here"
    )
    # Liquidity is computable from the row alone, with no history at all. If it
    # were allowed to stand as the risk surface, an asset the engine has just
    # said it cannot analyse would carry a reassuring LOW.
    assert verdict["risk"]["level"] is None


def test_a_series_too_short_to_reason_about_does_not_get_a_verdict_anyway():
    verdict = mi.assess(_asset(price=101.0), [100.0, 100.5, 101.0], _context(), None, None, "full")
    assert verdict["action"]["state"] == mi.DATA_UNAVAILABLE


def test_partial_data_is_reported_as_partial_rather_than_silently_narrowed():
    series = _rising(40)                       # enough to score, not enough for full depth
    verdict = mi.assess(_asset(price=series[-1]), series, _context(), None, None, "full")
    quality = verdict["dataQuality"]
    assert quality["level"] != "FULL"
    assert quality["note"], "a downgraded analysis must say so in words the screen can render"


# ---------------------------------------------------------------------------
# Properties that hold for every verdict
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "series,change",
    [
        (_rising(), 6.0),
        (_overextended(), 18.0),
        (_pullback_to_support(), -1.5),
        ([100.0] * 168, 0.0),                  # a flat market is a real market
    ]
)
def test_every_verdict_avoids_language_that_promises_an_outcome(series, change):
    verdict = mi.assess(_asset(price=series[-1], change24h=change), series, _context(),
                        _volumes(), None, "full")
    # The engine asserts this on its own output; calling it here is the
    # regression guard for the day someone adds a sixteenth state with copy
    # written in a hurry.
    mi.assert_safe_language(verdict)
    blob = repr(verdict).lower()
    for phrase in mi.BANNED_PHRASES:
        assert phrase.lower() not in blob


def test_the_banned_phrase_check_actually_rejects_a_banned_phrase():
    # A safety assertion that has never been seen to fail is not known to work.
    with pytest.raises(Exception):
        mi.assert_safe_language({"text": f"This is a {mi.BANNED_PHRASES[0]} right now."})


@pytest.mark.parametrize("series", [_rising(), _overextended(), _pullback_to_support()])
def test_scores_stay_inside_their_stated_range(series):
    verdict = mi.assess(_asset(price=series[-1]), series, _context(), _volumes(), None, "full")
    for key in ("opportunity", "entry"):
        score = verdict[key]["score"]
        assert score is None or 0.0 <= score <= 100.0
        assert not (score is not None and math.isnan(score))


@pytest.mark.parametrize("series", [_rising(), _overextended(), _pullback_to_support()])
def test_a_verdict_is_always_one_of_the_declared_states(series):
    verdict = mi.assess(_asset(price=series[-1]), series, _context(), _volumes(), None, "full")
    assert verdict["action"]["state"] in set(mi.ACTION_STATES) | {mi.DATA_UNAVAILABLE}
    assert verdict["action"]["tone"] in {"positive", "neutral", "caution", "watch", "negative", "muted"}


def test_list_depth_is_a_strict_subset_and_carries_the_reasons_the_row_shows():
    series = _rising()
    asset = _asset(price=series[-1])
    row = mi.assess(asset, series, _context(), _volumes(), None, "list")
    full = mi.assess(asset, series, _context(), _volumes(), None, "full")

    # The row and the drill-down are the same analysis at two depths. If they
    # could disagree, tapping a card would contradict the card.
    assert row["action"]["state"] == full["action"]["state"]
    assert row["opportunity"]["score"] == full["opportunity"]["score"]
    assert row["entry"]["score"] == full["entry"]["score"]
    assert len(row["why"]) <= 2
    assert "timeframes" not in row, "list depth must stay cheap enough for fifty rows"


def test_the_timeframe_table_reports_what_it_could_not_measure():
    series = _rising(30)                       # 1h and 4h are reachable, 1D and 1W are not
    verdict = mi.assess(_asset(price=series[-1]), series, _context(), _volumes(), None, "full")
    rows = verdict["timeframes"]["rows"]
    assert [row["key"] for row in rows] == [key for key, _, _, _ in mi.TIMEFRAMES]
    unmeasured = [row for row in rows if row["changePct"] is None]
    assert unmeasured, "30 hours cannot contain a weekly reading"
    for row in unmeasured:
        assert row["direction"] is None
        assert row["confidence"] == mi.UNAVAILABLE


def test_the_weekly_row_is_reachable_from_the_series_the_board_actually_sends():
    # The board sparkline is 168 hourly points — exactly seven days and not one
    # more. A weekly lookback that needed 169 would be permanently unavailable
    # for every row in the product, which is how this was found the first time.
    rows = mi.timeframe_trends(_rising(168))
    weekly = [row for row in rows if row["key"] == "1w"]
    assert weekly and weekly[0]["changePct"] is not None


def test_the_engine_is_incapable_of_fetching_anything():
    # Stage 17 in one assertion: fifty scored rows cost zero provider calls
    # because this module has nothing to call with.
    import inspect
    source = inspect.getsource(mi)
    for forbidden in ("import requests", "urllib", "httpx", "coingecko_client", "market_data"):
        assert forbidden not in source, (
            f"market_intelligence imported {forbidden!r}; list-depth scoring must stay networkless"
        )
