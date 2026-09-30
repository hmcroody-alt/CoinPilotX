"""What the buyer was told, kept exactly, and handed over to the carrier once.

What this file is defending
---------------------------
Six failures. Five of them are silent, and the sixth only exists in production.

* **A promise that changes.** Stripe redelivers a webhook; a buyer reloads an
  order page a month later; a retried checkout runs the estimator again. If any
  of those rewrites the stored window, the answer to "you said the 21st" becomes
  whatever today's supplier aging happens to be, and §36-37 is gone. The write
  is insert-only and the test proves it by writing a *different* window second
  and reading the first one back.
* **A carrier window that is really the promise.** Once a parcel ships the
  carrier is authoritative — but this package does not talk to a carrier. The
  tempting shape is to keep showing the promised dates under a carrier heading,
  which is precisely the fabricated precision §124 forbids. Asserted as
  ``window is None`` on the carrier branch, with ``promise_window`` still there,
  so the historical fact survives without being relabelled.
* **Authority transferred by a state a seller controls.** ``state = 'shipped'``
  is a string a merchant can set.
  ``marketplace_order_fulfillment.transition`` already refuses ``shipped``
  without a tracking reference for that reason, so the transfer here keys on
  the reference — the artifact a third party can be asked about. Tested with the
  state saying shipped and no reference present, which must stay ``PROMISE``.
* **An unmeasurable order scored.** An order with no promise row, a promise that
  was a *correct* refusal, an order not yet delivered: counting any of those as
  a hit or a miss is how §71-72's calibration learns from noise. Each returns
  ``measurable: False`` with a distinguishable reason.
* **The internal half leaking into the buyer half.** Freight cost and route ids
  are stored (they are what §70 groups by) and must come back in their own half.
  Asserted as a key-set equality on the buyer half, not a spot check, because a
  spot check passes for every field nobody thought of.
* **A key the database invents.** This one has no SQLite signature at all.
  ``services.db._translate_create_table`` rewrites ``<col> INTEGER PRIMARY KEY``
  to ``SERIAL PRIMARY KEY`` by regex for Postgres, which is correct for the
  tables whose id the database assigns and wrong for this one, whose key is the
  seller transaction id the caller already holds. Under SQLite the inline form
  works perfectly, so the only way to catch it is to read the DDL — which is
  what ``test_the_primary_key_is_not_declared_inline`` does.

The tests drive real SQL against a temp SQLite file with the table built from
``promise.SCHEMA_STATEMENTS`` via ``promise.create_schema``, never from a copy,
so a column added to the module is a column these tests get.
"""

import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_DB = tempfile.NamedTemporaryFile(prefix="pulsesoc-delivery-promise-", suffix=".db",
                                  delete=False)
_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"

from services import db  # noqa: E402
from services.delivery import estimate, promise, quote  # noqa: E402

TXN = 90210
OTHER_TXN = 90211
LISTING = 7700
VARIANT = "7700:VID-1"
AT = "2026-03-10T09:15:00+00:00"

#: The destination as the resolver hands it over. Country and precision only —
#: there is no postal code here and there must never be one, because
#: ``destination.POSTAL_TIERS`` allows a postal code at the checkout tier alone
#: and a promise row outlives the checkout that made it.
WHERE = {"country": "US", "precision": "COUNTRY", "tier": "SESSION", "known": True}


def buyer_window(earliest="2026-03-16", latest="2026-03-21",
                 confidence=estimate.CONFIDENCE_PROVIDER_QUOTED):
    """A composed buyer half, spelled out rather than borrowed.

    Written as a literal so that a field added to ``quote.BUYER_FIELDS`` fails
    ``test_the_fixture_is_a_complete_buyer_half`` here instead of being silently
    absent from every promise these tests store.
    """
    return {
        "state": estimate.STATE_ESTIMATED,
        "reason": None,
        "earliest": earliest,
        "latest": latest,
        "confidence": confidence,
        "guaranteed": False,
        "is_estimate": True,
        "shipping_price": "FREE",
    }


def buyer_refusal(reason="variant_data_incomplete"):
    return {
        "state": estimate.STATE_UNAVAILABLE,
        "reason": reason,
        "earliest": None,
        "latest": None,
        "confidence": estimate.CONFIDENCE_NONE,
        "guaranteed": False,
        "is_estimate": True,
        "shipping_price": "FREE",
    }


