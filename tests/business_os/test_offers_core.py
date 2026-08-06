"""Business OS — Marketplace OFFERS engine, exercised DIRECTLY.

Proves the negotiation engine honours the mission's non-negotiables:

  * DARK when BUSINESS_OS_MARKETPLACE is off — every entry point raises 503;
  * full happy path buyer-offer -> seller-counter -> buyer-accept -> convert
    lands on the SAME canonical business_os_mkt_orders table at the AGREED
    per-unit price, and the canonical pay/settle path still works on it;
  * **accepting an offer moves NO money** — the shared ledger is untouched until
    the buyer pays the converted order through the one payment engine;
  * acceptance takes a hard inventory hold; withdraw/expire restore exactly what
    was held; conversion consumes the reservation;
  * ownership scoping (stranger read -> None), turn-taking (proposer cannot
    answer their own proposal), self-offer refusal, duplicate-open-offer
    refusal, illegal transitions -> 409, account hold -> 403;
  * expiry sweep flips lapsed offers and releases accepted holds; touching a
    lapsed offer expires it on contact;
  * every mutation lands in business_os_mkt_audit and the offer event stream.

    python tests/business_os/test_offers_core.py   # no pytest needed
"""

import os
import tempfile
from datetime import timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_offers_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as eng  # noqa: E402
from services.business_os.marketplace import offers as off  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 800
BUYER = 801
STRANGER = 802
ADMIN = "admin:8"

_uid_counter = [900]


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    off.ensure_schema()
    ledger.ensure_schema()


def _fresh_buyer():
    _uid_counter[0] += 1
    return _uid_counter[0]


def _product(price=1000, inventory=5, seller=SELLER, fulfillment="physical"):
    mkt.upsert_seller(seller, display_name="S")
    mkt.set_seller_status(seller, "approved", actor=ADMIN)
    p = mkt.create_product(seller, title="Lamp", price_cents=price,
                           inventory_qty=inventory, fulfillment_type=fulfillment,
                           context=_ctx())
    mkt.transition_product(seller, p["product_id"], "publish", context=_ctx())
    return p["product_id"]


def _inventory(pid):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT inventory_qty FROM business_os_mkt_products WHERE product_id = ?",
            (pid,)).fetchone()
        return row["inventory_qty"] if hasattr(row, "keys") else row[0]
    finally:
        conn.close()


def _expect(code, http, fn):
    try:
        fn()
        raise AssertionError(f"expected {code}")
    except MarketplaceError as e:
        assert e.http_status == http and e.code == code, (e.http_status, e.code)


