"""Marketplace Stage 3 — core primitives (Parts 1-4) exercised DIRECTLY.

The api/admin/assistant suites reach these modules end-to-end through the
controller; this suite pins the primitives themselves so a regression in the
seller service, the order state machine, the ledger settlement, or the refund
guard is caught at the layer it lives in:

  * schema.ensure_schema is idempotent and creates the canonical tables;
  * seller approval gate (not-approved -> 403, account-hold overrides all);
  * product validation + lifecycle (draft, publish-with-no-inventory refused,
    ownership does not leak, public in_stock projection, illegal transition);
  * order state machine (self-purchase refused, inventory decrement on pay,
    escrow capture, full settlement math, cancel-only-before-pay, illegal jumps);
  * money settlement is computed from the CURRENT escrow balance so a prior
    partial refund is netted out and escrow always zeroes exactly;
  * refund guard (partial nets out, over-refund refused by the ledger overdraft
    guard, full refund -> 'refunded'), dispute refund drains escrow, verified
    review requires a completed order, payout balance reads the accrual.

    python tests/business_os/test_marketplace_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_mktcore_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as svc  # noqa: E402
from services.business_os.marketplace import orders as orders_mod  # noqa: E402
from services.business_os.marketplace import refunds as refunds_mod  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 700
BUYER = 701
OTHER = 702
ADMIN = "admin:7"


def setup_module(module=None):
    mkt_schema.ensure_schema()
    mkt_schema.ensure_schema()  # idempotent second call must not raise
    ledger.ensure_schema()


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def _approve(uid):
    svc.upsert_seller(uid, display_name="S")
    svc.set_seller_status(uid, "approved", actor=ADMIN)


def _expect(fn, code=None, http=None):
    try:
        fn()
    except MarketplaceError as e:
        if code is not None:
            assert e.code == code, f"expected code {code}, got {e.code}"
        if http is not None:
            assert e.http_status == http, f"expected http {http}, got {e.http_status}"
        return
    raise AssertionError(f"expected MarketplaceError(code={code}, http={http})")


# --- (a) schema + seller approval gate --------------------------------------
def test_seller_approval_gate():
    # brand-new seller starts pending -> not eligible to sell
    svc.upsert_seller(SELLER, display_name="S")
    assert svc.get_seller(SELLER)["status"] == "pending"
    _expect(lambda: svc.require_active_seller(SELLER, _ctx()),
            code="seller_not_approved", http=403)
    # approve -> eligible
    svc.set_seller_status(SELLER, "approved", actor=ADMIN)
    assert svc.require_active_seller(SELLER, _ctx())["status"] == "approved"
    # account hold overrides approval
    _expect(lambda: svc.require_active_seller(SELLER, _ctx(status="suspended")),
            code="account_hold", http=403)
    _expect(lambda: svc.require_active_seller(SELLER, _ctx(access=0)),
            code="account_hold", http=403)
    # set_seller_status requires an actor
    _expect(lambda: svc.set_seller_status(SELLER, "approved", actor=""),
            code="actor_required", http=400)


# --- (b) product validation + lifecycle + ownership -------------------------
def test_product_validation_and_lifecycle():
    _approve(SELLER)
    # invalid price / fulfillment rejected
    _expect(lambda: svc.create_product(SELLER, title="x", price_cents=-1,
                                       context=_ctx()), code="invalid_price")
    _expect(lambda: svc.create_product(SELLER, title="x", price_cents=100,
                                       fulfillment_type="teleport", context=_ctx()),
            code="invalid_fulfillment")
    _expect(lambda: svc.create_product(SELLER, title="", price_cents=100,
                                       context=_ctx()), code="title_required")
    # valid draft product
    p = svc.create_product(SELLER, title="Widget", price_cents=2000,
                           fulfillment_type="physical", inventory_qty=5, context=_ctx())
    pid = p["product_id"]
    assert p["status"] == "draft"
    # non-owner read does not leak existence
    assert svc.get_product(pid, requester_user_id=OTHER) is None
    # not public until active
    assert svc.get_product(pid, for_public=True) is None
    # publish -> active, public projection carries in_stock
    svc.transition_product(SELLER, pid, "publish", context=_ctx())
    pub = svc.public_product(svc.get_product(pid, for_public=True))
    assert pub["status"] == "active" and pub["in_stock"] is True
    # illegal transition: cannot restore an active product straight to draft
    _expect(lambda: svc.transition_product(SELLER, pid, "restore", context=_ctx()),
            code="illegal_transition", http=409)


def test_publish_requires_inventory():
    _approve(SELLER)
    pid = svc.create_product(SELLER, title="ZeroStock", price_cents=1000,
                             fulfillment_type="physical", inventory_qty=0,
                             context=_ctx())["product_id"]
    _expect(lambda: svc.transition_product(SELLER, pid, "publish", context=_ctx()),
            code="no_inventory", http=409)


# --- (c) order state machine + full settlement ------------------------------
def _live_product(seller, price=2000, inv=5):
    pid = svc.create_product(seller, title="W", price_cents=price,
                             fulfillment_type="physical", inventory_qty=inv,
                             context=_ctx())["product_id"]
    svc.transition_product(seller, pid, "publish", context=_ctx())
    return pid


def test_order_lifecycle_and_settlement():
    _approve(SELLER)
    pid = _live_product(SELLER, price=2000, inv=5)
    # a seller cannot buy their own product
    _expect(lambda: orders_mod.create_order(SELLER, pid, quantity=1, context=_ctx()),
            code="self_purchase")
    # over-quantity refused
    _expect(lambda: orders_mod.create_order(BUYER, pid, quantity=99, context=_ctx()),
            code="insufficient_inventory", http=409)

    oid = orders_mod.create_order(BUYER, pid, quantity=2, context=_ctx())["order_id"]
    assert orders_mod.get_order(oid)["status"] == "created"
    # cannot complete before paying (illegal transition)
    _expect(lambda: orders_mod.complete_order(oid, BUYER, context=_ctx()),
            code="illegal_transition", http=409)

    # pay: inventory decrements 5 -> 3, escrow captures the $40 total
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    assert svc.get_product(pid, requester_user_id=SELLER)["inventory_qty"] == 3
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 4000

    orders_mod.fulfill_order(oid, SELLER, tracking_ref="T")
    orders_mod.complete_order(oid, BUYER, context=_ctx())
    # settlement: escrow drains exactly, fee 10% to revenue, net to seller payable
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 0
    assert ledger.get_balance(orders_mod.seller_payable_account(SELLER)) == 3600
    assert ledger.get_balance(orders_mod.PLATFORM_REVENUE_ACCOUNT) == 400
    # completed is terminal
    assert orders_mod.get_order(oid)["status"] == "completed"


def test_cancel_only_before_payment():
    _approve(SELLER)
    pid = _live_product(SELLER, price=1500, inv=3)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.cancel_order(oid, BUYER, reason="changed mind", context=_ctx())
    assert orders_mod.get_order(oid)["status"] == "cancelled"
    # a fresh, paid order can no longer be cancelled
    oid2 = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.pay_order(oid2, BUYER, context=_ctx())
    _expect(lambda: orders_mod.cancel_order(oid2, BUYER, context=_ctx()),
            code="illegal_transition", http=409)


# --- (d) refund guard + settlement-from-current-escrow ----------------------
def test_refund_partial_over_and_full():
    _approve(SELLER)
    pid = _live_product(SELLER, price=2000, inv=5)
    oid = orders_mod.create_order(BUYER, pid, quantity=2, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())  # escrow 4000

    # actor + reason are mandatory
    _expect(lambda: refunds_mod.refund_order(oid, amount_cents=500, reason="x", actor=""),
            code="actor_required")
    _expect(lambda: refunds_mod.refund_order(oid, amount_cents=500, reason="", actor=ADMIN),
            code="reason_required")

    # partial 500 -> escrow 3500, order still paid
    refunds_mod.refund_order(oid, amount_cents=500, reason="goodwill", actor=ADMIN)
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 3500
    assert orders_mod.get_order(oid)["status"] == "paid"

    # over-refund of the remaining escrow is refused by the ledger overdraft guard
    _expect(lambda: refunds_mod.refund_order(oid, amount_cents=9999, reason="oops",
                                             actor=ADMIN),
            code="refund_exceeds_escrow", http=409)

    # full remaining refund -> escrow 0, order refunded
    refunds_mod.refund_order(oid, amount_cents=None, reason="return", actor=ADMIN)
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 0
    assert orders_mod.get_order(oid)["status"] == "refunded"
    # a refunded order is terminal — nothing left to refund
    _expect(lambda: refunds_mod.refund_order(oid, reason="again", actor=ADMIN),
            code="not_refundable", http=409)


def test_settlement_nets_prior_partial_refund():
    _approve(SELLER)
    S2 = 710
    _approve(S2)
    pid = _live_product(S2, price=2000, inv=5)
    oid = orders_mod.create_order(BUYER, pid, quantity=2, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())  # escrow 4000
    refunds_mod.refund_order(oid, amount_cents=1000, reason="partial", actor=ADMIN)
    orders_mod.fulfill_order(oid, S2, tracking_ref="T")
    orders_mod.complete_order(oid, BUYER, context=_ctx())
    # settled from the CURRENT escrow of 3000: fee 300, net 2700, escrow zeroes
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 0
    assert ledger.get_balance(orders_mod.seller_payable_account(S2)) == 2700


# --- (e) dispute refund + verified review + payout read ---------------------
def test_dispute_refund_and_verified_review():
    _approve(SELLER)
    S3 = 720
    B3 = 721
    _approve(S3)
    pid = _live_product(S3, price=2000, inv=5)
    oid = orders_mod.create_order(B3, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, B3, context=_ctx())  # escrow 2000

    # a review requires a COMPLETED order
    _expect(lambda: refunds_mod.create_review(B3, product_id=pid, order_id=oid,
                                              rating=5, context=_ctx()),
            code="order_not_completed", http=409)

    # open a dispute; a 'refund' resolution drains escrow and refunds the order
    did = refunds_mod.open_dispute(oid, B3, reason="not as described")["dispute_id"]
    refunds_mod.resolve_dispute(did, resolution="refund", actor=ADMIN,
                                reason="buyer right")
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 0
    assert orders_mod.get_order(oid)["status"] == "refunded"
    # cannot resolve the same dispute twice
    _expect(lambda: refunds_mod.resolve_dispute(did, resolution="deny", actor=ADMIN,
                                                reason="x"),
            code="already_resolved", http=409)


def test_verified_review_and_payout_balance():
    _approve(SELLER)
    S4 = 730
    B4 = 731
    _approve(S4)
    pid = _live_product(S4, price=2000, inv=5)
    oid = orders_mod.create_order(B4, pid, quantity=2, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, B4, context=_ctx())
    orders_mod.fulfill_order(oid, S4, tracking_ref="T")
    orders_mod.complete_order(oid, B4, context=_ctx())

    # rating must be 1..5
    _expect(lambda: refunds_mod.create_review(B4, product_id=pid, order_id=oid,
                                              rating=9, context=_ctx()),
            code="invalid_rating")
    rv = refunds_mod.create_review(B4, product_id=pid, order_id=oid, rating=5,
                                   body="great", context=_ctx())
    assert rv["rating"] == 5
    # one review per (buyer, order, product)
    _expect(lambda: refunds_mod.create_review(B4, product_id=pid, order_id=oid,
                                              rating=4, context=_ctx()),
            code="already_reviewed", http=409)
    summ = refunds_mod.product_rating_summary(pid)
    assert summ["review_count"] == 1 and summ["average_rating"] == 5.0

    # payout balance reads the accrual (net of the 10% fee on $40)
    bal = refunds_mod.seller_payout_balance(S4)
    assert bal["payable_cents"] == 3600
    assert bal["disbursement"] == "provider_side_out_of_scope"


def _run_standalone():
    setup_module()
    tests = [
        test_seller_approval_gate,
        test_product_validation_and_lifecycle,
        test_publish_requires_inventory,
        test_order_lifecycle_and_settlement,
        test_cancel_only_before_payment,
        test_refund_partial_over_and_full,
        test_settlement_nets_prior_partial_refund,
        test_dispute_refund_and_verified_review,
        test_verified_review_and_payout_balance,
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
