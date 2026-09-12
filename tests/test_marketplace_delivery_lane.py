"""The lane the seller chose, measured against a row the publish route wrote.

Every existing test of the lane rule builds its own listing dict, and every one
of them put a lane word — ``pickup``, ``shipping``, ``both`` — into
``delivery_type``. No row in the database has ever contained one. The publish
route's INSERT lists ``delivery_type, product_type`` against
``product_type, product_type``, the CJ importer hardcodes ``'physical'``, and the
column's DDL default is ``'digital'``. So the column reads ``physical`` for every
physical listing, and the expression it fed —

    option = delivery or str(meta.get("delivery_options") or "").strip().lower()

— could never reach its right-hand side. A seller selecting "local pickup only"
had their answer read from a field that did not contain it, and their buyers were
put on shipping and asked for a delivery address. ``shipping_or_pickup`` was
unreachable, so the lane chooser the checkout screen renders had no input that
could summon it, and ``resolve_choice``'s pickup branch was dead code.

None of that was visible from a unit test, because every unit test supplied the
inputs the bug required. So this file does not supply inputs at all: it publishes
through ``/api/pulse/marketplace/listings/create`` and reads the row back.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="delivery_lane_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_cart_routes as cart  # noqa: E402
from services import marketplace_fulfillment as mf  # noqa: E402

SELLER = 99901
NOW = "2026-09-12T00:00:00"


@pytest.fixture(scope="module", autouse=True)
def _app():
    bot.init_db()
    conn = sqlite3.connect(_DB)
    conn.execute(
        "INSERT INTO marketplace_sellers (user_id,status,display_name,created_at,updated_at) "
        "VALUES (?,'approved','Lane Store',?,?)",
        (SELLER, NOW, NOW),
    )
    conn.commit()
    conn.close()
    bot.api_account_user = lambda *a, **k: {
        "user_id": SELLER, "username": "lane_seller", "email": "lane@example.com"}
    bot.webhook_app.config["TESTING"] = True
    yield
    os.unlink(_DB)


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _cover():
    """A cover photo, without which the publish route refuses to create."""
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
    """Publish through the real route and return the stored row as a dict.

    The row is read back from the table rather than from the response, because
    what the lane rule reads is the row.
    """
    meta = {"condition": "new", "location": "Testville"}
    meta.update(metadata)
    if delivery_options is not None:
        meta["delivery_options"] = delivery_options
    response = bot.webhook_app.test_client().post(
        "/api/pulse/marketplace/listings/create",
        json={
            "title": f"Lane probe {delivery_options}", "description": "d" * 40,
            "short_description": "s", "category": "Home", "price": "25.00",
            "currency": "USD", "quantity": 5, "product_type": product_type,
            "listing_type": product_type, "submission_action": "submit",
            "media_ids": [_cover()], "listing_metadata": meta,
        },
    )
    assert response.status_code == 200, response.get_json()
    conn = _db()
    row = dict(conn.execute(
        "SELECT * FROM marketplace_listings ORDER BY id DESC LIMIT 1").fetchone())
    conn.close()
    return row


def kind_of(row):
    meta = json.loads(row.get("listing_metadata_json") or "{}")
    return mf.resolve_kind(row.get("listing_type") or row.get("product_type"),
                           row.get("delivery_type"), meta)


class TestWhatThePublishRouteActuallyStores:
    """Measured, not assumed. §2 of the brief: do not guess from the UI."""

    @pytest.mark.parametrize("option", ["pickup", "shipping", "both"])
    def test_the_delivery_column_never_receives_the_lane(self, option):
        row = publish(option)
        assert row["delivery_type"] == "physical"
        assert json.loads(row["listing_metadata_json"])["delivery_options"] == option

    def test_the_column_holds_the_product_type_for_every_type(self):
        # Not only physical. The same INSERT expression writes the product type
        # into the delivery column for all five listing types, which is why a
        # rule reading it answers a question nobody asked.
        for product_type in ("physical", "digital", "service", "event", "booking"):
            row = publish(None, product_type=product_type)
            assert row["delivery_type"] == product_type


class TestTheSellersChoiceSurvivesToTheBuyer:
    def test_pickup_only_is_a_pickup_order(self):
        # The defect in one line: this returned "shipping", and the buyer was
        # asked for a delivery address for an item they were collecting.
        assert kind_of(publish("pickup")) == "pickup"

    def test_offering_both_reaches_the_buyer_as_a_choice(self):
        # `shipping_or_pickup` had no input that could produce it. Without this,
        # the lane chooser on the checkout screen is unreachable UI and
        # `resolve_choice`'s pickup branch is dead code.
        kind = kind_of(publish("both"))
        assert kind == "shipping_or_pickup"
        assert kind in mf.UNDECIDED_KINDS

    def test_shipping_only_is_a_shipping_order(self):
        assert kind_of(publish("shipping")) == "shipping"

    def test_a_seller_who_named_no_lane_ships(self):
        # `delivery_options` is optional, so this is an ordinary row rather than
        # an error case. Shipping is the only lane it can mean.
        assert kind_of(publish(None)) == "shipping"

    def test_a_pickup_order_is_never_asked_for_an_address(self):
        kind = kind_of(publish("pickup"))
        assert mf.needs_shipping_address(kind) is False
        assert "address_line1" not in {f[0] for f in mf.required_fields(kind)}

    def test_a_pickup_listing_still_holds_stock(self):
        # Pickup is a physical order. If it ever resolved to a stockless kind the
        # shelf would not come down and the item would sell twice.
        assert kind_of(publish("pickup")) not in mf.STOCKLESS_KINDS


class TestTheBuyerIsAskedBeforeTheyPay:
    def test_an_undecided_lane_refuses_to_settle_itself(self):
        kind = kind_of(publish("both"))
        _, error = mf.resolve_choice(kind, "")
        assert error == mf.LANE_REQUIRED_CODE

    def test_the_buyers_pickup_answer_is_honoured(self):
        # Reachable only because the kind above is now `shipping_or_pickup`.
        # While it was `shipping`, `resolve_choice` passed it straight through
        # and the buyer's answer was discarded without a word.
        kind = kind_of(publish("both"))
        assert mf.resolve_choice(kind, "pickup") == ("pickup", "")
        assert mf.resolve_choice(kind, "shipping") == ("shipping", "")


class TestTheCartAgreesWithTheCheckout:
    """Two derivations of one fact, on one payload, must not disagree."""

    @pytest.mark.parametrize(
        "option,lane,kind",
        [("pickup", "pickup", "pickup"),
         ("shipping", "shipping", "shipping"),
         ("both", "both", "shipping_or_pickup")],
    )
    def test_the_cart_line_reports_the_lane_the_checkout_will_use(self, option, lane, kind):
        row = publish(option)
        assert cart._fulfillment(row) == lane
        assert cart._fulfillment_kind(row) == kind

    def test_a_digital_listing_is_still_digital_on_both(self):
        row = publish(None, product_type="digital")
        assert cart._fulfillment(row) == "digital"
        assert cart._fulfillment_kind(row) == "digital"


class TestTheImportedSupplierListing:
    def test_a_cj_import_ships(self):
        # The importer hardcodes `'physical','physical'` into
        # (delivery_type, product_type) and writes no delivery_options, so a
        # dropshipped listing declares no lane. Shipping is right for it — but it
        # is right by the rule, not by the accident of a column that happened to
        # miss every lane word.
        conn = _db()
        conn.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, status, approval_status, currency, quantity, "
            " delivery_type, product_type, listing_type, listing_metadata_json, created_at, updated_at) "
            "VALUES (?,?,'draft','pending_review','USD',0,'physical','physical','',?,?,?)",
            (SELLER, "Imported thing", json.dumps({"source": "dropship", "media": []}), NOW, NOW),
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM marketplace_listings ORDER BY id DESC LIMIT 1").fetchone())
        conn.close()
        assert kind_of(row) == "shipping"
        # And specifically not digital, which the empty `listing_type` plus a
        # `'digital'` column default would otherwise have produced: a physical
        # parcel with no address collected and no inventory held.
        assert kind_of(row) not in mf.STOCKLESS_KINDS
