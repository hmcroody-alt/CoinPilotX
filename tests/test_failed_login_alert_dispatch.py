"""A failed login must actually dispatch the security alert.

Regression cover for a bug that lived in production and staging: the alert
call site referenced an undefined name (`create_task`), the resulting NameError
was caught by a bare `except Exception` that logged FAILED_LOGIN_ALERT_TASK_SKIPPED
at info level, and the login endpoint went on returning a correct 401. Visible
behaviour was right; the side effect never happened.

So asserting the 401 is not enough, and neither is asserting the alert row on
its own -- a future swallow could hide a partial failure the same way. Each test
here watches the swallow log as well as the row.

Runs against a temp sqlite file so nothing touches coinpilotx.db.

Run: .venv/bin/python3 -m pytest tests/test_failed_login_alert_dispatch.py
"""

import logging
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="failed_login_alert_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

SWALLOW_LOG = "FAILED_LOGIN_ALERT_TASK_SKIPPED"

VICTIM_EMAIL = "burst-target@alert-dispatch.invalid"
VICTIM_PASSWORD = "Correct-Horse-Battery-1!"

_IPS = {}


def _ip_for(test_id):
    """A stable documentation-range IP per test, so rate buckets do not overlap."""
    return _IPS.setdefault(test_id, f"198.51.100.{len(_IPS) + 10}")


class _LogWatcher(logging.Handler):
    """Captures the alert path's swallow log so a silent failure fails the test."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def swallowed(self):
        return [m for m in self.messages if SWALLOW_LOG in m]


def _seed_victim():
    conn = sqlite3.connect(_DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE email=?", (VICTIM_EMAIL,))
        cur.execute(
            """
            INSERT INTO users (email, username, password_hash, email_verified, account_status, login_enabled, access_enabled, created_at)
            VALUES (?, ?, ?, 1, 'active', 1, 1, datetime('now'))
            """,
            (VICTIM_EMAIL, "burst_target", generate_password_hash(VICTIM_PASSWORD)),
        )
        conn.commit()
    finally:
        conn.close()


def _reset_failed_login_state():
    conn = sqlite3.connect(_DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM auth_events")
        cur.execute("DELETE FROM security_events WHERE event_type='failed_login_burst'")
        cur.execute("DELETE FROM failed_login_controls")
        cur.execute("DELETE FROM admin_tasks WHERE department='security'")
        conn.commit()
    finally:
        conn.close()


def _security_tasks():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM admin_tasks WHERE department='security' ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


class FailedLoginAlertDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.init_db()
        _seed_victim()

    def setUp(self):
        _reset_failed_login_state()
        # /api/mobile/auth/login allows 10 hits per 300s per IP and per device,
        # counted in a process-level bucket that no database reset clears. Each
        # test gets its own attacker identity so it starts under the limit.
        self.attacker_ip = _ip_for(self.id())
        self.device_id = f"regression-{abs(hash(self.id())) % 100000}"
        self.client = bot.webhook_app.test_client()
        self.watcher = _LogWatcher()
        root = logging.getLogger()
        self._restore_level = root.level
        root.addHandler(self.watcher)
        root.setLevel(logging.INFO)

    def tearDown(self):
        root = logging.getLogger()
        root.removeHandler(self.watcher)
        root.setLevel(self._restore_level)

    def _attempt(self, password="wrong-password", email=VICTIM_EMAIL):
        return self.client.post(
            "/api/mobile/auth/login",
            json={"identifier": email, "password": password},
            headers={
                "X-Forwarded-For": self.attacker_ip,
                "X-PulseSoc-Device-Id": self.device_id,
                "User-Agent": "PulseSoc Alert Regression",
            },
        )

    def _burst(self):
        """Drive failed logins up to the alert threshold, newest response back."""
        response = None
        for _ in range(bot.FAILED_LOGIN_CHALLENGE_AFTER):
            response = self._attempt()
        return response

    def test_failed_login_burst_dispatches_a_security_task(self):
        response = self._burst()

        # The endpoint's visible behaviour was never the broken part.
        self.assertEqual(response.status_code, 401)

        self.assertEqual(
            self.watcher.swallowed(),
            [],
            "the alert path raised and was swallowed instead of dispatching",
        )

        tasks = _security_tasks()
        self.assertEqual(len(tasks), 1, f"expected exactly one security alert task, got {tasks}")
        task = tasks[0]
        self.assertEqual(task["source_type"], "auth_event")
        self.assertEqual(task["status"], "open")
        self.assertIn(task["priority"], {"high", "critical"})
        self.assertIn("Failed login burst", task["title"])
        self.assertIn("alert-dispatch.invalid", task["title"])

    def test_alert_task_points_at_the_auth_event_that_triggered_it(self):
        self._burst()

        task = _security_tasks()[0]
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            latest = conn.execute("SELECT id FROM auth_events ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()

        # admin_tasks.source_id is TEXT; binding the raw int passes on sqlite and
        # fails on Postgres, so pin the stored form, not just the value.
        self.assertIsInstance(task["source_id"], str)
        self.assertEqual(task["source_id"], str(latest["id"]))

    def test_below_threshold_failed_logins_do_not_alert(self):
        response = self._attempt()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.watcher.swallowed(), [])
        self.assertEqual(_security_tasks(), [])

    def test_successful_login_does_not_alert(self):
        response = self._attempt(password=VICTIM_PASSWORD)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_security_tasks(), [])

    def test_watcher_detects_a_swallowed_alert_failure(self):
        """Proves the guard in the tests above can actually fail.

        Without this, a future refactor that stops emitting the swallow log would
        leave every assertEqual(swallowed(), []) vacuously green.
        """
        original = bot.db

        def exploding_db():
            raise RuntimeError("alert connection unavailable")

        bot.db = exploding_db
        try:
            bot.create_security_alert_task("Failed login burst from x.invalid", "high", "auth_event", 7)
        finally:
            bot.db = original

        self.assertTrue(
            self.watcher.swallowed(),
            "a raising alert path must still surface the swallow log",
        )
        self.assertEqual(_security_tasks(), [])

    def test_alert_dispatch_survives_a_failed_login_transaction(self):
        """The alert must not ride the caller's open transaction.

        It used to be written through the failed-login cursor. On Postgres a
        failure there aborts that transaction and loses the auth event, so the
        alert is dispatched on its own connection and committed independently.
        """
        self._burst()

        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            events = conn.execute(
                "SELECT COUNT(*) AS total FROM auth_events WHERE event_type='login_failed'"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(int(events["total"]), bot.FAILED_LOGIN_CHALLENGE_AFTER)
        self.assertEqual(len(_security_tasks()), 1)


if __name__ == "__main__":
    unittest.main()
