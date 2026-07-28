"""Business OS — Section 5 (Orders) canonical domain facade, exercised DIRECTLY.

Proves the Orders domain is a faithful REUSE of the one canonical marketplace order
engine + shared ledger — not a second order system:

  * DARK when BUSINESS_OS_ORDERS is off — every entry point raises 503 disabled,
    even while the underlying marketplace engine is enabled;
  * a full lifecycle driven ENTIRELY through the orders facade (create -> pay ->
    fulfill -> complete) lands on the SAME business_os_mkt_orders row the engine owns,
    and money settles through the SAME shared ledger (escrow zeroes, seller net
    accrues, platform fee accrues);
  * no new order table is created by importing/using this package;
  * ownership scoping and illegal transitions are inherited from the engine
    (stranger read -> None, illegal jump -> 409);
  * refund + payout accrual are reachable through the facade and hit the shared ledger.

    python tests/business_os/test_orders_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_orders_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"
os.environ["BUSINESS_OS_ORDERS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.orders import service as svc  # noqa: E402
from services.business_os.orders.service import OrderError  # noqa: E402
from services.business_os.marketplace import orders as _engine  # noqa: E402


SELLER = 700
BUYER = 701
STRANGER = 702
ADMIN = "admin:7"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ledger.ensure_schema()


def _approved_seller(uid=SELLER):
    mkt.upsert_seller(uid, display_name="S")
    mkt.set_seller_status(uid, "approved", actor=ADMIN)


def _digital_product(price=1000):
    """A published digital product (NULL inventory = unlimited) owned by SELLER."""
    _approved_seller(SELLER)
    p = mkt.create_product(SELLER, title="Ebook", price_cents=price,
                           fulfillment_type="digital", context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    return p["product_id"]


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_ORDERS"] = ""
    try:
        for fn in (
            lambda: svc.create_order(BUYER, "mktp_x"),
            lambda: svc.get_order("mkto_x"),
            lambda: svc.list_orders(buyer_user_id=BUYER),
            lambda: svc.order_money_summary("mkto_x"),
        ):
            try:
                fn()
                raise AssertionError("expected disabled")
            except OrderError as e:
                assert e.http_status == 503 and e.code == "disabled", (e.http_status, e.code)
    finally:
        os.environ["BUSINESS_OS_ORDERS"] = "on"


def test_vocabulary_is_reexported_not_redefined():
    # Same object identity as the engine — one definition, not a copy.
    assert svc.ORDER_STATUSES is _engine.ORDER_STATUSES
    assert svc.ALLOWED_ORDER_TRANSITIONS is _engine.ALLOWED_ORDER_TRANSITIONS


def test_no_new_order_table_created():
    # The facade must not have introduced any business_os_orders* table.
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_orders%'").fetchall()
        assert rows == [], [dict(r) if hasattr(r, 'keys') else r for r in rows]
    finally:
        conn.close()


def test_full_lifecycle_through_facade_hits_shared_ledger():
    pid = _digital_product(price=1000)
    order = svc.create_order(BUYER, pid, quantity=2, context=_ctx())
    oid = order["order_id"]
    assert order["status"] == "created"
    assert order["total_cents"] == 2000

    # Same row the engine owns.
    assert _engine.get_order(oid)["order_id"] == oid

    svc.pay_order(oid, BUYER, context=_ctx())
    # Captured into escrow via the shared ledger.
    assert ledger.get_balance(_engine.escrow_account(oid), "usd") == 2000

    svc.fulfill_order(oid, SELLER, context=_ctx())
    svc.complete_order(oid, BUYER, context=_ctx())

    # Escrow drains to exactly zero; fee + seller net accrue on the shared ledger.
    assert ledger.get_balance(_engine.escrow_account(oid), "usd") == 0
    assert ledger.get_balance(_engine.seller_payable_account(SELLER), "usd") == 1800  # 90%
    summary = svc.order_money_summary(oid)
    assert summary["status"] == "completed"
    assert summary["platform_fee_cents"] == 200  # 10%


def test_ownership_scoping_inherited():
    pid = _digital_product(price=500)
    order = svc.create_order(BUYER, pid, context=_ctx())
    oid = order["order_id"]
    # Stranger cannot read (existence not leaked).
    assert svc.get_order(oid, requester_user_id=STRANGER) is None
    assert svc.get_order_detail(oid, requester_user_id=STRANGER) is None
    # Buyer sees full detail (items + events).
    detail = svc.get_order_detail(oid, requester_user_id=BUYER)
    assert detail["order_id"] == oid
    assert len(detail["items"]) == 1
    assert any(e["to_status"] == "created" for e in detail["events"])


def test_illegal_transition_inherited():
    pid = _digital_product(price=500)
    oid = svc.create_order(BUYER, pid, context=_ctx())["order_id"]
    # Cannot fulfill before paying.
    try:
        svc.fulfill_order(oid, SELLER, context=_ctx())
        raise AssertionError("expected illegal transition")
    except OrderError as e:
        assert e.http_status == 409 and e.code == "illegal_transition", (e.http_status, e.code)


def test_self_purchase_refused_inherited():
    pid = _digital_product(price=500)
    try:
        svc.create_order(SELLER, pid, context=_ctx())
        raise AssertionError("expected self_purchase")
    except OrderError as e:
        assert e.http_status == 400 and e.code == "self_purchase", (e.http_status, e.code)


def test_account_hold_beats_write_inherited():
    pid = _digital_product(price=500)
    try:
        svc.create_order(BUYER, pid, context=_ctx(status="suspended"))
        raise AssertionError("expected account hold")
    except OrderError as e:
        assert e.http_status == 403, e.http_status


def test_refund_through_facade_hits_shared_ledger():
    pid = _digital_product(price=1000)
    oid = svc.create_order(BUYER, pid, context=_ctx())["order_id"]
    svc.pay_order(oid, BUYER, context=_ctx())
    assert ledger.get_balance(_engine.escrow_account(oid), "usd") == 1000
    svc.refund_order(oid, amount_cents=400, reason="partial", actor=ADMIN)
    # Partial refund drains escrow back to intake on the shared ledger.
    assert ledger.get_balance(_engine.escrow_account(oid), "usd") == 600
    refunds = svc.list_refunds(oid)
    assert len(refunds) == 1 and refunds[0]["amount_cents"] == 400


def test_seller_payout_balance_reads_accrual():
    pid = _digital_product(price=2000)
    before = svc.seller_payout_balance(SELLER)["payable_cents"]
    oid = svc.create_order(BUYER, pid, context=_ctx())["order_id"]
    svc.pay_order(oid, BUYER, context=_ctx())
    svc.fulfill_order(oid, SELLER, context=_ctx())
    svc.complete_order(oid, BUYER, context=_ctx())
    after = svc.seller_payout_balance(SELLER)["payable_cents"]
    assert after - before == 1800  # 90% of 2000 accrues to the shared ledger


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_vocabulary_is_reexported_not_redefined,
        test_no_new_order_table_created,
        test_full_lifecycle_through_facade_hits_shared_ledger,
        test_ownership_scoping_inherited,
        test_illegal_transition_inherited,
        test_self_purchase_refused_inherited,
        test_account_hold_beats_write_inherited,
        test_refund_through_facade_hits_shared_ledger,
        test_seller_payout_balance_reads_accrual,
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
