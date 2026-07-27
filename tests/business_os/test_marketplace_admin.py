"""Marketplace Stage 3 — consolidated admin governance surface (Part 6).

Proves the admin lens over the canonical marketplace tables:

  * cross-owner order inspection returns the full order payload;
  * governed refund delegates to the canonical money primitive AND writes an
    admin audit row with before/after;
  * governed dispute resolution ('refund'/'deny') rides the canonical resolver;
  * seller restrict / lift restriction ride the canonical ``suspended`` status,
    require actor + reason, and are idempotency-guarded;
  * seller appeals are recorded on the append-only audit log, listed with state,
    and a granted appeal lifts the restriction as part of the same governed action;
  * payout balance reads the accrued ledger amount and a payout NOTE is audit-only
    (never moves money).

    python tests/business_os/test_marketplace_admin.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_mktadm_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as svc  # noqa: E402
from services.business_os.marketplace import orders as orders_mod  # noqa: E402
from services.business_os.marketplace import refunds as refunds_mod  # noqa: E402
from services.business_os.marketplace import admin  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 850
BUYER = 851
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
    svc.upsert_seller(uid, display_name="S")
    svc.set_seller_status(uid, "approved", actor=ADMIN)


def _paid_order(seller, buyer, price=2000, qty=2, inv=5):
    pid = svc.create_product(seller, title="W", price_cents=price,
                             fulfillment_type="physical", inventory_qty=inv,
                             context=_ctx())["product_id"]
    svc.transition_product(seller, pid, "publish")
    oid = orders_mod.create_order(buyer, pid, quantity=qty)["order_id"]
    orders_mod.pay_order(oid, buyer)
    return pid, oid


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


# --- (a) cross-owner order inspection ---------------------------------------
def test_admin_order_inspection():
    _approve_seller(SELLER)
    pid, oid = _paid_order(SELLER, BUYER)
    view = admin.admin_get_order(oid)
    assert view["order"]["order_id"] == oid, view
    assert view["order"]["status"] == "paid", view
    assert len(view["items"]) == 1, view
    assert view["money"] is not None, view
    assert view["refunds"] == [], view
    assert view["disputes"] == [], view
    # listing sees it too
    lst = admin.admin_list_orders(seller_user_id=SELLER)
    assert any(o["order_id"] == oid for o in lst), lst
    _expect_error(lambda: admin.admin_get_order("nope"), code="not_found", http=404)


# --- (b) governed refund delegates + audits ---------------------------------
def test_admin_refund_governed():
    _approve_seller(SELLER)
    pid, oid = _paid_order(SELLER, BUYER)
    # actor + reason required
    _expect_error(lambda: admin.admin_refund_order(oid, actor="", reason="x"),
                  code="actor_required", http=400)
    _expect_error(lambda: admin.admin_refund_order(oid, actor="admin", reason=""),
                  code="reason_required", http=400)
    # partial refund of 500 of 4000
    out = admin.admin_refund_order(oid, actor="admin", reason="goodwill",
                                   amount_cents=500)
    assert out["refund"]["amount_cents"] == 500, out
    assert out["before"]["status"] == "paid", out
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 3500
    # an admin audit row exists
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_mkt_audit "
            "WHERE action = 'admin_refund' AND subject_ref = ?", (str(oid),)).fetchone()[0]
        assert n == 1, n
    finally:
        conn.close()


# --- (c) governed dispute resolution ----------------------------------------
def test_admin_dispute_resolution():
    _approve_seller(SELLER)
    pid, oid = _paid_order(SELLER, BUYER)
    disp = refunds_mod.open_dispute(oid, BUYER, reason="item not as described")
    did = disp["dispute_id"]
    assert any(d["dispute_id"] == did for d in admin.admin_list_disputes(status="open"))
    out = admin.admin_resolve_dispute(did, "refund", actor="admin",
                                      reason="buyer in the right")
    assert out["status"] == "resolved" and out["resolution"] == "refund", out
    # full refund drained escrow, order refunded
    assert ledger.get_balance(orders_mod.escrow_account(oid)) == 0
    assert orders_mod.get_order(oid)["status"] == "refunded"
    # cannot resolve twice
    _expect_error(lambda: admin.admin_resolve_dispute(did, "deny", actor="admin",
                                                      reason="x"),
                  code="already_resolved", http=409)


# --- (d) seller restrict / lift + idempotency -------------------------------
def test_admin_seller_restrict_and_lift():
    S = 860
    _approve_seller(S)
    r = admin.admin_restrict_seller(S, actor="admin", reason="policy breach")
    assert r["after_status"] == "suspended", r
    assert svc.get_seller(S)["status"] == "suspended"
    # double-restrict guarded
    _expect_error(lambda: admin.admin_restrict_seller(S, actor="admin", reason="again"),
                  code="already_restricted", http=409)
    # lift
    l = admin.admin_lift_seller_restriction(S, actor="admin", reason="appeal ok")
    assert l["after_status"] == "approved", l
    _expect_error(lambda: admin.admin_lift_seller_restriction(S, actor="admin",
                                                             reason="again"),
                  code="not_restricted", http=409)


# --- (e) appeal flow: open -> grant lifts restriction -----------------------
def test_appeal_flow_grant_lifts():
    S = 870
    _approve_seller(S)
    admin.admin_restrict_seller(S, actor="admin", reason="review")
    ap = admin.submit_appeal(S, reason="please reconsider")
    aid = ap["appeal_id"]
    assert ap["state"] == "open", ap
    open_list = admin.admin_list_appeals(state="open", user_id=S)
    assert any(a["appeal_id"] == aid for a in open_list), open_list
    res = admin.admin_resolve_appeal(aid, "grant", actor="admin", reason="valid")
    assert res["restriction_lifted"] is True, res
    assert svc.get_seller(S)["status"] == "approved"
    # now marked resolved; cannot resolve again
    resolved_list = admin.admin_list_appeals(state="resolved", user_id=S)
    assert any(a["appeal_id"] == aid for a in resolved_list), resolved_list
    _expect_error(lambda: admin.admin_resolve_appeal(aid, "deny", actor="admin",
                                                     reason="x"),
                  code="already_resolved", http=409)


# --- (f) payout balance read + audit-only note ------------------------------
def test_payout_balance_and_note():
    S = 880
    B = 881
    _approve_seller(S)
    pid, oid = _paid_order(S, B, price=2000, qty=2, inv=5)
    orders_mod.fulfill_order(oid, S, tracking_ref="T")
    orders_mod.complete_order(oid, B)
    bal = admin.admin_seller_payout_balance(S)
    assert bal["payable_cents"] == 3600, bal
    assert bal["disbursement"] == "provider_side_out_of_scope", bal
    before_payable = ledger.get_balance(orders_mod.seller_payable_account(S))
    note = admin.admin_record_payout_note(S, actor="admin", reason="paid via ACH",
                                          amount_cents=3600, provider_reference="ACH-1")
    assert note["moved_money"] is False, note
    # ledger balance is UNCHANGED — the note moved no money
    assert ledger.get_balance(orders_mod.seller_payable_account(S)) == before_payable
    # note is on the append-only audit log
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_mkt_audit "
            "WHERE action = 'admin_payout_settlement_note' AND subject_ref = ?",
            (str(S),)).fetchone()[0]
        assert n == 1, n
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_admin_order_inspection,
        test_admin_refund_governed,
        test_admin_dispute_resolution,
        test_admin_seller_restrict_and_lift,
        test_appeal_flow_grant_lifts,
        test_payout_balance_and_note,
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
