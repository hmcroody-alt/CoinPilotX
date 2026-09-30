"""Focused publication and inventory policy tests for the legacy Marketplace."""

import sqlite3

from services import marketplace_listing_lifecycle as lifecycle


def listing(**overrides):
    value = {
        "status": "published",
        "approval_status": "approved",
        "seller_status": "approved",
        "product_type": "physical",
        "quantity": 2,
        # Not decoration. `priced` is a gate rule with `passes_when_unknown=False`,
        # so a fixture that omits this column describes a listing no buyer may
        # reach -- correctly, since an unprojected column proves nothing and a
        # gate's safe default is no. Every query that asks the gate does select
        # `price_label`, so carrying it here is what makes this fixture a listing
        # the code will actually be handed. `test_price_is_required` pins the
        # omission case on purpose, so this line can never quietly become the
        # thing holding a real regression out of sight.
        "price_label": "$24.00",
    }
    value.update(overrides)
    return value


def test_only_approved_published_inventory_is_public():
    assert lifecycle.is_public(listing()) is True
    assert lifecycle.is_public(listing(status="draft")) is False
    assert lifecycle.is_public(listing(status="pending_review")) is False
    assert lifecycle.is_public(listing(status="review_ready")) is False
    assert lifecycle.is_public(listing(approval_status="pending_review")) is False
    assert lifecycle.is_public(listing(seller_status="suspended")) is False
    assert lifecycle.is_public(listing(quantity=0)) is False


def test_stockless_inventory_and_requested_quantity():
    assert lifecycle.is_public(listing(product_type="digital", quantity=0)) is True
    assert lifecycle.inventory_available(listing(quantity=2), 2) is True
    assert lifecycle.inventory_available(listing(quantity=2), 3) is False


def test_material_edits_require_review_but_quantity_does_not():
    assert lifecycle.requires_rereview({"title"}) is True
    assert lifecycle.requires_rereview({"price_label"}) is True
    assert lifecycle.requires_rereview({"quantity"}) is False


def test_truthful_seller_labels():
    assert lifecycle.seller_label(listing()) == "Live"
    assert lifecycle.seller_label(listing(status="pending_review", approval_status="pending_review")) == "In review"
    assert lifecycle.seller_label(listing(status="changes_requested")) == "Changes requested"


# ---------------------------------------------------------------------------
# The price condition
#
# Five listings were live in production with a blank `price_label`: published,
# moderator-approved, approved seller, five figures of stock each, and offered on
# every buyer surface right up to a checkout that refuses `amount_cents <= 0`.
# Nothing was mis-set. The gate required stock and never required a price, so the
# catalogue and the till disagreed about what was for sale, and the seller's own
# dashboard said "Live".
# ---------------------------------------------------------------------------


def test_price_is_required_to_buy():
    assert lifecycle.is_purchasable(listing(price_label="$24.00")) is True
    assert lifecycle.is_purchasable(listing(price_label="")) is False
    assert lifecycle.is_purchasable(listing(price_label=None)) is False
    # `price_label` is `DEFAULT 'Request access'`, so an untouched row carries a
    # phrase where a number belongs and is not caught by a NULL/blank test.
    assert lifecycle.is_purchasable(listing(price_label="Request access")) is False
    assert lifecycle.is_purchasable(listing(price_label="  REQUEST ACCESS  ")) is False
    # Unprojected is not the same fact as blank, and a gate answers no to both.
    unprojected = listing()
    unprojected.pop("price_label")
    assert lifecycle.is_purchasable(unprojected) is False


def test_price_is_not_required_to_be_seen():
    """The reason `priced` is `purchase_only`, and the whole point of the split.

    An unpriced listing is a real public web page: the product page renders it
    with no price pill and no schema.org `Offer`, and it stays in the sitemap --
    `marketplace_seo.eligibility` calls that `indexable=True, feed_eligible=False`
    and explains that filtering pages out of Search to satisfy a rule Search does
    not have withholds real pages for nothing. Had the price gone into
    `is_public`, those pages would 404 and their URLs would leave the sitemap:
    a worse defect than the one the rule fixes, and four tests in
    `test_marketplace_public_pages.py` say so.
    """
    unpriced = listing(price_label="")
    assert lifecycle.is_public(unpriced) is True
    assert lifecycle.is_purchasable(unpriced) is False
    # Every other condition still governs visibility, so the split has not
    # quietly turned `is_public` into "the row exists".
    assert lifecycle.is_public(listing(price_label="", status="draft")) is False
    assert lifecycle.is_public(listing(price_label="", quantity=0)) is False
    assert lifecycle.is_public(listing(price_label="", seller_status="suspended")) is False
    assert [rule.key for rule in lifecycle.VISIBILITY_RULES] == [
        "seller_approved", "seller_named", "released", "in_stock"]


