"""Flag resolution, tested at the point where a typo becomes a permission.

Configuration is where safety properties are quietly given away. Nothing in this module
denies anything on its own; it hands numbers and booleans to the layers that do. So the
question these tests ask is not "does it parse" but "what does a *mistake* resolve to".

Two mistakes shipped in earlier drafts and are pinned here:

* ``UNDX_AGENT_REQUIRE_VERIFICATION=treu`` resolved to ``False``, because every
  unrecognised boolean read as off. The flag that makes UNDX read a write back before
  calling it done was disabled by a transposition, and nothing said so.
* an unrecognised trust floor fell back to the shipped default, which for an operator
  *raising* the floor is looser than what they asked for.

Both are the same shape: an unreadable value silently resolving to the permissive
reading. The rule this module now follows is that an unreadable value resolves to the
documented default and says so in ``notes``.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_brain import config as c  # noqa: E402


class UnconfiguredIsOff(unittest.TestCase):
    """The whole mission's reversibility rests on this class."""

    def test_the_brain_is_not_available_with_an_empty_environment(self):
        self.assertFalse(c.brain_available({}))

    def test_an_unreadable_master_switch_does_not_turn_it_on(self):
        for raw in ("nonsense", "maybe", "-1", "enabled?"):
            with self.subTest(raw=raw):
                self.assertFalse(c.brain_available({"UNDX_BRAIN_ENABLED": raw}))

    def test_every_stage_flag_is_off_by_default(self):
        values = c.flags({})
        for name in (
            "UNDX_BRAIN_KNOWLEDGE_ENABLED",
            "UNDX_KNOWLEDGE_ALLOW_SOURCE_DISCOVERED",
        ):
            with self.subTest(name=name):
                self.assertFalse(values[name])

    def test_the_master_switch_actually_switches(self):
        # Otherwise the assertions above pass for a flag nothing reads.
        self.assertTrue(c.brain_available({"UNDX_BRAIN_ENABLED": "1"}))


class UnreadableValuesResolveToTheDocumentedDefault(unittest.TestCase):
    def test_a_misspelt_boolean_does_not_disable_a_protective_flag(self):
        # The specific bug: transposing two letters used to switch verification off.
        for raw in ("treu", "ture", "yess", "on!", "2"):
            with self.subTest(raw=raw):
                values = c.flags({"UNDX_AGENT_REQUIRE_VERIFICATION": raw})
                self.assertTrue(values["UNDX_AGENT_REQUIRE_VERIFICATION"])

    def test_every_protective_boolean_survives_an_unreadable_value(self):
        # Generalises the case above across the catalog, so a new fail-closed flag
        # inherits the property instead of having to remember it.
        protective = [
            flag for flag in c.CATALOG
            if flag.kind == "bool" and flag.fail == "closed" and c._bool(flag.default)
        ]
        self.assertTrue(protective, "expected some fail-closed booleans to default on")
        for flag in protective:
            with self.subTest(name=flag.name):
                self.assertTrue(c.flags({flag.name: "garbled"})[flag.name])

    def test_an_unreadable_boolean_is_reported(self):
        notes = c.resolve({"UNDX_AGENT_REQUIRE_VERIFICATION": "treu"}).notes
        self.assertTrue(
            any("REQUIRE_VERIFICATION" in note and "not a boolean" in note for note in notes),
            f"a corrected value that is not reported is a value nobody fixes: {notes}",
        )

    def test_a_readable_boolean_is_not_second_guessed(self):
        # The correction must not fire on the values it exists to protect. Turning
        # verification off deliberately has to remain possible.
        self.assertFalse(c.flags({"UNDX_AGENT_REQUIRE_VERIFICATION": "0"})[
            "UNDX_AGENT_REQUIRE_VERIFICATION"
        ])
        self.assertFalse(c.flags({"UNDX_AGENT_REQUIRE_VERIFICATION": "false"})[
            "UNDX_AGENT_REQUIRE_VERIFICATION"
        ])

    def test_a_non_integer_falls_back_and_says_so(self):
        resolution = c.resolve({"UNDX_KNOWLEDGE_MAX_RESULTS": "six"})
        self.assertEqual(resolution.values["UNDX_KNOWLEDGE_MAX_RESULTS"], 6)
        self.assertTrue(any("not an integer" in note for note in resolution.notes))

    def test_a_digit_that_is_not_an_ascii_digit_falls_back_and_says_so(self):
        # ``int("٩٩")`` is 99, ``int("１００")`` is 100, ``int("𝟵𝟵")`` is 99. Python
        # accepts every Unicode decimal digit and ``str.isdigit`` agrees, so a value a
        # reviewer cannot decipher parses cleanly into a large number. Applied to
        # UNDX_BRAIN_ROLLOUT_PERCENT that is a full production rollout configured by
        # something illegible; applied to a planner ceiling it is an unbounded plan.
        for raw in ("٩٩", "１００", "𝟵𝟵"):
            with self.subTest(raw=raw):
                resolution = c.resolve({"UNDX_KNOWLEDGE_MAX_RESULTS": raw})
                self.assertEqual(resolution.values["UNDX_KNOWLEDGE_MAX_RESULTS"], 6)
                self.assertTrue(any("not an integer" in note for note in resolution.notes))

    def test_a_python_underscore_separator_is_not_an_environment_variable(self):
        # ``int("1_0_0")`` is 100. That is a Python *literal* convenience; in a dashboard
        # ``1_0`` beside ``10`` is not a difference anybody notices.
        resolution = c.resolve({"UNDX_KNOWLEDGE_MAX_RESULTS": "1_0"})
        self.assertEqual(resolution.values["UNDX_KNOWLEDGE_MAX_RESULTS"], 6)
        self.assertTrue(any("not an integer" in note for note in resolution.notes))

    def test_an_ordinary_signed_integer_still_parses(self):
        # The strictness must not cost the normal spellings.
        self.assertEqual(c.flags({"UNDX_KNOWLEDGE_MAX_RESULTS": " 8 "})["UNDX_KNOWLEDGE_MAX_RESULTS"], 8)
        self.assertEqual(c.flags({"UNDX_KNOWLEDGE_MAX_RESULTS": "+8"})["UNDX_KNOWLEDGE_MAX_RESULTS"], 8)

    def test_a_value_outside_the_declared_choices_is_reported(self):
        resolution = c.resolve({"UNDX_KNOWLEDGE_MIN_TRUST_LEVEL": "teested"})
        self.assertTrue(any("MIN_TRUST_LEVEL" in note for note in resolution.notes))


