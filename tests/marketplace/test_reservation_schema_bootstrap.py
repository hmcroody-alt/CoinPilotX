"""Stage 176B — the sweeper must be bootable against production on its own.

What broke in production
------------------------
The lifecycle columns the sweep reads were created lazily by a cart route
handler. ``pulse_worker`` is a separate Railway service that never serves an
HTTP request, so on the real deployment the dependency read::

    a buyer opens a cart  →  columns exist  →  the sweeper works

and until a buyer did, every cycle died on ``column r.expires_at does not
exist``. The old fallback did not help: it re-issued a query that named
``expires_at`` as well, so it failed with the identical error.

What these tests establish
--------------------------
Every database below is built to the *pre-bootstrap* shape — the reservations
table exactly as it shipped, with none of the lifecycle columns — and no test
in this file calls a cart route or ``cart._ensure_reservation_lifecycle_columns``
before sweeping. That is the whole point: if the worker needs web traffic to
work, nothing here can pass.

On non-vacuity
--------------
A regression test that passes for the wrong reason is worse than no test.
Three separate proofs are carried here rather than assumed:

``test_02`` runs the historical query text against the pre-bootstrap table and
requires it to raise, which proves the fixture really is missing the column and
is not quietly being migrated by something else.

``test_03`` disables the bootstrap and requires the sweep to stop reporting
success, which proves the bootstrap — not some unrelated change — is what makes
``test_01`` pass.

``test_04`` asserts the generated SQL never mentions an absent column, which is
the property the old fallback violated and the reason it failed identically to
the query it was supposed to rescue.

A note on cache hygiene
-----------------------
``ensure_reservation_schema`` keeps a process-global "already done" flag, and
``cart._ensure_reservation_lifecycle_columns`` sets it too. Any test file that
ran earlier in the session can therefore leave it set. Without the autouse
reset below, a partial-migration case would short-circuit the ensure and report
ready against a table that is not — passing for exactly the reason this file
exists to rule out.
"""

import logging
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_reservation_policy as policy
from services import marketplace_reservation_schema as schema
from services import marketplace_reservation_sweeper as sweeper


LISTING_ID = 7
STARTING_STOCK = 50

NOW = "2026-08-31T12:30:00+00:00"
LONG_EXPIRED = "2026-08-31T11:00:00+00:00"
EXPIRED = "2026-08-31T12:00:00+00:00"


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    schema.reset_schema_cache()
    yield
    schema.reset_schema_cache()


# --------------------------------------------------------------------------
# Harness — production's pre-bootstrap shape, and nothing more
# --------------------------------------------------------------------------

#: The reservations table exactly as it originally shipped: no reserved_at, no
#: expires_at, no release bookkeeping. Copied literally rather than derived from
#: ``RESERVATION_TABLE_DDL`` so that a future edit to the real DDL cannot
#: silently add a lifecycle column here and hollow out every test below.
LEGACY_RESERVATION_DDL = """
CREATE TABLE marketplace_inventory_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_transaction_id INTEGER UNIQUE,
    buyer_user_id INTEGER,
    listing_id INTEGER,
    quantity INTEGER DEFAULT 1,
    status TEXT DEFAULT 'held',
    created_at TEXT,
    updated_at TEXT
)
"""

#: The candidate projection as it stood at 8a21d1b9, including the fallback.
#: Both spellings name ``expires_at``, which is why the fallback never fell
#: back to anything.
HISTORICAL_CANDIDATE_SQL = (
    "SELECT r.seller_transaction_id, r.listing_id, r.quantity, r.expires_at "
    "FROM marketplace_inventory_reservations r "
    "WHERE r.status = 'held' AND r.expires_at <= '2026-08-31T12:00:00+00:00'"
)


