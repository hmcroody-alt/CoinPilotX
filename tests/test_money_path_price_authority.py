"""What Buy Now charges when the shelf price and the seller's label disagree.

Every buyer-facing surface prices a listing through
``services.marketplace_web.derive_price``, which prefers
``marketplace_listing_variants.price_cents`` and falls back to ``price_label``.
Every checkout lane priced it by parsing ``price_label`` alone. Measured against
production on 2026-09-29, over 123 listings of which 117 carry live variants:

* 82 rows have an **empty** ``price_label`` while their variants carry a real
  price. The product card showed $117.75 (listing 45), $68.53 (56), $56.67 (44);
  checkout parsed "" to zero and answered "This item is currently free or not
  priced for checkout." Eighty-two priced, published, approved products could
  not be bought at all.
* 3 rows parse a label that disagrees with the displayed amount: listing 35
  charged $35.00 against a $14.33 shelf, 36 charged $38.00 against $2.29, 112
  charged $29.31 against $27.84.
* 17 rows have variants spanning more than one price — the widest
  $6.25..$49.40 — and the request body has no ``variant_id`` field, so picking
  Large could not move the charged amount by construction.

The tests below measure the amount the route actually charges and the amount it
records, against a listing whose label and variants say different things. A test
that only asserted ``resolve_unit_price`` in isolation would have stayed green
through the entire defect, because the authority was never the broken part — the
checkout lane simply did not ask it.
"""

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="price_authority_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_price_authority as authority  # noqa: E402
from services import marketplace_web  # noqa: E402

SELLER, BUYER = 99811, 99812
NOW = "2026-09-29T00:00:00"

#: The brief's own example: one listing, two sizes, two prices.
SMALL_CENTS = 1588
LARGE_CENTS = 1940

SHIPPING = {
    "contact_name": "Authority Buyer",
    "contact_phone": "+15555550124",
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
        "VALUES (?,'approved','Authority Store',?,?)",
        (SELLER, NOW, NOW),
    )
    conn.commit()
    conn.close()
    bot.api_account_user = lambda *a, **k: {
        "user_id": BUYER, "username": "authority_buyer", "email": "pa@example.com"}
    bot.webhook_app.config["TESTING"] = True
    yield
    os.unlink(_DB)


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


_NEXT_ID = [91000]


def _listing(price_label="", stock=10, **overrides):
    """A published, approved, physical listing.

    ``delivery_type`` is explicit: the column defaults to ``'digital'`` and
    ``resolve_kind`` reads it first, so a row that omits it takes the digital
    lane — no address, no stock movement — and would hollow out the assertions.
    """
    _NEXT_ID[0] += 1
    listing_id = _NEXT_ID[0]
    row = {
        "id": listing_id, "seller_user_id": SELLER, "title": "Probe blouse",
        "description": "d", "category": "Women's Clothing", "price_label": price_label,
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


def _variant(listing_id, price_cents, variant_key="Black-S", status="active"):
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO marketplace_listing_variants "
        "(listing_id,seller_user_id,variant_key,price_cents,currency,status,created_at,updated_at) "
        "VALUES (?,?,?,?,'USD',?,?,?)",
        (listing_id, SELLER, variant_key, price_cents, status, NOW, NOW),
    )
    variant_id = cur.lastrowid
    conn.commit()
    conn.close()
    return variant_id


def _variants_of(listing_id):
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, listing_id, price_cents, currency, status "
        "FROM marketplace_listing_variants WHERE listing_id=?", (listing_id,)).fetchall()]
    conn.close()
    return rows


def _listing_row(listing_id):
    conn = _db()
    row = dict(conn.execute("SELECT * FROM marketplace_listings WHERE id=?",
                            (listing_id,)).fetchone())
    conn.close()
    return row


def _buy(listing_id, **extra):
    body = {
        "item_type": "marketplace_product",
        "item_id": listing_id,
        # Cash is the only live Marketplace lane; card starts are hard-paused in
        # `services/marketplace_payment_pause.py`, so a card body here would be
        # refused at the pause and never reach the pricing code under test.
        "payment_mode": "cash",
        "fulfillment_details": dict(SHIPPING),
    }
    body.update(extra)
    return bot.webhook_app.test_client().post("/api/pulse/payments/checkout", json=body)


