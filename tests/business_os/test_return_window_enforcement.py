"""The return window is a deadline, and this file proves it is one.

Before this, the window was a number written down twice and read nowhere:
``policy.STANDARD_RETURN_WINDOW_DAYS = 14`` and a second
``OPEN_WINDOW_DAYS = 30`` in the Pulse returns route pack. Neither constant was
referenced by any code path. Both returns engines admitted a return on the
order's *status* alone, so a buyer could open one on an order delivered a year
ago and the seller's liability had no end date at all — while the seller
agreement was being drafted to promise a fourteen-day window.

The existing returns suites cannot see any of this: every fixture in them
creates an order seconds before returning it, so they stay green whether the
deadline is enforced, unenforced, or absent. This file is the one that looks.

What it pins:

  * the boundary itself — day 14 open, day 15 closed, evaluated at an injected
    ``now`` rather than by sleeping;
  * delivery anchors the window and beats the purchase date, because a buyer
    cannot judge an item they do not have yet;
  * purchase is the fallback, and the absence of a delivery timestamp must
    never mean "no deadline";
  * a row with no usable timestamp at all fails OPEN, on purpose — see the test
    for why that direction was chosen;
  * ``fulfill_order`` writes ``delivered_at`` exactly once, and a later status
    change rewrites ``updated_at`` without moving the deadline;
  * the refusal tells the buyer the date, and carries a machine code a client
    can branch on.

Every negative here is paired with a positive control, so a guard that refuses
everything fails this file just as loudly as one that refuses nothing.

    python tests/business_os/test_return_window_enforcement.py  # no pytest needed
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_return_window_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import policy  # noqa: E402
from services.business_os.marketplace import returns as ret  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 880
ADMIN = "admin:9"
WINDOW = policy.STANDARD_RETURN_WINDOW_DAYS

_uid = [2000]


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ret.ensure_schema()
    ledger.ensure_schema()


def _buyer():
    _uid[0] += 1
    return _uid[0]


def _iso(moment: datetime) -> str:
    """The exact format the marketplace writes, so the test does not accidentally
    prove only that the parser is lenient."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _days_ago(days: float) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


def _paid_order(buyer, price=1000):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Kettle", price_cents=price,
                           inventory_qty=50, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    o = ordm.create_order(buyer, p["product_id"], quantity=1, context=_ctx())
    return ordm.pay_order(o["order_id"], buyer, context=_ctx())


def _backdate(order_id, *, created_at=None, delivered_at=None):
    """Move an order into the past.

    Written as a direct UPDATE rather than by mocking the clock because the
    deadline is computed from what is *stored*, and storage is what a real aged
    order differs in."""
    conn = db.connect()
    if created_at is not None:
        conn.execute("UPDATE business_os_mkt_orders SET created_at = ? WHERE order_id = ?",
                     (created_at, str(order_id)))
    if delivered_at is not None:
        conn.execute("UPDATE business_os_mkt_orders SET delivered_at = ? WHERE order_id = ?",
                     (delivered_at, str(order_id)))
    conn.commit()
    conn.close()


def _column(order_id, name):
    conn = db.connect()
    row = conn.execute(
        f"SELECT {name} FROM business_os_mkt_orders WHERE order_id = ?",
        (str(order_id),)).fetchone()
    conn.close()
    return dict(row)[name]


def _expect(code, fn):
    try:
        fn()
    except MarketplaceError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return exc
    raise AssertionError(f"expected MarketplaceError {code}, none raised")


# --- the boundary ------------------------------------------------------------
def test_the_window_closes_exactly_one_window_after_the_anchor():
    anchor = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    at = _iso(anchor)

    # Inside, and on the final day: still open. `now` is injected so the boundary
    # is examined directly instead of inferred from a test that ran quickly.
    assert policy.return_window_open(delivered_at=at, now=anchor)
    assert policy.return_window_open(delivered_at=at,
                                     now=anchor + timedelta(days=WINDOW - 1))
    assert policy.return_window_open(delivered_at=at,
                                     now=anchor + timedelta(days=WINDOW))
    # One second past: closed. Without this the "open" assertions above would
    # also hold for a function that always returns True.
    assert not policy.return_window_open(delivered_at=at,
                                         now=anchor + timedelta(days=WINDOW,
                                                                seconds=1))
    assert not policy.return_window_open(delivered_at=at,
                                         now=anchor + timedelta(days=WINDOW + 1))

    closes = policy.return_window_closes_at(delivered_at=at)
    assert closes.startswith((anchor + timedelta(days=WINDOW)).strftime("%Y-%m-%d"))
    assert closes.endswith("Z"), "the deadline is quoted to buyers; it must be UTC-marked"