def _legacy_db(*, with_columns=()):
    """A database in the shape production was actually in.

    ``with_columns`` adds individual lifecycle columns back, which is how the
    partial-migration matrix builds each of its cases: a database that got half
    way through a hand-rolled migration, or a role that could add some columns
    and not others.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE marketplace_listings "
                "(id INTEGER PRIMARY KEY, quantity INTEGER, updated_at TEXT)")
    cur.execute(LEGACY_RESERVATION_DDL)
    cur.execute("""CREATE TABLE seller_transactions (
        id INTEGER PRIMARY KEY, buyer_user_id INTEGER, status TEXT,
        stripe_payment_intent_id TEXT, metadata_json TEXT, updated_at TEXT)""")
    for name, definition in schema.RESERVATION_LIFECYCLE_COLUMNS:
        if name in with_columns:
            cur.execute("ALTER TABLE marketplace_inventory_reservations "
                        f"ADD COLUMN {name} {definition}")
    cur.execute("INSERT INTO marketplace_listings VALUES (?, ?, '')",
                (LISTING_ID, STARTING_STOCK))
    return cur


def _legacy_hold(cur, tx_id, *, qty=2, tx_status="checkout_created", intent=None):
    """A held reservation written the way the legacy table allows.

    Only the columns that exist pre-bootstrap are populated. The deadline is
    backfilled by ``_set_expiry`` after the ensure has created the column, which
    is the same order production experiences: rows first, migration second.
    """
    cur.execute(
        "INSERT INTO seller_transactions (id, buyer_user_id, status, stripe_payment_intent_id) "
        "VALUES (?, 1, ?, ?)", (tx_id, tx_status, intent))
    cur.execute(
        "INSERT INTO marketplace_inventory_reservations "
        "(seller_transaction_id, buyer_user_id, listing_id, quantity, status, "
        " created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
        (tx_id, LISTING_ID, qty, policy.STATUS_HELD, LONG_EXPIRED, LONG_EXPIRED))
    cur.execute("UPDATE marketplace_listings SET quantity=quantity-? WHERE id=?",
                (qty, LISTING_ID))
    return tx_id


def _set_expiry(cur, tx_id, value=EXPIRED):
    cur.execute("UPDATE marketplace_inventory_reservations SET expires_at=? "
                "WHERE seller_transaction_id=?", (value, tx_id))


def _columns(cur):
    return {row[1] for row in cur.execute(
        "PRAGMA table_info(marketplace_inventory_reservations)").fetchall()}


def _stock(cur):
    return cur.execute("SELECT quantity FROM marketplace_listings WHERE id=?",
                       (LISTING_ID,)).fetchone()[0]


def _statuses(cur):
    return [r[0] for r in cur.execute(
        "SELECT status FROM marketplace_inventory_reservations "
        "ORDER BY seller_transaction_id").fetchall()]


def _sweep(cur, **kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("dry_run", True)
    return sweeper.run_reservation_expiry_sweep(cur, **kwargs)


class _AlterBlockingCursor:
    """A cursor that refuses one class of statement.

    Models the production failures that are not bugs: a role granted SELECT and
    UPDATE but not ALTER, a table locked by another migration, a replica that
    has not caught up. The worker has to survive all three, and the only honest
    way to test that is to make the statement fail rather than to patch the
    function that issues it.
    """

    def __init__(self, cur, *, blocked=("ALTER",), error=None):
        self._cur = cur
        self._blocked = blocked
        self._error = error or sqlite3.OperationalError("permission denied for relation")
        self.blocked_calls = []

    def execute(self, sql, params=None):
        head = str(sql).strip().upper()
        if any(head.startswith(word) for word in self._blocked):
            self.blocked_calls.append(sql)
            raise self._error
        return self._cur.execute(sql, params) if params is not None else self._cur.execute(sql)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _RecordingCursor:
    """Keeps every statement issued so the generated SQL can be asserted on.

    A wrapper rather than a monkeypatch because ``sqlite3.Cursor.execute`` is a
    read-only C slot. Recording the text is the only way to check the projection
    against the table's real shape — asserting on the helper's return value
    would test the helper, not the query that actually reached the database.
    """

    def __init__(self, cur):
        self._cur = cur
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(str(sql))
        return self._cur.execute(sql, params) if params is not None else self._cur.execute(sql)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def _disable_bootstrap(monkeypatch):
    """Reproduce 8a21d1b9: no schema ensure anywhere in the worker's path.

    Returns ``ready`` so the sweep proceeds exactly as far as the old code did,
    which puts the entire burden of the outcome on the query builder — the layer
    that used to crash.
    """
    monkeypatch.setattr(
        schema, "ensure_reservation_schema",
        lambda cur, **kw: {"status": schema.STATUS_READY, "columns": [],
                           "missing": [], "added": [], "table_created": False,
                           "error": None})


# ==========================================================================
# Stage 3 + 11 — the worker does not require web traffic
# ==========================================================================

def test_01_sweep_bootstraps_its_own_schema_with_no_cart_request():
    """The headline regression. No route was called; the sweep still works."""
    cur = _legacy_db()
    assert "expires_at" not in _columns(cur), "fixture must start pre-bootstrap"

    _legacy_hold(cur, 101)
    result = _sweep(cur)

    assert "expires_at" in _columns(cur)
    assert result["status"] == sweeper.STATUS_OK
    assert result["reason"] is None
    assert result["failed"] == 0
    # Zero candidates here is a real measurement: the column was created empty,
    # so the row has no deadline and is correctly not collectable.
    assert result["candidates"] == 0


def test_02_the_historical_query_really_does_fail_on_this_fixture():
    """Non-vacuity, part one: the pre-bootstrap shape is genuinely broken.

    If something in the import chain were quietly migrating the table, this
    would pass instead of raise and every other test in the file would be
    measuring nothing.
    """
    cur = _legacy_db()
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        cur.execute(HISTORICAL_CANDIDATE_SQL)
    assert "expires_at" in str(excinfo.value)


def test_03_without_the_bootstrap_the_sweep_stops_reporting_success(monkeypatch):
    """Non-vacuity, part two: remove the fix, lose the result.

    This is the same database and the same call as ``test_01``. The only
    difference is that the bootstrap is disabled, and the outcome flips from a
    measurement to a reported migration problem — so ``test_01`` cannot be
    passing for an unrelated reason.
    """
    cur = _legacy_db()
    _disable_bootstrap(monkeypatch)

    result = _sweep(cur)

    assert result["status"] == sweeper.STATUS_DEGRADED
    assert result["reason"] == sweeper.REASON_SCHEMA_MISSING
    assert result["failed"] == 1
    assert "expires_at" in result["schema_missing"]


def test_04_the_generated_sql_never_names_an_absent_column(monkeypatch):
    """Non-vacuity, part three: the fallback's actual defect.

    The old fallback failed with the same UndefinedColumn as the query it was
    rescuing because it also selected ``expires_at``. The replacement builds its
    projection from the table's real shape, so the failure mode is structurally
    impossible — asserted here against a database missing the optional columns.
    """
    cur = _RecordingCursor(_legacy_db(with_columns=("expires_at",)))
    _disable_bootstrap(monkeypatch)

    sweeper.select_expiry_candidates(cur, now=NOW)

    sql = next(s for s in cur.statements
               if "marketplace_inventory_reservations r" in s)
    for absent in ("reconciled_at", "reconcile_deferrals"):
        assert absent not in sql, f"projection named a column the table lacks: {absent}"
    assert "expires_at" in sql


def test_05_bootstrapped_schema_finds_a_real_candidate():
    """The sweep is not merely surviving — it measures.

    A hold with a real deadline is written after the bootstrap created the
    column, which is the shape production reaches once a cart request has run at
    least once. The sweep must count it.
    """
    cur = _legacy_db()
    _legacy_hold(cur, 102, intent=None)
    _sweep(cur)                      # bootstrap creates the columns
    _set_expiry(cur, 102, EXPIRED)   # a deadline now exists to be read

    result = _sweep(cur)

    assert result["candidates"] == 1
    assert result["would_release"] == 1
    assert result["released"] == 0, "dry run must not mutate"
    assert result["failed"] == 0
    assert _stock(cur) == STARTING_STOCK - 2


# ==========================================================================
# Stage 5 — the partial migration matrix
# ==========================================================================

def test_06_case1_all_columns_present_sweeps_normally():
    cur = _legacy_db(with_columns=[name for name, _ in schema.RESERVATION_LIFECYCLE_COLUMNS])
    _legacy_hold(cur, 201)
    _set_expiry(cur, 201)

    result = _sweep(cur)

    assert result["status"] == sweeper.STATUS_OK
    assert result["candidates"] == 1
    assert result["failed"] == 0


def test_07_case2_expires_at_missing_is_reported_not_hidden(monkeypatch):
    cur = _legacy_db(with_columns=("reserved_at", "released_at", "captured_at",
                                   "release_reason", "reconciled_at",
                                   "reconcile_deferrals"))
    _disable_bootstrap(monkeypatch)

    result = _sweep(cur)

    assert result["reason"] == sweeper.REASON_SCHEMA_MISSING
    assert result["schema_missing"] == ["expires_at"]
    assert result["candidates"] == 0 and result["failed"] == 1


def test_08_case3_reconcile_deferrals_missing_degrades_gracefully(monkeypatch):
    """An optional column. Backoff bookkeeping is lost; the sweep still runs."""
    cur = _legacy_db(with_columns=("reserved_at", "expires_at", "released_at",
                                   "captured_at", "release_reason"))
    _legacy_hold(cur, 202)
    _set_expiry(cur, 202)
    _disable_bootstrap(monkeypatch)

    result = _sweep(cur)

    assert result["status"] == sweeper.STATUS_OK
    assert result["reason"] is None
    assert result["candidates"] == 1


def test_09_case4_multiple_required_columns_missing_are_all_listed(monkeypatch):
    cur = _legacy_db()
    cur.execute("DROP TABLE marketplace_inventory_reservations")
    cur.execute("CREATE TABLE marketplace_inventory_reservations "
                "(id INTEGER PRIMARY KEY, seller_transaction_id INTEGER, status TEXT)")
    _disable_bootstrap(monkeypatch)

    result = _sweep(cur)

    assert result["reason"] == sweeper.REASON_SCHEMA_MISSING
    assert set(result["schema_missing"]) == {"expires_at", "listing_id", "quantity"}


def test_10_case5_old_schema_only_reports_migration_required(monkeypatch):
    """Production's exact pre-bootstrap state, with the bootstrap removed."""
    cur = _legacy_db()
    _legacy_hold(cur, 203)
    _disable_bootstrap(monkeypatch)

    result = _sweep(cur)

    assert result["status"] == sweeper.STATUS_DEGRADED
    assert result["reason"] == sweeper.REASON_SCHEMA_MISSING
    assert result["schema_missing"] == ["expires_at"]


