"""Business OS — Seller reports: ledger-backed finance + sales-by-day.

Proves the Reports screens' numbers are ledger truth, not client math:

  * DARK when BUSINESS_OS_MARKETPLACE is off;
  * finance report reconciles across the full order lifecycle: capture puts
    money IN ESCROW; completion moves it to PAYABLE (net) + platform fees;
    a refund reduces refunded/gross-vs-escrow consistently; ``paid_out_cents``
    is honestly None (disbursement is provider-side);
  * freshness: every report carries generated_at;
  * sales_by_day groups by UTC day off the order rows, respects bounds,
    rejects malformed dates, and an empty seller gets honest zeros/empty.

    python tests/business_os/test_reports_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_reports_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import returns as retm  # noqa: E402
from services.business_os.marketplace import reports as rep  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 2670
ADMIN = "admin:26"

_uid = [2680]


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    retm.ensure_schema()
    ledger.ensure_schema()


def _buyer():
    _uid[0] += 1
    return _uid[0]


def _published_product(price=1000):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=price,
                           inventory_qty=30, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    return p["product_id"]


def _expect(code, fn):
    try:
        fn()
    except MarketplaceError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}"
        return exc
    raise AssertionError(f"expected MarketplaceError {code}")


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        _expect("disabled", lambda: rep.finance_report(SELLER))
        _expect("disabled", lambda: rep.sales_by_day(SELLER))
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_empty_seller_honest_zeros():
    mkt.upsert_seller(9922, display_name="Z")
    f = rep.finance_report(9922)
    assert f["gross_captured_cents"] == 0 and f["in_escrow_cents"] == 0 \
        and f["payable_cents"] == 0
    assert f["paid_out_cents"] is None  # never fabricated
    assert f["generated_at"]
    s = rep.sales_by_day(9922)
    assert s["days"] == [] and s["generated_at"]


def test_finance_reconciles_across_lifecycle():
    pid = _published_product()

    # Order 1: paid -> money in escrow.
    b1 = _buyer()
    o1 = ordm.create_order(b1, pid, context=_ctx())
    o1 = ordm.pay_order(o1["order_id"], b1, context=_ctx())
    f = rep.finance_report(SELLER)
    assert f["gross_captured_cents"] == 1000
    assert f["in_escrow_cents"] == 1000
    assert f["payable_cents"] == 0

    # Order 2: full lifecycle -> escrow drains into payable + fees.
    b2 = _buyer()
    o2 = ordm.create_order(b2, pid, context=_ctx())
    o2 = ordm.pay_order(o2["order_id"], b2, context=_ctx())
    ordm.fulfill_order(o2["order_id"], SELLER, context=_ctx())
    done = ordm.complete_order(o2["order_id"], b2, context=_ctx())
    f = rep.finance_report(SELLER)
    assert f["gross_captured_cents"] == 2000
    assert f["in_escrow_cents"] == 1000            # only o1 remains held
    assert f["payable_cents"] == done["seller_net_cents"]
    assert f["payable_cents"] + f["platform_fees_cents"] >= 1000 \
        or f["platform_fees_cents"] == 0

    # Order 3: refunded via the returns flow -> refunded_cents up, its escrow gone.
    b3 = _buyer()
    o3 = ordm.create_order(b3, pid, context=_ctx())
    ordm.pay_order(o3["order_id"], b3, context=_ctx())
    r3 = retm.request_return(b3, o3["order_id"], reason="r", context=_ctx())
    retm.approve_return(r3["return_id"], SELLER, context=_ctx())
    retm.mark_received(r3["return_id"], SELLER, context=_ctx())
    retm.refund_return(r3["return_id"], SELLER, context=_ctx())
    f = rep.finance_report(SELLER)
    assert f["refunded_cents"] == 1000
    assert f["in_escrow_cents"] == 1000            # o3's escrow was emptied
    assert f["gross_captured_cents"] == 3000


def test_sales_by_day():
    s = rep.sales_by_day(SELLER)
    assert len(s["days"]) >= 1
    today = s["days"][0]
    assert today["orders"] == 3 and today["gross_cents"] == 3000 \
        and today["refunded_cents"] == 1000

    # Bounds respected; malformed dates rejected.
    assert rep.sales_by_day(SELLER, start_day="2099-01-01")["days"] == []
    assert rep.sales_by_day(SELLER, end_day="2000-01-01")["days"] == []
    inside = rep.sales_by_day(SELLER, start_day="2000-01-01",
                              end_day="2099-01-01")
    assert inside["days"] == s["days"]
    _expect("invalid_day", lambda: rep.sales_by_day(SELLER, start_day="junk"))
    _expect("invalid_day", lambda: rep.sales_by_day(SELLER,
                                                    end_day="2026/01/01"))


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_empty_seller_honest_zeros,
        test_finance_reconciles_across_lifecycle,
        test_sales_by_day,
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
