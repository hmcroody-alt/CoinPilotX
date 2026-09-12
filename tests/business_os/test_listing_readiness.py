"""Business OS — the one listing-readiness verdict.

What these tests are for, in order of how much they matter:

  * the verdict cannot become a FIFTH authority. It shares its vocabulary with
    the supplier evaluator, and renaming a code on either side fails here;
  * unknown stock is not empty stock. This is the distinction the client lost,
    and losing it tells a merchant their product is unavailable when the truth
    is that nobody knows;
  * a missing price is a blocker and never a price;
  * `checkout_ready` is never true while something that blocks checkout is
    present -- asserted as a property over every state the evaluator can reach,
    not by restating the rule;
  * no money, cost, margin or supplier fact appears in a verdict.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="readiness_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services.business_os.marketplace import listing_readiness as r  # noqa: E402


def listing(**overrides):
    """A listing that is ready to sell, so each test changes exactly one thing.

    Every test below starts from a verdict of "yes" and breaks one fact. A
    fixture that started from "no" would let a test pass because of a fault it
    was not looking at.
    """
    base = {
        "title": "Brass desk lamp",
        "category": "Home",
        "cover_image_url": "https://cdn.example/lamp.jpg",
        "price_label": "$24.00",
        "approval_status": "approved",
        "status": "active",
        # Both columns, because a real row carries both and they disagree: the
        # write path sets `listing_type`, while `product_type` and
        # `delivery_type` are declared `TEXT DEFAULT 'digital'`. A fixture that
        # set only one of them would agree with a wrong reading of the row --
        # which is exactly what happened. See
        # test_the_default_column_values_do_not_make_everything_stockless.
        "listing_type": "physical",
        "product_type": "physical",
        "quantity": 40,
    }
    base.update(overrides)
    return base


def test_the_fixture_is_actually_ready():
    assert r.evaluate(listing()) == {
        "publishable": True, "checkout_ready": True,
        "blockers": [], "warnings": []}


# --- the anti-drift test ------------------------------------------------------
def test_the_vocabulary_matches_the_supplier_evaluator():
    """Two evaluators are tolerable. Two vocabularies are not.

    `suppliers/drafts.py:_validate` answers the same question for supplier
    drafts and cannot be reused here (it needs a product_sources row and priced
    variants). What must not happen is the same fault acquiring two names, so
    that a merchant sees MISSING_PRICE on one screen and NO_PRICE on another and
    a maintainer has to know both.

    This fails if either side renames a shared code. It is the cheapest
    available guard against the fifth authority this module could have become.
    """
    from services.business_os.suppliers import drafts as supplier

    shared = ["MISSING_TITLE", "MISSING_CATEGORY", "NO_VALID_MEDIA",
              "MISSING_PRICE", "RESTRICTED_PRODUCT", "UNKNOWN_INVENTORY"]
    for name in shared:
        mine, theirs = getattr(r, name), getattr(supplier, name)
        assert mine == theirs, (
            f"{name} is {mine!r} here and {theirs!r} in the supplier evaluator. "
            f"One fault, one name, or the merchant learns two vocabularies")

    # And the shared names really are spelled as themselves, so the check above
    # cannot be satisfied by both sides being wrong in the same way.
    for name in shared:
        assert getattr(r, name) == name


# --- unknown is not zero ------------------------------------------------------
@pytest.mark.parametrize("quantity,expected", [
    (None, r.UNKNOWN_INVENTORY),
    ("", r.UNKNOWN_INVENTORY),
    ("not a number", r.UNKNOWN_INVENTORY),
    (0, r.OUT_OF_STOCK),
    (1, r.LOW_STOCK),
    (5, r.LOW_STOCK),
    (6, None),
])
def test_stock_states_keep_unknown_separate_from_empty(quantity, expected):
    verdict = r.evaluate(listing(quantity=quantity))
    assert verdict["warnings"] == ([expected] if expected else [])
    # Stock never blocks publication: a merchant restocking a live listing must
    # not have it unpublished under them over a temporary fact.
    assert verdict["publishable"] is True


def test_unknown_and_empty_are_different_answers():
    """The specific regression, stated as the inequality that was violated.

    `Number(item.quantity || 0)` on the client turned NULL into 0, so a physical
    listing whose seller does not track stock was counted out-of-stock, pushed
    into the "out" tab and raised the red banner. The server has always been
    able to tell these apart -- the column is nullable and the serializer spreads
    it through untouched -- so this is the distinction the verdict exists to
    carry.
    """
    unknown = r.evaluate(listing(quantity=None))
    empty = r.evaluate(listing(quantity=0))
    assert unknown["warnings"] != empty["warnings"]
    assert unknown["warnings"] == [r.UNKNOWN_INVENTORY]
    assert empty["warnings"] == [r.OUT_OF_STOCK]
    # Both fail closed for checkout. Not knowing is not a yes.
    assert unknown["checkout_ready"] is False
    assert empty["checkout_ready"] is False


@pytest.mark.parametrize("product_type", list(r.STOCKLESS_PRODUCT_TYPES))
def test_a_listing_with_no_stock_concept_reports_no_stock_state(product_type):
    """A course does not run out. An absent quantity on one of these is not a
    fact about stock, and reporting it as one would mark every course in the
    store unavailable."""
    verdict = r.evaluate(listing(listing_type=product_type,
                                 product_type=product_type, quantity=None))
    assert verdict == {"publishable": True, "checkout_ready": True,
                       "blockers": [], "warnings": []}


# --- the columns a real row actually carries ----------------------------------
def test_the_default_column_values_do_not_make_everything_stockless():
    """The bug this test was written after, and the reason the fixture above
    sets `listing_type` rather than only `product_type`.

    `marketplace_listings` declares BOTH ``product_type`` and ``delivery_type``
    as ``TEXT DEFAULT 'digital'``, so a physical lamp inserted by the modern
    write path -- which sets ``listing_type`` -- carries 'digital' in the other
    two columns. A first version of this module matched on those two, called
    every listing in the store stockless, and reported no inventory state at all
    for any of them. Nothing in the unit tests caught it, because the fixture
    passed ``product_type`` directly and so agreed with the mistake; the route
    test, which inserts a row and lets the defaults apply, is what failed.

    So this asserts against the row as the database actually hands it over.
    """
    row_as_stored = {
        "title": "Brass desk lamp", "category": "Home",
        "cover_image_url": "https://cdn.example/lamp.jpg",
        "price_label": "$24.00", "approval_status": "approved", "status": "active",
        "listing_type": "physical",   # what the write path sets
        "product_type": "digital",    # TEXT DEFAULT 'digital'
        "delivery_type": "digital",   # TEXT DEFAULT 'digital'
        "quantity": 0,
    }
    assert r.evaluate(row_as_stored)["warnings"] == [r.OUT_OF_STOCK]
    assert r.evaluate(dict(row_as_stored, quantity=None))["warnings"] == [
        r.UNKNOWN_INVENTORY]


def test_a_legacy_row_with_no_listing_type_is_still_read_correctly():
    """Rows predating the five-type column have only ``product_type``.

    The type authority resolves an unrecognised value to "physical", which is
    right for its own purpose and would be wrong here for a legacy 'course' --
    every one of them would acquire an inventory state and lose its checkout.
    """
    course = r.evaluate(listing(listing_type=None, product_type="course",
                                quantity=None))
    assert course["warnings"] == []
    assert course["checkout_ready"] is True

    physical = r.evaluate(listing(listing_type=None, product_type="physical",
                                  quantity=None))
    assert physical["warnings"] == [r.UNKNOWN_INVENTORY]

    # An unrecognised legacy value defaults to tracking stock, which is the
    # fail-closed direction: an unanswered question about a thing that might
    # ship is reported, not assumed away.
    mystery = r.evaluate(listing(listing_type=None, product_type="widget",
                                 quantity=None))
    assert mystery["warnings"] == [r.UNKNOWN_INVENTORY]


def test_the_type_vocabulary_is_the_shared_one():
    """`STOCKLESS_LISTING_TYPES` must name real listing types, or the check
    silently never fires. This fails if the five-type vocabulary is renamed."""
    from services import marketplace_listing_types as types

    for name in r.STOCKLESS_LISTING_TYPES:
        assert name in types.LISTING_TYPES, (
            f"{name!r} is not a listing type, so nothing will ever match it")
    # "physical" is the only type that tracks stock, so it must NOT be listed.
    assert "physical" not in r.STOCKLESS_LISTING_TYPES
    assert set(types.LISTING_TYPES) - set(r.STOCKLESS_LISTING_TYPES) == {"physical"}


# --- missing is not free ------------------------------------------------------
@pytest.mark.parametrize("label", ["", None, "   ", "Request access", "Ask for a quote"])
def test_a_listing_with_no_figures_in_its_price_has_no_price(label):
    """Blank and wordy labels both read as no price, because that is what
    checkout already does with them: `parse_price_label_to_cents` maps both to
    zero cents, so neither promises money. A verdict that called "Request
    access" a price would clear a listing whose checkout refuses."""
    verdict = r.evaluate(listing(price_label=label))
    assert verdict["blockers"] == [r.MISSING_PRICE]
    assert verdict["publishable"] is False
    assert verdict["checkout_ready"] is False


