"""Business OS — Seller dashboard HTTP controller, exercised DIRECTLY (no Flask).

Pins the (status, body) contract over the read-only projections:

  * DARK when BUSINESS_OS_MARKETPLACE is off — both handlers 404;
  * action center reflects real queue state through the controller (a paid
    order lands in to_fulfill; working it drains the queue);
  * sales summary surfaces the reconciled money figures with ok: True.

    python tests/business_os/test_seller_dashboard_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_selldash_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import offers as off  # noqa: E402
from services.business_os.marketplace import returns as retm  # noqa: E402
from services.business_os.marketplace import seller_dashboard_api as api  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 2270
BUYER = 2272
ADMIN = "admin:22"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    off.ensure_schema()
    retm.ensure_schema()
    ledger.ensure_schema()


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for status, body in (api.get_action_center(SELLER),
                             api.get_sales_summary(SELLER)):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_action_center_and_summary_through_controller():
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=1000,
                           inventory_qty=5, context=_ctx())
    pid = p["product_id"]
    mkt.transition_product(SELLER, pid, "publish", context=_ctx())
    o = ordm.create_order(BUYER, pid, context=_ctx())
    o = ordm.pay_order(o["order_id"], BUYER, context=_ctx())

    status, body = api.get_action_center(SELLER)
    assert status == 200 and body["ok"] is True
    ac = body["action_center"]
    assert ac["queues"]["to_fulfill"]["count"] == 1
    assert ac["queues"]["to_fulfill"]["preview"][0]["order_id"] == o["order_id"]

    ordm.fulfill_order(o["order_id"], SELLER, context=_ctx())
    status, body = api.get_action_center(SELLER)
    assert status == 200
    assert body["action_center"]["queues"]["to_fulfill"]["count"] == 0

    status, body = api.get_sales_summary(SELLER)
    assert status == 200 and body["ok"] is True
    s = body["summary"]
    assert s["gross_captured_cents"] == 1000
    assert s["orders_by_status"].get("fulfilled") == 1
    assert s["payable_cents"] == 0  # nothing completed yet


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_action_center_and_summary_through_controller,
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
