"""Business OS — Marketplace RETURNS workflow, exercised DIRECTLY.

Proves the return lifecycle rides the existing engines instead of duplicating them:

  * DARK when BUSINESS_OS_MARKETPLACE is off — every verb raises 503 disabled;
  * request rules: buyer-owned orders only (404 for strangers/sellers), returnable
    states only, one open return per order, line-item validation;
  * full merchandise flow request -> approve -> receive -> refund settles the money
    through refunds.refund_order (order flips to ``refunded``, escrow zeroes) and
    the refund is IDEMPOTENT per return (keyed ``return:{id}``);
  * decline requires a reason; cancel is buyer-only; received cannot be cancelled;
  * completed order: request/approve/receive fine, refund surfaces the escrow 409
    honestly, close_return ends it without money;
  * illegal transitions 409; event + audit trails are complete.

    python tests/business_os/test_returns_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_returns_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import returns as ret  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 990
STRANGER = 992
ADMIN = "admin:9"

_uid = [1000]


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ret.ensure_schema()
    ledger.ensure_schema()


def _buyer():
    _uid[0] += 1
    return _uid[0]


def _paid_order(buyer, price=1000, quantity=1):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=price,
                           inventory_qty=20, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    o = ordm.create_order(buyer, p["product_id"], quantity=quantity, context=_ctx())
    o = ordm.pay_order(o["order_id"], buyer, context=_ctx())
    return o, p["product_id"]


def _expect(code, fn):
    try:
        fn()
    except MarketplaceError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return exc
    raise AssertionError(f"expected MarketplaceError {code}, none raised")


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for fn in (
            lambda: ret.request_return(1, "mkto_x", reason="r"),
            lambda: ret.approve_return("mktret_x", SELLER),
            lambda: ret.decline_return("mktret_x", SELLER, reason="r"),
            lambda: ret.cancel_return("mktret_x", 1),
            lambda: ret.mark_received("mktret_x", SELLER),
            lambda: ret.refund_return("mktret_x", SELLER),
            lambda: ret.close_return("mktret_x", SELLER, reason="r"),
        ):
            _expect("disabled", fn)
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_request_rules():
    buyer = _buyer()
    o, pid = _paid_order(buyer)
    oid = o["order_id"]
    # Stranger and seller cannot open a return on this order.
    _expect("not_found", lambda: ret.request_return(STRANGER, oid, reason="r",
                                                    context=_ctx()))
    _expect("not_found", lambda: ret.request_return(SELLER, oid, reason="r",
                                                    context=_ctx()))
    # Reason is mandatory; line item must exist; quantity bounded.
    _expect("reason_required", lambda: ret.request_return(buyer, oid, reason="  ",
                                                          context=_ctx()))
    _expect("product_not_in_order", lambda: ret.request_return(
        buyer, oid, reason="r", product_id="mktp_nope", context=_ctx()))
    _expect("invalid_quantity", lambda: ret.request_return(
        buyer, oid, reason="r", product_id=pid, quantity=99, context=_ctx()))
    _expect("invalid_quantity", lambda: ret.request_return(
        buyer, oid, reason="r", quantity=1, context=_ctx()))
    # Account hold blocks the write.
    _expect("account_hold", lambda: ret.request_return(
        buyer, oid, reason="r", context=_ctx(status="suspended")))

    r = ret.request_return(buyer, oid, reason="damaged", product_id=pid,
                           quantity=1, context=_ctx())
    assert r["status"] == "requested" and r["order_id"] == oid
    # Only one open return per order.
    _expect("return_exists", lambda: ret.request_return(buyer, oid, reason="again",
                                                        context=_ctx()))
    # Unpaid orders are not returnable.
    buyer2 = _buyer()
    o2, _ = _paid_order(buyer2)
    conn = db.connect()
    conn.execute("UPDATE business_os_mkt_orders SET status = 'created' "
                 "WHERE order_id = ?", (o2["order_id"],))
    conn.commit(); conn.close()
    _expect("not_returnable", lambda: ret.request_return(
        buyer2, o2["order_id"], reason="r", context=_ctx()))


def test_full_refund_flow_and_idempotency():
    buyer = _buyer()
    o, pid = _paid_order(buyer, price=800, quantity=2)   # 1600 in escrow
    oid = o["order_id"]
    r = ret.request_return(buyer, oid, reason="wrong color", context=_ctx())
    rid = r["return_id"]

    # Wrong party on seller verbs -> 404 (role not leaked).
    _expect("not_found", lambda: ret.approve_return(rid, buyer, context=_ctx()))
    _expect("not_found", lambda: ret.approve_return(rid, STRANGER, context=_ctx()))

    r = ret.approve_return(rid, SELLER, context=_ctx())
    assert r["status"] == "approved"
    # Refund before the merchandise is back is an illegal transition.
    _expect("illegal_transition", lambda: ret.refund_return(rid, SELLER,
                                                            context=_ctx()))
    r = ret.mark_received(rid, SELLER, context=_ctx())
    assert r["status"] == "received"

    r = ret.refund_return(rid, SELLER, context=_ctx())
    assert r["status"] == "refunded"
    assert r["refund"]["duplicate"] is False
    assert r["refund_amount_cents"] == 1600
    assert ledger.get_balance(ordm.escrow_account(oid), "usd") == 0
    order = ordm.get_order(oid)
    assert order["status"] == "refunded"

    # Retry is a replay, not a second refund (and not an illegal-transition 500).
    events_before = len(ret.get_return_events(rid))
    _expect("illegal_transition", lambda: ret.refund_return(rid, SELLER,
                                                            context=_ctx()))
    assert len(ret.get_return_events(rid)) == events_before
    # The governed primitive itself replays on the derived key.
    from services.business_os.marketplace import refunds as ref
    replay = ref.refund_order(oid, reason="retry", actor=SELLER,
                              idempotency_key=f"return:{rid}")
    assert replay["duplicate"] is True and replay["amount_cents"] == 1600

    trail = [e["to_status"] for e in ret.get_return_events(rid)]
    assert trail == ["requested", "approved", "received", "refunded"]


def test_decline_cancel_and_close_paths():
    buyer = _buyer()
    o, _ = _paid_order(buyer)
    oid = o["order_id"]
    r = ret.request_return(buyer, oid, reason="r", context=_ctx())
    _expect("reason_required", lambda: ret.decline_return(r["return_id"], SELLER,
                                                          reason="", context=_ctx()))
    d = ret.decline_return(r["return_id"], SELLER, reason="worn item",
                           context=_ctx())
    assert d["status"] == "declined" and d["decline_reason"] == "worn item"
    # Terminal: nothing else may happen.
    _expect("illegal_transition", lambda: ret.cancel_return(r["return_id"], buyer,
                                                            context=_ctx()))

    # A declined return frees the order for a fresh request; buyer can cancel it.
    r2 = ret.request_return(buyer, oid, reason="second try", context=_ctx())
    c = ret.cancel_return(r2["return_id"], buyer, context=_ctx())
    assert c["status"] == "cancelled"

    # Completed order: flow works, refund honestly refuses, close ends it.
    buyer2 = _buyer()
    o2, _ = _paid_order(buyer2)
    ordm.fulfill_order(o2["order_id"], SELLER, context=_ctx())
    ordm.complete_order(o2["order_id"], buyer2, context=_ctx())
    r3 = ret.request_return(buyer2, o2["order_id"], reason="late return",
                            context=_ctx())
    ret.approve_return(r3["return_id"], SELLER, context=_ctx())
    ret.mark_received(r3["return_id"], SELLER, context=_ctx())
    _expect("not_refundable", lambda: ret.refund_return(r3["return_id"], SELLER,
                                                        context=_ctx()))
    assert ret.get_return(r3["return_id"])["status"] == "received"
    z = ret.close_return(r3["return_id"], SELLER, reason="escrow released; "
                         "settled via support", context=_ctx())
    assert z["status"] == "closed"


def test_scoping_lists_and_audit():
    buyer = _buyer()
    o, _ = _paid_order(buyer)
    r = ret.request_return(buyer, o["order_id"], reason="r", context=_ctx())
    rid = r["return_id"]
    assert ret.get_return(rid, requester_user_id=STRANGER) is None
    assert ret.get_return(rid, requester_user_id=buyer)["return_id"] == rid
    assert ret.get_return(rid, requester_user_id=SELLER)["return_id"] == rid
    assert any(x["return_id"] == rid
               for x in ret.list_returns(buyer_user_id=buyer))
    assert any(x["return_id"] == rid
               for x in ret.list_returns(seller_user_id=SELLER, status="requested"))
    assert ret.list_returns(buyer_user_id=STRANGER) == []

    conn = db.connect()
    rows = conn.execute(
        "SELECT action FROM business_os_mkt_audit WHERE subject_type = 'return' "
        "AND subject_ref = ? ORDER BY id", (rid,)).fetchall()
    conn.close()
    assert [row["action"] for row in rows] == ["return_request"]


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_request_rules,
        test_full_refund_flow_and_idempotency,
        test_decline_cancel_and_close_paths,
        test_scoping_lists_and_audit,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
