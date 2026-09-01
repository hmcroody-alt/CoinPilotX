"""Stage 5-6 — the shared settlement path and the reconciliation decision table.

Two things are proved here, and they are different in kind.

The first is *structural*: every unsuccessful-payment branch now runs the same
inventory mutation. Before this stage the release-plus-terminal-status pair was
copy-pasted into four Stripe webhook branches and omitted from a fifth
(``payment_intent.canceled``, which did not exist). The omission is what
stranded stock after a dismissed Apple Pay sheet. So the tests here exercise
``settle_failed_transactions`` directly and assert the properties every branch
inherits by calling it, rather than asserting them once per branch.

The second is *behavioural*: the reconciler must never let a timer overrule the
processor. ``decide_from_status`` is deliberately pure — no Stripe, no database,
no clock — so the whole decision table can be driven exhaustively and every
answer is a fact about the code rather than about a mock.

The asymmetry those tests encode: deferring wrongly holds stock a little too
long; releasing wrongly sells a paid item to somebody else. Every ambiguous
input must defer.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_cart_routes as cart
from services import marketplace_reservation_policy as policy
from services import marketplace_reservation_reconciler as reconciler


LISTING_ID = 7
STARTING_STOCK = 5
STAMP = "2026-08-31T12:30:00+00:00"


@pytest.fixture(autouse=True)
def _reset_column_cache():
    cart._RESERVATION_COLUMN_CACHE = None
    yield
    cart._RESERVATION_COLUMN_CACHE = None


@pytest.fixture(autouse=True)
def _clear_env():
    """The deferral bound is read from the environment on every call."""
    previous = os.environ.pop(reconciler.MAX_DEFERRALS_ENV_VAR, None)
    yield
    if previous is None:
        os.environ.pop(reconciler.MAX_DEFERRALS_ENV_VAR, None)
    else:
        os.environ[reconciler.MAX_DEFERRALS_ENV_VAR] = previous


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_listings "
                "(id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)")
    cur.execute("""CREATE TABLE marketplace_inventory_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_transaction_id INTEGER UNIQUE,
        buyer_user_id INTEGER, listing_id INTEGER, quantity INTEGER DEFAULT 1,
        status TEXT DEFAULT 'held', created_at TEXT, updated_at TEXT)""")
    cur.execute("""CREATE TABLE seller_transactions (
        id INTEGER PRIMARY KEY, buyer_user_id INTEGER, status TEXT,
        stripe_payment_intent_id TEXT, metadata_json TEXT, updated_at TEXT)""")
    cart._ensure_reservation_lifecycle_columns(cur)
    cur.execute("INSERT INTO marketplace_listings VALUES (?, ?, '')",
                (LISTING_ID, STARTING_STOCK))
    return cur


def _order(cur, tx_id, qty=2, *, tx_status="checkout_created", reserved=True):
    cur.execute("INSERT INTO seller_transactions (id, buyer_user_id, status) VALUES (?, 1, ?)",
                (tx_id, tx_status))
    if reserved:
        cur.execute(
            """INSERT INTO marketplace_inventory_reservations
            (seller_transaction_id, buyer_user_id, listing_id, quantity, status,
             created_at, updated_at, reserved_at, expires_at)
            VALUES (?, 1, ?, ?, 'held', ?, ?, ?, ?)""",
            (tx_id, LISTING_ID, qty, "2026-08-31T12:00:00+00:00",
             "2026-08-31T12:00:00+00:00", "2026-08-31T12:00:00+00:00",
             "2026-08-31T12:15:00+00:00"))
        cur.execute("UPDATE marketplace_listings SET quantity=quantity-? WHERE id=?",
                    (qty, LISTING_ID))
    return tx_id


def _stock(cur):
    return cur.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                       (LISTING_ID,)).fetchone()[0]


def _tx(cur, tx_id):
    return dict(cur.execute("SELECT * FROM seller_transactions WHERE id=?", (tx_id,)).fetchone())


def _reservation(cur, tx_id):
    return dict(cur.execute(
        "SELECT * FROM marketplace_inventory_reservations WHERE seller_transaction_id=?",
        (tx_id,)).fetchone())


# --------------------------------------------------------------------------
# The shared settlement path
# --------------------------------------------------------------------------

def test_settlement_returns_stock_and_closes_the_order():
    cur = _db()
    _order(cur, 100, qty=2)
    assert _stock(cur) == 3

    results = cart.settle_failed_transactions(
        cur, [100], reason=policy.REASON_PAYMENT_CANCELED,
        terminal_status="canceled", now=STAMP)

    assert _stock(cur) == STARTING_STOCK
    assert _tx(cur, 100)["status"] == "canceled"
    assert _reservation(cur, 100)["status"] == policy.STATUS_RELEASED
    assert _reservation(cur, 100)["release_reason"] == policy.REASON_PAYMENT_CANCELED
    assert results[0]["changed"] is True
    assert results[0]["transaction_updated"] is True


def test_settlement_handles_a_whole_cart_group():
    """The plural branch is one call, not a loop the caller has to get right."""
    cur = _db()
    for tx_id in (101, 102, 103):
        _order(cur, tx_id, qty=1)
    assert _stock(cur) == 2

    cart.settle_failed_transactions(
        cur, [101, 102, 103], reason=policy.REASON_PAYMENT_FAILED,
        terminal_status="failed", now=STAMP)

    assert _stock(cur) == STARTING_STOCK
    assert all(_tx(cur, tx_id)["status"] == "failed" for tx_id in (101, 102, 103))


@pytest.mark.parametrize("repeats", [2, 3, 5])
def test_replayed_webhook_cannot_double_credit_stock(repeats):
    """Stripe retries. The same event arriving five times must cost one credit.

    This is the single most expensive failure mode available here: a listing
    whose quantity inflates on every redelivery would let the seller oversell
    an item they do not have.
    """
    cur = _db()
    _order(cur, 104, qty=2)

    for _ in range(repeats):
        cart.settle_failed_transactions(
            cur, [104], reason=policy.REASON_PAYMENT_FAILED,
            terminal_status="failed", now=STAMP)

    assert _stock(cur) == STARTING_STOCK


def test_a_paid_order_survives_a_late_failure_event():
    """Out-of-order delivery must not unwind a settled sale.

    Stripe does not guarantee ordering. A ``payment_failed`` for an earlier
    attempt can land after the ``succeeded`` that followed it. Both guards are
    asserted together because either one alone is insufficient: the reservation
    guard keeps the stock consumed, the transaction guard keeps the order paid.
    """
    cur = _db()
    _order(cur, 105, qty=2)
    cart.capture_inventory_reservation(cur, 105, now="2026-08-31T12:10:00+00:00")
    cur.execute("UPDATE seller_transactions SET status='paid' WHERE id=105")
    stock_after_capture = _stock(cur)

    results = cart.settle_failed_transactions(
        cur, [105], reason=policy.REASON_PAYMENT_CANCELED,
        terminal_status="canceled", now=STAMP)

    assert _stock(cur) == stock_after_capture, "captured stock was returned"
    assert _tx(cur, 105)["status"] == "paid"
    assert results[0]["changed"] is False
    assert results[0]["transaction_updated"] is False


def test_refunded_orders_are_equally_protected():
    cur = _db()
    _order(cur, 106, qty=2, tx_status="refunded")

    cart.settle_failed_transactions(
        cur, [106], reason=policy.REASON_EXPIRED,
        terminal_status="checkout_expired", now=STAMP)

    assert _tx(cur, 106)["status"] == "refunded"


def test_settlement_records_failure_metadata_when_supplied():
    cur = _db()
    _order(cur, 107)

    cart.settle_failed_transactions(
        cur, [107], reason=policy.REASON_PAYMENT_FAILED, terminal_status="failed",
        now=STAMP, metadata_json='{"failure": "card_declined"}')

    assert "card_declined" in (_tx(cur, 107)["metadata_json"] or "")


def test_settlement_leaves_metadata_untouched_when_not_supplied():
    """The expiry branch has no failure payload and must not blank an existing one."""
    cur = _db()
    _order(cur, 108)
    cur.execute("UPDATE seller_transactions SET metadata_json='{\"keep\": 1}' WHERE id=108")

    cart.settle_failed_transactions(
        cur, [108], reason=policy.REASON_EXPIRED,
        terminal_status="checkout_expired", now=STAMP)

    assert _tx(cur, 108)["metadata_json"] == '{"keep": 1}'


def test_unknown_and_malformed_ids_are_skipped_not_raised():
    """Webhook metadata is attacker-adjacent input and is frequently ragged.

    A raise here becomes a 500, which Stripe answers by retrying the event
    forever while the stock it was meant to free stays held — the exact leak
    this mission closes, reintroduced through the error path.
    """
    cur = _db()
    _order(cur, 109, qty=1)

    results = cart.settle_failed_transactions(
        cur, [109, 0, -3, None, "", "abc", 99999],
        reason=policy.REASON_EXPIRED, terminal_status="checkout_expired", now=STAMP)

    assert _stock(cur) == STARTING_STOCK
    assert [r["seller_transaction_id"] for r in results] == [109, 99999]
    assert results[1]["changed"] is False


def test_empty_id_list_is_a_no_op():
    cur = _db()
    assert cart.settle_failed_transactions(
        cur, [], reason=policy.REASON_EXPIRED,
        terminal_status="checkout_expired", now=STAMP) == []


# --------------------------------------------------------------------------
# The reconciliation decision table
# --------------------------------------------------------------------------

def test_succeeded_never_releases():
    """The invariant the whole reconciler exists for."""
    decision = reconciler.decide_from_status("succeeded")
    assert decision["decision"] == reconciler.DECISION_CAPTURE


def test_succeeded_still_never_releases_after_the_deferral_bound():
    """No amount of elapsed time converts a settled payment into a release."""
    decision = reconciler.decide_from_status("succeeded", deferrals=10_000)
    assert decision["decision"] == reconciler.DECISION_CAPTURE


def test_processing_defers():
    decision = reconciler.decide_from_status("processing")
    assert decision["decision"] == reconciler.DECISION_DEFER


def test_processing_is_never_force_released_even_when_bound_is_exhausted():
    """An asynchronous method that is still settling may yet succeed.

    Releasing under it would recreate the paid-and-oversold outcome the bound
    was introduced to avoid, so ``processing`` escalates to an operator instead
    of terminating itself.
    """
    decision = reconciler.decide_from_status("processing", deferrals=99)
    assert decision["decision"] == reconciler.DECISION_DEFER
    assert decision["needs_attention"] is True


@pytest.mark.parametrize("status", sorted(reconciler.AWAITING_BUYER_STATUSES))
def test_awaiting_buyer_defers_then_releases_once_bounded(status):
    """A buyer mid-3DS is waited for — but not forever.

    Stripe emits no event when a buyer abandons an authentication prompt, so
    without the bound these holds would be immortal.
    """
    assert reconciler.decide_from_status(status, deferrals=0)["decision"] == reconciler.DECISION_DEFER

    exhausted = reconciler.decide_from_status(status, deferrals=reconciler.MAX_DEFERRALS)
    assert exhausted["decision"] == reconciler.DECISION_RELEASE
    assert exhausted["release_reason"] in policy.RELEASE_REASONS


def test_canceled_releases_with_the_provider_reason():
    decision = reconciler.decide_from_status("canceled")
    assert decision["decision"] == reconciler.DECISION_RELEASE
    assert decision["release_reason"] == policy.REASON_PAYMENT_CANCELED


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_no_payment_intent_releases(missing):
    """Checkout raised before Stripe: nothing can ever settle this hold."""
    decision = reconciler.decide_from_status(missing)
    assert decision["decision"] == reconciler.DECISION_RELEASE
    assert decision["release_reason"] == policy.REASON_EXPIRED


def test_unrecognised_status_defers_rather_than_releasing():
    """Stripe may add a status. Stock must not turn on a string we don't know."""
    decision = reconciler.decide_from_status("some_future_status")
    assert decision["decision"] == reconciler.DECISION_DEFER
    assert decision["needs_attention"] is True


