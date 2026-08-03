"""A savepoint that the layer beneath it destroys is not a savepoint.

## The defect

`CompatCursor.execute` in `services/db.py` caught every SQL error and called
`self._owner.rollback()` before re-raising. That rollback exists for a real
reason: on Postgres a failed statement poisons the whole transaction, and every
later statement in it fails with "current transaction is aborted" until
something unwinds it. Rolling back automatically meant a caller who swallowed
one error did not then meet a wall of unrelated ones.

But `rollback()` is the whole transaction. It discards the caller's savepoint
too. So code written in exactly the shape the manual prescribes —

    SAVEPOINT s
    INSERT ...            <- expected to fail on a UNIQUE violation
    ROLLBACK TO SAVEPOINT s

never reached its third line successfully. By then the savepoint was gone, and
`ROLLBACK TO SAVEPOINT` failed with SQLSTATE 3B001, "no such savepoint". The
caller asked to recover from a duplicate key and got an opaque error about a
savepoint it could see itself creating.

Two callers are written this way: `ledger.py::_ensure_balance_row`, which
races two posters bootstrapping the same new account, and
`push_service.py::enqueue_push_with_cursor`, which enqueues inside a caller's
transaction without opening a second writer.

The consequence was not a double-post — it failed safe — but the recovery branch
never ran, and worse, everything the transaction had done *before* the savepoint
was silently discarded along with it. A guard that fails safe by throwing away
committed-in-progress work is not the guard anyone wrote.

## What these tests do about it

The fix teaches `CompatConnection` which savepoints are open, by reading the
statements that pass through it, and skips the blanket rollback whenever there
is a savepoint the caller could still roll back to. The recovery then belongs to
the caller who opened the savepoint, which is the only layer that knows what it
was protecting.

These tests drive `CompatConnection` directly over an in-memory SQLite
connection. That is not a workaround for Postgres being unavailable — it is the
right level. The bug is in the wrapper's control flow, not in any engine's
behaviour, and SQLite reproduces it exactly: the old code's `rollback()` ends the
transaction and discards the savepoint on SQLite for the same reason it does on
Postgres.

Executable two ways:

    python -m pytest tests/business_os/test_savepoint_recovery.py
    python tests/business_os/test_savepoint_recovery.py
"""

import ast
import os
import sqlite3
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_savepoint_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _conn():
    """A CompatConnection over in-memory SQLite, in explicit-transaction mode.

    `isolation_level=None` turns off the sqlite3 driver's implicit BEGIN, which
    otherwise wraps statements in transactions of its own choosing and makes
    savepoint nesting unreadable. With it off, every BEGIN/SAVEPOINT in these
    tests is one this file wrote.
    """
    raw = sqlite3.connect(":memory:", isolation_level=None)
    conn = db.CompatConnection(raw)
    conn.execute("CREATE TABLE t (k TEXT PRIMARY KEY, v INTEGER)")
    return conn


def _rows(conn):
    cur = conn.execute("SELECT k, v FROM t ORDER BY k")
    return [(r[0], r[1]) for r in (cur.fetchall() or [])]


def _eq(actual, expected, note=""):
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}{(' — ' + note) if note else ''}")


# --------------------------------------------------------------------------
# the defect itself
# --------------------------------------------------------------------------

def test_a_savepoint_survives_a_failed_statement_inside_it():
    """The headline. Fail inside a savepoint, then roll back to it.

    Against the old code the failed INSERT triggered a full `rollback()`, the
    savepoint went with the transaction, and this `ROLLBACK TO SAVEPOINT` raised
    "no such savepoint" — the SQLSTATE 3B001 the review named.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO t (k, v) VALUES ('a', 1)")
    conn.execute("SAVEPOINT sp")
    try:
        conn.execute("INSERT INTO t (k, v) VALUES ('a', 2)")
        raise AssertionError("the duplicate key should have failed")
    except AssertionError:
        raise
    except Exception:
        pass
    # The line that used to die.
    conn.execute("ROLLBACK TO SAVEPOINT sp")
    conn.execute("RELEASE SAVEPOINT sp")
    conn.execute("COMMIT")
    _eq(_rows(conn), [("a", 1)])


def test_work_done_before_the_savepoint_is_not_discarded():
    """The quieter half of the same defect, and the more expensive one.

    An error raised is visible. Rows that were supposed to be in the transaction
    and are simply gone are not. The old blanket rollback threw away everything
    written before the savepoint, so a caller that caught the duplicate-key
    error and carried on committed a transaction missing its earlier work.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO t (k, v) VALUES ('before', 10)")
    conn.execute("SAVEPOINT sp")
    try:
        conn.execute("INSERT INTO t (k, v) VALUES ('before', 99)")
    except Exception:
        pass
    conn.execute("ROLLBACK TO SAVEPOINT sp")
    conn.execute("RELEASE SAVEPOINT sp")
    conn.execute("INSERT INTO t (k, v) VALUES ('after', 20)")
    conn.execute("COMMIT")
    _eq(_rows(conn), [("after", 20), ("before", 10)],
        "the row written before the savepoint was rolled back with it")


