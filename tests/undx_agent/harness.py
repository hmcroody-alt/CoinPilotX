"""A real database for the UNDX agent tests.

These tests run against ``services.alert_engine`` and
``services.pulsesoc_notification_system`` as they actually are, on a real SQLite
file, with real owner-scoped SQL. That is a deliberate cost: a mocked alert engine
would happily confirm that the gateway "paused" an alert belonging to somebody
else, because a mock has no ``WHERE user_id=?``. The isolation properties this
suite claims to test are properties of the SQL, so the SQL has to be present.

``services.db.connect`` reads ``DATABASE_URL`` at call time, which is what makes a
per-test temporary file possible without touching the developer's database.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

#: Flag values used by tests that expect the agent to be fully switched on. Tests
#: exercising a disabled or partially disabled agent override individual keys.
ENABLED_FLAGS = {
    "UNDX_AGENT_ENABLED": "1",
    "UNDX_AGENT_READS_ENABLED": "1",
    "UNDX_AGENT_WRITES_ENABLED": "1",
    "UNDX_AGENT_DISABLE_WRITES": "",
    "UNDX_AGENT_ENABLED_CAPABILITIES": "",
    "UNDX_AGENT_DISABLED_CAPABILITIES": "",
    "UNDX_AGENT_QA_USER_IDS": "7,8",
}

OWNER_ID = 7
OTHER_ID = 8
OUTSIDER_ID = 9  # authenticated, but outside the QA cohort


class AgentFixture:
    """One isolated database plus the flag environment for a single test case."""

    def __init__(self, **flag_overrides: str) -> None:
        self._path = tempfile.mkstemp(prefix="undx_agent_", suffix=".db")[1]
        self._saved: dict[str, str | None] = {}
        self._apply({"DATABASE_URL": f"sqlite:///{self._path}", **ENABLED_FLAGS, **flag_overrides})
        self.conn = None
        self.cur = None

    # -- environment ------------------------------------------------------

    def _apply(self, values: dict[str, str]) -> None:
        for key, value in values.items():
            self._saved.setdefault(key, os.environ.get(key))
            if value == "":
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def set_flags(self, **values: str) -> None:
        """Change flags mid-test. The policy engine reads ``os.getenv`` on every call,
        so this genuinely exercises a live kill switch rather than a cached one."""
        self._apply(values)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> "AgentFixture":
        from services import alert_engine, db, undx_architecture

        self.conn = db.connect()
        self.cur = self.conn.cursor()
        undx_architecture.ensure_schema(self.cur)
        # A minimal users table: alert_engine reads it for delivery-channel readiness.
        # Only the columns it actually consults are present, so the fixture cannot
        # accidentally satisfy a lookup that production would fail.
        self.cur.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, email TEXT,
                phone_number TEXT, phone_verified INTEGER DEFAULT 0,
                account_status TEXT DEFAULT 'active')"""
        )
        # alert_engine also reports delivery-channel readiness on create, which counts
        # push subscriptions. Present but empty is the honest fixture state: these test
        # users have no device registered.
        self.cur.execute(
            """CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                endpoint TEXT, is_active INTEGER DEFAULT 1, active INTEGER DEFAULT 1)"""
        )
        # Listing alerts joins the most recent delivery attempt per channel.
        self.cur.execute(
            """CREATE TABLE IF NOT EXISTS notification_delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                alert_rule_id INTEGER, channel TEXT, status TEXT,
                error_message TEXT, created_at TEXT)"""
        )
        for uid in (OWNER_ID, OTHER_ID, OUTSIDER_ID):
            self.cur.execute(
                "INSERT OR IGNORE INTO users (user_id, username, email) VALUES (?, ?, ?)",
                (uid, f"user{uid}", f"user{uid}@example.test"),
            )
        self.conn.commit()
        # ``ensure_alert_schema`` memoises "already done" in a module global, which is
        # right in production — the schema is created once per process — and wrong here,
        # where every test case gets a brand new database file. Without this reset the
        # first case in a run creates ``alert_rules`` and every later case silently
        # skips it, so the suite fails in a way that has nothing to do with the code
        # under test.
        alert_engine._ALERT_SCHEMA_READY = False
        alert_engine.ensure_alert_schema(self.conn)
        self.conn.commit()
        return self

    def commit(self) -> None:
        if self.conn is not None:
            self.conn.commit()

    def stop(self) -> None:
        try:
            if self.conn is not None:
                self.conn.close()
        finally:
            for key, value in self._saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            try:
                os.unlink(self._path)
            except OSError:
                pass

    def __enter__(self) -> "AgentFixture":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- seeding ----------------------------------------------------------

    def make_alert(self, user_id: int = OWNER_ID, *, symbol: str = "BTC",
                   condition: str = "above", threshold: float = 90000.0) -> int:
        """Create one alert through the real service and return its canonical id."""
        from services import alert_engine

        made = alert_engine.create_alert_rule(
            int(user_id), symbol=symbol, condition=condition, threshold=threshold,
        )
        alert_id = int(made.get("alert_id") or made.get("rule_id") or 0)
        assert alert_id, f"fixture could not create an alert: {made}"
        self.commit()
        return alert_id

    def alert_status(self, alert_id: int, user_id: int = OWNER_ID) -> str:
        """Read status straight from the service, bypassing the agent entirely.

        Tests assert against this rather than against the gateway's own receipt: a
        receipt that agreed with itself would prove nothing.
        """
        from services import alert_engine

        rule = alert_engine.get_alert_rule(int(alert_id), int(user_id))
        return "" if not rule else str(rule.get("status") or "active")

    def alert_threshold(self, alert_id: int, user_id: int = OWNER_ID) -> float:
        """The trigger price, read the same way and for the same reason.

        Needed by tests that assert a write did *not* happen. After a confirmation card
        the interesting claim is that the row still holds the old number, and only the
        service can make that claim without the agent vouching for itself.
        """
        from services import alert_engine

        rule = alert_engine.get_alert_rule(int(alert_id), int(user_id))
        return 0.0 if not rule else float(rule.get("threshold") or 0.0)


__all__ =["AgentFixture", "ENABLED_FLAGS", "OWNER_ID", "OTHER_ID", "OUTSIDER_ID"]
