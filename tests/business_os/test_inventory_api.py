"""Business OS — Inventory HTTP controller, exercised DIRECTLY (no Flask).

Pins the (status, body) contract over the inventory adjustments backend:

  * DARK when BUSINESS_OS_MARKETPLACE is off — every handler returns 404;
  * unknown body fields rejected (400 unknown_field); engine codes surface
    (invalid_reason, insufficient_inventory 409);
  * adjust 201 with the record; history + overview read back consistently;
  * foreign product answers 404 (existence not leaked).

    python tests/business_os/test_inventory_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_inv_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import inventory as invm  # noqa: E402
from services.business_os.marketplace import inventory_api as api  # noqa: E402


SELLER = 2070
STRANGER = 2072
ADMIN = "admin:20"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    invm.ensure_schema()


def _product(qty=10):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=1000,
                           inventory_qty=qty, context=_ctx())
    return p["product_id"]


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for status, body in (
            api.get_overview(SELLER),
            api.list_adjustments(SELLER),
            api.adjust(SELLER, "x", {"delta": 1, "reason": "found"},
                       context=_ctx()),
        ):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_flow_and_codes_through_controller():
    pid = _product(qty=10)
    status, body = api.adjust(SELLER, pid, {"delta": -4, "reason": "damaged",
                                            "cause": "x"}, context=_ctx())
    assert status == 400 and body["code"] == "unknown_field"
    status, body = api.adjust(SELLER, pid, {"delta": -4, "reason": "shrug"},
                              context=_ctx())
    assert status == 400 and body["code"] == "invalid_reason"

    status, body = api.adjust(SELLER, pid, {"delta": -4, "reason": "damaged",
                                            "note": "broken"}, context=_ctx())
    assert status == 201 and body["adjustment"]["after_qty"] == 6

    status, body = api.adjust(SELLER, pid, {"delta": -7, "reason": "lost"},
                              context=_ctx())
    assert status == 409 and body["code"] == "insufficient_inventory"

    status, body = api.list_adjustments(SELLER, product_id=pid)
    assert status == 200 and [a["reason"] for a in body["adjustments"]] == ["damaged"]

    status, body = api.get_overview(SELLER)
    assert status == 200
    row = next(p for p in body["overview"]["products"] if p["product_id"] == pid)
    assert row["on_hand_qty"] == 6

    # Foreign seller: existence not leaked.
    mkt.upsert_seller(STRANGER, display_name="X")
    mkt.set_seller_status(STRANGER, "approved", actor=ADMIN)
    status, body = api.adjust(STRANGER, pid, {"delta": 1, "reason": "found"},
                              context=_ctx())
    assert status == 404 and body["code"] == "not_found"
    status, body = api.list_adjustments(STRANGER, product_id=pid)
    assert status == 404


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_flow_and_codes_through_controller,
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