def test_an_unparseable_label_is_deliberately_allowed_through():
    """The known, chosen gap. Documented so a later reader does not "fix" it.

    This module cannot parse a price: it must not import `bot` (that runs
    `init_db()`), and `public_sql` has to stay exactly equivalent to `is_public`
    without running Python. So the test is membership, and a label that is
    non-empty but meaningless reads as priced here. It is still refused by the
    cart and by checkout, so the listing is not sellable either way -- and erring
    this direction withholds nothing from a healthy product, which is the one
    failure a publication gate must not have.

    `pulsedrop.hydration.state` keeps a parser-based check of its own on top of
    this rule for exactly that reason, and so closes the gap on the one surface
    that can afford to import `bot`.
    """
    assert lifecycle.is_purchasable(listing(price_label="ask us")) is True


def test_an_unpriced_listing_explains_itself_to_each_audience():
    unpriced = listing(price_label="")
    # The buyer learns nothing about the seller's bookkeeping.
    assert lifecycle.public_denial_code(unpriced) == "ITEM_UNAVAILABLE"
    # The merchant who owns it gets the one thing they can act on.
    assert lifecycle.seller_label(unpriced) == "Needs a price"
    assert lifecycle.publication_blocker(unpriced) == "priced"
    assert lifecycle.blocker_note("priced") == "the listing has no price"
    # And it is reported as the surprise it is: published, approved, unreachable.
    assert lifecycle.live_blocker(unpriced) == "priced"


def test_price_is_reported_last_so_no_existing_reason_changes():
    """Rule order is reported order, and 62 listings depend on this one.

    Those rows are unpriced *and* out of stock. Inserting `priced` earlier would
    have silently relabelled every one of them without changing whether anybody
    could buy them -- a worse status chip for no gain.
    """
    both = listing(price_label="", quantity=0)
    assert lifecycle.publication_blocker(both) == "in_stock"
    assert lifecycle.public_denial_code(both) == "OUT_OF_STOCK"
    assert lifecycle.PUBLICATION_RULES[-1].key == "priced"


def test_discovery_and_the_gate_share_one_definition_of_unpriced():
    """Not equal — the same object.

    `commerce_discovery` held this list alone while the gate had no price
    condition at all, which is how discovery ended up refusing to *recommend*
    listings every other surface went on offering. Two equal copies would drift;
    an alias cannot.
    """
    from services.commerce_discovery import eligibility

    assert eligibility._UNPRICED_LABELS is lifecycle.UNPRICED_LABELS


# ---------------------------------------------------------------------------
# `public_sql()` and `is_public()` are one rule in two languages
# ---------------------------------------------------------------------------

#: Rows chosen so each differs from a sellable row in one respect, plus the two
#: combinations that pin reporting order. Verified equivalent on the real
#: production catalogue too (107 rows, Postgres, zero disagreements); this pins it
#: in CI, which is the half that keeps being true.
_ROWS = [
    # (id, status, approval, product_type, quantity, price_label, seller_status, store_name)
    (1, "published", "approved", "physical", 2, "$24.00", "approved", "Shop"),
    (2, "published", "approved", "physical", 2, "", "approved", "Shop"),
    (3, "published", "approved", "physical", 2, "Request access", "approved", "Shop"),
    (4, "published", "approved", "physical", 2, "  REQUEST ACCESS  ", "approved", "Shop"),
    (5, "published", "approved", "physical", 2, None, "approved", "Shop"),
    (6, "published", "approved", "digital", 0, "$9.99", "approved", "Shop"),
    (7, "published", "approved", "digital", 0, "", "approved", "Shop"),
    (8, "published", "approved", "physical", 0, "$24.00", "approved", "Shop"),
    (9, "published", "approved", "physical", 0, "", "approved", "Shop"),
    (10, "draft", "approved", "physical", 2, "$24.00", "approved", "Shop"),
    (11, "published", "pending_review", "physical", 2, "$24.00", "approved", "Shop"),
    (12, "published", "approved", "physical", 2, "$24.00", "suspended", "Shop"),
    (13, "published", "approved", "physical", 2, "$24.00", "approved", ""),
    (14, "active", "approved", "physical", 2, "ask us", "approved", "Shop"),
]


