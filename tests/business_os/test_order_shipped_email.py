"""Whether a Business OS marketplace buyer is ever *emailed* that their order shipped.

There are two marketplaces in this repo and they notify through different
layers. Pulse Marketplace emits the ``order_shipped`` payment email. Business OS
emitted only its own in-app alert — title "Order shipped", body "Your order has
been fulfilled." — which carries no tracking reference, no item, no store name
and no greeting, and is not an email at all. A buyer on this lane learned
nothing outside the app.

The assertions here **render the email from the captured metadata** rather than
inspecting the context dict the module built. That is the whole point: the
context crosses ``payments_notifications.SAFE_CONTEXT_KEYS``, which silently
drops any key it does not recognise, so asserting on the dict would pass even if
the allowlist ate every field. Only the rendered body sees what the buyer sees,
and every template field reads ``ctx.get(...) or <fallback>`` — a missing name
and a nameless user render identically.

``test_a_nameless_buyer_still_gets_the_email`` is the negative control: it
proves "Hi there" is reachable, so the name assertions above it can fail.
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_order_shipped_email_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys  # noqa: E402

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import payments_notifications  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.marketplace import notifications as mkt_notifications  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402

SELLER = 880901
BUYER = 880902
NAMELESS_BUYER = 880903

ADMIN = "admin:9"

STORE_NAME = "Cherie Goods"
TRACKING = "1Z999AA10123456784"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    """One approved seller and three buyers.

    ``users`` itself is created by ``tests/business_os/conftest.py`` at session
    scope — deliberately not re-declared here, because a per-module
    ``CREATE TABLE IF NOT EXISTS`` with a narrower column list is exactly the
    run-order hazard that conftest exists to end.
    """
    mkt_schema.ensure_schema()
    ledger.ensure_schema()

    conn = db.connect()
    try:
        for user_id, username, full_name in (
            (SELLER, "cheriegoods", "Cherie Roody"),
            (BUYER, "danafox", "Dana Fox"),
            # No name of any kind. The greeting has nothing to find, which is
            # what makes the negative control below a real control.
            (NAMELESS_BUYER, "", None),
        ):
            conn.execute(
                "INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                (user_id, username, full_name),
            )
        conn.commit()
    finally:
        conn.close()

    mkt.upsert_seller(SELLER, display_name=STORE_NAME)
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    yield


class _Engine:
    """Stands in for the notification engine and keeps what reached its door.

    Captured at ``pulsesoc_notification_system.intake_event`` rather than at
    ``payments_notifications.emit`` so that the spec lookup, the recipient guard
    and the ``SAFE_CONTEXT_KEYS`` filter all really run. A stub one layer higher
    skips every one of them and would look green on a context of keys the
    allowlist throws away.
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


class _Alerts(list):
    """The Business OS in-app alerts, captured through the module's own seam.

    ``for_user`` matters: paying an order alerts the *seller* ("Order paid"), so
    a bare count here answers a question about two people at once.
    """

    def for_user(self, user_id) -> list:
        return [note for uid, note in self if str(uid) == str(user_id)]

    def kinds_for(self, user_id) -> list:
        return [note["data"]["kind"] for note in self.for_user(user_id)]


@pytest.fixture
def alerts():
    seen = _Alerts()
    mkt_notifications.set_sender(lambda user_id, note: seen.append((user_id, note)))
    try:
        yield seen
    finally:
        mkt_notifications.set_sender(None)


def _paid_order(buyer, *, fulfillment_type="physical", title="Hand-thrown mug"):
    product = mkt.create_product(
        SELLER, title=title, price_cents=2500, fulfillment_type=fulfillment_type,
        inventory_qty=20, context=_ctx())
    mkt.transition_product(SELLER, product["product_id"], "publish", context=_ctx())
    order = ordm.create_order(buyer, product["product_id"], quantity=1, context=_ctx())
    return ordm.pay_order(order["order_id"], buyer, context=_ctx())


# ---------------------------------------------------------------------------
def test_fulfilling_emails_the_buyer_by_name(engine, alerts):
    order = _paid_order(BUYER)

    ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    event = engine.only()
    assert event["event_type"] == "order_shipped"
    assert int(event["recipient_user_id"]) == BUYER

    body = engine.email()
    assert "Dana" in body
    assert "Hi there" not in body


def test_the_email_carries_the_store_item_and_tracking(engine, alerts):
    """Every fact the template asks for, through the allowlist and out again."""
    order = _paid_order(BUYER)

    ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    body = engine.email()
    assert STORE_NAME in body
    assert "Hand-thrown mug" in body
    assert TRACKING in body
    assert order["order_id"] in body


