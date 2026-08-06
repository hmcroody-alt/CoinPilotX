"""
`advertiser_analytics` is the only analytics source the advertising product has.

Both callers use it: the legacy route at bot.py:17456 and `portal_summary` at
services/pulse_advertiser_portal.py:443. Whatever it says is what an advertiser
reads on the web dashboard and in the mobile app, so an aggregate that is wrong
here is wrong everywhere at once, with no second opinion to contradict it.

The query LEFT JOINs impressions, clicks and events onto one campaign, which
produces a cartesian product: 10 impressions x 5 clicks x 3 events is 150 rows.
Every aggregate in the SELECT is written as COUNT(DISTINCT ...) and survives
that — except `viewable_impressions`, which was a bare
`SUM(CASE WHEN i.viewable=1 THEN 1 ELSE 0 END)` and counted the duplicates. It
reported 90 viewable impressions out of 10, so the viewability rate an
advertiser optimised against read 900%.

These tests use sqlite directly rather than the app's connection helper, because
the defect is in the SQL and nothing else here needs to be running to prove it.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import pulse_ads_service  # noqa: E402


SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY, business_name TEXT, owner_user_id INTEGER
);
CREATE TABLE pulse_ad_campaigns (
    id INTEGER PRIMARY KEY, ad_account_id INTEGER, campaign_name TEXT,
    status TEXT, spent_cents INTEGER
);
CREATE TABLE pulse_ad_impressions (
    id INTEGER PRIMARY KEY, campaign_id INTEGER, viewable INTEGER
);
CREATE TABLE pulse_ad_clicks (id INTEGER PRIMARY KEY, campaign_id INTEGER);
CREATE TABLE pulse_ad_events (
    id INTEGER PRIMARY KEY, campaign_id INTEGER, event_type TEXT
);
"""


def _conn(impressions=10, viewable=6, clicks=5, events=("hide", "hide", "conversion")):
    """One owner, one campaign, and enough of each child row to fan the join out."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO pulse_ad_accounts VALUES (1, 'Roody Goods', 99)")
    conn.execute("INSERT INTO pulse_ad_campaigns VALUES (5, 1, 'Launch', 'active', 4000)")
    for i in range(impressions):
        conn.execute(
            "INSERT INTO pulse_ad_impressions VALUES (?, 5, ?)", (i + 1, 1 if i < viewable else 0)
        )
    for i in range(clicks):
        conn.execute("INSERT INTO pulse_ad_clicks VALUES (?, 5)", (i + 1,))
    for i, kind in enumerate(events):
        conn.execute("INSERT INTO pulse_ad_events VALUES (?, 5, ?)", (i + 1, kind))
    return conn


def test_viewable_impressions_are_not_multiplied_by_the_join():
    conn = _conn()
    row = pulse_ads_service.advertiser_analytics(conn, 99)["campaigns"][0]
    assert row["impressions"] == 10
    assert row["clicks"] == 5
    # The number under test. Before the fix this was 90 — 6 x (5 clicks x 3 events).
    assert row["viewable_impressions"] == 6


def test_viewable_impressions_never_exceed_impressions():
    """The property that makes the old figure self-evidently impossible.

    Viewable impressions are a subset of impressions. Any result where the
    subset is larger than the set it came from is arithmetic, not data, and no
    amount of traffic can produce it honestly.
    """
    for clicks, events in ((0, ()), (1, ("hide",)), (5, ("hide", "report", "conversion")), (20, ("hide",) * 7)):
        conn = _conn(clicks=clicks, events=events)
        row = pulse_ads_service.advertiser_analytics(conn, 99)["campaigns"][0]
        assert row["viewable_impressions"] <= row["impressions"], (clicks, len(events))
        assert row["viewable_impressions"] == 6


def test_totals_agree_with_the_campaign_rows():
    conn = _conn()
    result = pulse_ads_service.advertiser_analytics(conn, 99)
    totals, campaigns = result["totals"], result["campaigns"]
    for key in ("impressions", "viewable_impressions", "clicks", "hides", "reports", "conversions"):
        assert totals[key] == sum(row[key] for row in campaigns), key


def test_conversions_are_counted_rather_than_written_and_forgotten():
    """The aggregate counts `conversion` rows correctly. That is all it does.

    Nothing read the column back, so an advertiser running a conversion
    objective saw clicks and spend and no evidence the objective was ever met.
    But adding the count is not the same as adding attribution, and the reason
    is worth writing down where the next reader will find it.

    The only code in the product that ever wrote `event_type='conversion'` was
    `SponsoredAdCard.flushViewability` in the native app, which fired it once an
    ad had been on screen for one second — on the line after the call that
    already records that same fact as `impressions.viewable = 1`. The web
    client never wrote one. So every `conversion` row in the table is a
    viewability duplicate filed under a word that means something else, and the
    internal command centre (services/dashboard_ads_command_center.py:386) has
    been reporting them as conversions.

    That write is removed. The count below is therefore honest about the table
    and honest about the world — zero, once the mislabelled history ages out —
    and the mobile client says so in words rather than rendering it as a
    metric. There is no order link and no value anywhere, so there is no
    attributed revenue to report and none to adjust after a refund (§37).
    """
    conn = _conn(events=("conversion", "conversion", "hide"))
    row = pulse_ads_service.advertiser_analytics(conn, 99)["campaigns"][0]
    assert row["conversions"] == 2
    assert row["hides"] == 1
    # No revenue field is invented alongside it. §37 forbids attributed revenue
    # left unadjusted after refunds; the only safe way to honour that with no
    # order link is to not claim revenue at all.
    assert "attributed_revenue_cents" not in row
    assert "revenue_cents" not in row


def test_a_campaign_with_no_traffic_reports_real_zeroes():
    conn = _conn(impressions=0, viewable=0, clicks=0, events=())
    row = pulse_ads_service.advertiser_analytics(conn, 99)["campaigns"][0]
    assert row["impressions"] == 0
    assert row["viewable_impressions"] == 0
    assert row["conversions"] == 0
    # A zero that reflects a campaign that genuinely ran nothing, not a failure.
    assert row["ctr"] == 0
    assert row["spend"] == "$40.00"


def test_analytics_are_owner_scoped_which_is_why_a_zero_here_is_ambiguous():
    """Documenting the constraint the client has to compensate for.

    The WHERE clause is `a.owner_user_id = ?`. A campaign manager with full
    write access gets an empty payload rather than a 403, so the totals read as
    a well-formed set of zeroes. That is indistinguishable from an account that
    has never run an ad, which is why the mobile client keys its wallet and
    analytics confidence on role rather than on the figures themselves.
    """
    conn = _conn()
    mine = pulse_ads_service.advertiser_analytics(conn, 99)
    theirs = pulse_ads_service.advertiser_analytics(conn, 1234)
    assert mine["campaigns"] and mine["totals"]["impressions"] == 10
    assert theirs["campaigns"] == []
    assert theirs["totals"]["impressions"] == 0
