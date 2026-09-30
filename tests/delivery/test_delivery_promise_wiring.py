"""The promise is recorded at payment, from what the buyer was shown, or not at all.

``services.delivery.promise`` is tested in isolation next door. What is untested
without this file is the seam: the ~40 lines in ``bot.py`` that decide *whether*
a promise exists for an order, and *which* window it is. Four failures live there
and none of them raises:

* **A promise recomputed instead of remembered.** The obvious way to make every
  order have a window is to quote the variant here, at settlement. It would work,
  it would be green, and every number it produced would be one no buyer ever saw
  — computed from supplier aging, route availability and an operator's handling
  time that have all moved since the page the buyer bought from. §36-37 exists
  because that is a different question. The assertion is that an order whose
  metadata carries no quote gets **no row**, which is the only observable
  difference between remembering and recomputing.
* **An absent quote treated as an error.** Checkout does not attach a quote yet,
  so the overwhelming majority of real orders take this path. It must be silent
  and it must not cost a settlement.
* **A redelivered webhook rewriting the window.** Stripe retries. The insert-only
  rule is proven in the unit suite; what is proven here is that the *caller* goes
  through it rather than around it.
* **The buyer's quantity lost.** ``marketplace_order_line`` is the one place that
  knows an order's quantity — it prefers ``commercial_quote`` over a raw ``qty``
  for reasons documented there. A promise that hard-coded 1 would divide wrongly
  in every per-unit report and would disagree with ``marketplace_orders`` about
  the same order.

``bot.init_db()`` is called for real, so the table these tests read is the one
``init_db`` creates — which is also the assertion that the bootstrap was wired
at all. A worker that never serves a request is the thing that reads this table,
so a table created lazily on a checkout path would not exist for it.
"""

import ast
import json
import logging
import os
import pathlib
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="delivery_promise_wiring_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services.delivery import estimate, promise, quote  # noqa: E402

TXN = 660100
LISTING = 660200
NOW = "2026-03-10T09:15:00"

BUYER_HALF = {
    "state": estimate.STATE_ESTIMATED,
    "reason": None,
    "earliest": "2026-03-16",
    "latest": "2026-03-21",
    "confidence": estimate.CONFIDENCE_PROVIDER_QUOTED,
    "guaranteed": False,
    "is_estimate": True,
    "shipping_price": "FREE",
}
INTERNAL_HALF = {
    "source": "PROVIDER",
    "route": {"logistic_name": "CJPacket Ordinary", "provider_total": "6.42", "currency": "USD"},
    "detail": None,
    "components": {"handling_days": 2, "transit_days_min": 8, "transit_days_max": 12,
                   "buffer_days": 2},
    "freight_total": "6.42",
    "freight_currency": "USD",
}

#: What checkout will attach. Spelled here as the shape ``bot`` reads rather than
#: borrowed from a helper, so a change to either side fails instead of tracking.
DELIVERY_QUOTE = {
    "variant_ref": f"{LISTING}:VID-1",
    "destination": {"country": "US", "precision": "COUNTRY"},
    "result": {"buyer": BUYER_HALF, "internal": INTERNAL_HALF},
}


@pytest.fixture(scope="module", autouse=True)
def _schema():
    bot.init_db()
    yield


@pytest.fixture(autouse=True)
def _clean():
    conn = sqlite3.connect(_DB)
    conn.execute("DELETE FROM delivery_promises")
    conn.commit()
    conn.close()
    yield


def transaction(*, transaction_id=TXN, metadata=None, amount_cents=7500,
                item_type="marketplace_product"):
    return {
        "id": transaction_id,
        "item_type": item_type,
        "item_id": LISTING,
        "amount_cents": amount_cents,
        "buyer_user_id": 501,
        "seller_user_id": 502,
        "currency": "USD",
        "metadata_json": json.dumps(metadata or {}),
    }


def rows():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM delivery_promises ORDER BY seller_transaction_id")]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# init_db owns the table
# --------------------------------------------------------------------------

