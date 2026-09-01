"""Stage 16 — the deterministic test matrix for the expiry sweep.

Everything here runs against a real SQLite database with the real lifecycle
columns, and nothing here touches a clock or a network. ``now`` and
``fetch_status`` are both injected on every call, which is what makes each
assertion a fact about the decision path rather than a fact about how long the
test took to run.

Two properties are worth stating up front, because most of the cases below
exist to defend one of them.

*Stock is never handed back to a listing for an order that might still pay.*
That is why the provider-unreachable case, the ``processing`` case and the
unknown-status case all assert ``released == 0`` and assert the listing quantity
is unchanged — asserting the decision alone would pass even if the sweeper
called the settlement path anyway.

*The sweep is safe to run twice.* Every mutation underneath it is a
compare-and-swap on ``status='held'``, so the second sweep over the same rows
must be a no-op. That is checked directly rather than assumed, because an
idempotency bug here does not raise: it silently credits stock twice.

The provider-call assertions are the other recurring shape. The sweep costs one
Stripe read per ambiguous candidate and zero for everything else, and
``result["provider_calls"]`` counts them at the boundary, so "healthy
reservations generate no provider traffic" is measured rather than argued.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_cart_routes as cart
from services import marketplace_reservation_policy as policy
from services import marketplace_reservation_reconciler as reconciler
from services import marketplace_reservation_sweeper as sweeper


LISTING_ID = 7
STARTING_STOCK = 50

#: Every timestamp below is anchored to this. The TTL is 15 minutes and the
#: grace window is 60 seconds, so a hold that expired at 12:00 is collectable
#: from 12:01 onward and this instant is comfortably past that.
NOW = "2026-08-31T12:30:00+00:00"
LONG_EXPIRED = "2026-08-31T11:00:00+00:00"
EXPIRED = "2026-08-31T12:00:00+00:00"
#: Thirty seconds past due — inside the grace window, so not yet collectable.
WITHIN_GRACE = "2026-08-31T12:29:30+00:00"
NOT_EXPIRED = "2026-08-31T12:45:00+00:00"


@pytest.fixture(autouse=True)
def _reset_column_cache():
    cart._RESERVATION_COLUMN_CACHE = None
    yield
    cart._RESERVATION_COLUMN_CACHE = None


@pytest.fixture(autouse=True)
def _clear_env():
    """Every bound in this subsystem is re-read from the environment on each
    call, so a stray value in the developer's shell would silently retune the
    behaviour under test."""
    names = (
        reconciler.MAX_DEFERRALS_ENV_VAR,
        sweeper.BATCH_LIMIT_ENV_VAR,
        sweeper.MIN_RECHECK_ENV_VAR,
        policy.TTL_ENV_VAR,
    )
    previous = {name: os.environ.pop(name, None) for name in names}
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

def _db():
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
    cur.execute("""CREATE TABLE seller_transactions (
        id INTEGER PRIMARY KEY, buyer_user_id INTEGER, status TEXT,
        stripe_payment_intent_id TEXT, metadata_json TEXT, updated_at TEXT)""")
    cart._ensure_reservation_lifecycle_columns(cur)
    cur.execute("INSERT INTO marketplace_listings VALUES (?, ?, '')",
                (LISTING_ID, STARTING_STOCK))
    return cur


def _order(cur, tx_id, *, qty=2, tx_status="checkout_created", intent=None,
           expires_at=EXPIRED, status=policy.STATUS_HELD, reconciled_at=None,
           deferrals=0, reserved=True):
    """One transaction plus its hold, with the listing already decremented.

    Mirrors what checkout actually writes: the stock is taken at reservation
    time, so a release must give it back and a capture must not.
    """
    cur.execute(
        "INSERT INTO seller_transactions (id, buyer_user_id, status, stripe_payment_intent_id) "
        "VALUES (?, 1, ?, ?)",
        (tx_id, tx_status, intent))
    if reserved:
        cur.execute(
            """INSERT INTO marketplace_inventory_reservations
            (seller_transaction_id, buyer_user_id, listing_id, quantity, status,
             created_at, updated_at, reserved_at, expires_at, reconciled_at,
             reconcile_deferrals)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx_id, LISTING_ID, qty, status, LONG_EXPIRED, LONG_EXPIRED,
             LONG_EXPIRED, expires_at, reconciled_at, deferrals))
        if status == policy.STATUS_HELD:
            cur.execute("UPDATE marketplace_listings SET quantity=quantity-? WHERE id=?",
                        (qty, LISTING_ID))
    return tx_id


