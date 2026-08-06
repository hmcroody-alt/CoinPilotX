"""Business OS — Seller reports HTTP controller, exercised DIRECTLY (no Flask).

  * DARK when BUSINESS_OS_MARKETPLACE is off — both handlers 404;
  * finance + sales-by-day surface the engine's figures with ok: True;
  * engine codes surface (invalid_day 400).

    python tests/business_os/test_reports_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_reports_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import reports_api as api  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 2770
BUYER = 2772
ADMIN = "admin:27"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ledger.ensure_schema()


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for status, body in (api.get_finance(SELLER),
                             api.get_sales_by_day(SELLER)):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_reports_through_controller():
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=1500,
                           inventory_qty=5, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    o = ordm.create_order(BUYER, p["product_id"], context=_ctx())
    ordm.pay_order(o["order_id"], BUYER, context=_ctx())

    status, body = api.get_finance(SELLER)
    assert status == 200 and body["ok"] is True
    r = body["report"]
    assert r["gross_captured_cents"] == 1500 and r["in_escrow_cents"] == 1500
    assert r["paid_out_cents"] is None and r["generated_at"]

    status, body = api.get_sales_by_day(SELLER)
    assert status == 200 and body["report"]["days"][0]["gross_cents"] == 1500

    status, body = api.get_sales_by_day(SELLER, start_day="nope")
    assert status == 400 and body["code"] == "invalid_day"


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_reports_through_controller,
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
