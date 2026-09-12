"""Provider health and the distributed breaker.

Three things here are easy to test vacuously, so each has a paired test whose
job is to prove the harness can fail.

**"The state is shared."** Nothing running inside one interpreter can check
this. A module-level dict and a Postgres row are indistinguishable until a
second OS process exists — which is exactly why the per-process breaker looked
correct for as long as it did. So the sharing tests spend real subprocesses,
and `test_a_process_local_breaker_would_fail_this_harness` runs the same shape
against process-local state to show the harness notices.

**"Exactly one probe."** A single-threaded test claims the probe, sees the
second call denied, and passes — while nine workers each quietly hold their
own. The probe tests race real processes for it and count the winners.

**"Health is truthful."** A state machine that only ever returns HEALTHY passes
any test that asserts HEALTHY. So the state tests assert the *absence* of the
comfortable answer: a provider nobody has called must not read healthy, and a
provider that failed on 402 must not read as something waiting will fix.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from services import undx_health  # noqa: E402


class _Isolated(unittest.TestCase):
    """Each test gets its own SQLite file, so nothing inherits a breaker."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "health.db")
        self._env = mock.patch.dict(
            os.environ, {"DATABASE_URL": "sqlite:///" + self.db_path}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        for name in undx_health.HEALTH_ENV_VARS:
            os.environ.pop(name, None)
        undx_health.reset_for_tests()
        self.addCleanup(undx_health.reset_for_tests)

    def _fail(self, provider="meta", times=1, status="response_failed", error=""):
        for _ in range(times):
            undx_health.record_failure(provider, status, error)


# ------------------------------------------------------------ §19 truthfulness

class HealthStateTest(_Isolated):
    """A provider that never answered must not read as one that did."""

    def test_a_provider_nobody_has_called_is_unknown(self):
        """The specific defect. Claude and Gemini read "Online" for the whole
        period they 404'd every request, because a key was present and no
        switch was off. Configuration was answering a question about health."""
        self.assertIsNone(undx_health.read("meta"))
        self.assertEqual(undx_health.state_for(undx_health.read("meta")),
                         undx_health.UNKNOWN)

    def test_unknown_is_not_merely_unreachable(self):
        """Anti-vacuity: prove UNKNOWN is not what this returns for everything.

        Without this, a `state_for` that returned UNKNOWN unconditionally would
        pass the test above."""
        undx_health.record_success("meta")
        self.assertEqual(undx_health.state_for(undx_health.read("meta")),
                         undx_health.HEALTHY)

    def test_a_row_with_counters_but_no_success_is_still_unknown(self):
        """A provider whose only record is bookkeeping has not been observed
        working, and HEALTHY would be an invention."""
        self.assertEqual(
            undx_health.state_for({"consecutive_failures": 0, "last_success_at": 0.0}),
            undx_health.UNKNOWN)

    def test_each_failure_class_gets_its_own_state(self):
        cases = {
            "http_429": undx_health.RATE_LIMITED,
            "http_401": undx_health.AUTH_FAILED,
            "http_403": undx_health.AUTH_FAILED,
            "http_402": undx_health.BILLING_FAILED,
            "http_500": undx_health.DEGRADED,          # one blip is not an outage
            "timeout": undx_health.DEGRADED,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                row = {"consecutive_failures": 1, "last_status": status,
                       "last_error": "", "last_success_at": 1.0}
                self.assertEqual(undx_health.state_for(row), expected)

    def test_one_blip_is_degraded_and_two_is_unavailable(self):
        """DEGRADED has to mean something, or it is a synonym for down."""
        row = {"consecutive_failures": 1, "last_status": "http_503",
               "last_error": "", "last_success_at": 1.0}
        self.assertEqual(undx_health.state_for(row), undx_health.DEGRADED)
        row["consecutive_failures"] = 2
        self.assertEqual(undx_health.state_for(row), undx_health.UNAVAILABLE)

    def test_a_404_is_only_retirement_when_the_body_says_so(self):
        """A 404 on a chat endpoint is ambiguous: wrong path or wrong model.
        Claiming retirement on the code alone would send someone changing a
        model ID to fix a typo'd URL."""
        bare = {"consecutive_failures": 1, "last_status": "http_404",
                "last_error": "Not Found", "last_success_at": 1.0}
        self.assertEqual(undx_health.state_for(bare), undx_health.UNAVAILABLE)
        named = dict(bare, last_error="The model `muse-spark-1.2` does not exist")
        self.assertEqual(undx_health.state_for(named), undx_health.MODEL_RETIRED)

    def test_retirement_is_detected_when_it_arrives_as_a_400(self):
        """Several providers report a retired model as 400 with the name in the
        body. The evidence is the message either way."""
        row = {"consecutive_failures": 1, "last_status": "http_400",
               "last_error": "invalid model: claude-3-opus-20240229",
               "last_success_at": 1.0}
        self.assertEqual(undx_health.state_for(row), undx_health.MODEL_RETIRED)

    def test_no_state_outside_the_declared_set(self):
        """Anything a dashboard can render has to be in HEALTH_STATES, or the
        set is documentation rather than a contract."""
        statuses = ["success", "timeout", "connection_failed", "response_failed",
                    "request_failed", "http_400", "http_401", "http_402",
                    "http_403", "http_404", "http_429", "http_500", "http_503",
                    "", "something_nobody_wrote"]
        for status in statuses:
            for failures in (0, 1, 2, 9):
                for opened in (0.0, 1.0):
                    row = {"consecutive_failures": failures, "last_status": status,
                           "last_error": "does not exist", "last_success_at": 1.0,
                           "opened_at": opened}
                    with self.subTest(status=status, failures=failures, opened=opened):
                        self.assertIn(undx_health.state_for(row),
                                      undx_health.HEALTH_STATES)
                        self.assertIn(undx_health.failure_state(row),
                                      undx_health.HEALTH_STATES)

    def test_the_circuit_does_not_hide_why_it_opened(self):
        """DeepSeek is at HTTP 402 in this deployment right now.

        A breaker that opened on timeouts heals by waiting. One that opened on
        402 reopens every cooldown until somebody pays an invoice. Reporting
        both as CIRCUIT_OPEN and nothing else routes the page to the wrong
        person, indefinitely.
        """
        self._fail("deepseek", times=undx_health.threshold(),
                   status="http_402", error="Insufficient Balance")
        entry = undx_health.snapshot()["deepseek"]
        self.assertEqual(entry["state"], undx_health.CIRCUIT_OPEN)
        self.assertEqual(entry["underlying_state"], undx_health.BILLING_FAILED)
        self.assertTrue(entry["actionable"])

    def test_a_transient_outage_is_not_reported_as_actionable(self):
        """Anti-vacuity for the test above: `actionable` must not be constant."""
        self._fail("gemini", times=undx_health.threshold(),
                   status="http_503", error="service unavailable")
        entry = undx_health.snapshot()["gemini"]
        self.assertEqual(entry["state"], undx_health.CIRCUIT_OPEN)
        self.assertEqual(entry["underlying_state"], undx_health.UNAVAILABLE)
        self.assertFalse(entry["actionable"])


class ClassifyFailureTest(unittest.TestCase):
    """The HTTP code has to survive the exception, or the states above are
    unreachable in production no matter how well they are computed."""

    def test_the_status_code_is_not_thrown_away(self):
        import requests
        for code in (401, 402, 403, 404, 429, 500):
            with self.subTest(code=code):
                response = mock.Mock(status_code=code)
                exc = requests.HTTPError("boom", response=response)
                self.assertEqual(undx_health.classify_failure(exc), f"http_{code}")

    def test_a_timeout_is_not_a_status_code(self):
        import requests
        self.assertEqual(undx_health.classify_failure(requests.Timeout()),
                         undx_health.STATUS_TIMEOUT)

    def test_a_connection_error_is_distinguished_from_a_refusal(self):
        """DNS failure and HTTP 401 both arrived as RequestException and were
        both recorded as `request_failed`. That erasure is what made a billing
        failure and a network blip look identical on the health surface."""
        import requests
        self.assertEqual(undx_health.classify_failure(requests.ConnectionError()),
                         undx_health.STATUS_CONNECTION)
        response = mock.Mock(status_code=401)
        self.assertNotEqual(
            undx_health.classify_failure(requests.HTTPError("x", response=response)),
            undx_health.classify_failure(requests.ConnectionError()))


# --------------------------------------------------------------- the breaker

class BreakerTest(_Isolated):

    def test_it_does_not_trip_before_the_threshold(self):
        self._fail(times=undx_health.threshold() - 1)
        self.assertFalse(undx_health.is_open("meta"))
        self.assertFalse(undx_health.should_skip("meta"))

    def test_the_threshold_opens_it(self):
        self._fail(times=undx_health.threshold())
        self.assertTrue(undx_health.is_open("meta"))
        self.assertTrue(undx_health.should_skip("meta"))

    def test_a_success_between_failures_keeps_it_closed(self):
        """Intermittent is not down. Gemini demonstrably returns transient 503s
        from upstream capacity; a breaker counting total rather than
        consecutive failures would eventually rest a working provider, and the
        rest would look exactly like the outage it was meant to detect."""
        for _ in range(10):
            self._fail(times=undx_health.threshold() - 1)
            undx_health.record_success("meta")
        self.assertFalse(undx_health.is_open("meta"))
        entry = undx_health.snapshot()["meta"]
        self.assertEqual(entry["consecutive_failures"], 0)
        self.assertEqual(entry["state"], undx_health.HEALTHY)

    def test_reading_health_does_not_spend_the_probe(self):
        """A dashboard refresh must not claim the single trial request.

        If it did, every real caller would keep resting behind a probe nobody
        was going to resolve: looking at the outage would extend it.
        """
        self._fail(times=undx_health.threshold())
        undx_health.rewind_for_tests("meta", undx_health.cooldown_seconds() + 1,
                                     ("opened_at",))
        for _ in range(20):
            self.assertTrue(undx_health.is_open("meta"))
        self.assertFalse(undx_health.snapshot()["meta"]["probing"])
        self.assertFalse(undx_health.should_skip("meta"))

    def test_the_expired_cooldown_admits_exactly_one_caller(self):
        self._fail(times=undx_health.threshold())
        undx_health.rewind_for_tests("meta", undx_health.cooldown_seconds() + 1,
                                     ("opened_at",))
        self.assertFalse(undx_health.should_skip("meta"))
        for _ in range(5):
            self.assertTrue(undx_health.should_skip("meta"))

    def test_an_abandoned_probe_expires(self):
        """A worker killed mid-probe — deploy, OOM, restart — would otherwise
        hold the claim forever and rest the provider forever, making the
        breaker a permanent outage of its own making."""
        self._fail(times=undx_health.threshold())
        undx_health.rewind_for_tests("meta", undx_health.cooldown_seconds() + 1,
                                     ("opened_at",))
        self.assertFalse(undx_health.should_skip("meta"))       # claimed, never resolved
        undx_health.rewind_for_tests("meta", 200.0, ("probe_started_at",))
        self.assertFalse(undx_health.should_skip("meta", probe_timeout=60.0))

    def test_a_failed_probe_restarts_the_cooldown(self):
        """The stored `opened_at` is already expired, so leaving it would admit
        the next caller instantly and recovery would be tested continuously
        instead of once."""
        self._fail(times=undx_health.threshold())
        undx_health.rewind_for_tests("meta", undx_health.cooldown_seconds() + 1,
                                     ("opened_at",))
        self.assertFalse(undx_health.should_skip("meta"))
        self._fail()                                            # the probe failed
        self.assertTrue(undx_health.should_skip("meta"))

    def test_a_successful_probe_closes_it(self):
        self._fail(times=undx_health.threshold())
        undx_health.rewind_for_tests("meta", undx_health.cooldown_seconds() + 1,
                                     ("opened_at",))
        self.assertFalse(undx_health.should_skip("meta"))
        undx_health.record_success("meta")
        self.assertFalse(undx_health.is_open("meta"))
        self.assertFalse(undx_health.should_skip("meta"))

    def test_opening_is_logged_at_error_once_not_per_request(self):
        """The only line that says a provider is *out*. If it repeated per
        request it would be filtered out, which is how both previous outages
        stayed invisible behind per-request warnings."""
        with self.assertLogs(level="ERROR") as logs:
            self._fail(times=undx_health.threshold())
            self._fail(times=6)
        self.assertEqual(sum("circuit opened" in line for line in logs.output), 1)

    def test_the_threshold_is_configurable_without_turning_it_off(self):
        with mock.patch.dict(os.environ, {"UNDX_BREAKER_THRESHOLD": "5"}):
            self._fail(times=4)
            self.assertFalse(undx_health.is_open("meta"))
            self._fail()
            self.assertTrue(undx_health.is_open("meta"))

    def test_a_threshold_of_one_opens_on_a_cold_row(self):
        """The INSERT arm and the UPDATE arm are different code paths, and the
        INSERT arm is the one a provider's very first failure takes."""
        with mock.patch.dict(os.environ, {"UNDX_BREAKER_THRESHOLD": "1"}):
            self._fail(provider="groq")
            self.assertTrue(undx_health.is_open("groq"))


# ------------------------------------------------- permanent faults rest longer

class AdaptiveCooldownTest(_Isolated):
    """A breaker on a fixed schedule turns a permanent fault into a slow leak.

    The cooldown was one number for every reason a provider could be resting.
    A revoked key, an unpaid invoice and a retired model ID therefore reprobed
    every two minutes, indefinitely, and each probe paid the provider's full
    timeout — up to sixty seconds on Meta — before failing in the identical way
    it had failed the time before. Nothing about waiting fixes any of those;
    the fix is a human action taken outside the process. So they still reprobe,
    because a breaker that could not notice a rotated key would need a restart
    to clear, but they reprobe on a schedule that matches how long a human
    actually takes.
    """

    def _rest(self, provider, status, error):
        self._fail(provider, times=undx_health.threshold(),
                   status=status, error=error)
        self.assertTrue(undx_health.should_skip(provider))

    def test_the_cooldown_depends_on_why_the_provider_is_resting(self):
        for status, error, expected in (
            ("http_401", "invalid api key", undx_health.actionable_cooldown_seconds()),
            ("http_402", "Insufficient Balance", undx_health.actionable_cooldown_seconds()),
            ("http_404", "model does not exist", undx_health.actionable_cooldown_seconds()),
            ("http_503", "service unavailable", undx_health.cooldown_seconds()),
            ("timeout", "", undx_health.cooldown_seconds()),
            ("http_429", "slow down", undx_health.cooldown_seconds()),
        ):
            with self.subTest(status=status):
                row = {"consecutive_failures": 3, "last_status": status,
                       "last_error": error, "last_success_at": 1.0}
                self.assertEqual(undx_health.cooldown_for(row), expected)

    def test_a_dead_key_is_still_resting_when_a_timeout_would_be_probed(self):
        """The behaviour, not just the number — and through `should_skip`, which
        is the function routing actually calls."""
        for shared in ("true", "false"):
            with self.subTest(shared=shared), \
                    mock.patch.dict(os.environ,
                                    {"UNDX_PROVIDER_HEALTH_SHARED": shared}):
                undx_health.reset_for_tests()
                self._rest("deepseek", "http_402", "Insufficient Balance")
                self._rest("gemini", "http_503", "service unavailable")
                past = undx_health.cooldown_seconds() + 1
                undx_health.rewind_for_tests("deepseek", past)
                undx_health.rewind_for_tests("gemini", past)
                self.assertTrue(undx_health.should_skip("deepseek"))
                # Anti-vacuity: the clock did move, and a transient rest ends.
                # Without this the test above would pass for a breaker that
                # simply never reopened anything.
                self.assertFalse(undx_health.should_skip("gemini"))

    def test_the_longer_rest_does_end(self):
        """Anti-vacuity from the other side: ACTIONABLE is a longer wait, not a
        permanent exclusion. A provider whose key was rotated an hour ago has to
        be able to come back without a deploy."""
        for shared in ("true", "false"):
            with self.subTest(shared=shared), \
                    mock.patch.dict(os.environ,
                                    {"UNDX_PROVIDER_HEALTH_SHARED": shared}):
                undx_health.reset_for_tests()
                self._rest("deepseek", "http_402", "Insufficient Balance")
                undx_health.rewind_for_tests(
                    "deepseek", undx_health.actionable_cooldown_seconds() + 1)
                self.assertFalse(undx_health.should_skip("deepseek"))

    def test_raising_the_base_cooldown_cannot_invert_the_relationship(self):
        """`max`, not a substitution. An operator who sets the base cooldown
        above the actionable one is asking for longer rests generally; reading
        the actionable value as an override would grant the opposite, and would
        do it to exactly the providers that least deserve a fast retry."""
        with mock.patch.dict(os.environ, {
                "UNDX_BREAKER_COOLDOWN_S": "3600",
                "UNDX_BREAKER_ACTIONABLE_COOLDOWN_S": "1800"}):
            row = {"consecutive_failures": 3, "last_status": "http_402",
                   "last_error": "Insufficient Balance", "last_success_at": 1.0}
            self.assertEqual(undx_health.cooldown_for(row), 3600)

    def test_both_cooldowns_are_configurable(self):
        with mock.patch.dict(os.environ, {
                "UNDX_BREAKER_COOLDOWN_S": "30",
                "UNDX_BREAKER_ACTIONABLE_COOLDOWN_S": "90"}):
            transient = {"consecutive_failures": 3, "last_status": "timeout",
                         "last_error": "", "last_success_at": 1.0}
            dead = dict(transient, last_status="http_401",
                        last_error="invalid api key")
            self.assertEqual(undx_health.cooldown_for(transient), 30)
            self.assertEqual(undx_health.cooldown_for(dead), 90)

    def test_the_snapshot_reports_the_rest_the_provider_is_actually_serving(self):
        """`cooldown_remaining_s` is what an operator reads to decide whether to
        wait. Computing it from the base cooldown while the breaker enforced a
        longer one would make the dashboard disagree with the router."""
        self._rest("deepseek", "http_402", "Insufficient Balance")
        entry = undx_health.snapshot()["deepseek"]
        self.assertGreater(entry["cooldown_remaining_s"],
                           undx_health.cooldown_seconds())
        self.assertLessEqual(entry["cooldown_remaining_s"],
                             undx_health.actionable_cooldown_seconds())
        self._rest("gemini", "http_503", "service unavailable")
        self.assertLessEqual(undx_health.snapshot()["gemini"]["cooldown_remaining_s"],
                             undx_health.cooldown_seconds())

    def test_the_opened_log_states_the_rest_it_is_actually_imposing(self):
        """That ERROR line is where an operator decides between waiting and
        going to fix something. Printing the base cooldown for a provider the
        breaker will rest for thirty minutes answers that question wrongly, in
        the direction of waiting — which is the failure mode this whole change
        exists to remove."""
        with self.assertLogs(level="ERROR") as logs:
            self._fail("deepseek", times=undx_health.threshold(),
                       status="http_402", error="Insufficient Balance")
        line = next(l for l in logs.output if "circuit opened" in l)
        self.assertIn("reason=%s" % undx_health.BILLING_FAILED, line)
        self.assertIn("actionable=True", line)
        self.assertIn("cooldown_s=%d" % undx_health.actionable_cooldown_seconds(),
                      line)

    def test_the_opened_log_does_not_call_everything_actionable(self):
        """Anti-vacuity for the line above."""
        with self.assertLogs(level="ERROR") as logs:
            self._fail("gemini", times=undx_health.threshold(),
                       status="http_503", error="service unavailable")
        line = next(l for l in logs.output if "circuit opened" in l)
        self.assertIn("reason=%s" % undx_health.UNAVAILABLE, line)
        self.assertIn("actionable=False", line)
        self.assertIn("cooldown_s=%d" % undx_health.cooldown_seconds(), line)

    def test_an_unknown_row_gets_the_ordinary_cooldown(self):
        """`cooldown_for(None)` is reachable: a store read can return nothing
        for a provider the mirror is resting. Defaulting to the long rest there
        would silently extend every breaker that lost its row."""
        self.assertEqual(undx_health.cooldown_for(None),
                         undx_health.cooldown_seconds())


# ------------------------------------------------------- actually distributed

class SharedAcrossProcessesTest(unittest.TestCase):
    """The only property that matters, and the only one a single interpreter
    cannot check."""

    _CHILD = textwrap.dedent("""
        import os, sys
        sys.path.insert(0, {repo!r})
        os.environ["DATABASE_URL"] = "sqlite:///" + {db!r}
        for name in ("UNDX_PROVIDER_HEALTH_SHARED", "UNDX_BREAKER_THRESHOLD",
                     "UNDX_BREAKER_COOLDOWN_S"):
            os.environ.pop(name, None)
        from services import undx_health
        undx_health.record_failure("meta", "http_500", "boom")
        row = undx_health.read("meta")
        print("%s %s" % (row["consecutive_failures"], 1 if row["opened_at"] else 0))
    """)

    _PROBE_CHILD = textwrap.dedent("""
        import os, sys, time
        sys.path.insert(0, {repo!r})
        os.environ["DATABASE_URL"] = "sqlite:///" + {db!r}
        os.environ["UNDX_PROVIDER_HEALTH_SHARED"] = {shared!r}
        from services import undx_health
        # Line up on a wall-clock instant so the claims genuinely contend
        # rather than arriving in a queue.
        while time.time() < {start!r}:
            time.sleep(0.002)
        print("CLAIMED" if not undx_health.should_skip("meta") else "DENIED")
    """)

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "health.db")

    def _run(self, source, **fmt):
        code = source.format(repo=REPO_ROOT, db=self.db_path, **fmt)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip().splitlines()[-1]

    def test_three_processes_failing_once_each_open_the_circuit(self):
        """This is the defect, stated as a number.

        Per-process state meant "three consecutive failures" needed three
        failures *in each of nine processes* — up to twenty-seven calls into a
        dead provider, a share of them paying Meta's 60s timeout, before every
        worker had independently reached the same conclusion.
        """
        seen = [self._run(self._CHILD) for _ in range(3)]
        self.assertEqual(seen, ["1 0", "2 0", "3 1"])

    def test_a_process_local_breaker_would_fail_this_harness(self):
        """Proof the harness detects what it claims to. Without it, a green
        result above shows only that subprocesses run."""
        code = textwrap.dedent("""
            state = {"n": 0}
            state["n"] += 1
            print("%s %s" % (state["n"], 1 if state["n"] >= 3 else 0))
        """)
        seen = []
        for _ in range(3):
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                  text=True, timeout=60)
            seen.append(proc.stdout.strip())
        self.assertEqual(seen, ["1 0", "1 0", "1 0"])
        self.assertNotEqual(seen, ["1 0", "2 0", "3 1"])

    def test_the_breaker_survives_a_restart(self):
        """A breaker a deploy resets is a breaker that a busy deploy day turns
        off, at the moment it is most needed."""
        for _ in range(3):
            self._run(self._CHILD)
        still_open = self._run(textwrap.dedent("""
            import os, sys
            sys.path.insert(0, {repo!r})
            os.environ["DATABASE_URL"] = "sqlite:///" + {db!r}
            from services import undx_health
            print(undx_health.is_open("meta"))
        """))
        self.assertEqual(still_open, "True")

    def test_exactly_one_process_wins_the_half_open_probe(self):
        """§17, raced rather than asserted.

        Six processes reach an expired cooldown simultaneously. Five must be
        denied. A per-process probe hands all six a trial request into a
        provider nobody has confirmed is back, and on Meta — where
        META_MUSE_TIMEOUT_MS is 60000 — that is six minutes of wall time spent
        at the exact moment the breaker exists to avoid spending it.
        """
        for _ in range(3):
            self._run(self._CHILD)
        # Expire the cooldown in the shared row, from a process that then exits.
        self._run(textwrap.dedent("""
            import os, sys
            sys.path.insert(0, {repo!r})
            os.environ["DATABASE_URL"] = "sqlite:///" + {db!r}
            from services import undx_health
            undx_health.rewind_for_tests(
                "meta", undx_health.cooldown_seconds() + 5, ("opened_at",))
            print("rewound")
        """))

        results = self._race_for_probe(shared="true")
        self.assertEqual(results.count("CLAIMED"), 1,
                         f"expected exactly one probe holder, got {results}")
        self.assertEqual(results.count("DENIED"), 5)

    def test_without_sharing_every_process_claims_its_own_probe(self):
        """§49: the guarantee above, with the guarantee removed.

        Identical race, `UNDX_PROVIDER_HEALTH_SHARED=false`. All six claim.
        This is not a hypothetical — it is what the breaker did in production
        before this module existed, and it is the reason the test above is
        worth its six subprocesses rather than being a single-threaded
        assertion that would have passed either way.
        """
        for _ in range(3):
            self._run(self._CHILD)
        results = self._race_for_probe(shared="false")
        self.assertEqual(results.count("CLAIMED"), 6,
                         f"expected the un-shared breaker to leak every probe, got {results}")

    def _race_for_probe(self, shared):
        import time
        start = time.time() + 3.0
        procs = []
        for _ in range(6):
            code = self._PROBE_CHILD.format(repo=REPO_ROOT, db=self.db_path,
                                            start=start, shared=shared)
            procs.append(subprocess.Popen([sys.executable, "-c", code],
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True))
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, err)
            results.append(out.strip().splitlines()[-1])
        return results


