"""Business OS — Section 5 (Orders) exercised through the HTTP CONTROLLER.

Where test_orders_core.py pins the canonical-facade service layer, this pins the
framework-agnostic ``api.py`` controller — the exact ``(status_code, body)`` contract
bot.py depends on, and proves the controller inherits the engine's rules:

  * DARK when BUSINESS_OS_ORDERS is off: every handler returns 404 (not 503) so no
    partial canonical path is exposed at the HTTP edge;
  * every body carries an ``ok`` bool;
  * create -> 201 (missing product_id -> 400); full lifecycle create/pay/fulfill/
    complete -> 200 and the money summary is server-authoritative;
  * ownership is not leaked: a stranger get -> 404, and stranger money-summary /
    refunds -> 404;
  * illegal transition surfaces as 409, self-purchase as 400, account hold as 403;
  * open_dispute requires a reason (-> 400) and returns 201.

    python tests/business_os/test_orders_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_orders_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"
os.environ["BUSINESS_OS_ORDERS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.orders import api as oapi  # noqa: E402


SELLER = 800
BUYER = 801
STRANGER = 802
ADMIN = "admin:8"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ledger.ensure_schema()


def _digital_product(price=1000):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Ebook", price_cents=price,
                           fulfillment_type="digital", context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    return p["product_id"]


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_ORDERS"] = ""
    try:
        for st, body in (
            oapi.create_order(BUYER, {"product_id": "x"}),
            oapi.get_order(BUYER, "mkto_x"),
            oapi.list_orders(BUYER),
            oapi.pay_order(BUYER, "mkto_x"),
            oapi.order_money_summary(BUYER, "mkto_x"),
            oapi.list_refunds(BUYER, "mkto_x"),
        ):
            assert st == 404 and body["ok"] is False, (st, body)
            assert body["code"] == "not_found", body
    finally:
        os.environ["BUSINESS_OS_ORDERS"] = "on"


def test_create_requires_product_id():
    st, body = oapi.create_order(BUYER, {})
    assert st == 400 and body["ok"] is False, (st, body)
    assert body["code"] == "invalid", body


def test_create_201_and_get_200_full_detail():
    pid = _digital_product(price=1500)
    st, body = oapi.create_order(BUYER, {"product_id": pid, "quantity": 2},
                                 context=_ctx())
    assert st == 201 and body["ok"] is True, (st, body)
    oid = body["order"]["order_id"]
    assert body["order"]["total_cents"] == 3000

    st, body = oapi.get_order(BUYER, oid)
    assert st == 200 and body["ok"] is True, (st, body)
    assert body["order"]["order_id"] == oid
    assert len(body["order"]["items"]) == 1
    assert "events" in body["order"]


def test_stranger_get_404_not_leaked():
    pid = _digital_product(price=500)
    _, body = oapi.create_order(BUYER, {"product_id": pid}, context=_ctx())
    oid = body["order"]["order_id"]
    st, body = oapi.get_order(STRANGER, oid)
    assert st == 404 and body["code"] == "not_found", (st, body)


def test_full_lifecycle_through_controller_and_money_summary():
    pid = _digital_product(price=1000)
    _, body = oapi.create_order(BUYER, {"product_id": pid}, context=_ctx())
    oid = body["order"]["order_id"]

    st, body = oapi.pay_order(BUYER, oid, context=_ctx())
    assert st == 200 and body["order"]["status"] == "paid", (st, body)

    st, body = oapi.fulfill_order(SELLER, oid, {"tracking_ref": "T1"}, context=_ctx())
    assert st == 200 and body["order"]["status"] == "fulfilled", (st, body)

    st, body = oapi.complete_order(BUYER, oid, context=_ctx())
    assert st == 200 and body["order"]["status"] == "completed", (st, body)

    # Money summary is server-authoritative and ownership-scoped.
    st, body = oapi.order_money_summary(BUYER, oid)
    assert st == 200 and body["ok"] is True, (st, body)
    assert body["summary"]["platform_fee_cents"] == 100  # 10% of 1000
    assert body["summary"]["seller_payable_cents"] == 900

    # Stranger cannot see the money summary (existence not leaked).
    st, body = oapi.order_money_summary(STRANGER, oid)
    assert st == 404 and body["code"] == "not_found", (st, body)


def test_illegal_transition_409():
    pid = _digital_product(price=500)
    _, body = oapi.create_order(BUYER, {"product_id": pid}, context=_ctx())
    oid = body["order"]["order_id"]
    # Fulfill before pay.
    st, body = oapi.fulfill_order(SELLER, oid, {}, context=_ctx())
    assert st == 409 and body["code"] == "illegal_transition", (st, body)


def test_self_purchase_400():
    pid = _digital_product(price=500)
    st, body = oapi.create_order(SELLER, {"product_id": pid}, context=_ctx())
    assert st == 400 and body["code"] == "self_purchase", (st, body)


def test_account_hold_403():
    pid = _digital_product(price=500)
    st, body = oapi.create_order(BUYER, {"product_id": pid},
                                 context=_ctx(status="suspended"))
    assert st == 403 and body["ok"] is False, (st, body)


def test_dispute_requires_reason_then_201():
    pid = _digital_product(price=1000)
    _, body = oapi.create_order(BUYER, {"product_id": pid}, context=_ctx())
    oid = body["order"]["order_id"]
    oapi.pay_order(BUYER, oid, context=_ctx())

    st, body = oapi.open_dispute(BUYER, oid, {})
    assert st == 400 and body["code"] == "invalid", (st, body)

    st, body = oapi.open_dispute(BUYER, oid, {"reason": "not delivered"})
    assert st == 201 and body["ok"] is True, (st, body)


def test_list_orders_buyer_and_seller_roles():
    pid = _digital_product(price=700)
    _, body = oapi.create_order(BUYER, {"product_id": pid}, context=_ctx())
    oid = body["order"]["order_id"]

    st, body = oapi.list_orders(BUYER, role="buyer")
    assert st == 200 and any(o["order_id"] == oid for o in body["orders"]), (st, body)

    st, body = oapi.list_orders(SELLER, role="seller")
    assert st == 200 and any(o["order_id"] == oid for o in body["orders"]), (st, body)


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_create_requires_product_id,
        test_create_201_and_get_200_full_detail,
        test_stranger_get_404_not_leaked,
        test_full_lifecycle_through_controller_and_money_summary,
        test_illegal_transition_409,
        test_self_purchase_400,
        test_account_hold_403,
        test_dispute_requires_reason_then_201,
        test_list_orders_buyer_and_seller_roles,
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