def test_11_case6_ensure_adds_only_what_is_missing():
    cur = _legacy_db(with_columns=("reserved_at", "expires_at"))

    state = schema.ensure_reservation_schema(cur, force=True)

    assert state["status"] == schema.STATUS_READY
    assert set(state["added"]) == {"released_at", "captured_at", "release_reason",
                                   "reconciled_at", "reconcile_deferrals"}
    assert "reserved_at" not in state["added"]


def test_12_case7_ensure_failure_is_structured_not_raised():
    cur = _AlterBlockingCursor(_legacy_db(), blocked=("ALTER",))

    state = schema.ensure_reservation_schema(cur, force=True)

    assert state["status"] == schema.STATUS_MISSING
    assert "expires_at" in state["missing"]
    assert state["added"] == []
    assert state["error"] is None


def test_13_case8_fallback_activates_with_a_complete_result_contract(monkeypatch):
    """Degraded is still a full result. The worker parses keys, not log lines."""
    cur = _legacy_db()
    _disable_bootstrap(monkeypatch)

    result = _sweep(cur)

    for key in ("status", "reason", "scanned", "candidates", "released",
                "captured", "deferred", "skipped", "reconciled", "failed",
                "would_release", "would_defer", "would_skip", "provider_calls",
                "needs_attention", "dry_run", "limit", "batch_exhausted",
                "duration_ms"):
        assert key in result, key
    assert result["scanned"] == 0
    assert result["would_release"] == 0


