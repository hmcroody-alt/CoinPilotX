"""What Buy Now charges, holds and records when the buyer picks more than one.

Until this file existed, nothing in the repository posted to
``/api/pulse/payments/checkout``. The one test file covering the route read its
source text instead, and among the strings it asserted on was
``"quantity=quantity-1"`` — the hardcoded single unit that made every multi-unit
Buy Now order wrong. The test was green because the defect was its subject.

The defect had four faces, and they only line up when the route is run:

* the product screen multiplies the unit price by the stepper value and shows
  the buyer that total, so a buyer picking three saw $75.00;
* the request carried no quantity at all, so the server charged $25.00;
* the shelf came down by one, so the other two units were sold again; and
* ``marketplace_orders.quantity`` recorded 1 — the same column
  ``fulfillment.create_intent`` compares a supplier line against, so no
  multi-unit dropship order could ever be dispatched.

Every assertion below therefore measures an outcome: money charged, units held,
units left, and the ledger row the fulfillment layer reads.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="buy_now_qty_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_cart_routes as cart  # noqa: E402

SELLER, BUYER = 99801, 99802
NOW = "2026-09-11T00:00:00"
UNIT_CENTS = 2500

#: A valid shipping submission. A physical listing resolves to the ``shipping``
#: fulfillment kind, which refuses checkout without one — so an incomplete
#: address here would make every test below pass for the wrong reason (HTTP 400,
#: no charge, no stock movement, all the "did not oversell" assertions trivially
#: satisfied). The first test asserts HTTP 200 explicitly to keep that honest.
SHIPPING = {
    "contact_name": "Quantity Buyer",
    "contact_phone": "+15555550123",
    "address_line1": "1 Probe Street",
    "address_city": "Testville",
    "address_region": "CA",
    "address_postal_code": "90001",
    "address_country": "US",
}


@pytest.fixture(scope="module", autouse=True)
def _app():
    bot.init_db()
    conn = sqlite3.connect(_DB)
    conn.execute(
        "INSERT INTO marketplace_sellers (user_id,status,display_name,created_at,updated_at) "
        "VALUES (?,'approved','Quantity Store',?,?)",
        (SELLER, NOW, NOW),
    )
    conn.commit()
    conn.close()
    bot.api_account_user = lambda *a, **k: {
        "user_id": BUYER, "username": "qty_buyer", "email": "qty@example.com"}
    bot.webhook_app.config["TESTING"] = True
    yield
    os.unlink(_DB)


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


_NEXT_ID = [90000]


def _listing(stock=10, **overrides):
    """A published, approved, physical listing with a known shelf.

    ``delivery_type`` is set explicitly. The column defaults to ``'digital'`` and
    ``resolve_kind`` reads it before anything else, so a row that omits it is a
    digital order: no address asked, no stock decrement, no reservation — which
    would quietly hollow out every test in this file.
    """
    _NEXT_ID[0] += 1
    listing_id = _NEXT_ID[0]
    row = {
        "id": listing_id, "seller_user_id": SELLER, "title": "Probe bed",
        "description": "d", "category": "Home", "price_label": "$25.00",
        "currency": "USD", "quantity": stock, "status": "published",
        "approval_status": "approved", "listing_type": "physical",
        "product_type": "physical", "delivery_type": "physical",
        "created_at": NOW, "updated_at": NOW,
    }
    row.update(overrides)
    conn = _db()
    conn.execute(
        f"INSERT INTO marketplace_listings ({','.join(row)}) "
        f"VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )
    conn.commit()
    conn.close()
    return listing_id


def _buy(listing_id, quantity=None, **extra):
    body = {
        "item_type": "marketplace_product",
        "item_id": listing_id,
        "payment_mode": "cash",
        "fulfillment_details": dict(SHIPPING),
    }
    if quantity is not None:
        body["quantity"] = quantity
    body.update(extra)
    return bot.webhook_app.test_client().post("/api/pulse/payments/checkout", json=body)


def _latest_tx():
    conn = _db()
    row = conn.execute("SELECT * FROM seller_transactions ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {}


def _stock(listing_id):
    conn = _db()
    row = conn.execute("SELECT quantity FROM marketplace_listings WHERE id=?", (listing_id,)).fetchone()
    conn.close()
    return row["quantity"]


def _reserved(tx_id):
    conn = _db()
    row = conn.execute(
        "SELECT quantity FROM marketplace_inventory_reservations WHERE seller_transaction_id=?",
        (tx_id,),
    ).fetchone()
    conn.close()
    return row["quantity"] if row else None


# ---------------------------------------------------------------------------
# The money, the shelf and the hold
# ---------------------------------------------------------------------------

def test_buying_three_charges_for_three():
    listing_id = _listing(stock=10)
    resp = _buy(listing_id, 3)

    assert resp.status_code == 200, resp.get_json()
    # The number the product screen already showed the buyer: 3 x $25.00.
    assert resp.get_json()["amount_cents"] == 3 * UNIT_CENTS
    assert _latest_tx()["amount_cents"] == 3 * UNIT_CENTS


def test_buying_three_takes_three_off_the_shelf():
    listing_id = _listing(stock=10)
    assert _buy(listing_id, 3).status_code == 200
    # Not nine. The two units the old route left behind were still discoverable
    # and still sellable after they had been paid for.
    assert _stock(listing_id) == 7


def test_the_hold_covers_every_unit_the_buyer_paid_for():
    """The reservation quantity is what a failed payment gives back.

    ``release_inventory_reservation`` reads this column, not the Stripe
    metadata. A hold of 1 against a charge for 3 means a declined card returns
    one unit to a shelf that lost three.
    """
    listing_id = _listing(stock=10)
    assert _buy(listing_id, 3).status_code == 200
    assert _reserved(_latest_tx()["id"]) == 3


def test_a_failed_payment_returns_every_unit_it_held():
    listing_id = _listing(stock=10)
    assert _buy(listing_id, 3).status_code == 200
    tx_id = _latest_tx()["id"]
    assert _stock(listing_id) == 7

    conn = _db()
    cart.settle_failed_transactions(
        conn.cursor(), [tx_id], reason="checkout_error",
        terminal_status="checkout_failed", now=NOW)
    conn.commit()
    conn.close()

    assert _stock(listing_id) == 10, "the card was not charged and the units did not come back"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_a_shelf_of_two_refuses_an_order_for_three():
    listing_id = _listing(stock=2)
    resp = _buy(listing_id, 3)

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error_code"] == "OUT_OF_STOCK"
    assert body["available_quantity"] == 2
    assert body["requested_quantity"] == 3


def test_the_decrement_refuses_when_the_pre_flight_check_was_stale(monkeypatch):
    """The `quantity>=?` predicate is the one that holds at commit time.

    The pre-flight refusal above is advisory: it reads a listing row that was
    fetched earlier in the request, so between that read and this UPDATE another
    buyer can empty the shelf. Only the conditional decrement settles it, and it
    has to be conditional on the *whole* amount — `quantity>=1` would take a
    shelf of two down to minus one and report success.

    The pre-flight is stubbed to approve rather than raced, because a race this
    test could lose is a race it could also win by accident.
    """
    listing_id = _listing(stock=2)
    monkeypatch.setattr(
        bot.marketplace_listing_lifecycle, "inventory_available", lambda *a, **k: True)
    resp = _buy(listing_id, 3)

    assert resp.status_code == 409
    assert _stock(listing_id) == 2, "the shelf went negative rather than refusing"


def test_a_refused_order_holds_nothing_and_moves_nothing():
    """A refusal that still decremented would be worse than overselling."""
    listing_id = _listing(stock=2)
    before = _latest_tx().get("id")
    assert _buy(listing_id, 3).status_code == 409
    assert _stock(listing_id) == 2
    assert _latest_tx().get("id") == before, "a refused checkout wrote a transaction"


def test_a_quantity_past_the_cart_limit_is_clamped_not_honoured():
    """Buy Now and the cart must not disagree about how much is too much.

    ``MAX_QTY_PER_LINE`` is the cart's ceiling. If this lane accepted more, the
    same listing would have two different maximums depending on which button the
    buyer pressed.
    """
    limit = cart.MAX_QTY_PER_LINE
    listing_id = _listing(stock=limit + 50)
    resp = _buy(listing_id, limit + 5)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["amount_cents"] == limit * UNIT_CENTS
    assert _stock(listing_id) == 50


@pytest.mark.parametrize("quantity", [0, -4, "three", None])
def test_a_missing_or_nonsense_quantity_still_means_one(quantity):
    """Absence must not become zero, and must not become "unknown".

    Every client build in the field today sends no quantity field at all. If an
    absent value stopped meaning one, this fix would break every single-unit
    purchase on the way to fixing multi-unit ones.
    """
    listing_id = _listing(stock=10)
    resp = _buy(listing_id, quantity)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["amount_cents"] == UNIT_CENTS
    assert _stock(listing_id) == 9
    assert _reserved(_latest_tx()["id"]) == 1


# ---------------------------------------------------------------------------
# The ledger row the fulfillment layer reads
# ---------------------------------------------------------------------------

def test_the_paid_order_records_the_quantity_the_supplier_must_ship():
    """``marketplace_orders.quantity`` is what ``create_intent`` compares against.

    ``order_line_mismatch`` refuses to dispatch a supplier line whose quantity
    disagrees with this column. While it always said 1, a three-unit order could
    only ever be dispatched as a one-unit order or refused outright.
    """
    listing_id = _listing(stock=10)
    assert _buy(listing_id, 3).status_code == 200
    tx = _latest_tx()

    conn = _db()
    cur = conn.cursor()
    bot.pulse_upsert_marketplace_order(cur, {**tx, "status": "paid"}, "pi_qty", NOW)
    conn.commit()
    order = dict(cur.execute("SELECT * FROM marketplace_orders ORDER BY id DESC LIMIT 1").fetchone())
    conn.close()

    assert order["quantity"] == 3
    assert order["amount_cents"] == 3 * UNIT_CENTS
    # The unit price is the quote's, not the total divided by the count.
    assert order["unit_price_cents"] == UNIT_CENTS


def test_the_quote_frozen_at_checkout_states_both_facts():
    """The order projection reads the quote rather than deriving the pair again.

    ``qty`` and ``amount // quantity`` were the same facts computed a second
    time, and the division is not a unit price for any order carrying shipping
    or tax.
    """
    listing_id = _listing(stock=10)
    assert _buy(listing_id, 3).status_code == 200
    details = json.loads(_latest_tx()["metadata_json"])

    assert details["qty"] == 3
    quote = details["commercial_quote"]
    assert quote["quantity"] == 3
    assert quote["unit_price_minor"] == UNIT_CENTS


def test_the_order_line_prefers_the_quote_over_a_disagreeing_qty():
    """When the two sources disagree, the priced one wins.

    ``qty`` is a loose metadata key; the quote is what the buyer was charged
    against. A shipping-bearing order divides badly, which is exactly the case
    the old derivation got wrong.
    """
    quantity, unit = bot.marketplace_order_line(
        {"qty": 1, "commercial_quote": {"quantity": 3, "unit_price_minor": UNIT_CENTS}},
        3 * UNIT_CENTS + 999,  # 3 units plus shipping
    )
    assert (quantity, unit) == (3, UNIT_CENTS)


def test_an_order_written_before_quotes_existed_still_projects():
    """Rows already in the ledger have no ``commercial_quote`` at all.

    They must keep projecting the way they always did rather than collapsing to
    zero or raising, which is why the old pair survives as the fallback.
    """
    assert bot.marketplace_order_line({"qty": 2}, 5000) == (2, 2500)
    assert bot.marketplace_order_line({}, 2500) == (1, 2500)
    # A malformed quote is not a quote. Both keys are present and both are
    # truthy here — only their *types* are wrong — because a check written as
    # `if quantity and unit is not None` passes this and writes the string "3"
    # into an integer column, where no comparison the fulfillment layer makes
    # will ever match it.
    assert bot.marketplace_order_line(
        {"qty": 2, "commercial_quote": {"quantity": "3", "unit_price_minor": 2500}},
        5000) == (2, 2500)
    assert bot.marketplace_order_line(
        {"qty": 2, "commercial_quote": {"quantity": 3, "unit_price_minor": "2500"}},
        5000) == (2, 2500)
    # `True` is an int to `isinstance` and is not a quantity.
    assert bot.marketplace_order_line(
        {"qty": 2, "commercial_quote": {"quantity": True, "unit_price_minor": 2500}},
        5000) == (2, 2500)
