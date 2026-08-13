"""Creative performance — sample floors, denominators, and fatigue.

These tests defend the properties that stop a dashboard from misleading the
person paying for it:

* **A rate below its sample floor is ``None``, never 0.0.** "Nobody clicked" and
  "nobody has seen it yet" call for opposite decisions, and a float cannot tell
  them apart.

* **Invalid traffic never reaches a denominator.** It stays counted, so the
  fraud review has evidence, but it cannot move a rate or be billed.

* **Negative feedback outranks performance.** A creative people are reporting
  must not be rescued by a strong click rate — that is the exact failure mode
  where optimising on engagement promotes something people hate.

* **Rollups are idempotent.** They are a cache of the event log, so a replayed
  batch must not double-count.

    python tests/business_os/test_ads_intelligence_performance.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_perf_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-performance")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import performance, taxonomy  # noqa: E402
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402

_FLAG = "BUSINESS_OS_ADS_INTELLIGENCE_MEASUREMENT"
_CREATIVE = "creative-perf-1"
_CAMPAIGN = "campaign-perf-1"
_DAY = "2026-08-10"


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def setup_module(module=None):
    ensure_schema()
    os.environ[_FLAG] = "on"


def teardown_module(module=None):
    os.environ.pop(_FLAG, None)


def _seed(events, *, creative_id=_CREATIVE, campaign_id=_CAMPAIGN, day=_DAY):
    """events: (event_name, count, validity, subject_suffix_base)"""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM ads_intel_events WHERE creative_id = ?",
                     (creative_id,))
        seq = 0
        for name, count, validity in events:
            for i in range(count):
                seq += 1
                when = f"{day}T12:00:00.000Z"
                conn.execute(
                    "INSERT INTO ads_intel_events "
                    "(event_id, dedup_key, event_name, event_family, occurred_at, "
                    "received_at, subject_ref, campaign_id, creative_id, validity, "
                    "duration_ms, billable, quality_status, ingest_source, created_at) "
                    "VALUES (?, ?, ?, 'impression', ?, ?, ?, ?, ?, ?, 10, 0, 'ok', "
                    "'client', ?)",
                    (f"ev-{creative_id}-{seq}", f"dk-{creative_id}-{seq}", name,
                     when, when, f"subj-{i}", campaign_id, creative_id,
                     validity, when))
        conn.commit()
    finally:
        conn.close()


def _rebuild(day=_DAY, creative_id=_CREATIVE):
    conn = db.connect()
    try:
        return performance.rebuild_creative_day(
            conn, creative_id, day, campaign_id=_CAMPAIGN)
    finally:
        conn.close()


def _stored(day=_DAY, creative_id=_CREATIVE):
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT served_count, viewable_count, click_count, invalid_count, "
            "fatigue_state FROM ads_intel_creative_daily "
            "WHERE creative_id = ? AND day = ?", (creative_id, day)).fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Sample floors
# --------------------------------------------------------------------------- #

def test_a_thin_sample_returns_none_not_zero():
    """The whole point: 0.0 and None mean different things to a human."""
    thin = {"click_count": 0, "viewable_count": 5}
    rate = performance.ctr_on_viewable(thin)
    _assert(rate is None, f"a 5-impression sample produced a click rate: {rate!r}")


def test_a_real_zero_is_reported_once_the_sample_is_large_enough():
    """Above the floor, zero is a finding rather than an absence of data."""
    rate = performance.ctr_on_viewable(
        {"click_count": 0, "viewable_count": taxonomy.MIN_IMPRESSIONS_FOR_CTR})
    _assert(rate == 0.0, f"expected a measured 0.0, got {rate!r}")


def test_the_floor_is_inclusive_at_the_boundary():
    floor = taxonomy.MIN_IMPRESSIONS_FOR_CTR
    _assert(performance.ctr_on_viewable(
        {"click_count": 1, "viewable_count": floor - 1}) is None)
    _assert(performance.ctr_on_viewable(
        {"click_count": 1, "viewable_count": floor}) is not None)


def test_three_clicks_on_eleven_impressions_is_not_a_27_percent_click_rate():
    """The specific lie this module exists to prevent."""
    _assert(performance.ctr_on_viewable(
        {"click_count": 3, "viewable_count": 11}) is None)


def test_conversion_rate_floors_on_clicks_not_impressions():
    """CVR's denominator is clicks, so a million impressions cannot unlock it."""
    _assert(performance.conversion_rate(
        {"conversion_count": 1, "click_count": 2,
         "viewable_count": 1_000_000}) is None)
    _assert(performance.conversion_rate(
        {"conversion_count": 1,
         "click_count": taxonomy.MIN_CLICKS_FOR_CVR}) is not None)


