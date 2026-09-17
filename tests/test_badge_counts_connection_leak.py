"""The badge readers must return their connection even when a query raises.

## The defect

`notification_service.pulse_badge_counts` and
`pulsesoc_notification_system.badge_counts` both opened a pooled connection at
the top and called `conn.close()` on the last line, with no `try/finally`
between them. Every `cur.execute` in between can raise -- a missing column is
the realistic case, since `pulse_badge_counts` reads `target_url`,
`conversation_type` and `membership_state` across four optional tables that are
probed with `_table_exists` rather than guaranteed by a migration.

What makes it expensive is where it is called from. `pulse_badge_counts` runs on
the push delivery path at `services/notification_service.py:1232`, once per
outbound notification -- and the call site is wrapped in a bare
`except Exception:` that falls back to `metadata["badge"]`. So the failure is
swallowed: the push still goes out, the caller sees a plausible badge, and the
only trace is one connection that never comes back. `badge_counts` is the same
shape on the central pipeline's `_push_payload`. The pool is 8 + 8 with a three
second timeout, so a burst of pushes against a drifted schema exhausts it and
the requests that start failing are other people's.

## Why these tests count connections instead of checking the counts

A leak is invisible to a behaviour test. Both functions returned correct numbers
the entire time the pool was draining, and on the failure path they do not
return at all -- there is no value to assert on. The only observable is the
ledger: connections handed out versus connections closed. So the factory is
wrapped, an error is forced, and the assertion is `closed == opened`.

The forced error walks every `execute` the function issues, not just the first.
A `try` that covers only the leading query would satisfy a single-error test
while still leaking on the optional blocks further down.

    python -m pytest tests/test_badge_counts_connection_leak.py
"""

import os
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import notification_service as ns
from services import pulsesoc_notification_system as pns
from services import user_context


# A missing column is the realistic trigger, so the forced failure is the error
# SQLite actually raises for one rather than a bare RuntimeError.
class MissingColumn(sqlite3.OperationalError):
    pass


class LedgerCursor:
    """Answers every badge query, and raises on the Nth execute when asked."""

    def __init__(self, raise_at=None):
        self.raise_at = raise_at
        self.executes = 0
        self._row = None

    def execute(self, sql, params=()):
        self.executes += 1
        if self.raise_at == self.executes:
            raise MissingColumn("no such column: target_url")
        flat = " ".join(str(sql).split())
        if "information_schema.tables" in flat or "sqlite_master" in flat:
            # Say every optional table exists, so the function takes its longest
            # path and the sweep below reaches every execute site it owns.
            self._row = (1,)
        elif "conversation_type" in flat:
            self._row = (3, 2)
        else:
            self._row = (1,)

    def fetchone(self):
        return self._row


class LedgerConnection:
    """One connection, counting its own closes."""

    def __init__(self, raise_at=None):
        self.cur = LedgerCursor(raise_at=raise_at)
        self.closed = 0

    def cursor(self):
        return self.cur

    def close(self):
        self.closed += 1


class PulseBadgeCountsConnectionTests(unittest.TestCase):
    def test_a_successful_read_closes_its_connection_once(self):
        """Positive control for the ledger, and for double-close.

        Without this, a `finally` that runs after an explicit `close()` would
        read as a pass on every leak test below while closing twice.
        """
        conn = LedgerConnection()
        with patch.object(user_context, "connect", return_value=conn) as factory:
            counts = ns.pulse_badge_counts(7)

        self.assertEqual(factory.call_count, 1)
        self.assertEqual(conn.closed, 1)
        self.assertTrue(counts["ok"])
        # Proves the fixture drove the long path rather than short-circuiting
        # past the optional blocks the sweep below depends on.
        self.assertGreaterEqual(conn.cur.executes, 6)

    def test_an_error_at_any_query_still_returns_the_connection(self):
        """The sweep: fail each execute in turn, demand the close every time."""
        total = self._execute_count()
        self.assertGreaterEqual(total, 6)

        for index in range(1, total + 1):
            with self.subTest(execute=index):
                conn = LedgerConnection(raise_at=index)
                with patch.object(user_context, "connect", return_value=conn):
                    with self.assertRaises(MissingColumn):
                        ns.pulse_badge_counts(7)
                self.assertEqual(
                    conn.closed,
                    1,
                    f"connection leaked when execute #{index} raised",
                )

    def test_the_push_path_swallows_the_error_that_leaks(self):
        """Why the leak is silent rather than loud.

        `_deliver_pulse_notification` catches the failure and substitutes
        `metadata['badge']`, so a drifted schema produces a plausible push and no
        log line. Pinning the call site here keeps the justification for the
        `finally` from drifting away from the code that needs it.
        """
        import ast
        import inspect

        source = inspect.getsource(ns)
        tree = ast.parse(source)
        guarded = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            for handler in node.handlers
            if "pulse_badge_counts" in ast.unparse(node.body)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == "Exception"
        ]
        self.assertTrue(
            guarded,
            "expected the push path to still swallow pulse_badge_counts errors",
        )

    def _execute_count(self):
        conn = LedgerConnection()
        with patch.object(user_context, "connect", return_value=conn):
            ns.pulse_badge_counts(7)
        return conn.cur.executes


class NotificationSystemBadgeCountsConnectionTests(unittest.TestCase):
    def test_a_successful_read_closes_its_connection_once(self):
        conn = LedgerConnection()
        with patch.object(pns.db_service, "connect", return_value=conn) as factory, patch.object(
            pns, "ensure_schema"
        ):
            counts = pns.badge_counts(7)

        self.assertEqual(factory.call_count, 1)
        self.assertEqual(conn.closed, 1)
        self.assertTrue(counts["ok"])

    def test_a_failing_count_still_returns_the_connection(self):
        conn = LedgerConnection(raise_at=1)
        with patch.object(pns.db_service, "connect", return_value=conn), patch.object(
            pns, "ensure_schema"
        ):
            with self.assertRaises(MissingColumn):
                pns.badge_counts(7)

        self.assertEqual(conn.closed, 1)

    def test_a_failing_ensure_schema_still_returns_the_connection(self):
        """`ensure_schema` runs before the first cursor, and can raise on its own.

        It issues DDL against the connection it is handed, so on Postgres it is
        the likeliest thing in this function to fail -- and it fails before any
        query the caller would recognise as theirs.
        """
        conn = LedgerConnection()
        with patch.object(pns.db_service, "connect", return_value=conn), patch.object(
            pns, "ensure_schema", side_effect=MissingColumn("no such column: status")
        ):
            with self.assertRaises(MissingColumn):
                pns.badge_counts(7)

        self.assertEqual(conn.closed, 1)


if __name__ == "__main__":
    unittest.main()
