"""The benchmark must refuse to be evidence when it isn't.

Every test here is about one of two failure modes, because they are the two
that turn a benchmark into a rubber stamp:

  * scoring a provider for something that was not its answer (a 429 counted as
    a wrong answer, a skipped provider rendered as 0.0), and
  * producing a ranking from a sample too small to support one.
"""

from __future__ import annotations

import types
import unittest
from unittest import mock

import requests

from services import undx_benchmark as bench
from services import undx_eval_corpus as corpus


class _Config:
    def __init__(self, label):
        self.label = label


def _fake_router(answers, *, enabled=True, keys=True, model="test-model-1"):
    """A router whose CALLERS return canned text per provider.

    `answers` maps provider -> (case_id -> answer | Exception | ""). Anything
    missing falls back to the case's own right answer, so a test only has to
    describe the deviation it is about.
    """
    names = list(answers)

    def caller_for(name):
        def call(system, message, history, timeout, **kwargs):
            case = next(c for c in corpus.CASES
                        if c.prompt == kwargs.get("user_content"))
            reply = answers[name].get(case.id, case.right)
            if isinstance(reply, Exception):
                raise reply
            return {"text": reply, "model": model,
                    "usage": {"provider": name, "model": model,
                              "input_tokens": 10, "output_tokens": 5,
                              "cost_usd": 0.0001}}
        return call

    return types.SimpleNamespace(
        PROVIDERS={name: _Config(name.title()) for name in names},
        CALLERS={name: caller_for(name) for name in names},
        _model=lambda name: model,
        provider_enabled=lambda name: enabled,
        _api_key=lambda name: "key" if keys else "",
        _clean_text=lambda value, limit=4000: str(value or "")[:limit].strip(),
        _safe_error=lambda exc: type(exc).__name__,
        _normalise_usage=lambda provider, model_, raw: {
            "provider": provider, "model": model_, "input_tokens": 0,
            "output_tokens": 0, "cost_usd": None},
    )


def _run(router, **kwargs):
    """Run without touching the ledger, the breaker or the real budget."""
    with mock.patch.object(bench, "_router", return_value=router), \
            mock.patch.object(bench.undx_cost, "month_snapshot", return_value={}), \
            mock.patch.object(bench.undx_cost, "refusal", return_value=""), \
            mock.patch.object(bench.undx_privacy, "provider_accepts", return_value=True):
        return bench.run(record=False, **kwargs)


class ScoringTest(unittest.TestCase):

    def test_a_perfect_provider_scores_one(self):
        router = _fake_router({"alpha": {}})
        result = _run(router)
        row = result["providers"]["alpha"]
        self.assertEqual(row["score"], 1.0)
        self.assertEqual(row["answered"], len(corpus.CASES))
        self.assertEqual(row["unanswered"], 0)
        self.assertEqual(row["failed_cases"], [])

    def test_a_wrong_answer_is_scored_and_named(self):
        router = _fake_router({"alpha": {"fast.exact_token": "Sure, acknowledged."}})
        row = _run(router)["providers"]["alpha"]
        self.assertEqual(row["passed"], len(corpus.CASES) - 1)
        self.assertEqual(row["answered"], len(corpus.CASES))
        self.assertEqual(row["failed_cases"], ["fast.exact_token"])
        self.assertEqual(row["outcomes"]["fast.exact_token"]["failed"], ["exact"])


class UnansweredIsNotWrongTest(unittest.TestCase):
    """The conflation this module exists to refuse.

    Folding a transport failure into the quality score measures uptime and
    prints it as intelligence — the same defect §19 removed from the health
    states, arriving here wearing a percentage sign.
    """

    def test_a_transport_failure_leaves_the_denominator(self):
        failures = {case.id: requests.Timeout("slow")
                    for case in corpus.CASES[:5]}
        router = _fake_router({"alpha": failures})
        row = _run(router)["providers"]["alpha"]
        self.assertEqual(row["answered"], len(corpus.CASES) - 5)
        self.assertEqual(row["unanswered"], 5)
        self.assertEqual(row["unanswered_reasons"], {"transport": 5})
        # Every case it *did* answer, it answered correctly. A benchmark that
        # divided by 21 here would report 0.76 and call it quality.
        self.assertEqual(row["score"], 1.0)

    def test_an_empty_completion_is_unanswered_not_failed(self):
        """`undx_model_audit` already treats an empty completion as a fault
        rather than a response; the two surfaces must not disagree."""
        router = _fake_router({"alpha": {"fast.exact_token": ""}})
        row = _run(router)["providers"]["alpha"]
        self.assertEqual(row["unanswered_reasons"], {"empty": 1})
        self.assertNotIn("fast.exact_token", row["failed_cases"])
        self.assertFalse(row["outcomes"]["fast.exact_token"]["answered"])

    def test_a_provider_that_answered_nothing_scores_none_not_zero(self):
        failures = {case.id: requests.Timeout("slow") for case in corpus.CASES}
        router = _fake_router({"alpha": failures})
        row = _run(router)["providers"]["alpha"]
        self.assertIsNone(row["score"])
        self.assertEqual(row["answered"], 0)

    def test_the_run_is_not_ok_when_nobody_answered(self):
        failures = {case.id: requests.Timeout("slow") for case in corpus.CASES}
        self.assertFalse(_run(_fake_router({"alpha": failures}))["ok"])


