"""Route-hit telemetry — the evidence every deletion in the web rebuild is gated on.

These tests are about the two ways this feature can be silently useless:

1. **Misclassifying the client.** If a phone browser counts as the native app,
   the gate protects routes the app never calls. If the app counts as web, the
   gate deletes routes the shipped App Store client still needs — and that
   failure ships to users who cannot roll back.
2. **Recording anything identifying.** The table is retained for a week across
   production traffic. It must hold url rules, not paths.

It runs against a temporary SQLite database, never production, and never
imports bot.py.
"""

import os
import tempfile
import time
import unittest


class RouteHitClientClassification(unittest.TestCase):
    """The four-value split, and why each boundary sits where it does."""

    @classmethod
    def setUpClass(cls):
        from services import route_hit_log
        cls.mod = route_hit_log

    def test_native_app_is_identified_by_its_own_header(self):
        client = self.mod.classify_client(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            {"X-PulseSoc-Platform": "ios"},
        )
        self.assertEqual(client, self.mod.CLIENT_NATIVE)

    def test_native_app_is_identified_by_its_ua_prefix(self):
        client = self.mod.classify_client("PulseSocNativeApp/13 (ios; Expo)")
        self.assertEqual(client, self.mod.CLIENT_NATIVE)

    def test_mobile_safari_is_web_not_native(self):
        """
        The load-bearing case. Mobile Safari is a *web* client: counting it as
        the app would make the deletion gate protect routes the shipped native
        client never calls, which is the opposite of what the gate is for.
        """
        client = self.mod.classify_client(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )
        self.assertEqual(client, self.mod.CLIENT_WEB)

    def test_desktop_browser_is_web(self):
        client = self.mod.classify_client(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
        self.assertEqual(client, self.mod.CLIENT_WEB)

    def test_monitors_and_crawlers_are_bots(self):
        """
        An uptime check that pings a route every 60 seconds would otherwise keep
        a dead route alive forever — the gate would never fire on anything.
        """
        for ua in ("curl/8.4.0", "python-requests/2.31.0", "Googlebot/2.1",
                   "Pingdom.com_bot_version_1.4", "UptimeRobot/2.0"):
            with self.subTest(ua=ua):
                self.assertEqual(self.mod.classify_client(ua), self.mod.CLIENT_BOT)

    def test_empty_user_agent_is_unknown_never_web(self):
        """Unknown is a first-class value. Guessing "web" here would inflate the
        one number the rebuild is allowed to delete on."""
        self.assertEqual(self.mod.classify_client(""), self.mod.CLIENT_UNKNOWN)
        self.assertEqual(self.mod.classify_client(None), self.mod.CLIENT_UNKNOWN)


class RouteHitBuffering(unittest.TestCase):
    """Buffer and flush behaviour, against a throwaway SQLite file."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.environ["DATABASE_URL"] = f"sqlite:///{self.tmp.name}"
        os.environ["PULSE_ROUTE_HIT_LOG_ENABLED"] = "1"

        import importlib
        from services import db as db_module
        importlib.reload(db_module)
        from services import route_hit_log
        importlib.reload(route_hit_log)
        self.mod = route_hit_log
        self.db = db_module

        conn = self.db.connect()
        self.mod.ensure_schema(conn)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("PULSE_ROUTE_HIT_LOG_ENABLED", None)
        os.environ.pop("DATABASE_URL", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def rows(self):
        conn = self.db.connect()
        cur = conn.cursor()
        cur.execute(
            f"SELECT day, rule, method, client, hits FROM {self.mod.TABLE} ORDER BY rule, client"
        )
        out = [tuple(r) for r in cur.fetchall()]
        conn.close()
        return out

    def test_disabled_by_default_records_nothing(self):
        os.environ.pop("PULSE_ROUTE_HIT_LOG_ENABLED", None)
        self.mod.record("/pulse/feed", "GET", self.mod.CLIENT_WEB)
        self.mod.flush()
        self.assertEqual(self.rows(), [])

    def test_hits_accumulate_into_one_row_per_key(self):
        """A route under load must cost one upsert per window, not one insert
        per hit — otherwise the telemetry is a write amplifier on the hot path."""
        for _ in range(50):
            self.mod.record("/pulse/feed", "GET", self.mod.CLIENT_WEB)
        self.mod.flush()
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], ("/pulse/feed", "GET", "web", 50))

    def test_the_first_record_does_not_trigger_a_flush(self):
        """
        With the flush clock seeded at zero instead of at import, a full window
        has already "elapsed" when the worker boots, so the very first request
        it serves pays for a synchronous database write. That is the exact
        per-request write the buffer exists to avoid, landing on the coldest
        request in the process.
        """
        self.mod.record("/pulse/feed", "GET", self.mod.CLIENT_WEB)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.mod.stats()["buffered"], 1)

    def test_the_flush_interval_is_honoured(self):
        self.mod.record("/pulse/feed", "GET", self.mod.CLIENT_WEB)
        self.assertEqual(self.rows(), [])
        self.mod.record(
            "/pulse/feed", "GET", self.mod.CLIENT_WEB,
            now=time.time() + self.mod.FLUSH_INTERVAL_SECONDS + 1,
        )
        self.assertEqual(self.rows()[0][4], 2)

    def test_clients_are_counted_separately(self):
        """The whole point: one number per route cannot distinguish a route only
        the app reaches from a route nothing reaches."""
        for _ in range(3):
            self.mod.record("/api/pulse/feed", "GET", self.mod.CLIENT_NATIVE)
        self.mod.record("/api/pulse/feed", "GET", self.mod.CLIENT_WEB)
        self.mod.flush()
        counts = {(r[3]): r[4] for r in self.rows()}
        self.assertEqual(counts, {"native": 3, "web": 1})

    def test_a_second_flush_adds_rather_than_replaces(self):
        self.mod.record("/pulse/feed", "GET", self.mod.CLIENT_WEB)
        self.mod.flush()
        self.mod.record("/pulse/feed", "GET", self.mod.CLIENT_WEB)
        self.mod.flush()
        self.assertEqual(self.rows()[0][4], 2)

    def test_unmatched_requests_are_recorded_under_one_key(self):
        self.mod.record("", "GET", self.mod.CLIENT_WEB)
        self.mod.flush()
        self.assertEqual(self.rows()[0][1], self.mod.UNMATCHED_RULE)

    def test_failed_flush_returns_the_counts_to_the_buffer(self):
        """Losing a window of evidence is how a route gets deleted on
        incomplete data, so a flush failure must not discard it."""
        self.mod.record("/pulse/feed", "GET", self.mod.CLIENT_WEB)

        def boom():
            raise RuntimeError("database is gone")

        original = self.db.connect
        self.db.connect = boom
        try:
            self.assertEqual(self.mod.flush(), 0)
        finally:
            self.db.connect = original

        self.assertEqual(self.mod.stats()["buffered"], 1)
        self.mod.flush()
        self.assertEqual(self.rows()[0][4], 1)

    def test_buffer_is_bounded(self):
        """This buffer must never be the thing that exhausts a gunicorn worker."""
        self.mod.MAX_BUFFER_KEYS = 5
        try:
            for i in range(20):
                self.mod.record(f"/rule/{i}", "GET", self.mod.CLIENT_WEB)
            self.assertLessEqual(self.mod.stats()["buffered"], 5)
            self.assertGreater(self.mod.stats()["dropped"], 0)
        finally:
            self.mod.MAX_BUFFER_KEYS = 20000
            self.mod.flush()

    def test_record_never_raises(self):
        """A telemetry hook that can 500 the route it measures has made the
        product worse in order to learn something about it."""
        for args in ((None, None, None), (object(), object(), object())):
            self.mod.record(*args)

    def test_schema_creation_is_idempotent(self):
        conn = self.db.connect()
        for _ in range(3):
            self.mod.ensure_schema(conn)
        conn.commit()
        conn.close()


class RouteHitPrivacy(unittest.TestCase):
    """What the table is allowed to hold."""

    def test_module_records_the_rule_not_the_path(self):
        import inspect
        from services import route_hit_log
        source = inspect.getsource(route_hit_log)
        self.assertNotIn("request.path", source)
        for column in ("user_id", "ip", "session", "email", "user_agent"):
            self.assertNotIn(
                f" {column} ", source.split("ensure_schema")[-1],
                f"{column} must not be a column: this table is retained for a "
                f"week across production traffic and is keyed by url rule only",
            )


if __name__ == "__main__":
    unittest.main()
