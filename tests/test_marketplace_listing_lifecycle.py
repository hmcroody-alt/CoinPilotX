"""Focused publication and inventory policy tests for the legacy Marketplace."""

from pathlib import Path

from services import marketplace_listing_lifecycle as lifecycle

ROOT = Path(__file__).resolve().parents[1]


def _deliberately_unpriced_labels():
    """``bot.PRICE_LABEL_UNPRICED``, read out of the source rather than imported.

    Importing the monolith starts its workers and runs ``init_db()``. This is one
    line of it, and reading it keeps the list below bound to the real constant: a
    fifth deliberate label added to ``bot`` arrives here without an edit.
    """
    source = ROOT.joinpath("bot.py").read_text(encoding="utf-8")
    start = source.index("PRICE_LABEL_UNPRICED = ")
    namespace: dict = {}
    exec(source[start:source.index("\n", start)], namespace)  # noqa: S102
    return namespace["PRICE_LABEL_UNPRICED"]


def listing(**overrides):
    value = {
        "status": "published",
        "approval_status": "approved",
        "seller_status": "approved",
        "product_type": "physical",
        "quantity": 2,
        "price_label": "$19.99",
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


def test_an_unpriced_listing_is_not_purchasable():
    """A blank price label holds the listing back, approval intact.

    Production shipped this: five approved, well-stocked supplier imports with
    ``price_label=''`` were being offered to buyers with no amount on them. The
    listing is not rejected — the moderation decision stands and the merchant
    gets a reason they can act on.
    """
    assert lifecycle.is_public(listing(price_label="")) is False
    assert lifecycle.publication_blocker(listing(price_label="")) == "priced"
    assert lifecycle.live_blocker(listing(price_label="")) == "priced"
    # Whitespace is how a spreadsheet import spells blank.
    assert lifecycle.publication_blocker(listing(price_label="   ")) == "priced"
    assert lifecycle.publication_blocker(listing(price_label=None)) == "priced"


def test_an_unpriced_listing_reuses_the_existing_unavailable_code():
    """No fourth denial code: buyer clients branch on exactly three strings.

    "May come back once the merchant prices it" is what ``ITEM_UNAVAILABLE``
    already means, and a code the app has never seen would reach it as the
    default "unavailable" anyway — with none of the copy.
    """
    assert lifecycle.public_denial_code(listing(price_label="")) == "ITEM_UNAVAILABLE"
    assert lifecycle.seller_label(listing(price_label="")) == "Price needed"
    assert lifecycle.blocker_note("priced") == "the listing has no price"


def test_a_deliberate_unpriced_label_is_still_on_sale():
    """"Free" is a price. Zero cents is an answer; a blank label is silence.

    Every label in ``PRICE_LABEL_UNPRICED`` parses to zero cents, so a rule
    phrased as "parses above zero" would have taken every free and
    request-access listing off sale — a far larger outage than the bug.
    """
    for label in _deliberately_unpriced_labels():
        assert lifecycle.is_public(listing(price_label=label)) is True, label
        assert lifecycle.publication_blocker(listing(price_label=label)) == "", label
    assert lifecycle.is_public(listing(price_label="$0.00")) is True


def test_a_row_without_a_price_column_is_not_accused_but_cannot_be_sold():
    """The gate/description split, on the price rule.

    A payload that never selected ``price_label`` proves nothing, so the
    merchant's chip does not say "Price needed" — telling somebody to fix a
    price you did not look at is the failure mode this asymmetry exists for.
    The gate still declines, because the safe default for a gate is no, and
    because ``price_label`` needs no join: every real caller of
    :func:`public_sql` selects it, so an unprojected price is a test artefact
    rather than a live query path being held back.
    """
    unprojected = listing()
    del unprojected["price_label"]
    assert lifecycle.publication_blocker(unprojected) == ""
    assert lifecycle.live_blocker(unprojected) == ""
    assert lifecycle.is_public(unprojected) is False
    assert lifecycle.public_denial_code(unprojected) == "ITEM_UNAVAILABLE"


def test_the_sql_predicate_states_the_price_rule_too():
    """Python and SQL cannot disagree about what "purchasable" means.

    Buyer discovery never calls :func:`is_public` — it runs this string — so a
    rule added to the table and not to the predicate would leave the bug live
    while every unit test above passed.
    """
    sql = lifecycle.public_sql("l", "ms")
    assert "NULLIF(TRIM(l.price_label),'') IS NOT NULL" in sql
    # Dialect-portable on purpose: this predicate runs on SQLite locally and
    # PostgreSQL in production, and `!=''` would not agree with itself on NULL.
    assert "l.price_label<>''" not in sql and "l.price_label != ''" not in sql
    assert lifecycle.public_sql("li", "s").count("li.price_label") == 1


def test_truthful_seller_labels():
    assert lifecycle.seller_label(listing()) == "Live"
    assert lifecycle.seller_label(listing(status="pending_review", approval_status="pending_review")) == "In review"
    assert lifecycle.seller_label(listing(status="changes_requested")) == "Changes requested"