def test_init_db_creates_the_promise_table():
    """A worker that serves no request still needs this table to exist.

    §70's accuracy reporting and §71-72's calibration both read it from outside
    the web process, so creating it lazily on a checkout path they never run
    means it is missing exactly where it is read.
    """
    conn = sqlite3.connect(_DB)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='delivery_promises'")}
    finally:
        conn.close()
    assert "delivery_promises" in names
    # The indexes come from the same tuple as the table, so their absence would
    # mean `create_schema` was half-applied rather than the DDL being copied.
    assert "idx_delivery_promises_window" in indexes
    assert "idx_delivery_promises_listing" in indexes


# --------------------------------------------------------------------------
# No quote, no promise
# --------------------------------------------------------------------------

@pytest.mark.parametrize("metadata", [
    {},
    {"qty": 3},
    {"commercial_quote": {"quantity": 3, "unit_price_minor": 2500}},
    {"delivery_quote": None},
    {"delivery_quote": "ESTIMATED"},
    {"delivery_quote": []},
])
def test_an_order_with_no_attached_quote_records_nothing(metadata):
    """The load-bearing assertion of this file.

    There is deliberately no fallback that quotes the variant now and calls the
    result a promise. Such a row would be a number in the accuracy ledger that no
    buyer ever saw — worse than an empty ledger, because it looks like data.
    """
    assert bot.pulse_snapshot_delivery_promise(transaction(metadata=metadata)) is None
    assert rows() == []
    assert promise.read(TXN) is None


def test_an_absent_quote_is_silent_rather_than_an_error(caplog):
    # This is the path nearly every real order takes today. It must not raise and
    # it must not log an error, because a settlement runs through here — an
    # exception log per order would make the one real defect unfindable.
    with caplog.at_level(logging.ERROR):
        assert bot.pulse_snapshot_delivery_promise(transaction(metadata={})) is None
    assert [r for r in caplog.records if "DELIVERY_PROMISE" in r.getMessage()] == []


@pytest.mark.parametrize("transaction_id", [0, None, -4])
def test_a_transaction_without_an_id_records_nothing(transaction_id):
    result = bot.pulse_snapshot_delivery_promise(
        transaction(transaction_id=transaction_id, metadata={"delivery_quote": DELIVERY_QUOTE}))
    assert result is None
    assert rows() == []


def test_unreadable_metadata_records_nothing_and_does_not_raise():
    tx = transaction(metadata={"delivery_quote": DELIVERY_QUOTE})
    tx["metadata_json"] = "{not json"
    assert bot.pulse_snapshot_delivery_promise(tx) is None
    assert rows() == []


# --------------------------------------------------------------------------
# A quote, remembered
# --------------------------------------------------------------------------

def test_an_attached_quote_becomes_the_stored_promise():
    written = bot.pulse_snapshot_delivery_promise(
        transaction(metadata={"delivery_quote": DELIVERY_QUOTE}), now=NOW)
    assert written["recorded"] is True
    stored = written["promise"]
    # The window the buyer was shown, not one computed here.
    assert stored["buyer"] == BUYER_HALF
    assert stored["internal"] == INTERNAL_HALF
    assert stored["variant_ref"] == f"{LISTING}:VID-1"
    assert stored["listing_id"] == LISTING
    assert stored["destination"] == {"country": "US", "precision": "COUNTRY"}
    assert stored["promised_at"] == NOW


def test_the_stored_promise_is_the_buyers_and_not_a_recomputation():
    """A date nothing in this repository could produce today, stored verbatim.

    2027 is far outside any window the estimator can generate from a supplier
    aging range, so a promise carrying it can only have come from the attached
    quote. An implementation that recomputed at settlement would overwrite it
    with something plausible, and a fixture using plausible dates could not tell.
    """
    quoted = json.loads(json.dumps(DELIVERY_QUOTE))
    quoted["result"]["buyer"]["earliest"] = "2027-11-02"
    quoted["result"]["buyer"]["latest"] = "2027-11-09"
    bot.pulse_snapshot_delivery_promise(transaction(metadata={"delivery_quote": quoted}), now=NOW)
    stored = promise.read(TXN)
    assert stored["buyer"]["earliest"] == "2027-11-02"
    assert stored["buyer"]["latest"] == "2027-11-09"


