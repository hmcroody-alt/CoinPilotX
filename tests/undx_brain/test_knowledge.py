"""Bounded retrieval, tested for its bounds first and its ranking second.

The ranking tests here name specific modules, which makes them look brittle. They are
deliberate: each one encodes a retrieval bug that was found and fixed while building this
layer, and each would silently return if the scorer were changed carelessly.

* "the tool gateway" returned ``bot.py`` and five ``mobile-native`` files, because path
  segments kept underscores whole and ``undx_tool_gateway`` never matched ``gateway``
* "how does verification work" ranked three audit scripts first, because ``work`` was
  substring-matched against ``/api/network/…`` and scored as an endpoint hit
* every query returned six arbitrary files, because ~900 records tied at one point and
  the alphabetical tie-break — which exists for reproducibility — then chose among them

The bounds tests matter more. A retrieval layer that ranks imperfectly gives a worse
answer; one that ignores its ceilings puts 1.4 MB of repository index into a prompt.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_brain import knowledge as k  # noqa: E402
from services.undx_brain.corpus import ingest  # noqa: E402
from services.undx_brain.truth import TrustLevel, rank  # noqa: E402


CORPUS = ingest()


class BoundsAreHard(unittest.TestCase):
    """Configuration may lower the ceilings. It may never raise them."""

    def test_result_count_cannot_exceed_the_module_ceiling(self):
        result = k.retrieve(
            "alert", env={"UNDX_KNOWLEDGE_MAX_RESULTS": "500"}, corpus=CORPUS
        )
        self.assertLessEqual(result.applied_limit, k.MAX_RESULTS)
        self.assertLessEqual(len(result.records), k.MAX_RESULTS)

    def test_char_budget_cannot_exceed_the_module_ceiling(self):
        result = k.retrieve(
            "alert", env={"UNDX_KNOWLEDGE_MAX_CONTEXT_CHARS": "999999"}, corpus=CORPUS
        )
        self.assertLessEqual(result.applied_char_limit, k.MAX_CONTEXT_CHARS)

    def test_an_explicit_caller_limit_is_also_clamped(self):
        result = k.retrieve("alert", limit=10_000, char_limit=10_000, corpus=CORPUS)
        self.assertLessEqual(result.applied_limit, k.MAX_RESULTS)
        self.assertLessEqual(result.applied_char_limit, k.MAX_CONTEXT_CHARS)

    def test_the_prompt_block_stays_inside_its_budget(self):
        result = k.retrieve("alert notification settings", corpus=CORPUS)
        block = result.prompt_block()
        self.assertLessEqual(len(block), k.MAX_CONTEXT_CHARS + 800)

    def test_a_broad_query_does_not_return_the_whole_corpus(self):
        # The failure this guards against is not slowness. It is a model handed 1,682
        # summaries finding one adjacent to any question and answering from it.
        result = k.retrieve("services api data user", corpus=CORPUS)
        self.assertLessEqual(len(result.records), k.MAX_RESULTS)
        self.assertLess(len(result.records), len(CORPUS.records) / 100)

    def test_a_lowered_limit_is_honoured(self):
        result = k.retrieve("alert", env={"UNDX_KNOWLEDGE_MAX_RESULTS": "2"}, corpus=CORPUS)
        self.assertEqual(result.applied_limit, 2)
        self.assertLessEqual(len(result.records), 2)


class TrustFloorIsEnforced(unittest.TestCase):
    def test_source_discovered_is_excluded_by_default(self):
        result = k.retrieve("scripts audit", corpus=CORPUS)
        for record in result.records:
            with self.subTest(path=record.path):
                self.assertIsNot(record.trust_level, TrustLevel.SOURCE_DISCOVERED)

    def test_raising_the_floor_removes_weaker_records(self):
        low = k.retrieve("alert", env={"UNDX_KNOWLEDGE_MIN_TRUST_LEVEL": "source_mapped"}, corpus=CORPUS)
        high = k.retrieve("alert", env={"UNDX_KNOWLEDGE_MIN_TRUST_LEVEL": "tested"}, corpus=CORPUS)
        self.assertLessEqual(len(high.records), len(low.records))
        for record in high.records:
            with self.subTest(path=record.path):
                self.assertIn(record.trust_level, (TrustLevel.TESTED, TrustLevel.LIVE_VERIFIED, TrustLevel.RUNTIME_CANONICAL))

    def test_an_unparseable_floor_becomes_stricter_not_looser(self):
        # The generic config resolver substitutes the shipped default for an unrecognised
        # value. Applied here that is a fail-open: an operator *raising* the floor who
        # typos it lands back on the default, and finds out when UNDX says something the
        # floor existed to prevent.
        result = k.retrieve(
            "alert", env={"UNDX_KNOWLEDGE_MIN_TRUST_LEVEL": "totally_bogus"}, corpus=CORPUS
        )
        self.assertIs(result.applied_min_trust, TrustLevel.DOCUMENTED)

    def test_an_unparseable_floor_is_reported_not_silently_corrected(self):
        result = k.retrieve(
            "alert", env={"UNDX_KNOWLEDGE_MIN_TRUST_LEVEL": "teested"}, corpus=CORPUS
        )
        self.assertTrue(
            any("MIN_TRUST_LEVEL" in note for note in result.notes),
            f"expected the rejected floor to be reported, got {result.notes}",
        )

    def test_a_valid_floor_is_not_second_guessed(self):
        # The correction above must not fire on the values it exists to protect.
        for name in ("source_mapped", "documented", "tested"):
            with self.subTest(name=name):
                result = k.retrieve(
                    "alert", env={"UNDX_KNOWLEDGE_MIN_TRUST_LEVEL": name}, corpus=CORPUS
                )
                self.assertIs(result.applied_min_trust, TrustLevel(name))
                self.assertEqual(result.notes, ())

    def test_exclusions_are_reported_rather_than_silent(self):
        result = k.retrieve("scripts audit", corpus=CORPUS)
        self.assertTrue(
            result.withheld,
            "retrieval that silently discards matches is indistinguishable from "
            "retrieval that found nothing",
        )


class NeverEstablishesAccountState(unittest.TestCase):
    def test_may_claim_live_state_is_false_even_on_a_rich_result(self):
        result = k.retrieve("pause a crypto alert endpoint", corpus=CORPUS)
        self.assertTrue(result.records, "expected the corpus to know about alerts")
        self.assertFalse(result.may_claim_live_state)

    def test_it_is_false_on_an_empty_result_too(self):
        self.assertFalse(k.retrieve("", corpus=CORPUS).may_claim_live_state)

    def test_results_carry_a_hedge(self):
        result = k.retrieve("tool gateway", corpus=CORPUS)
        self.assertTrue(result.hedge().strip())

    def test_the_hedge_reflects_the_weakest_result_not_the_best(self):
        result = k.retrieve("tool gateway", corpus=CORPUS)
        weakest = result.weakest_trust
        self.assertIsNotNone(weakest)
        for record in result.records:
            with self.subTest(path=record.path):
                self.assertGreaterEqual(
                    rank(record.trust_level), rank(weakest)
                )


class ProvenanceSurvives(unittest.TestCase):
    def test_every_result_can_be_cited(self):
        result = k.retrieve("capability registry", corpus=CORPUS)
        self.assertTrue(result.records)
        self.assertEqual(len(result.citations()), len(result.records))
        for citation in result.citations():
            self.assertTrue(citation.strip())

    def test_the_prompt_block_names_the_file_and_its_trust(self):
        result = k.retrieve("tool gateway", corpus=CORPUS)
        block = result.prompt_block()
        self.assertIn("services/undx_tool_gateway.py", block)
        self.assertIn("trust=", block)

    def test_the_legacy_slice_shape_keeps_provenance_in_the_title(self):
        # A consumer that renders only title and body must not lose the trust level.
        slices = k.retrieve("tool gateway", corpus=CORPUS).as_knowledge_slices()
        self.assertTrue(slices)
        for item in slices:
            with self.subTest(item=item["title"]):
                self.assertEqual(set(item), {"id", "title", "category", "body"})
                self.assertIn("trust=", item["title"])


class DegradesRatherThanFails(unittest.TestCase):
    def test_disabled_retrieval_returns_an_empty_result_with_a_reason(self):
        result = k.retrieve("alert", env={"UNDX_KNOWLEDGE_RETRIEVAL_ENABLED": "0"})
        self.assertTrue(result.degraded)
        self.assertTrue(result.reason)
        self.assertEqual(result.records, ())

    def test_a_missing_corpus_degrades_instead_of_raising(self):
        result = k.retrieve(
            "alert", env={"UNDX_SOURCE_CORPUS_PATH": "backend/undx/config/absent.yaml"}
        )
        self.assertTrue(result.degraded)
        self.assertEqual(result.records, ())

    def test_a_stopword_only_query_returns_nothing_without_degrading(self):
        result = k.retrieve("what is the", corpus=CORPUS)
        self.assertFalse(result.degraded)
        self.assertEqual(result.records, ())

    def test_nonsense_returns_nothing_rather_than_something(self):
        self.assertEqual(k.retrieve("qwertyuiop zxcvbnm", corpus=CORPUS).records, ())

    def test_retrieve_never_raises(self):
        for query in ("", None, 0, "x", "/" * 500, "a" * 5000, "🙂🙂🙂"):
            with self.subTest(query=repr(query)[:40]):
                k.retrieve(query, corpus=CORPUS)


class FlagGatesTheIntegration(unittest.TestCase):
    """An unconfigured deployment must behave exactly as it does today."""

    def test_slices_are_empty_while_the_knowledge_stage_is_off(self):
        self.assertEqual(k.knowledge_slices("tool gateway", env={}), [])

    def test_slices_appear_once_the_stage_is_enabled(self):
        slices = k.knowledge_slices(
            "tool gateway", env={"UNDX_BRAIN_KNOWLEDGE_ENABLED": "1"}
        )
        self.assertTrue(slices)


class RankingRegressions(unittest.TestCase):
    """Each of these is a bug that shipped in an earlier draft of the scorer."""

    def test_a_concept_finds_its_module_despite_the_underscored_filename(self):
        result = k.retrieve(
            "how does the tool gateway decide what UNDX is allowed to run", corpus=CORPUS
        )
        self.assertEqual(result.records[0].path, "services/undx_tool_gateway.py")

    def test_capability_registry_finds_the_registry_module(self):
        result = k.retrieve("what is the capability registry", corpus=CORPUS)
        self.assertEqual(result.records[0].path, "services/undx_capability_registry.py")

    def test_knowledge_map_finds_the_knowledge_map(self):
        result = k.retrieve("knowledge map", corpus=CORPUS)
        self.assertEqual(result.records[0].path, "services/undx_knowledge_map.py")

    def test_an_exact_path_query_returns_that_file_first(self):
        result = k.retrieve("services/undx_tool_gateway.py", corpus=CORPUS)
        self.assertEqual(result.records[0].path, "services/undx_tool_gateway.py")

    def test_an_ordinary_word_does_not_score_as_a_route_match(self):
        # "work" is a substring of "/api/network/...". Before the fix it earned the full
        # endpoint weight and buried the modules actually named for the concept.
        terms, routes = k._terms("how does verification work")
        self.assertIn("verification", terms)
        self.assertIn("work", terms)
        self.assertEqual(routes, frozenset())

    def test_a_route_shaped_query_does_score_as_a_route_match(self):
        _, routes = k._terms("/api/alerts/pause")
        self.assertIn("alerts", routes)

    def test_the_relevance_floor_drops_incidental_one_word_matches(self):
        result = k.retrieve("services/undx_tool_gateway.py", corpus=CORPUS)
        self.assertLessEqual(len(result.records), 3)
        self.assertTrue(
            any("relevance floor" in note for note in result.withheld),
            f"expected the floor to be reported, got {result.withheld}",
        )

    def test_ordering_is_stable_across_calls(self):
        first = [r.path for r in k.retrieve("alert engine", corpus=CORPUS).records]
        second = [r.path for r in k.retrieve("alert engine", corpus=CORPUS).records]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
