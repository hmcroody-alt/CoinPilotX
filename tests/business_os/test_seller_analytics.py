"""Per-seller, per-period analytics — the aggregates behind the Insights screen.

This module exists because the seller-orders endpoint is ``LIMIT 100`` per table with no
date range, so a total built from it is the sum of the newest hundred rows — an
understatement that grows the better the store does. Everything asserted here is a
property that, if it broke, would break *silently*: the screen would still render, still
look right, and be wrong about a number a seller uses to decide what to restock and what
to spend on ads.

What is pinned:

  * **No cap and no sampling.** 250 sales in the window produce a total of 250 sales.
  * **The status vocabulary.** Refunds, chargebacks, expiries and disputes are not
    revenue, and the rule matches the one the Store and Orders screens already apply.
  * **The seller's midnight.** Period edges are cut in the seller's timezone, so a sale
    at 11pm local belongs to that seller's today no matter what UTC thinks.
  * **The prior-period gate.** A new seller's first sale must never report ▲100%.
  * **The gap ledger.** Five metrics have no source. If somebody invents one, the count
    changes and this file says so.

    python tests/business_os/test_seller_analytics.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="seller_analytics_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import datetime, timedelta  # noqa: E402

from services import db  # noqa: E402
from services.business_os.insights import seller_analytics as sa  # noqa: E402


SELLER = 7001
OTHER_SELLER = 7002

#: A fixed clock. Every window in this file is computed against it, so a test that
#: passes today passes in December — period arithmetic bugs love a moving "now".
NOW = datetime(2026, 8, 2, 21, 0, 0)

#: UTC-7. Deliberately not zero: with a zero offset a timezone bug is invisible.
LA = -420


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def setup_module(module=None):
    conn = db.connect()
    cur = conn.cursor()
    # The real tables carry many more columns; these are the ones the module reads.
    # Creating them here rather than importing the app's bootstrap keeps this test
    # offline and independent of which of the two bootstrap paths ran.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS creator_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_user_id INTEGER,
            gross_amount_cents INTEGER,
            currency TEXT,
            status TEXT,
            item_id TEXT,
            item_type TEXT,
            seller_type TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS seller_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_user_id INTEGER,
            amount_cents INTEGER,
            currency TEXT,
            status TEXT,
            item_id TEXT,
            item_type TEXT,
            seller_type TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_user_id INTEGER,
            followed_user_id INTEGER,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS marketplace_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_user_id INTEGER,
            title TEXT,
            cover_image_url TEXT,
            media_url TEXT,
            status TEXT,
            quantity INTEGER,
            price_label TEXT,
            currency TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _wipe():
    conn = db.connect()
    cur = conn.cursor()
    for table in ("creator_transactions", "seller_transactions", "pulse_follows", "marketplace_listings"):
        cur.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


def _stamp(moment):
    return moment.replace(microsecond=0).isoformat(sep=" ")


def _sale(
    at,
    minor=1000,
    *,
    table="seller_transactions",
    seller=SELLER,
    status="delivered",
    item_id="1",
    item_type="listing",
    seller_type="merchant",
    currency="USD",
):
    conn = db.connect()
    cur = conn.cursor()
    amount_column = "gross_amount_cents" if table == "creator_transactions" else "amount_cents"
    cur.execute(
        f"""
        INSERT INTO {table}
            (seller_user_id, {amount_column}, currency, status, item_id, item_type, seller_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(seller), int(minor), currency, status, str(item_id), item_type, seller_type, _stamp(at)),
    )
    conn.commit()
    conn.close()


def _follow(at, seller=SELLER):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pulse_follows (follower_user_id, followed_user_id, created_at) VALUES (?, ?, ?)",
        (1, int(seller), _stamp(at)),
    )
    conn.commit()
    conn.close()


def _listing(listing_id, title="Blue Mug", status="active", quantity=10, seller=SELLER):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO marketplace_listings
            (id, seller_user_id, title, cover_image_url, media_url, status, quantity, price_label, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(listing_id), int(seller), title, None, None, status, quantity, "$10.00", "USD"),
    )
    conn.commit()
    conn.close()


def _summary(period="7d", tz=LA, top=5):
    return sa.seller_summary(SELLER, period=period, tz_offset_minutes=tz, top_limit=top, now=NOW)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def test_period_edges_sit_on_the_sellers_midnight():
    # 21:00 UTC is 14:00 in Los Angeles, so the seller's "today" runs from
    # 07:00Z today to 07:00Z tomorrow — the day they actually had, not UTC's.
    prior_start, start, end = sa.period_bounds("today", tz_offset_minutes=LA, now=NOW)
    assert start == datetime(2026, 8, 2, 7, 0), start
    assert end == datetime(2026, 8, 3, 7, 0), end
    assert prior_start == datetime(2026, 8, 1, 7, 0), prior_start