# --------------------------------------------------------------- degradation

class DegradationTest(_Isolated):
    """A bookkeeping table being unreachable must not become an AI outage."""

    def test_a_store_failure_falls_back_to_the_process_mirror(self):
        self._fail(times=undx_health.threshold())
        self.assertTrue(undx_health.is_open("meta"))
        with mock.patch.object(undx_health, "_connect",
                               side_effect=RuntimeError("no database")):
            self.assertTrue(undx_health.is_open("meta"))
            self.assertTrue(undx_health.should_skip("meta"))

    def test_a_degraded_snapshot_says_it_is_degraded(self):
        """A caller that cannot tell a deployment-wide answer from one worker's
        guess will read the degraded one as authoritative — which is how the
        per-process breaker looked correct for as long as it did."""
        self._fail(times=1)
        self.assertTrue(undx_health.snapshot()["meta"]["distributed"])
        with mock.patch.object(undx_health, "_connect",
                               side_effect=RuntimeError("no database")):
            self.assertFalse(undx_health.snapshot()["meta"]["distributed"])

    def test_the_degradation_is_counted(self):
        before = undx_health.stats()["degraded"]
        with mock.patch.object(undx_health, "_connect",
                               side_effect=RuntimeError("no database")):
            undx_health.record_failure("meta", "http_500")
            undx_health.should_skip("meta")
        self.assertGreater(undx_health.stats()["degraded"], before)

    def test_a_store_failure_does_not_raise_into_the_request(self):
        """The request has already failed; a bookkeeping fault must not become
        a second, different failure on top of it."""
        with mock.patch.object(undx_health, "_connect",
                               side_effect=RuntimeError("no database")):
            undx_health.record_failure("meta", "http_500", "boom")
            undx_health.record_success("meta")


class KillSwitchTest(_Isolated):
    """§39's shape: the new behaviour can be switched off without switching off
    the control it belongs to."""

    def test_sharing_can_be_disabled_without_disabling_the_breaker(self):
        with mock.patch.dict(os.environ, {"UNDX_PROVIDER_HEALTH_SHARED": "false"}):
            self.assertFalse(undx_health.shared_enabled())
            self._fail(times=undx_health.threshold())
            self.assertTrue(undx_health.is_open("meta"))
            self.assertTrue(undx_health.should_skip("meta"))

    def test_disabling_sharing_writes_nothing_to_the_store(self):
        with mock.patch.dict(os.environ, {"UNDX_PROVIDER_HEALTH_SHARED": "false"}):
            self._fail(times=undx_health.threshold())
        self.assertEqual(undx_health.stats()["writes"], 0)

    def test_sharing_is_on_by_default(self):
        """Stated as a test because the default is the decision. A breaker that
        opens too eagerly costs one provider for one cooldown and heals itself;
        that bounded, self-healing blast radius is why this defaults on where
        `sentinel.rate_limit` defaults off."""
        for name in undx_health.HEALTH_ENV_VARS:
            os.environ.pop(name, None)
        self.assertTrue(undx_health.shared_enabled())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
