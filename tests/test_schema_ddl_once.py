"""Schema DDL must run once per worker process, never once per request.

Regression test for the presence incident, generalised to the other hot request
paths that had the same shape.

`CREATE TABLE / INDEX IF NOT EXISTS` is free on SQLite and a trap on PostgreSQL:
it takes a ShareLock on the table, which conflicts with the RowExclusiveLock an
INSERT or UPDATE needs. Every call site covered here ran the DDL and then wrote
the same table immediately after — several of them on a transaction the request
keeps open afterwards, so the ShareLock is still held when the write lands.

    DeadlockDetected: UPDATE presence_sessions SET last_heartbeat_at=...
    Process A waits for RowExclusiveLock on relation 219680; blocked by B.
    Process B waits for RowExclusiveLock on relation 219680; blocked by A.

PostgreSQL kills one side; the loser strands its pooled connection in a failed
transaction, and gunicorn's gthread `--timeout` watches the worker heartbeat
rather than the request, so the thread is never recycled. At `--workers 2` that
was half of production hanging.

So the property under test is "how many times does the DDL actually run" — the
thing that would have caught this, and the thing that will catch it coming back.
"""

import os
import sqlite3
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import schema_guard  # noqa: E402


class CountingCursor:
    """A real cursor that records the DDL issued through it."""

    def __init__(self, cur):
        self._cur = cur
        self.ddl = []

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith(("CREATE", "ALTER")):
            self.ddl.append(" ".join(sql.split())[:70])
        return self._cur.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class CountingConnection:
    def __init__(self, conn):
        self._conn = conn
        self.cursors = []

    def cursor(self):
        c = CountingCursor(self._conn.cursor())
        self.cursors.append(c)
        return c

    @property
    def ddl(self):
        return [d for c in self.cursors for d in c.ddl]

    def __getattr__(self, name):
        return getattr(self._conn, name)


class GuardBehaviourTest(unittest.TestCase):
    """The guard primitive itself."""

    def test_the_body_runs_once_and_the_result_is_reused(self):
        calls = []

        @schema_guard.run_once_per_process
        def create():
            calls.append(1)
            return "created"

        self.assertEqual([create() for _ in range(50)], ["created"] * 50)
        self.assertEqual(len(calls), 1)

    def test_a_raised_failure_is_retried_rather_than_cached(self):
        # A transient failure must not leave the process convinced the tables
        # exist — that turns one bad moment into a permanently broken worker.
        calls = []

        @schema_guard.run_once_per_process
        def create():
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("transient")
            return True

        with self.assertRaises(sqlite3.OperationalError):
            create()
        self.assertTrue(create())
        self.assertEqual(len(calls), 2)

    def test_a_false_return_is_retried_rather_than_cached(self):
        # presence_service.ensure_schema signals failure by returning False
        # instead of raising; that must not be cached either.
        results = [False, False, True, True]
        calls = []

        @schema_guard.run_once_per_process
        def create():
            calls.append(1)
            return results[len(calls) - 1]

        self.assertFalse(create())
        self.assertFalse(create())
        self.assertTrue(create())
        self.assertTrue(create())
        self.assertEqual(len(calls), 3, "success should stop further calls")

    def test_concurrent_first_use_still_issues_the_ddl_once(self):
        # gunicorn runs --threads 4, so several threads reach first use together.
        # Without the lock they would each issue the DDL concurrently, which is
        # the very contention the guard exists to remove.
        calls = []
        start = threading.Barrier(8)

        @schema_guard.run_once_per_process
        def create():
            calls.append(1)
            return True

        def worker():
            start.wait()
            create()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(calls), 1)

    def test_reset_allows_a_fresh_database(self):
        # Guards the test-suite contract: the flag is process-global, so a new
        # in-memory DB needs an explicit reset or it silently gets no tables.
        calls = []

        @schema_guard.run_once_per_process
        def create():
            calls.append(1)

        create()
        create()
        self.assertEqual(len(calls), 1)
        create.reset()
        create()
        self.assertEqual(len(calls), 2)

    def test_reset_all_clears_every_registered_guard(self):
        calls = []

        @schema_guard.run_once_per_process
        def create():
            calls.append(1)

        create()
        schema_guard.reset_all()
        create()
        self.assertEqual(len(calls), 2)


class _HotPathTest(unittest.TestCase):
    """Base: a fresh in-memory DB per test, with all guards reset."""

    def setUp(self):
        schema_guard.reset_all()
        self.raw = sqlite3.connect(":memory:")
        self.raw.row_factory = sqlite3.Row
        self.conn = CountingConnection(self.raw)
        self.cur = self.conn.cursor()

    def tearDown(self):
        schema_guard.reset_all()
        self.raw.close()


