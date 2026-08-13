"""Pacing, frequency caps and ad load.

The properties defended here:

* **Pacing is never a money authority.** It reads the canonical spend view and
  can only reduce delivery. It fails *open*, because it was never the thing
  preventing an overspend.

* **Throttling is per-opportunity, not per-viewer.** Hashing the viewer would
  hand a throttled campaign a biased audience rather than a smaller one.

* **Frequency counts are derived from the immutable log**, so a replay cannot
  inflate them and a cap ages out on its own.

* **Ad load is not purchasable.** It is a property of the session, not of a
  campaign.

    python tests/business_os/test_ads_intelligence_pacing.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_pace_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-pacing")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import inspect  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import (  # noqa: E402
    frequency, pacing, taxonomy)
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _at(hour, minute=0):
    return datetime(2026, 8, 10, hour, minute, tzinfo=timezone.utc)


def setup_module(module=None):
    ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_ad_impression_events ("
            "event_id TEXT PRIMARY KEY, delivery_id TEXT, campaign_id TEXT, "
            "ad_set_id TEXT, creative_id TEXT, creative_version INTEGER, "
            "placement TEXT, subject_ref TEXT, advertiser_user_id TEXT, "
            "event_at TEXT, dedup_key TEXT UNIQUE, request_meta_json TEXT, "
            "fraud_status TEXT, billing_eligible INTEGER, "
            "billing_processed INTEGER, created_at TEXT)")
        conn.commit()
    finally:
        conn.close()


def _seed_impressions(subject, count, *, age_seconds=0, campaign="camp-1",
                      creative="cr-1", advertiser="adv-1"):
    conn = db.connect()
    try:
        when = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        for i in range(count):
            key = f"{subject}-{campaign}-{creative}-{age_seconds}-{i}"
            conn.execute(
                "INSERT OR REPLACE INTO business_os_ad_impression_events "
                "(event_id, delivery_id, campaign_id, ad_set_id, creative_id, "
                "creative_version, placement, subject_ref, advertiser_user_id, "
                "event_at, dedup_key, fraud_status, billing_eligible, "
                "billing_processed, created_at) VALUES "
                "(?, 'd', ?, 'a', ?, 1, 'feed', ?, ?, ?, ?, 'clean', 0, 0, ?)",
                (f"ev-{key}", campaign, creative, subject, advertiser, stamp,
                 f"dk-{key}", stamp))
        conn.commit()
    finally:
        conn.close()


def _clear_impressions():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_ad_impression_events")
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Pacing is not a money authority
# --------------------------------------------------------------------------- #

def test_pacing_never_moves_money():
    """It can slow delivery down. It cannot charge, reserve or release."""
    source = inspect.getsource(pacing)
    code = source.split('"""', 2)[-1]
    banned = ("reserve_funds", "release_funds", "charge", "capture",
              "post_ledger", "debit", "credit", "INSERT INTO business_os_ad_",
              "wallet")
    for word in banned:
        _assert(word not in code,
                f"{word!r} appears in pacing — it must not be a money authority")


def test_pacing_only_ever_reduces_delivery():
    """No input may produce a throttle above full delivery."""
    for budget in (0, 1, 10_000, 1_000_000):
        for spent in (0, 1, 5_000, 10_000, 999_999_999):
            for hour in (0, 6, 12, 18, 23):
                result = pacing.assess(budget, spent, now=_at(hour))
                _assert(0.0 <= result["throttle"] <= 1.0,
                        f"budget={budget} spent={spent} hour={hour} -> "
                        f"{result['throttle']}")


def test_pacing_fails_open_when_spend_cannot_be_read():
    """Failing closed would stop every campaign; failing open cannot overspend."""
    from services.business_os.advertising import spend as _spend
    original = _spend.get_campaign_spend
    _spend.get_campaign_spend = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("ledger unavailable"))
    try:
        result = pacing.state_for("camp-1")
    finally:
        _spend.get_campaign_spend = original
    _assert(result["throttle"] == 1.0, result)
    _assert(result["degraded"] is True, result)