def test_a_nameless_buyer_still_gets_the_email(engine, alerts):
    """Negative control: "Hi there" is reachable, so the name assertions can fail."""
    order = _paid_order(NAMELESS_BUYER)

    ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    assert "Hi there" in engine.email()


def test_a_digital_order_is_not_told_it_shipped(engine, alerts):
    """A download has no shipment to track.

    ``fulfill_order`` treats a digital order as delivered on purchase, so an
    email headed "Your order is on the way" under a Track order button beside an
    empty tracking field would describe something that never happened.
    """
    order = _paid_order(BUYER, fulfillment_type="digital", title="Preset pack")

    ordm.fulfill_order(order["order_id"], SELLER, context=_ctx())

    assert engine.events == []
    # The in-app fulfilment alert is unaffected — the buyer is still told their
    # order is ready, just not that it is in transit.
    assert alerts.kinds_for(BUYER) == ["order_fulfilled"]


def test_the_seller_is_not_told_the_buyers_shipment(engine, alerts):
    order = _paid_order(BUYER)

    ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    assert int(engine.only()["recipient_user_id"]) != SELLER


def test_the_email_does_not_duplicate_the_in_app_alert(engine, alerts):
    """Email only — the buyer already has an in-app row from ``order_fulfilled``.

    This is where this emit site differs from the Pulse Marketplace one, which
    sends the full fan-out because nothing else announces a shipment there.
    """
    order = _paid_order(BUYER)

    ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    event = engine.only()
    assert event["channels"] == ["email"]
    assert event["metadata"]["skip_pulse_legacy_mirror"] is True
    # And the alert it is deferring to was really sent — one, to the buyer.
    assert [note["title"] for note in alerts.for_user(BUYER)] == ["Order shipped"]


def test_a_refused_fulfilment_emails_nobody(engine, alerts):
    """An unpaid order cannot ship, and must not email as though it had."""
    product = mkt.create_product(SELLER, title="Unpaid kettle", price_cents=900,
                                 inventory_qty=5, context=_ctx())
    mkt.transition_product(SELLER, product["product_id"], "publish", context=_ctx())
    order = ordm.create_order(BUYER, product["product_id"], context=_ctx())

    with pytest.raises(MarketplaceError) as exc:
        ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    assert exc.value.code == "illegal_transition"
    assert engine.events == []


def test_a_stranger_cannot_ship_someone_elses_order(engine, alerts):
    order = _paid_order(BUYER)

    with pytest.raises(MarketplaceError):
        ordm.fulfill_order(order["order_id"], NAMELESS_BUYER, tracking_ref=TRACKING,
                           context=_ctx())

    assert engine.events == []


def test_paying_an_order_emails_nobody(engine, alerts):
    """Only the fulfil lane mails; the sibling transitions share this module."""
    _paid_order(BUYER)

    assert engine.events == []


# --- public store identity --------------------------------------------------
@pytest.fixture
def store_named():
    """Rename the seller's shop for one test, then put it back.

    The seeding is module-scoped, so the name is shared by every test in this
    file. A test that changes it and does not restore it silently rewrites the
    assertions of whichever tests happen to run after it.
    """
    def rename(name):
        mkt.upsert_seller(SELLER, display_name=name)

    try:
        yield rename
    finally:
        rename(STORE_NAME)


def test_a_blank_looking_store_name_does_not_mangle_the_sentence(
        engine, alerts, store_named):
    """A store name of only spaces must read as no store name at all.

    The store name is not a standalone row that can be quietly omitted — it is
    interpolated into a sentence the buyer reads: "Hi Dana, <store> has shipped
    your order." The template already handles an *absent* name by substituting
    "the seller", so the empty case is safe. "   " is not empty, so it defeats
    that substitution and mails the buyer a sentence with a hole in it.

    This is pinned end to end rather than in the identity module's own unit
    tests because the defect is not in resolving a name. It is in a caller
    reading ``display_name`` off the row instead of through the canonical
    accessor, and only a real render can tell those two apart.
    """
    store_named("   ")
    order = _paid_order(BUYER)

    ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    body = engine.email()
    assert "the seller has shipped your order" in body
    assert "  has shipped your order" not in body


def test_a_real_store_name_still_reaches_the_buyer(engine, alerts, store_named):
    """Negative control for the test above.

    Discarding the store name outright would also satisfy that assertion, and
    would lose the feature. This pins that a genuine name — surrounding
    whitespace and all — still lands in the buyer's sentence.
    """
    store_named("  Cherie Goods  ")
    order = _paid_order(BUYER)

    ordm.fulfill_order(order["order_id"], SELLER, tracking_ref=TRACKING, context=_ctx())

    body = engine.email()
    assert "Cherie Goods has shipped your order" in body