class SkipTest(unittest.TestCase):
    """A provider that was never called must not appear as a poor performer."""

    def _skipped(self, **patches):
        router = _fake_router({"alpha": {}})
        defaults = {"month_snapshot": {}, "refusal": "", "accepts": True}
        defaults.update(patches)
        with mock.patch.object(bench, "_router", return_value=router), \
                mock.patch.object(bench.undx_cost, "month_snapshot",
                                  return_value=defaults["month_snapshot"]), \
                mock.patch.object(bench.undx_cost, "refusal",
                                  return_value=defaults["refusal"]), \
                mock.patch.object(bench.undx_privacy, "provider_accepts",
                                  return_value=defaults["accepts"]):
            return bench.run(record=False)["providers"]["alpha"]

    def test_a_budget_refusal_skips_rather_than_scores(self):
        row = self._skipped(refusal="cost_budget: $50.00 of $50.00 spent")
        self.assertEqual(row["skipped"], "budget")
        self.assertIsNone(row["score"])
        self.assertEqual(row["answered"], 0)
        self.assertEqual(row["unanswered"], len(corpus.CASES))

    def test_a_privacy_ceiling_refusal_fails_closed(self):
        """SYNTHETIC is the bottom rung, so this should never fire in practice.

        It is checked anyway: a ceiling misconfigured to refuse everything has
        to stop the benchmark too, not be waved through because the content is
        "only" fixtures.
        """
        row = self._skipped(accepts=False)
        self.assertEqual(row["skipped"], "privacy")
        self.assertIsNone(row["score"])

    def test_a_disabled_provider_is_skipped_not_failed(self):
        router = _fake_router({"alpha": {}}, enabled=False)
        row = _run(router)["providers"]["alpha"]
        self.assertEqual(row["skipped"], "disabled")
        self.assertIsNone(row["score"])

    def test_a_keyless_provider_is_skipped_not_failed(self):
        router = _fake_router({"alpha": {}}, keys=False)
        row = _run(router)["providers"]["alpha"]
        self.assertEqual(row["skipped"], "no_key")
        self.assertIsNone(row["score"])

    def test_a_skipped_provider_spends_nothing(self):
        row = self._skipped(refusal="cost_budget: over")
        self.assertEqual(row["cost_usd"], 0.0)

    def test_every_skip_reason_is_in_the_published_set(self):
        for reason in ("budget", "privacy", "no_key", "disabled"):
            with self.subTest(reason=reason):
                self.assertIn(reason, bench.UNANSWERED_REASONS)


class SignTestTest(unittest.TestCase):
    """The arithmetic that sets the sample floor without anyone naming one."""

    def test_five_unanimous_wins_are_not_significant(self):
        self.assertGreater(bench.sign_test(5, 0), bench.ALPHA)

    def test_six_unanimous_wins_are(self):
        self.assertLess(bench.sign_test(6, 0), bench.ALPHA)

    def test_a_tie_is_never_significant(self):
        for pair in ((0, 0), (3, 3), (10, 10)):
            with self.subTest(pair=pair):
                self.assertEqual(bench.sign_test(*pair), 1.0)

    def test_it_is_symmetric(self):
        for a, b in ((6, 0), (8, 1), (12, 4)):
            with self.subTest(pair=(a, b)):
                self.assertEqual(bench.sign_test(a, b), bench.sign_test(b, a))

    def test_it_never_exceeds_one(self):
        for a in range(6):
            for b in range(6):
                with self.subTest(pair=(a, b)):
                    self.assertLessEqual(bench.sign_test(a, b), 1.0)


