"""Business OS — Offers HTTP controller, exercised DIRECTLY (no Flask).

Pins the (status, body) contract over the offers engine:

  * DARK when BUSINESS_OS_MARKETPLACE is off — every handler returns 404;
  * unknown body fields rejected (400 unknown_field), unknown verbs rejected
    (400 bad_action) — no silent no-ops;
  * create 201; full verb flow counter -> accept -> convert surfaces the
    converted order_id; wrong-turn actions surface 409 not_your_turn;
  * stranger reads 404 (existence not leaked);
  * expiry sweep reports a count and is callable repeatedly.

    python tests/business_os/test_offers_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_offers_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import offers as off  # noqa: E402
from services.business_os.marketplace import offers_api as api  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 870
STRANGER = 872
ADMIN = "admin:9"

_uid = [880]


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    off.ensure_schema()
    ledger.ensure_schema()


def _buyer():
    _uid[0] += 1
    return _uid[0]


def _product(price=1000, inventory=5):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=price,
                           inventory_qty=inventory, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    return p["product_id"]


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for status, body in (
            api.create_offer(1, {"product_id": "x", "amount_cents": 1}),
            api.get_offer(1, "mkoff_x"),
            api.list_own_offers(1),
            api.act_on_offer(1, "mkoff_x", "accept"),
            api.run_expiry_sweep(),
        ):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_unknown_fields_and_verbs_rejected():
    buyer = _buyer()
    pid = _product()
    status, body = api.create_offer(buyer, {"product_id": pid, "amount_cents": 500,
                                            "status": "accepted"}, context=_ctx())
    assert status == 400 and body["code"] == "unknown_field"
    status, body = api.act_on_offer(buyer, "mkoff_x", "approve", context=_ctx())
    assert status == 400 and body["code"] == "bad_action"
    status, body = api.list_own_offers(buyer, role="admin")
    assert status == 400 and body["code"] == "bad_role"


def test_full_flow_through_controller():
    buyer = _buyer()
    pid = _product(price=1000)
    status, body = api.create_offer(buyer, {"product_id": pid, "amount_cents": 700,
                                            "quantity": 2}, context=_ctx())
    assert status == 201 and body["offer"]["status"] == "needs_response"
    oid = body["offer"]["offer_id"]

    # Buyer answering their own proposal surfaces the turn rule.
    status, body = api.act_on_offer(buyer, oid, "accept", context=_ctx())
    assert status == 409 and body["code"] == "not_your_turn"

    status, body = api.act_on_offer(SELLER, oid, "counter",
                                    {"amount_cents": 850}, context=_ctx())
    assert status == 200 and body["offer"]["status"] == "countered"

    status, body = api.act_on_offer(buyer, oid, "accept", context=_ctx())
    assert status == 200 and body["offer"]["status"] == "accepted"

    status, body = api.act_on_offer(buyer, oid, "convert", context=_ctx())
    assert status == 200 and body["offer"]["status"] == "converted"
    assert body["order_id"] == body["offer"]["converted_order_id"]

    # Read-back includes the event trail.
    status, body = api.get_offer(buyer, oid)
    assert status == 200
    assert [e["to_status"] for e in body["events"]] == [
        "needs_response", "countered", "accepted", "converted"]

    # Stranger: existence not leaked.
    status, body = api.get_offer(STRANGER, oid)
    assert status == 404

    # Lists scope by role.
    status, body = api.list_own_offers(buyer, role="buyer")
    assert any(o["offer_id"] == oid for o in body["offers"])
    status, body = api.list_own_offers(SELLER, role="seller")
    assert any(o["offer_id"] == oid for o in body["offers"])


def test_expiry_sweep_endpoint():
    status, body = api.run_expiry_sweep(actor=ADMIN)
    assert status == 200 and body["ok"] is True and isinstance(body["expired"], int)
    status2, body2 = api.run_expiry_sweep(actor=ADMIN)
    assert status2 == 200  # idempotent / repeatable


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_unknown_fields_and_verbs_rejected,
        test_full_flow_through_controller,
        test_expiry_sweep_endpoint,
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