def _stock(cur):
    return cur.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                       (LISTING_ID,)).fetchone()[0]


def _tx(cur, tx_id):
    return dict(cur.execute("SELECT * FROM seller_transactions WHERE id=?", (tx_id,)).fetchone())


def _res(cur, tx_id):
    return dict(cur.execute(
        "SELECT * FROM marketplace_inventory_reservations WHERE seller_transaction_id=?",
        (tx_id,)).fetchone())


def _fetcher(mapping):
    """A Stripe stub that records what it was asked about.

    Returning a mapping rather than a constant keeps multi-row cases honest: a
    sweep that asked about the wrong intent would otherwise still pass.
    """
    seen = []

    def fetch(intent_id):
        seen.append(intent_id)
        value = mapping[intent_id]
        if isinstance(value, Exception):
            raise value
        return value

    fetch.seen = seen
    return fetch


def _sweep(cur, **kwargs):
    kwargs.setdefault("now", NOW)
    return sweeper.run_reservation_expiry_sweep(cur, **kwargs)


# --------------------------------------------------------------------------
# 1-8 — candidate selection
# --------------------------------------------------------------------------

def test_01_no_candidates_returns_a_zeroed_summary():
    cur = _db()
    result = _sweep(cur)
    assert result["candidates"] == 0
    assert result["released"] == 0
    assert result["deferred"] == 0
    assert result["failed"] == 0
    assert result["provider_calls"] == 0
    assert result["batch_exhausted"] is False


def test_02_result_contract_exposes_every_documented_key():
    """The worker must never parse a log line to find out what happened."""
    cur = _db()
    result = _sweep(cur)
    for key in ("scanned", "candidates", "released", "captured", "deferred",
                "skipped", "reconciled", "failed", "would_release",
                "would_defer", "would_skip", "provider_calls",
                "needs_attention", "dry_run", "limit", "batch_exhausted",
                "duration_ms"):
        assert key in result, key


def test_03_a_hold_that_has_not_expired_is_not_a_candidate():
    cur = _db()
    _order(cur, 100, expires_at=NOT_EXPIRED)
    before = _stock(cur)
    result = _sweep(cur)
    assert result["candidates"] == 0
    assert _stock(cur) == before
    assert _res(cur, 100)["status"] == policy.STATUS_HELD


def test_04_a_hold_inside_the_grace_window_is_not_a_candidate():
    """The grace window absorbs clock skew and lets a racing webhook land."""
    cur = _db()
    _order(cur, 100, expires_at=WITHIN_GRACE)
    result = _sweep(cur)
    assert result["candidates"] == 0
    assert _res(cur, 100)["status"] == policy.STATUS_HELD


def test_05_a_hold_past_the_grace_window_is_a_candidate():
    cur = _db()
    _order(cur, 100, expires_at=EXPIRED)
    result = _sweep(cur)
    assert result["candidates"] == 1


def test_06_terminal_reservations_are_never_reconsidered():
    cur = _db()
    _order(cur, 100, status=policy.STATUS_RELEASED)
    _order(cur, 101, status=policy.STATUS_CAPTURED)
    result = _sweep(cur)
    assert result["candidates"] == 0


@pytest.mark.parametrize("settled_status", ["paid", "refunded"])
def test_07_a_settled_transaction_is_excluded_before_any_provider_call(settled_status):
    """The cheapest safety guard there is: a paid order is not even looked at."""
    cur = _db()
    _order(cur, 100, tx_status=settled_status, intent="pi_paid")
    fetch = _fetcher({"pi_paid": "succeeded"})
    result = _sweep(cur, fetch_status=fetch)
    assert result["candidates"] == 0
    assert result["provider_calls"] == 0
    assert fetch.seen == []
    assert _res(cur, 100)["status"] == policy.STATUS_HELD


def test_08_a_row_with_no_deadline_at_all_is_left_alone():
    """Pre-migration rows have no ``expires_at``. Inventing one retroactively
    would release stock for an order that may still be in flight."""
    cur = _db()
    _order(cur, 100, expires_at=None)
    _order(cur, 101, expires_at="")
    _order(cur, 102, expires_at="not-a-timestamp")
    result = _sweep(cur)
    assert result["candidates"] == 0
    assert result["released"] == 0
    for tx_id in (100, 101, 102):
        assert _res(cur, tx_id)["status"] == policy.STATUS_HELD