class CompareTest(unittest.TestCase):

    def _result(self, alpha_fails, beta_fails):
        """Two providers, each failing the named cases with a wrong answer."""
        router = _fake_router({
            "alpha": {cid: corpus.CASES_BY_ID[cid].wrong for cid in alpha_fails},
            "beta": {cid: corpus.CASES_BY_ID[cid].wrong for cid in beta_fails},
        })
        return _run(router)

    def test_a_small_difference_is_not_evidence(self):
        """Three losses against none is the shape of a plausible-looking
        ranking that a routing change must not be built on."""
        ids = [case.id for case in corpus.CASES[:3]]
        verdict = bench.compare(self._result(ids, []), "alpha", "beta")
        self.assertEqual(verdict["better"], "")
        self.assertEqual(verdict["b_only_wins"], sorted(ids))
        self.assertIn("not evidence", verdict["reason"])

    def test_a_large_consistent_difference_is(self):
        ids = [case.id for case in corpus.CASES[:8]]
        verdict = bench.compare(self._result(ids, []), "alpha", "beta")
        self.assertEqual(verdict["better"], "beta")
        self.assertLess(verdict["p_value"], bench.ALPHA)
        self.assertEqual(verdict["reason"], "")

    def test_equal_failures_on_different_cases_are_not_a_winner(self):
        first = [case.id for case in corpus.CASES[:6]]
        second = [case.id for case in corpus.CASES[6:12]]
        verdict = bench.compare(self._result(first, second), "alpha", "beta")
        self.assertEqual(verdict["better"], "")
        self.assertEqual(len(verdict["a_only_wins"]), 6)
        self.assertEqual(len(verdict["b_only_wins"]), 6)

    def test_cases_both_passed_carry_no_information(self):
        result = self._result([], [])
        verdict = bench.compare(result, "alpha", "beta")
        self.assertEqual(verdict["better"], "")
        self.assertEqual(verdict["agreed"], len(corpus.CASES))
        self.assertEqual(verdict["a_only_wins"], [])

    def test_only_cases_both_answered_are_compared(self):
        """A case one provider never received says nothing about which is better.

        Alpha times out on six cases that beta answers wrongly. Counting those
        as beta losses would manufacture exactly the six discordant pairs the
        sign test needs, out of an outage.
        """
        blind = [case.id for case in corpus.CASES[:6]]
        router = _fake_router({
            "alpha": {cid: requests.Timeout("slow") for cid in blind},
            "beta": {cid: corpus.CASES_BY_ID[cid].wrong for cid in blind},
        })
        result = _run(router)
        verdict = bench.compare(result, "alpha", "beta")
        self.assertEqual(verdict["shared_cases"], len(corpus.CASES) - 6)
        self.assertEqual(verdict["dropped_cases"], 6)
        self.assertEqual(verdict["better"], "")

    def test_comparing_an_absent_provider_says_so(self):
        verdict = bench.compare(self._result([], []), "alpha", "nobody")
        self.assertEqual(verdict["better"], "")
        self.assertIn("not in this run", verdict["reason"])

    def test_two_skipped_providers_produce_no_verdict(self):
        router = _fake_router({"alpha": {}, "beta": {}}, keys=False)
        verdict = bench.compare(_run(router), "alpha", "beta")
        self.assertEqual(verdict["better"], "")
        self.assertIn("no case was answered by both", verdict["reason"])


class RankingTest(unittest.TestCase):

    def test_an_ordering_without_a_separated_pair_is_not_evidence(self):
        """The headline claim of this module.

        Seven providers in a printed order look like a result. `evidence` is
        the field that says the order is noise, and §1 reads that field.
        """
        router = _fake_router({
            "alpha": {corpus.CASES[0].id: corpus.CASES[0].wrong},
            "beta": {},
        })
        report = bench.ranking(_run(router))
        self.assertEqual([row["provider"] for row in report["order"]],
                         ["beta", "alpha"])
        self.assertEqual(report["separated"], [])
        self.assertFalse(report["evidence"])

    def test_a_separated_pair_is_reported_with_its_test(self):
        losses = [case.id for case in corpus.CASES[:9]]
        router = _fake_router({
            "alpha": {cid: corpus.CASES_BY_ID[cid].wrong for cid in losses},
            "beta": {},
        })
        report = bench.ranking(_run(router))
        self.assertTrue(report["evidence"])
        self.assertEqual(len(report["separated"]), 1)
        self.assertEqual(report["separated"][0]["better"], "beta")

    def test_a_skipped_provider_is_left_out_of_the_order(self):
        """Not ranked last. It has no score, and last is a score."""
        router = _fake_router({"alpha": {}, "beta": {}})
        with mock.patch.object(bench, "_router", return_value=router), \
                mock.patch.object(bench.undx_cost, "month_snapshot", return_value={}), \
                mock.patch.object(bench.undx_cost, "refusal",
                                  side_effect=lambda snap, name, model=None:
                                  "cost_budget: over" if name == "beta" else ""), \
                mock.patch.object(bench.undx_privacy, "provider_accepts",
                                  return_value=True):
            result = bench.run(record=False)
        report = bench.ranking(result)
        self.assertEqual([row["provider"] for row in report["order"]], ["alpha"])


