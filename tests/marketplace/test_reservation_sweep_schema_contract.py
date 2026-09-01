"""Stage 176B — the portable non-vacuity proof.

The companion file, ``test_reservation_schema_bootstrap.py``, imports
``services.marketplace_reservation_schema``. That module does not exist at
8a21d1b9, so running that file against the pre-fix tree produces a collection
error — which is technically a failure, and evidentially worth very little. "The
test failed because the module it imports was not written yet" says nothing
about whether the behaviour changed.

This file is written so it can be *collected and executed unmodified against
either tree*. It imports only ``marketplace_reservation_sweeper``, which exists
in both, and it asserts on the returned result dictionary rather than on any
symbol introduced by the fix. So when it fails at 8a21d1b9 and passes here, the
delta is behaviour and nothing else.

What 8a21d1b9 actually does, measured
-------------------------------------
Against a database in production's pre-bootstrap shape the old sweep does not
crash. Both its primary query and its "fallback" raise ``no such column:
r.expires_at`` — the fallback re-selected the same column, so it rescued
nothing — and the outer handler swallows both, returning::

    {'scanned': 0, 'candidates': 0, 'released': 0, ..., 'failed': 1}

with no status and no reason anywhere in it. That is the precise shape the
directive rules out: a worker that cannot see the deadline column reports the
same ``candidates: 0`` as a worker that swept a healthy table and found nothing
due. The inventory leak and the clean bill of health are the same three numbers.

The three assertions below are the contract that distinguishes them.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_reservation_sweeper as sweeper


NOW = "2026-08-31T12:30:00+00:00"

#: Production's reservations table before any cart request migrated it. Written
#: out literally, with no reference to any constant in the fix, so this fixture
#: is identical on both trees.
PRE_BOOTSTRAP_SQL = (
    """CREATE TABLE marketplace_listings
       (id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)""",
    """CREATE TABLE marketplace_inventory_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_transaction_id INTEGER UNIQUE,
        buyer_user_id INTEGER,
        listing_id INTEGER,
        quantity INTEGER DEFAULT 1,
        status TEXT DEFAULT 'held',
        created_at TEXT,
        updated_at TEXT)""",
    """CREATE TABLE seller_transactions (
        id INTEGER PRIMARY KEY, buyer_user_id INTEGER, status TEXT,
        stripe_payment_intent_id TEXT, metadata_json TEXT, updated_at TEXT)""",
    "INSERT INTO marketplace_listings VALUES (7, 50, '')",
)


@pytest.fixture(autouse=True)
def _reset_any_schema_cache():
    """Clear the process-global ready flag if this tree has one.

    Guarded by ``getattr`` so the fixture is a no-op on the pre-fix tree, where
    the module does not exist. Without it, a test file that ran earlier in the
    session could leave the flag set and let the ensure short-circuit against a
    table it never actually migrated.
    """
    try:
        from services import marketplace_reservation_schema as schema
    except ImportError:
        yield
        return
    schema.reset_schema_cache()
    yield
    schema.reset_schema_cache()


def _pre_bootstrap_cursor():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for statement in PRE_BOOTSTRAP_SQL:
        cur.execute(statement)
    return cur


def _columns(cur):
    return {row[1] for row in cur.execute(
        "PRAGMA table_info(marketplace_inventory_reservations)").fetchall()}


def test_the_fixture_is_genuinely_pre_bootstrap():
    """Guards every assertion below. If something migrates this table behind our
    backs, the rest of the file is measuring a database that was never in the
    broken state."""
    cur = _pre_bootstrap_cursor()
    assert "expires_at" not in _columns(cur)
    assert "reconcile_deferrals" not in _columns(cur)


def test_the_worker_bootstraps_the_schema_without_any_web_request():
    """FAILS at 8a21d1b9: the column is never created, because the only code
    that creates it lives behind a cart route handler that a worker process
    never reaches."""
    cur = _pre_bootstrap_cursor()

    sweeper.run_reservation_expiry_sweep(cur, now=NOW, dry_run=True)

    assert "expires_at" in _columns(cur), (
        "the sweep must create its own lifecycle columns; a worker that needs a "
        "buyer to open a cart first is not independently bootable")


def test_a_sweep_that_could_not_look_does_not_report_a_clean_table(monkeypatch):
    """FAILS at 8a21d1b9: the result carries no reason at all.

    The bootstrap is disabled here so that the query builder faces the same
    un-migrated table the old code faced. What must not happen is the old
    outcome — ``candidates: 0`` with nothing to distinguish it from a healthy
    sweep of an empty queue.
    """
    cur = _pre_bootstrap_cursor()
    try:
        from services import marketplace_reservation_schema as schema
    except ImportError:
        schema = None
    if schema is not None:
        monkeypatch.setattr(
            schema, "ensure_reservation_schema",
            lambda c, **kw: {"status": schema.STATUS_READY, "columns": [],
                             "missing": [], "added": [], "table_created": False,
                             "error": None})

    result = sweeper.run_reservation_expiry_sweep(cur, now=NOW, dry_run=True)

    assert result.get("reason"), (
        "a sweep blocked by a missing column must say so; "
        f"got a result with no reason: {result}")
    assert result.get("status") == "degraded"
    assert result["failed"] == 1
    assert result["candidates"] == 0
    assert result["released"] == 0


def test_a_blocked_sweep_mutates_nothing():
    """Safety, and it holds on both trees — asserted anyway because it is the
    property that made the production failure survivable rather than damaging."""
    cur = _pre_bootstrap_cursor()
    cur.execute(
        "INSERT INTO marketplace_inventory_reservations "
        "(seller_transaction_id, buyer_user_id, listing_id, quantity, status) "
        "VALUES (1, 1, 7, 2, 'held')")
    cur.execute("UPDATE marketplace_listings SET quantity=48 WHERE id=7")

    sweeper.run_reservation_expiry_sweep(cur, now=NOW, dry_run=False)

    stock = cur.execute("SELECT quantity FROM marketplace_listings WHERE id=7").fetchone()[0]
    status = cur.execute("SELECT status FROM marketplace_inventory_reservations "
                         "WHERE seller_transaction_id=1").fetchone()[0]
    assert stock == 48
    assert status == "held"
