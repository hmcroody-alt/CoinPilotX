"""Marketplace — the seller money READ surface (`money.py`).

The payments screen is only as honest as this module, so these tests are about
the ways a money view goes quietly wrong rather than about whether it returns
numbers at all:

  1. available is the payable accrual, and only completed orders reach it
  2. the escrow total is a server-side sum across per-order accounts
  3. escrow is read from the ledger, not from order.total_cents, so a partial
     refund is reflected the moment it lands
  4. a fully refunded open order contributes zero and is not listed as held
  5. the paid/fulfilled split follows the real state machine, and stays in sync
     with it by construction
  6. one seller's money never appears in another seller's overview
  7. the activity feed is one ordered, paginated union across payable + escrow,
     with signs taken from the seller's point of view
  8. the module is read-only: it cannot move money

    python tests/business_os/test_marketplace_money_read.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_money_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as svc  # noqa: E402
from services.business_os.marketplace import orders as orders_mod  # noqa: E402
from services.business_os.marketplace import refunds as refunds_mod  # noqa: E402
from services.business_os.marketplace import money  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

SELLER = 900
SELLER2 = 901
BUYER = 902
ADMIN = "admin:9"


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ledger.ensure_schema()


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def _approve(uid):
    svc.upsert_seller(uid, display_name="S%s" % uid)
    svc.set_seller_status(uid, "approved", actor=ADMIN)


def _live_product(seller, price=2000, inv=10):
    pid = svc.create_product(seller, title="W", price_cents=price,
                             inventory_qty=inv, context=_ctx())["product_id"]
    svc.transition_product(seller, pid, "publish", context=_ctx())
    return pid


def _order(seller, price=2000, qty=1, advance_to="paid"):
    """Create an order for `seller` and drive it to the requested state."""
    pid = _live_product(seller, price=price)
    oid = orders_mod.create_order(BUYER, pid, quantity=qty, context=_ctx())["order_id"]
    if advance_to == "created":
        return oid
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    if advance_to == "paid":
        return oid
    orders_mod.fulfill_order(oid, seller, tracking_ref="T")
    if advance_to == "fulfilled":
        return oid
    orders_mod.complete_order(oid, BUYER, context=_ctx())
    return oid


def _reset():
    conn = db.connect()
    for t in ("ledger_entries", "ledger_transactions", "ledger_balances",
              "business_os_mkt_orders", "business_os_mkt_order_items",
              "business_os_mkt_order_events"):
        try:
            conn.execute("DELETE FROM " + t)
        except Exception:
            pass
    conn.commit()
    conn.close()


# --- 1. available is the accrual, and only completed orders reach it --------
def test_available_reflects_only_completed_orders():
    _reset(); _approve(SELLER)

    _order(SELLER, price=5000, advance_to="paid")
    assert money.seller_money_overview(SELLER)["available_cents"] == 0, (
        "a captured-but-unsettled order owes the seller nothing yet")

    _order(SELLER, price=5000, advance_to="fulfilled")
    assert money.seller_money_overview(SELLER)["available_cents"] == 0, (
        "fulfilment is not settlement")

    _order(SELLER, price=5000, advance_to="completed")
    ov = money.seller_money_overview(SELLER)
    # 10% platform fee: the seller nets 4500 of a 5000 order.
    assert ov["available_cents"] == 4500, ov["available_cents"]
    assert ov["available_cents"] == ledger.get_balance(
        orders_mod.seller_payable_account(SELLER), "usd"), (
        "available must BE the ledger balance, not a recomputation of it")


# --- 2 & 5. escrow total and the state split -------------------------------
def test_escrow_total_is_summed_across_accounts_and_split_by_real_state():
    _reset(); _approve(SELLER)

    _order(SELLER, price=1000, advance_to="paid")
    _order(SELLER, price=2000, advance_to="paid")
    _order(SELLER, price=4000, advance_to="fulfilled")
    _order(SELLER, price=8000, advance_to="completed")   # settles, leaves escrow
    _order(SELLER, price=1600, advance_to="created")     # never captured

    ov = money.seller_money_overview(SELLER)
    assert ov["escrow_total_cents"] == 7000, ov["escrow_total_cents"]
    assert ov["escrow_by_status"]["paid"] == 3000
    assert ov["escrow_by_status"]["fulfilled"] == 4000
    assert ov["escrow_order_count"] == 3

    assert sorted(money.ESCROW_STATUSES) == sorted(orders_mod.IN_ESCROW_STATUSES), (
        "the money view must take the held states from the state machine, not "
        "keep its own copy that can drift")

    # The total is a sum of distinct ledger accounts, so it must equal the sum
    # of those balances read independently.
    independent = sum(ledger.get_balance(a, "usd") for a in ov["accounts"]["escrow"])
    assert independent == ov["escrow_total_cents"]


# --- 3. escrow follows the ledger, not the order total ---------------------
def test_partial_refund_moves_the_held_figure_immediately():
    _reset(); _approve(SELLER)
    oid = _order(SELLER, price=10000, advance_to="paid")

    assert money.seller_money_overview(SELLER)["escrow_total_cents"] == 10000
    refunds_mod.refund_order(oid, amount_cents=2500, actor=ADMIN, reason="partial")

    ov = money.seller_money_overview(SELLER)
    assert ov["escrow_total_cents"] == 7500, (
        "held must come off the ledger; order.total_cents is still 10000")
    row = [o for o in ov["escrow_orders"] if o["order_id"] == oid][0]
    assert row["held_cents"] == 7500


# --- 4. nothing held is not listed as held ---------------------------------
def test_fully_refunded_open_order_holds_nothing():
    _reset(); _approve(SELLER)
    oid = _order(SELLER, price=3000, advance_to="paid")
    refunds_mod.refund_order(oid, amount_cents=3000, actor=ADMIN, reason="full")

    ov = money.seller_money_overview(SELLER)
    assert ov["escrow_total_cents"] == 0
    assert ov["escrow_order_count"] == 0
    assert all(o["order_id"] != oid for o in ov["escrow_orders"]), (
        "an order holding nothing must not appear in a held list")


# --- 6. sellers are isolated ------------------------------------------------
def test_one_sellers_money_never_leaks_into_anothers():
    _reset(); _approve(SELLER); _approve(SELLER2)

    _order(SELLER, price=5000, advance_to="paid")
    _order(SELLER, price=5000, advance_to="completed")
    _order(SELLER2, price=9900, advance_to="paid")

    a = money.seller_money_overview(SELLER)
    b = money.seller_money_overview(SELLER2)

    assert a["escrow_total_cents"] == 5000 and a["available_cents"] == 4500
    assert b["escrow_total_cents"] == 9900 and b["available_cents"] == 0
    assert not set(a["accounts"]["escrow"]) & set(b["accounts"]["escrow"])

    feed_b = money.seller_activity(SELLER2)
    assert all(t["account"] in ([b["accounts"]["payable"]] + b["accounts"]["escrow"])
               for t in feed_b["transactions"])


# --- 7. the activity feed -------------------------------------------------
def test_activity_is_one_ordered_union_with_seller_relative_signs():
    _reset(); _approve(SELLER)
    _order(SELLER, price=1000, advance_to="paid")
    _order(SELLER, price=2000, advance_to="completed")

    feed = money.seller_activity(SELLER, limit=50)
    assert feed["transactions"], "the feed must not be empty after real orders"
    assert feed["accounts_scanned"] >= 2, "payable plus at least one escrow account"

    # A settlement credits payable; from the seller's point of view that is
    # money arriving, so the sign is positive.
    settle = [t for t in feed["transactions"]
              if t["account"] == orders_mod.seller_payable_account(SELLER)]
    assert settle and all(t["signed_amount_cents"] > 0 for t in settle)

    for t in feed["transactions"]:
        assert t["transaction_id"] and t["cursor"], "rows must be identifiable"
        assert t["status"], "rows must carry a real status"


def test_activity_paginates_without_gaps():
    _reset(); _approve(SELLER)
    for _ in range(6):
        _order(SELLER, price=1000, advance_to="completed")

    seen, cursor, guard = [], None, 0
    while True:
        page = money.seller_activity(SELLER, limit=3, before_cursor=cursor)
        seen.extend(t["cursor"] for t in page["transactions"])
        guard += 1
        assert guard < 25, "pagination failed to terminate"
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]

    assert len(seen) == len(set(seen)), "no row may be returned twice"
    assert len(seen) >= 6


# --- disputes: seller-scoped, and honest about what it cannot say ----------
def test_seller_sees_only_their_own_disputes():
    _reset(); _approve(SELLER); _approve(SELLER2)
    mine = _order(SELLER, price=4000, advance_to="paid")
    theirs = _order(SELLER2, price=4000, advance_to="paid")
    refunds_mod.open_dispute(mine, BUYER, reason="damaged", context=_ctx())
    refunds_mod.open_dispute(theirs, BUYER, reason="late", context=_ctx())

    a = money.seller_disputes(SELLER)
    assert [c["order_id"] for c in a["cases"]] == [mine]
    assert a["open_count"] == 1
    b = money.seller_disputes(SELLER2)
    assert [c["order_id"] for c in b["cases"]] == [theirs]


def test_dispute_reports_money_at_stake_from_the_ledger():
    _reset(); _approve(SELLER)
    oid = _order(SELLER, price=10000, advance_to="paid")
    refunds_mod.open_dispute(oid, BUYER, reason="wrong item", context=_ctx())
    refunds_mod.refund_order(oid, amount_cents=4000, actor=ADMIN, reason="partial")

    case = money.seller_disputes(SELLER)["cases"][0]
    assert case["amount_cents"] == 10000, "the order was for 10000"
    assert case["held_cents"] == 6000, "but only 6000 is still at stake"
    assert case["refunded_cents"] == 4000


def test_dispute_read_does_not_invent_a_deadline_or_authority():
    _reset(); _approve(SELLER)
    oid = _order(SELLER, price=2000, advance_to="paid")
    refunds_mod.open_dispute(oid, BUYER, reason="x", context=_ctx())

    page = money.seller_disputes(SELLER)
    assert page["auto_approval_policy"] == "none_defined", (
        "there is no auto-approval timer in this backend; claiming one would "
        "put a false countdown in front of a seller")
    assert page["seller_can_resolve"] is False, (
        "resolve_dispute takes an admin actor; a seller-facing resolve button "
        "would 403")
    assert page["cases"][0]["response_deadline"] is None


def test_resolved_disputes_are_reachable_but_not_in_the_open_list():
    _reset(); _approve(SELLER)
    oid = _order(SELLER, price=2000, advance_to="paid")
    did = refunds_mod.open_dispute(oid, BUYER, reason="x", context=_ctx())["dispute_id"]
    refunds_mod.resolve_dispute(did, resolution="deny", actor=ADMIN, reason="no")

    assert money.seller_disputes(SELLER, status="open")["cases"] == []
    every = money.seller_disputes(SELLER, status=None)
    assert len(every["cases"]) == 1 and every["cases"][0]["dispute_id"] == did
    assert every["open_count"] is None, (
        "an open count is meaningless on an unfiltered list and must not be faked")


# --- 8. read-only by construction ------------------------------------------
def test_module_cannot_move_money():
    src = open(money.__file__, "r", encoding="utf-8").read()
    # Strip the docstring and comments: this test is about what the code does,
    # not about whether the file is allowed to mention the write side.
    code = "\n".join(
        line for line in src.split('"""')[-1].split("\n")
        if not line.strip().startswith("#"))
    for forbidden in ("post_entry", "refund_order", "pay_order", "complete_order",
                      "INSERT ", "UPDATE ", "DELETE "):
        assert forbidden not in code, (
            "money.py is the read surface; found %r, which can change state"
            % forbidden)