def test_status_matching_is_case_and_whitespace_insensitive():
    assert reconciler.decide_from_status("  SUCCEEDED  ")["decision"] == reconciler.DECISION_CAPTURE


def test_every_release_decision_carries_an_auditable_reason():
    """A release with no recorded reason is an unexplained inventory movement."""
    probes = ["canceled", None, *reconciler.AWAITING_BUYER_STATUSES]
    for status in probes:
        for deferrals in (0, reconciler.MAX_DEFERRALS + 1):
            decision = reconciler.decide_from_status(status, deferrals=deferrals)
            if decision["decision"] == reconciler.DECISION_RELEASE:
                assert decision["release_reason"] in policy.RELEASE_REASONS


# --------------------------------------------------------------------------
# Reconciling a real reservation row
# --------------------------------------------------------------------------

def test_a_locally_paid_transaction_is_decided_without_calling_stripe():
    """"Do not call the provider for every reservation" starts here.

    If the local row already says paid, the answer is known and an API call is
    pure cost. The injected fetcher raises so that any call at all fails loudly.
    """
    def _must_not_be_called(_intent_id):
        raise AssertionError("Stripe was consulted for an already-settled order")

    decision = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_1", "transaction_status": "paid"},
        fetch_status=_must_not_be_called)

    assert decision["decision"] == reconciler.DECISION_CAPTURE