def test_14_case9_query_builder_omits_optional_columns_it_does_not_have():
    cur = _legacy_db(with_columns=("expires_at",))
    present = schema.reservation_columns(cur, refresh=True)

    projection = " ".join(sweeper._candidate_projection(present))

    assert "reconciled_at" not in projection
    assert "reconcile_deferrals" not in projection
    assert "r.expires_at" in projection


def test_15_case10_no_mutation_when_the_schema_is_incomplete(monkeypatch):
    """The safety property. A sweep that cannot look must not touch anything."""
    cur = _legacy_db()
    _legacy_hold(cur, 204)
    _disable_bootstrap(monkeypatch)

    stock_before, statuses_before = _stock(cur), _statuses(cur)
    result = _sweep(cur, dry_run=False)

    assert result["reason"] == sweeper.REASON_SCHEMA_MISSING
    assert result["released"] == 0 and result["captured"] == 0
    assert _stock(cur) == stock_before
    assert _statuses(cur) == statuses_before


# ==========================================================================
# Stage 6 — the worker loop survives a schema failure
# ==========================================================================

def test_16_alter_permission_denied_degrades_without_raising():
    cur = _AlterBlockingCursor(_legacy_db())

    result = _sweep(cur)

    assert result["status"] == sweeper.STATUS_DEGRADED
    assert result["reason"] == sweeper.REASON_SCHEMA_MISSING
    assert result["failed"] == 1
    assert cur.blocked_calls, "the test must actually have blocked an ALTER"


