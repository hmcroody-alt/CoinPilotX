"""`delivered_at_map` must fail quietly without taking the caller down with it.

## Why this file exists

Production, right now, is in exactly the state this file tests. On
2026-09-17 the marketplace return-window change shipped, and an introspection
of the production database found:

    business_os_mkt_orders.delivered_at             PRESENT
    marketplace_commercial_settlements.delivered_at ABSENT

The second column is added lazily, by `_ensure_transfer_group_column`, the
first time this module writes a settlement. Until a settlement is written the
column does not exist — but `delivered_at_map` is already being called on a
hot path, `/api/pulse/orders`, for every buyer who opens their orders list.

So the query fails, today, on every such request. That is intended: the helper
is documented to treat an absent table or column as "no delivery recorded" and
fall back to the purchase date, which still yields a bounded deadline. What
must not happen is the *other* failure — the caller's transaction being
destroyed on the way out.

## The two engines, and what each half of this file proves

`services/db.py::connect` returns two different things:

  * Postgres — a `CompatConnection`, whose `CompatCursor` rewrites `?` to
    `%s` and wraps rows as `CompatRow` (a `Mapping`, so `dict(row)` works).
  * SQLite — a raw `sqlite3` connection with `row_factory = sqlite3.Row`
    (native `?`, and `dict(row)` works there too).

Only Postgres poisons a whole transaction when one statement fails, and there
is no local Postgres to test against. So the halves are:

  * **Part 1** drives the helper with a cursor that models psycopg2's abort
    semantics: after a failed statement every later one raises until a
    `ROLLBACK TO SAVEPOINT` unwinds it. This is a model, not the real driver —
    but it is the documented contract, and it is what makes the SAVEPOINT in
    `delivered_at_map` load-bearing rather than decorative. The positive
    control `test_the_model_really_does_poison_without_a_rollback` proves the
    model can fail, so the tests around it are not vacuous.

  * **Part 2** drives the helper against real SQLite, exactly as the rest of
    the test suite reaches it, and proves the missing-column and missing-table
    cases return `{}` rather than raising on the engine the suite actually
    runs on.

Deliberately *not* tested here: `CompatConnection` over SQLite. That hybrid
exists nowhere in production and cannot carry `?` parameters, because the
Postgres translation runs unconditionally inside `CompatCursor`.

Executable two ways:

    python -m pytest tests/business_os/test_delivered_at_map_transaction_safety.py
    python tests/business_os/test_delivered_at_map_transaction_safety.py
"""

import os
import sqlite3
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="mkt_delivered_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import marketplace_settlement_service as settlements  # noqa: E402


def _eq(actual, expected, note=""):
    if actual != expected:
        raise AssertionError(
            f"expected {expected!r}, got {actual!r}{(' — ' + note) if note else ''}")


# ==========================================================================
# Part 1 — the Postgres contract, modelled
# ==========================================================================

class AbortedTransaction(Exception):
    """Stands in for psycopg2's InFailedSqlTransaction (SQLSTATE 25P02)."""


class MissingColumn(Exception):
    """Stands in for psycopg2's UndefinedColumn (SQLSTATE 42703)."""


class FakePostgresCursor:
    """A cursor with Postgres's all-or-nothing transaction behaviour.

    Only the rules that decide this code's correctness are modelled:

      * a statement matching `fail_on` raises, once, and poisons the cursor;
      * while poisoned, every statement raises `AbortedTransaction` except
        `ROLLBACK TO SAVEPOINT` (which un-poisons) and `RELEASE` (which is
        tolerated afterwards);
      * `SAVEPOINT` / `RELEASE` / `ROLLBACK TO` maintain a name stack, so a
        `ROLLBACK TO` naming a savepoint that was never opened is an error
        rather than a silent no-op.

    That last rule matters: it is what would catch the helper rolling back to
    a savepoint it forgot to open, which on the real driver is SQLSTATE 3B001.
    """

    def __init__(self, fail_on="FROM marketplace_commercial_settlements", rows=None):
        self.fail_on = fail_on
        self.rows = rows or []
        self.statements = []
        self.poisoned = False
        self.savepoints = []
        self._result = []

    def execute(self, sql, params=None):
        text = str(sql)
        self.statements.append(text)
        upper = text.strip().upper()

        if upper.startswith("ROLLBACK TO SAVEPOINT") or upper.startswith("ROLLBACK TO "):
            name = text.split()[-1]
            if name not in self.savepoints:
                raise Exception(f"3B001: no such savepoint: {name}")
            del self.savepoints[self.savepoints.index(name) + 1:]
            self.poisoned = False
            return self
        if upper.startswith("RELEASE"):
            name = text.split()[-1]
            if name not in self.savepoints:
                raise Exception(f"3B001: no such savepoint: {name}")
            del self.savepoints[self.savepoints.index(name):]
            return self

        if self.poisoned:
            raise AbortedTransaction(
                "current transaction is aborted, commands ignored until end of "
                "transaction block")

        if upper.startswith("SAVEPOINT"):
            self.savepoints.append(text.split()[-1])
            return self

        if self.fail_on and self.fail_on in text:
            self.poisoned = True
            raise MissingColumn('column "delivered_at" does not exist')

        self._result = list(self.rows)
        return self

    def fetchall(self):
        return self._result


