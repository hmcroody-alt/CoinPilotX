"""The canary chooses people, never providers, and one switch takes it all away."""

from __future__ import annotations

import ast
import os
import pathlib
import types
import unittest
from unittest import mock

from services import undx_canary as canary

MODULE_PATH = pathlib.Path(canary.__file__)


def _router(*, omni=True):
    return types.SimpleNamespace(omni_router_enabled=lambda: omni)


def _env(**overrides):
    base = {"UNDX_CANARY_ENABLED": "true", "UNDX_CANARY_USER_IDS": "7,8,9"}
    base.update(overrides)
    return mock.patch.dict(os.environ, base, clear=False)


class KillSwitchTest(unittest.TestCase):
    """§39: one switch drops the new behaviour without dropping UNDX."""

    def test_omni_off_puts_everyone_in_control(self):
        with _env():
            for user_id in (7, 8, 9):
                with self.subTest(user_id=user_id):
                    assignment = canary.cohort(_router(omni=False), user_id)
                    self.assertFalse(assignment.is_canary)
                    self.assertEqual(assignment.reason, "omni_disabled")

    def test_omni_off_beats_every_other_setting(self):
        """The switch an operator pulls during an incident has to be the last
        word, or pulling it is a negotiation with four other variables."""
        with _env(UNDX_CANARY_ENABLED="true", UNDX_CANARY_USER_IDS="7"):
            self.assertEqual(canary.cohort(_router(omni=False), 7).reason,
                             "omni_disabled")

    def test_the_switch_is_off_by_default(self):
        from undx_router import omni_router_enabled
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(omni_router_enabled())

    def test_both_switches_are_off_by_default(self):
        """The omni switch had this test; the canary's own did not.

        The omni gate runs first, so `UNDX_CANARY_ENABLED` defaulting to true
        would be inert today — and would become live the moment somebody turns
        the omni switch on, enrolling whoever happened to be listed. A default
        that is only safe because another default is safe is not a default.
        """
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(canary.enabled())
            self.assertEqual(canary.cohort(_router(), 7).reason, "canary_disabled")

    def test_the_switch_does_not_disable_undx_routing(self):
        """The failure this switch exists to avoid is an operator refusing to
        pull it because pulling it takes the assistant down too."""
        import undx_router
        with mock.patch.dict(os.environ, {"UNDX_OMNI_ROUTER_ENABLED": "false",
                                          "UNDX_ROUTER_ENABLED": "true"},
                             clear=False):
            self.assertFalse(undx_router.omni_router_enabled())
            self.assertTrue(undx_router.router_enabled())
            self.assertTrue(undx_router.provider_priority(
                {"category": "research"}))

    def test_one_switch_has_one_implementation(self):
        """A kill switch read in two places is a kill switch that is on in one.

        Only `undx_router` may *read* the environment variable; everything else
        reaches it through the router object it already holds. Discussing it in
        a docstring is fine and expected, so this looks for the name being
        passed to a call — an actual lookup — rather than for the characters
        appearing in the file.
        """
        root = MODULE_PATH.parent.parent
        reading = []
        for path in list(root.glob("services/undx_*.py")) + [root / "undx_router.py"]:
            source = path.read_text(encoding="utf-8")
            if "UNDX_OMNI_ROUTER_ENABLED" not in source:
                continue
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                if any(isinstance(arg, ast.Constant)
                       and arg.value == "UNDX_OMNI_ROUTER_ENABLED"
                       for arg in node.args):
                    reading.append(path.name)
                    break
        self.assertEqual(reading, ["undx_router.py"], reading)