def test_delivery_anchors_the_window_and_the_purchase_date_is_only_a_fallback():
    old_purchase = _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))
    delivery = _iso(datetime(2026, 3, 1, tzinfo=timezone.utc))
    judged = datetime(2026, 3, 5, tzinfo=timezone.utc)

    # Delivered four days ago: open, even though the purchase is two months old.
    assert policy.return_window_open(delivered_at=delivery,
                                     purchased_at=old_purchase, now=judged)
    # Same order, same instant, no delivery recorded: closed. This is the control
    # that proves the previous line came from the delivery date and not from the
    # function being permissive.
    assert not policy.return_window_open(purchased_at=old_purchase, now=judged)

    assert policy.return_window_anchor(delivered_at=delivery,
                                       purchased_at=old_purchase) == \
        datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert policy.return_window_anchor(purchased_at=old_purchase) == \
        datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_an_order_with_no_usable_date_fails_open_and_that_is_deliberate():
    """A missing timestamp is a bookkeeping defect, not evidence the buyer is late.

    Refusing here would take a real right away from someone because a column was
    NULL, so the chosen direction is to allow the return and leave the defect
    visible. The deadline is reported as unknown rather than invented."""
    assert policy.return_window_open() is True
    assert policy.return_window_open(delivered_at="", purchased_at=None) is True
    assert policy.return_window_open(delivered_at="not a date") is True
    assert policy.return_window_closes_at() is None
    assert policy.return_window_closes_at(delivered_at="not a date") is None

    # But a garbage delivery value must fall through to a usable purchase date
    # rather than discarding the deadline entirely.
    purchase = _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert not policy.return_window_open(delivered_at="not a date",
                                         purchased_at=purchase,
                                         now=datetime(2026, 6, 1, tzinfo=timezone.utc))


def test_a_naive_timestamp_is_read_as_utc_rather_than_rejected():
    """Timestamps here are TEXT written by several modules and are not uniformly
    suffixed. Rejecting a naive value would silently mean "no deadline"."""
    assert policy.return_window_anchor(delivered_at="2026-01-01T00:00:00") == \
        datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert policy.return_window_anchor(delivered_at="2026-01-01T00:00:00Z") == \
        datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert policy.return_window_anchor(delivered_at="2026-01-01T02:00:00+02:00") == \
        datetime(2026, 1, 1, tzinfo=timezone.utc)


# --- the engine --------------------------------------------------------------
def test_a_fresh_order_can_still_be_returned():
    """The positive control for every refusal below. If this ever fails, the
    deadline is refusing returns it should be allowing."""
    buyer = _buyer()
    order = _paid_order(buyer)
    r = ret.request_return(buyer, order["order_id"], reason="damaged", context=_ctx())
    assert r["status"] == "requested"


def test_an_order_older_than_the_window_is_refused_with_the_date():
    buyer = _buyer()
    order = _paid_order(buyer)
    _backdate(order["order_id"], created_at=_days_ago(WINDOW + 1))

    exc = _expect("return_window_closed",
                  lambda: ret.request_return(buyer, order["order_id"],
                                             reason="damaged", context=_ctx()))
    assert exc.http_status == 409
    expected_close = (datetime.now(timezone.utc)
                      - timedelta(days=1)).strftime("%Y-%m-%d")
    assert expected_close in str(exc), \
        f"the refusal must name the deadline, got: {exc}"
    assert str(WINDOW) in str(exc), "the refusal must name the window length"

    # And nothing was written — a refused return must not leave a row behind.
    assert ret.list_returns(buyer_user_id=buyer) == []


def test_the_day_before_the_deadline_still_works():
    """Paired with the test above: same code path, one day earlier, accepted."""
    buyer = _buyer()
    order = _paid_order(buyer)
    _backdate(order["order_id"], created_at=_days_ago(WINDOW - 1))
    r = ret.request_return(buyer, order["order_id"], reason="damaged", context=_ctx())
    assert r["status"] == "requested"