# --------------------------------------------------------------------------
# 9-16 — the decision table, end to end
# --------------------------------------------------------------------------

def test_09_no_payment_intent_releases_without_touching_stripe():
    cur = _db()
    _order(cur, 100, qty=2, intent=None)
    assert _stock(cur) == STARTING_STOCK - 2
    fetch = _fetcher({})

    result = _sweep(cur, fetch_status=fetch)

    assert result["released"] == 1
    assert result["provider_calls"] == 0
    assert fetch.seen == []
    assert _stock(cur) == STARTING_STOCK
    assert _res(cur, 100)["status"] == policy.STATUS_RELEASED
    assert _tx(cur, 100)["status"] == "checkout_expired"


def test_10_stripe_canceled_releases_and_closes_the_order_as_canceled():
    """Reuses the ``payment_intent.canceled`` webhook's terminal status so one
    outcome does not appear under two names in an owner's reports."""
    cur = _db()
    _order(cur, 100, qty=3, intent="pi_1")
    fetch = _fetcher({"pi_1": "canceled"})

    result = _sweep(cur, fetch_status=fetch)

    assert result["released"] == 1
    assert result["provider_calls"] == 1
    assert _stock(cur) == STARTING_STOCK
    assert _res(cur, 100)["release_reason"] == policy.REASON_PAYMENT_CANCELED
    assert _tx(cur, 100)["status"] == "canceled"


def test_11_stripe_succeeded_never_returns_stock():
    """A lost ``payment_intent.succeeded`` webhook. The hold is consumed so no
    later sweep can hand a paid item back; the order repair is left to an
    operator rather than reimplemented here."""
    cur = _db()
    _order(cur, 100, qty=4, intent="pi_1")
    fetch = _fetcher({"pi_1": "succeeded"})

    result = _sweep(cur, fetch_status=fetch)

    assert result["released"] == 0
    assert result["captured"] == 1
    assert result["needs_attention"] == 1
    assert _stock(cur) == STARTING_STOCK - 4
    assert _res(cur, 100)["status"] == policy.STATUS_CAPTURED


def test_12_stripe_processing_defers_and_holds_the_stock():
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1")
    fetch = _fetcher({"pi_1": "processing"})

    result = _sweep(cur, fetch_status=fetch)

    assert result["released"] == 0
    assert result["deferred"] == 1
    assert _stock(cur) == STARTING_STOCK - 2
    assert _res(cur, 100)["status"] == policy.STATUS_HELD
    assert _res(cur, 100)["reconciled_at"] == NOW


@pytest.mark.parametrize("status", sorted(reconciler.AWAITING_BUYER_STATUSES))
def test_13_an_awaiting_buyer_status_defers_while_the_bound_allows(status):
    cur = _db()
    _order(cur, 100, intent="pi_1")
    fetch = _fetcher({"pi_1": status})

    result = _sweep(cur, fetch_status=fetch)

    assert result["deferred"] == 1
    assert result["released"] == 0
    assert _res(cur, 100)["reconcile_deferrals"] == 1


@pytest.mark.parametrize("status", sorted(reconciler.AWAITING_BUYER_STATUSES))
def test_14_an_awaiting_buyer_status_releases_once_the_bound_is_exhausted(status):
    """Otherwise an abandoned 3-D Secure prompt holds scarce stock forever —
    indistinguishable from an active one, and unbounded."""
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1", deferrals=reconciler.MAX_DEFERRALS)
    fetch = _fetcher({"pi_1": status})

    result = _sweep(cur, fetch_status=fetch)

    assert result["released"] == 1
    assert _stock(cur) == STARTING_STOCK
    assert _res(cur, 100)["release_reason"] == policy.REASON_EXPIRED


def test_15_processing_is_never_force_released_even_past_the_bound():
    """An asynchronous method still settling may yet succeed. The bound
    terminates a wait on the *buyer*, not a wait on the *bank*."""
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1", deferrals=reconciler.MAX_DEFERRALS * 3)
    fetch = _fetcher({"pi_1": "processing"})

    result = _sweep(cur, fetch_status=fetch)

    assert result["released"] == 0
    assert result["deferred"] == 1
    assert result["needs_attention"] == 1
    assert _stock(cur) == STARTING_STOCK - 2


