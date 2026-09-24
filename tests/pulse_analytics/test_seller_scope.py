"""The read layer must not hand one seller another seller's rows.

Every test here is written so that it fails against a plausible wrong
implementation, not merely against a broken one. The wrong implementations these
defend against are all things a reasonable person would write:

- filtering on ``listing_id`` and forgetting ``seller_user_id``
- filtering on ``seller_user_id`` and forgetting ``listing_id``
- taking the seller id from the caller's request instead of the session
- selecting ``*`` and shipping ``subject_ref`` to the seller's browser
"""

from __future__ import annotations

import pytest

from services.pulse_analytics import read

from .conftest import (
    A_LISTINGS,
    B_LISTINGS,
    SELLER_A,
    SELLER_B,
    write_engagement,
    write_impression,
    write_order,
)


def test_a_seller_sees_only_their_own_impressions(cur):
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    write_impression(cur, listing_id=B_LISTINGS[0], seller_user_id=SELLER_B)

    rows = read.impressions(cur, SELLER_A)

    assert [row["listing_id"] for row in rows] == [A_LISTINGS[0]]


def test_a_seller_sees_only_their_own_engagements(cur):
    write_engagement(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A, action="click")
    write_engagement(cur, listing_id=B_LISTINGS[0], seller_user_id=SELLER_B, action="click")

    rows = read.engagements(cur, SELLER_B)

    assert [row["listing_id"] for row in rows] == [B_LISTINGS[0]]


def test_a_forged_seller_id_on_the_event_row_is_not_enough(cur):
    """Owning the listing is necessary; claiming the seller id is not sufficient.

    This is the row an attacker would need to write to read someone else's
    traffic: seller A's id stamped onto a listing A does not own. The listing
    scope is read from ``marketplace_listings``, so the row is out of scope
    regardless of what the event claims.
    """
    write_impression(cur, listing_id=B_LISTINGS[0], seller_user_id=SELLER_A)

    rows = read.impressions(cur, SELLER_A)

    assert rows == []


def test_owning_the_listing_is_not_enough_if_the_event_names_another_seller(cur):
    """And the converse: the listing scope alone does not authorise the row.

    A listing that changed hands leaves history stamped with the previous
    owner. The new owner owns the listing today and still must not be handed
    the previous owner's traffic.
    """
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_B)

    rows = read.impressions(cur, SELLER_A)

    assert rows == []


def test_subject_ref_is_never_returned(cur):
    """A stable viewer hash must not reach a seller-facing payload.

    It is not reversible, but it is stable, which is enough to count returning
    visitors and to join one person across every listing in a store.
    """
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    write_engagement(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A, action="click")

    rows = read.impressions(cur, SELLER_A) + read.engagements(cur, SELLER_A)

    assert rows, "fixture wrote no rows — the assertion below would be vacuous"
    for row in rows:
        assert "subject_ref" not in row
        assert "session_id" not in row


@pytest.mark.parametrize("bad", [0, -1, None, "", "7; DROP TABLE users", "abc", 1.5])
def test_an_unusable_seller_identity_is_refused_not_widened(cur, bad):
    """The failure mode to avoid is a falsy id silently meaning "no filter"."""
    with pytest.raises(read.SellerScopeError):
        read.impressions(cur, bad)


def test_seller_scope_error_is_raised_before_any_query(cur):
    """A rejected identity must not reach the database at all."""

    class _Spy:
        def __init__(self, inner):
            self.inner = inner
            self.executed = []

        def execute(self, sql, *args, **kwargs):
            self.executed.append(sql)
            return self.inner.execute(sql, *args, **kwargs)

        def fetchall(self):
            return self.inner.fetchall()

    spy = _Spy(cur)
    with pytest.raises(read.SellerScopeError):
        read.impressions(spy, 0)
    assert spy.executed == []


def test_self_views_are_excluded(cur):
    """A seller scrolling their own store must not inflate their exposure."""
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A, self_view=0)
    write_impression(
        cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A, self_view=1, minutes_ago=2
    )

    rows = read.impressions(cur, SELLER_A)

    assert len(rows) == 1
    assert rows[0]["self_view"] == 0