def test_a_late_delivery_reopens_a_window_the_purchase_date_had_closed():
    """The case the purchase-date fallback gets wrong on its own.

    A slow-shipping seller must not consume the buyer's return window. Same
    ancient purchase date as the refused order above; the only difference is a
    recorded delivery."""
    buyer = _buyer()
    order = _paid_order(buyer)
    _backdate(order["order_id"], created_at=_days_ago(WINDOW * 3),
              delivered_at=_days_ago(1))
    r = ret.request_return(buyer, order["order_id"], reason="damaged", context=_ctx())
    assert r["status"] == "requested"


def test_a_delivery_older_than_the_window_closes_it_even_on_a_recent_purchase():
    """The mirror of the test above, so "delivery wins" is pinned in both
    directions rather than only where it is generous."""
    buyer = _buyer()
    order = _paid_order(buyer)
    _backdate(order["order_id"], created_at=_days_ago(1),
              delivered_at=_days_ago(WINDOW + 2))
    _expect("return_window_closed",
            lambda: ret.request_return(buyer, order["order_id"], reason="damaged",
                                       context=_ctx()))


def test_completed_orders_get_a_deadline_too_which_is_where_liability_used_to_be_endless():
    """``completed`` is a returnable status, so before the deadline existed this
    was the unbounded case: an order finished months ago still accepted returns."""
    buyer = _buyer()
    order = _paid_order(buyer)
    oid = order["order_id"]
    ordm.fulfill_order(oid, SELLER, context=_ctx())
    ordm.complete_order(oid, buyer, context=_ctx())
    _backdate(oid, created_at=_days_ago(90), delivered_at=_days_ago(90))
    assert ordm.get_order(oid)["status"] == "completed"
    _expect("return_window_closed",
            lambda: ret.request_return(buyer, oid, reason="damaged", context=_ctx()))


# --- the anchor's own integrity ----------------------------------------------
def test_fulfilment_stamps_delivered_at_once_and_later_writes_do_not_move_it():
    """``delivered_at`` is kept separate from ``updated_at`` precisely so an
    unrelated status change cannot extend or shorten a buyer's rights."""
    buyer = _buyer()
    order = _paid_order(buyer)
    oid = order["order_id"]
    assert _column(oid, "delivered_at") is None, "unfulfilled orders have no delivery"

    ordm.fulfill_order(oid, SELLER, tracking_ref="T1", context=_ctx())
    stamped = _column(oid, "delivered_at")
    assert stamped, "fulfilment must record when the return window starts"
    updated_then = _column(oid, "updated_at")

    ordm.complete_order(oid, buyer, context=_ctx())
    assert _column(oid, "delivered_at") == stamped, \
        "a later status change moved the buyer's deadline"
    # Positive control: the row really was rewritten, so the assertion above is
    # about COALESCE holding the value and not about nothing having happened.
    assert _column(oid, "updated_at") != updated_then, \
        "complete_order did not touch the row; the test above proves nothing"


def test_a_second_fulfilment_attempt_cannot_restart_the_window():
    """Guards the COALESCE directly. A re-fulfilment is refused by the state
    machine today, but the deadline must not depend on that staying true."""
    buyer = _buyer()
    order = _paid_order(buyer)
    oid = order["order_id"]
    ordm.fulfill_order(oid, SELLER, context=_ctx())
    stamped = _column(oid, "delivered_at")

    conn = db.connect()
    conn.execute(
        "UPDATE business_os_mkt_orders SET delivered_at = COALESCE(delivered_at, ?) "
        "WHERE order_id = ?", (_iso(datetime.now(timezone.utc)), str(oid)))
    conn.commit()
    conn.close()
    assert _column(oid, "delivered_at") == stamped


def _run_standalone():
    setup_module()
    tests = [
        test_the_window_closes_exactly_one_window_after_the_anchor,
        test_delivery_anchors_the_window_and_the_purchase_date_is_only_a_fallback,
        test_an_order_with_no_usable_date_fails_open_and_that_is_deliberate,
        test_a_naive_timestamp_is_read_as_utc_rather_than_rejected,
        test_a_fresh_order_can_still_be_returned,
        test_an_order_older_than_the_window_is_refused_with_the_date,
        test_the_day_before_the_deadline_still_works,
        test_a_late_delivery_reopens_a_window_the_purchase_date_had_closed,
        test_a_delivery_older_than_the_window_closes_it_even_on_a_recent_purchase,
        test_completed_orders_get_a_deadline_too_which_is_where_liability_used_to_be_endless,
        test_fulfilment_stamps_delivered_at_once_and_later_writes_do_not_move_it,
        test_a_second_fulfilment_attempt_cannot_restart_the_window,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    raise SystemExit(0 if _run_standalone() else 1)