def test_a_row_without_an_intent_is_decided_without_calling_stripe():
    def _must_not_be_called(_intent_id):
        raise AssertionError("Stripe was consulted for a row with no intent")

    decision = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "", "transaction_status": "checkout_created"},
        fetch_status=_must_not_be_called)

    assert decision["decision"] == reconciler.DECISION_RELEASE


def test_a_stripe_outage_defers_and_never_mass_releases():
    """The failure mode that would turn one provider incident into an oversell.

    Every expired reservation in the store reconciles during an outage. If an
    unreachable Stripe read as "release", a single Stripe incident would empty
    every hold in the system at once.
    """
    def _boom(_intent_id):
        raise RuntimeError("connection reset")

    decision = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_2", "transaction_status": "checkout_created"},
        fetch_status=_boom)

    assert decision["decision"] == reconciler.DECISION_DEFER
    assert decision["needs_attention"] is True


def test_provider_status_reaches_the_decision_with_the_intent_id_attached():
    decision = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_3", "transaction_status": "checkout_created"},
        fetch_status=lambda intent_id: "succeeded")

    assert decision["decision"] == reconciler.DECISION_CAPTURE
    assert decision["stripe_payment_intent_id"] == "pi_3"


def test_a_lost_webhook_is_repaired_rather_than_compounded():
    """The reservation expired locally but Stripe says the money moved.

    Reaching this branch means a ``payment_intent.succeeded`` webhook was never
    delivered. Reconciliation is the backstop that notices — and it must
    capture, not release, or the buyer pays for an item that gets resold.
    """
    decision = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_4", "transaction_status": "checkout_created"},
        fetch_status=lambda intent_id: "succeeded", deferrals=reconciler.MAX_DEFERRALS + 5)

    assert decision["decision"] == reconciler.DECISION_CAPTURE