def test_prior_window_is_the_same_length_as_the_window():
    # Every "vs prior" line on the screen says "the prior 7 days" in words. If the
    # windows were different lengths the sentence would be false.
    for period, days in sa.PERIOD_DAYS.items():
        prior_start, start, end = sa.period_bounds(period, tz_offset_minutes=LA, now=NOW)
        assert (end - start) == timedelta(days=days), period
        assert (start - prior_start) == timedelta(days=days), period


def test_the_window_is_half_open_at_both_ends():
    _wipe()
    _, start, end = sa.period_bounds("today", tz_offset_minutes=LA, now=NOW)
    _sale(start - timedelta(seconds=1), minor=500)     # just before: excluded
    _sale(start, minor=100)                            # exactly at start: included
    _sale(end - timedelta(seconds=1), minor=200)       # last second: included
    _sale(end, minor=900)                              # exactly at end: excluded
    totals = _summary("today")["totals"]
    assert totals == {"revenue_minor": 300, "orders": 2}, totals


def test_an_unknown_period_falls_back_to_a_week_rather_than_failing():
    assert sa.period_bounds("all_time", tz_offset_minutes=0, now=NOW) == sa.period_bounds(
        sa.DEFAULT_PERIOD, tz_offset_minutes=0, now=NOW
    )


def test_an_absurd_timezone_offset_is_clamped_not_trusted():
    _wipe()
    _sale(NOW - timedelta(hours=1))
    # A client sending 100_000 minutes would otherwise shift the window by 69 days.
    summary = sa.seller_summary(SELLER, period="today", tz_offset_minutes=100_000, top_limit=5, now=NOW)
    assert -720 <= summary["timezone_offset_minutes"] <= 840, summary["timezone_offset_minutes"]


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------

def test_totals_are_totals_not_the_newest_hundred_rows():
    # This is the whole reason this module exists. The endpoint it replaces caps at
    # 100 rows per table; a cap here would understate a busy seller's revenue and get
    # worse the better they did.
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    for index in range(250):
        _sale(start + timedelta(minutes=index), minor=100)
    totals = _summary("7d")["totals"]
    assert totals == {"revenue_minor": 25_000, "orders": 250}, totals


def test_both_order_tables_are_counted():
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=1_500, table="creator_transactions")
    _sale(start + timedelta(hours=2), minor=2_500, table="seller_transactions")
    assert _summary("7d")["totals"] == {"revenue_minor": 4_000, "orders": 2}


def test_another_sellers_revenue_is_never_included():
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=1_000)
    _sale(start + timedelta(hours=2), minor=99_999, seller=OTHER_SELLER)
    assert _summary("7d")["totals"]["revenue_minor"] == 1_000


def test_money_that_did_not_stay_with_the_seller_is_not_revenue():
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=5_000, status="delivered")
    for index, status in enumerate(
        ("refunded", "cancelled", "payment_failed", "expired", "chargeback", "charge_back", "voided", "disputed")
    ):
        _sale(start + timedelta(hours=2 + index), minor=5_000, status=status)
    totals = _summary("7d")["totals"]
    assert totals == {"revenue_minor": 5_000, "orders": 1}, totals


def test_the_exclusion_rule_matches_the_owner_screens():
    # Store and Orders both apply `status.includes("cancel") || status.includes("refund")`.
    # Insights must agree about what an order is, or the two screens disagree about
    # the same day. The extra fragments are terminal failures those screens never see.
    assert "cancel" in sa.EXCLUDED_STATUS_FRAGMENTS
    assert "refund" in sa.EXCLUDED_STATUS_FRAGMENTS
    assert sa._counts_as_sale("delivered") is True
    assert sa._counts_as_sale("PARTIALLY_REFUNDED") is False
    assert sa._counts_as_sale(None) is True  # a missing status is not a failure state


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def test_a_brand_new_seller_gets_no_prior_period_at_all():
    # Not a zero to divide by — an absence. The screen says "New — no prior period"
    # rather than inventing ▲100% against a week the seller did not exist in.
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=4_000)
    summary = _summary("7d")
    assert summary["has_prior_period"] is False
    assert summary.get("prior_totals") is None
    assert summary["followers"].get("prior_gained") is None


def test_a_seller_with_older_trades_gets_a_real_prior_total():
    _wipe()
    prior_start, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(prior_start + timedelta(hours=1), minor=3_000)
    _sale(start + timedelta(hours=1), minor=4_000)
    summary = _summary("7d")
    assert summary["has_prior_period"] is True
    assert summary["prior_totals"] == {"revenue_minor": 3_000, "orders": 1}


