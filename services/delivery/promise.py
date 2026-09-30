"""The delivery promise as it stood at the moment of purchase, kept forever.

Why a snapshot and not a recomputation
--------------------------------------
Everything that produces an estimate is a moving part. The supplier's quoted
aging changes, a route is withdrawn, an operator raises the handling time, a
warehouse closes for a holiday nobody had declared last week. Recomputing a past
order's window from today's inputs would answer a different question than the one
the buyer was asked to agree to — and it would answer it differently every time
the page was loaded.

So §36-37: what the buyer was shown is written down once, at the moment they paid,
and never touched again. A dispute over "you said the 21st" is a question about a
sentence, and the only way to answer it is to have kept the sentence.

Immutability is enforced by the write path, not by convention
-------------------------------------------------------------
:func:`snapshot` is insert-only. There is no update, no upsert of the window, and
no argument that would let a caller replace one — a redelivered webhook or a
retried checkout finds the row already there and leaves it alone, returning what
is stored rather than what it brought. That is a deliberate asymmetry with
``marketplace_orders``, which *does* upsert: an order's payment state legitimately
advances, while a promise is a thing that was said at a point in time and cannot
advance.

The one thing that *is* allowed to change is which source is authoritative, and
that is not stored at all — see :func:`authority`.

Why the internal half is stored too
-----------------------------------
The freight PulseSoc paid, the route that was chosen, the confidence tier. None of
it is ever shown to a buyer (§33-35), and all of it is needed later: §70's
delivery-accuracy measurement compares the promised window against the actual
arrival *per route*, and §71-72's self-calibration cannot learn a per-corridor
correction it has no record of. Reconstructing them afterwards is the
recomputation this module exists to avoid.

Kept in a separate column from the buyer half, not merged, so that the boundary
that matters at serving time survives into storage: a reader that wants the
buyer's sentence reads one column and cannot accidentally serialize the other.

What "after shipment" means
---------------------------
§41-42. Once a parcel has a tracking reference, the carrier knows things this
estimate never did — that it left the warehouse late, that it cleared customs
early, that it is out for delivery today. From that moment the carrier is the
better answer and the promise becomes the historical record it always was.

:func:`authority` decides that, and it deliberately *returns* the decision instead
of writing it: the stored promise is what was promised, and an order that shipped
has not changed what was promised. Storing a "superseded" flag would be storing a
derived fact that the tracking table already implies, and the two would disagree
the first time a tracking reference was corrected.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable, Dict, Optional

from services import db

from . import estimate, quote

#: Which source a surface should believe right now.
#:
#: ``PROMISE`` is the window the buyer agreed to. ``CARRIER`` means the parcel is
#: in a carrier's hands and the carrier's own information is closer to the truth.
#: There is no third value: an order that has not shipped has nothing but the
#: promise, and an order that has shipped has a carrier.
AUTHORITY_PROMISE = "PROMISE"
AUTHORITY_CARRIER = "CARRIER"

#: What a promise row is missing, when it is missing.
#:
#: Distinguished from "the estimate was unavailable at purchase" — which is a
#: *stored* promise whose state is ``UNAVAILABLE`` — because the two have opposite
#: implications. An absent row means nothing was recorded and nothing can be
#: measured; a stored refusal means we recorded, correctly, that we had no date to
#: give, and an accuracy report must not count that as a missed promise.
NOT_PROMISED = "no_promise_recorded"

SCHEMA_STATEMENTS = (
    # The primary key is declared as a *table* constraint, not inline on the
    # column, and that is not a style choice. `services.db._translate_create_table`
    # rewrites `<name> INTEGER PRIMARY KEY` to `<name> SERIAL PRIMARY KEY` for
    # Postgres, unconditionally and by regex — which is right for the ~170 tables
    # whose key the database invents, and wrong here, where the key is the
    # seller transaction id the caller already has. Inline, this table would get a
    # sequence and a `DEFAULT nextval(...)` in production and not in any test,
    # which is the exact shape of divergence that hides until a Postgres deploy.
    # `PRIMARY KEY (col)` has no `INTEGER` in front of it, so the regex leaves it
    # alone and both engines get the same constraint.
    """CREATE TABLE IF NOT EXISTS delivery_promises (
        seller_transaction_id INTEGER NOT NULL,
        listing_id INTEGER,
        variant_ref TEXT,
        quantity INTEGER DEFAULT 1,
        destination_country TEXT,
        destination_precision TEXT,
        state TEXT NOT NULL,
        reason TEXT,
        earliest TEXT,
        latest TEXT,
        confidence TEXT,
        buyer_json TEXT NOT NULL,
        internal_json TEXT NOT NULL,
        promised_at TEXT NOT NULL,
        PRIMARY KEY (seller_transaction_id))""",
    # `earliest`/`latest`/`confidence` are duplicated out of `buyer_json` on
    # purpose. They are the columns §70's accuracy reporting groups and filters
    # on, and a report that had to parse a JSON blob per row to find a date would
    # be a full scan with a deserialize per row. The blob remains the record of
    # record; these are an index over it, written by the same statement so they
    # cannot drift.
    "CREATE INDEX IF NOT EXISTS idx_delivery_promises_window "
    "ON delivery_promises(state, latest)",
    "CREATE INDEX IF NOT EXISTS idx_delivery_promises_listing "
    "ON delivery_promises(listing_id)",
)


def create_schema(cur) -> None:
    """Apply the statements with a caller's cursor.

    Same reason as ``marketplace_order_fulfillment.create_schema``: a test that
    builds these tables from a hand-copied imitation drifts from this file the
    first time a column is added, and then defends a schema production does not
    have.
    """
    for statement in SCHEMA_STATEMENTS:
        cur.execute(statement)


class PromiseRejected(ValueError):
    """The caller tried to record something that is not a delivery promise."""


def _json_text(value: Any) -> str:
    # `sort_keys` so two identical promises serialize identically. Without it a
    # byte-comparison of an expected against a stored promise fails on dict order,
    # which is a test that fails for a reason nobody can act on.
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def snapshot(*,
             seller_transaction_id: int,
             listing_id: Optional[int],
             variant_ref: str,
             quantity: int,
             destination: Dict[str, Any],
             result: Dict[str, Any],
             promised_at: str,
             connect: Callable[[], Any] = db.connect) -> Dict[str, Any]:
    """Write the promise for one paid order line, once.

    ``result`` is a whole composed quote — both halves, exactly as
    ``quote.quote_delivery`` returned it. Taking the composed value rather than a
    flattened set of fields means this module cannot disagree with the composer
    about what a promise contains: adding a field there adds it here, inside the
    blob, with no edit.

    Returns ``{"recorded": bool, "promise": {...}}``. ``recorded`` is false when a
    row already existed — which is the normal outcome of a redelivered payment
    webhook, not an error. The returned promise is always the *stored* one, so a
    caller that logs what it wrote logs what is actually there.

    Raises :class:`PromiseRejected` for a result that is not a composed quote.
    The alternative — storing a best-effort subset — would put a row in the
    accuracy ledger that claims a promise was made and cannot say what it was.
    """
    transaction_id = int(seller_transaction_id or 0)
    if transaction_id <= 0:
        raise PromiseRejected("a promise belongs to one seller transaction")
    buyer = (result or {}).get("buyer")
    internal = (result or {}).get("internal")
    if not isinstance(buyer, dict) or not isinstance(internal, dict):
        raise PromiseRejected("a promise is composed from a whole quote, both halves")
    missing = quote.BUYER_FIELDS - set(buyer)
    if missing:
        raise PromiseRejected(f"the buyer half is incomplete: {sorted(missing)}")

    where = dict(destination or {})
    row = (
        transaction_id,
        int(listing_id) if listing_id else None,
        str(variant_ref or ""),
        max(1, int(quantity or 1)),
        where.get("country"),
        where.get("precision"),
        str(buyer["state"]),
        buyer.get("reason"),
        buyer.get("earliest"),
        buyer.get("latest"),
        buyer.get("confidence"),
        _json_text(buyer),
        _json_text(internal),
        str(promised_at),
    )

    conn = connect()
    try:
        cursor = conn.cursor()
        try:
            # `DO NOTHING`, never `DO UPDATE`. This is the immutability rule
            # expressed in the only place that can enforce it: a second write for
            # the same order is silently the first write's outcome. A webhook
            # redelivered a week later must not overwrite a week-old promise with
            # today's estimate.
            cursor.execute(
                "INSERT INTO delivery_promises "
                "(seller_transaction_id,listing_id,variant_ref,quantity,destination_country,"
                " destination_precision,state,reason,earliest,latest,confidence,buyer_json,"
                " internal_json,promised_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(seller_transaction_id) DO NOTHING",
                row,
            )
            # `rowcount` and not a follow-up SELECT-if-zero: on both dialects a
            # conflicting `DO NOTHING` reports zero affected rows, and asking the
            # database "did I write it" is cheaper and more honest than asking
            # "is something there now", which cannot tell a skipped insert from a
            # concurrent one.
            recorded = cursor.rowcount == 1
        finally:
            cursor.close()
        conn.commit()
    finally:
        conn.close()

    stored = read(transaction_id, connect=connect)
    return {"recorded": recorded, "promise": stored}


def read(seller_transaction_id: int, *,
         connect: Callable[[], Any] = db.connect) -> Optional[Dict[str, Any]]:
    """The stored promise, or ``None``.

    ``None`` means no row — an order placed before this package existed, or one
    whose checkout never recorded a promise. Deliberately not an empty promise:
    §70 has to be able to exclude those from an accuracy measurement instead of
    scoring them as failures, and a caller that receives a plausible-looking blank
    cannot.
    """
    transaction_id = int(seller_transaction_id or 0)
    if transaction_id <= 0:
        return None
    conn = connect()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT buyer_json, internal_json, variant_ref, quantity, "
                "destination_country, destination_precision, promised_at, listing_id "
                "FROM delivery_promises WHERE seller_transaction_id = ?",
                (transaction_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        conn.close()
    if row is None:
        return None
    # `row_values` because iterating a row yields values on SQLite and column
    # names on Postgres. The positional unpack below is only safe through it.
    values = db.row_values(row)
    buyer_json, internal_json = values[0], values[1]
    return {
        "seller_transaction_id": transaction_id,
        "listing_id": values[7],
        "variant_ref": values[2],
        "quantity": values[3],
        "destination": {"country": values[4], "precision": values[5]},
        "promised_at": values[6],
        # Parsed back into the same two halves the composer produced, so a reader
        # of a stored promise and a reader of a live quote handle the same shape.
        "buyer": json.loads(buyer_json),
        "internal": json.loads(internal_json),
    }


def authority(promise: Optional[Dict[str, Any]],
              fulfillment: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Which source a surface should show, and what it should show from it.

    ``fulfillment`` is a row from ``marketplace_order_fulfillment`` — or anything
    shaped like one: what is read is ``tracking_reference``, ``shipped_at`` and
    ``delivered_at``. A row is not required; ``None`` is an order with no
    fulfillment record, which is the state every order is in before it has one.

    Returns ``{"source", "window", "tracking", "promise_window"}``.

    ``promise_window`` is always the promise, unchanged, even once the carrier has
    taken over. That is the point of §36-37: the historical promise stays
    readable beside the current expectation, so a dispute can be answered and an
    accuracy report can be computed, and neither has to trust that the current
    view was not the promised one.

    The transition is on the *tracking reference*, not on the state string. A
    seller can set a state; ``marketplace_order_fulfillment`` refuses to accept
    ``shipped`` without a reference precisely because a state a seller controls
    alone is not evidence. So the thing that transfers authority here is the
    artifact that a third party can be asked about.
    """
    stored = promise if isinstance(promise, dict) else None
    promise_window = _window_of(stored)

    row = dict(fulfillment or {})
    reference = str(row.get("tracking_reference") or "").strip()
    # A delivered order is terminal whether or not anyone recorded a reference:
    # local pickup and hand-delivery have no carrier, and an arrival that has been
    # confirmed is more authoritative than any estimate of it. The reference is
    # what transfers authority *in flight*; delivery ends the question.
    delivered = bool(str(row.get("delivered_at") or "").strip())
    if not reference and not delivered:
        return {
            "source": AUTHORITY_PROMISE,
            "window": promise_window,
            "tracking": None,
            "promise_window": promise_window,
        }

    return {
        "source": AUTHORITY_CARRIER,
        # No window from the carrier. This package does not poll a carrier and
        # must not appear to: a window here would either be the promise wearing a
        # carrier's name — the exact fabrication §124 forbids — or a number
        # invented at this layer. A tracking-fed window is a later slice with a
        # real source behind it, and until then the honest answer is that the
        # carrier is authoritative and has told us a reference, not a date.
        "window": None,
        "tracking": {
            "carrier": str(row.get("carrier") or "") or None,
            "reference": reference or None,
            "url": str(row.get("tracking_url") or "") or None,
            "shipped_at": str(row.get("shipped_at") or "") or None,
            "delivered_at": str(row.get("delivered_at") or "") or None,
        },
        "promise_window": promise_window,
    }


