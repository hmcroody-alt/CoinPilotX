"""Renaming a shop in one screen renames it everywhere buyers can see it.

The defect these cover is not a crash. Every write succeeded; they just landed
in different tables, so the Business Profile said one thing, the Store dashboard
said another, and the marketplace listing — the only one a buyer ever reads —
said whatever the merchant application captured first. Tests that only assert
"the save returned 200" cannot see that, so these assert on the canonical column
after the save.
"""

from __future__ import annotations

import sqlite3

import pytest

from services import marketplace_seller_identity as identity
from services import store_identity_sync as sync


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE marketplace_sellers ("
        " user_id INTEGER PRIMARY KEY, status TEXT,"
        " display_name TEXT, business_name TEXT)"
    )
    conn.execute(
        "INSERT INTO marketplace_sellers (user_id, status, display_name, business_name)"
        " VALUES (7, 'approved', 'Old Shop', 'Roody Cherie Ltd')"
    )
    conn.commit()
    yield conn.cursor()
    conn.close()


def _row(cur, user_id=7):
    cur.execute("SELECT * FROM marketplace_sellers WHERE user_id=?", (user_id,))
    return dict(cur.fetchone())


def test_a_profile_rename_lands_on_the_column_buyers_read(cur):
    assert sync.adopt_store_name(cur, 7, "M&W Store") == "M&W Store"
    row = _row(cur)
    assert row["display_name"] == "M&W Store"
    # And the buyer-facing resolver now answers with it, through the same
    # authority the listing queries use.
    assert identity.store_name(row) == "M&W Store"
    assert identity.display_store_name(row) == "M&W Store"


def test_the_rename_never_touches_the_legal_name(cur):
    sync.adopt_store_name(cur, 7, "M&W Store")
    row = _row(cur)
    assert row["business_name"] == "Roody Cherie Ltd"
    assert identity.legal_business_name(row) == "Roody Cherie Ltd"
    # The legal name is present on the row and still not public.
    assert identity.store_name(row) == "M&W Store"


def test_a_blank_name_cannot_erase_the_store(cur):
    """An empty store name takes every listing off sale.

    `marketplace_listing_lifecycle` refuses to publish a seller with no public
    identity, so a screen that submits a partial payload — or a field the user
    cleared by accident — must not be able to reach that state through this
    path. Blank is ignored, not stored.
    """
    for blank in ("", "   ", None):
        assert sync.adopt_store_name(cur, 7, blank) is None
        assert _row(cur)["display_name"] == "Old Shop"


def test_an_unchanged_name_is_not_a_write(cur):
    assert sync.adopt_store_name(cur, 7, "Old Shop") is None
    # Whitespace differences are not a change either.
    assert sync.adopt_store_name(cur, 7, "  Old   Shop ") is None
    assert _row(cur)["display_name"] == "Old Shop"


def test_editing_a_profile_never_mints_a_seller_account(cur):
    """A non-seller can keep a business profile without becoming a seller.

    Upserting here would hand someone a `marketplace_sellers` row with no
    approved application behind it — creating the exact second authority this
    work exists to remove, via a route that only claims to save a profile.
    """
    assert sync.adopt_store_name(cur, 999, "Ghost Store") is None
    cur.execute("SELECT COUNT(*) AS n FROM marketplace_sellers")
    assert dict(cur.fetchone())["n"] == 1


def test_a_suspended_seller_can_still_correct_their_name(cur):
    """Renaming is not selling.

    Suspension gates the seller *surfaces*; it does not freeze the record. A
    seller told to fix a misleading store name must be able to fix it.
    """
    cur.execute("UPDATE marketplace_sellers SET status='suspended' WHERE user_id=7")
    assert sync.adopt_store_name(cur, 7, "Corrected Store") == "Corrected Store"
    assert _row(cur)["display_name"] == "Corrected Store"


def test_the_name_is_clamped_and_collapsed(cur):
    assert sync.adopt_store_name(cur, 7, "A" * 500) == "A" * sync.STORE_NAME_MAX
    assert sync.adopt_store_name(cur, 7, "Two\n\tWords") == "Two Words"


def test_canonical_read_and_write_agree(cur):
    sync.adopt_store_name(cur, 7, "M&W Store")
    assert sync.canonical_store_name(cur, 7) == "M&W Store"
    # A user with no seller row reads as "no store name", not as an error.
    assert sync.canonical_store_name(cur, 999) == ""


def test_an_unreadable_table_does_not_break_a_profile_save(cur):
    """The profile write has already committed when propagation runs.

    Raising here would tell an owner their edit was lost when it was stored, so
    the failure mode is a stale marketplace name — visible to the audit script —
    rather than a false error on a successful save.
    """
    cur.execute("DROP TABLE marketplace_sellers")
    assert sync.canonical_store_name(cur, 7) == ""
    assert sync.adopt_store_name(cur, 7, "M&W Store") is None
