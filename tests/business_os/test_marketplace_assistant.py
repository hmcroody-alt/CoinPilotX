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
OTHER = 802   # a third party: neither buyer nor seller on anything below
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


# ===========================================================================
# Approval-token hardening. Mission XIII requires an approval to be bound to one
# action, one canonical payload and one actor, AND to be time-limited, single-use,
# replay-resistant and revocable. A token derived as hash(salt, user, tool, params)
# satisfies only the first three: it is reproducible forever, so approving
# "publish product X" once silently approves every future publish of X. These cases
# pin the stored-grant behaviour that closes that gap.
# ===========================================================================

# --- (h) an approval is SINGLE-USE: replay after a state cycle is refused ----
def test_token_is_single_use_replay_refused():
    """The regression that motivated the grant table.

    publish(T) -> pause -> publish(T) again previously SUCCEEDED, because the token
    was a pure function of (user, tool, params) and the state machine happened to
    allow paused->active. One human approval, two publishes.
    """
    _approve_seller(SELLER)
    p = svc.create_product(SELLER, title="Replay", price_cents=1000,
                           fulfillment_type="physical", inventory_qty=5, context=_ctx())
    pid = p["product_id"]

    pub = assistant.plan(SELLER, "publish_product", {"product_id": pid})
    tok = pub["confirmation_token"]
    out = assistant.execute(SELLER, "publish_product", {"product_id": pid},
                            confirmation_token=tok)
    assert out["verified"] is True and out["observed"]["status"] == "active", out

    # take it back out of 'active' so the state machine would permit publish again
    pa = assistant.plan(SELLER, "pause_product", {"product_id": pid})
    assistant.execute(SELLER, "pause_product", {"product_id": pid},
                      confirmation_token=pa["confirmation_token"])
    assert svc.get_product(pid, requester_user_id=SELLER)["status"] == "paused"

    # the burnt approval must NOT re-publish
    _expect_error(lambda: assistant.execute(SELLER, "publish_product", {"product_id": pid},
                                            confirmation_token=tok),
                  code="confirmation_used", http=409)
    # and canonical state proves nothing happened
    assert svc.get_product(pid, requester_user_id=SELLER)["status"] == "paused"


# --- (h2) single-use also holds for a money-moving verb ---------------------
def test_pay_token_cannot_be_replayed():
    _approve_seller(SELLER)
    pid = _active_product(SELLER, price=2000, inv=5)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    p = assistant.plan(BUYER, "pay_order", {"order_id": oid})
    tok = p["confirmation_token"]
    assistant.execute(BUYER, "pay_order", {"order_id": oid}, confirmation_token=tok)
    escrow_after_first = ledger.get_balance(orders_mod.escrow_account(oid))
    assert escrow_after_first == 2000

    # replay is refused by the APPROVAL layer, not merely by the state machine
    _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid},
                                            confirmation_token=tok),
                  code="confirmation_used", http=409)
    # no double capture
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == escrow_after_first


# --- (i) an approval EXPIRES -------------------------------------------------
def test_token_expires():
    _approve_seller(SELLER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    p = assistant.plan(BUYER, "pay_order", {"order_id": oid})
    assert p["expires_at"], p
    assert p["single_use"] is True, p
    assert p["ttl_seconds"] >= 30, p

    # force the stored grant into the past rather than sleeping
    conn = db.connect()
    try:
        conn.execute(
            f"UPDATE {assistant._CONFIRM_TABLE} SET expires_at = ? WHERE token_hash = ?",
            ("2000-01-01T00:00:00.000000Z",
             assistant._token_hash(p["confirmation_token"])))
        conn.commit()
    finally:
        conn.close()

    _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid},
                                            confirmation_token=p["confirmation_token"]),
                  code="confirmation_expired", http=409)
    # order untouched
    assert orders_mod.get_order(oid, requester_user_id=BUYER)["status"] == "created"


# --- (j) an approval is REVOCABLE before redemption -------------------------
def test_token_revocable():
    _approve_seller(SELLER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    p = assistant.plan(BUYER, "pay_order", {"order_id": oid})

    # a DIFFERENT actor cannot revoke someone else's approval
    assert assistant.revoke_confirmation(SELLER, p["confirmation_token"])["revoked"] is False

    # the owning actor can
    assert assistant.revoke_confirmation(BUYER, p["confirmation_token"])["revoked"] is True
    _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid},
                                            confirmation_token=p["confirmation_token"]),
                  code="confirmation_revoked", http=409)
    assert orders_mod.get_order(oid, requester_user_id=BUYER)["status"] == "created"


# --- (k) editing the payload after approval invalidates the approval --------
def test_edited_payload_invalidates_approval():
    """Mission XIII: 'Editing a high-risk action after approval must invalidate the
    prior approval.' Approve tracking_ref TRK-A, then execute with TRK-B."""
    _approve_seller(SELLER)
    pid = _active_product(SELLER, price=2000, inv=5)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    pay = assistant.plan(BUYER, "pay_order", {"order_id": oid})
    assistant.execute(BUYER, "pay_order", {"order_id": oid},
                      confirmation_token=pay["confirmation_token"])

    p = assistant.plan(SELLER, "fulfill_order",
                       {"order_id": oid, "tracking_ref": "TRK-A"})
    _expect_error(lambda: assistant.execute(SELLER, "fulfill_order",
                                            {"order_id": oid, "tracking_ref": "TRK-B"},
                                            confirmation_token=p["confirmation_token"]),
                  code="confirmation_mismatch", http=409)
    assert orders_mod.get_order(oid, requester_user_id=BUYER)["status"] == "paid"


