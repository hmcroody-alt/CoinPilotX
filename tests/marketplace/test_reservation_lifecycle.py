"""Reservation lifecycle — the money-safety invariants.

These are the cases from the payment-hardening directive that Stage 1-2
(schema + canonical TTL) makes testable. The remaining cases — Stripe
reconciliation, the webhook branches, and the expiry sweeper — arrive with
Stages 4-7 and extend this file rather than starting a new one.

Every test here is deterministic: no clocks are read, no network is touched,
and every timestamp is passed in explicitly.

Two invariants matter more than the rest, because they are the ones that cost
real money rather than merely looking untidy:

* stock must never be credited twice for one reservation, and
* a reservation that has been captured (the buyer paid) must never be
  releasable, no matter which path asks or how many times.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_cart_routes as cart
from services import marketplace_reservation_policy as policy


LISTING_ID = 7
STARTING_STOCK = 5


@pytest.fixture(autouse=True)
def _reset_column_cache():
    """The column cache is process-global; each test gets its own database."""
    cart._RESERVATION_COLUMN_CACHE = None
    yield
    cart._RESERVATION_COLUMN_CACHE = None


def _db(*, legacy: bool = False):
    """A cursor over a fresh in-memory marketplace.

    ``legacy=True`` reproduces a database that predates the lifecycle columns,
    which is what production looks like at the instant this code first
    deploys.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_listings "
                "(id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)")
    cur.execute("""CREATE TABLE marketplace_inventory_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_transaction_id INTEGER UNIQUE,
        buyer_user_id INTEGER, listing_id INTEGER, quantity INTEGER DEFAULT 1,
        status TEXT DEFAULT 'held', created_at TEXT, updated_at TEXT)""")
    if not legacy:
        cart._ensure_reservation_lifecycle_columns(cur)
    cur.execute("INSERT INTO marketplace_listings VALUES (?, ?, '')",
                (LISTING_ID, STARTING_STOCK))
    return cur


def _reserve(cur, tx_id, qty=2, *, status="held", expires_at=None,
             reserved_at="2026-08-31T12:00:00+00:00"):
    cur.execute(
        """INSERT INTO marketplace_inventory_reservations
        (seller_transaction_id, buyer_user_id, listing_id, quantity, status,
         created_at, updated_at)
        VALUES (?, 1, ?, ?, ?, ?, ?)""",
        (tx_id, LISTING_ID, qty, status, reserved_at, reserved_at))
    # The decrement the checkout route performs alongside the row.
    cur.execute("UPDATE marketplace_listings SET quantity=quantity-? WHERE id=?",
                (qty, LISTING_ID))
    if expires_at is not None:
        cur.execute("UPDATE marketplace_inventory_reservations SET expires_at=?, reserved_at=? "
                    "WHERE seller_transaction_id=?", (expires_at, reserved_at, tx_id))
    return tx_id


def _stock(cur):
    return cur.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                       (LISTING_ID,)).fetchone()[0]


def _row(cur, tx_id):
    return dict(cur.execute(
        "SELECT * FROM marketplace_inventory_reservations WHERE seller_transaction_id=?",
        (tx_id,)).fetchone())


# --------------------------------------------------------------------------
# Reservation creation
# --------------------------------------------------------------------------

def test_reservation_decrements_stock_and_records_a_deadline():
    cur = _db()
    _reserve(cur, 1, qty=2, expires_at=policy.expires_at_for("2026-08-31T12:00:00+00:00"))
    row = _row(cur, 1)
    assert _stock(cur) == STARTING_STOCK - 2
    assert row["status"] == policy.STATUS_HELD
    assert row["expires_at"] == "2026-08-31T12:15:00+00:00"
    assert row["released_at"] is None and row["captured_at"] is None


def test_every_new_reservation_carries_an_expiry():
    """The defect this mission exists to fix: a hold with no deadline is a
    hold that can never be collected, because a dismissed payment sheet
    produces no webhook to collect it."""
    cur = _db()
    _reserve(cur, 1, expires_at=policy.expires_at_for("2026-08-31T12:00:00+00:00"))
    assert _row(cur, 1)["expires_at"]