class ComparabilityTest(unittest.TestCase):

    def test_runs_of_different_corpus_versions_are_refused(self):
        first = {"corpus_version": "1", "case_ids": ["a"]}
        second = {"corpus_version": "2", "case_ids": ["a"]}
        self.assertIn("different questions", bench.comparable(first, second))

    def test_runs_over_different_case_sets_are_refused(self):
        first = {"corpus_version": "1", "case_ids": ["a", "b"]}
        second = {"corpus_version": "1", "case_ids": ["a"]}
        self.assertIn("different case sets", bench.comparable(first, second))

    def test_identical_runs_are_comparable(self):
        run = {"corpus_version": "1", "case_ids": ["a", "b"]}
        self.assertEqual(bench.comparable(run, dict(run)), "")

    def test_a_result_records_the_version_it_graded(self):
        result = _run(_fake_router({"alpha": {}}))
        self.assertEqual(result["corpus_version"], corpus.CORPUS_VERSION)
        self.assertEqual(result["case_ids"], [c.id for c in corpus.CASES])


class CostTest(unittest.TestCase):

    def test_estimate_calls_no_provider(self):
        router = _fake_router({"alpha": {}})
        called = []
        router.CALLERS["alpha"] = lambda *a, **k: called.append(1)
        with mock.patch.object(bench, "_router", return_value=router):
            report = bench.estimate()
        self.assertEqual(called, [])
        self.assertEqual(report["calls"], len(corpus.CASES))

    def test_an_unpriced_provider_estimates_to_none_not_zero(self):
        """0.0 reads as free. Only two of seven models here have a price."""
        router = _fake_router({"alpha": {}})
        with mock.patch.object(bench, "_router", return_value=router), \
                mock.patch.object(bench.undx_cost, "is_priced", return_value=False), \
                mock.patch.object(bench.undx_cost, "estimate_cost_usd",
                                  return_value=None):
            report = bench.estimate()
        self.assertIsNone(report["providers"]["alpha"]["cost_usd"])
        self.assertFalse(report["cost_complete"])
        self.assertEqual(report["unpriced_providers"], ["alpha"])

    def test_a_run_meters_every_answered_call(self):
        router = _fake_router({"alpha": {}})
        with mock.patch.object(bench, "_router", return_value=router), \
                mock.patch.object(bench.undx_cost, "month_snapshot", return_value={}), \
                mock.patch.object(bench.undx_cost, "refusal", return_value=""), \
                mock.patch.object(bench.undx_privacy, "provider_accepts",
                                  return_value=True), \
                mock.patch.object(bench.undx_cost, "record") as record, \
                mock.patch.object(bench.undx_health, "record_success"):
            bench.run(record=True)
        self.assertEqual(record.call_count, len(corpus.CASES))

    def test_incomplete_pricing_is_reported_rather_than_summed_away(self):
        router = _fake_router({"alpha": {}})
        for name in router.CALLERS:
            router.CALLERS[name] = lambda *a, **k: {
                "text": "ACKNOWLEDGED", "model": "m",
                "usage": {"provider": "alpha", "model": "m", "input_tokens": 1,
                          "output_tokens": 1, "cost_usd": None}}
        result = _run(router, max_cases=1)
        self.assertFalse(result["cost_complete"])
        self.assertEqual(result["providers"]["alpha"]["cost_usd"], 0.0)


class NoUserContentTest(unittest.TestCase):

    def test_the_prompts_sent_are_exactly_the_corpus_prompts(self):
        """A benchmark that accepted caller text would broadcast it to every
        provider at once, from a path nobody reads as a data path."""
        sent = []
        router = _fake_router({"alpha": {}})
        original = router.CALLERS["alpha"]

        def spy(system, message, history, timeout, **kwargs):
            sent.append((system, kwargs.get("user_content")))
            return original(system, message, history, timeout, **kwargs)

        router.CALLERS["alpha"] = spy
        _run(router)
        prompts = {prompt for _, prompt in sent}
        self.assertEqual(prompts, {case.prompt for case in corpus.CASES})
        self.assertEqual({system for system, _ in sent},
                         {case.system for case in corpus.CASES})

    def test_run_takes_no_prompt_argument(self):
        import inspect
        names = set(inspect.signature(bench.run).parameters)
        for forbidden in ("prompt", "message", "user_content", "text", "content"):
            with self.subTest(parameter=forbidden):
                self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