def test_a_missing_price_is_never_reported_as_a_price():
    """The verdict must give the merchant a name for the gap. Rendering silence
    (what the row does today) or a zero (what a coercion would do) are the two
    ways this goes wrong, and neither is available: the only thing the object
    says about an unpriced listing is MISSING_PRICE."""
    verdict = r.evaluate(listing(price_label=""))
    flat = repr(verdict)
    assert "0.00" not in flat and "Free" not in flat and "free" not in flat
    assert r.MISSING_PRICE in verdict["blockers"]


# --- the remaining blockers ---------------------------------------------------
@pytest.mark.parametrize("field,value,code", [
    ("title", "", "MISSING_TITLE"),
    ("title", "   ", "MISSING_TITLE"),
    ("category", None, "MISSING_CATEGORY"),
    ("approval_status", "rejected", "RESTRICTED_PRODUCT"),
    ("approval_status", "suspended", "RESTRICTED_PRODUCT"),
])
def test_one_broken_fact_yields_exactly_its_own_blocker(field, value, code):
    verdict = r.evaluate(listing(**{field: value}))
    assert verdict["blockers"] == [getattr(r, code)]
    assert verdict["publishable"] is False


def test_media_counts_from_either_the_cover_column_or_the_attached_rows():
    """The serializer shows a cover from `cover_image_url` even with no media
    rows attached, so a verdict that demanded rows would print NO_VALID_MEDIA
    underneath a visible photograph."""
    no_media = r.evaluate(listing(cover_image_url="", media_url=""))
    assert no_media["blockers"] == [r.NO_VALID_MEDIA]

    # Either source alone is enough.
    assert r.evaluate(listing(cover_image_url="https://cdn/x.jpg"))["blockers"] == []
    assert r.evaluate(listing(cover_image_url="", media_url="https://cdn/y.jpg")
                      )["blockers"] == []
    assert r.evaluate(listing(cover_image_url="", media_url=""),
                      media=[{"media_url": "https://cdn/z.jpg"}])["blockers"] == []