def _backdate_expiry(offer_id, hours=1):
    """Move an offer's clock into the past (test-only time travel)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT expires_at FROM business_os_mkt_offers "
                           "WHERE offer_id = ?", (offer_id,)).fetchone()
        exp = off._parse_iso(row["expires_at"] if hasattr(row, "keys") else row[0])
        past = off._iso(exp - timedelta(hours=off.OFFER_TTL_HOURS + hours))
        conn.execute("UPDATE business_os_mkt_offers SET expires_at = ? "
                     "WHERE offer_id = ?", (past, offer_id))
        conn.execute("UPDATE business_os_mkt_offer_reservations SET expires_at = ? "
                     "WHERE offer_id = ?", (past, offer_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for fn in (
            lambda: off.create_offer(BUYER, "mktp_x", 100),
            lambda: off.counter_offer("mkoff_x", SELLER, 100),
            lambda: off.accept_offer("mkoff_x", SELLER),
            lambda: off.decline_offer("mkoff_x", SELLER),
            lambda: off.withdraw_offer("mkoff_x", BUYER),
            lambda: off.convert_offer("mkoff_x", BUYER),
            lambda: off.expire_offers(),
        ):
            _expect("disabled", 503, fn)
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_full_negotiation_to_paid_order_at_agreed_price():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=5)
    o = off.create_offer(buyer, pid, 700, quantity=2, context=_ctx())
    assert o["status"] == "needs_response" and o["current_proposer"] == "buyer"
    # No hold yet — a proposal is words, not a claim on stock.
    assert _inventory(pid) == 5

    o = off.counter_offer(o["offer_id"], SELLER, 850, context=_ctx())
    assert o["status"] == "countered" and o["current_proposer"] == "seller"

    # Ledger silent through acceptance: the intake account never moves.
    intake_before = ledger.get_balance(eng.INTAKE_ACCOUNT, "usd")
    o = off.accept_offer(o["offer_id"], buyer, context=_ctx())
    assert o["status"] == "accepted" and o["agreed_amount_cents"] == 850
    assert ledger.get_balance(eng.INTAKE_ACCOUNT, "usd") == intake_before
    # Hard hold taken.
    assert _inventory(pid) == 3
    res = off.get_reservation(o["reservation_id"])
    assert res["status"] == "active" and res["inventory_held"] == 1

    o = off.convert_offer(o["offer_id"], buyer, context=_ctx())
    assert o["status"] == "converted" and o["converted_order_id"]
    # Hold consumed and restored (pay takes its own guarded decrement).
    assert off.get_reservation(o["reservation_id"])["status"] == "consumed"
    assert _inventory(pid) == 5

    order = eng.get_order(o["converted_order_id"])
    assert order["status"] == "created"
    assert order["total_cents"] == 1700          # 850 x 2, NOT list price
    items = eng.get_order_items(order["order_id"])
    assert items[0]["unit_price_cents"] == 850 and items[0]["quantity"] == 2

    # The canonical payment engine finishes the job on the same row.
    eng.pay_order(order["order_id"], buyer, context=_ctx())
    assert ledger.get_balance(eng.escrow_account(order["order_id"]), "usd") == 1700
    assert _inventory(pid) == 3


def test_accept_moves_no_money_anywhere():
    buyer = _fresh_buyer()
    pid = _product(price=500, inventory=3)
    o = off.create_offer(buyer, pid, 400, context=_ctx())
    conn = db.connect()
    try:
        n_before = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_transactions").fetchone()
        n_before = n_before["c"] if hasattr(n_before, "keys") else n_before[0]
    finally:
        conn.close()
    off.accept_offer(o["offer_id"], SELLER, context=_ctx())
    conn = db.connect()
    try:
        n_after = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_transactions").fetchone()
        n_after = n_after["c"] if hasattr(n_after, "keys") else n_after[0]
    finally:
        conn.close()
    assert n_after == n_before, "acceptance created a ledger transaction"


def test_withdraw_after_accept_restores_hold():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=4)
    o = off.create_offer(buyer, pid, 900, quantity=3, context=_ctx())
    off.accept_offer(o["offer_id"], SELLER, context=_ctx())
    assert _inventory(pid) == 1
    o = off.withdraw_offer(o["offer_id"], buyer, context=_ctx())
    assert o["status"] == "withdrawn"
    assert _inventory(pid) == 4
    assert off.get_reservation(o["reservation_id"])["status"] == "released"


def test_expiry_sweep_releases_accepted_hold():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=2)
    o = off.create_offer(buyer, pid, 800, quantity=2, context=_ctx())
    o = off.accept_offer(o["offer_id"], SELLER, context=_ctx())
    assert _inventory(pid) == 0
    _backdate_expiry(o["offer_id"])
    n = off.expire_offers()
    assert n >= 1
    o2 = off.get_offer(o["offer_id"])
    assert o2["status"] == "expired"
    assert _inventory(pid) == 2
    assert off.get_reservation(o["reservation_id"])["status"] == "expired"


def test_lapsed_offer_expires_on_touch():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=5)
    o = off.create_offer(buyer, pid, 600, context=_ctx())
    _backdate_expiry(o["offer_id"])
    _expect("offer_expired", 409,
            lambda: off.accept_offer(o["offer_id"], SELLER, context=_ctx()))
    assert off.get_offer(o["offer_id"])["status"] == "expired"


def test_turn_taking_enforced():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=5)
    o = off.create_offer(buyer, pid, 600, context=_ctx())
    # The buyer proposed; the buyer cannot answer their own proposal.
    _expect("not_your_turn", 409,
            lambda: off.accept_offer(o["offer_id"], buyer, context=_ctx()))
    _expect("not_your_turn", 409,
            lambda: off.counter_offer(o["offer_id"], buyer, 650, context=_ctx()))
    # After the seller counters, the seller is likewise locked out.
    off.counter_offer(o["offer_id"], SELLER, 900, context=_ctx())
    _expect("not_your_turn", 409,
            lambda: off.accept_offer(o["offer_id"], SELLER, context=_ctx()))


def test_scoping_self_offer_duplicates_and_holds():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=5)
    _expect("self_offer", 400,
            lambda: off.create_offer(SELLER, pid, 500, context=_ctx()))
    _expect("account_hold", 403,
            lambda: off.create_offer(buyer, pid, 500, context=_ctx(status="suspended")))
    o = off.create_offer(buyer, pid, 500, context=_ctx())
    _expect("duplicate_offer", 409,
            lambda: off.create_offer(buyer, pid, 550, context=_ctx()))
    # Stranger reads get None — existence not leaked.
    assert off.get_offer(o["offer_id"], requester_user_id=STRANGER) is None
    _expect("not_found", 404,
            lambda: off.accept_offer(o["offer_id"], STRANGER, context=_ctx()))
    _expect("invalid_amount", 400,
            lambda: off.create_offer(_fresh_buyer(), pid, 0, context=_ctx()))
    _expect("insufficient_inventory", 409,
            lambda: off.create_offer(_fresh_buyer(), pid, 500, quantity=99, context=_ctx()))


def test_illegal_transitions_are_409():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=5)
    o = off.create_offer(buyer, pid, 500, context=_ctx())
    off.decline_offer(o["offer_id"], SELLER, context=_ctx())
    for fn in (
        lambda: off.accept_offer(o["offer_id"], SELLER, context=_ctx()),
        lambda: off.counter_offer(o["offer_id"], SELLER, 600, context=_ctx()),
        lambda: off.withdraw_offer(o["offer_id"], buyer, context=_ctx()),
        lambda: off.convert_offer(o["offer_id"], buyer, context=_ctx()),
    ):
        _expect("illegal_transition", 409, fn)
    # Convert before acceptance is likewise illegal.
    buyer2 = _fresh_buyer()
    o2 = off.create_offer(buyer2, pid, 500, context=_ctx())
    _expect("illegal_transition", 409,
            lambda: off.convert_offer(o2["offer_id"], buyer2, context=_ctx()))


def test_unlimited_inventory_reservation_holds_nothing():
    seller = 850
    buyer = _fresh_buyer()
    pid = _product(price=700, inventory=None, seller=seller, fulfillment="digital")
    o = off.create_offer(buyer, pid, 600, context=_ctx())
    o = off.accept_offer(o["offer_id"], seller, context=_ctx())
    res = off.get_reservation(o["reservation_id"])
    assert res["inventory_held"] == 0 and res["status"] == "active"
    assert _inventory(pid) is None
    o = off.convert_offer(o["offer_id"], buyer, context=_ctx())
    assert eng.get_order(o["converted_order_id"])["total_cents"] == 600


def test_audit_and_event_trail_complete():
    buyer = _fresh_buyer()
    pid = _product(price=1000, inventory=5)
    o = off.create_offer(buyer, pid, 700, context=_ctx())
    off.counter_offer(o["offer_id"], SELLER, 800, context=_ctx())
    off.accept_offer(o["offer_id"], buyer, context=_ctx())
    off.convert_offer(o["offer_id"], buyer, context=_ctx())
    events = off.get_offer_events(o["offer_id"])
    assert [e["to_status"] for e in events] == [
        "needs_response", "countered", "accepted", "converted"]
    conn = db.connect()
    try:
        actions = [r["action"] if hasattr(r, "keys") else r[0] for r in conn.execute(
            "SELECT action FROM business_os_mkt_audit WHERE subject_type = 'offer' "
            "AND subject_ref = ? ORDER BY id", (o["offer_id"],)).fetchall()]
    finally:
        conn.close()
    assert actions == ["offer.create", "offer.counter", "offer.accept", "offer.convert"]


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_full_negotiation_to_paid_order_at_agreed_price,
        test_accept_moves_no_money_anywhere,
        test_withdraw_after_accept_restores_hold,
        test_expiry_sweep_releases_accepted_hold,
        test_lapsed_offer_expires_on_touch,
        test_turn_taking_enforced,
        test_scoping_self_offer_duplicates_and_holds,
        test_illegal_transitions_are_409,
        test_unlimited_inventory_reservation_holds_nothing,
        test_audit_and_event_trail_complete,
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