def internal_half(freight="6.42"):
    """The half a buyer never sees. §33-35.

    Carries a real freight figure on purpose: every test that asserts the buyer
    half is clean is asserting it against a result that genuinely contains the
    number it must not print.
    """
    return {
        "source": "PROVIDER",
        "route": {"logistic_name": "CJPacket Ordinary", "provider_total": freight,
                  "currency": "USD"},
        "detail": None,
        "components": {"handling_days": 2, "transit_days_min": 8, "transit_days_max": 12,
                       "buffer_days": 2},
        "freight_total": freight,
        "freight_currency": "USD",
    }


def composed(buyer=None, internal=None):
    return {"buyer": buyer if buyer is not None else buyer_window(),
            "internal": internal if internal is not None else internal_half()}


@pytest.fixture(autouse=True)
def schema():
    conn = db.connect()
    try:
        conn.execute("DROP TABLE IF EXISTS delivery_promises")
        cur = conn.cursor()
        promise.create_schema(cur)
        cur.close()
        conn.commit()
    finally:
        conn.close()
    yield


#: A default that is distinguishable from an explicit ``None``. Without it
#: ``record(result=None)`` silently gets a valid quote and the test that means to
#: prove ``None`` is refused proves nothing instead — which is what the first run
#: of this file caught.
_DEFAULT = object()


def record(*, transaction_id=TXN, listing_id=LISTING, variant_ref=VARIANT,
           quantity=1, destination=_DEFAULT, result=_DEFAULT, promised_at=AT):
    return promise.snapshot(
        seller_transaction_id=transaction_id,
        listing_id=listing_id,
        variant_ref=variant_ref,
        quantity=quantity,
        destination=WHERE if destination is _DEFAULT else destination,
        result=composed() if result is _DEFAULT else result,
        promised_at=promised_at,
    )


