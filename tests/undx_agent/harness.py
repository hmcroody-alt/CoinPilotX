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
        # The columns ``alert_engine`` consults, plus the ones the feed's author join
        # selects by name. A feed query names them explicitly (``u.display_name``,
        # ``u.premium_status`` and a dozen more), so a users table with only the alert
        # engine's columns does not fail the *feed* test — it fails it as an
        # ``OperationalError: no such column``, which looks like a code defect and is
        # a fixture gap. Nothing here grants anything: every added column is presentation
        # or plan metadata, and ``hidden_from_discovery`` defaults to visible exactly as
        # production does.
        self.cur.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, email TEXT,
                phone_number TEXT, phone_verified INTEGER DEFAULT 0,
                account_status TEXT DEFAULT 'active',
                full_name TEXT, display_name TEXT, avatar_url TEXT,
                plan TEXT, subscription_plan TEXT, subscription_status TEXT,
                is_pro INTEGER DEFAULT 0, pro_active INTEGER DEFAULT 0,
                pro_expires_at TEXT, subscription_expires_at TEXT,
                premium_status TEXT, premium_expires_at TEXT,
                lifetime_premium INTEGER DEFAULT 0,
                premium_glow_manual_grant INTEGER DEFAULT 0,
                premium_mark_override INTEGER DEFAULT 0, premium_mark_type TEXT,
                hidden_from_discovery INTEGER DEFAULT 0)"""
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

    #: The feed tables ``pulse_feed_engine`` reads to answer one visible-post query.
    #: Written out rather than borrowed from ``bot.init_db`` because that function
    #: builds roughly a hundred and seventy tables and imports the whole monolith to do
    #: it. Only the columns the feed query actually names are here; a column the engine
    #: does not read is a column this fixture must not invent, since inventing one is
    #: how a fixture starts satisfying a lookup production would fail.
    FEED_SCHEMA = (
        """CREATE TABLE IF NOT EXISTS pulse_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            post_type TEXT DEFAULT 'text', title TEXT, body TEXT,
            media_ids_json TEXT DEFAULT '[]', tags_json TEXT DEFAULT '[]',
            visibility TEXT DEFAULT 'public', moderation_status TEXT DEFAULT 'approved',
            status TEXT DEFAULT 'published', deleted_at TEXT,
            created_at TEXT, updated_at TEXT,
            engagement_score REAL DEFAULT 0, risk_score REAL DEFAULT 0,
            view_count INTEGER DEFAULT 0, repost_of_post_id INTEGER,
            public_player_id TEXT)""",
        """CREATE TABLE IF NOT EXISTS pulse_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER,
            reaction_type TEXT, created_at TEXT, UNIQUE(post_id, user_id))""",
        """CREATE TABLE IF NOT EXISTS pulse_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER,
            parent_comment_id INTEGER, body TEXT, media_ids_json TEXT DEFAULT '[]',
            moderation_status TEXT DEFAULT 'approved',
            deleted_at TEXT, created_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS pulse_post_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER,
            visitor_id TEXT, viewed_at TEXT, dwell_ms INTEGER DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS pulse_post_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER,
            collection_name TEXT DEFAULT 'Saved', created_at TEXT,
            UNIQUE(post_id, user_id))""",
        """CREATE TABLE IF NOT EXISTS arena_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            public_player_id TEXT, avatar_url TEXT)""",
        """CREATE TABLE IF NOT EXISTS pulse_follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_user_id INTEGER, followed_user_id INTEGER)""",
        """CREATE TABLE IF NOT EXISTS pulse_friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            friend_user_id INTEGER, status TEXT DEFAULT 'active')""",
        """CREATE TABLE IF NOT EXISTS blocked_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker_user_id INTEGER, blocked_user_id INTEGER)""",
    )

    def ensure_feed_schema(self) -> None:
        """Create the feed tables. Idempotent, so tests may call it or not."""
        for statement in self.FEED_SCHEMA:
            self.cur.execute(statement)
        self.commit()

    def make_post(self, user_id: int = OWNER_ID, *, body: str = "Launch day is close.",
                  created_at: str = "2026-08-01T00:00:00", visibility: str = "public",
                  title: str = "") -> int:
        """Insert one visible post and return its id.

        ``created_at`` is an explicit argument rather than "now", because every test
        that means anything here is about *which* post is the most recent one, and a
        fixture that stamps two rows in the same millisecond cannot express that.
        """
        self.ensure_feed_schema()
        self.cur.execute(
            """INSERT INTO pulse_posts
               (user_id, post_type, title, body, visibility, moderation_status,
                status, created_at, updated_at)
               VALUES (?,'text',?,?,?,'approved','published',?,?)""",
            (int(user_id), title, body, visibility, created_at, created_at),
        )
        post_id = int(self.cur.lastrowid or 0)
        assert post_id, "fixture could not create a post"
        self.commit()
        return post_id

    def post_liked(self, post_id: int, user_id: int = OWNER_ID) -> bool:
        """Whether this account has liked this post, read straight from the table.

        The evidence, not the receipt. ``feed_intelligence_service.get_post_like``
        would also answer, but it is the function the *verifier* calls, so asserting
        against it would let one read vouch for itself.
        """
        self.cur.execute(
            "SELECT reaction_type FROM pulse_reactions WHERE post_id=? AND user_id=? LIMIT 1",
            (int(post_id), int(user_id)),
        )
        row = self.cur.fetchone()
        return bool(row and (dict(row).get("reaction_type") if hasattr(row, "keys")
                             else row[0]) == "like")

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