# --------------------------------------------------------------------------
# Success consumes stock
# --------------------------------------------------------------------------

def test_capture_consumes_stock_and_never_returns_it():
    cur = _db()
    _reserve(cur, 1, qty=2)
    after_reserve = _stock(cur)
    result = cart.capture_inventory_reservation(cur, 1, now="2026-08-31T12:05:00+00:00")
    assert result["changed"] is True
    assert _stock(cur) == after_reserve, "capture must not touch listing quantity"
    assert _row(cur, 1)["status"] == policy.STATUS_CAPTURED


def test_duplicate_capture_is_harmless():
    cur = _db()
    _reserve(cur, 1)
    cart.capture_inventory_reservation(cur, 1)
    before = _stock(cur)
    second = cart.capture_inventory_reservation(cur, 1)
    assert second["changed"] is False and second["reason"] == "not_held"
    assert _stock(cur) == before


# --------------------------------------------------------------------------
# Release returns stock — exactly once
# --------------------------------------------------------------------------

@pytest.mark.parametrize("reason", [
    policy.REASON_BUYER_CANCELLED,
    policy.REASON_EXPIRED,
    policy.REASON_PAYMENT_FAILED,
    policy.REASON_PAYMENT_CANCELED,
    policy.REASON_CHECKOUT_ERROR,
    policy.REASON_OUT_OF_STOCK_ROLLBACK,
])
def test_release_returns_stock_for_every_reason(reason):
    """Cancel, expiry, failure and cancellation all converge on one release
    path. If they diverged, each would need its own correct implementation of
    the stock arithmetic — and one of them would eventually be wrong."""
    cur = _db()
    _reserve(cur, 1, qty=2)
    result = cart.release_inventory_reservation(cur, 1, reason=reason)
    assert result["changed"] is True
    assert result["release_reason"] == reason
    assert _stock(cur) == STARTING_STOCK
    assert _row(cur, 1)["status"] == policy.STATUS_RELEASED


def test_duplicate_release_cannot_double_increment_stock():
    cur = _db()
    _reserve(cur, 1, qty=2)
    cart.release_inventory_reservation(cur, 1, reason=policy.REASON_EXPIRED)
    for _ in range(5):
        again = cart.release_inventory_reservation(cur, 1, reason=policy.REASON_EXPIRED)
        assert again["changed"] is False
    assert _stock(cur) == STARTING_STOCK, "stock must be credited exactly once"


def test_paid_reservation_can_never_be_released():
    """The single most expensive failure available here: returning stock for
    an order the buyer has already paid for, so the item is sold twice."""
    cur = _db()
    _reserve(cur, 1, qty=2)
    cart.capture_inventory_reservation(cur, 1)
    consumed = _stock(cur)
    for reason in (policy.REASON_EXPIRED, policy.REASON_BUYER_CANCELLED,
                   policy.REASON_PAYMENT_FAILED):
        result = cart.release_inventory_reservation(cur, 1, reason=reason)
        assert result["changed"] is False
    assert _stock(cur) == consumed
    assert _row(cur, 1)["status"] == policy.STATUS_CAPTURED


def test_expired_reservation_cannot_be_captured_after_release():
    """Ordering matters: once the sweeper has released a hold, a late
    settlement must not silently consume stock that has been given back. The
    capture is refused and the mismatch is left visible for reconciliation
    rather than papered over."""
    cur = _db()
    _reserve(cur, 1, qty=2)
    cart.release_inventory_reservation(cur, 1, reason=policy.REASON_EXPIRED)
    late = cart.capture_inventory_reservation(cur, 1)
    assert late["changed"] is False and late["reason"] == "not_held"
    assert _row(cur, 1)["status"] == policy.STATUS_RELEASED