def test_a_poisoned_transaction_is_unwound_and_the_caller_gets_no_deliveries():
    """The headline for Postgres: the helper cleans up after itself."""
    cur = FakePostgresCursor()
    _eq(settlements.delivered_at_map(cur, [7, 8]), {})
    if cur.poisoned:
        raise AssertionError("the helper left the transaction aborted")


def test_the_caller_can_keep_using_the_transaction_afterwards():
    """What the route does next — read more rows, or INSERT the return."""
    cur = FakePostgresCursor()
    settlements.delivered_at_map(cur, [7])
    # Would raise AbortedTransaction if the savepoint had not been unwound.
    cur.execute("INSERT INTO marketplace_returns (id) VALUES (1)")


def test_the_helper_opens_a_savepoint_before_the_query_it_expects_to_fail():
    """Order matters. A savepoint opened after the failure is useless."""
    cur = FakePostgresCursor()
    settlements.delivered_at_map(cur, [7])
    kinds = [s.strip().upper().split()[0] for s in cur.statements]
    if "SAVEPOINT" not in kinds:
        raise AssertionError(f"no savepoint was opened: {cur.statements!r}")
    if kinds.index("SAVEPOINT") > kinds.index("SELECT"):
        raise AssertionError("the savepoint is opened after the query it protects")


def test_the_savepoint_stack_is_left_empty():
    """A leaked savepoint changes how every later error in the request behaves."""
    cur = FakePostgresCursor()
    settlements.delivered_at_map(cur, [7])
    _eq(cur.savepoints, [], "savepoint left open")


def test_the_savepoint_is_released_on_the_success_path_too():
    """Not just on failure — the happy path must not leak one either."""
    cur = FakePostgresCursor(
        fail_on=None,
        rows=[{"seller_transaction_id": 7, "delivered_at": "2026-09-10T00:00:00.000000Z"}])
    _eq(settlements.delivered_at_map(cur, [7]), {7: "2026-09-10T00:00:00.000000Z"})
    _eq(cur.savepoints, [], "savepoint left open on the success path")


def test_the_model_really_does_poison_without_a_rollback():
    """Positive control. If this passes cleanly, Part 1 proves nothing.

    Same cursor, same failing statement, but nobody unwinds it — the next
    statement must be refused.
    """
    cur = FakePostgresCursor()
    cur.execute("SAVEPOINT sp")
    try:
        cur.execute("SELECT delivered_at FROM marketplace_commercial_settlements")
        raise AssertionError("the modelled query should have failed")
    except MissingColumn:
        pass
    try:
        cur.execute("SELECT 1")
        raise AssertionError(
            "the model did not poison the transaction, so it cannot detect a "
            "missing rollback and every test above is vacuous")
    except AbortedTransaction:
        pass


def test_a_rollback_to_a_savepoint_that_was_never_opened_is_an_error():
    """Second control: the model can also catch the 3B001 failure mode."""
    cur = FakePostgresCursor()
    try:
        cur.execute("ROLLBACK TO SAVEPOINT never_opened")
        raise AssertionError("the model accepted a rollback to a missing savepoint")
    except AssertionError:
        raise
    except Exception:
        pass


# ==========================================================================
# Part 2 — real SQLite, the engine the suite runs on
# ==========================================================================

