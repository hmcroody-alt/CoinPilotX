"""Model-retirement detection, and the three ways it could be a fake control.

1. It could ask ListModels. That endpoint answers a different question — what a
   vendor will name, not what it will serve — and this repo holds the proof:
   `gemini-2.5-flash` is advertised to this deployment's key and 404s on
   `generateContent`. `test_a_listing_based_check_would_miss_this` builds that
   exact provider and shows the audit still catches it.

2. It could report every provider retired, or none. Each retirement assertion
   is paired with a healthy provider in the same run.

3. It could be right about the verdict while sending the caller's text to seven
   vendors at once. `test_the_probe_never_carries_caller_content` pins the
   prompt to a module constant.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

import undx_router  # noqa: E402
from services import undx_cost, undx_health, undx_model_audit  # noqa: E402


def _http_error(code: int, body: str = "") -> requests.HTTPError:
    response = requests.Response()
    response.status_code = code
    response._content = body.encode()
    return requests.HTTPError(body, response=response)


class _Isolated(unittest.TestCase):
    """Own database, own breaker, own ledger. No probe reaches a network."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        # Keys for every provider, named by the router rather than by a second
        # copy of the mapping here — a hardcoded list would drift and would
        # silently turn "provider was probed" into "provider was skipped",
        # which is a passing test for a check that did nothing.
        env = {"DATABASE_URL": "sqlite:///" + os.path.join(self._dir.name, "a.db")}
        for name, config in undx_router.PROVIDERS.items():
            env[config.key_env] = f"sk-test-{name}"
        self._env = mock.patch.dict(os.environ, env, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        for name in undx_health.HEALTH_ENV_VARS:
            os.environ.pop(name, None)
        undx_health.reset_for_tests()
        undx_cost.reset_for_tests()
        self.addCleanup(undx_health.reset_for_tests)
        self.addCleanup(undx_cost.reset_for_tests)

    def _callers(self, **behaviours):
        """Replace the adapter table with canned behaviour per provider.

        A callable raises; anything else is returned as the adapter's result.
        """
        def make(name, outcome):
            def caller(system_prompt, message, history, timeout, **kwargs):
                if isinstance(outcome, BaseException):
                    raise outcome
                result = outcome(system_prompt, message, history, timeout, **kwargs) \
                    if callable(outcome) else dict(outcome)
                # Real adapters hand back a usage dict that has already been
                # through `_normalise_usage`. Doing it here too keeps the fake
                # honest about the shape the ledger is entitled to expect.
                usage = result.get("usage")
                if isinstance(usage, dict) and "provider" not in usage:
                    result["usage"] = undx_router._normalise_usage(
                        name, result.get("model") or undx_router._model(name), usage)
                return result
            return caller

        table = {name: make(name, outcome) for name, outcome in behaviours.items()}
        return mock.patch.dict(undx_router.CALLERS, table, clear=False)

    @staticmethod
    def _answer(text="ok", model=None, tokens=6):
        """A healthy reply. `model=None` means "whatever was asked for", which
        is what a provider that is not substituting anything does."""
        out = {"text": text,
               "usage": {"prompt_tokens": 4, "completion_tokens": tokens,
                         "total_tokens": 4 + tokens}}
        if model is not None:
            out["model"] = model
        return out


# --------------------------------------------------------- the control itself

class RetirementDetectionTest(_Isolated):

    def test_a_retired_model_is_named_as_retired(self):
        with self._callers(openai=_http_error(
                404, '{"error":{"message":"The model `gpt-4o-mini` does not exist"}}')):
            result = undx_model_audit.probe("openai")
        self.assertEqual(result["state"], undx_health.MODEL_RETIRED)
        self.assertTrue(result["probed"])

    def test_a_working_model_is_not(self):
        """Anti-vacuity: MODEL_RETIRED must not be what this returns for
        everything that answers."""
        with self._callers(openai=self._answer()):
            result = undx_model_audit.probe("openai")
        self.assertEqual(result["state"], undx_health.HEALTHY)
        self.assertTrue(result["ok"])

    def test_a_listing_based_check_would_miss_this(self):
        """The specific reason this is a completion and not a ListModels diff.

        `undx_router.py:98-100` records that `gemini-2.5-flash` is advertised to
        this key by ListModels and 404s on generateContent. A check built on the
        listing reports the model present — it *is* present, in the listing —
        and stays green through a total outage of that provider. The probe sends
        the same request a user sends, so it cannot be green while users are
        not.
        """
        listed_but_dead = _http_error(
            404, '{"error":{"message":"models/gemini-2.5-flash is not found for '
                 'API version v1beta, or is not supported for generateContent"}}')
        with self._callers(gemini=listed_but_dead, openai=self._answer()):
            result = undx_model_audit.audit(["gemini", "openai"])
        self.assertEqual(result["retired"], ["gemini"])
        self.assertEqual(result["healthy"], ["openai"])
        self.assertEqual(result["method"], "completion")

    def test_a_bare_404_is_not_called_retirement(self):
        """Same rule the health module applies: a 404 with no model named is a
        wrong path as easily as a wrong model, and sending someone to change a
        model ID to fix a typo'd URL is a worse outcome than saying 'down'."""
        with self._callers(openai=_http_error(404, "Not Found")):
            result = undx_model_audit.probe("openai")
        self.assertEqual(result["state"], undx_health.UNAVAILABLE)

    def test_an_empty_completion_counts_as_a_failure(self):
        """Transport succeeded and nothing came back. At least one provider
        retires a model this way, and `ok` has to mean the model answered."""
        with self._callers(openai=self._answer(text="")):
            result = undx_model_audit.probe("openai")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], undx_health.STATUS_RESPONSE)

    def test_every_failure_class_keeps_its_own_name(self):
        cases = {
            401: undx_health.AUTH_FAILED,
            402: undx_health.BILLING_FAILED,
            429: undx_health.RATE_LIMITED,
            503: undx_health.DEGRADED,
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                undx_health.reset_for_tests()
                with self._callers(openai=_http_error(code, "nope")):
                    self.assertEqual(undx_model_audit.probe("openai")["state"],
                                     expected)

    def test_no_verdict_outside_the_declared_state_set(self):
        outcomes = [_http_error(c, "does not exist") for c in
                    (400, 401, 402, 403, 404, 429, 500, 503)]
        outcomes += [requests.Timeout("slow"), requests.ConnectionError("dns"),
                     ValueError("weird"), self._answer(), self._answer(text="")]
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                undx_health.reset_for_tests()
                with self._callers(openai=outcome):
                    state = undx_model_audit.probe("openai")["state"]
                self.assertIn(state, undx_health.HEALTH_STATES)

    def test_a_probe_never_raises_into_the_caller(self):
        """A diagnostic that can crash the thing diagnosing is not one."""
        with self._callers(openai=RuntimeError("adapter exploded")):
            result = undx_model_audit.probe("openai")
        self.assertEqual(result["state"], undx_health.DEGRADED)
        self.assertIn("adapter exploded", result["error"])


# ------------------------------------------------------------ what it will not do

class ProbeSafetyTest(_Isolated):

    def test_the_probe_never_carries_caller_content(self):
        """The prompt is a constant. An audit that echoed caller text would be
        broadcasting it to every configured provider at once — including ones a
        privacy ceiling would have refused — from a code path nobody reads as a
        data path."""
        seen: list[tuple[str, str]] = []

        def capture(system_prompt, message, history, timeout, **kwargs):
            seen.append((system_prompt, kwargs.get("user_content", "")))
            return self._answer()

        with self._callers(openai=capture, meta=capture):
            undx_model_audit.audit(["openai", "meta"])
        self.assertTrue(seen)
        for system_prompt, user_content in seen:
            self.assertEqual(system_prompt, undx_model_audit.PROBE_SYSTEM)
            self.assertEqual(user_content, undx_model_audit.PROBE_PROMPT)

    def test_probe_takes_no_content_parameter_at_all(self):
        """Belt and braces for the test above: the safety property is that
        there is no way to pass content in, not merely that nobody does."""
        import inspect

        params = set(inspect.signature(undx_model_audit.probe).parameters)
        for leak in ("message", "prompt", "user_content", "content", "text"):
            self.assertNotIn(leak, params)

    def test_it_does_not_consume_the_half_open_probe_lease(self):
        """The breaker grants exactly one trial request across the deployment.
        An audit that spent it would make a real recovery wait another cooldown,
        and would make audit traffic look like organic recovery."""
        for _ in range(undx_health.threshold()):
            undx_health.record_failure("openai", "http_503", "down")
        undx_health.rewind_for_tests("openai", undx_health.cooldown_seconds() + 1)
        with self._callers(openai=_http_error(503, "still down")):
            undx_model_audit.probe("openai")
        # The lease was never claimed, so the next real caller still gets it.
        undx_health.rewind_for_tests("openai", undx_health.cooldown_seconds() + 1)
        self.assertFalse(undx_health.should_skip("openai"))

    def test_it_probes_a_provider_the_breaker_is_resting(self):
        """The audit exists to find out *why* a provider is out. Deferring to
        the breaker would blind it exactly when it is needed."""
        for _ in range(undx_health.threshold()):
            undx_health.record_failure("openai", "http_503", "down")
        self.assertTrue(undx_health.is_open("openai"))
        with self._callers(openai=self._answer()):
            result = undx_model_audit.probe("openai")
        self.assertTrue(result["probed"])
        self.assertEqual(result["state"], undx_health.HEALTHY)

    def test_a_disabled_provider_is_not_called(self):
        calls: list[str] = []

        def caller(*args, **kwargs):
            calls.append("openai")
            return self._answer()

        with mock.patch.dict(os.environ, {"UNDX_OPENAI_ENABLED": "false"}), \
                self._callers(openai=caller):
            result = undx_model_audit.probe("openai")
        self.assertEqual(calls, [])
        self.assertFalse(result["probed"])
        self.assertEqual(result["skipped"], "disabled")

    def test_a_provider_with_no_key_is_not_called(self):
        calls: list[str] = []

        def caller(*args, **kwargs):
            calls.append("claude")
            return self._answer()

        key_env = undx_router.PROVIDERS["claude"].key_env
        with mock.patch.dict(os.environ, {key_env: ""}), self._callers(claude=caller):
            result = undx_model_audit.probe("claude")
        self.assertEqual(calls, [])
        self.assertEqual(result["state"], undx_health.AUTH_FAILED)
        self.assertEqual(result["skipped"], "no api key")


# ------------------------------------------------------------------ side effects

class AuditSideEffectTest(_Isolated):

    def test_the_probe_updates_the_health_surface(self):
        """The point of a real call: a provider nobody has exercised is UNKNOWN
        until something observes it, and the audit is the only thing that can
        observe one on purpose."""
        self.assertEqual(undx_health.state_for(undx_health.read("openai")),
                         undx_health.UNKNOWN)
        with self._callers(openai=self._answer()):
            undx_model_audit.probe("openai")
        self.assertEqual(undx_health.state_for(undx_health.read("openai")),
                         undx_health.HEALTHY)

    def test_the_money_is_counted(self):
        """A budget with an unmetered spender in it is not a budget."""
        with self._callers(openai=self._answer(tokens=6)):
            undx_model_audit.probe("openai")
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["providers"]["openai"]["calls"], 1)

    def test_record_false_spends_nothing_and_records_nothing(self):
        """Anti-vacuity for both of the above, and the switch a dry run needs."""
        with self._callers(openai=self._answer()):
            undx_model_audit.probe("openai", record=False)
        self.assertEqual(undx_cost.month_snapshot().get("providers", {}), {})
        self.assertIsNone(undx_health.read("openai"))

    def test_a_failed_probe_counts_toward_the_breaker(self):
        with self._callers(openai=_http_error(402, "Insufficient Balance")):
            for _ in range(undx_health.threshold()):
                undx_model_audit.probe("openai")
        self.assertTrue(undx_health.is_open("openai"))


