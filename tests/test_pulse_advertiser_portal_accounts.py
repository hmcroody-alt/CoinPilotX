"""`list_accounts` is the advertiser's list of their own businesses, with money on it.

It feeds two things: the accounts list itself, and `portal_summary`, which sums
`total_spend_cents` across the rows into `metrics.total_spend` — the headline
lifetime-spend figure on the portal. It also feeds `_account_health`, which reads
`pending_reviews`.

The query used to LEFT JOIN campaigns *and* creatives onto one account row. Two
sibling joins off one parent is a cartesian product: 4 campaigns x 9 creatives is
36 rows. `campaign_count` was `COUNT(DISTINCT c.id)` and survived. The other three
aggregates were bare `SUM(CASE ...)` / `SUM(c.spent_cents)` and counted every fact
once per row of the other table.

The one that matters is `total_spend_cents`. An advertiser with nine creatives was
shown nine times the money they had actually spent, on the accounts list and in
the portal rollup at once.

These tests use sqlite directly rather than the app's connection helper, because
the defect is in the SQL and nothing else here needs to be running to prove it.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import pulse_advertiser_portal  # noqa: E402


SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY, business_name TEXT, owner_user_id INTEGER,
    status TEXT, verification_status TEXT
);
CREATE TABLE pulse_ad_account_profiles (
    account_id INTEGER PRIMARY KEY, industry TEXT, website TEXT, contact_email TEXT
);
CREATE TABLE pulse_ad_campaigns (
    id INTEGER PRIMARY KEY, ad_account_id INTEGER, campaign_name TEXT,
    status TEXT, spent_cents INTEGER
);
CREATE TABLE pulse_ad_creatives (
    id INTEGER PRIMARY KEY, ad_account_id INTEGER, campaign_id INTEGER,
    moderation_status TEXT
);
CREATE TABLE pulse_ad_team_members (
    id INTEGER PRIMARY KEY, account_id INTEGER, user_id INTEGER,
    role TEXT, status TEXT
);
"""