def test_release_of_unknown_transaction_is_a_no_op():
    cur = _db()
    result = cart.release_inventory_reservation(cur, 999, reason=policy.REASON_EXPIRED)
    assert result["changed"] is False
    assert _stock(cur) == STARTING_STOCK


def test_unrecognised_release_reason_is_normalised_not_stored():
    """A typo'd reason must not silently poison the audit trail."""
    cur = _db()
    _reserve(cur, 1)
    result = cart.release_inventory_reservation(cur, 1, reason="not-a-real-reason")
    assert result["release_reason"] == policy.REASON_MANUAL
    assert _row(cur, 1)["release_reason"] == policy.REASON_MANUAL


# --------------------------------------------------------------------------
# Oversell protection
# --------------------------------------------------------------------------

def test_two_buyers_competing_for_the_final_unit():
    cur = _db()
    cur.execute("UPDATE marketplace_listings SET quantity=1 WHERE id=?", (LISTING_ID,))
    _reserve(cur, 1, qty=1)
    assert _stock(cur) == 0

    # The second buyer's guarded decrement matches no row, exactly as the
    # checkout route's `WHERE quantity>=?` guard requires.
    cur.execute("UPDATE marketplace_listings SET quantity=quantity-1 "
                "WHERE id=? AND quantity>=1", (LISTING_ID,))
    assert cur.rowcount == 0
    assert _stock(cur) == 0

    # First buyer abandons; the unit returns and becomes purchasable again.
    cart.release_inventory_reservation(cur, 1, reason=policy.REASON_EXPIRED)
    assert _stock(cur) == 1


def test_release_never_drives_stock_above_its_reserved_total():
    cur = _db()
    _reserve(cur, 1, qty=2)
    _reserve(cur, 2, qty=3)
    assert _stock(cur) == 0
    cart.release_inventory_reservation(cur, 1, reason=policy.REASON_EXPIRED)
    cart.release_inventory_reservation(cur, 2, reason=policy.REASON_EXPIRED)
    cart.release_inventory_reservation(cur, 1, reason=policy.REASON_EXPIRED)
    cart.release_inventory_reservation(cur, 2, reason=policy.REASON_EXPIRED)
    assert _stock(cur) == STARTING_STOCK


def test_mixed_terminal_states_settle_independently():
    cur = _db()
    _reserve(cur, 1, qty=2)
    _reserve(cur, 2, qty=2)
    cart.capture_inventory_reservation(cur, 1)
    cart.release_inventory_reservation(cur, 2, reason=policy.REASON_BUYER_CANCELLED)
    assert _stock(cur) == STARTING_STOCK - 2
    assert _row(cur, 1)["status"] == policy.STATUS_CAPTURED
    assert _row(cur, 2)["status"] == policy.STATUS_RELEASED


# --------------------------------------------------------------------------
# Durability across restarts
# --------------------------------------------------------------------------

def test_expiry_survives_a_process_restart():
    """The deadline lives in the row, not in memory, so closing the app,
    restarting the backend or redeploying cannot lose it."""
    path = ":memory:"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_inventory_reservations ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, seller_transaction_id INTEGER UNIQUE, "
                "buyer_user_id INTEGER, listing_id INTEGER, quantity INTEGER, "
                "status TEXT, created_at TEXT, updated_at TEXT)")
    cart._ensure_reservation_lifecycle_columns(cur)
    deadline = policy.expires_at_for("2026-08-31T12:00:00+00:00")
    cur.execute("INSERT INTO marketplace_inventory_reservations "
                "(seller_transaction_id, listing_id, quantity, status, expires_at) "
                "VALUES (1, 7, 2, 'held', ?)", (deadline,))
    conn.commit()

    # A new cursor stands in for a fresh worker process reading the same row.
    reread = conn.cursor()
    stored = reread.execute("SELECT expires_at FROM marketplace_inventory_reservations "
                            "WHERE seller_transaction_id=1").fetchone()[0]
    assert stored == deadline
    assert policy.is_expired(stored, now="2026-08-31T12:20:00+00:00")
    conn.close()


