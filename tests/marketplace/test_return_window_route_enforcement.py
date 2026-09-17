"""The Pulse marketplace's own returns route, held to the same deadline.

There are two returns implementations in this repo and they are not duplicates:
``business_os/marketplace/returns.py`` serves Business OS orders, this route pack
serves the Pulse/mobile marketplace and keys on ``seller_transactions``. Both
settle into the same seller ledger, so both had to grow the same deadline — and
this file is the half that covers the route, because nothing covered it at all
before (a search for ``marketplace_returns_routes`` outside ``bot.py`` found no
callers and no tests).

Two defects are pinned here:

  * ``POST /api/pulse/marketplace/returns`` accepted a return on any transaction
    row regardless of the deadline, because its ``OPEN_WINDOW_DAYS = 30`` was
    never read by anything. It is gone; the route now asks the policy module,
    the same one the Business OS engine asks.
  * the route never checked that the purchase was *paid*. Every
    ``seller_transactions`` row in production today is an abandoned or failed
    checkout, so a buyer could open a return against a purchase that never
    happened. That was reachable, not theoretical.

Each refusal below is paired with an acceptance on the same route, so a gate
that rejects everything fails this file too.
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="return_window_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_returns_routes as routes  # noqa: E402
from services.business_os.marketplace import policy  # noqa: E402

BUYER = 70701
SELLER = 70702
WINDOW = policy.STANDARD_RETURN_WINDOW_DAYS


@pytest.fixture(scope="module", autouse=True)
def _app():
    bot.init_db()
    bot.api_account_user = lambda *a, **k: {
        "user_id": BUYER, "username": "window_buyer", "email": "b@example.com"}
    bot.webhook_app.config["TESTING"] = True
    yield
    os.unlink(_DB)


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _days_ago(days: float) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


def _purchase(*, created_at=None, status="paid", buyer=BUYER,
              item_type="marketplace_product"):
    """A real row in the table the route reads, aged however the test needs."""
    conn = _db()
    conn.execute(
        "INSERT INTO marketplace_listings (seller_user_id, title, price_label, "
        "currency, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (SELLER, "Window Lamp", "$25.00", "USD", "active", _days_ago(60),
         _days_ago(60)))
    listing_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO seller_transactions (buyer_user_id, seller_user_id, seller_type, "
        "item_type, item_id, amount_cents, currency, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (buyer, SELLER, "marketplace", item_type, listing_id, 2500, "USD",
         status, created_at or _iso(datetime.now(timezone.utc)),
         _iso(datetime.now(timezone.utc))))
    tx_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()
    return tx_id


def _record_delivery(transaction_id, delivered_at):
    """Write the delivery stamp the way the settlement service does.

    The settlements table is created on that service's first use, which in a
    fresh database has not happened, so the test creates it here with the two
    columns the lookup touches. That is also the honest shape of production on
    day one."""
    conn = _db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS marketplace_commercial_settlements ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, seller_transaction_id INTEGER, "
        "protection_ends_at TEXT, delivered_at TEXT)")
    conn.execute(
        "INSERT INTO marketplace_commercial_settlements (seller_transaction_id, "
        "delivered_at) VALUES (?,?)", (transaction_id, delivered_at))
    conn.commit()
    conn.close()


def _drop_settlements():
    conn = _db()
    conn.execute("DROP TABLE IF EXISTS marketplace_commercial_settlements")
    conn.commit()
    conn.close()


def _open_return(transaction_id, reason="damaged"):
    client = bot.webhook_app.test_client()
    response = client.post("/api/pulse/marketplace/returns",
                           json={"transaction_id": transaction_id, "reason": reason,
                                 "explanation": "arrived cracked"})
    return response.status_code, json.loads(response.data or b"{}")


# --- the constant is gone ----------------------------------------------------
def test_the_dead_thirty_day_constant_no_longer_exists():
    """It disagreed with the policy module's 14 and neither was read. Leaving it
    in place would let a future reader believe the route has its own window."""
    assert not hasattr(routes, "OPEN_WINDOW_DAYS")
    assert routes._policy.STANDARD_RETURN_WINDOW_DAYS == WINDOW


# --- the deadline ------------------------------------------------------------
def test_a_recent_purchase_can_be_returned():
    """The positive control. Every refusal below runs through this same route."""
    status, body = _open_return(_purchase(created_at=_days_ago(1)))
    assert status == 200, body
    assert body["ok"] is True
    assert body["return"]["state"] == "awaiting_seller"


def test_a_purchase_older_than_the_window_is_refused_and_told_the_date():
    tx = _purchase(created_at=_days_ago(WINDOW + 3))
    status, body = _open_return(tx)
    assert status == 409, body
    assert body["error_code"] == "return_window_closed"
    assert str(WINDOW) in body["message"]
    # The deadline is handed back as a field, not only buried in prose, because
    # the mobile client already reads `return_window_closes_at`.
    assert body["return_window_closes_at"], "the client is given nothing to show"
    assert body["return_window_closes_at"].startswith(
        (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d"))
    # Refused means nothing was written.
    conn = _db()
    rows = conn.execute("SELECT COUNT(*) FROM marketplace_returns WHERE transaction_id=?",
                        (tx,)).fetchone()[0]
    conn.close()
    assert rows == 0


def test_the_last_day_of_the_window_still_works():
    """One day earlier than the refusal above, same route, accepted."""
    status, body = _open_return(_purchase(created_at=_days_ago(WINDOW - 1)))
    assert status == 200, body
    assert body["ok"] is True


def test_delivery_anchors_the_route_window_too():
    """A slow shipment must not consume the buyer's return window here either."""
    tx = _purchase(created_at=_days_ago(WINDOW * 3))
    _record_delivery(tx, _days_ago(2))
    status, body = _open_return(tx)
    assert status == 200, body

    # Control: an identical ancient purchase with an ancient delivery is refused,
    # so the acceptance above came from the delivery date and not from the
    # settlements lookup merely existing.
    tx2 = _purchase(created_at=_days_ago(WINDOW * 3))
    _record_delivery(tx2, _days_ago(WINDOW + 5))
    status2, body2 = _open_return(tx2)
    assert status2 == 409, body2
    assert body2["error_code"] == "return_window_closed"


