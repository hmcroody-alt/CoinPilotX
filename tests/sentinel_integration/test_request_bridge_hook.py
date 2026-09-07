"""The Sentinel request-bridge hook, against the real Flask app — Stage 3.

Kept out of ``tests/sentinel/`` on purpose: importing ``bot`` costs a full boot
and sets a process-wide ``DATABASE_URL``, so this runs as its own process, the
same way ``tests/admin_auth/test_pre_auth_gateway.py`` does.

Unit tests cannot answer the questions here, and they are the ones that decide
whether Stage 3 is real:

* **Is the hook on the app that actually serves traffic?** ``bot.py`` assigns
  ``webhook_app = Flask(...)`` twice (lines 384 and 1130) and the second wins,
  discarding anything attached to the first. A decorator can therefore register
  a hook onto an object nobody ever calls, and every unit test still passes.
* **Does an observed status reach it?** ``/api/`` paths are excluded from the
  visitor-log hook for cost reasons; a copied exclusion here would silently
  blind the bridge to almost all native traffic.
* **Does observing change what the client receives?** Hard Rule #3: the shipped
  binary is frozen, so a byte the observer adds is a byte the client did not
  expect.

Run: python3 -m pytest tests/sentinel_integration/test_request_bridge_hook.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="sentinel_bridge_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services.sentinel import bootstrap, request_bridge, store  # noqa: E402

# A route that returns an observed status without any authentication set-up.
# Asserted below rather than assumed — if it stops returning 401 the tests that
# depend on it become vacuous.
UNAUTHENTICATED_API = "/api/pulse/feed"
ORDINARY_PAGE = "/"


class RequestBridgeHookTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        bootstrap.ensure_schema(force=True)
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        os.environ["SENTINEL_REQUEST_BRIDGE_ENABLED"] = "1"
        request_bridge.reset_for_tests()
        # The events table is process-wide and persists across tests, so
        # "how many events did THIS request produce" is only answerable from an
        # empty table. Without this every count assertion drifts upward with
        # test order and the file passes or fails depending on how it is run.
        self._truncate_events()
        # The flush worker is a thread racing every buffer assertion here. It
        # has its own coverage in tests/sentinel/test_request_bridge.py; these
        # tests drive flush() explicitly so the counts are deterministic.
        self._real_ensure_worker = request_bridge._ensure_worker
        request_bridge._ensure_worker = lambda: None

    def tearDown(self):
        request_bridge._ensure_worker = self._real_ensure_worker
        request_bridge.reset_for_tests()
        os.environ.pop("SENTINEL_REQUEST_BRIDGE_ENABLED", None)

    @staticmethod
    def _truncate_events():
        conn = store.platform_db.connect()
        try:
            conn.cursor().execute("DELETE FROM sentinel_events")
            conn.commit()
        finally:
            conn.close()

    def rows(self):
        request_bridge.flush()
        conn = store.platform_db.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT category, event_type, actor_id, actor_type, "
                        "payload_json, source_trust, network_ref "
                        "FROM sentinel_events ORDER BY id")
            return [tuple(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # --- registration ------------------------------------------------------

    def test_hook_is_registered_on_the_app_that_serves_traffic(self):
        names = [f.__name__ for f in bot.webhook_app.after_request_funcs.get(None, [])]
        self.assertIn("sentinel_observe_security_response", names)
        self.assertIs(bot.app, bot.webhook_app)

    def test_the_probe_route_really_returns_an_observed_status(self):
        """Anti-vacuity anchor. Every emission test below rests on this."""
        status = self.client.get(UNAUTHENTICATED_API).status_code
        self.assertEqual(status, 401)
        self.assertIn(status, bot.SENTINEL_OBSERVED_STATUSES)

    # --- observation -------------------------------------------------------

    def test_an_api_401_produces_one_event(self):
        self.client.get(UNAUTHENTICATED_API)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        category, event_type, actor_id, actor_type, payload, trust, _ = rows[0]
        self.assertEqual(category, "AUTH")
        self.assertEqual(event_type, "request.unauthenticated")
        self.assertEqual(actor_type, "DEVICE")
        self.assertTrue(actor_id.startswith("device:"))
        self.assertEqual(trust, "AUTHORITATIVE")
        self.assertIn(UNAUTHENTICATED_API, payload)

    def test_api_paths_are_not_excluded_the_way_the_visitor_log_excludes_them(self):
        """``log_visitor`` skips ``/api/`` because it writes per request. The
        bridge buffers instead, so it must NOT inherit that exclusion — the
        native app drives almost all of its traffic through ``/api/``."""
        self.client.get(UNAUTHENTICATED_API)
        self.assertEqual(request_bridge.stats()["emitted"], 1)

    def test_ordinary_traffic_produces_nothing(self):
        """The pair that stops the test above passing for a hook that fires on
        everything. A 200 firehose must not become a Sentinel firehose."""
        r = self.client.get(ORDINARY_PAGE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(request_bridge.stats()["emitted"], 0)

    def test_repeated_failures_are_counted_individually(self):
        """The brute-force signal is volume. Twenty attempts in one second must
        be twenty events, not one collapsed by the default dedupe key."""
        for _ in range(20):
            self.client.get(UNAUTHENTICATED_API)
        self.assertEqual(len(self.rows()), 20)

    # --- the switch --------------------------------------------------------

    def test_switched_off_the_hook_is_inert(self):
        os.environ.pop("SENTINEL_REQUEST_BRIDGE_ENABLED", None)
        self.client.get(UNAUTHENTICATED_API)
        self.assertEqual(request_bridge.stats()["emitted"], 0)
        self.assertEqual(self.rows(), [])

    # --- Hard Rule #3: the shipped client must not notice ------------------

    def test_observation_does_not_alter_the_response(self):
        """Hard Rule #3: the shipped binary is frozen, so a byte the observer
        adds is a byte the client never expected.

        Some headers differ between any two identical requests on their own
        (``X-Trace-Id``, timing). Rather than hardcode an ignore-list — which
        would quietly grow until it covered a real regression — the test
        measures which headers are volatile by issuing the same request twice
        with the bridge OFF, then requires equality everywhere else.
        """
        os.environ.pop("SENTINEL_REQUEST_BRIDGE_ENABLED", None)
        first = self.client.get(UNAUTHENTICATED_API)
        second = self.client.get(UNAUTHENTICATED_API)
        volatile = {k for k in dict(first.headers)
                    if dict(first.headers).get(k) != dict(second.headers).get(k)}

        baseline_headers = dict(second.headers)
        baseline_body = second.get_data()
        baseline_status = second.status_code

        os.environ["SENTINEL_REQUEST_BRIDGE_ENABLED"] = "1"
        after = self.client.get(UNAUTHENTICATED_API)
        after_headers = dict(after.headers)

        self.assertEqual(after.status_code, baseline_status)
        self.assertEqual(after.get_data(), baseline_body)
        self.assertEqual(set(after_headers), set(baseline_headers),
                         "the observer added or removed a header")
        for name, value in baseline_headers.items():
            if name in volatile:
                continue
            self.assertEqual(after_headers[name], value,
                             f"the observer changed header {name}")
        # The comparison must not have been vacuous: something stable had to
        # be compared. If every header were volatile this would prove nothing.
        self.assertTrue(set(baseline_headers) - volatile,
                        "no stable header was actually compared")
        # And the bridge must genuinely have run for this request.
        self.assertEqual(request_bridge.stats()["emitted"], 1)

    def test_a_broken_bridge_cannot_break_the_request(self):
        """A security observer that can 500 the product is a worse security
        outcome than no observer at all."""
        original = request_bridge.build_request_event

        def explode(**_kw):
            raise RuntimeError("bridge is on fire")

        request_bridge.build_request_event = explode
        try:
            r = self.client.get(UNAUTHENTICATED_API)
            self.assertEqual(r.status_code, 401)
        finally:
            request_bridge.build_request_event = original

    # --- the amplification guarantee, measured -----------------------------

    def test_observing_costs_no_database_connections_on_the_request_path(self):
        """The whole reason the bridge buffers.

        ``bot.db()`` opens a fresh connection per call, so an inline emit would
        convert each attacker request into a connection exactly when the
        database can least afford one.
        """
        opened = {"n": 0}
        real_connect = store.platform_db.connect

        def counting_connect(*a, **kw):
            opened["n"] += 1
            return real_connect(*a, **kw)

        store.platform_db.connect = counting_connect
        try:
            for _ in range(50):
                self.client.get(UNAUTHENTICATED_API)
            self.assertEqual(request_bridge.stats()["pending"], 50)
            self.assertEqual(opened["n"], 0, "observation reached the database")
        finally:
            store.platform_db.connect = real_connect

    def test_the_hook_never_rotates_a_session(self):
        """``account_user_id()`` falls through to
        ``restore_account_from_persistent_cookie()``, which rotates the user's
        refresh token. If the hook ever calls it, logging a 401 starts churning
        token families — a security log with a side effect on sessions."""
        called = {"n": 0}
        original = bot.restore_account_from_persistent_cookie

        def tripwire(*a, **kw):
            called["n"] += 1
            return original(*a, **kw)

        bot.restore_account_from_persistent_cookie = tripwire
        try:
            before = called["n"]
            self.client.get(UNAUTHENTICATED_API)
            after_request_calls = called["n"] - before
        finally:
            bot.restore_account_from_persistent_cookie = original

        # Other hooks legitimately resolve the account; what must not happen is
        # the bridge adding a rotation of its own. Compare against the same
        # request with the bridge switched off.
        os.environ.pop("SENTINEL_REQUEST_BRIDGE_ENABLED", None)
        bot.restore_account_from_persistent_cookie = tripwire
        try:
            before = called["n"]
            self.client.get(UNAUTHENTICATED_API)
            baseline_calls = called["n"] - before
        finally:
            bot.restore_account_from_persistent_cookie = original

        self.assertEqual(after_request_calls, baseline_calls,
                         "the bridge added a session-rotating lookup")


if __name__ == "__main__":
    unittest.main()