# --------------------------------------------------------------------------
# Schema migration
# --------------------------------------------------------------------------

def test_migration_is_additive_and_idempotent():
    cur = _db(legacy=True)
    from services import db as db_module

    before = db_module.get_table_columns(cur, "marketplace_inventory_reservations")
    assert "expires_at" not in before

    cart._ensure_reservation_lifecycle_columns(cur)
    once = db_module.get_table_columns(cur, "marketplace_inventory_reservations")
    cart._ensure_reservation_lifecycle_columns(cur)
    twice = db_module.get_table_columns(cur, "marketplace_inventory_reservations")

    assert before < once and once == twice
    assert {"reserved_at", "expires_at", "released_at", "captured_at",
            "release_reason", "reconciled_at"} <= once


def test_release_still_works_on_a_pre_migration_database():
    """Release runs inside Stripe webhook handlers. If a missing audit column
    made it raise, the webhook would 500 and Stripe would retry indefinitely
    while the stock stayed held — the exact leak this mission is closing."""
    cur = _db(legacy=True)
    _reserve(cur, 1, qty=2)
    result = cart.release_inventory_reservation(cur, 1, reason=policy.REASON_EXPIRED)
    assert result["changed"] is True
    assert _stock(cur) == STARTING_STOCK
    assert _row(cur, 1)["status"] == policy.STATUS_RELEASED


def test_capture_still_works_on_a_pre_migration_database():
    cur = _db(legacy=True)
    _reserve(cur, 1, qty=2)
    held = _stock(cur)
    assert cart.capture_inventory_reservation(cur, 1)["changed"] is True
    assert _stock(cur) == held
    assert _row(cur, 1)["status"] == policy.STATUS_CAPTURED


# --------------------------------------------------------------------------
# TTL policy
# --------------------------------------------------------------------------

def test_default_ttl_is_within_the_directive_band():
    assert 10 * 60 <= policy.DEFAULT_TTL_SECONDS <= 15 * 60


@pytest.mark.parametrize("raw,expected", [
    (None, policy.DEFAULT_TTL_SECONDS),
    ("", policy.DEFAULT_TTL_SECONDS),
    ("600", 600),
    ("1", policy.MIN_TTL_SECONDS),        # clamped up
    ("999999", policy.MAX_TTL_SECONDS),   # clamped down
    ("not-a-number", policy.DEFAULT_TTL_SECONDS),
])
def test_ttl_is_configurable_but_cannot_be_made_unsafe(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(policy.TTL_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(policy.TTL_ENV_VAR, raw)
    assert policy.reservation_ttl_seconds() == expected


def test_expiry_respects_the_grace_period():
    deadline = policy.expires_at_for("2026-08-31T12:00:00+00:00")
    assert not policy.is_expired(deadline, now="2026-08-31T12:14:00+00:00")
    assert not policy.is_expired(deadline, now="2026-08-31T12:15:00+00:00")
    assert policy.is_expired(deadline, now="2026-08-31T12:16:30+00:00")


def test_a_row_with_no_deadline_is_never_treated_as_expired():
    """Rows written before this migration have no deadline. Inventing one
    retroactively could release stock for an order still mid-authorisation."""
    assert policy.is_expired(None) is False
    assert policy.is_expired("") is False
    assert policy.is_expired("garbage") is False


def test_legacy_backfill_anchors_on_the_rows_own_age():
    """A hold taken three hours ago should become collectable immediately,
    not win a fresh fifteen-minute window."""
    backfilled = policy.legacy_backfill_expiry("2026-08-31T09:00:00+00:00")
    assert policy.is_expired(backfilled, now="2026-08-31T12:00:00+00:00")


def test_timestamps_are_normalised_to_utc():
    naive = policy.parse_timestamp("2026-08-31T12:00:00")
    zulu = policy.parse_timestamp("2026-08-31T12:00:00Z")
    offset = policy.parse_timestamp("2026-08-31T12:00:00+00:00")
    assert naive == zulu == offset
    assert naive.tzinfo is not None