def test_17_introspection_failure_reports_ensure_failed_not_missing():
    """Two different problems. A table that cannot be read is not a table that
    is missing a column, and an operator paging on the second should not be
    handed the first."""
    cur = _AlterBlockingCursor(
        _legacy_db(), blocked=("PRAGMA", "ALTER"),
        error=sqlite3.OperationalError("database is locked"))

    result = _sweep(cur)

    assert result["reason"] == sweeper.REASON_SCHEMA_ENSURE_FAILED
    assert "locked" in result["error"]
    assert result["failed"] == 1


def test_18_a_failed_ensure_retries_on_the_next_interval():
    """The bug this rules out: caching a broken answer for the process lifetime.

    A worker that gave up permanently because one ALTER hit a lock would need a
    restart to recover from a condition that clears by itself in seconds.
    """
    underlying = _legacy_db()
    blocked = _AlterBlockingCursor(underlying)

    first = _sweep(blocked)
    assert first["status"] == sweeper.STATUS_DEGRADED

    second = _sweep(underlying)  # the lock cleared; same process, same cache

    assert second["status"] == sweeper.STATUS_OK
    assert "expires_at" in _columns(underlying)


def test_19_a_stale_ready_cache_cannot_produce_a_wrong_query(monkeypatch):
    """The cache may skip the DDL. It may never skip the gate.

    ``cart._ensure_reservation_lifecycle_columns`` sets the ready flag process
    wide, so a worker could hold a ready cache against a database that has since
    been rebuilt. The column check runs off the live table for exactly this
    reason.
    """
    schema._SCHEMA_READY = True
    cur = _legacy_db()

    result = _sweep(cur)

    assert result["reason"] == sweeper.REASON_SCHEMA_MISSING
    assert schema._SCHEMA_READY is False, "the stale cache must be cleared"


# ==========================================================================
# Stage 7 — two processes may ensure at the same instant
# ==========================================================================

def test_20_ensure_is_idempotent_across_repeated_calls():
    cur = _legacy_db()

    first = schema.ensure_reservation_schema(cur, force=True)
    second = schema.ensure_reservation_schema(cur, force=True)

    assert first["status"] == second["status"] == schema.STATUS_READY
    assert second["added"] == [], "the second pass must add nothing"


def test_21_losing_the_add_column_race_is_not_an_error():
    """Web process and worker ensure simultaneously; one of them loses.

    Simulated by introspecting a table, letting a second writer add the column,
    and then issuing the ALTER the first writer had already planned — which is
    precisely the interleaving that produces a duplicate-column error.
    """
    cur = _legacy_db()
    schema.reset_schema_cache()
    schema.reservation_columns(cur, refresh=True)          # first process looks
    cur.execute("ALTER TABLE marketplace_inventory_reservations "
                "ADD COLUMN expires_at TEXT")               # second process wins

    state = schema.ensure_reservation_schema(cur, force=True)

    assert state["status"] == schema.STATUS_READY
    assert "expires_at" not in state["added"]