def _window_of(stored: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The buyer-facing window of a stored promise, or ``None``.

    ``None`` for an absent row *and* for a stored refusal, because neither is a
    window — but they are told apart by ``reason``, which is why the caller of
    this gets the whole promise and not just its window.
    """
    if not stored:
        return None
    buyer = stored.get("buyer") or {}
    if buyer.get("state") != estimate.STATE_ESTIMATED:
        return None
    return {
        "earliest": buyer.get("earliest"),
        "latest": buyer.get("latest"),
        "confidence": buyer.get("confidence"),
        # Carried so a surface reading a historical promise renders the same
        # "Estimated", never "Guaranteed", wording as the live one. §58/§125 do not
        # stop applying because the order is in the past.
        "guaranteed": False,
        "is_estimate": True,
    }


def accuracy(promise: Optional[Dict[str, Any]],
             delivered_on: Optional[str]) -> Dict[str, Any]:
    """Did the promise hold? §70's single measurement, computed from stored facts.

    ``delivered_on`` is a ``YYYY-MM-DD`` calendar day. Returns
    ``{"measurable", "reason", "within", "days_early", "days_late"}``.

    ``measurable`` is false — with a reason — far more often than it is true, and
    that is the whole value of the function. An order with no promise row, a
    promise that was a refusal, an order not yet delivered: all of those must be
    *excluded* from an accuracy figure rather than counted as hits or misses.
    Counting an unmeasurable order either way is how a calibration loop learns
    from noise.
    """
    if not promise:
        return _unmeasurable(NOT_PROMISED)
    buyer = promise.get("buyer") or {}
    if buyer.get("state") != estimate.STATE_ESTIMATED:
        # We correctly said we had no date. Not a missed promise.
        return _unmeasurable(str(buyer.get("reason") or "no_window_promised"))
    earliest, latest = buyer.get("earliest"), buyer.get("latest")
    if not (_is_day(earliest) and _is_day(latest)):
        return _unmeasurable("stored_window_unusable")
    if not _is_day(delivered_on):
        return _unmeasurable("not_delivered_yet")

    actual = str(delivered_on)
    early = _days_between(actual, str(earliest)) if actual < str(earliest) else 0
    late = _days_between(str(latest), actual) if actual > str(latest) else 0
    return {
        "measurable": True,
        "reason": None,
        "within": early == 0 and late == 0,
        "days_early": early,
        "days_late": late,
    }


def _unmeasurable(reason: str) -> Dict[str, Any]:
    return {"measurable": False, "reason": reason,
            "within": None, "days_early": None, "days_late": None}


def _is_day(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        _parse_day(value)
    except ValueError:
        return False
    return True


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


def _days_between(start: str, end: str) -> int:
    return (_parse_day(end) - _parse_day(start)).days