def _conn(campaigns=(("active", 1000), ("active", 2000), ("paused", 500)), creatives=("approved",)):
    """One owner, one account, and enough campaigns and creatives to fan the join out.

    Defaults: three campaigns, two of them active, $35.00 of spend between them.
    `creatives` is a tuple of moderation statuses — its *length* is the multiplier
    the old query applied to campaign-derived figures.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO pulse_ad_accounts VALUES (1, 'Roody Goods', 99, 'active', 'verified')")
    conn.execute("INSERT INTO pulse_ad_account_profiles VALUES (1, 'Retail', 'https://x.test', 'a@x.test')")
    for i, (status, spent) in enumerate(campaigns):
        conn.execute(
            "INSERT INTO pulse_ad_campaigns VALUES (?, 1, ?, ?, ?)",
            (i + 1, f"Campaign {i + 1}", status, spent),
        )
    for i, moderation in enumerate(creatives):
        conn.execute(
            "INSERT INTO pulse_ad_creatives VALUES (?, 1, 1, ?)", (i + 1, moderation)
        )
    return conn


def test_spend_is_not_multiplied_by_the_creative_count():
    conn = _conn(creatives=("approved",) * 9)
    account = pulse_advertiser_portal.list_accounts(conn, 99)[0]
    # Three campaigns spending 1000 + 2000 + 500. Before the fix this was 31500 —
    # 3500 x 9 creatives — and rendered as "$315.00".
    assert account["total_spend_cents"] == 3500
    assert account["total_spend"] == "$35.00"


def test_reported_spend_is_independent_of_how_many_creatives_exist():
    """The invariant that makes the old figure self-evidently wrong.

    Money spent is a property of the campaigns. Uploading a second image to an
    account cannot change what that account has been charged, so if the number
    moves when only the creative count moves, the number is arithmetic rather
    than accounting.
    """
    baseline = None
    for count in (0, 1, 2, 5, 17):
        conn = _conn(creatives=("approved",) * count)
        account = pulse_advertiser_portal.list_accounts(conn, 99)[0]
        if baseline is None:
            baseline = account["total_spend_cents"]
        assert account["total_spend_cents"] == baseline == 3500, count
        assert account["campaign_count"] == 3, count
        assert account["active_campaigns"] == 2, count


def test_pending_reviews_are_not_multiplied_by_the_campaign_count():
    conn = _conn(creatives=("pending", "pending", "approved", "rejected"))
    account = pulse_advertiser_portal.list_accounts(conn, 99)[0]
    # Before the fix: 2 pending x 3 campaigns = 6.
    assert account["pending_reviews"] == 2


def test_pending_reviews_are_independent_of_how_many_campaigns_exist():
    for campaigns in (
        (("active", 100),),
        (("active", 100), ("paused", 0)),
        (("draft", 0), ("draft", 0), ("active", 100), ("paused", 0), ("completed", 0)),
    ):
        conn = _conn(campaigns=campaigns, creatives=("pending", "approved", "pending"))
        account = pulse_advertiser_portal.list_accounts(conn, 99)[0]
        assert account["pending_reviews"] == 2, len(campaigns)


def test_health_score_reflects_a_real_review_queue():
    """`_account_health` awards 5 points for an empty review queue.

    The inflated `pending_reviews` could never be zero when it was non-zero, so
    this particular bug did not invent a clean queue — but it is read by the
    same function, so the two are pinned together here rather than separately.
    """
    clean = pulse_advertiser_portal.list_accounts(_conn(creatives=("approved",) * 9), 99)[0]
    dirty = pulse_advertiser_portal.list_accounts(_conn(creatives=("pending",)), 99)[0]
    assert clean["pending_reviews"] == 0
    assert dirty["pending_reviews"] == 1
    assert clean["health_score"] == dirty["health_score"] + 5


def test_an_account_with_nothing_on_it_reports_real_zeroes():
    conn = _conn(campaigns=(), creatives=())
    account = pulse_advertiser_portal.list_accounts(conn, 99)[0]
    assert account["campaign_count"] == 0
    assert account["active_campaigns"] == 0
    assert account["pending_reviews"] == 0
    # A confirmed real zero, not an absent figure. §31.
    assert account["total_spend_cents"] == 0
    assert account["total_spend"] == "$0.00"


def test_the_profile_join_still_delivers_its_columns():
    """The profile LEFT JOIN was kept, so prove it still joins.

    `pulse_ad_account_profiles.account_id` is a PRIMARY KEY, so it is 1:1 and
    cannot fan out — which is why it did not need replacing with a subquery.
    """
    conn = _conn()
    account = pulse_advertiser_portal.list_accounts(conn, 99)[0]
    assert account["industry"] == "Retail"
    assert account["website"] == "https://x.test"
    assert account["contact_email"] == "a@x.test"
    assert account["business_name"] == "Roody Goods"
    assert account["role"] == "owner"


def test_an_account_with_no_profile_row_still_appears():
    conn = _conn()
    conn.execute("DELETE FROM pulse_ad_account_profiles")
    account = pulse_advertiser_portal.list_accounts(conn, 99)[0]
    assert account["industry"] is None
    assert account["total_spend_cents"] == 3500


def test_accounts_are_scoped_to_the_user():
    conn = _conn()
    assert pulse_advertiser_portal.list_accounts(conn, 99)
    assert pulse_advertiser_portal.list_accounts(conn, 1234) == []


def test_spend_is_summed_per_account_and_not_across_them():
    """Two accounts, so the subqueries have to stay correlated.

    A subquery that dropped its `WHERE ... = a.id` correlation would compile and
    return the same number on every row, which is the failure mode that replaces
    the one being fixed.
    """
    conn = _conn()
    conn.execute("INSERT INTO pulse_ad_accounts VALUES (2, 'Second Shop', 99, 'active', 'verified')")
    conn.execute("INSERT INTO pulse_ad_campaigns VALUES (90, 2, 'Other', 'active', 700)")
    conn.execute("INSERT INTO pulse_ad_creatives VALUES (90, 2, 90, 'pending')")
    by_id = {row["id"]: row for row in pulse_advertiser_portal.list_accounts(conn, 99)}
    assert by_id[1]["total_spend_cents"] == 3500
    assert by_id[2]["total_spend_cents"] == 700
    assert by_id[1]["pending_reviews"] == 0
    assert by_id[2]["pending_reviews"] == 1
    assert by_id[1]["campaign_count"] == 3
    assert by_id[2]["campaign_count"] == 1