def _latest_tx():
    conn = _db()
    row = conn.execute("SELECT * FROM seller_transactions ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# The charge
# ---------------------------------------------------------------------------

def test_a_listing_priced_only_in_its_variants_can_be_bought():
    """The 82-listing case. ``price_label`` is empty and the price is real."""
    listing_id = _listing(price_label="")
    _variant(listing_id, SMALL_CENTS)

    resp = _buy(listing_id)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["amount_cents"] == SMALL_CENTS
    assert _latest_tx()["amount_cents"] == SMALL_CENTS


def test_the_variant_price_wins_over_a_label_that_disagrees():
    """Production listing 35: label $35.00, shelf $14.33. The shelf is the price.

    The label is a display string a human typed; the variant is what the supplier
    sync writes and what every buyer surface renders. Charging the label
    overcharged this buyer by $20.67 on a single unit.
    """
    listing_id = _listing(price_label="$35.00")
    _variant(listing_id, 1433)

    resp = _buy(listing_id)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["amount_cents"] == 1433


def test_picking_the_large_charges_for_the_large():
    """§12. Selecting a variant has to move the authoritative amount."""
    listing_id = _listing(price_label="")
    _variant(listing_id, SMALL_CENTS, "Black-S")
    large_id = _variant(listing_id, LARGE_CENTS, "Black-L")

    resp = _buy(listing_id, variant_id=large_id)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["amount_cents"] == LARGE_CENTS
    assert _latest_tx()["amount_cents"] == LARGE_CENTS


def test_a_multi_price_listing_refuses_rather_than_guessing_a_variant():
    """The 17-listing case, with no selection supplied.

    Charging the low bound undercharges the seller and the high bound
    overcharges the buyer, and the buyer's screen showed a range, so no amount
    here is one both parties agreed to. The bounds ship with the refusal because
    the buyer's only move is to pick one.
    """
    listing_id = _listing(price_label="")
    _variant(listing_id, 625, "A")
    _variant(listing_id, 4940, "B")

    resp = _buy(listing_id)

    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body["error_code"] == authority.VARIANT_REQUIRED
    assert body["price_min_cents"] == 625
    assert body["price_max_cents"] == 4940
    # Nothing was recorded and nothing was held: the refusal is free.
    assert _latest_tx().get("listing_id") != listing_id


def test_an_unpriced_listing_is_refused_by_its_own_name():
    """§61. No price anywhere, so no amount is fabricated."""
    listing_id = _listing(price_label="")

    resp = _buy(listing_id)

    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["error_code"] == authority.NO_PRICE


def test_a_variant_that_is_gone_is_not_silently_swapped_for_a_cheaper_one():
    listing_id = _listing(price_label="$35.00")
    _variant(listing_id, SMALL_CENTS, "Black-S")

    resp = _buy(listing_id, variant_id=999999)

    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["error_code"] == authority.NO_PRICE


def test_an_archived_variant_does_not_price_a_listing():
    listing_id = _listing(price_label="")
    _variant(listing_id, SMALL_CENTS, "Black-S", status="archived")

    resp = _buy(listing_id)

    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["error_code"] == authority.NO_PRICE


def test_quantity_multiplies_the_authoritative_unit_price():
    listing_id = _listing(price_label="$35.00")
    _variant(listing_id, 1433)

    resp = _buy(listing_id, quantity=3)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["amount_cents"] == 3 * 1433


# ---------------------------------------------------------------------------
# The invariant the defect violated
# ---------------------------------------------------------------------------

def test_the_shelf_and_the_charge_agree_on_every_shape_of_row():
    """What the buyer is shown and what checkout charges, from one authority.

    ``derive_price`` is what the product page, the Home rail, the storefront and
    the cart card render. This asserts the two answers are the same number, which
    is the property that was false on 85 of 123 production rows.
    """
    for label, variant_cents in (("", SMALL_CENTS), ("$35.00", 1433), ("$19.99", None)):
        listing_id = _listing(price_label=label)
        if variant_cents is not None:
            _variant(listing_id, variant_cents)
        row = _listing_row(listing_id)
        variants = _variants_of(listing_id)

        shown = marketplace_web.derive_price(row, variants)
        charged = authority.resolve_unit_price(row, variants)

        assert charged.ok, (label, variant_cents)
        assert charged.unit_price_minor == shown.min_cents, (label, variant_cents)
        assert charged.currency == shown.currency


def test_the_authority_keeps_the_label_parsers_ceiling():
    """The label lane clamps at ``bot.MAX_PRICE_LABEL_CENTS``; so must this one.

    Routing checkout onto the variant column would otherwise have quietly
    removed a ceiling that a downstream ``int`` column and Stripe's own maximum
    both depend on.
    """
    assert authority.MAX_UNIT_PRICE_MINOR == bot.MAX_PRICE_LABEL_CENTS
    decision = authority.resolve_unit_price(
        {"currency": "USD"},
        [{"id": 1, "price_cents": 10 ** 12, "currency": "USD", "status": "active"}],
    )
    assert decision.unit_price_minor == bot.MAX_PRICE_LABEL_CENTS
