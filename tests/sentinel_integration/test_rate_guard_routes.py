"""The Stage 6 limiter against the real Flask app.

``tests/sentinel/test_rate_limit.py`` proves the counter is shared and atomic.
It cannot prove the thing that decides whether Stage 6 is real rather than
decorative: **is the shared counter actually on a route, and does the route
return a status the shipped client understands?**

A module with a perfect test suite and no call sites is exactly the condition
the Stage 0 map found Sentinel in — 55 modules, ~11k lines, and
``grep -c sentinel bot.py`` returning 0. Adding a fifteenth such module would
have been the mission going wrong. So these tests drive HTTP.

What this file had to be rewritten to test
------------------------------------------

The first version of these tests wired the limiter into three routes by hand and
failed, because ``bot.basic_abuse_guard`` — a ``before_request`` this mission's
Stage 0 map missed — **already** rate limits all three, with its own deployed
table of eleven paths and their limits. A second policy beside it would have
been the duplicate-security-control failure Hard Rule #6 names. So the guard was
extended rather than bypassed, and these tests target it.

That relocation makes the central assertion subtle, and worth stating plainly:

    In a single process, the distributed limiter and the pre-existing in-process
    bucket refuse the *same* request. One test client is one worker, so counting
    requests here proves nothing about sharing.

The property that distinguishes them only exists across workers. So
:func:`test_this_worker_refuses_because_another_worker_counted` writes what a
*different* gunicorn worker would have written straight into
``sentinel_rate_counters`` and then asserts this worker refuses on a request its
own bucket would happily have allowed. That is the entire point of Stage 6, and
it is false unless the wiring is real.

Kept out of ``tests/sentinel/`` for the same reason as the Stage 3 hook tests:
importing ``bot`` costs a full boot and sets a process-wide ``DATABASE_URL``.

Run: python3 -m pytest tests/sentinel_integration/test_rate_guard_routes.py
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="sentinel_rlroute_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
import services.pulse_security_core as pulse_security_core  # noqa: E402
import services.security_guard as security_guard  # noqa: E402
from services import cache_engine  # noqa: E402
from services.sentinel import bootstrap, rate_limit, store  # noqa: E402

RECOVER = "/api/mobile/auth/recover"
FORGOT = "/forgot-password"

# Statuses the shipped App Store binary already understands (Stage 1 contract).
# Any enforcement added by this mission must express itself only in these.
CLIENT_UNDERSTOOD = {401, 403, 423, 429}


class RateGuardRouteTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        bootstrap.ensure_schema(force=True)
        cls.client = bot.webhook_app.test_client()
        cls.SUBJECT = cls._discover_subject()

    def setUp(self):
        os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = "enforce"
        self._reset_all_limiters()

    def tearDown(self):
        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        self._reset_all_limiters()

    # --- resetting every limiter on these paths, not just the new one ------

    @classmethod
    def _reset_all_limiters(cls):
        """Three pre-existing limiters share this request path. All must be reset.

        Writing this method is how the count went from two to three. The first
        version of these tests cleared only ``bot.RATE_LIMIT_BUCKETS`` and then
        saw ``/api/mobile/auth/recover`` return 429 on a *first* request with a
        body no rate limiter in this mission produces::

            {"ok": false, "message": "Slow down for a moment, then try again.",
             "security_state": "rate_limited", "retry_after": 599}

        That is ``pulse_security_guard`` calling
        ``pulse_security_core.rate_limited`` — a 600-second window on the same
        route, in process memory, leaking state from one test into the next.
        ``security_guard.BUCKETS`` is a fourth on other paths.

        The wider point is not about tests. Four independent limiters now decide
        this one route, three of them per-worker. Stage 21 should collapse them;
        this stage's job was to make the count shared, not to renumber the
        platform's limits on a client that cannot be updated.
        """
        rate_limit.reset_for_tests()
        bot.RATE_LIMIT_BUCKETS.clear()
        pulse_security_core._RATE_BUCKETS.clear()
        security_guard.BUCKETS.clear()
        cache_engine._MEMORY.clear()
        cls._truncate()

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _truncate():
        conn = store.platform_db.connect()
        try:
            conn.cursor().execute("DELETE FROM sentinel_rate_counters")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _counter_rows():
        conn = store.platform_db.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT scope, subject, window_start, hits "
                        "FROM sentinel_rate_counters ORDER BY id")
            return cur.fetchall()
        finally:
            conn.close()

    @staticmethod
    def effective_limit(path):
        """The number of requests this route really allows one worker.

        Not the number ``basic_abuse_guard`` states. Several of its paths are
        also covered by ``pulse_security_core.HIGH_RISK_RATE_RULES`` with a
        different, sometimes stricter limit, and the strictest one is what a
        caller actually meets. A test that assumed the stated number would fail
        for a reason that has nothing to do with this mission.
        """
        stated, _ = bot.ABUSE_GUARD_PROTECTED[path]
        rule = pulse_security_core.rate_rule_for(path, "POST")
        return min(stated, rule.limit) if rule else stated

    def post_recover(self, n=1):
        return [self.client.post(RECOVER, json={"email": "nobody@example.com"})
                for _ in range(n)]

    @classmethod
    def _discover_subject(cls):
        """Read the subject the guard actually writes, rather than recomputing it.

        This was the second finding of the rewrite. The obvious version built the
        subject with ``bot.client_ip_hash()`` inside a ``test_request_context``
        — and that context has no ``REMOTE_ADDR``, so ``client_ip_hash()``
        returns ``""`` and the subject came out as the literal ``"ip:"``. Every
        priming write landed on a row no request would ever read, and the tests
        that appeared to pass were passing on a *different* limiter's 429.

        Deriving the value from a real request through the real client removes
        the entire class of mistake: if this returns the wrong subject, the
        priming has no effect and the tests fail loudly instead of quietly
        measuring something else.
        """
        os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = "shadow"
        cls._truncate()
        rate_limit.reset_for_tests()
        cls.client.post(RECOVER, json={"email": "probe@example.com"})
        rows = cls._counter_rows()
        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        cls._truncate()
        assert rows, "the guard wrote no counter row — the wiring is not live"
        subjects = {row[1] for row in rows}
        assert len(subjects) == 1, subjects
        subject = subjects.pop()
        assert subject.startswith("ip:") and len(subject) > 3, subject
        return subject

    def prime_other_worker(self, path, hits):
        """Write what a *different* gunicorn worker would have written.

        This is the whole trick. A second worker's counting is invisible to this
        process except through the shared table, so writing the row directly is
        a faithful stand-in for it — and it is the only way to observe, from one
        test process, the behaviour that four production workers produce.
        """
        _limit, window_seconds = bot.ABUSE_GUARD_PROTECTED[path]
        window_start = int(time.time() // window_seconds) * window_seconds
        conn = store.platform_db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sentinel_rate_counters "
                "(scope, subject, window_start, hits, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (scope, subject, window_start) "
                "DO UPDATE SET hits = ?",
                (path, self.SUBJECT, window_start, hits, "2026-01-01 00:00:00",
                 hits))
            conn.commit()
        finally:
            conn.close()
        return self.SUBJECT, window_start

    # =====================================================================
    # The property Stage 6 exists for
    # =====================================================================

    def test_this_worker_refuses_because_another_worker_counted(self):
        """The one assertion that is false for a per-process limiter.

        This process's own bucket has seen a single request — nowhere near the
        limit — so today's code allows it. It is refused only because the shared
        counter carries what other workers already saw. Delete the
        ``sentinel_rate_refused`` call from ``basic_abuse_guard`` and this test
        fails; nothing else in this file does that.
        """
        limit, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        self.prime_other_worker(RECOVER, limit)

        first = self.post_recover(1)[0]

        self.assertEqual(first.status_code, 429, first.get_data(as_text=True)[:300])
        # ...and this worker's own bucket never came close to the limit, so the
        # pre-existing guard cannot be what refused it.
        local = [stamps for key, stamps in bot.RATE_LIMIT_BUCKETS.items()
                 if key.endswith(RECOVER)]
        self.assertTrue(all(len(stamps) < limit for stamps in local), local)

    def test_the_in_process_bucket_alone_would_have_allowed_it(self):
        """Anti-vacuity partner: proves the test above is not passing because the
        route was already broken or already refusing everything.

        Identical priming, mode off. If this returned 429 too, the assertion
        above would be measuring the pre-existing guard rather than the new one.
        """
        limit, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        self.prime_other_worker(RECOVER, limit)

        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        first = self.post_recover(1)[0]

        self.assertEqual(first.status_code, 200)
        self.assertEqual(rate_limit.stats()["checks"], 0)

    def test_one_abusive_network_does_not_lock_out_everyone_else(self):
        """The subject must be per-caller, and nothing else in this file checks it.

        Found by mutation, not by reading: replacing the subject with a constant
        left all twelve other tests green, because they all speak from one IP. A
        constant subject is not a subtle bug — it is a limiter that lets any
        single attacker take the login and password-recovery endpoints away from
        every user of the platform at once, by design, with the security tests
        passing. The blast radius is the opposite of the one intended.
        """
        limit, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        self.prime_other_worker(RECOVER, limit)

        # The primed network is refused...
        self.assertEqual(self.post_recover(1)[0].status_code, 429)

        # ...and an unrelated one is not.
        innocent = self.client.post(
            RECOVER, json={"email": "elsewhere@example.com"},
            environ_base={"REMOTE_ADDR": "203.0.113.7"})
        self.assertEqual(innocent.status_code, 200,
                         "one network's abuse refused a different network")

        subjects = {row[1] for row in self._counter_rows()}
        self.assertIn(self.SUBJECT, subjects)
        self.assertGreaterEqual(len(subjects), 2,
                                f"both callers were counted as one subject: {subjects}")

    def test_shadow_sees_the_other_worker_without_refusing_anyone(self):
        limit, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        self.prime_other_worker(RECOVER, limit)

        os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = "shadow"
        first = self.post_recover(1)[0]

        self.assertEqual(first.status_code, 200, "shadow mode changed a response")
        stats = rate_limit.stats()
        self.assertGreaterEqual(stats["shadow_limited"], 1)
        self.assertEqual(stats["enforced_blocks"], 0)

    # =====================================================================
    # The guard is genuinely on the routes
    # =====================================================================

    def test_every_protected_path_reaches_the_shared_counter(self):
        """All eleven deployed paths, not the three this mission first picked.

        Reusing ``basic_abuse_guard`` instead of adding routes of my own is what
        made this cheap; a per-route registry would have covered three and left
        eight of the platform's own security-critical paths still counting per
        worker.
        """
        posted = 0
        for path in sorted(bot.ABUSE_GUARD_PROTECTED):
            rate_limit.reset_for_tests()
            bot.RATE_LIMIT_BUCKETS.clear()
            self._truncate()
            self.client.post(path, json={}, data=None)
            scopes = {row[0] for row in self._counter_rows()}
            self.assertIn(path, scopes,
                          f"{path} is in the protected table but never counted")
            posted += 1
        self.assertEqual(posted, len(bot.ABUSE_GUARD_PROTECTED))
        self.assertGreaterEqual(posted, 11)

    def test_the_web_form_shares_the_api_limit(self):
        """A counter an attacker could reset by switching client would not be a
        limit, it would be a suggestion.

        ``/forgot-password`` and ``/api/mobile/auth/recover`` are separate scopes
        by design — they are separate paths in the deployed table — but each must
        genuinely reach the shared store, and each must answer in the shape its
        own caller expects.
        """
        limit, _ = bot.ABUSE_GUARD_PROTECTED[FORGOT]
        self.prime_other_worker(FORGOT, limit)

        page = self.client.post(FORGOT, data={"email": "x@example.com"})
        self.assertEqual(page.status_code, 429)
        # HTML, not the API's JSON — the shape must match the client that asked.
        self.assertNotIn("application/json", page.headers.get("Content-Type", ""))

    # =====================================================================
    # Hard Rule #3: only codes the shipped binary understands
    # =====================================================================

    def test_enforcement_uses_only_client_understood_statuses(self):
        limit, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        self.prime_other_worker(RECOVER, limit)
        responses = self.post_recover(3)
        refused = [r for r in responses if r.status_code != 200]
        self.assertTrue(refused)
        for r in refused:
            self.assertIn(r.status_code, CLIENT_UNDERSTOOD)

    def test_the_429_body_matches_the_shape_this_route_already_returned(self):
        """The distributed refusal must be indistinguishable from the one this
        route has always sent, or the frozen client meets a body it has never
        parsed (Hard Rule #3)."""
        limit, window = bot.ABUSE_GUARD_PROTECTED[RECOVER]

        # What the pre-existing in-process guard sends, with Sentinel off.
        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        legacy = self.post_recover(limit + 1)[-1]
        self.assertEqual(legacy.status_code, 429)

        rate_limit.reset_for_tests()
        bot.RATE_LIMIT_BUCKETS.clear()
        self._truncate()

        # What the distributed guard sends.
        os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = "enforce"
        self.prime_other_worker(RECOVER, limit)
        distributed = self.post_recover(1)[0]

        self.assertEqual(distributed.status_code, legacy.status_code)
        self.assertEqual(distributed.get_json(), legacy.get_json())
        self.assertIs(distributed.get_json()["ok"], False)
        # The only permitted difference is a header the old refusal omitted; a
        # header cannot break a client that does not read it.
        self.assertIsNone(legacy.headers.get("Retry-After"))
        self.assertLessEqual(int(distributed.headers["Retry-After"]), window)

    def test_a_429_leaks_nothing_about_whether_the_account_exists(self):
        """These endpoints answer identically for known and unknown addresses on
        purpose. A limiter keyed on the email would undo that, and would also let
        an attacker lock a named victim out of their own recovery."""
        limit, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]

        self.prime_other_worker(RECOVER, limit)
        blocked_unknown = self.client.post(
            RECOVER, json={"email": "definitely-unknown@example.com"})

        rate_limit.reset_for_tests()
        bot.RATE_LIMIT_BUCKETS.clear()
        self._truncate()
        self.prime_other_worker(RECOVER, limit)
        blocked_other = self.client.post(
            RECOVER, json={"email": "someone-else@example.com"})

        self.assertEqual(blocked_unknown.status_code, blocked_other.status_code)
        self.assertEqual(blocked_unknown.get_json(), blocked_other.get_json())

        # And the stored subject is the network, not the address.
        subjects = [row[1] for row in self._counter_rows()]
        self.assertTrue(subjects)
        for subject in subjects:
            self.assertTrue(subject.startswith("ip:"), subject)
            self.assertNotIn("@", subject)

    # =====================================================================
    # Default OFF
    # =====================================================================

    def test_off_by_default_changes_nothing_about_this_route(self):
        """The paired test that stops every assertion above from passing merely
        because the routes were already broken."""
        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        codes = [r.status_code for r in self.post_recover(self.effective_limit(RECOVER))]
        self.assertEqual(set(codes), {200}, codes)
        self.assertEqual(rate_limit.stats()["checks"], 0)
        self.assertEqual(self._counter_rows(), [])

    def test_off_still_enforces_the_pre_existing_per_worker_limit(self):
        """Sentinel being off must not have *removed* a control while adding one.

        The refactor that extracted ``rate_limit_refusal`` touched the existing
        refusal path. This is the test that would catch it having broken.
        """
        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        effective = self.effective_limit(RECOVER)
        codes = [r.status_code for r in self.post_recover(effective + 2)]
        self.assertNotIn(429, codes[:effective])
        self.assertEqual(codes[effective:], [429, 429], codes)

    def test_the_older_guards_limit_is_unreachable_on_this_route(self):
        """A finding this stage turned up, recorded rather than papered over.

        ``basic_abuse_guard`` says 6-per-300s for ``/api/mobile/auth/recover``.
        It never fires. ``pulse_security_core.HIGH_RISK_RATE_RULES`` says
        5-per-600s for the same path, and although ``basic_abuse_guard`` is
        registered first and therefore *checks* first, it allows request 6 (its
        bucket holds 5, which is under 6) and hands it straight to the stricter
        guard, which refuses. By request 7 — where ``basic_abuse_guard`` would
        finally have refused — the caller is already being turned away.

        So one of the two numbers configured for this route is decorative, and
        nothing in either file says which. That is not a bug this stage should
        fix: changing either limit changes what real users of a frozen client
        experience, and this mission's job here was to make the count shared
        rather than to renumber the platform. It is written down so Stage 21 can
        collapse these deliberately instead of discovering it again.
        """
        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        stated, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        responses = self.post_recover(stated + 1)
        first_refusal = next(r for r in responses if r.status_code == 429)

        self.assertLess(responses.index(first_refusal), stated,
                        "basic_abuse_guard's limit is reachable after all — "
                        "this finding is stale, re-check the guard ordering")
        # And the refusal is the older guard's, identifiable by its body.
        self.assertEqual(first_refusal.get_json().get("security_state"),
                         "rate_limited")

    def test_turning_the_shared_counter_on_inverts_which_limit_is_decorative(self):
        """Stage 21. The finding above is true of one configuration, not of the
        route, and the distinction is the thing worth writing down.

        ``test_the_older_guards_limit_is_unreachable_on_this_route`` measures a
        single process with the shared counter off. Both of those conditions do
        work in the result. ``basic_abuse_guard``'s 6-per-300s is counted
        *fleet-wide* once the counter is enforcing, while
        ``pulse_security_core``'s 5-per-600s stays in one worker's memory — so
        across four workers the older guard needs about twenty-four requests to
        see six on any one of them, and the shared limit binds first at the
        seventh request to the fleet.

        Which guard is decorative therefore flips when an operator turns the
        switch on. That matters beyond bookkeeping: it means the two numbers
        cannot be reconciled by reading them, because neither file is wrong in
        every configuration, and an operator enabling the distributed limiter is
        also silently changing which policy is authoritative for this route.

        Nothing is renumbered here. This test exists so that whoever finally
        collapses these four limiters does it knowing both answers.
        """
        stated, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        rule = pulse_security_core.rate_rule_for(RECOVER, "POST")
        self.assertIsNotNone(rule, "the older guard no longer covers this route")
        self.assertLess(rule.limit, stated,
                        "the premise of the original finding is gone: the older "
                        "guard is no longer the stricter of the two")

        os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = "enforce"
        try:
            # Six requests already counted by other workers — at the shared
            # limit, not over it. This worker has served none of them.
            self.prime_other_worker(RECOVER, stated)
            response = self.post_recover(1)[0]

            self.assertEqual(response.status_code, 429,
                             response.get_data(as_text=True)[:300])
            # ``rate_limit_refusal`` carries no ``security_state``; the older
            # guard's body does. This is how the two refusals are told apart.
            self.assertIsNone(response.get_json().get("security_state"),
                              "the older guard refused, so this proves nothing "
                              "about the shared counter")

            # And the older guard was nowhere near firing: it saw one request on
            # this worker against a limit of five.
            older = [stamps for key, stamps in pulse_security_core._RATE_BUCKETS.items()
                     if key.endswith(RECOVER)]
            self.assertTrue(all(len(stamps) < rule.limit for stamps in older), older)
        finally:
            os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)

    def test_with_the_shared_counter_off_the_same_priming_changes_nothing(self):
        """Anti-vacuity partner. Six requests from other workers must be
        invisible while the switch is off, or the test above is measuring a
        route that refuses everyone rather than a limit that became reachable."""
        stated, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        self.prime_other_worker(RECOVER, stated)
        response = self.post_recover(1)[0]
        self.assertNotEqual(response.status_code, 429,
                            "another worker's count reached this request with "
                            "the switch off")

    # =====================================================================
    # Availability
    # =====================================================================

    def test_a_broken_limiter_does_not_break_the_route(self):
        """A limiter that can 500 the endpoint it protects has made the product
        less available, not more secure."""
        original = rate_limit.check

        def explode(*a, **kw):
            raise RuntimeError("limiter is on fire")

        rate_limit.check = explode
        try:
            r = self.client.post(RECOVER, json={"email": "x@example.com"})
            self.assertEqual(r.status_code, 200)
        finally:
            rate_limit.check = original

    def test_the_guard_runs_before_the_route_does_its_work(self):
        """Refusing after the email has already been sent would be a limiter in
        name only — the expensive side effect is the thing being limited."""
        limit, _ = bot.ABUSE_GUARD_PROTECTED[RECOVER]
        self.prime_other_worker(RECOVER, limit)

        calls = {"n": 0}
        original = bot.safe_password_reset_request

        def counting(*a, **kw):
            calls["n"] += 1
            return original(*a, **kw)

        bot.safe_password_reset_request = counting
        try:
            self.post_recover(3)
        finally:
            bot.safe_password_reset_request = original

        self.assertEqual(calls["n"], 0,
                         "the route did its work after being rate limited")


if __name__ == "__main__":
    unittest.main()