# ------------------------------------------------------------ the run as a whole

class AuditReportTest(_Isolated):

    def test_a_run_separates_wait_from_go_fix_something(self):
        with self._callers(
                openai=self._answer(),
                gemini=_http_error(503, "upstream unavailable"),
                deepseek=_http_error(402, "Insufficient Balance"),
                meta=_http_error(404, "model muse-spark-1.2 does not exist")):
            result = undx_model_audit.audit(
                ["openai", "gemini", "deepseek", "meta"])
        self.assertEqual(result["healthy"], ["openai"])
        self.assertEqual(result["retired"], ["meta"])
        self.assertEqual(result["actionable"], ["deepseek", "meta"])
        self.assertFalse(result["ok"])

    def test_an_all_healthy_run_is_ok(self):
        """Anti-vacuity: `ok` must be capable of being true."""
        with self._callers(openai=self._answer(), meta=self._answer()):
            result = undx_model_audit.audit(["openai", "meta"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["actionable"], [])

    def test_retirement_is_logged_at_error(self):
        """Failover means a retired model still returns 200 to the user. If
        nothing logs loudly, nothing contradicts the green dashboard — which is
        how the last two outages lasted as long as they did."""
        with self.assertLogs(level="ERROR") as logs, self._callers(
                openai=_http_error(404, "The model does not exist")):
            undx_model_audit.audit(["openai"])
        self.assertTrue(any("retired models" in line for line in logs.output))

    def test_a_substituted_model_is_reported(self):
        """A provider that quietly serves something else makes the price table
        and every benchmark result be about a different model than the one
        answering."""
        with self._callers(openai=self._answer(model="gpt-3.5-turbo")):
            result = undx_model_audit.audit(["openai"])
        self.assertEqual(result["model_mismatch"], ["openai"])

    def test_a_dated_build_of_the_requested_model_is_not_a_substitution(self):
        """Anti-vacuity: providers pin `gpt-4o-mini` to `gpt-4o-mini-2024-07-18`
        and that is the same model, not drift. Flagging it would make the check
        fire constantly and therefore be ignored."""
        with self._callers(openai=self._answer(model="gpt-4o-mini-2024-07-18")):
            result = undx_model_audit.audit(["openai"])
        self.assertEqual(result["model_mismatch"], [])

    def test_the_run_reports_what_it_spent(self):
        with self._callers(openai=self._answer(), meta=self._answer()):
            result = undx_model_audit.audit(["openai", "meta"])
        self.assertEqual(result["probes"], 2)
        self.assertGreaterEqual(result["cost_usd"], 0.0)

    def test_an_unpriced_model_makes_the_total_a_floor(self):
        """`cost_complete` false is the same distinction `undx_cost` draws:
        a sum missing an unknown term is a floor, and rounding that away is how
        a budget silently stops covering a provider."""
        with mock.patch.object(undx_cost, "price_for", return_value=None), \
                self._callers(openai=self._answer()):
            result = undx_model_audit.audit(["openai"])
        self.assertFalse(result["cost_complete"])

    def test_it_audits_every_configured_provider_by_default(self):
        with self._callers(**{name: self._answer() for name in undx_router.PROVIDERS}):
            result = undx_model_audit.audit()
        self.assertEqual(set(result["providers"]), set(undx_router.PROVIDERS))

    def test_models_come_from_the_router_not_from_a_second_list(self):
        """One configuration authority. A second copy of the model IDs here
        would drift from the one that is actually sent, and the audit would
        cheerfully verify models nothing uses."""
        with self._callers(**{name: self._answer() for name in undx_router.PROVIDERS}):
            result = undx_model_audit.audit()
        for name, model in undx_router.configured_models().items():
            with self.subTest(provider=name):
                self.assertEqual(result["providers"][name]["model"], model)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