def test_every_blocker_is_reported_at_once():
    """A merchant fixing one problem, re-submitting, and discovering the next is
    the experience this avoids -- the same reason the supplier evaluator returns
    a list rather than the first failure."""
    verdict = r.evaluate(listing(title="", category="", cover_image_url="",
                                 media_url="", price_label=""))
    assert set(verdict["blockers"]) == {
        r.MISSING_TITLE, r.MISSING_CATEGORY, r.NO_VALID_MEDIA, r.MISSING_PRICE}
    assert verdict["publishable"] is False


# --- checkout_ready as a property, not a restatement --------------------------
def test_checkout_ready_is_false_whenever_anything_blocks_checkout():
    """Asserted over every state the evaluator can actually reach.

    Deliberately not a list of expected booleans: that would restate the rule,
    and a restatement passes against a wrong rule that agrees with itself. The
    property is the invariant -- if any code present in the verdict is one this
    module classifies as blocking checkout, `checkout_ready` must be false --
    and it is checked against verdicts produced by the real evaluator.
    """
    cases = [
        listing(),
        listing(quantity=None), listing(quantity=0), listing(quantity=1),
        listing(quantity=6), listing(quantity="oops"),
        listing(title=""), listing(category=""), listing(price_label=""),
        listing(cover_image_url="", media_url=""),
        listing(approval_status="rejected"),
        listing(product_type="digital", quantity=None),
        listing(title="", quantity=0),
    ]
    saw_true = saw_false = False
    for case in cases:
        verdict = r.evaluate(case)
        present = set(verdict["blockers"]) | set(verdict["warnings"])
        blocked = bool(present & r.CHECKOUT_BLOCKING)
        assert verdict["checkout_ready"] is not blocked, (
            f"{case.get('title')!r}/qty={case.get('quantity')!r}: "
            f"checkout_ready={verdict['checkout_ready']} with {sorted(present)}")
        saw_true = saw_true or verdict["checkout_ready"]
        saw_false = saw_false or not verdict["checkout_ready"]
    # A property test that only ever saw one answer has proved nothing.
    assert saw_true and saw_false, "the cases must cover both verdicts"