def test_a_missing_settlements_table_falls_back_to_the_purchase_date():
    """The lookup is optional — the settlements table may not have been created
    yet — but "optional" must mean "falls back to purchase", never "no deadline".

    On Postgres the failed SELECT would abort the enclosing transaction, which is
    why the lookup runs inside a SAVEPOINT. SQLite cannot demonstrate that half;
    what it can demonstrate is that the fallback is bounded and that the request
    still completes its INSERT afterwards."""
    _drop_settlements()
    status, body = _open_return(_purchase(created_at=_days_ago(WINDOW + 3)))
    assert status == 409, body
    assert body["error_code"] == "return_window_closed"

    # And the transaction is still usable: a fresh purchase inserts fine right
    # after the failed lookup. This is the part that would break on Postgres if
    # the SAVEPOINT were removed.
    status2, body2 = _open_return(_purchase(created_at=_days_ago(1)))
    assert status2 == 200, body2
    assert body2["return"]["id"]


# --- the purchase has to have happened ---------------------------------------
@pytest.mark.parametrize("state", ["created", "pending", "failed", "expired",
                                   "refunded", "cancelled"])
def test_a_purchase_that_was_never_paid_cannot_be_returned(state):
    status, body = _open_return(_purchase(status=state))
    assert status == 409, body
    assert body["error_code"] == "not_paid"


def test_the_paid_check_is_the_only_thing_standing_between_these_two_cases():
    """Same row, same age, same route — only ``status`` differs."""
    assert _open_return(_purchase(status="paid", created_at=_days_ago(1)))[0] == 200
    assert _open_return(_purchase(status="failed", created_at=_days_ago(1)))[0] == 409


# --- the buyer is told the deadline ------------------------------------------
def _buyer_orders():
    client = bot.webhook_app.test_client()
    response = client.get("/api/pulse/orders?limit=100")
    assert response.status_code == 200
    return {int(o["id"]): o for o in json.loads(response.data)["orders"]}


