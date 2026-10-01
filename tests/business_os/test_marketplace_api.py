"""Marketplace Stage 3 — HTTP controller contract (Part 7 api.py).

Proves the framework-agnostic controller: (status, body) tuples with an ``ok`` bool,
the whole surface DARK (404) when the flag is off, unknown-field rejection, ownership
(non-owner ⇒ 404), the full buyer/seller order flow through the controller, the governed
assistant plan/execute, and the admin surface.

    python tests/business_os/test_marketplace_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_mktapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as svc  # noqa: E402
from services.business_os.marketplace import api  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 900
BUYER = 901
OTHER = 902
ADMIN = "admin:9"


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ledger.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)")
        conn.commit()
    finally:
        conn.close()


def _ctx():
    return {"account_status": "active", "access_enabled": 1}


def _approve(uid):
    svc.upsert_seller(uid, display_name="S")
    svc.set_seller_status(uid, "approved", actor=ADMIN)


# --- (a) dark surface when the flag is off ----------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = "0"
    try:
        assert api.register_seller(SELLER, {})[0] == 404
        assert api.create_product(SELLER, {})[0] == 404
        assert api.public_list_products()[0] == 404
        assert api.assistant_plan(SELLER, {"tool": "seller_products"})[0] == 404
        assert api.admin_get_order(ADMIN, "x")[0] == 404
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


# --- (b) unknown field is rejected ------------------------------------------
def test_unknown_field_rejected():
    _approve(SELLER)
    st, body = api.create_product(SELLER, {"title": "x", "price_cents": 100, "evil": 1},
                                  context=_ctx())
    assert st == 400 and body["code"] == "unknown_field", body


# --- (c) full catalog + storefront through the controller -------------------
def test_catalog_and_storefront():
    _approve(SELLER)
    st, body = api.create_product(
        SELLER, {"title": "Widget", "price_cents": 2000,
                 "fulfillment_type": "physical", "inventory_qty": 5}, context=_ctx())
    assert st == 201 and body["ok"], body
    pid = body["product"]["product_id"]

    # not yet public (draft)
    assert api.public_get_product(pid)[0] == 404

    st, body = api.product_lifecycle(SELLER, pid, "publish", context=_ctx())
    assert st == 200 and body["product"]["status"] == "active", body

    st, body = api.public_list_products(seller_user_id=SELLER)
    assert st == 200 and any(p["product_id"] == pid for p in body["products"]), body
    st, body = api.public_get_product(pid)
    assert st == 200 and body["product"]["in_stock"] is True, body


# --- (d) ownership: non-owner cannot read another's order -------------------
def test_order_ownership():
    _approve(SELLER)
    pid = api.create_product(
        SELLER, {"title": "W2", "price_cents": 1000, "fulfillment_type": "physical",
                 "inventory_qty": 3}, context=_ctx())[1]["product"]["product_id"]
    api.product_lifecycle(SELLER, pid, "publish", context=_ctx())
    oid = api.create_order(BUYER, {"product_id": pid, "quantity": 1},
                           context=_ctx())[1]["order"]["order_id"]
    # buyer + seller can read; OTHER cannot
    assert api.get_order(BUYER, oid)[0] == 200
    assert api.get_order(SELLER, oid)[0] == 200
    assert api.get_order(OTHER, oid)[0] == 404


# --- (e) buyer/seller order lifecycle through the controller ----------------
def test_full_order_lifecycle():
    _approve(SELLER)
    pid = api.create_product(
        SELLER, {"title": "W3", "price_cents": 2000, "fulfillment_type": "physical",
                 "inventory_qty": 5}, context=_ctx())[1]["product"]["product_id"]
    api.product_lifecycle(SELLER, pid, "publish", context=_ctx())
    oid = api.create_order(BUYER, {"product_id": pid, "quantity": 2},
                           context=_ctx())[1]["order"]["order_id"]

    assert api.pay_order(BUYER, oid, context=_ctx())[1]["order"]["status"] == "paid"
    assert api.fulfill_order(SELLER, oid, {"tracking_ref": "T"},
                             context=_ctx())[1]["order"]["status"] == "fulfilled"
    assert api.complete_order(BUYER, oid, context=_ctx())[1]["order"]["status"] == "completed"

    # verified-purchase review works now
    st, body = api.create_review(BUYER, {"product_id": pid, "order_id": oid,
                                          "rating": 5, "body": "great"}, context=_ctx())
    assert st == 201, body
    st, body = api.product_reviews(pid)
    assert st == 200 and body["summary"]["review_count"] == 1, body

    # payout accrued to the seller
    st, body = api.seller_payout_balance(SELLER)
    assert st == 200 and body["payout"]["payable_cents"] == 3600, body


# --- (f) governed assistant through the controller --------------------------
def test_assistant_controller():
    _approve(SELLER)
    pid = api.create_product(
        SELLER, {"title": "W4", "price_cents": 1000, "fulfillment_type": "physical",
                 "inventory_qty": 3}, context=_ctx())[1]["product"]["product_id"]
    api.product_lifecycle(SELLER, pid, "publish", context=_ctx())
    oid = api.create_order(BUYER, {"product_id": pid, "quantity": 1},
                           context=_ctx())[1]["order"]["order_id"]
    # plan a consequential tool -> token minted
    st, body = api.assistant_plan(BUYER, {"tool": "pay_order", "params": {"order_id": oid}})
    assert st == 200 and body["plan"]["requires_confirmation"] is True, body
    token = body["plan"]["confirmation_token"]
    # execute without token -> 428 surfaced as error body
    st, body = api.assistant_execute(BUYER, {"tool": "pay_order",
                                             "params": {"order_id": oid}})
    assert st == 428 and body["code"] == "confirmation_required", body
    # execute with token -> verified
    st, body = api.assistant_execute(BUYER, {"tool": "pay_order",
                                             "params": {"order_id": oid},
                                             "confirmation_token": token})
    assert st == 200 and body["result"]["verified"] is True, body


# --- (g) admin surface through the controller -------------------------------
def test_admin_controller():
    _approve(SELLER)
    pid = api.create_product(
        SELLER, {"title": "W5", "price_cents": 2000, "fulfillment_type": "physical",
                 "inventory_qty": 5}, context=_ctx())[1]["product"]["product_id"]
    api.product_lifecycle(SELLER, pid, "publish", context=_ctx())
    oid = api.create_order(BUYER, {"product_id": pid, "quantity": 1},
                           context=_ctx())[1]["order"]["order_id"]
    api.pay_order(BUYER, oid, context=_ctx())

    st, body = api.admin_get_order(ADMIN, oid)
    assert st == 200 and body["order"]["order_id"] == oid, body

    # refund requires a reason (admin module enforces)
    st, body = api.admin_refund_order(ADMIN, oid, {"amount_cents": 500})
    assert st == 400 and body["code"] == "reason_required", body
    st, body = api.admin_refund_order(ADMIN, oid, {"amount_cents": 500, "reason": "goodwill"})
    assert st == 200 and body["refund"]["refund"]["amount_cents"] == 500, body

    # restrict + appeal + grant through controller
    st, body = api.admin_restrict_seller(ADMIN, SELLER, {"reason": "policy"})
    assert st == 200 and body["result"]["after_status"] == "suspended", body
    aid = api.submit_appeal(SELLER, {"reason": "please"})[1]["appeal"]["appeal_id"]
    st, body = api.admin_resolve_appeal(ADMIN, aid, {"decision": "grant", "reason": "ok"})
    assert st == 200 and body["appeal"]["restriction_lifted"] is True, body
    assert svc.get_seller(SELLER)["status"] == "approved"


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_unknown_field_rejected,
        test_catalog_and_storefront,
        test_order_ownership,
        test_full_order_lifecycle,
        test_assistant_controller,
        test_admin_controller,
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