def stored_row(transaction_id=TXN):
    """The raw columns, for the assertions about the indexed duplicates."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT state, reason, earliest, latest, confidence, buyer_json, "
                    "internal_json, quantity, destination_country, destination_precision "
                    "FROM delivery_promises WHERE seller_transaction_id = ?",
                    (transaction_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return None if row is None else db.row_values(row)


# --------------------------------------------------------------------------
# The fixture pins
# --------------------------------------------------------------------------

def test_the_fixture_is_a_complete_buyer_half():
    # If this fails, `quote` grew a buyer field and every promise in this file has
    # been storing an incomplete one. That is the failure, not the assertion.
    assert set(buyer_window()) == set(quote.BUYER_FIELDS)
    assert set(buyer_refusal()) == set(quote.BUYER_FIELDS)


def test_the_primary_key_is_not_declared_inline():
    """The Postgres-only defect, caught the only way it can be.

    ``<name> INTEGER PRIMARY KEY`` becomes ``SERIAL PRIMARY KEY`` under
    ``services.db._translate_create_table``, giving this table a sequence and a
    default for a key the caller supplies — in production and in no test. SQLite
    accepts both forms identically, so there is no runtime signature to assert
    on. The DDL itself is the evidence.
    """
    ddl = promise.SCHEMA_STATEMENTS[0]
    assert re.search(r"\bINTEGER\s+PRIMARY\s+KEY\b", ddl, flags=re.I) is None
    assert re.search(r"PRIMARY\s+KEY\s*\(\s*seller_transaction_id\s*\)", ddl, flags=re.I)
    # And the constraint is real, not merely well-worded.
    record()
    with pytest.raises(Exception):
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO delivery_promises (seller_transaction_id, state, "
                "buyer_json, internal_json, promised_at) VALUES (?,?,?,?,?)",
                (TXN, "ESTIMATED", "{}", "{}", AT))
            conn.commit()
        finally:
            conn.close()


# --------------------------------------------------------------------------
# Writing, once
# --------------------------------------------------------------------------

def test_a_promise_is_recorded_and_read_back_in_two_halves():
    written = record()
    assert written["recorded"] is True
    stored = written["promise"]
    assert stored["buyer"] == buyer_window()
    assert stored["internal"] == internal_half()
    assert stored["variant_ref"] == VARIANT
    assert stored["listing_id"] == LISTING
    assert stored["quantity"] == 1
    assert stored["promised_at"] == AT
    assert stored["destination"] == {"country": "US", "precision": "COUNTRY"}


def test_the_buyer_half_never_carries_the_freight_it_was_stored_beside():
    stored = record()["promise"]
    assert set(stored["buyer"]) == set(quote.BUYER_FIELDS)
    blob = json.dumps(stored["buyer"])
    assert "6.42" not in blob
    assert "freight" not in blob
    assert "CJPacket" not in blob
    # And the number really was present to leak.
    assert stored["internal"]["freight_total"] == "6.42"


def test_a_second_write_changes_nothing_and_says_so():
    record()
    again = record(result=composed(buyer=buyer_window("2026-04-01", "2026-04-09")))
    assert again["recorded"] is False
    # The window the buyer agreed to, not the one the second call brought.
    assert again["promise"]["buyer"]["earliest"] == "2026-03-16"
    assert again["promise"]["buyer"]["latest"] == "2026-03-21"
    assert promise.read(TXN)["buyer"]["latest"] == "2026-03-21"


def test_a_second_write_cannot_replace_a_refusal_with_a_window():
    # The direction that would look like a bug fix and is not: an order whose
    # estimate was unavailable at purchase was sold without a date, and inventing
    # one later rewrites what the buyer agreed to.
    record(result=composed(buyer=buyer_refusal()))
    again = record()
    assert again["recorded"] is False
    assert again["promise"]["buyer"]["state"] == estimate.STATE_UNAVAILABLE
    assert again["promise"]["buyer"]["earliest"] is None


def test_the_indexed_columns_agree_with_the_blob():
    # They are duplicated for §70's reporting. Written by one statement so they
    # cannot drift — which is only true if they are actually written.
    record()
    state, reason, earliest, latest, confidence, buyer_json = stored_row()[:6]
    buyer = json.loads(buyer_json)
    assert (state, reason, earliest, latest, confidence) == (
        buyer["state"], buyer["reason"], buyer["earliest"], buyer["latest"],
        buyer["confidence"])


def test_a_refusal_is_stored_with_its_reason_in_the_column_too():
    record(result=composed(buyer=buyer_refusal("destination_unresolved")))
    state, reason, earliest, latest, confidence = stored_row()[:5]
    assert state == estimate.STATE_UNAVAILABLE
    assert reason == "destination_unresolved"
    assert earliest is None and latest is None
    assert confidence == estimate.CONFIDENCE_NONE


def test_two_orders_do_not_share_a_promise():
    record()
    record(transaction_id=OTHER_TXN,
           result=composed(buyer=buyer_window("2026-05-01", "2026-05-06")))
    assert promise.read(TXN)["buyer"]["earliest"] == "2026-03-16"
    assert promise.read(OTHER_TXN)["buyer"]["earliest"] == "2026-05-01"


def test_quantity_is_floored_at_one():
    # A line with quantity 0 is not a thing that ships, but a promise row with
    # quantity 0 would divide badly in every per-unit report that reads it.
    assert record(quantity=0)["promise"]["quantity"] == 1


# --------------------------------------------------------------------------
# What is not a promise
# --------------------------------------------------------------------------

@pytest.mark.parametrize("transaction_id", [0, None, -5])
def test_a_promise_needs_an_order(transaction_id):
    with pytest.raises(promise.PromiseRejected):
        record(transaction_id=transaction_id)


@pytest.mark.parametrize("result", [
    None,
    {},
    {"buyer": buyer_window()},                 # internal half missing
    {"internal": internal_half()},             # buyer half missing
    {"buyer": None, "internal": internal_half()},
    {"buyer": buyer_window(), "internal": "PROVIDER"},
])
def test_a_half_quote_is_refused(result):
    with pytest.raises(promise.PromiseRejected):
        record(result=result)


def test_an_incomplete_buyer_half_is_refused_and_names_what_is_missing():
    partial = buyer_window()
    partial.pop("confidence")
    partial.pop("shipping_price")
    with pytest.raises(promise.PromiseRejected) as raised:
        record(result=composed(buyer=partial))
    message = str(raised.value)
    assert "confidence" in message and "shipping_price" in message


def test_a_refused_promise_wrote_nothing():
    with pytest.raises(promise.PromiseRejected):
        record(result={"buyer": buyer_window()})
    assert promise.read(TXN) is None


# --------------------------------------------------------------------------
# Reading what is not there
# --------------------------------------------------------------------------

def test_an_order_with_no_promise_reads_as_none_not_as_a_blank_promise():
    # §70 has to exclude these. A plausible-looking empty promise cannot be
    # excluded, only scored.
    assert promise.read(TXN) is None


@pytest.mark.parametrize("transaction_id", [0, None, -1])
def test_reading_a_non_order_is_none_rather_than_an_error(transaction_id):
    assert promise.read(transaction_id) is None


# --------------------------------------------------------------------------
# Authority
# --------------------------------------------------------------------------

def test_before_anything_ships_the_promise_is_the_answer():
    stored = record()["promise"]
    decided = promise.authority(stored, None)
    assert decided["source"] == promise.AUTHORITY_PROMISE
    assert decided["tracking"] is None
    assert decided["window"] == {"earliest": "2026-03-16", "latest": "2026-03-21",
                                "confidence": estimate.CONFIDENCE_PROVIDER_QUOTED,
                                "guaranteed": False, "is_estimate": True}
    assert decided["promise_window"] == decided["window"]


def test_a_window_read_from_history_still_says_estimate():
    # §58/§125 do not stop applying because the order is in the past.
    stored = record()["promise"]
    window = promise.authority(stored, None)["window"]
    assert window["guaranteed"] is False
    assert window["is_estimate"] is True


def test_a_fulfillment_row_with_no_reference_does_not_take_over():
    stored = record()["promise"]
    decided = promise.authority(stored, {"state": "accepted", "fulfillment_kind": "shipped",
                                        "carrier": None, "tracking_reference": None})
    assert decided["source"] == promise.AUTHORITY_PROMISE
    assert decided["tracking"] is None


def test_the_state_string_alone_does_not_transfer_authority():
    """A merchant can type 'shipped'. A merchant cannot invent a tracking number.

    ``marketplace_order_fulfillment`` refuses ``shipped`` without a reference for
    exactly this reason, so a row that claims the state without the artifact is
    either a bug upstream or a hand-edited row, and neither is evidence a carrier
    has the parcel.
    """
    stored = record()["promise"]
    decided = promise.authority(stored, {"state": "shipped", "tracking_reference": "   "})
    assert decided["source"] == promise.AUTHORITY_PROMISE
    assert decided["window"] == decided["promise_window"]


def test_a_tracking_reference_hands_over_without_inventing_a_date():
    stored = record()["promise"]
    decided = promise.authority(stored, {
        "state": "shipped", "carrier": "YunExpress", "tracking_reference": "YT7700001",
        "tracking_url": "https://track.example/YT7700001",
        "shipped_at": "2026-03-13T02:00:00+00:00", "delivered_at": None})
    assert decided["source"] == promise.AUTHORITY_CARRIER
    # The load-bearing assertion of this whole module. Not the promise wearing a
    # carrier's name, and not a number this layer made up.
    assert decided["window"] is None
    assert decided["tracking"] == {"carrier": "YunExpress", "reference": "YT7700001",
                                   "url": "https://track.example/YT7700001",
                                   "shipped_at": "2026-03-13T02:00:00+00:00",
                                   "delivered_at": None}
    # And the promise is still readable beside it, unchanged. §36-37.
    assert decided["promise_window"]["latest"] == "2026-03-21"


def test_handing_over_does_not_rewrite_the_stored_promise():
    stored = record()["promise"]
    promise.authority(stored, {"tracking_reference": "YT7700001"})
    assert promise.read(TXN)["buyer"] == buyer_window()


def test_a_delivered_order_is_terminal_even_with_no_carrier():
    # Local pickup and hand-delivery have no tracking reference, and a confirmed
    # arrival outranks any estimate of one.
    stored = record()["promise"]
    decided = promise.authority(stored, {"state": "picked_up", "tracking_reference": None,
                                        "delivered_at": "2026-03-19T17:40:00+00:00"})
    assert decided["source"] == promise.AUTHORITY_CARRIER
    assert decided["window"] is None
    assert decided["tracking"]["reference"] is None
    assert decided["tracking"]["delivered_at"] == "2026-03-19T17:40:00+00:00"


def test_a_stored_refusal_has_no_window_in_either_direction():
    stored = record(result=composed(buyer=buyer_refusal()))["promise"]
    before = promise.authority(stored, None)
    assert before["source"] == promise.AUTHORITY_PROMISE
    assert before["window"] is None and before["promise_window"] is None
    after = promise.authority(stored, {"tracking_reference": "YT1"})
    assert after["source"] == promise.AUTHORITY_CARRIER
    assert after["promise_window"] is None


def test_an_order_with_no_promise_at_all_still_gets_an_answer():
    # An order placed before this package existed. It must not raise on an order
    # page, and it must not pretend to a window.
    decided = promise.authority(None, {"tracking_reference": "YT1"})
    assert decided["source"] == promise.AUTHORITY_CARRIER
    assert decided["promise_window"] is None
    assert promise.authority(None, None)["source"] == promise.AUTHORITY_PROMISE


# --------------------------------------------------------------------------
# Accuracy — mostly the refusals
# --------------------------------------------------------------------------

def test_an_order_with_no_promise_is_excluded_not_scored():
    result = promise.accuracy(None, "2026-03-18")
    assert result["measurable"] is False
    assert result["reason"] == promise.NOT_PROMISED
    assert result["within"] is None
    assert result["days_early"] is None and result["days_late"] is None


def test_a_correct_refusal_is_not_a_missed_promise():
    stored = record(result=composed(buyer=buyer_refusal("variant_data_incomplete")))["promise"]
    result = promise.accuracy(stored, "2026-03-18")
    assert result["measurable"] is False
    # The stored reason, so a report can tell a supplier outage from a
    # seller-fulfilled listing without joining anything.
    assert result["reason"] == "variant_data_incomplete"
    assert result["within"] is None


def test_an_undelivered_order_is_not_yet_measurable():
    stored = record()["promise"]
    for delivered in (None, "", "soon", "2026-13-40", "2026-03-18T00:00:00Z"):
        result = promise.accuracy(stored, delivered)
        assert result["measurable"] is False, delivered
        assert result["reason"] == "not_delivered_yet"


def test_a_stored_window_that_cannot_be_read_is_excluded():
    # Belt and braces for a row written before a validation existed. Scoring it
    # would mean scoring a parse failure as a delivery outcome.
    broken = {"buyer": dict(buyer_window(), earliest="soon")}
    result = promise.accuracy(broken, "2026-03-18")
    assert result["measurable"] is False
    assert result["reason"] == "stored_window_unusable"


def test_arriving_inside_the_window_is_a_hit():
    stored = record()["promise"]
    result = promise.accuracy(stored, "2026-03-18")
    assert result == {"measurable": True, "reason": None, "within": True,
                      "days_early": 0, "days_late": 0}


@pytest.mark.parametrize("delivered", ["2026-03-16", "2026-03-21"])
def test_the_window_is_inclusive_at_both_ends(delivered):
    # A promise of "16 – 21 Mar" that counted the 21st as late would report a
    # miss for a parcel that arrived on the day the buyer was told.
    stored = record()["promise"]
    assert promise.accuracy(stored, delivered)["within"] is True


def test_arriving_after_the_window_is_late_by_the_days_past_it():
    stored = record()["promise"]
    result = promise.accuracy(stored, "2026-03-24")
    assert result["measurable"] is True
    assert result["within"] is False
    assert result["days_late"] == 3
    assert result["days_early"] == 0


def test_arriving_before_the_window_is_early_not_late():
    # Early is not a failure, but it is not a hit either: a window that is
    # systematically early is a buffer §71-72 should be learning to shrink.
    stored = record()["promise"]
    result = promise.accuracy(stored, "2026-03-13")
    assert result["measurable"] is True
    assert result["within"] is False
    assert result["days_early"] == 3
    assert result["days_late"] == 0


def test_a_month_boundary_is_counted_in_days_not_in_date_arithmetic():
    # "31 Mar promised, 2 Apr delivered" is two days late, not negative
    # twenty-nine. A string comparison decides which side of the window it is on;
    # a date subtraction decides how far.
    stored = record(result=composed(buyer=buyer_window("2026-03-28", "2026-03-31")))["promise"]
    assert promise.accuracy(stored, "2026-04-02")["days_late"] == 2
    assert promise.accuracy(stored, "2026-02-27")["days_early"] == 29


def test_accuracy_reads_a_promise_straight_off_the_disk():
    # The whole point of storing it: the measurement runs from the row, with no
    # estimator, no supplier and no clock involved.
    record()
    result = promise.accuracy(promise.read(TXN), "2026-03-23")
    assert result["measurable"] is True and result["days_late"] == 2