def test_the_quantity_comes_from_the_order_line_not_from_one():
    # `marketplace_order_line` prefers `commercial_quote` over a raw `qty`, and a
    # promise that hard-coded 1 would disagree with `marketplace_orders` about the
    # same order.
    bot.pulse_snapshot_delivery_promise(transaction(metadata={
        "delivery_quote": DELIVERY_QUOTE,
        "commercial_quote": {"quantity": 3, "unit_price_minor": 2500},
    }), now=NOW)
    assert promise.read(TXN)["quantity"] == 3


def test_a_bare_qty_is_honoured_too():
    bot.pulse_snapshot_delivery_promise(transaction(metadata={
        "delivery_quote": DELIVERY_QUOTE, "qty": 2}), now=NOW)
    assert promise.read(TXN)["quantity"] == 2


def test_a_quote_stored_as_the_bare_result_is_accepted():
    # Tolerated because the shape checkout attaches is not settled yet: a caller
    # that hands over `{"buyer":…, "internal":…}` without the wrapper means the
    # same thing. The destination and variant are then absent, which is recorded
    # as absent rather than guessed.
    written = bot.pulse_snapshot_delivery_promise(transaction(metadata={
        "delivery_quote": {"buyer": BUYER_HALF, "internal": INTERNAL_HALF}}), now=NOW)
    assert written["recorded"] is True
    stored = written["promise"]
    assert stored["buyer"] == BUYER_HALF
    assert stored["variant_ref"] == ""
    assert stored["destination"] == {"country": None, "precision": None}


def test_a_quote_missing_a_half_records_nothing_and_does_not_raise(caplog):
    # `promise.snapshot` raises PromiseRejected; this seam must absorb it, because
    # a malformed attachment is a bug in checkout and not a reason to fail a paid
    # settlement.
    with caplog.at_level(logging.ERROR):
        result = bot.pulse_snapshot_delivery_promise(transaction(metadata={
            "delivery_quote": {"result": {"buyer": BUYER_HALF}}}), now=NOW)
    assert result is None
    assert rows() == []
    # Silent to the buyer, loud in the log: this one *is* a defect upstream, and a
    # handler that swallowed it without a trace would hide a checkout that had
    # stopped attaching a half.
    logged = [r for r in caplog.records
              if "DELIVERY_PROMISE_SNAPSHOT_FAILED" in r.getMessage()]
    assert len(logged) == 1
    # `getMessage()` already interpolates `args`, so the id is in the rendered
    # line. Asserting on the rendered line and not on `args` is the point: an
    # operator greps the log, and a record carrying the id only in an unformatted
    # tuple reads as `seller_transaction_id=%s` on the screen.
    assert str(TXN) in logged[0].getMessage()


def test_the_settlement_handlers_call_a_logger_that_exists():
    """Both `except` blocks in the settlement path, checked by name.

    The handler beside this one used to call ``log_error``, which is defined
    nowhere in this repository — so the block written to guarantee a fulfillment
    failure could not lose a settlement raised ``NameError`` from inside itself
    the moment it fired. A handler is the one kind of code that is never exercised
    by a green test run, which is why this asserts on the source.

    Read through ``ast`` rather than by substring, because the fix left a comment
    naming the old function and a substring check matched that comment and called
    it a call — which is how this test failed on its first run. The repository has
    been bitten by the same thing before: its route-authorization audit counts
    commented-out decorators as live ones. The only honest way to ask "is this
    name called here" is to ask the parser.
    """
    module = ast.parse(pathlib.Path(bot.__file__).read_text())
    functions = {node.name: node for node in ast.walk(module)
                 if isinstance(node, ast.FunctionDef)}
    called = set()
    for name in ("pulse_finalize_marketplace_settlement", "pulse_snapshot_delivery_promise"):
        assert name in functions, name
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call):
                called.add(ast.unparse(node.func))
    assert "log_error" not in called
    assert "logging.exception" in called
    # And the name really is absent module-wide, so no future handler can reach
    # for it on the assumption that it exists somewhere.
    assert not hasattr(bot, "log_error")


