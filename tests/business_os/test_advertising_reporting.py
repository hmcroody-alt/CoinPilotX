"""Advertising Stage 2 — authoritative advertiser reporting matrix.

Exercises ``services.business_os.advertising.reporting`` on immutable impression /
click / billing records. Proves the reporting contract (spec §2):

  * impressions / clicks / reach / frequency / CTR / spend are Confirmed and counted
    directly from the immutable logs + ledger-backed billing events;
  * video views / engagement are reported as ``available: false`` with a ``null``
    value — never 0 (there is no video-playback event source in the MVP);
  * a ratio with a 0 denominator (CTR with no impressions) is ``null``, not 0;
  * date-range, placement, and campaign filters scope every figure;
  * spend is attributed to the SOURCE event's activity time, so per-day spend lines
    up with per-day impressions/clicks;
  * placement + creative breakdowns paginate; a daily/hourly time-series buckets
    correctly; CSV export renders nulls as empty cells (never fabricated zeros).

    python tests/business_os/test_advertising_reporting.py   # no pytest needed
"""

import os
import tempfile
import uuid

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_adrep_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import schema as ad_schema  # noqa: E402
from services.business_os.advertising import pricing, billing, reporting  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


CID = "rep_camp"


def _fund(cid, cents):
    ledger.post_entry(
        idempotency_key="f_" + uuid.uuid4().hex, actor="test", amount_cents=cents,
        currency="usd", entry_type="escrow_fund", source="external:test",
        destination="ad_campaign_escrow:" + cid, reason="fund")


def _impr(cid, eid, subj, placement, cver, at):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_ad_impression_events "
            "(event_id, delivery_id, campaign_id, ad_set_id, creative_id, "
            "creative_version, placement, subject_ref, advertiser_user_id, event_at, "
            "dedup_key, fraud_status, billing_eligible, billing_processed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'clean', 1, 0, ?)",
            (eid, "d_" + eid, cid, "as1", "cr1", cver, placement, subj, "adv1", at,
             "dk_" + eid, at))
        conn.commit()
    finally:
        conn.close()


def _click(cid, eid, subj, placement, cver, at):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_ad_click_events "
            "(event_id, delivery_id, impression_event_id, campaign_id, ad_set_id, "
            "creative_id, creative_version, placement, subject_ref, advertiser_user_id, "
            "destination_type, destination_ref, event_at, dedup_key, fraud_status, "
            "billing_eligible, billing_processed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'url', 'https://x', ?, ?, 'clean', "
            "1, 0, ?)",
            (eid, "d_" + eid, "i_" + eid, cid, "as1", "cr1", cver, placement, subj,
             "adv1", at, "dk_" + eid, at))
        conn.commit()
    finally:
        conn.close()


def setup_module():
    ad_schema.ensure_schema()
    ledger.ensure_schema()
    pricing.publish_policy("cpm", "usd", 500, actor="admin")
    pricing.publish_policy("cpc", "usd", 25, actor="admin")
    _fund(CID, 100000)
    # 4 impressions (3 distinct viewers), across feed/reels and creative v1/v2.
    _impr(CID, "i1", "u1", "feed", 1, "2026-07-20T10:00:00")
    _impr(CID, "i2", "u1", "feed", 1, "2026-07-20T11:00:00")
    _impr(CID, "i3", "u2", "feed", 2, "2026-07-21T10:00:00")
    _impr(CID, "i4", "u3", "reels", 1, "2026-07-21T12:00:00")
    _click(CID, "k1", "u1", "feed", 1, "2026-07-20T10:05:00")
    _click(CID, "k2", "u2", "feed", 2, "2026-07-21T10:05:00")
    billing.process_pending(campaign_id=CID)


def test_summary_core_metrics_confirmed():
    r = reporting.campaign_report(CID)
    m = r["metrics"]
    assert m["impressions"] == 4, m
    assert m["clicks"] == 2, m
    assert m["reach"] == 3, m                      # u1, u2, u3
    assert abs(m["frequency"] - round(4 / 3, 4)) < 1e-9, m
    assert m["ctr"] == 0.5, m
    # 4 impr * 500 milli-cents = 2c CPM + 2 clicks * 25c = 50c CPC -> 52c.
    assert m["spend_cents"] == 52, m
    assert m["cost_per_click_cents"] == 26, m
    assert r["metric_meta"]["impressions"]["confidence"] == "Confirmed", r


def test_video_is_unavailable_not_zero():
    r = reporting.campaign_report(CID)
    assert r["metrics"]["video_views"] is None, r
    assert r["metric_meta"]["video_views"]["available"] is False, r
    assert r["metric_meta"]["video_views"]["confidence"] is None, r


def test_ratio_null_when_denominator_zero():
    r = reporting.campaign_report("no_such_campaign")
    assert r["metrics"]["impressions"] == 0, r
    assert r["metrics"]["ctr"] is None, r          # not 0
    assert r["metrics"]["frequency"] is None, r


def test_placement_filter_scopes_metrics():
    r = reporting.campaign_report(CID, placement="reels")
    assert r["metrics"]["impressions"] == 1, r
    assert r["metrics"]["clicks"] == 0, r


def test_date_filter_scopes_metrics():
    r = reporting.campaign_report(CID, start="2026-07-21", end="2026-07-22")
    assert r["metrics"]["impressions"] == 2, r


def test_placement_breakdown_paginates():
    pb = reporting.placement_breakdown(CID)
    assert pb["total"] == 2, pb
    by = {row["placement"]: row for row in pb["rows"]}
    assert by["feed"]["impressions"] == 3, pb
    assert by["reels"]["impressions"] == 1, pb
    # pagination
    p1 = reporting.placement_breakdown(CID, limit=1, offset=0)
    p2 = reporting.placement_breakdown(CID, limit=1, offset=1)
    assert len(p1["rows"]) == 1 and len(p2["rows"]) == 1, (p1, p2)
    assert p1["rows"][0]["placement"] != p2["rows"][0]["placement"]


def test_creative_breakdown():
    cb = reporting.creative_breakdown(CID)
    assert cb["total"] == 2, cb


def test_time_series_daily_buckets_and_spend_alignment():
    ts = reporting.time_series(CID, granularity="day")
    assert len(ts["series"]) == 2, ts
    b = {row["bucket"]: row for row in ts["series"]}
    assert b["2026-07-20"]["impressions"] == 2, ts
    assert b["2026-07-21"]["impressions"] == 2, ts
    # spend attributed to source activity day: 26c each day (1c CPM flush + 25c CPC).
    assert b["2026-07-20"]["spend_cents"] == 26, ts
    assert b["2026-07-21"]["spend_cents"] == 26, ts


def test_csv_export_renders_nulls_as_empty():
    pb = reporting.placement_breakdown(CID)
    text = reporting.to_csv(pb["rows"])
    assert text.startswith("placement,"), text
    # video_views is None -> empty cell, never a fabricated 0
    lines = text.strip().splitlines()
    assert len(lines) == 3, text  # header + 2 placements


def _run_standalone():
    setup_module()
    tests = [
        test_summary_core_metrics_confirmed,
        test_video_is_unavailable_not_zero,
        test_ratio_null_when_denominator_zero,
        test_placement_filter_scopes_metrics,
        test_date_filter_scopes_metrics,
        test_placement_breakdown_paginates,
        test_creative_breakdown,
        test_time_series_daily_buckets_and_spend_alignment,
        test_csv_export_renders_nulls_as_empty,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
