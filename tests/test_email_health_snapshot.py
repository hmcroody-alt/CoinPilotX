"""The email health page is where you go when mail is not arriving.

It answered with two numbers that were not about the thing they named.

`failed emails` was `WHERE lower(status) IN ('failed','error')`. Nothing in this
codebase ever writes either of those two bare words -- every failure is
`failed_<reason>`, which is the whole point of having reason codes. In
production that query found 2 of the 1,551 recorded failures, so the page
greeted an operator investigating a delivery outage with a reassuring "2".

`queued retries` was `SELECT COUNT(*) FROM failed_email_queue` with no
predicate. That table is not a queue; it is the outbox's whole history --
delivered rows, dead-lettered rows, and scheduled mail whose `next_retry_at` is
deliberately weeks out. It reported a backlog of 2,208 against a real backlog
of 6.

Both errors point the same way: they make the page agree with whatever you
already believed. Runs against a temp sqlite file so nothing touches
coinpilotx.db.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="email_health_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.init_db()


def _iso(delta=None):
    return (datetime.utcnow() + (delta or timedelta(0))).isoformat(timespec="seconds")


class EmailHealthSnapshotCase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM email_logs")
        cur.execute("DELETE FROM failed_email_queue")
        conn.commit()
        conn.close()

    def _log(self, status, email_type="transactional", created_at=None):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO email_logs (user_id, recipient_email, email_type, subject, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "someone@example.com", email_type, "Subject", status, created_at or _iso()),
        )
        conn.commit()
        conn.close()

    def _queue(self, status, next_retry_at=None, updated_at=None):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO failed_email_queue (recipient_email, email_type, subject, status, next_retry_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("someone@example.com", "transactional", "Subject", status, next_retry_at, _iso(), updated_at or _iso()),
        )
        conn.commit()
        conn.close()

    def test_a_a_reason_coded_failure_is_a_failure(self):
        # `failed_brevo_401` is the shape every real failure has. The old query
        # matched neither it nor any other row this codebase writes.
        self._log("failed_brevo_401")
        self._log("failed_brevo_not_configured")
        self._log("failed_smtp_timeout")
        self.assertEqual(bot.email_health_snapshot()["failed_email_count"], 3)

    def test_b_a_successful_send_is_not_counted_as_a_failure(self):
        self._log("sent_brevo")
        self._log("sent_smtp")
        self._log("queued")
        self.assertEqual(bot.email_health_snapshot()["failed_email_count"], 0)

    def test_c_the_queue_depth_counts_only_what_is_due(self):
        self._queue("pending")
        self._queue("pending", next_retry_at=_iso(timedelta(seconds=-60)))
        self._queue("retry_ready", next_retry_at="")
        snapshot = bot.email_health_snapshot()
        self.assertEqual(snapshot["queue_depth_due_now"], 3)

    def test_d_scheduled_mail_is_not_a_backlog(self):
        # The six rows sitting in production are `private_meeting_reminder`
        # scheduled for a future date. They are working as designed.
        self._queue("pending", next_retry_at=_iso(timedelta(days=7)))
        self.assertEqual(bot.email_health_snapshot()["queue_depth_due_now"], 0)

    def test_e_delivered_and_dead_lettered_history_is_not_a_backlog(self):
        # 1,887 dead-lettered and 315 sent rows were the bulk of the 2,208.
        self._queue("sent")
        self._queue("dead_letter")
        snapshot = bot.email_health_snapshot()
        self.assertEqual(snapshot["queue_depth_due_now"], 0)
        self.assertEqual(snapshot["queue_dead_lettered"], 1)

    def test_f_dead_letters_are_reported_rather_than_hidden(self):
        # Excluding them from the queue depth must not make them disappear --
        # a dead letter is mail nobody will ever receive, which is worse news
        # than a retry, not better.
        for _ in range(4):
            self._queue("dead_letter")
        self.assertEqual(bot.email_health_snapshot()["queue_dead_lettered"], 4)

    def test_g_the_worker_is_judged_by_recent_deliveries(self):
        # There is no heartbeat; the outbox worker is the only thing that moves
        # a row to 'sent', so a recent one is the evidence that it is alive.
        self._queue("sent", updated_at=_iso(timedelta(minutes=-5)))
        self._queue("sent", updated_at=_iso(timedelta(hours=-9)))
        self.assertEqual(bot.email_health_snapshot()["worker_sends_in_the_last_hour"], 1)

    def test_h_the_last_successful_send_and_last_failure_are_both_reported(self):
        self._log("sent_brevo", created_at=_iso(timedelta(hours=-2)))
        self._log("failed_brevo_401", created_at=_iso(timedelta(hours=-1)))
        snapshot = bot.email_health_snapshot()
        self.assertTrue(snapshot["last_successful_send_at"])
        self.assertEqual(snapshot["last_failure"]["status"], "failed_brevo_401")

    def test_i_readiness_comes_from_the_sender_not_from_one_env_var(self):
        # The page used to read `bool(os.getenv("BREVO_API_KEY"))`, which says
        # nothing about BREVO_EMAIL_ENABLED, a blank sender, or a key pasted
        # with a trailing newline.
        snapshot = bot.email_health_snapshot()
        for key in (
            "provider_ready",
            "api_key_configured",
            "sender_email_configured",
            "sender_name_configured",
            "sending_enabled",
            "missing_fields",
        ):
            self.assertIn(key, snapshot)

    def test_j_no_secret_value_is_ever_in_the_snapshot(self):
        key = "brevo-secret-value-9c1f"
        previous = os.environ.get("BREVO_API_KEY")
        os.environ["BREVO_API_KEY"] = key
        try:
            rendered = repr(bot.email_health_snapshot())
        finally:
            if previous is None:
                os.environ.pop("BREVO_API_KEY", None)
            else:
                os.environ["BREVO_API_KEY"] = previous
        self.assertNotIn(key, rendered)


if __name__ == "__main__":
    unittest.main()