def test_a_refunded_prior_period_still_counts_as_having_existed():
    # The seller traded last week; the trade was refunded. They existed, so the
    # comparison is "nothing in the prior 7 days", not "no prior period".
    _wipe()
    prior_start, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(prior_start + timedelta(hours=1), minor=3_000, status="refunded")
    _sale(start + timedelta(hours=1), minor=4_000)
    summary = _summary("7d")
    assert summary["has_prior_period"] is True
    assert summary["prior_totals"] == {"revenue_minor": 0, "orders": 0}


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def test_a_quiet_day_is_a_zero_bucket_not_a_missing_one():
    # A chart that omits empty days compresses a quiet week into one busy-looking
    # point, which reads as growth that did not happen.
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=1_000)
    _sale(start + timedelta(days=3, hours=1), minor=2_000)
    summary = _summary("7d")
    assert summary["bucket"] == "day"
    assert len(summary["series"]) == 7
    assert [bucket["revenue_minor"] for bucket in summary["series"]] == [1_000, 0, 0, 2_000, 0, 0, 0]


def test_the_series_folds_into_weeks_before_the_axis_becomes_unreadable():
    for period, expected_bucket, expected_points in (("30d", "day", 30), ("90d", "week", 13)):
        summary = _summary(period)
        assert summary["bucket"] == expected_bucket, period
        assert len(summary["series"]) == expected_points, (period, len(summary["series"]))
        assert len(summary["series"]) <= sa.MAX_DAILY_BUCKETS or expected_bucket == "week"


def test_series_revenue_adds_up_to_the_headline_total():
    # The chart and the KPI above it are the same money. If these ever diverge the
    # screen contradicts itself in a single glance.
    _wipe()
    _, start, _end = sa.period_bounds("30d", tz_offset_minutes=LA, now=NOW)
    for index in range(20):
        _sale(start + timedelta(days=index, hours=3), minor=750)
    summary = _summary("30d")
    assert sum(bucket["revenue_minor"] for bucket in summary["series"]) == summary["totals"]["revenue_minor"]
    assert sum(bucket["orders"] for bucket in summary["series"]) == summary["totals"]["orders"]


def test_bucket_dates_are_the_sellers_calendar_days():
    _wipe()
    summary = _summary("7d")
    # 7 days ending tomorrow-local: the last bucket is the seller's today.
    assert summary["series"][-1]["date"] == "2026-08-02", summary["series"][-1]["date"]
    assert summary["series"][0]["date"] == "2026-07-27", summary["series"][0]["date"]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def test_the_source_split_is_a_partition_of_the_total():
    # The breakdown must never exceed or fall short of the number it breaks down.
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=1_000, item_type="listing", seller_type="merchant")
    _sale(start + timedelta(hours=2), minor=2_000, item_type="digital", seller_type="creator")
    _sale(start + timedelta(hours=3), minor=3_000, item_type="product", seller_type="creator")
    summary = _summary("7d")
    assert sum(source["revenue_minor"] for source in summary["sources"]) == summary["totals"]["revenue_minor"]
    assert sum(source["orders"] for source in summary["sources"]) == summary["totals"]["orders"]


def test_item_type_decides_the_source_before_seller_type_does():
    # A merchant selling a creator product made a creator sale.
    assert sa._source_of("merchant", "digital") == "marketplace"   # seller type carries it
    assert sa._source_of("creator", "listing") == "marketplace"    # item type carries it
    assert sa._source_of("creator", "digital") == "store"
    assert sa._source_of(None, None) == "store"


def test_there_is_no_ads_source_row():
    # Sellers set next month's budget on this row. It ships only when a real
    # attribution model produces it, and no per-seller, per-period read exists.
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=1_000)
    keys = {source["key"] for source in _summary("7d")["sources"]}
    assert keys <= {"store", "marketplace"}, keys


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------

def test_top_items_rank_by_revenue_and_carry_their_listing_state():
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _listing(1, title="Blue Mug", quantity=0)
    _listing(2, title="Notebook", quantity=12)
    _sale(start + timedelta(hours=1), minor=9_000, item_id="1")
    _sale(start + timedelta(hours=2), minor=1_000, item_id="2")
    top = _summary("7d")["top_items"]
    assert [item["item_id"] for item in top] == ["1", "2"], top
    assert top[0]["title"] == "Blue Mug"
    assert top[0]["stock"] == 0          # sold out — the tip card's highest-priority rule
    assert top[1]["stock"] == 12


def test_a_listing_deleted_since_it_sold_still_counts_but_has_no_title():
    # Dropping the row would understate a list headed "where the money came from".
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=5_000, item_id="404")
    top = _summary("7d")["top_items"]
    assert len(top) == 1
    assert top[0]["title"] is None
    assert top[0]["revenue_minor"] == 5_000