def test_16_an_unrecognised_stripe_status_defers_rather_than_releasing():
    """Stripe may add a status. This module must not release stock on a string
    it has never seen."""
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1")
    fetch = _fetcher({"pi_1": "some_future_status"})

    result = _sweep(cur, fetch_status=fetch)

    assert result["released"] == 0
    assert result["deferred"] == 1
    assert result["needs_attention"] == 1
    assert _stock(cur) == STARTING_STOCK - 2


def test_17_an_unreachable_stripe_never_produces_a_release():
    """The mass-release failure mode. During an outage every expired hold
    reconciles at once; if that released, one provider incident would empty the
    store and resell paid orders wholesale."""
    cur = _db()
    for tx_id in range(100, 110):
        _order(cur, tx_id, qty=1, intent=f"pi_{tx_id}")
    fetch = _fetcher({f"pi_{tx_id}": RuntimeError("stripe down")
                      for tx_id in range(100, 110)})

    result = _sweep(cur, fetch_status=fetch)

    assert result["candidates"] == 10
    assert result["released"] == 0
    assert result["deferred"] == 10
    assert result["needs_attention"] == 10
    assert _stock(cur) == STARTING_STOCK - 10
    for tx_id in range(100, 110):
        assert _res(cur, tx_id)["status"] == policy.STATUS_HELD


def test_18_a_release_reason_is_always_a_real_machine_reason():
    """``release_inventory_reservation`` normalises an unrecognised reason to
    ``manual``, so a wrong constant here would silently produce an audit trail
    that claims a human did it."""
    cur = _db()
    _order(cur, 100, intent=None)
    _order(cur, 101, intent="pi_1")
    fetch = _fetcher({"pi_1": "canceled"})

    _sweep(cur, fetch_status=fetch)

    for tx_id in (100, 101):
        reason = _res(cur, tx_id)["release_reason"]
        assert reason in policy.RELEASE_REASONS
        assert reason != policy.REASON_MANUAL


# --------------------------------------------------------------------------
# 19-21 — dry run
# --------------------------------------------------------------------------

def test_19_dry_run_evaluates_a_release_without_performing_it():
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1")
    fetch = _fetcher({"pi_1": "canceled"})

    result = _sweep(cur, fetch_status=fetch, dry_run=True)

    assert result["dry_run"] is True
    assert result["would_release"] == 1
    assert result["released"] == 0
    assert _stock(cur) == STARTING_STOCK - 2
    assert _res(cur, 100)["status"] == policy.STATUS_HELD
    assert _tx(cur, 100)["status"] == "checkout_created"


def test_20_dry_run_writes_no_backoff_state_for_a_deferral():
    """A dry run that recorded a deferral would advance the bound toward a
    release nobody asked for."""
    cur = _db()
    _order(cur, 100, intent="pi_1")
    fetch = _fetcher({"pi_1": "processing"})

    result = _sweep(cur, fetch_status=fetch, dry_run=True)

    assert result["would_defer"] == 1
    assert result["deferred"] == 0
    row = _res(cur, 100)
    assert row["reconciled_at"] is None
    assert (row["reconcile_deferrals"] or 0) == 0


def test_21_dry_run_does_not_capture():
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1")
    fetch = _fetcher({"pi_1": "succeeded"})

    result = _sweep(cur, fetch_status=fetch, dry_run=True)

    assert result["would_skip"] == 1
    assert result["captured"] == 0
    assert _res(cur, 100)["status"] == policy.STATUS_HELD


# --------------------------------------------------------------------------
# 22-26 — bounds, ordering, backoff, provider traffic
# --------------------------------------------------------------------------

def test_22_the_batch_limit_is_a_hard_bound():
    cur = _db()
    for tx_id in range(100, 120):
        _order(cur, tx_id, qty=1, intent=None)

    result = _sweep(cur, limit=5)

    assert result["candidates"] == 5
    assert result["released"] == 5
    assert result["batch_exhausted"] is True
    assert _stock(cur) == STARTING_STOCK - 15


def test_23_candidates_are_ordered_oldest_deadline_first_then_by_id():
    """Deterministic ordering is what makes a bounded batch drain a backlog
    instead of starving its oldest rows."""
    cur = _db()
    _order(cur, 103, expires_at="2026-08-31T12:00:00+00:00")
    _order(cur, 101, expires_at="2026-08-31T12:00:00+00:00")
    _order(cur, 102, expires_at="2026-08-31T10:00:00+00:00")

    rows = sweeper.select_expiry_candidates(cur, now=NOW, limit=10)

    assert [row["seller_transaction_id"] for row in rows] == [102, 101, 103]