class CohortTest(unittest.TestCase):

    def test_a_listed_user_is_in_the_canary(self):
        with _env():
            self.assertTrue(canary.cohort(_router(), 8).is_canary)
            self.assertEqual(canary.routing_mode(_router(), 8), "canary")

    def test_an_unlisted_user_is_control(self):
        with _env():
            assignment = canary.cohort(_router(), 404)
        self.assertEqual(assignment.mode, "control")
        self.assertEqual(assignment.reason, "not_enrolled")

    def test_assignment_is_stable_across_repeated_calls(self):
        """A user who flips cohort mid-conversation produces a bug report
        describing behaviour that no single code path produces."""
        with _env():
            modes = {canary.routing_mode(_router(), 7) for _ in range(200)}
        self.assertEqual(modes, {"canary"})

    def test_the_module_consults_no_clock_and_no_randomness(self):
        """Stability asserted structurally, not just observed 200 times.

        A `random` or `time` call added later would still pass the loop above
        most of the time, which is the worst kind of test.
        """
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("random", imported)
        self.assertNotIn("time", imported)
        self.assertNotIn("hashlib", imported)

    def test_an_anonymous_or_malformed_user_is_control(self):
        with _env():
            self.assertEqual(canary.cohort(_router(), None).reason, "no_user")
            self.assertEqual(canary.cohort(_router(), "").reason, "no_user")
            self.assertEqual(canary.cohort(_router(), "abc").reason, "bad_user")

    def test_a_numeric_string_id_is_accepted(self):
        """Request plumbing hands ids over as strings more often than not."""
        with _env():
            self.assertTrue(canary.cohort(_router(), "8").is_canary)

    def test_an_empty_cohort_is_its_own_reason(self):
        """"Nobody is listed" and "this user is not listed" are different
        problems, and only one of them means the canary is inert."""
        with _env(UNDX_CANARY_USER_IDS=""):
            self.assertEqual(canary.cohort(_router(), 7).reason, "empty_cohort")

    def test_canary_disabled_is_distinct_from_omni_disabled(self):
        with _env(UNDX_CANARY_ENABLED="false"):
            self.assertEqual(canary.cohort(_router(), 7).reason,
                             "canary_disabled")

    def test_every_control_reason_is_declared(self):
        with self.assertRaises(ValueError):
            canary.Assignment(mode="control", reason="felt like it")
        with self.assertRaises(ValueError):
            canary.Assignment(mode="treatment", reason="")

    def test_a_canary_assignment_carries_no_control_reason(self):
        """Otherwise `reason` means two things and neither is checkable."""
        with self.assertRaises(ValueError):
            canary.Assignment(mode="canary", reason="not_enrolled")


class CohortParsingTest(unittest.TestCase):

    def test_whitespace_and_separators_are_tolerated(self):
        for raw in ("7, 8 ,9", "7;8;9", " 7,8,9 ", "7,,8,9,"):
            with self.subTest(raw=raw), _env(UNDX_CANARY_USER_IDS=raw):
                self.assertEqual(canary.cohort_ids(), frozenset({7, 8, 9}))

    def test_one_bad_entry_does_not_empty_the_cohort(self):
        """A typo must not turn into "the canary silently stopped" — which
        looks identical to "the canary is running and finding nothing"."""
        with _env(UNDX_CANARY_USER_IDS="7,oops,9"):
            self.assertEqual(canary.cohort_ids(), frozenset({7, 9}))

    def test_an_entirely_malformed_list_yields_no_cohort(self):
        with _env(UNDX_CANARY_USER_IDS="all,staff"):
            self.assertEqual(canary.cohort_ids(), frozenset())
            self.assertEqual(canary.cohort(_router(), 7).reason, "empty_cohort")


class NoProviderPreferenceTest(unittest.TestCase):
    """§1, enforced by making a provider name unsayable in this module."""

    def test_the_canary_module_names_no_provider(self):
        """A canary that reordered the lane table for a cohort would be a
        provider promotion with a smaller blast radius and no benchmark behind
        it. The cheapest way to make that impossible is to keep the vocabulary
        out of the module entirely.
        """
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        for provider in ("openai", "claude", "gemini", "groq", "deepseek",
                         "perplexity", "meta", "muse"):
            with self.subTest(provider=provider):
                self.assertNotIn(provider, source)

    def test_the_canary_returns_a_cohort_not_a_provider_order(self):
        with _env():
            assignment = canary.cohort(_router(), 7)
        self.assertIn(assignment.mode, canary.MODES)
        self.assertNotIsInstance(assignment.mode, (list, tuple))

    def test_the_module_does_not_import_the_router(self):
        """The router is injected, so this module cannot reach the lane table
        even if a later edit wanted to."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("router", node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("router", alias.name)


class StateTest(unittest.TestCase):

    def test_state_publishes_the_size_and_not_the_ids(self):
        """A dashboard outlives the experiment; a list of colleagues' user ids
        rendered onto one is a disclosure nobody signed off."""
        with _env(UNDX_CANARY_USER_IDS="7,8,9"):
            report = canary.state(_router())
        self.assertEqual(report["cohort_size"], 3)
        self.assertTrue(report["has_cohort"])
        for value in report.values():
            self.assertNotIn("7", str(value).replace("True", "").replace("3", ""))

    def test_state_distinguishes_switched_on_but_empty(self):
        """The state most easily mistaken for "running fine"."""
        with _env(UNDX_CANARY_USER_IDS=""):
            report = canary.state(_router())
        self.assertTrue(report["canary_enabled"])
        self.assertTrue(report["omni_router_enabled"])
        self.assertFalse(report["has_cohort"])


if __name__ == "__main__":
    unittest.main()
