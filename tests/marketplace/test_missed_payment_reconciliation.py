"""What happens to a Marketplace payment whose webhook never arrives.

This file is modelled on a real incident rather than on a hypothetical. On
2026-08-23 a live PaymentIntent for $0.50 succeeded against the production
account. Stripe's ``payment_intent.succeeded`` event never reached this server:
``stripe_events`` has no row for it and has never held a single
``payment_intent.succeeded`` of any kind. The consequences were all silent —
``seller_transactions`` row 27 still read ``checkout_created`` with
``updated_at`` equal to ``created_at`` to the second, ``marketplace_orders`` had
zero rows, the seller was never credited, and the money was swept to the
platform's own bank a month later. Nobody noticed for 37 days, and by then
Stripe's event retention window had passed the event, so it can never be
replayed.

The fix under test is a sweep that asks Stripe instead of waiting to be told.
Its correctness is entirely about *direction*: the reservation sweep defers
whenever it is unsure, because releasing stock wrongly oversells a paid item;
this sweep settles whenever Stripe says ``succeeded``, because failing to record
money that really moved is the defect being closed. Both halves of that
asymmetry are pinned below — it repairs a lost payment, and it refuses to act on
anything short of ``succeeded``, including a provider outage, which must never be
recorded as "Stripe says unpaid".

Every assertion measures a stored outcome — the transaction status, the order
row, the count of order rows after a second pass — and not the summary the
function returns about itself. A sweep that reported ``repaired`` while writing
nothing would pass a summary-only suite and lose the next payment.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="missed_payment_reconcile_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
# Never a real key. The sweep's only network call is injected in every test
# below, so nothing here reaches Stripe; this exists so importing ``bot`` does
# not fall back to a configured production key if one is in the environment.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_missed_payment_reconciliation_only"

import bot  # noqa: E402

SELLER, BUYER = 99811, 99812
# Comfortably outside the 30-minute grace window.
LONG_AGO = "2026-01-01T00:00:00"


@pytest.fixture(scope="module", autouse=True)
def _app():
    bot.init_db()
    conn = sqlite3.connect(_DB)
    conn.execute(
        "INSERT INTO marketplace_sellers (user_id,status,display_name,created_at,updated_at) "
        "VALUES (?,'approved','Reconcile Store',?,?)",
        (SELLER, LONG_AGO, LONG_AGO),
    )
    conn.commit()
    conn.close()
    yield
    os.unlink(_DB)


@pytest.fixture(autouse=True)
def _one_transaction_at_a_time():
    """Clear the candidate table between tests.

    The sweep selects every unsettled transaction in the database, not a set it
    is handed, so a row a previous test deliberately left unsettled — the unpaid
    intent, the outage, the unrecognised shape — would be offered to the next
    test's fetcher. That fetcher raises on any intent it was not told about, on
    purpose, so without this the suite would fail in a way that looks like a bug
    in the sweep. Transaction ids are never reused, so the settlement and ledger
    rows keyed on them are left in place.
    """
    conn = _db()
    conn.execute("DELETE FROM seller_transactions")
    conn.execute("DELETE FROM marketplace_orders")
    conn.commit()
    conn.close()
    yield


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


_NEXT = [70000]


def _transaction(status="checkout_created", intent_id=None, created_at=LONG_AGO,
                 item_type="marketplace_product", amount_cents=50):
    """One seller transaction in the shape a started card checkout leaves behind.

    ``metadata_json`` carries a commercial quote because the settlement service
    reads its immutable snapshot rather than recomputing a fee. Without it the
    settlement would be exercised against derived zeroes and the "seller was
    credited" assertions would pass for the wrong reason.
    """
    _NEXT[0] += 1
    tx_id = _NEXT[0]
    quote = {
        "quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
        "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": 0,
        "merchandise_net_minor": amount_cents, "shipping_minor": 0, "tax_minor": 0,
        "seller_shipping_credit_minor": 0, "buyer_total_minor": amount_cents,
        "quantity": 1, "unit_price_minor": amount_cents,
    }
    conn = _db()
    conn.execute(
        "INSERT INTO seller_transactions (id,buyer_user_id,seller_user_id,seller_type,item_type,"
        "item_id,amount_cents,currency,platform_fee_cents,seller_net_cents,status,"
        "stripe_payment_intent_id,metadata_json,created_at,updated_at) "
        "VALUES (?,?,?,'merchant',?,?,?,'USD',0,?,?,?,?,?,?)",
        (tx_id, BUYER, SELLER, item_type, 4242, amount_cents, amount_cents, status,
         intent_id or f"pi_missed_{tx_id}", json.dumps({"commercial_quote": quote}),
         created_at, created_at),
    )
    conn.commit()
    conn.close()
    return tx_id


def _succeeded(tx_id, intent_id=None):
    """A PaymentIntent as Stripe returns it, carrying the metadata checkout set."""
    return {
        "id": intent_id or f"pi_missed_{tx_id}",
        "status": "succeeded",
        "amount": 50,
        "amount_received": 50,
        "currency": "usd",
        "livemode": True,
        "metadata": {
            "cart_checkout": "1",
            "seller_transaction_ids": str(tx_id),
            "buyer_user_id": str(BUYER),
        },
    }


def _fetcher(mapping):
    def fetch(intent_id):
        if intent_id not in mapping:
            raise AssertionError(f"the sweep fetched an intent it should not have: {intent_id}")
        value = mapping[intent_id]
        if isinstance(value, Exception):
            raise value
        return value
    return fetch


def _status(tx_id):
    conn = _db()
    try:
        row = conn.execute("SELECT status FROM seller_transactions WHERE id=?", (tx_id,)).fetchone()
        return str(row["status"]) if row else None
    finally:
        conn.close()


def _orders(tx_id):
    conn = _db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM marketplace_orders WHERE seller_transaction_id=?", (tx_id,)).fetchall()]
    finally:
        conn.close()


def _sweep(mapping, **kwargs):
    """Drive the sweep in repair mode unless a test says otherwise.

    The production default is the opposite — report only — so every call that
    expects a write states ``dry_run=False`` through this helper rather than
    relying on the default. ``test_report_only_is_the_default`` pins which way
    round that is.
    """
    kwargs.setdefault("dry_run", False)
    return bot.pulse_reconcile_missed_marketplace_payments(
        fetch_payment_intent=_fetcher(mapping), **kwargs)


def _incidents(tx_id):
    from services.business_os.payments import incidents
    return [row for row in incidents.list_incidents(limit=200).get("incidents") or []
            if str(row.get("related_object") or "") == f"seller_transaction:{tx_id}"]


def test_a_payment_stripe_took_but_no_webhook_recorded_is_repaired():
    """The incident itself: succeeded at Stripe, ``checkout_created`` here.

    The order row is the assertion that matters most. Production had zero rows in
    ``marketplace_orders`` after a real buyer paid, so a repair that moved the
    status but projected no order would leave the seller with nothing to fulfil
    and the platform with no record of what was owed.
    """
    tx_id = _transaction()
    intent = _succeeded(tx_id)

    assert _status(tx_id) == "checkout_created"
    assert _orders(tx_id) == []

    result = _sweep({intent["id"]: intent})

    assert _status(tx_id) == "paid"
    orders = _orders(tx_id)
    assert len(orders) == 1
    assert orders[0]["status"] == "paid"
    assert orders[0]["provider_payment_id"] == intent["id"]
    assert orders[0]["amount_cents"] == 50
    assert [entry["transaction_id"] for entry in result["repaired"]] == [tx_id]


def test_repairing_twice_settles_once():
    """Both triggers can arrive for the same intent, so every write is idempotent.

    A late webhook after a sweep, or two sweeps overlapping, must not double the
    order or credit the seller twice. The second pass is also the one that proves
    the candidate query excludes what it already fixed — a sweep that kept
    re-examining settled rows would re-credit on every tick.
    """
    tx_id = _transaction()
    intent = _succeeded(tx_id)
    _sweep({intent["id"]: intent})

    assert len(_orders(tx_id)) == 1
    second = _sweep({})  # raises if the row is offered to Stripe a second time

    assert _status(tx_id) == "paid"
    assert len(_orders(tx_id)) == 1
    assert tx_id not in [entry["transaction_id"] for entry in second["repaired"]]


def test_a_checkout_still_in_flight_is_left_alone():
    """Stripe retries a failing endpoint for hours; the sweep must not race it.

    Without the grace window every checkout in progress would be reported as a
    lost payment, and the alarm that makes this defect visible would be buried in
    false positives from the moment it shipped.
    """
    tx_id = _transaction(created_at="2099-01-01T00:00:00")

    result = _sweep({}, grace_minutes=30)  # raises if the row is examined

    assert _status(tx_id) == "checkout_created"
    assert result["examined"] == 0


def test_an_unpaid_intent_is_never_settled():
    """Only ``succeeded`` is money. Everything else is reported and left alone."""
    tx_id = _transaction()
    intent = dict(_succeeded(tx_id), status="requires_payment_method")

    result = _sweep({intent["id"]: intent})

    assert _status(tx_id) == "checkout_created"
    assert _orders(tx_id) == []
    assert tx_id in result["unpaid"]


def test_a_stripe_outage_is_not_recorded_as_unpaid():
    """The distinction that keeps an incident from erasing a real payment.

    If an unreachable provider collapsed into "unpaid", a Stripe outage during a
    sweep would file every genuinely paid order as unpaid and the operator
    reading the summary would conclude there was nothing to repair.
    """
    tx_id = _transaction()
    intent_id = f"pi_missed_{tx_id}"

    result = _sweep({intent_id: RuntimeError("stripe is down")})

    assert _status(tx_id) == "checkout_created"
    assert tx_id in result["unreachable"]
    assert tx_id not in result["unpaid"]
    assert result["repaired"] == []


def test_an_unrecognised_metadata_shape_is_reported_not_guessed():
    """Crediting the wrong party is worse than not crediting anyone yet.

    The singular and ad-funding checkout shapes settle through different webhook
    branches. This sweep drives the cart branch, so anything whose metadata it
    does not recognise is escalated rather than pushed through the one settlement
    it happens to know.
    """
    tx_id = _transaction()
    intent = dict(_succeeded(tx_id), metadata={"purpose": "pulse_ad_wallet_funding"})

    result = _sweep({intent["id"]: intent})

    assert _status(tx_id) == "checkout_created"
    assert _orders(tx_id) == []
    assert [item["transaction_id"] for item in result["needs_attention"]] == [tx_id]


def test_a_refunded_transaction_is_never_walked_back_to_paid():
    """A refund that already settled outranks the original success.

    The candidate query excludes it, so the sweep never even asks Stripe — which
    it would answer ``succeeded`` for, since a refunded charge's intent stays
    succeeded forever.
    """
    tx_id = _transaction(status="refunded")

    result = _sweep({})  # raises if the refunded row is offered to Stripe

    assert _status(tx_id) == "refunded"
    assert result["examined"] == 0


def test_report_only_is_the_default():
    """Reporting is the default; repairing is a decision someone has to take.

    ``services/business_os/payments/reconciliation.py`` states the rule for every
    check in it — detect and report, never repair — and completing a settlement
    credits a seller, which is not something to do on a timer unattended. A
    default of ``dry_run=False`` would make a deployed sweep start crediting on
    its first tick, so the default is pinned here rather than left to review.
    """
    tx_id = _transaction()
    intent = _succeeded(tx_id)

    result = bot.pulse_reconcile_missed_marketplace_payments(
        fetch_payment_intent=_fetcher({intent["id"]: intent}))

    assert result["dry_run"] is True
    assert _status(tx_id) == "checkout_created"
    assert _orders(tx_id) == []
    assert result["repaired"] == [
        {"transaction_id": tx_id, "payment_intent_id": intent["id"], "applied": False}]


def test_a_missed_payment_is_reported_even_when_nothing_is_repaired():
    """The part that was actually missing in August.

    The money was recoverable for 37 days; what was absent was anyone knowing.
    So the incident is filed on detection, in report-only mode, before and
    independently of any repair — a sweep that only recorded discrepancies it
    also fixed would have stayed silent through the whole incident.
    """
    tx_id = _transaction()
    intent = _succeeded(tx_id)

    bot.pulse_reconcile_missed_marketplace_payments(
        fetch_payment_intent=_fetcher({intent["id"]: intent}))

    filed = _incidents(tx_id)
    assert len(filed) == 1
    assert filed[0]["incident_type"] == "missing_webhook_event"
    assert filed[0]["severity"] == "critical"
    assert filed[0]["status"] == "open"


def test_reporting_the_same_missed_payment_twice_files_one_incident():
    """A sweep on a timer must not bury the queue under one repeating fact."""
    tx_id = _transaction()
    intent = _succeeded(tx_id)
    fetch = _fetcher({intent["id"]: intent})

    bot.pulse_reconcile_missed_marketplace_payments(fetch_payment_intent=fetch)
    bot.pulse_reconcile_missed_marketplace_payments(fetch_payment_intent=fetch)

    assert len(_incidents(tx_id)) == 1
