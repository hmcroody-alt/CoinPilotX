"""The wave-1 endpoint, driven as real HTTP.

``tests/pulse_control_plane/test_control_plane_wave_1.py`` proves the module:
that arming takes two independent facts, that the ten wave-1 capabilities
resolve identically under both engines, that scope is an allowlist. None of that
says the endpoint in ``bot.py`` is wired to it, and the wiring is the entire
content of wave 1 — the mission is "let the request path consult the control
plane at all", so a test of the plane alone tests everything except the thing
being shipped.

This file lives at the top level of ``tests/`` rather than beside the rest of the
control-plane suite for a mechanical reason worth stating so nobody helpfully
moves it: the ``capability-drift`` CI job installs **pytest and nothing else**,
and that narrowness is load-bearing — if its install step ever needs to grow, a
module in the package has acquired a dependency and somebody should notice. A
file that imports ``bot`` inside ``tests/pulse_control_plane/`` would break that
job, so the two suites are kept on opposite sides of the import.

What the endpoint must *not* do is most of what is asserted here. It grants
nothing and gates nothing; the routes behind these ten capabilities still run
their own checks, unchanged. So the tests below spend their effort on the two
confusions that would actually hurt:

  * **"no opinion" read as denial.** An unarmed process returns an empty
    ``capabilities`` dict, and so would a process that denied everything. The
    payload distinguishes them with ``consulted``, and ``ok`` stays true, because
    not being asked is not an error.
  * **scope read as permission.** A key absent from ``capabilities`` may be out
    of the wave or may be denied. ``scope`` is returned so the client can tell,
    and it is pinned here to the ten keys — with ``marketplace_checkout``
    asserted absent separately, since that row is the one whose wiring withdraws
    checkout from every non-admin.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="pulse_capabilities_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import route_auth  # noqa: E402
from services.pulse_control_plane import capabilities as capability_registry  # noqa: E402
from services.pulse_control_plane import runtime  # noqa: E402


def _use_module_database():
    """Re-point the process at this module's temp database.

    ``services.db`` resolves ``DATABASE_URL`` per connection and ``init_db``
    short-circuits on ``INIT_DB_COMPLETED``, so a module collected before this
    one leaves both pointing somewhere else. Re-asserting per test makes the
    file independent of collection order.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.PULSE_MESSENGER_SCHEMA_READY = False
    bot.init_db()


def _registry_rows(**overrides):
    """The ``feature_flags`` content a correctly migrated production would hold.

    Built from the capability registry rather than typed out, because a
    hand-typed copy would make this suite assert that the endpoint agrees with
    *this file* instead of with the registry. ``overrides`` exists for the one
    test that needs a row to disagree.
    """
    rows = {
        cap.key: {
            "deployment_state": cap.deployment_state,
            "eligibility_policy": cap.eligibility.key,
        }
        for cap in capability_registry.CAPABILITIES
    }
    for key, row in overrides.items():
        rows[key] = row
    return rows


class PulseCapabilitiesRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        runtime.reset_for_tests()
        self._real_account_user_id = bot.account_user_id
        self._real_admin_current_user = bot.admin_current_user

    def tearDown(self):
        bot.account_user_id = self._real_account_user_id
        bot.admin_current_user = self._real_admin_current_user
        runtime.reset_for_tests()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @contextmanager
    def signed_in(self, user_id=4242):
        previous_account, previous_admin = bot.account_user_id, bot.admin_current_user
        bot.account_user_id = lambda *a, **k: user_id
        bot.admin_current_user = lambda *a, **k: None
        try:
            yield user_id
        finally:
            bot.account_user_id, bot.admin_current_user = previous_account, previous_admin

    def get_payload(self, expect_status=200):
        resp = self.client.get("/api/pulse/capabilities")
        self.assertEqual(
            resp.status_code,
            expect_status,
            resp.get_data(as_text=True)[:400],
        )
        return resp.get_json() or {}

    def arm_for_test(self, rows=None):
        result = runtime.arm(
            rows if rows is not None else _registry_rows(),
            environ={runtime.CONSULTATION_ENV: "1"},
        )
        return result

    # ------------------------------------------------------------------
    # authentication
    # ------------------------------------------------------------------
    def test_anonymous_request_is_refused_even_when_armed(self):
        """Arming must not become a way in.

        The consultation is a *read* of the control plane, and it is tempting to
        treat a read of something that grants nothing as harmless to expose. It
        is not harmless: the payload is keyed by subject, so an open endpoint
        would answer questions about whoever the server thought was asking. The
        401 is asserted with the process armed precisely because that is the
        configuration in which someone might argue it could be relaxed.
        """
        self.arm_for_test()
        bot.account_user_id = lambda *a, **k: 0
        bot.admin_current_user = lambda *a, **k: None
        payload = self.get_payload(expect_status=401)
        self.assertEqual(payload.get("error_code"), "auth_required")
        self.assertNotIn("capabilities", payload)

    def test_route_declares_its_auth(self):
        """The declaration, not the detector, is the security mechanism.

        ``services/route_auth.py`` is default-deny for new routes: a route that
        declares nothing fails the protection suite. Pinning the declaration
        here as well means a change that strips the decorator fails in the file
        about this endpoint, next to the reason, rather than only in an audit
        whose output is a list of route names.
        """
        view = bot.webhook_app.view_functions["api_pulse_capabilities"]
        declaration = getattr(view, route_auth.DECLARATION_ATTR, None)
        self.assertIsNotNone(declaration, "the route declares no auth at all")
        self.assertEqual(declaration.get("kind"), route_auth.AUTH_USER)

    # ------------------------------------------------------------------
    # unarmed: no opinion
    # ------------------------------------------------------------------
    def test_unarmed_process_reports_no_opinion_and_not_a_denial(self):
        """``consulted: false`` with an empty dict, and ``ok`` still true.

        This is the shape a client sees today, since the code ships disarmed.
        Every assertion here is about making "nobody was asked" impossible to
        misread as "the answer was no" — an empty ``capabilities`` looks
        identical either way, so ``consulted`` carries the difference, and a
        populated ``scope`` proves the endpoint still knows what wave 1 covers
        while declining to answer about it.
        """
        with self.signed_in():
            payload = self.get_payload()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["consulted"])
        self.assertEqual(payload["capabilities"], {})
        self.assertEqual(payload["wave"], 1)
        self.assertEqual(sorted(payload["scope"]), sorted(runtime.WAVE_1_KEYS))

    def test_a_process_that_never_armed_matches_one_that_armed_and_refused(self):
        """Both are "no opinion" to a client, and must look identical.

        ``runtime.arming()`` distinguishes them — ``None`` means nobody decided,
        a disarmed result means somebody did — and that distinction belongs in
        the logs, where an operator can act on it. Leaking it into the payload
        would invite a client to treat "armed and refused" as a signal about the
        user, which it is not: it is a signal about the database.
        """
        with self.signed_in():
            never_armed = self.get_payload()
            self.arm_for_test(_registry_rows(pulse_posts={
                "deployment_state": "DISABLED",
                "eligibility_policy": "STANDARD",
            }))
            armed_and_refused = self.get_payload()
        self.assertIsNotNone(runtime.arming(), "arm() should have recorded a decision")
        self.assertFalse(runtime.arming().armed)
        self.assertTrue(runtime.arming().blocked_by_disagreement)
        self.assertEqual(never_armed, armed_and_refused)

    def test_one_disagreeing_row_withdraws_the_whole_wave_not_just_that_row(self):
        """Verification is all-or-nothing, and the endpoint must show that.

        Answering about the nine rows that did agree would be the more helpful
        behaviour and the wrong one: a table with one unexplained row is a table
        nobody reconciled, and the value of the wave-1 gate is that it refuses
        on evidence about the table rather than filtering row by row.
        """
        self.arm_for_test(_registry_rows(pulse_reels={
            "deployment_state": "LIVE_GLOBAL",
            "eligibility_policy": "PREMIUM",
        }))
        with self.signed_in():
            payload = self.get_payload()
        self.assertFalse(payload["consulted"])
        self.assertEqual(payload["capabilities"], {})
        self.assertNotIn("pulse_posts", payload["capabilities"])

    # ------------------------------------------------------------------
    # armed: the answer wave 1 exists to produce
    # ------------------------------------------------------------------
    def test_armed_response_answers_for_every_wave_1_capability(self):
        self.arm_for_test()
        with self.signed_in():
            payload = self.get_payload()
        self.assertTrue(payload["consulted"])
        self.assertEqual(sorted(payload["capabilities"]), sorted(runtime.WAVE_1_KEYS))
        for key, decision in payload["capabilities"].items():
            self.assertTrue(decision["visible"], key)
            self.assertTrue(decision["usable"], key)
            self.assertEqual(decision["reason_code"], "AVAILABLE", key)
            self.assertEqual(decision["deployment_state"], "LIVE_GLOBAL", key)

    def test_arming_changes_the_response_and_nothing_else_does(self):
        """Non-vacuity: the armed and unarmed payloads must actually differ.

        Every assertion above would survive an endpoint that ignored the runtime
        entirely and returned a constant. Comparing the two payloads from the
        same signed-in client, with arming as the only change between them, is
        what rules that out.
        """
        with self.signed_in():
            before = self.get_payload()
            self.arm_for_test()
            after = self.get_payload()
        self.assertNotEqual(before["capabilities"], after["capabilities"])
        self.assertEqual(before["scope"], after["scope"])
        self.assertEqual(before["wave"], after["wave"])
        self.assertEqual(before["model_version"], after["model_version"])

    def test_model_version_is_reported_so_a_stale_client_is_detectable(self):
        self.arm_for_test()
        with self.signed_in():
            payload = self.get_payload()
        self.assertEqual(payload["model_version"], runtime.model_version())
        self.assertTrue(payload["model_version"])

    # ------------------------------------------------------------------
    # scope
    # ------------------------------------------------------------------
    def test_checkout_is_never_in_the_response_armed_or_not(self):
        """Pinned on its own, and not as a consequence of the scope assertion.

        ``marketplace_checkout`` is seeded ``internal-only`` while production has
        taken real orders through it, so wiring that row as written removes
        checkout from every non-admin. It is wave 3 for that reason. A test that
        only compared ``scope`` to ``WAVE_1_KEYS`` would follow a careless edit
        to the allowlist straight into green; this one cannot.
        """
        self.arm_for_test()
        with self.signed_in():
            payload = self.get_payload()
        for forbidden in ("marketplace_checkout", "premium_identity", "premium_advanced_tools", "admin_command", "pulse_livestream"):
            self.assertNotIn(forbidden, payload["scope"], forbidden)
            self.assertNotIn(forbidden, payload["capabilities"], forbidden)

    def test_scope_is_reported_even_when_nothing_is_answered(self):
        """So "not in this wave" and "denied" stay distinguishable.

        Both would otherwise present as an absent key. A client that cannot tell
        them apart has to guess, and the safe guess — treat absence as denial —
        is exactly the reading that turns a quiet wave into an outage.
        """
        with self.signed_in():
            payload = self.get_payload()
        self.assertFalse(payload["consulted"])
        self.assertEqual(len(payload["scope"]), 10)
        self.assertIn("pulse_posts", payload["scope"])


if __name__ == "__main__":
    unittest.main()
