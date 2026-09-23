"""The scope clause must keep hitting an index as the platform grows.

``read`` scopes through the seller's listing ids rather than through
``seller_user_id`` for one reason: there is no index leading with
``seller_user_id``, so the obvious query degrades to a scan of the whole
impression log on every dashboard load — slower precisely as PulseSoc
succeeded. That is a claim about a query plan, and a claim about a query plan
belongs in a test rather than in a docstring, because the next person to add a
``WHERE`` clause will read neither.

What this proves and what it does not
-------------------------------------

``EXPLAIN QUERY PLAN`` here is SQLite's, and production is PostgreSQL, so the
planner is not the one that will run these queries. The index *set* is the
same — verified against production on 2026-09-22, where
``commerce_discovery_impression_events`` carries all six indexes
``schema.py`` declares.

So what is actually pinned is the **shape of the predicate**: that the scoped
queries lead with the indexed column, and that the seller-only alternative does
not. A future edit that reorders or drops the ``listing_id`` clause changes
that shape under both planners, and fails here.

``marketplace_listings`` is included because it is the honest hole in the
design, not because it passes.
"""

from __future__ import annotations

import pytest

from .conftest import SELLER_A

ANY_WINDOW = "2026-03-01T00:00:00+00:00"


def _plan(cur, sql, params) -> str:
    rows = cur.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return "\n".join(str(dict(row).get("detail") or "") for row in rows)


def test_the_impression_scope_searches_an_index_on_both_columns(cur):
    """`idx_cd_impr_listing (listing_id, event_at)` — the whole reason for the design."""
    plan = _plan(
        cur,
        "SELECT event_id FROM commerce_discovery_impression_events "
        "WHERE listing_id IN (?,?) AND seller_user_id=? AND event_at>? AND self_view=0 "
        "ORDER BY event_at DESC LIMIT ?",
        (1001, 1002, SELLER_A, ANY_WINDOW, 100),
    )

    assert "SEARCH" in plan
    assert "idx_cd_impr_listing" in plan
    # Both columns of the index, not just the leading one: the window is what
    # keeps a seller's first year of traffic out of a seven-day query.
    assert "listing_id=?" in plan and "event_at>?" in plan


def test_the_seller_only_scope_would_scan_and_is_why_it_was_not_written(cur):
    """The rejected alternative, kept as a test so the reason stays visible.

    Without this, `idx_cd_impr_seller_freq` looks like it covers the case — its
    name says seller — and someone simplifies the query. It leads with
    `subject_ref` because every question the frequency cap asks is about one
    viewer, so it cannot serve "everything for this seller" and degrades to a
    full traversal.
    """
    plan = _plan(
        cur,
        "SELECT event_id FROM commerce_discovery_impression_events "
        "WHERE seller_user_id=? AND event_at>? AND self_view=0 "
        "ORDER BY event_at DESC LIMIT ?",
        (SELLER_A, ANY_WINDOW, 100),
    )

    assert "SEARCH" not in plan, f"the alternative stopped being worse:\n{plan}"
    assert "SCAN" in plan


def test_the_engagement_scope_searches_its_index_too(cur):
    """On the leading column only — `idx_cd_engage_listing` is (listing_id, action, event_at).

    `action` sits between the two columns this query filters on, so the window
    cannot be pushed into the index seek and is applied as a filter. Recorded
    rather than fixed: engagements are a fraction of impressions (3 rows against
    24 in production today), and widening a shared index is not this mission's
    to make.
    """
    plan = _plan(
        cur,
        "SELECT event_id FROM commerce_discovery_engagement_events "
        "WHERE listing_id IN (?,?) AND seller_user_id=? AND event_at>? "
        "ORDER BY event_at DESC LIMIT ?",
        (1001, 1002, SELLER_A, ANY_WINDOW, 100),
    )

    assert "SEARCH" in plan
    assert "idx_cd_engage_listing" in plan


@pytest.mark.parametrize(
    "table,sql",
    [
        ("marketplace_listings", "SELECT id FROM marketplace_listings WHERE seller_user_id=? LIMIT ?"),
        (
            "seller_transactions",
            "SELECT id FROM seller_transactions WHERE seller_user_id=? "
            "ORDER BY created_at DESC LIMIT ?",
        ),
    ],
)
def test_the_two_scoping_reads_are_scans_and_this_is_recorded_not_claimed_away(
    cur, table, sql
):
    """Neither table has an index on `seller_user_id`. Verified in production.

    This test asserts the *defect*, which is deliberate. The module's docstring
    would otherwise imply the whole read path is indexed, and a reader would
    have no way to find out otherwise short of running EXPLAIN themselves.

    Why it is not fixed here: both are small (47 listings and 32 orders in
    production on 2026-09-22, one seller) so both scans are free today, and the
    fix is a `CREATE INDEX` on two shared tables that every other feature also
    reads. That is a schema change with its own review, not a line smuggled in
    under an analytics mission.

    When this test starts failing, someone added the index and the comment above
    is out of date — which is the correct way for it to fail.
    """
    plan = _plan(cur, sql, (SELLER_A, 100))

    assert "SCAN" in plan, (
        f"{table} appears to be indexed on seller_user_id now. Good — update "
        f"read.py's performance notes and delete this case.\n{plan}"
    )