def test_the_orders_list_finally_sends_the_field_the_app_has_always_read():
    """``mobile-native/src/api/ordersDashboard.ts`` maps
    ``return_window_closes_at`` into ``returnWindowClosesAt``, and no endpoint
    had ever sent it, so the value was permanently undefined: a buyer could only
    discover the deadline by being refused."""
    _drop_settlements()
    tx = _purchase(created_at=_days_ago(3))
    order = _buyer_orders()[tx]
    assert order["return_window_closes_at"], "the app reads this and got nothing"
    expected = (datetime.now(timezone.utc) + timedelta(days=WINDOW - 3)
                ).strftime("%Y-%m-%d")
    assert order["return_window_closes_at"].startswith(expected)


def test_the_served_deadline_is_the_one_the_route_enforces():
    """The number shown and the number enforced must be the same number, which is
    the whole reason both go through the policy module."""
    tx = _purchase(created_at=_days_ago(WINDOW + 2))
    shown = _buyer_orders()[tx]["return_window_closes_at"]
    _, refusal = _open_return(tx)
    assert refusal["error_code"] == "return_window_closed"
    assert refusal["return_window_closes_at"] == shown


def test_delivery_moves_the_served_deadline_too():
    tx = _purchase(created_at=_days_ago(WINDOW * 2))
    before = _buyer_orders()[tx]["return_window_closes_at"]
    _record_delivery(tx, _days_ago(1))
    after = _buyer_orders()[tx]["return_window_closes_at"]
    assert after > before, "a recorded delivery must push the deadline out"


@pytest.mark.parametrize("state", ["created", "failed", "refunded", "cancelled"])
def test_no_deadline_is_invented_for_an_order_that_cannot_be_returned(state):
    """Showing a return deadline on a refunded or never-paid order would be worse
    than showing nothing: it promises a right that does not exist.

    ``.get`` rather than ``[]`` because absent and null are the same fact to the
    client — ``return_window_closes_at || undefined`` — and this test is about
    the promise not being made, not about which of the two shapes carries it."""
    tx = _purchase(status=state, created_at=_days_ago(1))
    assert _buyer_orders()[tx].get("return_window_closes_at") is None


def test_a_paid_order_of_the_same_age_does_get_one():
    """Control for the test above — the difference is the status, not the age."""
    tx = _purchase(status="paid", created_at=_days_ago(1))
    assert _buyer_orders()[tx].get("return_window_closes_at") is not None


def test_the_deadline_costs_one_lookup_per_page_not_one_per_order():
    """The orders list renders up to 100 rows. A per-row lookup here is exactly
    the N+1 that made this the slowest surface in the app once already, so the
    batching is asserted rather than assumed from reading the code."""
    from services import marketplace_settlement_service as settlements

    fresh = [_purchase(created_at=_days_ago(2)) for _ in range(6)]
    calls = []
    real = settlements.delivered_at_map

    def counting(cur, transaction_ids):
        ids = list(transaction_ids or ())
        calls.append(ids)
        return real(cur, ids)

    settlements.delivered_at_map = counting
    try:
        served = _buyer_orders()
    finally:
        settlements.delivered_at_map = real

    assert len(calls) == 1, f"one batched lookup expected, got {len(calls)}"
    # And the single call really did cover the page, so "1 call" is not achieved
    # by looking nothing up.
    assert set(fresh).issubset(set(calls[0]))
    assert all(served[tx]["return_window_closes_at"] for tx in fresh)


# --- untouched neighbours ----------------------------------------------------
def test_the_older_refusals_still_fire_first():
    """The new gates are additions, not replacements: ownership and item type
    are still checked, and are still checked *before* the deadline, so a stranger
    is told "not found" rather than being told when someone else's window
    closes."""
    stranger_tx = _purchase(buyer=99999, created_at=_days_ago(WINDOW + 3))
    status, body = _open_return(stranger_tx)
    assert status == 404 and "not found" in body["message"].lower()

    wrong_type = _purchase(item_type="subscription", created_at=_days_ago(1))
    status, body = _open_return(wrong_type)
    assert status == 400
    assert "marketplace purchases only" in body["message"]