class BoundsAreClampedNotObeyed(unittest.TestCase):
    def test_an_oversized_int_is_clamped_to_the_declared_maximum(self):
        resolution = c.resolve({"UNDX_KNOWLEDGE_MAX_CONTEXT_CHARS": "10000000"})
        flag = c.BY_NAME["UNDX_KNOWLEDGE_MAX_CONTEXT_CHARS"]
        self.assertEqual(resolution.values[flag.name], flag.maximum)
        self.assertTrue(any("above the maximum" in note for note in resolution.notes))

    def test_a_negative_int_is_clamped_to_the_declared_minimum(self):
        resolution = c.resolve({"UNDX_PLANNER_MAX_STEPS": "-4"})
        flag = c.BY_NAME["UNDX_PLANNER_MAX_STEPS"]
        self.assertEqual(resolution.values[flag.name], flag.minimum)
        self.assertTrue(any("below the minimum" in note for note in resolution.notes))

    def test_every_int_flag_declares_the_bounds_it_is_clamped_to(self):
        for flag in c.CATALOG:
            if flag.kind != "int":
                continue
            with self.subTest(name=flag.name):
                self.assertIsNotNone(flag.minimum, "int flag with no floor")
                self.assertIsNotNone(flag.maximum, "int flag with no ceiling")
                self.assertLessEqual(flag.minimum, flag.maximum)


class TheCatalogIsWellFormed(unittest.TestCase):
    def test_names_are_unique_and_prefixed(self):
        names = [flag.name for flag in c.CATALOG]
        self.assertEqual(len(set(names)), len(names))
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(name.startswith("UNDX_"))
                self.assertEqual(name, name.upper())

    def test_every_flag_explains_itself(self):
        for flag in c.CATALOG:
            with self.subTest(name=flag.name):
                self.assertGreater(len(flag.purpose.strip()), 40, "a purpose too short to be one")
                self.assertIn(flag.fail, ("open", "closed", "n/a"))
                self.assertTrue(flag.rollback.strip())
                self.assertTrue(flag.environments)

    def test_every_declared_default_is_itself_valid(self):
        # A default that its own validator rejects is a default nobody ever runs.
        resolution = c.resolve({})
        for flag in c.CATALOG:
            with self.subTest(name=flag.name):
                self.assertIn(flag.name, resolution.values)
                value = resolution.values[flag.name]
                if flag.kind == "int":
                    self.assertGreaterEqual(value, flag.minimum)
                    self.assertLessEqual(value, flag.maximum)
                if flag.choices:
                    self.assertIn(value, flag.choices)

    def test_an_empty_environment_produces_no_correction_notes(self):
        # Only "unset, running on the default" notes for required flags. A correction
        # note with no environment to correct would mean a default is malformed.
        for note in c.resolve({}).notes:
            with self.subTest(note=note):
                self.assertIn("is unset", note)

    def test_by_name_covers_the_catalog(self):
        self.assertEqual(set(c.BY_NAME), {flag.name for flag in c.CATALOG})