def test_a_negative_or_missing_denominator_never_raises():
    for bad in ({}, {"viewable_count": None}, {"viewable_count": -5},
                {"viewable_count": "banana"}):
        _assert(performance.ctr_on_viewable(bad) is None, repr(bad))


# --------------------------------------------------------------------------- #
# Denominators
# --------------------------------------------------------------------------- #

def test_ctr_on_viewable_and_ctr_on_served_are_genuinely_different():
    """If these ever agree the viewability contract has stopped meaning anything."""
    row = {"click_count": 100, "viewable_count": 1000, "served_count": 4000}
    on_viewable = performance.ctr_on_viewable(row)
    on_served = performance.ctr_on_served(row)
    _assert(abs(on_viewable - 0.10) < 1e-9, on_viewable)
    _assert(abs(on_served - 0.025) < 1e-9, on_served)
    _assert(on_viewable > on_served,
            "unseen impressions must not flatter the click rate")


def test_summarise_never_collapses_none_into_zero():
    """A renderer must be able to print 'not enough data' rather than 0%."""
    out = performance.summarise({"served_count": 3, "viewable_count": 2})
    _assert(out["ctr_on_viewable"] is None)
    _assert(out["conversion_rate"] is None)
    _assert(out["has_enough_data"] is False)
    _assert(out["served"] == 3, "counts are still exact regardless of the floors")


def test_every_rate_in_the_summary_names_its_denominator():
    """Guards against a future 'ctr' key that nobody can interpret."""
    out = performance.summarise({"served_count": 1})
    rate_keys = [k for k in out if k.endswith("_rate") or k.startswith("ctr_")]
    for key in rate_keys:
        _assert("_on_" in key or key.startswith(
            ("viewability_", "conversion_", "negative_", "invalid_")),
            f"{key} does not say what it is a rate over")
    _assert("ctr" not in out, "a bare 'ctr' key is ambiguous by construction")


# --------------------------------------------------------------------------- #
# Invalid traffic
# --------------------------------------------------------------------------- #

def test_invalid_traffic_is_counted_but_never_reaches_a_denominator():
    """Fraud stays visible as evidence and inert as a metric."""
    _seed([("ad_served", 600, "valid"), ("ad_viewable", 600, "valid"),
           ("ad_click", 6, "valid"),
           ("ad_served", 400, "invalid"), ("ad_viewable", 400, "invalid"),
           ("ad_click", 300, "invalid")])
    counts = _rebuild()

    _assert(counts["served_count"] == 600,
            f"invalid impressions leaked into served_count: {counts['served_count']}")
    _assert(counts["click_count"] == 6,
            f"invalid clicks leaked into click_count: {counts['click_count']}")
    _assert(counts["invalid_count"] == 1100,
            f"evidence was lost: {counts['invalid_count']}")

    ctr = performance.ctr_on_viewable(counts)
    _assert(abs(ctr - 0.01) < 1e-9,
            f"300 fake clicks moved the click rate to {ctr!r}")


def test_suspect_and_under_review_traffic_is_also_held_out_of_rates():
    """Undecided is not the same as clean; spending on it would be a decision."""
    _seed([("ad_served", 600, "valid"), ("ad_viewable", 600, "valid"),
           ("ad_click", 6, "valid"),
           ("ad_click", 500, "suspect"), ("ad_click", 500, "under_review")])
    counts = _rebuild()
    _assert(counts["click_count"] == 6,
            f"undecided clicks were counted as real: {counts['click_count']}")
    _assert(counts["excluded_count"] == 1000, counts["excluded_count"])
    _assert(counts["invalid_count"] == 0,
            "'suspect' must not be reported as a proven invalid")


