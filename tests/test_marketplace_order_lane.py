"""The lane an *order* was placed on, measured on the payload that is served.

The listing side of this was fixed first: `delivery_options` is the seller's
declaration, `delivery_type` holds the product type, and every surface now folds
down from one rule. That left the order side, which is a different mistake with
the same shape.

`ordersDashboard.variantOf` chose the buyer's progress strip by reading
`order.delivery_type`, falling back to `order.listing.delivery_type`. Neither
field has ever been served. `pulse_buyer_order_response` names its listing
columns explicitly and `delivery_type` is not among them, and nothing adds a
top-level one. So the argument was always `undefined`, the `"pickup"` branch was
unreachable, and every order in the app — pickup, shipping, digital, booking —
rendered the shipping timeline. `escrowPresentable` is `variant === "pickup"`,
so the escrow/safety panel was unreachable UI.

The seller's copy had its own version: `variantOf(String(order.item_type || ""))`
passed a row kind into a parameter named `deliveryType`. `item_type` reads
"marketplace_product" on every marketplace row, which matches no lane word, so
that path was shipping-only by construction too.

The fix is not a seventh derivation. Checkout already freezes the *settled* kind
onto the transaction — `marketplace_fulfillment.snapshot`, whose docstring says
it exists so an order read back next year still says where it was going — and
`pulse_buyer_order_response` has been parsing that metadata all along without
ever reading the key. So the order serializers now serve it, and the client folds
down from that.

Why the frozen value rather than a fresh look at the listing: it is post
`resolve_choice`. A listing offering both lanes resolves to `shipping_or_pickup`,
and only the buyer's answer narrows it. Re-deriving recovers the ambiguity, not
the choice. And a seller can edit or delist the item after the sale.

Every assertion below therefore runs through the real checkout route and reads
the real order endpoints. Nothing here builds an order dict by hand.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="order_lane_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_fulfillment as mf  # noqa: E402

SELLER = 99801
BUYER = 99802
NOW = "2026-09-12T00:00:00"

# What a pickup order obliges the buyer to answer, and what a shipping one does.
PICKUP_DETAILS = {"contact_name": "Ada Probe", "contact_phone": "+15125550123"}
SHIPPING_DETAILS = {
    "contact_name": "Ada Probe",
    "contact_phone": "+15125550123",
    "address_line1": "1 Main St",
    "address_city": "Austin",
    "address_region": "TX",
    "address_postal_code": "78701",
    "address_country": "US",
}


@pytest.fixture(scope="module", autouse=True)
def _app():
    bot.init_db()
    conn = sqlite3.connect(_DB)
    conn.execute(
        "INSERT INTO marketplace_sellers (user_id,status,display_name,created_at,updated_at) "
        "VALUES (?,'approved','Order Lane Store',?,?)",
        (SELLER, NOW, NOW),
    )
    conn.commit()
    conn.close()
    bot.webhook_app.config["TESTING"] = True
    yield
    os.unlink(_DB)


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _as(user_id, username):
    bot.api_account_user = lambda *a, **k: {"user_id": user_id, "username": username}
    return bot.webhook_app.test_client()


def _cover():
    conn = _db()
    conn.execute(
        "INSERT INTO marketplace_product_media "
        "(merchant_id, product_id, media_url, media_type, is_cover, moderation_status, created_at) "
        "VALUES (?,0,?,'image',1,'approved',?)",
        (SELLER, "https://cdn.example/cover.jpg", NOW),
    )
    media_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()
    return media_id


def publish(delivery_options=None, product_type="physical", **metadata):
    """Publish and approve a listing through the real create route."""
    meta = {"condition": "new", "location": "Testville"}
    meta.update(metadata)
    if delivery_options is not None:
        meta["delivery_options"] = delivery_options
    response = _as(SELLER, "order_lane_seller").post(
        "/api/pulse/marketplace/listings/create",
        json={
            "title": f"Order lane {delivery_options or product_type}",
            "description": "d" * 40, "short_description": "s", "category": "Home",
            "price": "25.00", "currency": "USD", "quantity": 5,
            "product_type": product_type, "listing_type": product_type,
            "submission_action": "submit", "media_ids": [_cover()],
            "listing_metadata": meta,
        },
    )
    assert response.status_code == 200, response.get_json()
    conn = _db()
    listing_id = int(conn.execute(
        "SELECT id FROM marketplace_listings ORDER BY id DESC LIMIT 1").fetchone()[0])
    # Approved and priced, so the checkout route will take it. `price_label` is
    # what checkout parses; the create route stores the price elsewhere.
    conn.execute(
        "UPDATE marketplace_listings SET status='published', approval_status='approved', "
        "price_label='$25.00' WHERE id=?", (listing_id,))
    conn.commit()
    conn.close()
    return listing_id


def buy(listing_id, details, lane=None):
    """Check out as the buyer through the real payments route."""
    body = {
        "item_type": "marketplace_product", "item_id": listing_id, "quantity": 1,
        "payment_mode": "cash", "fulfillment_details": details,
    }
    if lane is not None:
        body["fulfillment"] = lane
    response = _as(BUYER, "order_lane_buyer").post("/api/pulse/payments/checkout", json=body)
    assert response.status_code == 200, response.get_json()
    conn = _db()
    tx_id = int(conn.execute(
        "SELECT id FROM seller_transactions ORDER BY id DESC LIMIT 1").fetchone()[0])
    conn.close()
    return tx_id


def buyer_order(tx_id):
    payload = _as(BUYER, "order_lane_buyer").get("/api/pulse/orders?limit=50").get_json()
    for order in payload.get("orders") or []:
        if int(order.get("id") or 0) == tx_id:
            return order
    raise AssertionError(f"order {tx_id} not in buyer payload")


def seller_order(tx_id):
    payload = _as(SELLER, "order_lane_seller").get("/api/pulse/payments/seller/orders").get_json()
    for order in payload.get("orders") or []:
        if int(order.get("id") or 0) == tx_id:
            return order
    raise AssertionError(f"order {tx_id} not in seller payload")


class TestTheFieldTheClientWasReading:
    """First: prove the old input never existed, so the branch was unreachable."""

    def test_no_order_payload_carries_a_delivery_type(self):
        order = buyer_order(buy(publish("pickup"), PICKUP_DETAILS))
        assert "delivery_type" not in order
        assert "delivery_type" not in (order.get("listing") or {})

    def test_the_seller_payload_has_no_lane_of_its_own_either(self):
        # `item_type` is what the seller path passed to a lane parameter. It is
        # the same word on every marketplace row, so it could not distinguish
        # anything — which is what made that timeline shipping-only.
        order = seller_order(buy(publish("pickup"), PICKUP_DETAILS))
        assert order["item_type"] == "marketplace_product"
        assert "delivery_type" not in order


class TestTheOrderSaysWhichLaneItWasPlacedOn:
    def test_a_pickup_order_is_served_as_pickup(self):
        # The defect in one line: this had no field to answer from at all, so
        # the buyer's timeline said "On its way" about an item they had arranged
        # to collect in person.
        order = buyer_order(buy(publish("pickup"), PICKUP_DETAILS))
        assert order["fulfillment_kind"] == "pickup"

    def test_a_shipping_order_is_served_as_shipping(self):
        order = buyer_order(buy(publish("shipping"), SHIPPING_DETAILS))
        assert order["fulfillment_kind"] == "shipping"

    def test_a_digital_order_is_not_described_as_a_parcel(self):
        order = buyer_order(buy(publish(None, product_type="digital"), {}))
        assert order["fulfillment_kind"] == "digital"

    def test_the_seller_sees_the_same_lane_as_the_buyer(self):
        # Two endpoints, two code paths, one order. They disagreeing is how a
        # seller posts an item the buyer is on their way to collect.
        tx_id = buy(publish("pickup"), PICKUP_DETAILS)
        assert seller_order(tx_id)["fulfillment_kind"] == buyer_order(tx_id)["fulfillment_kind"]
        assert seller_order(tx_id)["fulfillment_kind"] == "pickup"


class TestAnOpenLaneIsServedAsTheBuyersAnswer:
    """The case re-deriving from the listing cannot get right."""

    @pytest.mark.parametrize(
        "chosen,details", [("pickup", PICKUP_DETAILS), ("shipping", SHIPPING_DETAILS)])
    def test_the_settled_choice_is_what_the_order_carries(self, chosen, details):
        listing_id = publish("both")
        # The listing itself is undecided, and stays undecided.
        conn = _db()
        row = dict(conn.execute(
            "SELECT * FROM marketplace_listings WHERE id=?", (listing_id,)).fetchone())
        conn.close()
        assert mf.resolve_kind(row["listing_type"], row["delivery_type"],
                               json.loads(row["listing_metadata_json"])) == "shipping_or_pickup"

        order = buyer_order(buy(listing_id, details, lane=chosen))
        assert order["fulfillment_kind"] == chosen

    def test_an_undecided_kind_never_reaches_an_order(self):
        # `resolve_choice` settles the lane before the transaction row exists,
        # so no order can carry one. If one ever did, the client would have to
        # render a timeline for a lane nobody picked.
        for chosen, details in (("pickup", PICKUP_DETAILS), ("shipping", SHIPPING_DETAILS)):
            order = buyer_order(buy(publish("both"), details, lane=chosen))
            assert order["fulfillment_kind"] not in mf.UNDECIDED_KINDS


class TestTheOrderKeepsItsOwnHistory:
    def test_editing_the_listing_afterwards_does_not_move_the_order(self):
        # Why the frozen kind, and not a fresh read of the listing: the order is
        # a record of a thing that happened. The seller relisting the item for
        # shipping cannot retroactively make a completed pickup a parcel.
        listing_id = publish("pickup")
        tx_id = buy(listing_id, PICKUP_DETAILS)
        conn = _db()
        meta = json.loads(conn.execute(
            "SELECT listing_metadata_json FROM marketplace_listings WHERE id=?",
            (listing_id,)).fetchone()[0])
        meta["delivery_options"] = "shipping"
        conn.execute("UPDATE marketplace_listings SET listing_metadata_json=? WHERE id=?",
                     (json.dumps(meta), listing_id))
        conn.commit()
        conn.close()

        assert buyer_order(tx_id)["fulfillment_kind"] == "pickup"
        assert seller_order(tx_id)["fulfillment_kind"] == "pickup"

    def test_a_deleted_listing_still_leaves_the_order_readable(self):
        listing_id = publish("pickup")
        tx_id = buy(listing_id, PICKUP_DETAILS)
        conn = _db()
        conn.execute("DELETE FROM marketplace_listings WHERE id=?", (listing_id,))
        conn.commit()
        conn.close()
        assert buyer_order(tx_id)["fulfillment_kind"] == "pickup"


class TestTheLegacyFallback:
    """Rows written before the snapshot existed, which is most of the table."""

    def test_an_order_with_no_frozen_kind_reads_the_listing(self):
        listing_id = publish("pickup")
        tx_id = buy(listing_id, PICKUP_DETAILS)
        conn = _db()
        # Strip the snapshot, leaving the row as a pre-fulfilment order.
        metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM seller_transactions WHERE id=?", (tx_id,)).fetchone()[0])
        metadata.pop("fulfillment", None)
        conn.execute("UPDATE seller_transactions SET metadata_json=? WHERE id=?",
                     (json.dumps(metadata), tx_id))
        conn.commit()
        conn.close()
        assert buyer_order(tx_id)["fulfillment_kind"] == "pickup"

    def test_a_legacy_order_whose_listing_is_gone_claims_nothing(self):
        # Empty, not "shipping". An order with neither a snapshot nor a listing
        # genuinely does not record a lane, and the client's own default is the
        # right place to decide what to draw for one — not a guess invented here
        # and served as though it were a fact.
        listing_id = publish("pickup")
        tx_id = buy(listing_id, PICKUP_DETAILS)
        conn = _db()
        metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM seller_transactions WHERE id=?", (tx_id,)).fetchone()[0])
        metadata.pop("fulfillment", None)
        conn.execute("UPDATE seller_transactions SET metadata_json=? WHERE id=?",
                     (json.dumps(metadata), tx_id))
        conn.execute("DELETE FROM marketplace_listings WHERE id=?", (listing_id,))
        conn.commit()
        conn.close()
        assert buyer_order(tx_id)["fulfillment_kind"] == ""

    def test_the_seller_list_has_no_listing_to_fall_back_to_and_says_so(self):
        # `api_payments_list_seller_orders` joins no listing at all, by design —
        # it is a raw transaction list. So a stripped row answers empty there
        # even while the buyer's endpoint can still recover it.
        tx_id = buy(publish("pickup"), PICKUP_DETAILS)
        conn = _db()
        metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM seller_transactions WHERE id=?", (tx_id,)).fetchone()[0])
        metadata.pop("fulfillment", None)
        conn.execute("UPDATE seller_transactions SET metadata_json=? WHERE id=?",
                     (json.dumps(metadata), tx_id))
        conn.commit()
        conn.close()
        assert seller_order(tx_id)["fulfillment_kind"] == ""


class TestOrderKindItself:
    """The rule, exercised directly on the shapes the routes hand it."""

    def test_the_frozen_kind_wins_over_the_listing(self):
        assert mf.order_kind(
            {"fulfillment": {"kind": "pickup"}},
            {"listing_type": "physical", "listing_metadata": {"delivery_options": "shipping"}},
        ) == "pickup"

    def test_a_junk_kind_is_not_trusted(self):
        # Only a member of KINDS counts. Anything else falls through to the
        # listing rather than being echoed onto the wire as a lane.
        for junk in ["", "  ", "yes", "PICKUP_MAYBE", None, 3, [], {}]:
            assert mf.order_kind(
                {"fulfillment": {"kind": junk}},
                {"listing_type": "physical", "listing_metadata": {"delivery_options": "pickup"}},
            ) == "pickup"

    def test_a_kind_is_matched_case_and_space_insensitively(self):
        assert mf.order_kind({"fulfillment": {"kind": " Pickup "}}) == "pickup"

    def test_metadata_that_is_not_a_dict_is_not_a_lane(self):
        for junk in [None, "", "pickup", 7, [], {"fulfillment": "pickup"}]:
            assert mf.order_kind(junk) == ""

    def test_the_raw_metadata_column_is_parsed_rather_than_dropped(self):
        # `resolve_kind` ignores a metadata string silently. Passing one through
        # would lose `delivery_options` — the seller's entire declaration — and
        # answer "shipping" for a pickup-only listing.
        assert mf.order_kind({}, {
            "listing_type": "physical",
            "listing_metadata_json": json.dumps({"delivery_options": "pickup"}),
        }) == "pickup"
        assert mf.order_kind({}, {
            "listing_type": "physical", "listing_metadata_json": "{not json",
        }) == "shipping"

    def test_no_listing_and_no_snapshot_is_empty_not_shipping(self):
        assert mf.order_kind({}) == ""
        assert mf.order_kind({}, {}) == ""
        assert mf.order_kind({}, None) == ""
