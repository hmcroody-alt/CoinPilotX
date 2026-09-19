"""The lock-health monitor's decision logic, exercised without a database.

Companion to `test_schema_ddl_once.py`: that suite stops the 2026-09-19 lock
convoy from being reintroduced, this one makes sure that if something else ever
produces the same shape, somebody hears about it. Both exist because the outage
was silent — zero 5xx, requests returning 200 after eleven minutes, and no
signal anywhere that PostgreSQL had 2041 deadlocks and every request slot parked
on a lock.

Everything interesting in `pg_lock_health` lives in `evaluate`, which is pure on
purpose. The alternative — asserting through a live sample — would need a real
PostgreSQL server under deliberate lock contention, which the SQLite-backed test
suite cannot provide, and would make the rate arithmetic (counter deltas,
statistics resets, the first-sample case) untestable at exactly the points where
it is easy to get wrong.

The sampling half is covered structurally instead: the queries are asserted to
read only the in-memory statistics views, because a probe that took a table lock
would join the convoy it exists to watch.

That structural cover has a known limit, learned the hard way. A recording
cursor accepts any string, so these tests all passed against a query that named
`pg_stat_activity.wait_start` — a column that does not exist in any PostgreSQL
version. Only running the probe against the real server found it. Whether the
SQL is *valid* is therefore not something this file can answer; what it can do
is pin the specific mistakes already made, which `WaitClockColumnTest` below
does.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import pg_lock_health  # noqa: E402


LIMITS = {
    "deadlocks_per_min": 3,
    "lock_waiters": 5,
    "lock_wait_seconds": 30,
    "cooldown_seconds": 900,
    "statement_timeout_ms": 3000,
}


def healthy(**overrides):
    signals = {
        "ok": True,
        "supported": True,
        "deadlocks": 100,
        "commits": 5000,
        "rollbacks": 3,
        "backends": 12,
        "lock_waiters": 0,
        "longest_lock_wait_seconds": 0.0,
        "contended_relations": [],
    }
    signals.update(overrides)
    return signals


class DeadlockRateTest(unittest.TestCase):
    """The counter is cumulative; only its rate means anything."""

    def test_first_sample_reports_no_rate_and_does_not_alert(self):
        # A raw threshold on the total would have fired on deploy and kept
        # firing forever: production sat at 2041 accumulated deadlocks long
        # after the incident was over.
        verdict = pg_lock_health.evaluate(
            healthy(deadlocks=2041), {"deadlocks": None, "at": None}, now=1000.0, limits=LIMITS
        )
        self.assertIsNone(verdict["deadlocks_per_min"])
        self.assertFalse(verdict["alert"])

    def test_rate_over_threshold_alerts(self):
        # +10 deadlocks in 60s = 10/min against a threshold of 3.
        verdict = pg_lock_health.evaluate(
            healthy(deadlocks=110), {"deadlocks": 100, "at": 940.0}, now=1000.0, limits=LIMITS
        )
        self.assertEqual(verdict["deadlocks_per_min"], 10.0)
        self.assertTrue(verdict["alert"])
        self.assertIn("deadlocks", verdict["reasons"][0])

    def test_rate_under_threshold_is_reported_but_silent(self):
        # One deadlock a minute is a busy database, not an incident. The number
        # is still carried so the healthy log line has a trend in it.
        verdict = pg_lock_health.evaluate(
            healthy(deadlocks=101), {"deadlocks": 100, "at": 940.0}, now=1000.0, limits=LIMITS
        )
        self.assertEqual(verdict["deadlocks_per_min"], 1.0)
        self.assertFalse(verdict["alert"])

    def test_short_interval_scales_up_not_down(self):
        # The worker's cycle is 45s, not 60. Two deadlocks in 15s is 8/min, and
        # an implementation that compared raw deltas against a per-minute
        # threshold would call that quiet.
        verdict = pg_lock_health.evaluate(
            healthy(deadlocks=102), {"deadlocks": 100, "at": 985.0}, now=1000.0, limits=LIMITS
        )
        self.assertEqual(verdict["deadlocks_per_min"], 8.0)
        self.assertTrue(verdict["alert"])

    def test_statistics_reset_is_treated_as_zero_not_as_a_negative_rate(self):
        # `pg_stat_reset()` or a failover drops the counter below the previous
        # reading. A negative rate is meaningless; the danger is that it also
        # reads as reassuringly calm.
        verdict = pg_lock_health.evaluate(
            healthy(deadlocks=0), {"deadlocks": 2041, "at": 940.0}, now=1000.0, limits=LIMITS
        )
        self.assertEqual(verdict["deadlocks_per_min"], 0.0)
        self.assertFalse(verdict["alert"])

    def test_no_rate_when_the_clock_did_not_advance(self):
        verdict = pg_lock_health.evaluate(
            healthy(deadlocks=150), {"deadlocks": 100, "at": 1000.0}, now=1000.0, limits=LIMITS
        )
        self.assertIsNone(verdict["deadlocks_per_min"])
        self.assertFalse(verdict["alert"])


class LockWaiterTest(unittest.TestCase):
    """Two different shapes of the same failure, each needing its own trigger."""

    def test_many_waiters_alerts(self):
        verdict = pg_lock_health.evaluate(
            healthy(lock_waiters=8, longest_lock_wait_seconds=4.0),
            {"deadlocks": None, "at": None},
            now=1000.0,
            limits=LIMITS,
        )
        self.assertTrue(verdict["alert"])
        self.assertIn("waiting on locks", verdict["reasons"][0])

    def test_a_couple_of_brief_waiters_is_ordinary_contention(self):
        verdict = pg_lock_health.evaluate(
            healthy(lock_waiters=2, longest_lock_wait_seconds=0.3),
            {"deadlocks": None, "at": None},
            now=1000.0,
            limits=LIMITS,
        )
        self.assertFalse(verdict["alert"])

    def test_one_session_stuck_for_a_long_time_alerts(self):
        # The count threshold alone cannot see this, and it is the more
        # dangerous shape: a single holder that never lets go is what a convoy
        # grows out of.
        verdict = pg_lock_health.evaluate(
            healthy(lock_waiters=1, longest_lock_wait_seconds=95.0),
            {"deadlocks": None, "at": None},
            now=1000.0,
            limits=LIMITS,
        )
        self.assertTrue(verdict["alert"])
        self.assertIn("95.0s", verdict["reasons"][0])

    def test_a_long_wait_needs_an_actual_waiter(self):
        # `longest` is a MAX over an empty set, which COALESCEs to 0 — but guard
        # the pairing anyway, so a future query change that returns a stale
        # maximum with no rows cannot fire a phantom alert.
        verdict = pg_lock_health.evaluate(
            healthy(lock_waiters=0, longest_lock_wait_seconds=900.0),
            {"deadlocks": None, "at": None},
            now=1000.0,
            limits=LIMITS,
        )
        self.assertFalse(verdict["alert"])


class UnsampleableDatabaseTest(unittest.TestCase):
    def test_a_failed_sample_on_postgres_is_itself_the_alert(self):
        # During a convoy, a probe timing out is a plausible outcome and is
        # exactly the moment the operator most needs to hear something. Silence
        # here would reproduce the original failure in the monitor itself.
        verdict = pg_lock_health.evaluate(
            {"ok": False, "supported": True, "reason": "sample_failed", "error": "statement timeout"},
            {"deadlocks": None, "at": None},
            now=1000.0,
            limits=LIMITS,
        )
        self.assertTrue(verdict["alert"])
        self.assertIn("statement timeout", verdict["reasons"][0])

    def test_sqlite_is_silent_rather_than_alerting(self):
        # Local development and the test suite run on SQLite, which has none of
        # these views. An unsupported backend is not a fault.
        verdict = pg_lock_health.evaluate(
            {"ok": False, "supported": False, "reason": "not_postgres"},
            {"deadlocks": None, "at": None},
            now=1000.0,
            limits=LIMITS,
        )
        self.assertFalse(verdict["alert"])


class CooldownTest(unittest.TestCase):
    """Email is rate limited; the log deliberately is not."""

    def test_second_alert_inside_the_window_does_not_escalate(self):
        store = {}
        self.assertTrue(pg_lock_health.should_escalate("lock_health", 1000.0, LIMITS, store))
        self.assertFalse(pg_lock_health.should_escalate("lock_health", 1400.0, LIMITS, store))

    def test_escalation_resumes_after_the_window(self):
        store = {}
        pg_lock_health.should_escalate("lock_health", 1000.0, LIMITS, store)
        self.assertTrue(pg_lock_health.should_escalate("lock_health", 1901.0, LIMITS, store))

    def test_kinds_are_throttled_independently(self):
        store = {}
        pg_lock_health.should_escalate("lock_health", 1000.0, LIMITS, store)
        self.assertTrue(pg_lock_health.should_escalate("sample_failed", 1000.0, LIMITS, store))


class ProbeIsReadOnlyTest(unittest.TestCase):
    """The probe must not become part of the problem it watches."""

    def test_sampling_reads_only_in_memory_statistics_views(self):
        # pg_stat_database, pg_stat_activity and pg_locks are all in-memory and
        # take no table locks. A probe that touched a real relation — or that
        # wrote a heartbeat row to record that it had run — would block behind
        # the convoy it was sent to detect.
        recorded = []

        class RecordingCursor:
            def execute(self, sql, params=None):
                recorded.append(" ".join(sql.split()))

            def fetchone(self):
                return (2041, 5000, 3, 12)

            def fetchall(self):
                return [("mobile_security_sessions", 7)]

        pg_lock_health.read_signals(RecordingCursor())

        self.assertTrue(recorded, "read_signals issued no queries")
        for statement in recorded:
            upper = statement.upper()
            self.assertTrue(
                upper.startswith("SELECT"),
                "lock-health probe issued a non-SELECT statement: %s" % statement,
            )
            for forbidden in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "LOCK ", "FOR UPDATE"):
                self.assertNotIn(forbidden, upper, "probe statement contains %s: %s" % (forbidden, statement))

    def test_contended_relation_lookup_is_skipped_when_nothing_is_waiting(self):
        # The relation breakdown joins pg_class and is pure diagnostic colour.
        # On a healthy database — which is almost always — it should not be
        # issued at all.
        recorded = []

        class QuietCursor:
            def execute(self, sql, params=None):
                recorded.append(" ".join(sql.split()))

            def fetchone(self):
                return (0, 0) if "pg_stat_activity" in recorded[-1] else (10, 1, 0, 4)

            def fetchall(self):
                raise AssertionError("relation breakdown queried with no waiters present")

        result = pg_lock_health.read_signals(QuietCursor())
        self.assertEqual(result["lock_waiters"], 0)
        self.assertFalse(any("pg_class" in statement for statement in recorded))


class WaitClockColumnTest(unittest.TestCase):
    """Pin the column the wait duration is measured from.

    The first version of this probe asked `pg_stat_activity` for `wait_start`.
    No such column exists — the wait clock lives on `pg_locks.waitstart`, and
    `pg_stat_activity` offers only `query_start`, which is when the statement
    began rather than when it blocked. Every other test in this file passed
    against that query, because a recording cursor will accept any string.

    So this asserts the two facts a mock genuinely can check: that the real
    column name is present, and that the invented one is not.
    """

    def _statements(self):
        recorded = []

        class RecordingCursor:
            def execute(self, sql, params=None):
                recorded.append(" ".join(sql.split()))

            def fetchone(self):
                return (0, 0) if "pg_stat_activity" in recorded[-1] else (0, 0, 0, 0)

            def fetchall(self):
                return []

        pg_lock_health.read_signals(RecordingCursor())
        return recorded

    def test_wait_duration_comes_from_pg_locks_waitstart(self):
        joined = " ".join(self._statements())
        self.assertIn("waitstart", joined)

    def test_the_column_that_does_not_exist_is_not_asked_for(self):
        joined = " ".join(self._statements())
        self.assertNotIn("wait_start", joined)

    def test_an_old_server_without_waitstart_degrades_instead_of_alerting(self):
        # waitstart arrived in PostgreSQL 14. On anything older the precise
        # query raises, and the fallback must still return a usable count —
        # otherwise the monitor would report "could not sample" on every cycle
        # forever, which is noise indistinguishable from a real outage.
        class OldServerCursor:
            def __init__(self):
                self.statements = []

            def execute(self, sql, params=None):
                flat = " ".join(sql.split())
                self.statements.append(flat)
                if "waitstart" in flat:
                    raise RuntimeError('column "waitstart" does not exist')

            def fetchone(self):
                return (3, 12.5) if "pg_stat_activity" in self.statements[-1] else (7, 100, 2, 9)

            def fetchall(self):
                return [("mobile_security_sessions", 3)]

        cursor = OldServerCursor()
        signals = pg_lock_health.read_signals(cursor)
        self.assertEqual(signals["lock_waiters"], 3)
        self.assertEqual(signals["longest_lock_wait_seconds"], 12.5)
        self.assertTrue(any("query_start" in s and "waitstart" not in s for s in cursor.statements))


class ThresholdConfigurationTest(unittest.TestCase):
    def test_a_malformed_threshold_falls_back_to_the_default(self):
        # Tunables are read at call time so a typo in a Railway variable
        # degrades this monitor to its defaults rather than crashing the alert
        # worker it is hosted in.
        previous = os.environ.get("PG_DEADLOCKS_PER_MIN_THRESHOLD")
        os.environ["PG_DEADLOCKS_PER_MIN_THRESHOLD"] = "not-a-number"
        try:
            self.assertEqual(pg_lock_health.thresholds()["deadlocks_per_min"], 3)
        finally:
            if previous is None:
                os.environ.pop("PG_DEADLOCKS_PER_MIN_THRESHOLD", None)
            else:
                os.environ["PG_DEADLOCKS_PER_MIN_THRESHOLD"] = previous

    def test_disabling_the_monitor_skips_sampling_entirely(self):
        previous = os.environ.get("PG_LOCK_ALERT_ENABLED")
        os.environ["PG_LOCK_ALERT_ENABLED"] = "0"
        try:
            self.assertEqual(pg_lock_health.run_once(), {"ok": True, "skipped": "disabled"})
        finally:
            if previous is None:
                os.environ.pop("PG_LOCK_ALERT_ENABLED", None)
            else:
                os.environ["PG_LOCK_ALERT_ENABLED"] = previous


if __name__ == "__main__":
    unittest.main()
