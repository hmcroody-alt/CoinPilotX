"""The Foundation map, checked against the code it claims to describe.

A map of who-owns-what is worth exactly as much as its accuracy, and prose maps rot
silently: a module gets renamed, the document keeps naming it, and six months later
somebody builds a second confirmation store because the map said there wasn't one.
Two confirmation stores is a worse outcome than either of them alone.

So the map is ``(module, symbol)`` pairs and this file imports every one. The tests
that matter here are therefore the boring ones — ``verify().ok`` and
``verify().complete``. The rest exist to stop the map being made accurate by making
it vague: an UNOWNED entry must say what is missing, a PARTIAL entry must say which
part, and neither may be quietly upgraded to OWNED without deleting an assertion.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: Repository root, used by the drift tests that read a source file as text rather than
#: importing it — a claim about how many call sites write to a table cannot be checked
#: through the import system.
ROOT = Path(__file__).resolve().parents[2]

from tests.undx_agent import bootstrap  # noqa: E402

# Before importing anything under ``services``. One owner module reaches werkzeug
# through an unrelated import chain; without the shim it lands in ``unavailable`` and
# ``complete`` is False for a reason that has nothing to do with the map.
bootstrap.install()

from services.undx_brain import foundation as f  # noqa: E402


REPORT = f.verify()


class TheMapMatchesTheCode(unittest.TestCase):
    def test_every_claimed_owner_still_exists(self):
        self.assertTrue(
            REPORT.ok,
            "the Foundation map names code that is gone:\n  "
            + "\n  ".join(REPORT.missing),
        )

    def test_every_owner_was_actually_reachable(self):
        # Without this, ``ok`` could pass in an environment where most owners failed to
        # import and were therefore never checked.
        self.assertTrue(
            REPORT.complete,
            f"owners went unverified here: {REPORT.unavailable}",
        )

    def test_it_checked_a_serious_number_of_symbols(self):
        # A floor, not an exact count. It fails if the map is gutted, not if it grows.
        self.assertGreaterEqual(REPORT.checked, 100)
        self.assertGreaterEqual(len(f.FOUNDATION), 24)

    def test_require_is_silent_while_the_map_holds(self):
        f.require()

    def test_require_raises_rather_than_returning_falsy(self):
        # ``verify`` returns; ``require`` raises. A caller who ignores a return value
        # proceeds on a stale map, which is the failure this module exists to prevent.
        self.assertTrue(issubclass(f.FoundationError, AssertionError))


class TheMapCannotBeMadeVague(unittest.TestCase):
    def test_keys_are_unique(self):
        keys = [item.key for item in f.FOUNDATION]
        self.assertEqual(len(set(keys)), len(keys))

    def test_every_responsibility_states_what_it_is(self):
        for item in f.FOUNDATION:
            with self.subTest(key=item.key):
                self.assertTrue(item.key.strip())
                self.assertTrue(item.summary.strip())
                self.assertIsInstance(item.ownership, f.Ownership)

    def test_the_map_still_admits_to_holes(self):
        # Not "there is an UNOWNED entry" — that would make closing the last one look
        # like a regression, and would reward inventing a hole to keep the test green.
        # The property is that the map has not quietly declared itself finished.
        self.assertTrue(
            f.gaps(), "a map with no holes in it is a map that stopped looking"
        )

    def test_an_unowned_responsibility_names_the_gap_and_no_owner(self):
        for item in [i for i in f.FOUNDATION if i.ownership is f.Ownership.UNOWNED]:
            with self.subTest(key=item.key):
                self.assertEqual(item.owners, ())
                self.assertTrue(item.gap.strip(), "UNOWNED without stating what is missing")

    def test_a_partial_responsibility_names_which_part(self):
        for item in f.FOUNDATION:
            if item.ownership is not f.Ownership.PARTIAL:
                continue
            with self.subTest(key=item.key):
                self.assertTrue(item.owners, "PARTIAL but names nobody")
                self.assertTrue(item.gap.strip(), "PARTIAL without stating which part")

    def test_an_owned_responsibility_names_someone(self):
        for item in f.FOUNDATION:
            if item.ownership is not f.Ownership.OWNED:
                continue
            with self.subTest(key=item.key):
                self.assertTrue(item.owners)

    def test_owner_pairs_are_well_formed(self):
        for item in f.FOUNDATION:
            for pair in item.owners:
                with self.subTest(key=item.key, pair=pair):
                    self.assertEqual(len(pair), 2)
                    module_name, symbol = pair
                    self.assertTrue(module_name.startswith("services."))
                    self.assertTrue(symbol.strip())


class TheKnownGapsAreRecorded(unittest.TestCase):
    """Naming these in a test is the point.

    Closing one means deleting its assertion here, which is a deliberate act with a
    diff attached. Leaving one open costs nothing and stays visible. The alternative —
    gaps living only in a ``gap`` string nobody greps — is how they get forgotten.
    """

    def test_memory_isolation_has_an_owner_but_not_yet_the_whole_estate(self):
        # This assertion used to read "still unowned". ``services.undx_brain.memory``
        # closed the rule itself, so the honest state is PARTIAL: the module owns the
        # isolation, and the pre-existing hand-written owner clauses have not been
        # migrated onto it. Upgrading this to OWNED before that migration would be the
        # exact false claim the Foundation exists to prevent.
        item = f.by_key("memory_isolation")
        self.assertIsNotNone(item)
        self.assertIs(item.ownership, f.Ownership.PARTIAL)
        self.assertIn("memory_isolation", REPORT.partial)
        self.assertIn(
            ("services.undx_brain.memory", "open_scope"),
            item.owners,
            "the map must name the module that actually enforces the scope",
        )

    def test_the_partial_responsibilities_are_the_ones_we_know_about(self):
        expected = {
            "planning",
            "prompt_injection_boundary",
            "skill_lifecycle",
            "evidence_state_machine",
            "qa_gating",
            "memory_isolation",
        }
        self.assertTrue(
            expected.issubset(set(REPORT.partial)),
            f"a known gap was reclassified without updating this test: "
            f"expected {sorted(expected)}, report has {sorted(REPORT.partial)}",
        )

    def test_gaps_returns_everything_not_fully_owned(self):
        keys = {item.key for item in f.gaps()}
        self.assertEqual(keys, set(REPORT.partial) | set(REPORT.unowned))
        self.assertNotIn("governed_gateway", keys)


class LookupsWork(unittest.TestCase):
    def test_by_key_finds_a_known_responsibility(self):
        item = f.by_key("governed_gateway")
        self.assertIsNotNone(item)
        self.assertIs(item.ownership, f.Ownership.OWNED)

    def test_by_key_returns_none_rather_than_raising(self):
        self.assertIsNone(f.by_key("no_such_responsibility"))

    def test_owning_modules_is_sorted_and_deduplicated(self):
        modules = f.owning_modules()
        self.assertEqual(list(modules), sorted(set(modules)))
        self.assertIn("services.undx_tool_gateway", modules)

    def test_the_map_covers_the_undx_service_layer_broadly(self):
        # PART 1 says map what exists. A map naming three modules would pass every test
        # above and describe almost nothing.
        self.assertGreaterEqual(len(f.owning_modules()), 15)


class TheCognitiveEntriesSayTrueThings(unittest.TestCase):
    """The entries added for the cognitive subsystems make countable claims.

    A gap string is prose, and prose about a codebase goes stale silently — the numbers
    stay written down long after the code stops matching them. Every figure asserted
    below is recomputed from the code on each run, so the map cannot drift away from
    what it describes without something going red. Closing a gap should mean editing the
    entry *and* this test, which is the deliberate act the class above is built around.
    """

    def test_every_memory_class_named_in_part_seven_has_an_entry(self):
        # The strong form: if an eighth kind is ever added to ``MemoryKind``, the map
        # gains a hole and this fails. A weaker test that only checked the seven that
        # exist today would pass forever.
        from services.undx_brain import memory as m

        keys = {item.key for item in f.FOUNDATION}
        for kind in m.MemoryKind:
            with self.subTest(kind=kind.value):
                self.assertIn(f"memory_{kind.value}", keys)

    def test_the_learning_event_class_now_has_a_reader_and_still_says_what_is_missing(self):
        # This test read ``..._is_unowned_and_says_why`` until the reader was built, and
        # the rename is the point: an entry moving off UNOWNED is a deliberate act, so
        # the assertion that pinned it there had to be rewritten by hand rather than
        # quietly relaxed. What replaces it is not weaker. It checks that the module
        # named as owner is the one that exists, that the entry still says the three
        # things that remain untrue of it, and that the map now has no unowned entries
        # at all — which is a stronger claim than the one it replaces, and one that goes
        # red the moment a new hole is opened anywhere in the map.
        item = f.by_key("memory_learning_event")
        self.assertIsNotNone(item)
        self.assertIs(item.ownership, f.Ownership.PARTIAL)

        owners = {module for module, _ in item.owners}
        self.assertIn("services.undx_brain.learning", owners)
        self.assertIn("services.undx_brain.memory", owners)

        # Named symbols must be importable, not merely spelled convincingly.
        from services.undx_brain import learning as L

        for module, symbol in item.owners:
            if module == "services.undx_brain.learning":
                with self.subTest(symbol=symbol):
                    self.assertTrue(hasattr(L, symbol), f"learning has no {symbol}")

        # The three things the entry admits are still missing. Reach is the one the next
        # task closes; attribution is a boundary and is not expected to close here.
        self.assertIn("Nothing calls ``learning``", item.gap)
        self.assertIn("UNDX_BRAIN_LEARNING_ENABLED", item.gap)
        self.assertIn("NULL ``user_id``", item.gap)

        self.assertEqual(
            REPORT.unowned, (),
            "an unowned entry appeared; the map is meant to have none left",
        )

    def test_the_learning_event_table_is_still_written_and_not_read(self):
        # The entry's claim, checked against the source rather than remembered: eleven
        # writes, and one reader inside the service that counts rows without opening
        # one. The claim used to live in ``.gap`` and now lives in ``.note``, because the
        # reader that was missing got built — but the asymmetry it describes did not
        # change, so the assertion follows the sentence rather than being dropped with
        # it. Both fields are searched: which of the two holds it is an editorial
        # decision, and a test that pinned the wrong one would fail for a reason that
        # says nothing about the system.
        service = (ROOT / "services" / "pulse_ai_service.py").read_text(encoding="utf-8")
        writes = len(re.findall(r"_record_learning_event\(", service)) - 1  # minus the def
        self.assertEqual(writes, 11, "the number of learning-event writers moved")
        item = f.by_key("memory_learning_event")
        self.assertIn("eleven call sites", f"{item.note}\n{item.gap}")

        selects = [
            line.strip()
            for line in service.splitlines()
            if "pulse_ai_learning_events" in line and "INSERT" not in line.upper()
            and "CREATE TABLE" not in line.upper()
        ]
        # One survivor: the admin dashboard's ``(table, key)`` pair, whose only query is
        # ``SELECT COUNT(*)``. Any other read means the class has a real consumer.
        self.assertEqual(
            len(selects), 1,
            f"a reader of pulse_ai_learning_events appeared: {selects}",
        )
        self.assertIn("COUNT(*)", service)

    def test_the_specialist_coverage_numbers_are_the_real_ones(self):
        from services import undx_capability_registry as registry
        from services import undx_domain_reasoning as domain

        item = f.by_key("specialist_domains")
        self.assertIsNotNone(item)
        self.assertEqual(len(domain.ANALYSERS), 10, "the analyser count moved")
        self.assertEqual(len(registry.REGISTRY), 80, "the capability count moved")
        self.assertIn("Ten analysers", item.gap)
        self.assertIn("eighty capabilities", item.gap)
        self.assertIn("forty-four product areas", item.gap)

    def test_every_analyser_is_keyed_by_a_capability_that_exists(self):
        # The entry claims analysers are bound to capability ids rather than to areas,
        # and gives a safety reason for it. An analyser keyed by an id nobody can
        # execute would make that claim decorative.
        from services import undx_capability_registry as registry
        from services import undx_domain_reasoning as domain

        for capability_id in sorted(domain.ANALYSERS):
            with self.subTest(capability_id=capability_id):
                self.assertIn(capability_id, registry.REGISTRY)

    def test_the_simulator_predicts_the_same_thing_regardless_of_the_resource(self):
        # ``prediction`` is recorded as PARTIAL on the grounds that
        # ``simulate_operation`` reads no state. That is a claim about behaviour, so it
        # is checked as one: two calls naming different resources come back identical
        # apart from the arguments echoed straight back.
        from services import undx_architecture

        item = f.by_key("prediction")
        self.assertIs(item.ownership, f.Ownership.PARTIAL)

        tool = "pulsesoc.crypto_alerts.list"
        first = undx_architecture.simulate_operation(tool, {"limit": 5})
        second = undx_architecture.simulate_operation(tool, {"limit": 500})
        for field_name in ("predicted_outcome", "uncertainty", "mitigation", "assumptions"):
            with self.subTest(field=field_name):
                self.assertEqual(first[field_name], second[field_name])
        self.assertFalse(first["production_write"])

    def test_the_new_entries_all_state_a_gap_or_a_reason(self):
        # PARTIAL and UNOWNED entries are required to explain themselves elsewhere in
        # this file; this pins the specific keys added for the cognitive subsystems so
        # that adding one without a gap is caught here rather than diluting the map.
        added = (
            "action_selection", "prediction", "metacognition",
            "specialist_domains", "homeostasis",
            "memory_conversation", "memory_preference", "memory_task_state",
            "memory_fact", "memory_relationship", "memory_approval",
            "memory_learning_event",
        )
        for key in added:
            with self.subTest(key=key):
                item = f.by_key(key)
                self.assertIsNotNone(item, f"{key} is missing from the map")
                if item.ownership is f.Ownership.OWNED:
                    self.assertTrue(item.note.strip(), f"{key} is OWNED without a note")
                else:
                    self.assertTrue(item.gap.strip(), f"{key} states no gap")

    def test_the_fact_entry_describes_what_record_fact_actually_does(self):
        # The gap for ``memory_fact`` rests on a specific, checkable claim about the
        # existing store: it flags two sources agreeing and misses two sources
        # disagreeing. Asserting it here rather than trusting the prose means that if
        # ``record_fact`` is ever fixed, the entry claiming it is broken goes red.
        import sqlite3

        from services import undx_architecture as architecture

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()
        try:
            architecture.ensure_schema(cursor)
            first = architecture.record_fact(
                cursor, "btc alert threshold is 50000", "crypto.alerts.get", 0.9, 7)
            agreeing = architecture.record_fact(
                cursor, "btc alert threshold is 50000", "user_statement", 0.5, 7)
            disagreeing = architecture.record_fact(
                cursor, "btc alert threshold is 60000", "crypto.alerts.get", 0.9, 7)
        finally:
            connection.close()

        self.assertEqual(agreeing["contradictions"], [first["fact_id"]])
        self.assertEqual(agreeing["status"], "review")
        self.assertEqual(disagreeing["contradictions"], [])
        self.assertEqual(disagreeing["status"], "active")

        gap = f.by_key("memory_fact").gap
        self.assertIn("status='review'", gap)
        self.assertIn("status='active'", gap)
        self.assertIn("UNDX_BRAIN_FACTS_ENABLED", gap)

    def test_the_fact_entry_names_every_module_that_calls_it(self):
        # This read ``..._is_still_honest_that_nothing_calls_it`` and asserted the list
        # of importers was empty, which was the claim most likely to go stale — because
        # the thing that would make it stale is the next task doing its job. It did:
        # ``learning`` calls ``facts.read`` and ``facts.parse_moment``. So the test no
        # longer asserts *nobody* calls it. It asserts something that stays useful as
        # callers accumulate: every module that imports ``facts`` is named in the entry
        # that claims to describe its reach. A twelfth caller appearing without being
        # written down is still a failure; a caller appearing *and being described* is
        # not, and that is the difference between a map and a padlock.
        importers = sorted(
            path.stem
            for path in (ROOT / "services").rglob("*.py")
            if path.name != "facts.py"
            and re.search(
                r"^\s*(from\s+\.\s+import\s+facts|from\s+\.facts\s+import"
                r"|from\s+services\.undx_brain\s+import\s+.*\bfacts\b"
                r"|import\s+services\.undx_brain\.facts)",
                path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
        # Without this the loop below is vacuous, and it would go vacuous for the least
        # interesting reason imaginable: the import regex drifting out of step with how
        # somebody happens to spell an import. A test that passes by iterating over
        # nothing is worse than no test, because it reports green while checking air.
        self.assertIn(
            "learning", importers,
            "learning calls facts.read and facts.parse_moment; if it is not in this "
            "list the detector is broken, not the code",
        )

        gap = f.by_key("memory_fact").gap
        for module in importers:
            with self.subTest(module=module):
                self.assertIn(
                    module, gap,
                    f"services/undx_brain/{module}.py imports facts and the memory_fact "
                    f"entry does not mention it",
                )

        # The part of the original claim that has *not* gone stale, and is the one that
        # actually matters: every caller so far is itself flag-gated, so the reach of
        # ``facts`` into a real request is still zero. ``learning`` calling it is one
        # dark module calling another.
        self.assertIn("Nothing on the live path calls either", gap)
        self.assertIn("UNDX_BRAIN_FACTS_ENABLED", gap)


class ThePackageNamesItsOwnModules(unittest.TestCase):
    """``__all__`` in ``services/undx_brain/__init__.py`` lists every module present.

    Its docstring says a test walks the directory and compares, so this is that test. A
    package whose ``__all__`` has silently fallen behind is a small thing on its own and
    a reliable sign that a module was added without anybody looking at the package as a
    whole — which is the same failure the Foundation map exists to catch one level up.
    """

    def test_every_module_on_disk_is_named_and_every_name_exists(self):
        import services.undx_brain as package

        on_disk = sorted(
            path.stem
            for path in (ROOT / "services" / "undx_brain").glob("*.py")
            if path.stem != "__init__"
        )
        self.assertEqual(
            sorted(package.__all__), on_disk,
            "__all__ and the modules on disk have drifted apart",
        )

    def test_the_names_are_listed_in_a_deliberate_order_not_alphabetically(self):
        # Sorted order would mean the list is generated rather than curated, and the
        # comment above it claims a reading order. This keeps the two agreeing.
        import services.undx_brain as package

        self.assertNotEqual(list(package.__all__), sorted(package.__all__))
        self.assertEqual(package.__all__[0], "config")
        self.assertEqual(package.__all__[1], "truth")


if __name__ == "__main__":
    unittest.main()