def test_the_invalid_impression_rate_is_over_all_impressions_recorded():
    """Dividing by the survivors would understate the junk."""
    _seed([("ad_served", 750, "valid"), ("ad_served", 250, "invalid")])
    counts = _rebuild()
    rate = performance.invalid_impression_rate(counts)
    _assert(abs(rate - 0.25) < 1e-9,
            f"expected 250/1000, got {rate!r} (denominator is probably wrong)")


# --------------------------------------------------------------------------- #
# Rollups
# --------------------------------------------------------------------------- #

def test_a_replayed_rebuild_does_not_double_count():
    """Rollups are a cache of the log, so they are replaced, never incremented."""
    _seed([("ad_served", 600, "valid"), ("ad_viewable", 600, "valid"),
           ("ad_click", 30, "valid")])
    first = _rebuild()
    second = _rebuild()
    third = _rebuild()
    _assert(first["served_count"] == second["served_count"] == third["served_count"],
            f"{first['served_count']} vs {second['served_count']} vs "
            f"{third['served_count']}")
    rows = _stored()
    _assert(len(rows) == 1, f"rebuild created {len(rows)} rows for one creative/day")
    _assert(rows[0][0] == 600, rows[0])


def test_a_rebuild_reflects_deleted_events_rather_than_remembering_them():
    """The rollup follows the log down as well as up."""
    _seed([("ad_served", 600, "valid"), ("ad_viewable", 600, "valid")])
    _assert(_rebuild()["served_count"] == 600)
    _seed([("ad_served", 10, "valid")])
    _assert(_rebuild()["served_count"] == 10,
            "the rollup kept counts the event log no longer supports")


def test_rollups_are_scoped_to_their_day():
    _seed([("ad_served", 600, "valid")], day=_DAY)
    _assert(_rebuild(day=_DAY)["served_count"] == 600)
    other = _rebuild(day="2026-08-11")
    _assert(other["served_count"] == 0,
            "events bled across the day boundary")


def test_a_campaign_rollup_never_fabricates_an_opportunity_count():
    """A permanent 100% win rate is worse than an empty column."""
    _seed([("ad_served", 600, "valid"), ("ad_viewable", 600, "valid")])
    conn = db.connect()
    try:
        performance.rebuild_campaign_day(conn, _CAMPAIGN, _DAY)
        row = conn.execute(
            "SELECT opportunity_count, eligible_count, won_count "
            "FROM ads_intel_campaign_daily WHERE campaign_id = ? AND day = ?",
            (_CAMPAIGN, _DAY)).fetchone()
    finally:
        conn.close()
    _assert(row is not None, "no campaign rollup was written")
    _assert(row[0] == 0 and row[1] == 0,
            f"opportunity/eligible were invented from won_count: {tuple(row)}")


def test_a_broken_connection_degrades_to_zero_rather_than_exploding():
    """A reporting failure must never take out the request that triggered it."""
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("db is down")

        def commit(self):
            raise RuntimeError("db is down")

    counts = performance.rebuild_creative_day(_Broken(), _CREATIVE, _DAY)
    _assert(counts["served_count"] == 0, counts)


def test_an_empty_identifier_is_rejected_before_any_write():
    _assert(performance.rebuild_creative_day(None, "", _DAY) == {})
    _assert(performance.rebuild_campaign_day(None, None, _DAY) == {})


# --------------------------------------------------------------------------- #
# Fatigue
# --------------------------------------------------------------------------- #

def test_fatigue_says_insufficient_data_rather_than_guessing_healthy():
    """Most creatives most of the time genuinely have no verdict available."""
    verdict = performance.assess_fatigue({"viewable_count": 40})
    _assert(verdict["state"] == "INSUFFICIENT_DATA", verdict)
    _assert(str(taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE) in verdict["reason"],
            "the reason should say how much data is needed")


def test_negative_feedback_beats_a_strong_click_rate():
    """The core safety property: engagement cannot rescue a reported creative."""
    viewable = taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE
    loved_but_reported = {
        "viewable_count": viewable,
        "click_count": int(viewable * 0.5),          # a spectacular click rate
        "negative_count": int(viewable * performance.FATIGUE_NEGATIVE_RATE) + 1,
    }
    verdict = performance.assess_fatigue(loved_but_reported)
    _assert(verdict["state"] == "REJECTED",
            f"a widely-reported creative was kept alive by its CTR: {verdict}")


