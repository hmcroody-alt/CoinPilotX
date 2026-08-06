"""Business OS — Marketplace inventory adjustments + overview, exercised DIRECTLY.

Proves the adjustment layer is governed and the overview is honest:

  * DARK when BUSINESS_OS_MARKETPLACE is off — every entry point raises 503;
  * reason enum enforced; exactly one of delta/set_qty; note bounded;
  * relative delta is guarded-atomic (cannot go below zero — 409, count intact);
  * unlimited (NULL) inventory refuses deltas (409 unlimited_inventory);
  * every adjustment appends a record with before/after/actor + an audit row;
  * history is seller-scoped (foreign product -> 404, existence not leaked);
  * overview buckets honestly (in_stock/low_stock/out_of_stock/unlimited), an
    empty catalog is an empty list, and offer-reservation holds surface as
    ``held_qty`` on the right product.

    python tests/business_os/test_inventory_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_inv_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import offers as off  # noqa: E402
from services.business_os.marketplace import inventory as inv  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402


SELLER = 1870
OTHER_SELLER = 1871
BUYER = 1875
ADMIN = "admin:18"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    off.ensure_schema()
    inv.ensure_schema()


def _seller(uid=SELLER):
    mkt.upsert_seller(uid, display_name="S")
    mkt.set_seller_status(uid, "approved", actor=ADMIN)


def _product(uid=SELLER, qty=10, fulfillment="physical", title="Lamp"):
    _seller(uid)
    p = mkt.create_product(uid, title=title, price_cents=1000,
                           fulfillment_type=fulfillment,
                           inventory_qty=qty, context=_ctx())
    return p["product_id"]


def _expect(code, fn):
    try:
        fn()
    except MarketplaceError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return exc
    raise AssertionError(f"expected MarketplaceError {code}, none raised")


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for fn in (
            lambda: inv.adjust_inventory(SELLER, "x", delta=1, reason="found"),
            lambda: inv.list_adjustments(SELLER),
            lambda: inv.inventory_overview(SELLER),
        ):
            _expect("disabled", fn)
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_validation_and_governance():
    pid = _product(qty=10)
    _expect("invalid_reason", lambda: inv.adjust_inventory(
        SELLER, pid, delta=1, reason="because", context=_ctx()))
    _expect("invalid_adjustment", lambda: inv.adjust_inventory(
        SELLER, pid, delta=1, set_qty=5, reason="found", context=_ctx()))
    _expect("invalid_adjustment", lambda: inv.adjust_inventory(
        SELLER, pid, reason="found", context=_ctx()))
    _expect("invalid_adjustment", lambda: inv.adjust_inventory(
        SELLER, pid, delta=0, reason="found", context=_ctx()))
    _expect("invalid_adjustment", lambda: inv.adjust_inventory(
        SELLER, pid, set_qty=-1, reason="recount", context=_ctx()))
    _expect("note_too_long", lambda: inv.adjust_inventory(
        SELLER, pid, delta=1, reason="found", note="x" * 1001, context=_ctx()))
    _expect("account_hold", lambda: inv.adjust_inventory(
        SELLER, pid, delta=1, reason="found", context=_ctx(status="suspended")))
    # Foreign product: existence not leaked.
    _seller(OTHER_SELLER)
    _expect("not_found", lambda: inv.adjust_inventory(
        OTHER_SELLER, pid, delta=1, reason="found", context=_ctx()))


def test_delta_and_recount_with_records():
    pid = _product(qty=10, title="Chair")
    r1 = inv.adjust_inventory(SELLER, pid, delta=-3, reason="damaged",
                              note="dropped pallet", context=_ctx())
    assert (r1["before_qty"], r1["after_qty"], r1["delta"]) == (10, 7, -3)
    r2 = inv.adjust_inventory(SELLER, pid, delta=2, reason="found", context=_ctx())
    assert (r2["before_qty"], r2["after_qty"]) == (7, 9)
    r3 = inv.adjust_inventory(SELLER, pid, set_qty=20, reason="recount",
                              context=_ctx())
    assert (r3["before_qty"], r3["after_qty"], r3["delta"]) == (9, 20, None)
    # Below-zero refused; the count is untouched.
    _expect("insufficient_inventory", lambda: inv.adjust_inventory(
        SELLER, pid, delta=-21, reason="lost", context=_ctx()))
    assert mkt.get_product(pid, requester_user_id=SELLER)["inventory_qty"] == 20

    history = inv.list_adjustments(SELLER, product_id=pid)
    assert [h["reason"] for h in history] == ["recount", "found", "damaged"]
    assert all(h["actor"] == str(SELLER) for h in history)

    conn = db.connect()
    audits = conn.execute(
        "SELECT reason FROM business_os_mkt_audit WHERE subject_ref = ? "
        "AND action = 'inventory_adjust' ORDER BY id", (pid,)).fetchall()
    conn.close()
    assert [a["reason"] for a in audits] == ["damaged", "found", "recount"]

    # Unlimited inventory refuses deltas honestly.
    pid_d = _product(qty=None, fulfillment="digital", title="Ebook")
    _expect("unlimited_inventory", lambda: inv.adjust_inventory(
        SELLER, pid_d, delta=1, reason="found", context=_ctx()))
    # But a recount can START tracking a finite count.
    r = inv.adjust_inventory(SELLER, pid_d, set_qty=100, reason="recount",
                             context=_ctx())
    assert r["before_qty"] is None and r["after_qty"] == 100


def test_overview_buckets_and_holds():
    # Fresh seller so the overview is exactly this catalog.
    uid = 1890
    _seller(uid)
    assert inv.inventory_overview(uid)["products"] == []

    p_ok = mkt.create_product(uid, title="Plenty", price_cents=500,
                              inventory_qty=50, context=_ctx())["product_id"]
    p_low = mkt.create_product(uid, title="Low", price_cents=500,
                               inventory_qty=3, context=_ctx())["product_id"]
    p_out = mkt.create_product(uid, title="Out", price_cents=500,
                               inventory_qty=0, context=_ctx())["product_id"]
    p_unl = mkt.create_product(uid, title="Unl", price_cents=500,
                               fulfillment_type="digital", context=_ctx())["product_id"]

    ov = inv.inventory_overview(uid)
    assert ov["counts"] == {"tracked": 3, "unlimited": 1,
                            "out_of_stock": 1, "low_stock": 1}
    by_id = {p["product_id"]: p for p in ov["products"]}
    assert by_id[p_ok]["bucket"] == "in_stock"
    assert by_id[p_low]["bucket"] == "low_stock"
    assert by_id[p_out]["bucket"] == "out_of_stock"
    assert by_id[p_unl]["bucket"] == "unlimited"
    assert all(p["held_qty"] == 0 for p in ov["products"])

    # An accepted offer's hard hold surfaces as held_qty on the right product.
    mkt.transition_product(uid, p_ok, "publish", context=_ctx())
    o = off.create_offer(BUYER, p_ok, 400, quantity=2, context=_ctx())
    off.accept_offer(o["offer_id"], uid, context=_ctx())
    ov = inv.inventory_overview(uid)
    row = next(p for p in ov["products"] if p["product_id"] == p_ok)
    assert row["held_qty"] == 2 and row["on_hand_qty"] == 48

    _expect("invalid_threshold",
            lambda: inv.inventory_overview(uid, low_stock_threshold=0))


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_validation_and_governance,
        test_delta_and_recount_with_records,
        test_overview_buckets_and_holds,
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