# --- (l) an approval cannot be redeemed for a DIFFERENT TOOL ----------------
def test_token_bound_to_tool():
    """cancel_order and pay_order normalize to the IDENTICAL canonical payload
    ({'order_id': ...}), so only the tool binding separates them."""
    _approve_seller(SELLER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    cancel_tok = assistant.plan(BUYER, "cancel_order",
                                {"order_id": oid})["confirmation_token"]
    _expect_error(lambda: assistant.execute(BUYER, "pay_order", {"order_id": oid},
                                            confirmation_token=cancel_tok),
                  code="confirmation_mismatch", http=409)
    assert orders_mod.get_order(oid, requester_user_id=BUYER)["status"] == "created"


# --- (m) an approval cannot be redeemed by a DIFFERENT ACTOR ---------------
def test_token_bound_to_actor():
    _approve_seller(SELLER)
    _approve_seller(OTHER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    # OTHER mints an approval for the same canonical action
    other_tok = assistant.plan(OTHER, "cancel_order",
                               {"order_id": oid})["confirmation_token"]
    _expect_error(lambda: assistant.execute(BUYER, "cancel_order", {"order_id": oid},
                                            confirmation_token=other_tok),
                  code="confirmation_mismatch", http=409)
    assert orders_mod.get_order(oid, requester_user_id=BUYER)["status"] == "created"


# --- (n) cross-tenant isolation on both read and write paths ---------------
def test_cross_tenant_isolation():
    """A third party who is neither buyer nor seller gets 404 — existence is not
    leaked — and cannot mutate the object even holding a self-minted approval."""
    _approve_seller(SELLER)
    _approve_seller(OTHER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]

    # reads
    _expect_error(lambda: assistant.plan(OTHER, "order_status", {"order_id": oid}),
                  code="not_found", http=404)

    # write against a foreign PRODUCT
    tok = assistant.plan(OTHER, "pause_product", {"product_id": pid})["confirmation_token"]
    _expect_error(lambda: assistant.execute(OTHER, "pause_product", {"product_id": pid},
                                            confirmation_token=tok), http=404)
    assert svc.get_product(pid, requester_user_id=SELLER)["status"] == "active"

    # write against a foreign ORDER
    tok2 = assistant.plan(OTHER, "cancel_order", {"order_id": oid})["confirmation_token"]
    _expect_error(lambda: assistant.execute(OTHER, "cancel_order", {"order_id": oid},
                                            confirmation_token=tok2), http=404)
    assert orders_mod.get_order(oid, requester_user_id=BUYER)["status"] == "created"


# --- (o) the raw approval is never stored -----------------------------------
def test_raw_token_never_persisted():
    """A database read must not yield a usable approval."""
    _approve_seller(SELLER)
    pid = _active_product(SELLER)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    p = assistant.plan(BUYER, "pay_order", {"order_id": oid})
    raw = p["confirmation_token"]
    conn = db.connect()
    try:
        rows = conn.execute(f"SELECT * FROM {assistant._CONFIRM_TABLE}").fetchall()
    finally:
        conn.close()
    blob = " ".join(str(dict(r)) for r in rows)
    assert raw not in blob, "raw confirmation token was persisted"
    assert assistant._token_hash(raw) in blob, "grant row not found by token hash"


# --- (p) a redeemed approval is burnt even when the verb itself fails -------
def test_token_burnt_when_handler_fails():
    """Otherwise a failed high-risk attempt leaves a live approval lying around
    that can be retried without a human in the loop."""
    _approve_seller(SELLER)
    pid = _active_product(SELLER, price=2000, inv=5)
    oid = orders_mod.create_order(BUYER, pid, quantity=1)["order_id"]
    # completing a CREATED order is an illegal transition
    p = assistant.plan(BUYER, "complete_order", {"order_id": oid})
    tok = p["confirmation_token"]
    try:
        assistant.execute(BUYER, "complete_order", {"order_id": oid},
                          confirmation_token=tok)
        raise AssertionError("expected the illegal transition to be refused")
    except MarketplaceError as e:
        assert e.code != "confirmation_used", e.code
    # the approval was consumed by the attempt
    _expect_error(lambda: assistant.execute(BUYER, "complete_order", {"order_id": oid},
                                            confirmation_token=tok),
                  code="confirmation_used", http=409)


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
        test_token_is_single_use_replay_refused,
        test_pay_token_cannot_be_replayed,
        test_token_expires,
        test_token_revocable,
        test_edited_payload_invalidates_approval,
        test_token_bound_to_tool,
        test_token_bound_to_actor,
        test_cross_tenant_isolation,
        test_raw_token_never_persisted,
        test_token_burnt_when_handler_fails,
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
