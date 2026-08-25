"""The presence schema DDL must not run on every request.

This is a regression test for a production incident, so it tests the property
that caused the incident rather than the shape of the code.

Every presence heartbeat called `ensure_schema`, whose docstring said it was
"safe to call on every request". On SQLite it was. On PostgreSQL
`CREATE INDEX IF NOT EXISTS` takes a ShareLock on `presence_sessions`, which
conflicts with the RowExclusiveLock taken by the `UPDATE ... SET
last_heartbeat_at` that runs immediately after it. Two gunicorn workers
interleaving (UPDATE, DDL) against (DDL, UPDATE) is a lock cycle:

    DeadlockDetected: UPDATE presence_sessions SET last_heartbeat_at=...
    Process A waits for RowExclusiveLock on relation 219680; blocked by B.
    Process B waits for RowExclusiveLock on relation 219680; blocked by A.

Postgres kills one side; the losing thread strands its pooled connection in a
failed transaction. gunicorn's gthread `--timeout` watches the worker heartbeat
and not the request, so the thread is never recycled. At `--workers 2`, half of
production stopped answering.

`_SCHEMA_READY` was already there and already assigned — it was simply never
read. So the test that matters is "how many times does the DDL actually run",
which is what would have caught this and what will catch it coming back.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import presence_service as ps  # noqa: E402


class _CountingCursor:
    """A real cursor that records the DDL issued through it."""

    def __init__(self, cur):
        self._cur = cur
        self.ddl = []

    def execute(self, sql, params=()):
        stripped = sql.strip().upper()
        if stripped.startswith("CREATE"):
            self.ddl.append(" ".join(sql.split())[:60])
        return self._cur.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class PresenceSchemaDdlTest(unittest.TestCase):
    def setUp(self):
        ps.reset_schema_cache()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = _CountingCursor(self.conn.cursor())

    def tearDown(self):
        ps.reset_schema_cache()
        self.conn.close()

    def test_first_call_creates_the_schema(self):
        self.assertTrue(ps.ensure_schema(self.cur))
        self.assertTrue(self.cur.ddl, "expected the first call to issue the DDL")

    def test_repeat_calls_issue_no_further_ddl(self):
        ps.ensure_schema(self.cur)
        issued = len(self.cur.ddl)

        for _ in range(50):
            ps.ensure_schema(self.cur)

        # 50 heartbeats, zero extra DDL. Before the guard this was 50x the DDL,
        # each one taking a ShareLock that the next UPDATE had to queue behind.
        self.assertEqual(len(self.cur.ddl), issued)

    def test_heartbeat_path_issues_no_ddl_after_warmup(self):
        # The actual incident path: connect once, then heartbeat repeatedly.
        ps.ensure_schema(self.cur)
        res = ps.connect(self.cur, 10, device_id="phone", device_label="iPhone")
        session_id = res["session_id"]
        self.cur.ddl.clear()

        for _ in range(25):
            beat = ps.heartbeat(self.cur, 10, session_id)
            self.assertTrue(beat["ok"])

        self.assertEqual(
            self.cur.ddl,
            [],
            "a heartbeat must never issue DDL; that is what deadlocked production",
        )

    def test_a_failed_creation_is_retried_rather_than_cached(self):
        # A transient failure must not leave the process convinced the tables
        # exist — that would turn one bad moment into a permanently broken worker.
        class _Failing:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("transient")

        self.assertFalse(ps.ensure_schema(_Failing()))
        self.assertTrue(ps.ensure_schema(self.cur))
        self.assertTrue(self.cur.ddl)

    def test_reset_is_required_for_a_fresh_database(self):
        # Guards the test-suite contract itself: the flag is process-global, so a
        # new in-memory DB needs an explicit reset or it silently gets no tables.
        ps.ensure_schema(self.cur)
        other = sqlite3.connect(":memory:")
        self.addCleanup(other.close)
        other_cur = other.cursor()

        ps.ensure_schema(other_cur)  # cached: does nothing
        with self.assertRaises(sqlite3.OperationalError):
            other_cur.execute("SELECT 1 FROM presence_sessions")

        ps.reset_schema_cache()
        ps.ensure_schema(other_cur)
        other_cur.execute("SELECT 1 FROM presence_sessions")


if __name__ == "__main__":
    unittest.main()