def test_the_failure_still_reaches_the_caller():
    """Skipping the rollback must not skip the raise.

    The point of the fix is where recovery happens, not whether the caller is
    told. A silent failure would be a far worse defect than the one being fixed.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO t (k, v) VALUES ('a', 1)")
    conn.execute("SAVEPOINT sp")
    raised = False
    try:
        conn.execute("INSERT INTO t (k, v) VALUES ('a', 2)")
    except Exception:
        raised = True
    _eq(raised, True, "the duplicate key did not propagate")
    conn.execute("ROLLBACK TO SAVEPOINT sp")


# --------------------------------------------------------------------------
# the blanket rollback is still there when nothing is protecting the caller
# --------------------------------------------------------------------------

def test_without_a_savepoint_the_transaction_is_still_rolled_back():
    """The behaviour being narrowed, not removed.

    With no savepoint open there is nothing finer-grained to unwind to, and the
    Postgres poisoning problem is real. The rollback must still fire.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO t (k, v) VALUES ('a', 1)")
    try:
        conn.execute("INSERT INTO t (k, v) VALUES ('a', 2)")
    except Exception:
        pass
    # Rolled back by the wrapper: the transaction is over and 'a' never landed.
    _eq(conn.has_savepoint(), False)
    _eq(_rows(conn), [], "the un-savepointed failure should have rolled back")


def test_a_released_savepoint_no_longer_defers_the_rollback():
    """RELEASE ends the protection, and the wrapper has to notice.

    A stack that only ever grows would suppress the rollback forever after the
    first savepoint in a connection's life, which is the opposite failure and
    just as bad.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT sp")
    conn.execute("INSERT INTO t (k, v) VALUES ('a', 1)")
    conn.execute("RELEASE SAVEPOINT sp")
    _eq(conn.has_savepoint(), False)
    try:
        conn.execute("INSERT INTO t (k, v) VALUES ('a', 2)")
    except Exception:
        pass
    _eq(_rows(conn), [], "the released savepoint should not have deferred the rollback")


def test_commit_clears_the_savepoint_stack():
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT sp")
    conn.execute("COMMIT")
    _eq(conn.has_savepoint(), False, "COMMIT ends every savepoint in the transaction")


def test_rollback_clears_the_savepoint_stack():
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT sp")
    conn.rollback()
    _eq(conn.has_savepoint(), False)


# --------------------------------------------------------------------------
# stack bookkeeping
# --------------------------------------------------------------------------

def test_nested_savepoints_unwind_one_at_a_time():
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT outer_sp")
    conn.execute("SAVEPOINT inner_sp")
    _eq(conn.has_savepoint(), True)
    conn.execute("RELEASE SAVEPOINT inner_sp")
    _eq(conn.has_savepoint(), True, "the outer savepoint is still open")
    conn.execute("RELEASE SAVEPOINT outer_sp")
    _eq(conn.has_savepoint(), False)


def test_releasing_an_outer_savepoint_releases_the_inner_ones_too():
    """SQL semantics, not a convenience.

    RELEASE of an outer savepoint destroys every savepoint opened after it. A
    stack that popped only the named entry would keep believing an inner
    savepoint was available and go on suppressing rollbacks that should fire.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT outer_sp")
    conn.execute("SAVEPOINT inner_sp")
    conn.execute("RELEASE SAVEPOINT outer_sp")
    _eq(conn.has_savepoint(), False)


def test_rolling_back_to_a_savepoint_keeps_it_open():
    """ROLLBACK TO does not release. The savepoint may be rolled back to again.

    `ledger.py::_ensure_balance_row` depends on this: it issues ROLLBACK
    TO and then RELEASE as separate statements, and the RELEASE would fail if
    the first had destroyed the savepoint.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT sp")
    conn.execute("INSERT INTO t (k, v) VALUES ('a', 1)")
    conn.execute("ROLLBACK TO SAVEPOINT sp")
    _eq(conn.has_savepoint(), True, "ROLLBACK TO must not pop the savepoint")
    conn.execute("RELEASE SAVEPOINT sp")
    _eq(conn.has_savepoint(), False)


def test_rolling_back_to_an_outer_savepoint_drops_the_inner_ones():
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT outer_sp")
    conn.execute("SAVEPOINT inner_sp")
    conn.execute("ROLLBACK TO SAVEPOINT outer_sp")
    _eq(conn._savepoints, ["outer_sp"],
        "inner savepoints do not survive a rollback past them")


def test_a_reused_savepoint_name_unwinds_to_the_most_recent():
    """Re-using a name is legal SQL and the newer one shadows the older.

    Matching from the front of the stack instead of the back would have released
    the wrong one and left a phantom entry behind.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT sp")
    conn.execute("SAVEPOINT sp")
    conn.execute("RELEASE SAVEPOINT sp")
    _eq(conn._savepoints, ["sp"], "only the inner one should have been released")