def test_24_a_recently_deferred_row_is_left_alone_until_the_backoff_elapses():
    """Without this a ``processing`` order would be re-read from Stripe on every
    single cycle — a hundred and twenty provider calls an hour for one
    undecided payment."""
    cur = _db()
    _order(cur, 100, intent="pi_1", reconciled_at="2026-08-31T12:29:00+00:00")
    fetch = _fetcher({"pi_1": "processing"})

    result = _sweep(cur, fetch_status=fetch, recheck_seconds=300)

    assert result["candidates"] == 0
    assert result["provider_calls"] == 0


def test_25_a_row_past_its_backoff_window_is_reconsidered():
    cur = _db()
    _order(cur, 100, intent="pi_1", reconciled_at="2026-08-31T12:00:00+00:00")
    fetch = _fetcher({"pi_1": "processing"})

    result = _sweep(cur, fetch_status=fetch, recheck_seconds=300)

    assert result["candidates"] == 1
    assert result["deferred"] == 1


def test_26_only_ambiguous_rows_generate_provider_traffic():
    """One Stripe read per candidate that actually needs one, and none for the
    rest. The count comes from the boundary, not from an assertion about intent."""
    cur = _db()
    _order(cur, 100, intent=None)                       # no intent  → 0 calls
    _order(cur, 101, tx_status="paid", intent="pi_a")   # settled    → not a candidate
    _order(cur, 102, expires_at=NOT_EXPIRED, intent="pi_b")  # healthy → not a candidate
    _order(cur, 103, intent="pi_c")                     # ambiguous  → 1 call
    _order(cur, 104, intent="pi_d")                     # ambiguous  → 1 call
    fetch = _fetcher({"pi_c": "canceled", "pi_d": "processing"})

    result = _sweep(cur, fetch_status=fetch)

    assert result["candidates"] == 3
    assert result["provider_calls"] == 2
    assert sorted(fetch.seen) == ["pi_c", "pi_d"]
    assert result["reconciled"] == 2


# --------------------------------------------------------------------------
# 27-31 — isolation, idempotency, durability
# --------------------------------------------------------------------------

def test_27_one_failing_row_does_not_cost_the_others_their_sweep():
    """The failure boundary wraps a whole candidate, and a raise leaves that row
    untouched and eligible for the next cycle."""
    cur = _db()
    _order(cur, 100, qty=1, intent=None)
    _order(cur, 101, qty=1, intent="pi_boom")
    _order(cur, 102, qty=1, intent=None)

    real_settle = cart.settle_failed_transactions

    def exploding_fetch(intent_id):
        raise KeyboardInterrupt("not caught by decide_for_reservation")

    # `decide_for_reservation` swallows `Exception`, so a plain error would be
    # converted to a defer and never reach the sweeper's own boundary. A
    # BaseException that is not an Exception would escape entirely, so the
    # failure is injected at the settlement path instead — the realistic place
    # for a per-row database error.
    calls = {"n": 0}

    def flaky_settle(cur_, ids, **kwargs):
        calls["n"] += 1
        if list(ids) == [101]:
            raise sqlite3.OperationalError("row-level failure")
        return real_settle(cur_, ids, **kwargs)

    cart.settle_failed_transactions = flaky_settle
    try:
        result = _sweep(cur, fetch_status=_fetcher({"pi_boom": "canceled"}))
    finally:
        cart.settle_failed_transactions = real_settle

    assert result["candidates"] == 3
    assert result["released"] == 2
    assert result["failed"] == 1
    assert _res(cur, 101)["status"] == policy.STATUS_HELD
    assert _stock(cur) == STARTING_STOCK - 1


def test_28_a_second_sweep_over_the_same_rows_changes_nothing():
    """An idempotency bug here does not raise. It credits stock twice."""
    cur = _db()
    _order(cur, 100, qty=3, intent=None)

    first = _sweep(cur)
    assert first["released"] == 1
    assert _stock(cur) == STARTING_STOCK

    second = _sweep(cur)

    assert second["candidates"] == 0
    assert second["released"] == 0
    assert _stock(cur) == STARTING_STOCK