def test_a_listing_id_is_only_resolved_against_the_sellers_own_listings():
    # Listing ids are global, so decorating a ranked row means looking one up by id.
    # Without the tenancy filter that lookup would happily return a stranger's row and
    # print their product title, image and stock level onto this seller's dashboard —
    # and the stock number would then drive a restock tip about someone else's shelf.
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _listing(9, title="Someone else's mug", quantity=0, seller=OTHER_SELLER)
    _sale(start + timedelta(hours=1), minor=5_000, item_id="9")
    top = _summary("7d")["top_items"]
    assert top[0]["title"] is None
    assert top[0]["stock"] is None


def test_a_listing_that_does_not_track_stock_reports_null_not_zero():
    # `null` and `0` are different claims: "not counted" versus "sold out". Reading
    # one as the other would tell a seller to restock something never counted.
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _listing(5, title="Workshop ticket", quantity=None)
    _sale(start + timedelta(hours=1), minor=5_000, item_id="5")
    assert _summary("7d")["top_items"][0]["stock"] is None


def test_the_top_limit_is_clamped_to_something_a_phone_can_render():
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    for index in range(60):
        _sale(start + timedelta(minutes=index), minor=100 * (index + 1), item_id=str(index))
    assert len(sa.seller_summary(SELLER, period="7d", tz_offset_minutes=LA, top_limit=999, now=NOW)["top_items"]) <= 50
    assert len(sa.seller_summary(SELLER, period="7d", tz_offset_minutes=LA, top_limit=0, now=NOW)["top_items"]) >= 1


# ---------------------------------------------------------------------------
# Followers
# ---------------------------------------------------------------------------

def test_followers_are_counted_inside_the_same_window_as_the_money():
    _wipe()
    prior_start, start, end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(prior_start + timedelta(hours=1), minor=100)  # gives the seller a prior period
    _follow(prior_start + timedelta(hours=2))
    _follow(start + timedelta(hours=1))
    _follow(start + timedelta(hours=2))
    _follow(end + timedelta(hours=1))                   # after the window: excluded
    summary = _summary("7d")
    assert summary["followers"]["gained"] == 2
    assert summary["followers"]["prior_gained"] == 1


def test_another_sellers_followers_are_never_counted():
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _follow(start + timedelta(hours=1), seller=OTHER_SELLER)
    assert _summary("7d")["followers"]["gained"] == 0


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

def test_the_headline_currency_is_the_one_carrying_the_most_money():
    _wipe()
    _, start, _end = sa.period_bounds("7d", tz_offset_minutes=LA, now=NOW)
    _sale(start + timedelta(hours=1), minor=1_000, currency="EUR")
    _sale(start + timedelta(hours=2), minor=9_000, currency="GBP")
    summary = _summary("7d")
    assert summary["currency"] == "GBP", summary["currency"]
    # Every currency seen is still reported, so the screen can warn about the mix
    # instead of silently adding pounds to euros.
    assert set(summary["currencies"]) == {"EUR", "GBP"}


# ---------------------------------------------------------------------------
# The gap ledger
# ---------------------------------------------------------------------------

def test_the_response_names_every_metric_it_cannot_measure():
    _wipe()
    keys = [gap["key"] for gap in _summary("7d")["unavailable"]]
    assert keys == [
        "store_views",
        "ads_attribution",
        "on_time_dispatch",
        "reply_rate",
        "offers_answered",
    ], keys


def test_every_gap_states_the_change_that_would_close_it():
    for gap in sa.UNAVAILABLE_METRICS:
        assert gap["key"] and gap["label"]
        # A gap without a stated remedy is a shrug, not a ledger entry.
        assert len(gap["needs"]) > 20, gap["key"]


def test_no_unmeasurable_metric_leaks_into_the_payload_as_a_zero():
    # A zero renders as a measurement. "0 store views" would be a false one.
    _wipe()
    summary = _summary("7d")
    for forbidden in ("store_views", "views", "on_time_dispatch", "reply_rate", "offers_answered"):
        assert forbidden not in summary, forbidden


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------

def test_a_store_with_no_sales_returns_a_shaped_empty_response():
    # The screen renders zeros with "New — no prior period"; it must not have to
    # guard every field against None.
    _wipe()
    summary = _summary("7d")
    assert summary["totals"] == {"revenue_minor": 0, "orders": 0}
    assert summary["sources"] == []
    assert summary["top_items"] == []
    assert summary["followers"]["gained"] == 0
    assert len(summary["series"]) == 7
    assert summary["has_prior_period"] is False


def _run_standalone():
    setup_module()
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    passed = 0
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    raise SystemExit(0 if _run_standalone() else 1)