def test_disbursement_is_reported_as_absent_not_invented():
    _reset(); _approve(SELLER)
    ov = money.seller_money_overview(SELLER)
    assert ov["payout_execution"] == "provider_side_out_of_scope"
    for invented in ("next_payout_date", "payout_method", "instant_payout_fee_cents",
                     "estimated_arrival"):
        assert invented not in ov, (
            "the overview must not carry a field this environment cannot source")


def test_overview_carries_no_ad_wallet_balance_of_its_own():
    """The ad-wallet card must not have a second source.

    There are two advertiser wallets in this codebase — the Pulse Ads wallet
    that the Advertising screen renders, and the Business OS vertical's ledger
    wallet. They are different systems with different balances. If this overview
    returned the second one, the Payments screen and the Advertising screen
    would both show a card labelled "ad wallet", both sourced from a real
    backend, showing different numbers.

    So the overview carries no wallet balance at all, only a pointer saying
    which endpoint owns that card. This test is the guard: it fails the moment
    somebody adds a balance-shaped ad-wallet field back here.
    """
    _reset(); _approve(SELLER)
    ov = money.seller_money_overview(SELLER)
    assert ov["ad_wallet_source"] == "pulse_ads_wallet_endpoint", ov

    for leaked in ("ad_wallet", "ad_wallet_cents", "ad_wallet_balance_cents",
                   "wallet_balance_cents", "wallet"):
        assert leaked not in ov, (
            "the overview must not carry %r — the ad-wallet card has exactly "
            "one source and it is not this endpoint" % leaked)


if __name__ == "__main__":
    setup_module()
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  %s" % fn.__name__)
    print("\n%d passed" % len(fns))