def test_the_negative_threshold_is_inclusive():
    viewable = taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE
    exactly_at = {"viewable_count": viewable, "click_count": 10,
                  "negative_count": int(viewable * performance.FATIGUE_NEGATIVE_RATE)}
    _assert(performance.assess_fatigue(exactly_at)["state"] == "REJECTED")


def test_a_falling_click_rate_wears_then_fatigues():
    viewable = taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE
    baseline = {"viewable_count": viewable, "click_count": 100}

    def _at(fraction):
        return performance.assess_fatigue(
            {"viewable_count": viewable, "click_count": int(100 * fraction)},
            baseline=baseline)["state"]

    _assert(_at(1.0) == "HEALTHY", "flat performance is not fatigue")
    _assert(_at(0.90) == "HEALTHY", "a 10% dip is noise, not fatigue")
    _assert(_at(0.65) == "WEARING", "a 35% drop should be WEARING")
    _assert(_at(0.40) == "FATIGUED", "a 60% drop should be FATIGUED")


def test_fatigue_is_not_a_statement_about_age():
    """A creative running for a year with steady engagement is not fatigued."""
    viewable = taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE * 50
    steady = {"viewable_count": viewable, "click_count": int(viewable * 0.02)}
    verdict = performance.assess_fatigue(steady, baseline=dict(steady))
    _assert(verdict["state"] == "HEALTHY", verdict)


def test_a_verdict_always_carries_its_reason():
    """Pausing a creative is a decision somebody will ask us to justify."""
    viewable = taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE
    cases = [
        {"viewable_count": 3},
        {"viewable_count": viewable, "click_count": 10,
         "negative_count": viewable},
        {"viewable_count": viewable, "click_count": 10},
    ]
    for case in cases:
        verdict = performance.assess_fatigue(case)
        _assert(verdict["state"] in performance.FATIGUE_STATES, verdict)
        _assert(verdict.get("reason"), f"no reason attached: {verdict}")


def test_a_missing_baseline_does_not_invent_a_decline():
    viewable = taxonomy.MIN_IMPRESSIONS_FOR_FATIGUE
    current = {"viewable_count": viewable, "click_count": 5}
    for baseline in (None, {}, {"viewable_count": 0, "click_count": 0}):
        verdict = performance.assess_fatigue(current, baseline=baseline)
        _assert(verdict["state"] == "HEALTHY",
                f"baseline={baseline!r} produced {verdict['state']}")


def test_the_stored_fatigue_state_matches_a_fresh_assessment():
    """The rollup's cached verdict must not drift from the live rule."""
    _seed([("ad_served", 600, "valid"), ("ad_viewable", 600, "valid"),
           ("ad_click", 12, "valid")])
    counts = _rebuild()
    _assert(_stored()[0][4] == performance.assess_fatigue(counts)["state"])


def test_a_trend_compares_a_creative_against_its_own_history():
    """Comparing to a platform average would call a niche creative fatigued."""
    now = datetime.now(timezone.utc)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM ads_intel_creative_daily WHERE creative_id = ?",
                     ("creative-trend",))
        for age, clicks in ((10, 200), (9, 200), (2, 40), (1, 40)):
            day = (now - timedelta(days=age)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO ads_intel_creative_daily "
                "(rollup_id, creative_id, day, served_count, viewable_count, "
                "click_count, computed_at) VALUES (?, ?, ?, 6000, 6000, ?, ?)",
                (f"creative-trend:{day}", "creative-trend", day, clicks,
                 _iso(now)))
        conn.commit()
        trend = performance.creative_trend(conn, "creative-trend", days=14, now=now)
    finally:
        conn.close()

    _assert(trend["creative_id"] == "creative-trend")
    _assert(trend["recent"]["clicks"] == 80, trend["recent"])
    _assert(trend["baseline"]["clicks"] == 400, trend["baseline"])
    _assert(trend["state"] == "FATIGUED",
            f"an 80% collapse in click rate read as {trend['state']}")


def test_a_trend_on_an_unknown_creative_is_answerable():
    conn = db.connect()
    try:
        trend = performance.creative_trend(conn, "creative-does-not-exist")
    finally:
        conn.close()
    _assert(trend["state"] == "INSUFFICIENT_DATA", trend)


def _main():
    setup_module()
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failed += 1
            print(f"  FAIL {name}: {exc}")
    teardown_module()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