def test_the_window_excludes_older_rows(cur):
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A, minutes_ago=1)
    write_impression(cur, listing_id=A_LISTINGS[1], seller_user_id=SELLER_A, minutes_ago=60 * 24 * 30)

    assert len(read.impressions(cur, SELLER_A)) == 1
    assert len(read.impressions(cur, SELLER_A, since_seconds=60 * 60 * 24 * 365)) == 2


def test_a_seller_with_no_listings_reads_nothing_rather_than_everything(cur):
    """An empty scope must close, not open.

    The dangerous implementation drops the ``listing_id IN (…)`` clause when the
    id list is empty — an easy thing to do, because ``IN ()`` is a syntax error
    in SQL and omitting the clause is the obvious way around it.

    Seller C is the case that makes this observable: they own no listings, so
    their scope is empty, but events exist carrying their seller id (their
    listings were deleted, or the rows predate a transfer). With the listing
    clause dropped, ``seller_user_id=?`` alone happily returns those rows.
    """
    seller_c = 303
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    write_impression(cur, listing_id=3001, seller_user_id=seller_c)

    assert read.seller_listing_ids(cur, seller_c) == []
    assert read.impressions(cur, seller_c) == []


def test_an_unreadable_table_is_a_missing_measurement_not_an_exception(cur):
    """The dashboard's other half must still render."""
    cur.execute("DROP TABLE commerce_discovery_impression_events")

    assert read.impressions(cur, SELLER_A) == []


def test_a_seller_sees_only_their_own_orders(cur):
    write_order(cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    write_order(cur, item_id=B_LISTINGS[0], seller_user_id=SELLER_B)

    rows = read.orders(cur, SELLER_A)

    assert [row["item_id"] for row in rows] == [A_LISTINGS[0]]


def test_orders_are_windowed_to_the_same_stretch_as_the_events(cur):
    """Else a week of clicks is compared against a year of sales.

    ``seller_metrics`` counts whatever rows it is handed, so an unwindowed order
    list makes ``confirmed_orders`` lifetime while the funnel above it is seven
    days — and ``client_server_purchase_gap`` then reports the store's entire
    history as a discrepancy against one week of traffic.
    """
    write_order(cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A, minutes_ago=1)
    write_order(
        cur, item_id=A_LISTINGS[1], seller_user_id=SELLER_A, minutes_ago=60 * 24 * 30
    )

    assert len(read.orders(cur, SELLER_A)) == 1
    assert len(read.orders(cur, SELLER_A, since_seconds=60 * 60 * 24 * 365)) == 2


def test_an_order_with_an_unreadable_timestamp_is_kept(cur):
    """A formatting fault must not delete money from a seller's count."""
    write_order(cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A, created_at="not-a-date")

    assert len(read.orders(cur, SELLER_A)) == 1


def test_orders_are_not_filtered_by_status_here(cur):
    """`is_confirmed_order` owns that question and must see the rows to answer it."""
    write_order(cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A, status="checkout_created")

    rows = read.orders(cur, SELLER_A)

    assert [row["status"] for row in rows] == ["checkout_created"]


@pytest.mark.parametrize("bad", [0, -1, None, "", 1.5, True])
def test_orders_refuse_an_unusable_seller_identity_too(cur, bad):
    with pytest.raises(read.SellerScopeError):
        read.orders(cur, bad)


def test_an_unreadable_orders_table_is_none_and_never_an_empty_list(cur):
    """`[]` here would reach the seller as a confident "0 orders".

    The event reads fail soft because a missing impression log produces `None`
    rates that say "not measured". An empty order list produces a number.
    """
    cur.execute("DROP TABLE seller_transactions")

    assert read.orders(cur, SELLER_A) is None


def test_a_seller_with_no_orders_in_the_window_is_an_empty_list_not_none(cur):
    """And the distinction has to cut both ways, or it is not a distinction."""
    write_order(
        cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A, minutes_ago=60 * 24 * 30
    )

    assert read.orders(cur, SELLER_A) == []