def test_29_a_hold_captured_between_selection_and_settlement_is_not_released():
    """The compare-and-swap underneath. Selection is a filter, not a lock."""
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1")
    rows = sweeper.select_expiry_candidates(cur, now=NOW, limit=10)
    assert len(rows) == 1

    # The webhook wins the race after the sweep chose its candidates.
    cart.capture_inventory_reservation(cur, 100, now=NOW)
    assert _stock(cur) == STARTING_STOCK - 2

    result = _sweep(cur, fetch_status=_fetcher({"pi_1": "canceled"}))

    assert result["released"] == 0
    assert _stock(cur) == STARTING_STOCK - 2
    assert _res(cur, 100)["status"] == policy.STATUS_CAPTURED


def test_30_the_deferral_count_survives_between_sweeps_and_eventually_terminates():
    """The bound only exists if the counter is durable. Without the column each
    sweep would restart at zero and ``requires_action`` would defer forever."""
    cur = _db()
    _order(cur, 100, qty=2, intent="pi_1")
    fetch = _fetcher({"pi_1": "requires_action"})

    stamps = [f"2026-08-31T{12 + n}:30:00+00:00" for n in range(reconciler.MAX_DEFERRALS)]
    for index, stamp in enumerate(stamps, start=1):
        result = _sweep(cur, now=stamp, fetch_status=fetch, recheck_seconds=0)
        assert result["deferred"] == 1, stamp
        assert _res(cur, 100)["reconcile_deferrals"] == index

    final = _sweep(cur, now="2026-08-31T23:30:00+00:00", fetch_status=fetch,
                   recheck_seconds=0)

    assert final["released"] == 1
    assert _stock(cur) == STARTING_STOCK
    assert _res(cur, 100)["release_reason"] == policy.REASON_EXPIRED


def test_31_the_sweep_is_driven_entirely_by_injected_time():
    """Same database, two different ``now`` values, two different answers — and
    no wall clock anywhere in between."""
    cur = _db()
    _order(cur, 100, expires_at="2026-08-31T18:00:00+00:00", intent=None)

    early = _sweep(cur, now="2026-08-31T17:00:00+00:00")
    assert early["candidates"] == 0

    late = _sweep(cur, now="2026-08-31T18:30:00+00:00")
    assert late["released"] == 1
    assert _res(cur, 100)["released_at"] == "2026-08-31T18:30:00+00:00"


# --------------------------------------------------------------------------
# 32-35 — configuration, portability, and the structural constraint
# --------------------------------------------------------------------------

def test_32_the_batch_limit_is_clamped_against_a_configuration_typo():
    os.environ[sweeper.BATCH_LIMIT_ENV_VAR] = "0"
    assert sweeper.batch_limit() == 1
    os.environ[sweeper.BATCH_LIMIT_ENV_VAR] = "99999"
    assert sweeper.batch_limit() == 500
    os.environ[sweeper.BATCH_LIMIT_ENV_VAR] = "not a number"
    assert sweeper.batch_limit() == sweeper.DEFAULT_BATCH_LIMIT
    os.environ.pop(sweeper.BATCH_LIMIT_ENV_VAR)
    assert sweeper.batch_limit() == sweeper.DEFAULT_BATCH_LIMIT


def test_33_the_backoff_window_is_clamped():
    os.environ[sweeper.MIN_RECHECK_ENV_VAR] = "-5"
    assert sweeper.min_recheck_seconds() == 0
    os.environ[sweeper.MIN_RECHECK_ENV_VAR] = "999999"
    assert sweeper.min_recheck_seconds() == 3600
    os.environ[sweeper.MIN_RECHECK_ENV_VAR] = "junk"
    assert sweeper.min_recheck_seconds() == sweeper.DEFAULT_MIN_RECHECK_SECONDS