def test_settlement_calls_the_snapshot_and_does_not_guard_it_behind_the_promise():
    """The seam itself, which no behavioural test in this file can reach.

    Every other test here calls ``pulse_snapshot_delivery_promise`` directly, so
    all of them stay green if the one line that invokes it from settlement is
    deleted. That deletion is the single most consequential edit anyone could make
    to this slice — it produces a system that quotes, presents and stores nothing,
    with a full green suite — so it is asserted on the source.

    The second half is the subtler claim. The call sits *after* the fulfillment
    block's ``finally``, unconditionally, not inside the ``try`` whose failure the
    block above absorbs and not behind an ``if`` on a flag. A promise is owed the
    moment the buyer is, and a snapshot that only happens when fulfillment opened
    cleanly would leave exactly the failed orders — the ones worth measuring —
    outside the accuracy ledger.
    """
    module = ast.parse(pathlib.Path(bot.__file__).read_text())
    settlement = next(node for node in ast.walk(module)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "pulse_finalize_marketplace_settlement")
    calls = [node for node in ast.walk(settlement)
             if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "pulse_snapshot_delivery_promise"]
    assert len(calls) == 1, "settlement must record the promise exactly once"
    # Directly in the function body — not nested in a `try`, `if`, `for` or `with`,
    # each of which is a way for the call to be skipped on a live order.
    statements = [statement for statement in settlement.body
                  if any(node in calls for node in ast.walk(statement))]
    assert len(statements) == 1 and isinstance(statements[0], ast.Expr), (
        "the snapshot is conditional on something; it must not be")


# --------------------------------------------------------------------------
# Called twice
# --------------------------------------------------------------------------

def test_a_redelivered_webhook_re_reads_rather_than_rewrites():
    bot.pulse_snapshot_delivery_promise(
        transaction(metadata={"delivery_quote": DELIVERY_QUOTE}), now=NOW)
    later = json.loads(json.dumps(DELIVERY_QUOTE))
    later["result"]["buyer"]["earliest"] = "2026-04-01"
    later["result"]["buyer"]["latest"] = "2026-04-09"
    again = bot.pulse_snapshot_delivery_promise(
        transaction(metadata={"delivery_quote": later}), now="2026-03-17T00:00:00")
    assert again["recorded"] is False
    assert again["promise"]["buyer"]["latest"] == "2026-03-21"
    assert again["promise"]["promised_at"] == NOW
    assert len(rows()) == 1


def test_two_orders_from_one_cart_each_get_their_own_promise():
    bot.pulse_snapshot_delivery_promise(
        transaction(metadata={"delivery_quote": DELIVERY_QUOTE}), now=NOW)
    other = json.loads(json.dumps(DELIVERY_QUOTE))
    other["result"]["buyer"]["earliest"] = "2026-05-01"
    other["result"]["buyer"]["latest"] = "2026-05-06"
    bot.pulse_snapshot_delivery_promise(
        transaction(transaction_id=TXN + 1, metadata={"delivery_quote": other}), now=NOW)
    assert promise.read(TXN)["buyer"]["earliest"] == "2026-03-16"
    assert promise.read(TXN + 1)["buyer"]["earliest"] == "2026-05-01"


# --------------------------------------------------------------------------
# The two halves, still apart
# --------------------------------------------------------------------------

def test_the_freight_survives_the_round_trip_and_stays_internal():
    # §33-35. The number the pricing engine's landed-cost basis needs is here, and
    # the half a surface serializes outward does not contain it.
    bot.pulse_snapshot_delivery_promise(
        transaction(metadata={"delivery_quote": DELIVERY_QUOTE}), now=NOW)
    stored = promise.read(TXN)
    assert stored["internal"]["freight_total"] == "6.42"
    assert set(stored["buyer"]) == set(quote.BUYER_FIELDS)
    assert "6.42" not in json.dumps(stored["buyer"])


def test_a_stored_promise_is_immediately_usable_by_the_authority_and_accuracy_layers():
    # The point of the seam: what settlement writes is what an order page and an
    # accuracy report read, with no adapter between them.
    bot.pulse_snapshot_delivery_promise(
        transaction(metadata={"delivery_quote": DELIVERY_QUOTE}), now=NOW)
    stored = promise.read(TXN)
    assert promise.authority(stored, None)["window"]["latest"] == "2026-03-21"
    assert promise.accuracy(stored, "2026-03-19")["within"] is True
    assert promise.accuracy(stored, "2026-03-25")["days_late"] == 4