def test_exhaustion_is_reported_by_the_canonical_gate_not_decided_here():
    """Pacing agrees with the budget gate; it does not compute exhaustion."""
    result = pacing.assess(10_000, 10_000, now=_at(12), exhausted=True)
    _assert(result["state"] == "EXHAUSTED", result)
    _assert(result["throttle"] == 0.0, result)
    # Without the gate saying so, heavy spend is throttled but not stopped.
    ahead = pacing.assess(10_000, 10_000, now=_at(12), exhausted=False)
    _assert(ahead["state"] != "EXHAUSTED", ahead)
    _assert(ahead["throttle"] > 0.0,
            "pacing decided a campaign was exhausted on its own")


# --------------------------------------------------------------------------- #
# Pacing behaviour
# --------------------------------------------------------------------------- #

def test_an_evenly_spending_campaign_is_left_alone():
    # Half the day gone, half the budget spent.
    result = pacing.assess(10_000, 5_000, now=_at(12))
    _assert(result["state"] == "ON_TARGET", result)
    _assert(result["throttle"] == 1.0, result)


def test_a_slow_campaign_is_never_throttled():
    result = pacing.assess(10_000, 1_000, now=_at(12))
    _assert(result["state"] == "UNDERPACING", result)
    _assert(result["throttle"] == 1.0, result)


def test_a_fast_campaign_is_throttled_proportionally():
    modest = pacing.assess(10_000, 6_500, now=_at(12))   # 1.3x
    severe = pacing.assess(10_000, 10_000, now=_at(12))  # 2.0x
    _assert(modest["state"] == "OVERPACING", modest)
    _assert(severe["state"] == "LIMITED", severe)
    _assert(severe["throttle"] < modest["throttle"] < 1.0,
            (modest["throttle"], severe["throttle"]))


def test_pacing_slows_a_campaign_but_never_makes_it_vanish():
    """Zero delivery is indistinguishable from broken, from the advertiser's side."""
    absurd = pacing.assess(10_000, 100_000_000, now=_at(12))
    _assert(absurd["throttle"] >= pacing.MIN_THROTTLE, absurd)
    _assert(absurd["throttle"] > 0.0, absurd)


def test_the_first_minutes_of_the_day_do_not_trigger_a_throttle():
    """Early on, the elapsed fraction is tiny and every ratio explodes."""
    result = pacing.assess(10_000, 500, now=_at(0, 10))
    _assert(result["throttle"] == 1.0, result)
    _assert(result["state"] == "ON_TARGET", result)


def test_no_daily_budget_means_no_pacing_rather_than_no_delivery():
    result = pacing.assess(0, 50_000, now=_at(12))
    _assert(result["throttle"] == 1.0, result)


def test_every_assessment_carries_a_reason():
    cases = [(0, 0, False), (10_000, 0, False), (10_000, 5_000, False),
             (10_000, 50_000, False), (10_000, 10_000, True)]
    for budget, spent, exhausted in cases:
        result = pacing.assess(budget, spent, now=_at(12), exhausted=exhausted)
        _assert(result.get("reason"), result)
        _assert(result["state"] in taxonomy.PACING_STATES, result)
        _assert(pacing.explain(result), result)


# --------------------------------------------------------------------------- #
# Throttle admission
# --------------------------------------------------------------------------- #

def test_throttling_is_spread_across_opportunities_not_viewers():
    """Hashing the viewer would give a throttled campaign a biased audience."""
    params = inspect.signature(pacing.admits).parameters
    _assert("subject_ref" not in params and "viewer" not in params,
            f"admits() is keyed on the viewer: {list(params)}")


def test_a_throttle_admits_roughly_its_share():
    admitted = sum(1 for i in range(2000)
                   if pacing.admits("camp-1", f"opp-{i}", 0.25))
    _assert(400 < admitted < 600,
            f"a 25% throttle admitted {admitted}/2000 opportunities")


def test_admission_is_deterministic_for_the_same_opportunity():
    first = [pacing.admits("camp-1", f"opp-{i}", 0.5) for i in range(200)]
    second = [pacing.admits("camp-1", f"opp-{i}", 0.5) for i in range(200)]
    _assert(first == second, "admission is not reproducible")