def test_22_index_creation_failure_does_not_block_the_sweep():
    """A missing index makes the sweep slow. A missing column makes it wrong."""
    cur = _AlterBlockingCursor(_legacy_db(), blocked=("CREATE INDEX",))

    state = schema.ensure_reservation_schema(cur, force=True)

    assert state["status"] == schema.STATUS_READY
    assert cur.blocked_calls


# ==========================================================================
# Stage 10 — the three failures are distinguishable in the logs
# ==========================================================================

def test_23_ready_and_blocked_emit_distinct_events(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    cur = _legacy_db()
    _sweep(cur)
    ready_events = [r.getMessage() for r in caplog.records]
    assert any("RESERVATION_SCHEMA_READY" in m for m in ready_events)
    assert any("RESERVATION_SWEEP_STARTED" in m for m in ready_events)
    assert not any("RESERVATION_SWEEP_SCHEMA_BLOCKED" in m for m in ready_events)

    caplog.clear()
    schema.reset_schema_cache()
    blocked_cur = _legacy_db()
    _disable_bootstrap(monkeypatch)
    _sweep(blocked_cur)
    blocked_events = [r.getMessage() for r in caplog.records]
    assert any("RESERVATION_SWEEP_SCHEMA_MISSING" in m for m in blocked_events)
    assert not any("RESERVATION_SWEEP_STARTED" in m for m in blocked_events)


def test_24_schema_missing_is_distinguishable_from_a_broken_query(monkeypatch):
    """Both produce failed=1 and zero candidates. Only the reason separates
    "this database needs a migration" from "this query is broken"."""
    cur = _legacy_db(with_columns=[n for n, _ in schema.RESERVATION_LIFECYCLE_COLUMNS])
    monkeypatch.setattr(sweeper, "select_expiry_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    result = _sweep(cur)

    assert result["reason"] == sweeper.REASON_CANDIDATE_QUERY_FAILED
    assert result["reason"] != sweeper.REASON_SCHEMA_MISSING
    assert result["failed"] == 1
    assert "boom" in result["error"]


# ==========================================================================
# Stage 12 — count semantics are unchanged by any of this
# ==========================================================================

def test_25_a_healthy_sweep_still_reports_ok_with_no_reason():
    cur = _legacy_db()
    _legacy_hold(cur, 205)
    _sweep(cur)
    _set_expiry(cur, 205)

    result = _sweep(cur, dry_run=False)

    assert result["status"] == sweeper.STATUS_OK
    assert result["reason"] is None
    assert result["released"] == 1
    assert result["failed"] == 0
    assert _stock(cur) == STARTING_STOCK


def test_26_a_row_level_failure_is_degraded_and_keeps_its_counts(monkeypatch):
    """Partial success stays partial success.

    The sweep did look, so ``scanned`` and ``candidates`` are real numbers and
    must survive. Only ``status`` moves, and it carries no schema reason —
    because the schema was fine.
    """
    cur = _legacy_db()
    _legacy_hold(cur, 206)
    _legacy_hold(cur, 207)
    _sweep(cur)
    _set_expiry(cur, 206)
    _set_expiry(cur, 207)

    calls = {"n": 0}
    real = sweeper._process_candidate

    def flaky(cur_, row, tx_id, result, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("row blew up")
        return real(cur_, row, tx_id, result, **kw)

    monkeypatch.setattr(sweeper, "_process_candidate", flaky)
    result = _sweep(cur, dry_run=False)

    assert result["candidates"] == 2
    assert result["scanned"] == 2
    assert result["failed"] == 1
    assert result["released"] == 1, "the healthy row must still be counted"
    assert result["status"] == sweeper.STATUS_DEGRADED
    assert result["reason"] is None, "a row failure is not a schema failure"