_WITHOUT_COLUMN = """
    CREATE TABLE marketplace_commercial_settlements (
        id INTEGER PRIMARY KEY,
        seller_transaction_id INTEGER
    )
"""

_WITH_COLUMN = """
    CREATE TABLE marketplace_commercial_settlements (
        id INTEGER PRIMARY KEY,
        seller_transaction_id INTEGER,
        delivered_at TEXT
    )
"""


def _sqlite(ddl=_WITHOUT_COLUMN):
    """What `db.connect()` hands back on SQLite: raw driver, Row factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if ddl:
        conn.execute(ddl)
    return conn


class RecordingCursor:
    """A pass-through proxy that logs statements.

    A proxy rather than a monkeypatch because `sqlite3.Cursor.execute` is a
    read-only attribute — assigning to it raises, which is how this class came
    to exist.
    """

    def __init__(self, cursor):
        self._cursor = cursor
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(str(sql))
        if params is None:
            return self._cursor.execute(sql)
        return self._cursor.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    @property
    def selects(self):
        return [s for s in self.statements if "SELECT" in s.upper()]


def test_sqlite_missing_column_yields_no_deliveries_rather_than_raising():
    """Production's column state, on the engine the tests actually use."""
    conn = _sqlite(_WITHOUT_COLUMN)
    _eq(settlements.delivered_at_map(conn.cursor(), [1, 2, 3]), {})


def test_sqlite_missing_table_yields_no_deliveries_rather_than_raising():
    """A fresh deployment, before this module has ever written a settlement."""
    conn = _sqlite(ddl=None)
    _eq(settlements.delivered_at_map(conn.cursor(), [7]), {})


def test_sqlite_returns_the_deliveries_once_the_column_exists():
    """The control that stops `return {}` satisfying this whole file."""
    conn = _sqlite(_WITH_COLUMN)
    conn.execute(
        "INSERT INTO marketplace_commercial_settlements "
        "(id, seller_transaction_id, delivered_at) VALUES (1, 7, '2026-09-10T00:00:00.000000Z')")
    conn.execute(
        "INSERT INTO marketplace_commercial_settlements "
        "(id, seller_transaction_id, delivered_at) VALUES (2, 8, NULL)")
    _eq(settlements.delivered_at_map(conn.cursor(), [7, 8, 9]),
        {7: "2026-09-10T00:00:00.000000Z"},
        "8 has no delivery date and 9 has no settlement; neither is a delivery")


def test_sqlite_lookup_is_one_statement_for_many_ids():
    """The N+1 guard at the helper, not just at the route."""
    conn = _sqlite(_WITH_COLUMN)
    for i in range(1, 26):
        conn.execute(
            "INSERT INTO marketplace_commercial_settlements "
            "(id, seller_transaction_id, delivered_at) VALUES (?, ?, ?)",
            (i, i, "2026-09-10T00:00:00.000000Z"))

    cur = RecordingCursor(conn.cursor())
    found = settlements.delivered_at_map(cur, list(range(1, 26)))

    _eq(len(found), 25)
    _eq(len(cur.selects), 1,
        f"expected one batched SELECT, got {len(cur.selects)}")


def test_sqlite_garbage_ids_are_dropped_without_touching_the_database():
    """No usable ids means no statement at all — not a savepoint round trip."""
    conn = _sqlite(_WITHOUT_COLUMN)
    cur = RecordingCursor(conn.cursor())

    _eq(settlements.delivered_at_map(cur, []), {})
    _eq(settlements.delivered_at_map(cur, None), {})
    _eq(settlements.delivered_at_map(cur, ["not-a-number", None, 0]), {})
    _eq(cur.statements, [], f"expected no SQL at all, got {cur.statements!r}")


def test_sqlite_a_string_id_that_is_a_number_still_matches():
    """Ids arrive from JSON and from row objects; both must find the same row."""
    conn = _sqlite(_WITH_COLUMN)
    conn.execute(
        "INSERT INTO marketplace_commercial_settlements "
        "(id, seller_transaction_id, delivered_at) VALUES (1, 7, '2026-09-10T00:00:00.000000Z')")
    _eq(settlements.delivered_at_map(conn.cursor(), ["7"]),
        {7: "2026-09-10T00:00:00.000000Z"})


# --------------------------------------------------------------------------

def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {fn.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
