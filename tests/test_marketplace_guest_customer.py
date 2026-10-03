"""A cart that belongs to nobody still has to belong to exactly one browser.

``marketplace_guest_customer`` allocates the integer that goes in
``marketplace_cart_items.user_id`` for a visitor with no account. Everything in
the cart lane reads ``WHERE c.user_id = ?`` and nothing joins ``users``, so that
integer is the *only* thing standing between a guest and somebody else's cart.
These pin the properties that makes it safe to be only thing: the ids are
disjoint from any id ``users`` can issue, they are never reused, and the token
is the sole way to reach one.

The failure these exist to catch is silent. A guest reading a member's cart
raises nothing, logs nothing and renders a perfectly ordinary page.

Run: python3 -m pytest tests/test_marketplace_guest_customer.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import marketplace_guest_customer as guest  # noqa: E402

NOW = "2026-10-01T00:00:00"


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    guest.ensure_schema(cursor)
    yield cursor
    conn.close()


def test_a_guest_owner_id_can_never_collide_with_a_member(cur):
    """The property the whole design rests on.

    ``users.user_id`` is ``INTEGER PRIMARY KEY AUTOINCREMENT``, so it is always
    ``>= 1``. Guest ids are below ``-1_000_000`` and count further down, so the
    two spaces cannot meet however many of either are issued.
    """
    seen = set()
    for _ in range(25):
        _token, owner_id = guest.allocate(cur, NOW)
        assert owner_id <= guest.FIRST_OWNER_ID
        assert owner_id not in seen
        seen.add(owner_id)
    assert guest.is_guest_owner(min(seen))
    # And the classifier agrees about the ids it is meant to exclude.
    for member_id in (1, 2, 95101, 2**31 - 1):
        assert not guest.is_guest_owner(member_id)
    assert not guest.is_guest_owner(0)
    assert not guest.is_guest_owner(None)


def test_an_expired_guest_does_not_hand_their_cart_to_the_next_visitor(cur):
    """Expiry revokes the token and keeps the id, so ids are never reissued.

    This was written expecting a ``DELETE`` to be safe and it was not: the high
    water mark is ``MIN(owner_id)`` over the table, so removing the lowest row
    moved the mark back up and the next visitor was allocated an id whose cart
    lines could still exist. Their first page load would have shown somebody
    else's shopping, raising nothing. :func:`revoke` is the supported expiry for
    that reason, and this pins both halves -- the token stops working, the id
    does not come back.
    """
    first_token, first = guest.allocate(cur, NOW)
    _second_token, second = guest.allocate(cur, NOW)
    assert guest.revoke(cur, first_token) == first
    assert guest.owner_id_for_token(cur, first_token) == 0
    _third_token, third = guest.allocate(cur, NOW)
    assert third < second < first
    # Revoking is not repeatable and a stale token cannot retire anyone else.
    assert guest.revoke(cur, first_token) == 0


def test_only_the_token_opens_the_cart(cur):
    token, owner_id = guest.allocate(cur, NOW)
    assert guest.owner_id_for_token(cur, token) == owner_id
    # Anything else is an empty cart, not an error and not someone else's.
    for miss in ("", "   ", None, "deadbeef", token[:-1], token.upper()):
        assert guest.owner_id_for_token(cur, miss) == 0


def test_reading_a_cart_never_mints_a_guest(cur):
    """Allocation is an action, not a page load.

    Every crawler hit and every bounced visit reaches a product page. If a read
    allocated, the table would grow by one row per request and the overwhelming
    majority would never hold a line.
    """
    guest.owner_id_for_token(cur, "no-such-token")
    guest.owner_id_for_token(cur, "")
    cur.execute("SELECT COUNT(*) FROM marketplace_guest_customers")
    assert cur.fetchone()[0] == 0


def test_a_claimed_token_stops_opening_the_cart(cur):
    """Signing in spends the guest identity.

    Without this the token keeps resolving after the lines have been merged into
    the member's cart, so the browser cookie -- which may be on a shared
    computer -- would still name an owner id whose rows now belong to an
    account.
    """
    token, owner_id = guest.allocate(cur, NOW)
    assert guest.claim_for_member(cur, token, 95102) == owner_id
    assert guest.owner_id_for_token(cur, token) == 0
    # Claiming is not repeatable, so a replayed sign-in cannot re-point it.
    assert guest.claim_for_member(cur, token, 95103) == 0
    cur.execute(
        "SELECT claimed_by_user_id FROM marketplace_guest_customers WHERE owner_id=?", (owner_id,))
    assert cur.fetchone()[0] == 95102


def test_allocation_is_not_an_account(cur):
    """Nothing here writes to ``users``, and the row holds no identity.

    Asserted on the table's own shape: a column for an email or a password here
    would be the beginning of the forced-signup this work removes.
    """
    guest.allocate(cur, NOW)
    cur.execute("PRAGMA table_info(marketplace_guest_customers)")
    columns = {row[1] for row in cur.fetchall()}
    assert columns == {
        "id", "guest_token", "owner_id", "created_at", "last_seen_at", "claimed_by_user_id"}


def test_the_schema_helper_is_idempotent_and_does_not_raise(cur):
    assert guest.ensure_schema(cur) is True
    assert guest.ensure_schema(cur) is True
    # A cursor that cannot run DDL degrades to False rather than exploding in a
    # request path -- a guest who cannot be given a cart must still get the page.
    closed = sqlite3.connect(":memory:")
    closed_cur = closed.cursor()
    closed.close()
    assert guest.ensure_schema(closed_cur) is False
    assert guest.owner_id_for_token(closed_cur, "anything") == 0