def test_full_throttle_admits_everything_and_zero_admits_nothing():
    _assert(all(pacing.admits("c", f"o-{i}", 1.0) for i in range(100)))
    _assert(not any(pacing.admits("c", f"o-{i}", 0.0) for i in range(100)))


def test_a_junk_throttle_admits_rather_than_blocks():
    """A malformed throttle must not silently stop a campaign."""
    for junk in (None, "banana", [], {}):
        _assert(pacing.admits("c", "o", junk) is True, repr(junk))


def test_a_pacing_record_is_written_for_diagnostics():
    result = pacing.assess(10_000, 9_000, now=_at(18))
    conn = db.connect()
    try:
        _assert(pacing.record(conn, "camp-record", result, now=_at(18)) is True)
        row = conn.execute(
            "SELECT pacing_state, throttle_factor FROM ads_intel_campaign_pacing "
            "WHERE campaign_id = ?", ("camp-record",)).fetchone()
    finally:
        conn.close()
    _assert(row is not None, "no pacing record was written")
    _assert(row[0] == result["state"], row)


def test_a_pacing_write_failure_is_not_fatal():
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("down")

        def commit(self):
            raise RuntimeError("down")

    _assert(pacing.record(_Broken(), "c", {"state": "ON_TARGET"}) is False)


# --------------------------------------------------------------------------- #
# Frequency
# --------------------------------------------------------------------------- #

def test_frequency_counts_come_from_the_immutable_log():
    """No counter table means nothing to drift or reconcile."""
    source = inspect.getsource(frequency)
    _assert("business_os_ad_impression_events" in source)
    code = source.split('"""', 2)[-1]
    for write in ("INSERT INTO ads_intel_frequency_windows", "UPDATE "):
        _assert(write not in code,
                f"frequency maintains a counter with {write.strip()}")


def test_a_cap_is_hit_at_the_configured_count():
    """The boundary is ``>=``, not ``>``: a cap of n permits n, not n + 1.

    Seeded an hour back so the impressions sit inside the day window but
    outside the half-hour session window — otherwise the much tighter session
    cap fires first and this stops being a test of the day cap.
    """
    _clear_impressions()
    cap = taxonomy.DEFAULT_FREQUENCY_CAPS[("creative", "day")]
    an_hour = 3600
    for i in range(cap - 1):
        _seed_impressions("subj-cap", 1, age_seconds=an_hour + i)
    conn = db.connect()
    try:
        before = frequency.check(conn, subject_ref="subj-cap",
                                 creative_id="cr-1")
        _assert(before["capped"] is False,
                f"capped at {cap - 1} of a cap of {cap}: {before}")
        _seed_impressions("subj-cap", 1, age_seconds=an_hour + cap)
        after = frequency.check(conn, subject_ref="subj-cap", creative_id="cr-1")
    finally:
        conn.close()
    _assert(after["capped"] is True, after)
    _assert(after["reason"] == "FREQUENCY_LIMITED", after)
    _assert(any(e["window"] == "day" for e in after["breached"]), after)


def test_an_old_impression_ages_out_of_its_window():
    """The legacy lifetime counter could only ever tighten. This one recovers."""
    _clear_impressions()
    older_than_a_week = 8 * 24 * 3600
    _seed_impressions("subj-old", 50, age_seconds=older_than_a_week)
    conn = db.connect()
    try:
        result = frequency.check(conn, subject_ref="subj-old",
                                 creative_id="cr-1", campaign_id="camp-1")
    finally:
        conn.close()
    _assert(result["capped"] is False,
            f"impressions from {older_than_a_week // 86400} days ago still "
            f"count: {result}")


def test_the_tightest_breach_is_the_one_reported():
    """That is the cap the advertiser actually has to do something about."""
    _clear_impressions()
    _seed_impressions("subj-many", 40)
    conn = db.connect()
    try:
        result = frequency.check(conn, subject_ref="subj-many",
                                 creative_id="cr-1", campaign_id="camp-1",
                                 advertiser_user_id="adv-1")
    finally:
        conn.close()
    _assert(result["capped"] is True, result)
    caps = [b["cap"] for b in result["breached"]]
    _assert(min(caps) == min(b["cap"] for b in result["breached"]))
    _assert(str(min(caps)) in result["detail"], result["detail"])