class TyposAreDetected(unittest.TestCase):
    def test_an_undeclared_undx_variable_is_reported(self):
        unknown = c.unknown_undx_brain_vars({"UNDX_BRAIN_ENABLE": "1"})
        self.assertIn("UNDX_BRAIN_ENABLE", unknown)

    def test_a_declared_variable_is_not_reported(self):
        self.assertEqual(c.unknown_undx_brain_vars({"UNDX_BRAIN_ENABLED": "1"}), ())

    def test_variables_owned_by_older_layers_are_not_reported_as_typos(self):
        # A detector that cries wolf about every pre-existing UNDX_ variable is one
        # somebody stops reading.
        self.assertEqual(c.unknown_undx_brain_vars({"UNDX_V5_SOMETHING": "1"}), ())

    def test_non_undx_variables_are_ignored_entirely(self):
        self.assertEqual(c.unknown_undx_brain_vars({"PATH": "/usr/bin", "HOME": "/root"}), ())

    def test_resolution_carries_the_unknowns(self):
        self.assertIn("UNDX_BRAIN_ENABLE", c.resolve({"UNDX_BRAIN_ENABLE": "1"}).unknown)


class TheReportWithholdsSecrets(unittest.TestCase):
    def test_a_secret_flag_never_reports_its_value(self):
        # No flag is marked secret today. The property under test is that the
        # withholding is structural — that a secret-bearing flag added later by somebody
        # who never read the docstring is covered anyway.
        secret = c.Flag(
            "UNDX_BRAIN_TEST_SECRET", "str", "",
            "Synthetic flag used only to prove the report withholds secret values.",
            fail="closed", secret=True,
        )
        original = c.CATALOG
        c.CATALOG = original + (secret,)
        try:
            rows = c.describe_for_report({"UNDX_BRAIN_TEST_SECRET": "hunter2"})
        finally:
            c.CATALOG = original
        row = next(r for r in rows if r["name"] == "UNDX_BRAIN_TEST_SECRET")
        self.assertTrue(row["set_in_environment"])
        self.assertNotIn("hunter2", str(row))
        self.assertEqual(row["effective"], "withheld (secret)")

    def test_the_report_covers_every_flag(self):
        rows = c.describe_for_report({})
        self.assertEqual({row["name"] for row in rows}, set(c.BY_NAME))

    def test_the_report_distinguishes_set_from_defaulted(self):
        rows = {r["name"]: r for r in c.describe_for_report({"UNDX_BRAIN_ENABLED": "1"})}
        self.assertTrue(rows["UNDX_BRAIN_ENABLED"]["set_in_environment"])
        self.assertFalse(rows["UNDX_KNOWLEDGE_MAX_RESULTS"]["set_in_environment"])

    def test_a_blank_value_counts_as_unset(self):
        # Railway stores a deleted variable as an empty string often enough that this
        # is the difference between "the operator configured it" and "they cleared it".
        rows = {r["name"]: r for r in c.describe_for_report({"UNDX_BRAIN_ENABLED": "  "})}
        self.assertFalse(rows["UNDX_BRAIN_ENABLED"]["set_in_environment"])


class ResolveNeverRaises(unittest.TestCase):
    def test_hostile_values_do_not_raise(self):
        hostile = {
            "UNDX_BRAIN_ENABLED": "\x00",
            "UNDX_KNOWLEDGE_MAX_RESULTS": "9" * 400,
            "UNDX_KNOWLEDGE_MIN_TRUST_LEVEL": "../../etc/passwd",
            "UNDX_PLANNER_MAX_STEPS": "NaN",
            "UNDX_SOURCE_CORPUS_PATH": "/etc/shadow",
        }
        resolution = c.resolve(hostile)
        self.assertEqual(set(resolution.values), set(c.BY_NAME))

    def test_resolve_reads_the_real_environment_when_given_none(self):
        # The default argument is the production path; if it were broken every test
        # above would still pass, because they all pass an explicit mapping.
        self.assertEqual(set(c.resolve().values), set(c.BY_NAME))

    def test_flags_and_resolve_agree(self):
        env = {"UNDX_BRAIN_ENABLED": "1", "UNDX_KNOWLEDGE_MAX_RESULTS": "3"}
        self.assertEqual(c.flags(env), c.resolve(env).values)


if __name__ == "__main__":
    unittest.main()
