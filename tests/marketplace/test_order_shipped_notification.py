"""Whether the buyer is ever told their order shipped.

``order_shipped`` had a notification spec and an email template and no producer:
nothing in the repo emitted it, so the seller pressed "shipped", the state
machine recorded it, and the buyer was told nothing. A missing producer looks
exactly like a working one from inside the fulfillment tests, which assert on
the state column and never on what anybody was told.

So these tests drive the real route and capture at the notification engine's
door, then **render the email from the captured metadata**. That is deliberate:
the context passes through ``SAFE_CONTEXT_KEYS``, which drops any key it does
not recognise, and asserting on the dict the route built would pass even if the
allowlist ate every one of them. Only the rendered body sees what the buyer
sees. ``test_a_nameless_buyer_still_gets_the_email`` is the negative control
that proves these assertions can fail.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="order_shipped_notification_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_order_fulfillment as fulfillment  # noqa: E402
from services import payments_notifications  # noqa: E402

SELLER = 90901
BUYER = 90902
NAMELESS_BUYER = 90903

TX = 7001
TX_NAMELESS = 7002
TX_PICKUP = 7003

#: Taken from the module, never spelled out: a fixture free to invent a lane the
#: state machine does not know would have every shipment refused for the wrong
#: reason, and the "no email" assertions would all pass vacuously.
SHIPPING_KIND = sorted(fulfillment.SHIPPING_KINDS)[0]


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    bot.init_db()
    conn = bot.db()
    for user_id, username, full_name in (
        (SELLER, "cheriegoods", "Cherie Roody"),
        (BUYER, "danafox", "Dana Fox"),
        (NAMELESS_BUYER, "", None),
    ):
        conn.execute(
            "INSERT INTO users (user_id, username, full_name) VALUES (?,?,?)",
            (user_id, username, full_name),
        )
    conn.execute(
        "INSERT INTO marketplace_sellers (user_id, display_name) VALUES (?,?)",
        (SELLER, "Cherie Goods"),
    )
    for tx_id, buyer in ((TX, BUYER), (TX_NAMELESS, NAMELESS_BUYER), (TX_PICKUP, BUYER)):
        conn.execute(
            "INSERT INTO seller_transactions "
            "(id, buyer_user_id, seller_user_id, item_type, amount_cents, currency, metadata_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (tx_id, buyer, SELLER, "marketplace_product", 2500, "usd",
             json.dumps({"title": "Hand-thrown mug"})),
        )
    cur = conn.cursor()
    for tx_id, buyer, kind in ((TX, BUYER, SHIPPING_KIND),
                               (TX_NAMELESS, NAMELESS_BUYER, SHIPPING_KIND),
                               (TX_PICKUP, BUYER, "pickup")):
        fulfillment.open_fulfillment(
            cur, seller_transaction_id=tx_id, seller_id=SELLER,
            buyer_user_id=buyer, fulfillment_kind=kind, order_id=f"ord_{tx_id}")
    conn.commit()
    conn.close()
    yield
    os.unlink(_DB)


@pytest.fixture(autouse=True)
def _unshipped():
    """Put every order back to paid between tests.

    The route derives its idempotency key from the move alone
    (``{tx}:{state}:{role}``), so an order shipped by one test is answered as a
    duplicate for every test after it — and a duplicate emits nothing, which
    would turn the rest of this file green for the wrong reason.
    """
    conn = bot.db()
    try:
        conn.execute("DELETE FROM marketplace_order_fulfillment_events")
        conn.execute(
            "UPDATE marketplace_order_fulfillment SET state=?, carrier=NULL, "
            "tracking_reference=NULL, tracking_url=NULL, shipped_at=NULL, auto_advance_at=NULL",
            (fulfillment.PAID,),
        )
        conn.commit()
    finally:
        conn.close()
    yield


class _Engine:
    """Stands in for the notification engine and keeps what reached its door.

    Captures at ``intake_event`` rather than at ``payments_notifications.emit``
    so that the spec lookup, the recipient check and the ``SAFE_CONTEXT_KEYS``
    filter all really run — a stub one layer higher would skip every one of them
    and still look green.
    """

    def __init__(self):
        self.events = []

    def __call__(self, **payload):
        self.events.append(payload)
        return {"ok": True}

    def only(self):
        assert len(self.events) == 1, self.events
        return self.events[0]

    def email(self):
        rendered = payments_notifications.render_email(self.only()["metadata"])
        assert rendered, "the notification carried no renderable email"
        return str(rendered)


@pytest.fixture
def engine(monkeypatch):
    from services import pulsesoc_notification_system

    captured = _Engine()
    monkeypatch.setattr(pulsesoc_notification_system, "intake_event", captured)
    return captured


@pytest.fixture
def ship(monkeypatch):
    """POST the seller's "I shipped it" through the real route."""
    client = bot.app.test_client()

    def _post(transaction_id, *, actor=SELLER, **body):
        monkeypatch.setattr(bot, "api_account_user", lambda: {"user_id": actor})
        payload = {"carrier": "UPS", "tracking_reference": "1Z999AA10123456784"}
        payload.update(body)
        return client.post(
            f"/api/pulse/marketplace/orders/{transaction_id}/shipped", json=payload)

    return _post


def test_marking_shipped_emails_the_buyer_by_name(engine, ship):
    assert ship(TX).status_code == 200

    event = engine.only()
    assert event["event_type"] == "order_shipped"
    assert int(event["recipient_user_id"]) == BUYER

    body = engine.email()
    assert "Dana" in body
    assert "Hi there" not in body


def test_the_email_carries_the_store_item_and_tracking(engine, ship):
    """Every fact the template asks for, through the allowlist and out again."""
    ship(TX)

    body = engine.email()
    assert "Cherie Goods" in body
    assert "Hand-thrown mug" in body
    assert "1Z999AA10123456784" in body
    assert "UPS" in body


def test_a_nameless_buyer_still_gets_the_email(engine, ship):
    """Negative control: "Hi there" is reachable, so the name assertions can fail."""
    assert ship(TX_NAMELESS).status_code == 200

    body = engine.email()
    assert "Hi there" in body


def test_the_seller_is_not_told_their_own_shipment(engine, ship):
    ship(TX)

    assert int(engine.only()["recipient_user_id"]) != SELLER


def test_a_second_press_of_shipped_sends_nothing(engine, ship):
    """The route answers a re-press without moving the order, so there is no news.

    A double-tap is the expected failure on this button; sending the buyer a
    second "your order is on its way" for it would be the notification layer
    inventing an event the state machine refused to record.
    """
    assert ship(TX).status_code == 200
    assert ship(TX).status_code == 200

    assert len(engine.events) == 1, engine.events


def test_a_refused_shipment_notifies_nobody(engine, ship):
    """A pickup order cannot ship, and must not email as though it had."""
    assert ship(TX_PICKUP).status_code == 409

    assert engine.events == []


def test_shipping_without_tracking_notifies_nobody(engine, ship):
    assert ship(TX, tracking_reference="").status_code == 409

    assert engine.events == []


def test_marking_processing_notifies_nobody(engine, monkeypatch):
    """Only the shipped lane has an email; the sibling transitions share a route."""
    monkeypatch.setattr(bot, "api_account_user", lambda: {"user_id": SELLER})

    response = bot.app.test_client().post(
        f"/api/pulse/marketplace/orders/{TX}/processing", json={})

    assert response.status_code == 200
    assert engine.events == []
