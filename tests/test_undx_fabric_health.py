"""The composed fabric surface, and the two ways a composed surface lies.

It can contradict itself — each source honest, the pair impossible, which is
what "HEALTHY with no key" was. And it can leak, because composing four modules
means inheriting every field they publish, including the ones that were only
ever safe behind a session. Both have a test here that fails when the guard is
removed.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import undx_router  # noqa: E402
from services import undx_fabric_health as fabric  # noqa: E402
from services import undx_health  # noqa: E402


def _walk(value):
    """Every string in a nested payload, so a leak cannot hide one level down."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    else:
        yield str(value)


class _Fabric(unittest.TestCase):
    """Every provider configured and reachable, so a test that wants a defect
    has to introduce exactly one."""

    def setUp(self):
        env = {config.key_env: f"sk-test-{name}"
               for name, config in undx_router.PROVIDERS.items()}
        patch = mock.patch.dict(os.environ, env, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        for name in undx_health.HEALTH_ENV_VARS:
            os.environ.pop(name, None)

    def _snapshot(self, observed=None):
        with mock.patch.object(undx_health, "snapshot", return_value=observed or {}):
            return fabric.snapshot()

    @staticmethod
    def _row(state=undx_health.HEALTHY, **extra):
        row = {
            "state": state, "underlying_state": state, "distributed": True,
            "circuit": "closed", "probing": False, "probe_owner": "",
            "consecutive_failures": 0, "successes": 5, "failures": 0,
            "last_status": "success", "last_error": "",
            "last_success_at": 1.0, "last_failure_at": 0.0,
            "cooldown_remaining_s": 0,
            "actionable": state in undx_health.ACTIONABLE_STATES,
        }
        row.update(extra)
        return row


class ContradictionTest(_Fabric):
    """Configuration decides what the next call does; the breaker only
    remembers what an old one did."""

    def test_a_provider_with_no_key_is_never_reported_healthy(self):
        key_env = undx_router.PROVIDERS["openai"].key_env
        with mock.patch.dict(os.environ, {key_env: ""}):
            result = self._snapshot({"openai": self._row()})
        entry = result["providers"]["openai"]
        self.assertEqual(entry["state"], undx_health.AUTH_FAILED)
        self.assertTrue(entry["actionable"])
        self.assertIn("openai", result["actionable_providers"])

    def test_the_remembered_verdict_is_kept_beside_the_override(self):
        """A key someone rotated away and a key nobody ever set need different
        people, and the only thing that separates them is whether it ever
        worked."""
        key_env = undx_router.PROVIDERS["openai"].key_env
        with mock.patch.dict(os.environ, {key_env: ""}):
            result = self._snapshot({"openai": self._row()})
        self.assertEqual(result["providers"]["openai"]["remembered_state"],
                         undx_health.HEALTHY)

    def test_a_provider_with_a_key_keeps_its_observed_state(self):
        """Anti-vacuity. Without this the override could be unconditional and
        every test above would still pass, leaving a surface that reports every
        provider as broken — which is a different lie, not a fix."""
        result = self._snapshot({"openai": self._row()})
        entry = result["providers"]["openai"]
        self.assertEqual(entry["state"], undx_health.HEALTHY)
        self.assertFalse(entry["actionable"])
        self.assertNotIn("remembered_state", entry)

    def test_a_disabled_provider_is_unavailable_not_failed(self):
        """An operator turning something off is not a fault, and paging on it
        teaches people to ignore the page."""
        with mock.patch.object(undx_router, "provider_enabled",
                               side_effect=lambda n: n != "gemini"):
            result = self._snapshot({"gemini": self._row()})
        self.assertEqual(result["providers"]["gemini"]["state"],
                         undx_health.UNAVAILABLE)

    def test_a_provider_that_was_never_called_is_present_and_unknown(self):
        """§19 in its original form: a provider that never answered may not be
        absent from the surface any more than it may be labelled Online.
        Absence reads, on a dashboard, as no problems here."""
        result = self._snapshot({})
        for name in undx_router.PROVIDERS:
            with self.subTest(provider=name):
                entry = result["providers"][name]
                self.assertFalse(entry["observed"])
                self.assertEqual(entry["state"], undx_health.UNKNOWN)

    def test_every_configured_provider_appears(self):
        result = self._snapshot({})
        self.assertEqual(set(result["providers"]), set(undx_router.PROVIDERS))


class SecretFreeTest(_Fabric):
    """Reachable by anyone who can reach the service."""

    def test_a_provider_error_body_never_reaches_the_payload(self):
        """This deployment has already seen a provider error carry a full
        request URL. `last_error` is excluded rather than truncated, because a
        truncation limit is a guess about where the secret is."""
        row = self._row(state=undx_health.AUTH_FAILED,
                        last_error="401 for https://api.openai.com/v1/x?key=sk-SECRET")
        result = self._snapshot({"openai": row})
        self.assertNotIn("last_error", result["providers"]["openai"])
        self.assertNotIn("sk-SECRET", "".join(_walk(result)))

    def test_drift_findings_publish_the_variable_name_not_its_value(self):
        """`ANTHROPIC_BASE_URL points at 'https://api.meta.ai'` is an accurate
        finding and an unauthenticated disclosure of internal routing. The name
        is already public in .env.example; the value is not."""
        with mock.patch.dict(os.environ,
                             {"ANTHROPIC_BASE_URL": "https://internal.example.test"}):
            result = self._snapshot({})
        text = "".join(_walk(result))
        self.assertIn("ANTHROPIC_BASE_URL", text)
        self.assertNotIn("internal.example.test", text)
        for finding in result["config"]["findings"]:
            with self.subTest(code=finding["code"]):
                self.assertEqual(set(finding), set(fabric.PUBLIC_FINDING_FIELDS))

    def test_no_api_key_value_appears_anywhere(self):
        text = "".join(_walk(self._snapshot({})))
        for name, config in undx_router.PROVIDERS.items():
            with self.subTest(provider=name):
                self.assertNotIn(f"sk-test-{name}", text)

    def test_the_source_scan_is_not_published(self):
        """File paths and line numbers do not belong on a public endpoint, and
        do not exist in a deployed container either."""
        with mock.patch.object(fabric.undx_config_drift, "scan_source",
                               side_effect=AssertionError("source scan called")):
            result = self._snapshot({})
        self.assertTrue(result["config"])


class NoProviderIsCalledTest(_Fabric):
    """An endpoint anyone can GET is the last place to put a paid call."""

    def test_the_snapshot_contacts_no_provider(self):
        callers = {name: mock.Mock(side_effect=AssertionError(f"{name} was called"))
                   for name in undx_router.CALLERS}
        with mock.patch.dict(undx_router.CALLERS, callers, clear=False):
            result = self._snapshot({})
        self.assertTrue(result["providers"])
        for name, caller in callers.items():
            with self.subTest(provider=name):
                caller.assert_not_called()


class DegradationTest(_Fabric):
    """A health surface that goes dark on the first failure hides the outage it
    exists to report."""

    def test_one_broken_section_does_not_blank_the_others(self):
        with mock.patch.object(fabric, "_cost", side_effect=RuntimeError("boom")):
            result = self._snapshot({})
        self.assertEqual(result["cost"], {"error": "RuntimeError"})
        self.assertTrue(result["providers"])
        self.assertIn("config", result)

    def test_a_section_that_failed_makes_the_verdict_false(self):
        """An unverified guarantee is not a kept one — the same rule the drift
        check applies to a check that did not run."""
        with mock.patch.object(fabric, "_cost", side_effect=RuntimeError("boom")):
            result = self._snapshot({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["degraded_sections"], ["cost"])

    def test_a_failing_section_reports_the_class_not_the_message(self):
        secret = RuntimeError("connection to postgres://user:hunter2@host failed")
        with mock.patch.object(fabric, "_cost", side_effect=secret):
            result = self._snapshot({})
        self.assertNotIn("hunter2", "".join(_walk(result)))


class VerdictTest(_Fabric):
    """`ok` is about guarantees, not about weather."""

    def _healthy_world(self):
        return {name: self._row() for name in undx_router.PROVIDERS}

    def test_a_fully_configured_fabric_reports_ok(self):
        """Anti-vacuity for every test below: if `ok` could never be true, each
        assertion that it is false would be measuring nothing."""
        with mock.patch.object(fabric, "_drift",
                               return_value={"ok": True, "critical": 0, "warning": 0,
                                             "info": 0, "findings": []}):
            result = self._snapshot(self._healthy_world())
        self.assertTrue(result["ok"], result.get("actionable_providers"))

    def test_a_transient_outage_does_not_make_the_verdict_false(self):
        """A provider resting on a 503 is the breaker working. Reporting that
        as a failed guarantee would page someone to watch a timer."""
        world = self._healthy_world()
        world["gemini"] = self._row(state=undx_health.UNAVAILABLE,
                                    last_status="http_5xx", consecutive_failures=3)
        with mock.patch.object(fabric, "_drift",
                               return_value={"ok": True, "critical": 0, "warning": 0,
                                             "info": 0, "findings": []}):
            result = self._snapshot(world)
        self.assertEqual(result["actionable_providers"], [])
        self.assertTrue(result["ok"])

    def test_a_permanent_fault_does_make_the_verdict_false(self):
        """An unpaid invoice will not heal by waiting and nothing else on this
        surface would say so."""
        world = self._healthy_world()
        world["deepseek"] = self._row(state=undx_health.BILLING_FAILED,
                                      last_status="payment_required")
        with mock.patch.object(fabric, "_drift",
                               return_value={"ok": True, "critical": 0, "warning": 0,
                                             "info": 0, "findings": []}):
            result = self._snapshot(world)
        self.assertEqual(result["actionable_providers"], ["deepseek"])
        self.assertFalse(result["ok"])

    def test_a_critical_drift_finding_makes_the_verdict_false(self):
        with mock.patch.object(fabric, "_drift",
                               return_value={"ok": False, "critical": 1, "warning": 0,
                                             "info": 0, "findings": []}):
            result = self._snapshot(self._healthy_world())
        self.assertFalse(result["ok"])

    def test_no_reachable_provider_makes_the_verdict_false(self):
        blank = {config.key_env: "" for config in undx_router.PROVIDERS.values()}
        with mock.patch.dict(os.environ, blank):
            result = self._snapshot(self._healthy_world())
        self.assertEqual(result["reachable_providers"], [])
        self.assertFalse(result["ok"])

    def test_every_provider_disabled_is_still_not_ok(self):
        """The case the keyless test above does not reach, and the only one the
        `reachable` clause decides on its own.

        A missing key is a fault, so blanking every key makes every provider
        actionable and the verdict false for that reason — which left the
        reachability clause passing a mutation that deleted it. Disabling
        providers is not a fault: an operator meant to do it, each one reports
        UNAVAILABLE, nothing is actionable, and a fabric that cannot route a
        single request would otherwise report ok.
        """
        with mock.patch.object(undx_router, "provider_enabled", return_value=False), \
                mock.patch.object(fabric, "_drift",
                                  return_value={"ok": True, "critical": 0,
                                                "warning": 0, "info": 0,
                                                "findings": []}):
            result = self._snapshot(self._healthy_world())
        self.assertEqual(result["actionable_providers"], [])
        self.assertEqual(result["reachable_providers"], [])
        self.assertFalse(result["ok"])


class CostTest(_Fabric):
    """A total that cannot see part of the spend is a floor."""

    def test_an_unpriced_call_makes_the_total_incomplete(self):
        with mock.patch.object(fabric.undx_cost, "month_snapshot", return_value={
                "month": "2026-09", "source": "ledger",
                "providers": {"groq": {"calls": 12, "uncosted_calls": 12,
                                       "cost_micro_usd": 0}}}):
            result = self._snapshot({})
        self.assertFalse(result["cost"]["cost_complete"])
        self.assertEqual(result["cost"]["uncosted_calls"], 12)

    def test_a_fully_priced_month_is_complete(self):
        with mock.patch.object(fabric.undx_cost, "month_snapshot", return_value={
                "month": "2026-09", "source": "ledger",
                "providers": {"meta": {"calls": 69, "uncosted_calls": 0,
                                       "cost_micro_usd": 147436}}}):
            result = self._snapshot({})
        self.assertTrue(result["cost"]["cost_complete"])
        self.assertEqual(result["cost"]["cost_usd"], 0.147436)

    def test_the_ledger_source_is_reported(self):
        """A $0 month and an unreachable ledger demand opposite reactions."""
        with mock.patch.object(fabric.undx_cost, "month_snapshot", return_value={
                "month": "2026-09", "source": "process", "providers": {}}):
            result = self._snapshot({})
        self.assertEqual(result["cost"]["source"], "process")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