class PushDeliveryDdlTest(_HotPathTest):
    """The hottest path in the audit: ~1,180 routes reach this via notify_user.

    Worse than presence, because enqueue_push_with_cursor runs on the caller's
    open transaction — so on PostgreSQL the DDL's ShareLock is held until the
    request commits, not just for the length of the CREATE.
    """

    def setUp(self):
        super().setUp()
        os.environ["PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"
        self.addCleanup(os.environ.pop, "PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED", None)

    def test_first_call_creates_the_schema(self):
        from services import push_service

        push_service._ensure_push_delivery_jobs(self.cur)
        self.assertTrue(self.cur.ddl)

    def test_enqueue_issues_no_ddl_after_warmup(self):
        from services import push_service

        first = push_service.enqueue_push_with_cursor(
            self.cur, 10, "hello", "body", push_type="test")
        self.assertTrue(first["ok"], first)
        self.assertTrue(self.cur.ddl, "expected the first enqueue to create tables")
        self.cur.ddl.clear()

        for i in range(25):
            res = push_service.enqueue_push_with_cursor(
                self.cur, 10, "hello", f"body {i}", push_type="test")
            self.assertTrue(res["ok"], res)

        self.assertEqual(
            self.cur.ddl, [],
            "enqueueing a push must never issue DDL; that is what deadlocked "
            "production, and this path runs on the caller's open transaction")


class FeedDdlTest(_HotPathTest):
    """/api/pulse/feed and /api/pulse/profile/me — the highest-QPS surfaces."""

    def test_repeat_calls_issue_no_further_ddl(self):
        from services import pulse_feed_engine

        pulse_feed_engine._ensure_home_safety_tables(self.cur)
        self.assertTrue(self.cur.ddl)
        self.cur.ddl.clear()

        for _ in range(50):
            pulse_feed_engine._ensure_home_safety_tables(self.cur)

        self.assertEqual(self.cur.ddl, [], "a feed load must not issue DDL")


class ChatHealthDdlTest(_HotPathTest):
    """record_trace writes on the caller's cursor on every messenger request."""

    def test_repeat_traces_issue_no_further_ddl(self):
        from services import chat_health_service

        chat_health_service.record_trace(self.cur, 1, "/api/x", "ok", "trace-1")
        self.assertTrue(self.cur.ddl)
        self.cur.ddl.clear()

        for i in range(25):
            chat_health_service.record_trace(self.cur, 1, "/api/x", "ok", f"t-{i}")

        self.assertEqual(self.cur.ddl, [])
        self.cur.execute("SELECT COUNT(*) FROM pulse_chat_health_traces")
        self.assertEqual(self.cur.fetchone()[0], 26, "traces must still be written")


class GuardedHotPathsTest(unittest.TestCase):
    """The audited hot call sites must stay guarded.

    Route reachability was measured with a call graph over bot.py + services/;
    every entry below was reachable from a request handler without passing
    through an already-guarded barrier such as bot.init_db.
    """

    HOT_CALL_SITES = [
        ("services.push_service", "_ensure_push_delivery_jobs"),
        ("services.push_service", "_ensure_expo_push_tickets"),
        ("services.push_service", "_ensure_user_device_tokens"),
        ("services.pulse_feed_engine", "_ensure_home_safety_tables"),
        ("services.conversion_funnel_engine", "ensure_schema"),
        ("services.chat_health_service", "ensure_trace_schema"),
        ("services.pulsesoc_notification_system", "ensure_schema"),
        ("services.pulsesoc_dashboard_centers", "ensure_tables"),
        ("services.dashboard_account_command_center", "ensure_schema"),
        ("services.brevo_contacts", "ensure_sync_table"),
        ("services.premium_entitlement_service", "ensure_founder_schema"),
        ("services.business_os.rewards.engine", "ensure_schema"),
        ("services.pulsesoc_growth_engine", "ensure_schema"),
        ("services.pulse_settings_routes", "ensure_settings_schema"),
        ("services.pulse_region_preferences", "ensure_schema"),
    ]

    def test_every_audited_hot_call_site_is_guarded(self):
        import importlib

        unguarded = []
        for mod_name, func_name in self.HOT_CALL_SITES:
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, func_name)
            if not hasattr(fn, "__wrapped_ddl__"):
                unguarded.append(f"{mod_name}.{func_name}")

        self.assertEqual(
            unguarded, [],
            "these run schema DDL on a hot request path and lost their guard")


if __name__ == "__main__":
    unittest.main()