def test_checkout_ready_requires_publishable():
    """Nothing unpublishable is purchasable, whatever the stock says."""
    verdict = r.evaluate(listing(title="", quantity=40))
    assert verdict["publishable"] is False
    assert verdict["checkout_ready"] is False


def test_low_stock_warns_without_closing_checkout():
    """The one warning that does not block: buyers can still buy the last few."""
    verdict = r.evaluate(listing(quantity=2))
    assert verdict["warnings"] == [r.LOW_STOCK]
    assert verdict["publishable"] is True and verdict["checkout_ready"] is True
    assert r.LOW_STOCK not in r.CHECKOUT_BLOCKING


# --- the seller's business stays the seller's ---------------------------------
def test_a_verdict_carries_no_money_and_no_supplier_facts():
    """A verdict is codes and booleans. Nothing in it can leak supplier cost,
    margin, a token, an openId or a connection id, because none of those are in
    the object at all -- which is what makes it safe to render anywhere."""
    verdict = r.evaluate(listing(quantity=0, price_label=""))
    assert set(verdict) == {"publishable", "checkout_ready", "blockers", "warnings"}
    assert all(isinstance(code, str) for code in
               verdict["blockers"] + verdict["warnings"])
    forbidden = ("cost", "margin", "supplier", "token", "openid", "connection",
                 "cents", "profit")
    flat = repr(verdict).lower()
    for word in forbidden:
        assert word not in flat, f"{word!r} has no business in a readiness verdict"


def test_the_threshold_lives_on_the_server():
    """The client owned a copy of this number, which meant the quantity a
    merchant saw called low and the quantity the server believed was low were
    two independent facts."""
    assert r.LOW_STOCK_THRESHOLD == 5
    assert r.evaluate(listing(quantity=r.LOW_STOCK_THRESHOLD))["warnings"] == [r.LOW_STOCK]
    assert r.evaluate(listing(quantity=r.LOW_STOCK_THRESHOLD + 1))["warnings"] == []