def _sqlite_catalogue():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE marketplace_listings (
            id INTEGER PRIMARY KEY, seller_user_id INTEGER, status TEXT,
            approval_status TEXT, product_type TEXT, listing_type TEXT,
            quantity INTEGER, price_label TEXT);
        CREATE TABLE marketplace_sellers (
            user_id INTEGER PRIMARY KEY, status TEXT, display_name TEXT);
        """
    )
    for row in _ROWS:
        listing_id, status, approval, ptype, qty, label, seller_status, store = row
        conn.execute(
            "INSERT INTO marketplace_listings "
            "(id, seller_user_id, status, approval_status, product_type, quantity, price_label) "
            "VALUES (?,?,?,?,?,?,?)",
            (listing_id, listing_id, status, approval, ptype, qty, label),
        )
        conn.execute(
            "INSERT INTO marketplace_sellers (user_id, status, display_name) VALUES (?,?,?)",
            (listing_id, seller_status, store),
        )
    conn.commit()
    return conn


def _ids_matching(conn, predicate):
    return {
        row["id"]
        for row in conn.execute(
            "SELECT l.id FROM marketplace_listings l "
            "JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
            f"WHERE {predicate}"
        )
    }


def _ids_where(conn, verdict):
    matched = set()
    for row in conn.execute(
        "SELECT l.id, l.status, l.approval_status, l.product_type, l.listing_type, "
        "l.quantity, l.price_label, "
        "COALESCE(ms.status,'missing') AS seller_status, "
        "COALESCE(ms.display_name,'') AS seller_store_name "
        "FROM marketplace_listings l "
        "JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id"
    ):
        if verdict(dict(row)):
            matched.add(row["id"])
    return matched


def test_each_predicate_agrees_with_its_python_twin_row_for_row():
    """The invariant the whole module exists for, asserted against a database.

    A count that matches proves nothing about *which* rows match, so this
    compares sets of ids. Adding a condition to one half and not the other is
    exactly the drift `PUBLICATION_RULES` was introduced to end, and it is
    invisible to any test that exercises only the Python side. Both pairs are
    checked, because the price condition lives in one pair and not the other and
    that asymmetry is the easiest thing here to get wrong.
    """
    conn = _sqlite_catalogue()

    visible_sql = _ids_matching(conn, lifecycle.public_sql("l", "ms"))
    visible_py = _ids_where(conn, lifecycle.is_public)
    assert visible_sql == visible_py

    sellable_sql = _ids_matching(conn, lifecycle.purchasable_sql("l", "ms"))
    sellable_py = _ids_where(conn, lifecycle.is_purchasable)
    assert sellable_sql == sellable_py

    # A fixture where every predicate said "nothing" would satisfy the equalities
    # above and assert nothing at all. 1 is priced and stocked, 6 is priced and
    # stockless, 14 carries the deliberately-allowed unparseable label. 2, 3, 4,
    # 5 and 7 are visible-but-unsellable: the unpriced rows whose web pages must
    # survive. 8 is priced with no stock, so it is in neither set.
    assert sellable_sql == {1, 6, 14}
    assert visible_sql == {1, 2, 3, 4, 5, 6, 7, 14}
    # Purchasable is a strict subset of visible, in SQL as well as in Python.
    assert sellable_sql < visible_sql
    conn.close()


def test_the_sql_price_clause_is_generated_from_the_python_set():
    """So a phrase added to `UNPRICED_LABELS` reaches SQL without a second edit."""
    predicate = lifecycle.purchasable_sql("l", "ms")
    for label in lifecycle.UNPRICED_LABELS:
        assert "'%s'" % label in predicate
    # And it is absent from the visibility predicate, which is the split itself.
    assert "price_label" not in lifecycle.public_sql("l", "ms")
