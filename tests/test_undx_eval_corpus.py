"""The corpus grades what it claims to grade, and no case is decorative."""

from __future__ import annotations

import unittest

from services import undx_eval_corpus as corpus
from services.undx_privacy import PRIVACY_SYNTHETIC


class CorpusSelfCheckTest(unittest.TestCase):
    """Every grader must separate its own right answer from its own wrong one."""

    def test_every_case_is_sound(self):
        result = corpus.self_check()
        self.assertTrue(result["ok"], f"unsound cases: {result['broken']}")
        self.assertEqual(result["broken"], [])

    def test_the_self_check_can_fail(self):
        """The check above is only worth running if it can come back false.

        A `self_check` that returned ok for anything would pass on an empty
        corpus and on a corpus of graders that accept every string, which is
        precisely the benchmark this module exists to not be.
        """
        vacuous = corpus.Case(
            id="vacuous", lane="general_builder",
            prompt="Does this matter?",
            assertions=(("regex", r".*"),),
            right="yes", wrong="no")
        self.assertTrue(corpus.grade(vacuous, vacuous.right)["passed"])
        self.assertTrue(corpus.grade(vacuous, vacuous.wrong)["passed"])

        original = corpus.CASES
        try:
            corpus.CASES = original + (vacuous,)
            result = corpus.self_check()
        finally:
            corpus.CASES = original
        self.assertFalse(result["ok"])
        self.assertIn("accepts its own wrong answer",
                      [row["problem"] for row in result["broken"]])

    def test_case_ids_are_unique(self):
        ids = [case.id for case in corpus.CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_declares_a_known_lane(self):
        for case in corpus.CASES:
            with self.subTest(case=case.id):
                self.assertIn(case.lane, corpus.LANES)

    def test_every_case_has_at_least_one_assertion(self):
        for case in corpus.CASES:
            with self.subTest(case=case.id):
                self.assertTrue(case.assertions)

    def test_every_assertion_kind_is_implemented(self):
        for case in corpus.CASES:
            for kind, _ in case.assertions:
                with self.subTest(case=case.id, kind=kind):
                    self.assertIn(kind, corpus.GRADERS)

    def test_an_unknown_assertion_kind_raises_rather_than_marking_wrong(self):
        """A broken case must not be charged to the provider.

        If `grade` returned `passed: False` for a kind it does not implement,
        a typo in the corpus would read as a provider failing every case — and
        the provider would be demoted for the author's mistake.
        """
        broken = corpus.Case(id="broken", lane="general_builder", prompt="x",
                             assertions=(("nonexistent", 1),), right="a", wrong="b")
        with self.assertRaises(KeyError):
            corpus.grade(broken, "anything")


class GraderTest(unittest.TestCase):
    """The primitives, at the edges where a lenient one would hide a wrong answer."""

    def test_exact_ignores_punctuation_but_not_extra_words(self):
        case = corpus.CASES_BY_ID["fast.exact_token"]
        for answer in ("ACKNOWLEDGED", "acknowledged.", "  Acknowledged!  "):
            with self.subTest(answer=answer):
                self.assertTrue(corpus.grade(case, answer)["passed"])
        for answer in ("Sure, acknowledged.", "ACKNOWLEDGED — anything else?",
                       "I acknowledge", ""):
            with self.subTest(answer=answer):
                self.assertFalse(corpus.grade(case, answer)["passed"])

    def test_number_rejects_an_answer_that_hedges_between_two(self):
        """"Contains the number" would pass a model that lists every candidate."""
        case = corpus.CASES_BY_ID["fast.number_only"]
        self.assertTrue(corpus.grade(case, "366")["passed"])
        self.assertTrue(corpus.grade(case, "The answer is 366.")["passed"])
        self.assertFalse(corpus.grade(case, "365 or 366")["passed"])
        self.assertFalse(corpus.grade(case, "It depends.")["passed"])

    def test_number_handles_thousands_separators_and_decimals(self):
        self.assertTrue(corpus._number("1,200", 1200))
        self.assertTrue(corpus._number("0.0156", 0.0156))
        self.assertFalse(corpus._number("0.0157", 0.0156))

    def test_words_match_on_word_boundaries_not_substrings(self):
        """The failure this module names in its own docstring.

        "no" inside "nobody" is not the word no, and a grader that thinks it is
        marks a refusal case passed for an answer that refused nothing.
        """
        self.assertFalse(corpus._words("nobody knows", ["no"]))
        self.assertTrue(corpus._words("No, it should not.", ["no"]))

    def test_words_none_catches_the_forbidden_word_anywhere(self):
        case = corpus.CASES_BY_ID["product.negative_constraint"]
        self.assertFalse(corpus.grade(
            case, "A loading indicator says work continues.")["passed"])
        self.assertTrue(corpus.grade(
            case, "It says the system is still working.")["passed"])

    def test_a_forbidden_word_is_a_word_not_a_substring(self):
        """Mutation testing found this guarantee untested.

        Every `words_none` case happened to use a forbidden word that never
        appeared inside a longer one, so a grader matching substrings passed
        the whole suite — and would have rejected "proof of stake networks"
        for containing "work". That direction of error is the expensive one:
        it marks a correct provider wrong.
        """
        self.assertTrue(corpus._no_words("proof of stake networks", ["work"]))
        self.assertTrue(corpus._no_words("the framework is fine", ["work"]))
        self.assertFalse(corpus._no_words("proof of work", ["work"]))
        self.assertTrue(corpus._no_words("reloading the page", ["loading"]))
        self.assertFalse(corpus._no_words("loading the page", ["loading"]))

    def test_json_keys_accepts_a_fenced_block_and_rejects_prose(self):
        case = corpus.CASES_BY_ID["auto.json_object"]
        fenced = '```json\n{"action": "restart", "target": "billing worker"}\n```'
        self.assertTrue(corpus.grade(case, fenced)["passed"])
        self.assertTrue(corpus.grade(
            case, 'Here you go: {"action": "restart", "target": "worker"}')["passed"])
        self.assertFalse(corpus.grade(case, '{"action": "restart"}')["passed"])
        self.assertFalse(corpus.grade(case, "action: restart")["passed"])
        self.assertFalse(corpus.grade(case, "[1, 2, 3]")["passed"])

    def test_max_words_rejects_an_empty_answer(self):
        """Zero words is under every ceiling and is not a summary."""
        self.assertFalse(corpus._max_words("", 8))
        self.assertFalse(corpus._max_words("   ", 8))
        self.assertTrue(corpus._max_words("four words go here", 8))
        self.assertFalse(corpus._max_words("a b c d e f g h i", 8))

    def test_all_assertions_must_hold(self):
        """Partial credit would let a provider ignore the format and still win."""
        case = corpus.CASES_BY_ID["product.exact_count"]
        self.assertTrue(corpus.grade(case, "red, green, blue")["passed"])
        # Right colours, wrong format.
        marked = corpus.grade(case, "The primary colours are red, green and blue.")
        self.assertFalse(marked["passed"])
        self.assertIn("regex", marked["failed"])


class HallucinationCaseTest(unittest.TestCase):
    """The two cases that catch a confident answer to an unanswerable question."""

    def test_false_premise_requires_rejecting_the_premise(self):
        case = corpus.CASES_BY_ID["research.false_premise"]
        for answer in ("Turing never won a Nobel Prize.",
                       "He did not win one; the Nobel has no computing category.",
                       "Alan Turing was not awarded a Nobel Prize."):
            with self.subTest(answer=answer):
                self.assertTrue(corpus.grade(case, answer)["passed"])
        for answer in ("1952.", "Alan Turing won it in 1936 for his work on computability."):
            with self.subTest(answer=answer):
                self.assertFalse(corpus.grade(case, answer)["passed"])

    def test_missing_input_case_rejects_an_invented_summary(self):
        case = corpus.CASES_BY_ID["gen.admit_missing_input"]
        self.assertTrue(corpus.grade(
            case, "I don't see an attachment, so there is nothing to summarise.")["passed"])
        self.assertFalse(corpus.grade(
            case, "Revenue grew 12% with margin expansion across segments.")["passed"])


class CorpusShapeTest(unittest.TestCase):

    def test_the_corpus_carries_no_user_content(self):
        """Everything here is a module constant, so the class is SYNTHETIC.

        If this ever becomes parameterised, the privacy class stops being a
        property of the module and starts being a property of the caller — and
        a benchmark sends the same text to every provider at once, including
        ones a ceiling would have refused.
        """
        self.assertEqual(corpus.PRIVACY_CLASS, PRIVACY_SYNTHETIC)
        self.assertIsInstance(corpus.CASES, tuple)

    def test_current_web_is_not_a_lane(self):
        """A frozen corpus cannot grade freshness without eventually lying.

        Any case whose right answer changes with the date becomes, months
        later, a case that marks the correct provider wrong — and it does so
        silently, with a number that still looks like a measurement.
        """
        self.assertNotIn("current_web", corpus.LANES)
        for case in corpus.CASES:
            self.assertNotEqual(case.lane, "current_web")

    def test_no_lane_is_declared_but_unpopulated(self):
        """A lane with zero cases would render as a scoreless row forever."""
        for lane, count in corpus.lane_counts().items():
            with self.subTest(lane=lane):
                self.assertGreater(count, 0)

    def test_filtering_is_stable_and_narrowing(self):
        every = corpus.cases()
        self.assertEqual(every, corpus.CASES)
        repository = corpus.cases(lane="repository")
        self.assertTrue(repository)
        self.assertTrue(all(c.lane == "repository" for c in repository))
        self.assertEqual(repository, corpus.cases(lane="repository"))
        tagged = corpus.cases(tag="hallucination")
        self.assertTrue(tagged)
        self.assertTrue(all("hallucination" in c.tags for c in tagged))

    def test_cases_are_frozen(self):
        """Results are keyed by case id, so a mutated case silently rewrites
        the meaning of every stored run that referenced it."""
        with self.assertRaises(Exception):
            corpus.CASES[0].prompt = "something else"  # type: ignore[misc]

    def test_max_tokens_leaves_room_for_the_right_answer(self):
        """A budget too small marks a correct provider wrong for being cut off.

        Four characters per token is the same rough rule `undx_benchmark`
        estimates with; the margin asked for here is 3x, because the check is
        for an obviously-too-small budget rather than an exact fit.
        """
        for case in corpus.CASES:
            with self.subTest(case=case.id):
                needed = max(1, len(case.right) // 4)
                self.assertGreaterEqual(case.max_tokens, needed * 3)


if __name__ == "__main__":
    unittest.main()
