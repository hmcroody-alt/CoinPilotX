"""The buyer sees the store, never the person who owns it.

These tests exist because the leak they guard against is invisible in review: a
`COALESCE(u.display_name, ...)` reads like defensive coding, and the failure only
shows up when a real seller's legal name appears on a stranger's receipt. So the
assertions here are deliberately about *source text* as well as behaviour — the
rule is "buyer-facing marketplace SQL does not join `users` for a name", and a
rule you can only test by running production data is a rule that will rot.
"""

from __future__ import annotations

import pathlib
import re

from services import marketplace_listing_lifecycle as lifecycle
from services import marketplace_seller_identity as identity

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_store_name_prefers_storefront_then_registered_business():
    assert identity.store_name({"display_name": "Roody's Shop"}) == "Roody's Shop"
    assert identity.store_name({"business_name": "Roody LLC"}) == "Roody LLC"
    # The storefront name wins: it is what the seller told buyers they trade as.
    assert identity.store_name({"display_name": "Roody's Shop", "business_name": "Roody LLC"}) == "Roody's Shop"


def test_store_name_never_falls_back_to_a_personal_name():
    """A row carrying only the owner's name resolves to no store identity.

    `username` and personal `display_name` on `users` are not consulted, so even
    a caller that hands over a user row cannot smuggle a person into a buyer
    surface. Whitespace-only is the same as absent.
    """
    assert identity.store_name({"username": "roody", "first_name": "Roody", "last_name": "Cherie"}) == ""
    assert identity.store_name({"display_name": "   "}) == ""
    assert identity.display_store_name({"username": "roody"}) == identity.FALLBACK_STORE_NAME
    assert "roody" not in identity.display_store_name({"username": "roody"}).lower()


def test_publication_requires_a_store_name():
    live = {
        "status": "published",
        "approval_status": "approved",
        "seller_status": "approved",
        "quantity": 4,
        "product_type": "physical",
    }
    assert lifecycle.is_public({**live, "seller_store_name": "Roody's Shop"})
    assert lifecycle.public_denial_code({**live, "seller_store_name": "Roody's Shop"}) == ""

    # A seller with no public identity is not sellable-from. The buyer's next
    # move is the same as for a suspended seller: none.
    nameless = {**live, "seller_store_name": ""}
    assert not lifecycle.is_public(nameless)
    assert lifecycle.public_denial_code(nameless) == "SELLER_UNAVAILABLE"


def test_a_row_without_identity_columns_is_not_treated_as_nameless():
    """Silence is not evidence.

    Plenty of internal call sites fetch a listing without joining the seller. If
    "no store-name column" meant "no store", those paths would quietly take
    healthy listings off sale. The SQL predicate is where the invariant binds.
    """
    unprojected = {
        "status": "published",
        "approval_status": "approved",
        "seller_status": "approved",
        "quantity": 4,
        "product_type": "physical",
    }
    assert lifecycle.is_public(unprojected)
    assert lifecycle.public_denial_code(unprojected) == ""


def test_public_sql_enforces_the_invariant_on_the_seller_table():
    sql = lifecycle.public_sql("l", "ms")
    assert "ms.display_name" in sql and "ms.business_name" in sql
    assert "IS NOT NULL" in sql
    # The predicate must never reach for a users alias.
    assert "u.display_name" not in sql


def _buyer_marketplace_sql(text: str) -> list[str]:
    """Marketplace SELECT statements that project a seller name from `users`."""
    return re.findall(r"COALESCE\(\s*u\.display_name[^)]*\)\s+AS\s+seller_name", text)


def test_no_buyer_marketplace_query_selects_the_owner_personal_name():
    for relative in ("bot.py", "services/marketplace_cart_routes.py"):
        text = (REPO / relative).read_text(encoding="utf-8", errors="ignore")
        offenders = _buyer_marketplace_sql(text)
        assert not offenders, f"{relative} still projects a personal name as seller_name: {offenders[:2]}"


def test_cart_lines_carry_the_canonical_field_name():
    """The client is handed `seller_store_name` explicitly, not left to guess."""
    text = (REPO / "services/marketplace_cart_routes.py").read_text(encoding="utf-8")
    assert '"seller_store_name"' in text
    # And the legacy alias resolves to the same value rather than its own query.
    assert text.count("seller_identity.display_store_name(row)") >= 2