def test_every_scope_and_window_pair_is_evaluated():
    """'Which caps am I hitting' is the diagnostic that answers stalled reach."""
    _clear_impressions()
    _seed_impressions("subj-all", 1)
    conn = db.connect()
    try:
        result = frequency.check(conn, subject_ref="subj-all",
                                 creative_id="cr-1", campaign_id="camp-1",
                                 advertiser_user_id="adv-1")
    finally:
        conn.close()
    pairs = {(c["scope"], c["window"]) for c in result["checks"]}
    expected = {(s, w) for (s, w) in taxonomy.DEFAULT_FREQUENCY_CAPS}
    _assert(pairs == expected, f"missing: {expected - pairs}")


def test_a_broken_frequency_read_does_not_block_delivery():
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("down")

    result = frequency.check(_Broken(), subject_ref="s", campaign_id="c",
                             creative_id="cr", advertiser_user_id="a")
    _assert(result["capped"] is False,
            "a failed frequency read stopped delivery")


def test_a_missing_subject_counts_nothing():
    conn = db.connect()
    try:
        count = frequency.exposure_count(conn, scope="campaign",
                                         scope_ref="camp-1", subject_ref=None,
                                         window="day")
    finally:
        conn.close()
    _assert(count == 0)


def test_the_viewer_facing_explanation_does_not_name_the_advertiser():
    result = {"capped": True, "detail": "this viewer has seen this campaign "
                                        "9 times in the last day"}
    text = frequency.explain_cap(result)
    _assert("campaign" not in text.lower(), text)
    _assert("9" not in text, text)
    # The advertiser gets the specifics, because it is their campaign.
    _assert("9" in frequency.explain_for_advertiser(result))


# --------------------------------------------------------------------------- #
# Ad load
# --------------------------------------------------------------------------- #

def test_ad_load_is_not_purchasable():
    """No campaign, advertiser or price may reach the ceiling."""
    params = inspect.signature(frequency.ad_load_permits).parameters
    for banned in ("campaign_id", "advertiser_user_id", "price", "bid", "tier"):
        _assert(banned not in params,
                f"ad_load_permits takes {banned} — the ceiling would belong to "
                f"whoever paid most")


def test_the_session_ceiling_holds():
    at_limit = frequency.ad_load_permits(
        ads_this_session=taxonomy.MAX_ADS_PER_SESSION,
        items_since_last_ad=100)
    _assert(at_limit["permitted"] is False, at_limit)
    _assert(at_limit["reason"] == "SESSION_AD_LIMIT")


def test_consecutive_ads_are_blocked():
    result = frequency.ad_load_permits(
        ads_this_session=1, items_since_last_ad=100,
        consecutive_ads=taxonomy.MAX_CONSECUTIVE_ADS)
    _assert(result["permitted"] is False, result)
    _assert(result["reason"] == "CONSECUTIVE_ADS")


def test_ads_must_be_spaced_by_organic_items():
    tight = frequency.ad_load_permits(ads_this_session=1, items_since_last_ad=1)
    spaced = frequency.ad_load_permits(
        ads_this_session=1,
        items_since_last_ad=taxonomy.MIN_ORGANIC_ITEMS_BETWEEN_ADS)
    _assert(tight["permitted"] is False, tight)
    _assert(tight["reason"] == "AD_SPACING")
    _assert(spaced["permitted"] is True, spaced)


def test_the_first_ad_of_a_session_is_not_blocked_by_spacing():
    """There is nothing to space away from yet."""
    result = frequency.ad_load_permits(ads_this_session=0, items_since_last_ad=0)
    _assert(result["permitted"] is True, result)


def test_frequency_caps_alone_cannot_substitute_for_ad_load():
    """Twelve advertisers each within cap still make a feed a quarter ads."""
    per_advertiser_cap = taxonomy.DEFAULT_FREQUENCY_CAPS[("advertiser", "session")]
    _assert(taxonomy.MAX_ADS_PER_SESSION < per_advertiser_cap * 12,
            "the session ceiling is so high that frequency caps bound it "
            "first, which means ad load is not actually protecting the feed")


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
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