# --------------------------------------------------------------------------
# The deferral bound is configuration, and configuration can be wrong
# --------------------------------------------------------------------------

def test_deferral_bound_defaults_when_unset():
    assert reconciler.max_deferrals() == reconciler.MAX_DEFERRALS


@pytest.mark.parametrize("raw,expected", [
    ("3", 3),
    ("0", 1),        # never zero: that would release the instant a hold expires
    ("-5", 1),
    ("500", 50),     # never unbounded: an immortal hold is the original bug
    ("banana", reconciler.MAX_DEFERRALS),
    ("", reconciler.MAX_DEFERRALS),
])
def test_deferral_bound_is_clamped_and_typo_tolerant(raw, expected):
    """A bad value must not make holds immortal or release them instantly."""
    os.environ[reconciler.MAX_DEFERRALS_ENV_VAR] = raw
    assert reconciler.max_deferrals() == expected


def test_the_stripe_import_stays_off_module_scope():
    """The sweeper imports this module at boot; it must not need the SDK there.

    A top-level ``import stripe`` would make the expiry worker fail to start on
    any deployment without the SDK installed or the key configured — and a
    sweeper that cannot start is a reservation leak with extra steps. Asserted
    against the parsed source rather than ``sys.modules``, because another test
    module importing Stripe would make a module-state check pass by accident.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(reconciler))
    top_level_imports = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
    }
    top_level_imports |= {
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "stripe" not in top_level_imports