def test_34_the_candidate_query_survives_a_database_without_the_new_columns():
    """A partial migration must not stop the sweep. A sweeper that stops working
    recreates the leak it exists to fix."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_listings "
                "(id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)")
    cur.execute("""CREATE TABLE marketplace_inventory_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, seller_transaction_id INTEGER UNIQUE,
        buyer_user_id INTEGER, listing_id INTEGER, quantity INTEGER DEFAULT 1,
        status TEXT DEFAULT 'held', created_at TEXT, updated_at TEXT,
        expires_at TEXT)""")
    cur.execute("""CREATE TABLE seller_transactions (
        id INTEGER PRIMARY KEY, buyer_user_id INTEGER, status TEXT,
        stripe_payment_intent_id TEXT, metadata_json TEXT, updated_at TEXT)""")
    cur.execute("INSERT INTO marketplace_listings VALUES (?, ?, '')",
                (LISTING_ID, STARTING_STOCK))
    cur.execute("INSERT INTO seller_transactions (id, buyer_user_id, status) "
                "VALUES (100, 1, 'checkout_created')")
    cur.execute("""INSERT INTO marketplace_inventory_reservations
        (seller_transaction_id, buyer_user_id, listing_id, quantity, status,
         created_at, updated_at, expires_at)
        VALUES (100, 1, ?, 2, 'held', ?, ?, ?)""",
        (LISTING_ID, LONG_EXPIRED, LONG_EXPIRED, EXPIRED))

    rows = sweeper.select_expiry_candidates(cur, now=NOW, limit=10)

    assert [row["seller_transaction_id"] for row in rows] == [100]
    assert "reconcile_deferrals" not in rows[0]


def _executable_strings(path: str) -> str:
    """Every string literal the module can actually execute, docstrings removed.

    Reading the raw file would be wrong here: this module's docstrings *name*
    the mutations it promises not to perform, and a test that cannot tell a
    prohibition from a violation is worse than no test. Docstrings are the first
    statement of a module, class or function, so they are identifiable in the
    AST; comments never reach the AST at all.
    """
    import ast

    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        first = body[0] if body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstrings.add(id(first.value))

    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    return "\n".join(literals).lower()


def test_35_every_named_telemetry_event_is_actually_emitted(caplog):
    """Named events, not prose. The worker reads the structured result, but an
    operator debugging a live incident reads these, and an event that only
    exists in the directive is not observability."""
    cur = _db()
    _order(cur, 100, intent=None)          # → released
    _order(cur, 101, intent="pi_defer")    # → deferred + reconciled
    _order(cur, 102, intent="pi_boom")     # → failed
    fetch = _fetcher({"pi_defer": "processing", "pi_boom": "canceled"})

    real_settle = cart.settle_failed_transactions

    def flaky_settle(cur_, ids, **kwargs):
        if list(ids) == [102]:
            raise sqlite3.OperationalError("row-level failure")
        return real_settle(cur_, ids, **kwargs)

    cart.settle_failed_transactions = flaky_settle
    try:
        with caplog.at_level("DEBUG", logger=sweeper.LOGGER.name):
            _sweep(cur, fetch_status=fetch)
    finally:
        cart.settle_failed_transactions = real_settle

    emitted = "\n".join(record.getMessage() for record in caplog.records)
    for event in ("RESERVATION_SWEEP_STARTED", "RESERVATION_CANDIDATE",
                  "RESERVATION_RECONCILED", "RESERVATION_RELEASED",
                  "RESERVATION_DEFERRED", "RESERVATION_FAILED",
                  "RESERVATION_SWEEP_COMPLETED"):
        assert event in emitted, event


def test_36_telemetry_carries_no_credential_or_card_material(caplog):
    """Sweep logs go to Railway's log drain, which is not a secret store."""
    cur = _db()
    _order(cur, 100, intent="pi_1")
    fetch = _fetcher({"pi_1": "canceled"})

    with caplog.at_level("DEBUG", logger=sweeper.LOGGER.name):
        _sweep(cur, fetch_status=fetch)

    emitted = "\n".join(record.getMessage() for record in caplog.records).lower()
    for forbidden in ("sk_live", "sk_test", "rk_live", "whsec_",
                      "card_number", "cvc", "client_secret"):
        assert forbidden not in emitted, forbidden


def test_37_the_sweeper_executes_no_private_release_or_close_mutation():
    """Stages 5-6 collapsed six copies of the release-plus-close pair into one
    function, and the wiring guard now walks every module under ``services/`` to
    keep it that way. This asserts the sweeper did not become the seventh."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "services", "marketplace_reservation_sweeper.py")

    sql = _executable_strings(path)
    assert "update marketplace_listings" not in sql
    assert "update seller_transactions" not in sql
    assert "update marketplace_inventory_reservations" not in sql
    assert "insert into" not in sql
    assert "delete from" not in sql

    import ast

    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "release_inventory_reservation" not in called
    assert "settle_failed_transactions" in called
