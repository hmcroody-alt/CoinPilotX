"""Business OS — Returns HTTP controller, exercised DIRECTLY (no Flask).

Pins the (status, body) contract over the returns workflow:

  * DARK when BUSINESS_OS_MARKETPLACE is off — every handler returns 404;
  * unknown body fields rejected (400 unknown_field), unknown verbs rejected
    (400 bad_action), bad role rejected (400 bad_role);
  * create 201; full verb flow approve -> receive -> refund surfaces the refund
    payload; wrong-party actions surface 404 (role not leaked);
  * stranger reads 404 (existence not leaked); lists scope by role.

    python tests/business_os/test_returns_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_returns_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import returns as retm  # noqa: E402
from services.business_os.marketplace import returns_api as api  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 1970
STRANGER = 1972
ADMIN = "admin:19"

_uid = [1980]


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    retm.ensure_schema()
    ledger.ensure_schema()


def _buyer():
    _uid[0] += 1
    return _uid[0]


def _paid_order(buyer, price=1000):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=price,
                           inventory_qty=9, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    o = ordm.create_order(buyer, p["product_id"], context=_ctx())
    return ordm.pay_order(o["order_id"], buyer, context=_ctx())


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for status, body in (
            api.create_return(1, {"order_id": "x", "reason": "r"}),
            api.get_return(1, "mktret_x"),
            api.list_own_returns(1),
            api.act_on_return(1, "mktret_x", "approve"),
        ):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_rejection_paths():
    buyer = _buyer()
    o = _paid_order(buyer)
    status, body = api.create_return(
        buyer, {"order_id": o["order_id"], "reason": "r", "status": "refunded"},
        context=_ctx())
    assert status == 400 and body["code"] == "unknown_field"
    status, body = api.act_on_return(buyer, "mktret_x", "obliterate", context=_ctx())
    assert status == 400 and body["code"] == "bad_action"
    status, body = api.list_own_returns(buyer, role="admin")
    assert status == 400 and body["code"] == "bad_role"
    status, body = api.create_return(buyer, {"reason": "r"}, context=_ctx())
    assert status == 400 and body["code"] == "invalid"


def test_full_flow_through_controller():
    buyer = _buyer()
    o = _paid_order(buyer, price=1200)
    oid = o["order_id"]
    status, body = api.create_return(buyer, {"order_id": oid, "reason": "cracked"},
                                     context=_ctx())
    assert status == 201 and body["return"]["status"] == "requested"
    rid = body["return"]["return_id"]

    # Buyer on a seller verb -> 404, role not leaked.
    status, body = api.act_on_return(buyer, rid, "approve", context=_ctx())
    assert status == 404 and body["code"] == "not_found"

    status, body = api.act_on_return(SELLER, rid, "approve", context=_ctx())
    assert status == 200 and body["return"]["status"] == "approved"
    status, body = api.act_on_return(SELLER, rid, "receive", context=_ctx())
    assert status == 200 and body["return"]["status"] == "received"
    status, body = api.act_on_return(SELLER, rid, "refund", context=_ctx())
    assert status == 200 and body["return"]["status"] == "refunded"
    assert body["refund"]["amount_cents"] == 1200
    assert ledger.get_balance(ordm.escrow_account(oid), "usd") == 0

    # Read-back includes the event trail; stranger gets nothing.
    status, body = api.get_return(buyer, rid)
    assert status == 200
    assert [e["to_status"] for e in body["events"]] == [
        "requested", "approved", "received", "refunded"]
    status, body = api.get_return(STRANGER, rid)
    assert status == 404

    # Lists scope by role.
    status, body = api.list_own_returns(buyer, role="buyer")
    assert any(r["return_id"] == rid for r in body["returns"])
    status, body = api.list_own_returns(SELLER, role="seller")
    assert any(r["return_id"] == rid for r in body["returns"])
    status, body = api.list_own_returns(STRANGER, role="buyer")
    assert body["returns"] == []


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_rejection_paths,
        test_full_flow_through_controller,
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