def test_a_failed_savepoint_statement_is_not_recorded():
    """The stack tracks what the database holds, not what was attempted."""
    conn = _conn()
    conn.execute("BEGIN")
    try:
        conn.execute("SAVEPOINT")  # syntactically invalid
    except Exception:
        pass
    _eq(conn.has_savepoint(), False)


def test_a_failed_rollback_to_savepoint_falls_back_to_a_full_rollback():
    """When the recovery itself fails there is nothing finer left to try.

    Continuing to defer would leave a poisoned Postgres transaction with no
    unwind path at all — every later statement failing, and the wrapper politely
    declining to do the one thing that would help.
    """
    conn = _conn()
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT sp")
    try:
        conn.execute("ROLLBACK TO SAVEPOINT no_such_sp")
    except Exception:
        pass
    _eq(conn.has_savepoint(), False,
        "a failed ROLLBACK TO must clear the stack so the full rollback runs")


# --------------------------------------------------------------------------
# statement classification
# --------------------------------------------------------------------------

def test_rollback_to_savepoint_is_not_read_as_a_plain_rollback():
    """The two statements start with the same word and mean opposite things.

    "ROLLBACK TO SAVEPOINT x" narrows the unwind; "ROLLBACK" ends the
    transaction. Classifying the first as the second would clear the whole stack
    on the exact statement that proves a savepoint is in use.
    """
    _eq(db._savepoint_op("ROLLBACK TO SAVEPOINT sp"), ("rollback_to", "sp"))
    _eq(db._savepoint_op("ROLLBACK TO sp"), ("rollback_to", "sp"))
    _eq(db._savepoint_op("  rollback work to savepoint SP  "), ("rollback_to", "sp"))
    _eq(db._savepoint_op("ROLLBACK"), ("txn_end", ""))
    _eq(db._savepoint_op("COMMIT;"), ("txn_end", ""))


def test_savepoint_names_are_matched_case_insensitively():
    """SQL identifiers here are unquoted, so `SP` and `sp` are one savepoint."""
    _eq(db._savepoint_op("savepoint MySP"), ("savepoint", "mysp"))
    _eq(db._savepoint_op("RELEASE SAVEPOINT mysp"), ("release", "mysp"))
    _eq(db._savepoint_op("RELEASE mysp"), ("release", "mysp"))


def test_ordinary_statements_are_not_mistaken_for_savepoint_control():
    """A column called `savepoint_id` must not push anything onto the stack."""
    for sql in (
        "SELECT savepoint_name FROM t",
        "INSERT INTO t (k, v) VALUES ('savepoint', 1)",
        "UPDATE t SET v = 1 WHERE k = 'release savepoint sp'",
        "DELETE FROM t",
    ):
        _eq(db._savepoint_op(sql), ("", ""), f"misclassified: {sql}")


# --------------------------------------------------------------------------
# the callers this was written for
# --------------------------------------------------------------------------

def _fn_source(path, name):
    with open(os.path.join(os.path.dirname(__file__), "..", "..", path), encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found in {path}")


def test_the_ledger_bootstrap_still_uses_the_savepoint_shape():
    """Pin the caller, not just the wrapper.

    The fix is only worth anything while a caller depends on it. If the ledger's
    bootstrap were rewritten without savepoints this test should fail loudly and
    force someone to decide whether `db.py` still needs the special case, rather
    than leaving dead machinery behind.
    """
    src = _fn_source("services/business_os/ledger/ledger.py", "_ensure_balance_row")
    for stmt in ("SAVEPOINT ledger_balance_bootstrap",
                 "ROLLBACK TO SAVEPOINT ledger_balance_bootstrap",
                 "RELEASE SAVEPOINT ledger_balance_bootstrap"):
        if stmt not in src:
            raise AssertionError(f"missing {stmt!r} in _ensure_balance_row")


def test_the_wrapper_checks_for_a_savepoint_before_rolling_back():
    """Source-level, because the SQLite tests above cannot see the ordering.

    The check has to happen *before* the rollback call, not after it. A version
    that rolled back and then consulted the stack would pass every behavioural
    test on an engine that tolerates the extra rollback, and lose the savepoint
    on the one that does not.
    """
    src = _fn_source("services/db.py", "execute")
    if "has_savepoint()" not in src:
        raise AssertionError("CompatCursor.execute does not consult the savepoint stack")
    guard = src.index("has_savepoint()")
    rollback = src.index("self._owner.rollback()")
    if guard > rollback:
        raise AssertionError("the savepoint check runs after the rollback it is meant to prevent")


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
