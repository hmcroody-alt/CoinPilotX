"""Marketplace Stage 3 — the governed UNDX Marketplace Assistant.

Proves the two server-side properties a language model cannot be trusted to enforce
itself, applied to commerce (Stage 3 Part 5):

  1. **Confirmation before any consequential change.** A read-only tool runs immediately
     from ``plan``; a low-risk write (create_product, create_order) executes without a
     token but is read-after-write verified; a consequential tool (pay/fulfill/complete/
     cancel/publish/pause) mints a ``confirmation_token`` bound to the EXACT (user, tool,
     canonical params) and ``execute`` refuses without a matching token — 428
     ``confirmation_required`` when absent, 409 ``confirmation_mismatch`` when forged or
     minted for a different action.

  2. **Read-after-write verification against canonical state.** ``execute`` never reports
     success from the verb's return value; it RE-READS the authoritative order/product and
     reports ``verified`` from the observed status.

Also proves the write kill switch (``BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES``)
disables writes without touching reads, and that an unknown tool is rejected.

    python tests/business_os/test_marketplace_assistant.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_mktasst_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"
os.environ.pop("BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES", None)

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as svc  # noqa: E402
from services.business_os.marketplace import orders as orders_mod  # noqa: E402
from services.business_os.marketplace import assistant  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 800
BUYER = 801
ADMIN = 9


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


def _approve_seller(uid):
    svc.upsert_seller(uid, display_name="Test Seller")
    svc.set_seller_status(uid, "approved", actor=ADMIN)


def _active_product(seller, price=2000, inv=5, ftype="physical"):
    p = svc.create_product(seller, title="Widget", price_cents=price,
                           fulfillment_type=ftype, inventory_qty=inv, context=_ctx())
    pid = p["product_id"]
    svc.transition_product(seller, pid, "publish")
    return pid


def _expect_error(fn, code=None, http=None):
    try:
        fn()
    except MarketplaceError as e:
        if code is not None:
            assert e.code == code, f"expected code {code}, got {e.code}"
        if http is not None:
            assert e.http_status == http, f"expected http {http}, got {e.http_status}"
        return
    raise AssertionError(f"expected MarketplaceError(code={code}, http={http})")


# --- (a) read-only tools run from plan() with no confirmation ---------------
def test_read_tools_run_without_confirmation():
    _approve_seller(SELLER)
    pid = _active_product(SELLER)
    order = orders_mod.create_order(BUYER, pid, quantity=1)
    oid = order["order_id"]

    out = assistant.plan(SELLER, "seller_products", {})
    assert out["requires_confirmation"] is False and out["write"] is False, out
    assert out["result"] is not None, out

    out = assistant.plan(BUYER, "order_status", {"order_id": oid})
    assert out["requires_confirmation"] is False, out
    assert out["result"]["status"] == "created", out

    out = assistant.plan(SELLER, "payout_balance", {})
    assert out["write"] is False, out

    # catalog marks reads/writes correctly
    cat = {t["tool"]: t for t in assistant.list_tools()}
    assert cat["order_status"]["requires_confirmation"] is False, cat
    assert cat["pay_order"]["requires_confirmation"] is True, cat
    assert cat["pay_order"]["is_write"] is True, cat
    assert cat["create_product"]["is_write"] is True, cat
    assert cat["create_product"]["requires_confirmation"] is False, cat


# --- (b) low-risk writes execute without a token and are verified -----------
def test_low_risk_writes_verified_no_token():
    _approve_seller(SELLER)
    out = assistant.execute(SELLER, "create_product",
                            {"title": "Assistant Widget", "price_cents": 1500,
                             "fulfillment_type": "physical", "inventory_qty": 3})
    assert out["ok"] is True and out["write"] is True, out
    assert out["verified"] is True, out
    assert out["observed"]["status"] == "draft", out
    pid = out["observed"]["product_id"]

    # publish it so a buyer can order
    p = assistant.plan(SELLER, "publish_product", {"product_id": pid})
    assistant.execute(SELLER, "publish_product", {"product_id": pid},
                      confirmation_token=p["confirmation_token"])

    out = assistant.execute(BUYER, "create_order", {"product_id": pid, "quantity": 1})
    assert out["verified"] is True, out
    assert out["observed"]["status"] == "created", out


# --- (c) consequential tool: token required, mismatch refused ---------------
def test_consequential_requires_matching_token():
    _approve_seller(SELLER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]

    p = assistant.plan(BUYER, "pay_order", {"order_id": oid})
    assert p["requires_confirmation"] is True, p
    assert p["confirmation_token"], p
    assert p["canonical_params"]["order_id"] == svc._sid(oid), p

    # execute WITHOUT a token -> 428
    _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid}),
                  code="confirmation_required", http=428)

    # FORGED token -> 409
    _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid},
                                            confirmation_token="deadbeef" * 8),
                  code="confirmation_mismatch", http=409)

    # token minted for a DIFFERENT order cannot execute this one
    oid2 = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    p2 = assistant.plan(BUYER, "pay_order", {"order_id": oid2})
    _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid},
                                            confirmation_token=p2["confirmation_token"]),
                  code="confirmation_mismatch", http=409)


# --- (d) correct token executes and is verified against canonical state -----
def test_execute_with_token_is_verified():
    _approve_seller(SELLER)
    pid = _active_product(SELLER, price=2000, inv=5)
    oid = orders_mod.create_order(BUYER, pid, quantity=2)["order_id"]

    # pay
    p = assistant.plan(BUYER, "pay_order", {"order_id": oid})
    out = assistant.execute(BUYER, "pay_order", {"order_id": oid},
                            confirmation_token=p["confirmation_token"])
    assert out["ok"] is True and out["verified"] is True, out
    assert out["observed"]["status"] == "paid", out
    # inventory decremented 5 -> 3
    prod = svc.get_product(pid, requester_user_id=SELLER)
    assert prod["inventory_qty"] == 3, prod
    # money captured to escrow
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 4000

    # fulfill (seller)
    p = assistant.plan(SELLER, "fulfill_order", {"order_id": oid, "tracking_ref": "TRK1"})
    out = assistant.execute(SELLER, "fulfill_order",
                            {"order_id": oid, "tracking_ref": "TRK1"},
                            confirmation_token=p["confirmation_token"])
    assert out["verified"] is True and out["observed"]["status"] == "fulfilled", out

    # complete (buyer) -> settles escrow
    p = assistant.plan(BUYER, "complete_order", {"order_id": oid})
    out = assistant.execute(BUYER, "complete_order", {"order_id": oid},
                            confirmation_token=p["confirmation_token"])
    assert out["verified"] is True and out["observed"]["status"] == "completed", out
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 0
    assert ledger.get_balance(orders_mod.seller_payable_account(SELLER)) == 3600
    assert ledger.get_balance(orders_mod.PLATFORM_REVENUE_ACCOUNT) == 400


# --- (e) write kill switch disables writes but not reads --------------------
def test_writes_kill_switch():
    _approve_seller(SELLER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    p = assistant.plan(BUYER, "pay_order", {"order_id": oid})
    os.environ["BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES"] = "1"
    try:
        # even with a valid token, the write is refused
        _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid},
                                                confirmation_token=p["confirmation_token"]),
                      code="writes_disabled", http=409)
        # low-risk writes also refused
        _expect_error(lambda: assistant.execute(SELLER, "create_product",
                                                {"title": "x", "price_cents": 100}),
                      code="writes_disabled", http=409)
        # reads still work
        r = assistant.execute(BUYER, "order_status", {"order_id": oid})
        assert r["ok"] is True and r["result"]["status"] == "created", r
    finally:
        os.environ.pop("BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES", None)


# --- (f) missing required id is rejected before minting a token -------------
def test_missing_id_rejected():
    _expect_error(lambda: assistant.plan(BUYER, "pay_order", {}),
                  code="order_id_required", http=400)
    _expect_error(lambda: assistant.plan(SELLER, "publish_product", {}),
                  code="product_id_required", http=400)


# --- (g) unknown tool is rejected -------------------------------------------
def test_unknown_tool_rejected():
    _expect_error(lambda: assistant.plan(BUYER, "delete_everything", {}),
                  code="unknown_tool", http=400)


def _run_standalone():
    setup_module()
    tests = [
        test_read_tools_run_without_confirmation,
        test_low_risk_writes_verified_no_token,
        test_consequential_requires_matching_token,
        test_execute_with_token_is_verified,
        test_writes_kill_switch,
        test_missing_id_rejected,
        test_unknown_tool_rejected,
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
