"""The sampled market observation series, and what it refuses to claim.

Time-window alerts are the one place in this feature where the honest answer is
usually "I don't know yet", and the whole value of the module is that it says so
instead of producing a number. So most of what is locked in here is refusal:

  * a window longer than the series is old is ``window_not_covered``, never
    quietly answered with however much history happens to exist;
  * a hole in the middle of the series is ``window_gap``, not a comparison
    against whatever sample sits on the far side of it;
  * a sampler that has stopped is ``series_stale``, so a twenty-minute-old
    "current" price is never presented as current;
  * a metric the provider omitted for either endpoint is ``metric_unavailable``,
    never zero;
  * re-recording a cached board is a no-op, so the sample count measures how
    often the market moved on from us rather than how often we asked;
  * ``coverage`` offers only the windows that can be answered right now, which
    is what stops a member creating a rule that is undecidable the moment it is
    saved;
  * the window vocabulary has exactly one owner. If this module ever declares
    its own, a rule the validator accepts could be a window the series refuses,
    and the member would experience it as an alert that silently never fires.

Run directly (no pytest required):

    python tests/test_market_observations.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_market_obs_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import crypto_alert_conditions as conditions  # noqa: E402
from services import market_observations as obs  # noqa: E402
from services import user_context  # noqa: E402


FAILURES: list[str] = []

#: A fixed "now" so every window boundary in this file is exact arithmetic
#: rather than a race against the wall clock.
NOW = datetime(2026, 8, 23, 12, 0, 0)


def check(label: str, actual, expected):
    if actual == expected:
        print(f"  PASS  {label}")
        return
    FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    raise AssertionError(label)


def reset():
    obs.ensure_schema()
    conn = user_context.connect()
    try:
        conn.cursor().execute("DELETE FROM market_observations")
        conn.commit()
    finally:
        conn.close()
    obs._LAST_PRUNE_AT[0] = None


def sample(symbol="BTC", minutes_ago=0, price=100.0, stamp=None, **metrics):
    """Record one board containing one symbol, as of ``minutes_ago``.

    ``stamp`` defaults to something unique per moment, which is the normal case:
    a genuine provider read. Passing it explicitly is how the cached-board case
    is reproduced.
    """
    moment = NOW - timedelta(minutes=minutes_ago)
    item = {"symbol": symbol, "price": price}
    item.update(metrics)
    board = {"markets": [item], "source": "test",
             "updated_at": stamp or f"stamp-{symbol}-{minutes_ago}"}
    return obs.record_board(board, now=moment)


def reading(symbol="BTC", metric="price", minutes=60):
    return obs.window_reading(symbol, metric, minutes, now=NOW)


def count_rows(symbol="BTC") -> int:
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM market_observations WHERE symbol=?", (symbol,))
        return int((cur.fetchone() or [0])[0] or 0)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# MOBS-001  The vocabulary has one owner
# --------------------------------------------------------------------------
def test_window_vocabulary_is_not_duplicated():
    """If these ever diverge, a rule the validator accepts becomes a window the
    series refuses, and the member sees an alert that never fires with no error
    anywhere to explain it."""
    check("window choices are the validator's",
          obs.WINDOW_CHOICES is conditions.WINDOW_CHOICES, True)
    check("windowable metrics are the validator's",
          obs.WINDOWABLE_METRICS is conditions.WINDOWABLE_METRICS, True)
    check("normalize_window is the validator's",
          obs.normalize_window is conditions.normalize_window, True)


# --------------------------------------------------------------------------
# MOBS-002  Sampling
# --------------------------------------------------------------------------
def test_a_cached_board_is_recorded_once():
    """The board is cached for the same 45s the worker cycles on, so a cycle
    landing on a cache hit is re-reading a market already recorded. Counting it
    again would make a sparse series look dense."""
    reset()
    first = sample(minutes_ago=0, price=100.0, stamp="provider-A")
    check("first board recorded", first["recorded"], 1)
    sample(minutes_ago=0, price=100.0, stamp="provider-A")
    check("re-read of the same board stored nothing", count_rows(), 1)
    sample(minutes_ago=0, price=101.0, stamp="provider-B")
    check("a genuinely new board is stored", count_rows(), 2)


def test_a_board_without_a_provider_stamp_is_refused():
    """With no way to tell one read from the next, every cycle would record a
    fresh sample of a possibly-cached market."""
    reset()
    result = obs.record_board({"markets": [{"symbol": "BTC", "price": 1.0}]}, now=NOW)
    check("refused", result["ok"], False)
    check("reason", result["reason"], "no_provider_timestamp")
    check("nothing stored", count_rows(), 0)


def test_a_row_without_a_price_is_not_an_observation():
    reset()
    board = {"markets": [{"symbol": "BTC", "price": None},
                         {"symbol": "ETH", "price": 20.0}],
             "updated_at": "stamp-1", "source": "test"}
    result = obs.record_board(board, now=NOW)
    check("only the priced row was recorded", result["recorded"], 1)
    check("the unpriced row was skipped", result["skipped"], 1)
    check("BTC stored nothing", count_rows("BTC"), 0)


def test_an_empty_board_is_a_no_op_not_a_crash():
    """Sampling failure must never stop the worker's alert sweep: every rule
    that does not use a window works perfectly well without the series."""
    reset()
    check("empty", obs.record_board({"markets": []}, now=NOW)["reason"], "empty_board")
    check("none", obs.record_board(None, now=NOW)["reason"], "empty_board")


# --------------------------------------------------------------------------
# MOBS-003  Windows that can be answered
# --------------------------------------------------------------------------
def test_a_covered_window_reports_the_change_and_what_it_measured():
    reset()
    sample(minutes_ago=61, price=100.0)
    sample(minutes_ago=30, price=105.0)
    sample(minutes_ago=0, price=110.0)
    result = reading(minutes=60)
    check("ok", result["ok"], True)
    check("change is measured from the sample at the boundary",
          round(result["change_percent"], 4), 10.0)
    check("baseline", result["baseline"], 100.0)
    check("latest", result["latest"], 110.0)
    # The copy quotes this, not the requested window: it is what could actually
    # be measured, and claiming the requested window to the minute would be a
    # precision the sampler does not have.
    check("baseline age is the real interval", result["baseline_age_seconds"], 61 * 60)
    check("sample count spans the window", result["sample_count"], 3)


def test_a_fall_is_reported_as_a_negative_change():
    reset()
    sample(minutes_ago=61, price=200.0)
    sample(minutes_ago=0, price=180.0)
    check("change", round(reading(minutes=60)["change_percent"], 4), -10.0)


def test_the_baseline_is_the_reading_current_when_the_window_opened():
    """Not the oldest row we have. Answering a 60-minute question from a
    four-hour-old sample is a different question with a different answer, and
    the member cannot see the substitution."""
    reset()
    sample(minutes_ago=240, price=50.0)
    sample(minutes_ago=61, price=100.0)
    sample(minutes_ago=0, price=110.0)
    check("60m uses the 61m sample", reading(minutes=60)["baseline"], 100.0)
    check("240m uses the 240m sample", reading(minutes=240)["baseline"], 50.0)


def test_volume_and_market_cap_are_windowable():
    reset()
    sample(minutes_ago=61, price=1.0, volume_24h=1000.0, market_cap=500.0)
    sample(minutes_ago=0, price=1.0, volume_24h=1500.0, market_cap=250.0)
    check("volume", round(reading(metric="volume_24h")["change_percent"], 4), 50.0)
    check("market cap", round(reading(metric="market_cap")["change_percent"], 4), -50.0)


# --------------------------------------------------------------------------
# MOBS-004  Windows that must be refused
# --------------------------------------------------------------------------
def test_a_window_older_than_the_series_is_undecidable():
    """The sampler started twenty minutes ago. A one-hour rule must not be
    answered with twenty minutes of history."""
    reset()
    sample(minutes_ago=20, price=100.0)
    sample(minutes_ago=0, price=110.0)
    result = reading(minutes=60)
    check("undecidable", result["ok"], False)
    check("reason", result["reason"], "window_not_covered")
    check("no number is offered", result["change_percent"], None)


def test_a_hole_in_the_series_is_undecidable():
    """A sample far past the boundary is not the reading that was current when
    the window opened, and comparing against it would report a much larger
    interval as the requested window."""
    reset()
    sample(minutes_ago=600, price=100.0)
    sample(minutes_ago=0, price=110.0)
    result = reading(minutes=60)
    check("undecidable", result["ok"], False)
    check("reason", result["reason"], "window_gap")


def test_a_baseline_inside_the_tolerance_is_still_answered():
    """A lost cycle or two — a worker restart, a provider timeout — must not
    invalidate the window. Only a real hole does."""
    reset()
    drift = obs.MAX_BASELINE_DRIFT_SECONDS // 2
    sample(minutes_ago=60 + drift // 60, price=100.0)
    sample(minutes_ago=0, price=110.0)
    check("answered", reading(minutes=60)["ok"], True)


def test_a_stopped_sampler_is_undecidable_not_flat():
    """A 'current' value from far in the past compared against an even older
    baseline is not the window anyone asked for."""
    reset()
    stale = obs.MAX_LATEST_AGE_SECONDS // 60 + 10
    sample(minutes_ago=stale + 70, price=100.0)
    sample(minutes_ago=stale, price=110.0)
    result = reading(minutes=60)
    check("undecidable", result["ok"], False)
    check("reason", result["reason"], "series_stale")


def test_a_symbol_never_sampled_is_undecidable():
    reset()
    sample(symbol="BTC", minutes_ago=0)
    result = reading(symbol="DOGE")
    check("undecidable", result["ok"], False)
    check("reason", result["reason"], "no_series")


def test_a_metric_the_provider_omitted_is_undecidable_not_zero():
    """The Coinbase fallback carries a price and nothing else. A market cap that
    was never published must not read as a market cap that collapsed."""
    reset()
    sample(minutes_ago=61, price=100.0, market_cap=None)
    sample(minutes_ago=0, price=110.0, market_cap=None)
    result = reading(metric="market_cap")
    check("undecidable", result["ok"], False)
    check("reason", result["reason"], "metric_unavailable")


def test_a_zero_baseline_has_no_percent_change():
    """A percent change from zero has no value, and reporting one would be
    inventing a number rather than reading one."""
    reset()
    sample(minutes_ago=61, price=100.0, volume_24h=0.0)
    sample(minutes_ago=0, price=110.0, volume_24h=500.0)
    result = reading(metric="volume_24h")
    check("undecidable", result["ok"], False)
    check("reason", result["reason"], "baseline_zero")


def test_a_delta_metric_cannot_be_windowed():
    """The percent change of a 24h percent change is not a quantity anybody
    means to ask about."""
    reset()
    sample(minutes_ago=61, price=100.0, change_24h=1.0)
    sample(minutes_ago=0, price=110.0, change_24h=2.0)
    result = reading(metric="change_24h")
    check("undecidable", result["ok"], False)
    check("reason", result["reason"], "unsupported_metric")


def test_a_window_outside_the_offered_set_is_refused():
    reset()
    sample(minutes_ago=61, price=100.0)
    sample(minutes_ago=0, price=110.0)
    check("45m is not offered", reading(minutes=45)["reason"], "unsupported_window")
    check("nonsense is not offered", reading(minutes="soon")["reason"], "unsupported_window")


# --------------------------------------------------------------------------
# MOBS-005  Coverage and retention
# --------------------------------------------------------------------------
def test_coverage_offers_only_windows_that_can_be_answered():
    """The UI reads this, which is what stops a member creating a rule that is
    undecidable the moment it is saved."""
    reset()
    sample(minutes_ago=70, price=100.0)
    sample(minutes_ago=0, price=110.0)
    result = obs.coverage("BTC", now=NOW)
    check("15m offered", 15 in result["available_windows"], True)
    check("60m offered", 60 in result["available_windows"], True)
    check("120m not offered", 120 in result["available_windows"], False)
    check("sample count", result["sample_count"], 2)


def test_coverage_offers_nothing_while_the_sampler_is_down():
    """A long history does not make a window answerable if the current end of it
    is missing."""
    reset()
    stale = obs.MAX_LATEST_AGE_SECONDS // 60 + 10
    sample(minutes_ago=stale + 600, price=100.0)
    sample(minutes_ago=stale, price=110.0)
    result = obs.coverage("BTC", now=NOW)
    check("stale", result["stale"], True)
    check("no windows offered", result["available_windows"], [])


def test_coverage_of_an_unsampled_symbol_is_empty_not_an_error():
    reset()
    result = obs.coverage("NOPE", now=NOW)
    check("ok", result["ok"], True)
    check("no samples", result["sample_count"], 0)
    check("no windows", result["available_windows"], [])


def test_retention_drops_samples_past_the_horizon():
    """Pruning rides along with sampling — the worker has no separate schedule,
    and a series nobody is writing to has nothing worth pruning."""
    reset()
    sample(minutes_ago=(obs.RETENTION_HOURS + 5) * 60, price=50.0)
    sample(minutes_ago=(obs.RETENTION_HOURS - 5) * 60, price=75.0)
    check("both stored", count_rows(), 2)
    sample(minutes_ago=0, price=100.0)
    check("only the samples inside the horizon remain", count_rows(), 2)
    check("the pruned one is the oldest",
          obs.coverage("BTC", now=NOW)["span_minutes"], (obs.RETENTION_HOURS - 5) * 60)


def test_retention_keeps_the_longest_window_answerable():
    """Retention must exceed the longest offered window, or the rule at the
    boundary loses its baseline to the pruner rather than to the market."""
    check("retention outlasts the longest window",
          obs.RETENTION_HOURS * 60 > max(obs.WINDOW_CHOICES), True)


def test_pruning_is_rate_limited():
    """It runs on every sampling cycle; a full-table scan every 45 seconds is
    not what the horizon requires."""
    reset()
    obs._LAST_PRUNE_AT[0] = None
    obs.prune_if_due(now=NOW)
    second = obs.prune_if_due(now=NOW + timedelta(seconds=30))
    check("skipped", second.get("skipped"), True)
    third = obs.prune_if_due(now=NOW + timedelta(seconds=obs.PRUNE_INTERVAL_SECONDS + 60))
    check("due again", third.get("skipped"), None)


TESTS = [
    test_window_vocabulary_is_not_duplicated,
    test_a_cached_board_is_recorded_once,
    test_a_board_without_a_provider_stamp_is_refused,
    test_a_row_without_a_price_is_not_an_observation,
    test_an_empty_board_is_a_no_op_not_a_crash,
    test_a_covered_window_reports_the_change_and_what_it_measured,
    test_a_fall_is_reported_as_a_negative_change,
    test_the_baseline_is_the_reading_current_when_the_window_opened,
    test_volume_and_market_cap_are_windowable,
    test_a_window_older_than_the_series_is_undecidable,
    test_a_hole_in_the_series_is_undecidable,
    test_a_baseline_inside_the_tolerance_is_still_answered,
    test_a_stopped_sampler_is_undecidable_not_flat,
    test_a_symbol_never_sampled_is_undecidable,
    test_a_metric_the_provider_omitted_is_undecidable_not_zero,
    test_a_zero_baseline_has_no_percent_change,
    test_a_delta_metric_cannot_be_windowed,
    test_a_window_outside_the_offered_set_is_refused,
    test_coverage_offers_only_windows_that_can_be_answered,
    test_coverage_offers_nothing_while_the_sampler_is_down,
    test_coverage_of_an_unsampled_symbol_is_empty_not_an_error,
    test_retention_drops_samples_past_the_horizon,
    test_retention_keeps_the_longest_window_answerable,
    test_pruning_is_rate_limited,
]


def main():
    print("Market observation series")
    for test in TESTS:
        print(f"\n{test.__name__}")
        test()
    print(f"\n{len(TESTS)} tests, {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
