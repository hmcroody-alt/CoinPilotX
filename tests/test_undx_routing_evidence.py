"""An explanation that cannot be wrong is narration, not evidence.

The load-bearing test here is `RouterDriftTest`: it fails when the explainer's
reconstruction stops matching what `undx_router.provider_priority` actually
returns. Without it this module would keep producing fluent, confident reasons
for decisions the router never took.
"""

from __future__ import annotations

import unittest
from unittest import mock

import undx_router
from services import undx_call_domain
from services import undx_eval_corpus as corpus
from services import undx_routing_evidence as evidence


def _all_on(**overrides):
    """Router patched so a plan survives to the gates: multi-model on, keys set."""
    defaults = {
        "multi_model_mode": True,
        "router_enabled": True,
        "default_provider": "openai",
        "provider_enabled": True,
        "_api_key": "key",
        "_breaker_should_skip": False,
        "_budget_refusal": "",
        "_budget_snapshot": {},
    }
    defaults.update(overrides)
    patches = []
    for name, value in defaults.items():
        target = getattr(undx_router, name)
        if callable(value) or isinstance(value, mock.Mock):
            patches.append(mock.patch.object(undx_router, name, value))
        else:
            patches.append(mock.patch.object(undx_router, name,
                                             return_value=value)
                           if callable(target)
                           else mock.patch.object(undx_router, name, value))
    return patches


class _Patched:
    def __init__(self, **overrides):
        self.patches = _all_on(**overrides)

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self.patches):
            patch.stop()
        return False


class RouterDriftTest(unittest.TestCase):
    """The explainer must agree with the router it explains."""

    def test_the_reconstruction_matches_the_real_plan_on_every_lane(self):
        with _Patched():
            for lane in undx_router.LANE_PRIORITIES:
                with self.subTest(lane=lane):
                    record = evidence.explain(f"__lane__{lane}")
                    # classify_request will not necessarily land on `lane`, so
                    # drive the comparison through the reconstruction directly.
                    rebuilt, _ = evidence._reconstruct(undx_router, lane)
                    actual = undx_router.provider_priority({"category": lane})
                    self.assertEqual(rebuilt, actual)
                    self.assertTrue(record["consistent"])

    def test_an_unknown_category_falls_back_the_same_way_in_both(self):
        with _Patched():
            rebuilt, _ = evidence._reconstruct(undx_router, "not_a_lane")
            self.assertEqual(rebuilt,
                             undx_router.provider_priority({"category": "not_a_lane"}))

    def test_drift_is_reported_rather_than_narrated(self):
        """If the router's order changes and this module's replay does not, the
        record must say its reasons are unusable — not attach them to the new
        plan and read as an explanation of it."""
        with _Patched():
            with mock.patch.object(undx_router, "provider_priority",
                                   return_value=["groq", "claude"]):
                record = evidence.explain("hello")
        self.assertFalse(record["consistent"])
        self.assertEqual(record["plan"], ["groq", "claude"])
        self.assertTrue(record["reconstruction"])
        self.assertNotEqual(record["reconstruction"], record["plan"])

    def test_every_planned_provider_gets_a_reason(self):
        with _Patched():
            record = evidence.explain("compare these two market reports")
        self.assertTrue(record["steps"])
        for step in record["steps"]:
            with self.subTest(provider=step["provider"]):
                self.assertNotEqual(step["reason"], "unknown")

    def test_the_lane_table_comes_from_the_router(self):
        """One authority. A second copy would explain a table nobody routes on."""
        with _Patched():
            record = evidence.explain("what is the price of bitcoin today")
        self.assertEqual(record["lane_table"],
                         undx_router.LANE_PRIORITIES[record["lane"]])


class PlanRulesTest(unittest.TestCase):
    """The four invisible transformations, each made visible."""

    def test_single_provider_mode_is_named_as_the_reason(self):
        with _Patched(multi_model_mode=False, default_provider="groq"):
            record = evidence.explain("anything")
        self.assertEqual(record["plan"][0], "groq")
        self.assertEqual(record["steps"][0]["reason"], "single_provider_mode")
        self.assertFalse(record["multi_model"])

    def test_the_configured_default_is_named_when_it_is_prepended(self):
        with _Patched(default_provider="meta"):
            record = evidence.explain("summarise this product page design")
        self.assertEqual(record["plan"][0], "meta")
        self.assertEqual(record["steps"][0]["reason"], "configured_default")

    def test_openai_is_named_as_the_universal_fallback(self):
        """Every lane is emptied of openai, not just the one guessed at.

        The first draft patched `general_builder` and asserted against a "hi"
        that classifies as `fast_directive` — so the assertion read a lane the
        test had not touched. Patching them all removes the dependence on what
        `classify_request` does with the probe string.
        """
        table = {lane: ["groq", "claude"] for lane in undx_router.LANE_PRIORITIES}
        with _Patched(default_provider="groq"), \
                mock.patch.object(undx_router, "LANE_PRIORITIES", table):
            record = evidence.explain("hi")
        reasons = {step["provider"]: step["reason"] for step in record["steps"]}
        self.assertEqual(reasons.get("openai"), "universal_fallback")
        self.assertEqual(record["plan"][-1], "openai")

    def test_a_disabled_provider_is_excluded_and_says_why(self):
        with _Patched(provider_enabled=lambda name: name != "groq"):
            record = evidence.explain("reply fast")
        self.assertNotIn("groq", record["plan"])
        self.assertIn({"provider": "groq", "reason": "disabled"},
                      record["excluded"])

    def test_the_lane_leader_is_position_one(self):
        with _Patched():
            record = evidence.explain("debug this python file")
        self.assertEqual(record["steps"][0]["position"], 1)
        self.assertEqual(record["steps"][0]["reason"], "lane_leader")


class GateTest(unittest.TestCase):
    """The dry run must apply the same gates, in the routing loop's order."""

    def test_a_privacy_refusal_gates_the_provider(self):
        with _Patched(), mock.patch.object(
                undx_router, "_privacy_refusal",
                side_effect=lambda name, klass: "ceiling" if name == "openai" else ""):
            record = evidence.explain("hello", privacy_class="RESTRICTED")
        gates = {step["provider"]: step["gate"] for step in record["steps"]}
        self.assertEqual(gates["openai"], "privacy_refused")
        self.assertNotIn("openai", record["would_try"])

    def test_privacy_is_checked_before_the_credential(self):
        """The routing loop checks the ceiling first so that a provider which
        must not see the content is not consulted about whether it could have.
        The explanation has to report the same reason the loop would act on."""
        with _Patched(_api_key=""), mock.patch.object(
                undx_router, "_privacy_refusal", return_value="ceiling"):
            record = evidence.explain("hello", privacy_class="RESTRICTED")
        for step in record["steps"]:
            with self.subTest(provider=step["provider"]):
                self.assertEqual(step["gate"], "privacy_refused")

    def test_budget_is_checked_before_the_credential(self):
        """`PUBLIC` is declared so that the *budget* is what this isolates.

        This test used to pass no class at all and assert that every step read
        `budget_exceeded`. It passed for the wrong reason: `explain` skipped the
        privacy gate whenever the caller declared nothing, so budget was the first
        gate that could fire. With the ceiling now applied to an omitted class —
        CONFIDENTIAL, which four providers cannot receive — the same call reports
        `privacy_refused` for those four, and correctly. A test about budgets that
        depends on the privacy gate being absent is not a test about budgets.
        """
        with _Patched(_api_key="", _budget_refusal="cost_budget: over"):
            record = evidence.explain("hello", privacy_class="PUBLIC")
        for step in record["steps"]:
            with self.subTest(provider=step["provider"]):
                self.assertEqual(step["gate"], "budget_exceeded")

    def test_an_open_breaker_gates_the_provider(self):
        with _Patched(_breaker_should_skip=lambda name: name == "openai"):
            record = evidence.explain("hello")
        gates = {step["provider"]: step["gate"] for step in record["steps"]}
        self.assertEqual(gates["openai"], "circuit_open")

    def test_a_fully_gated_plan_is_reported_as_a_failure(self):
        with _Patched(_api_key=""):
            record = evidence.explain("hello")
        self.assertTrue(record["would_fail"])
        self.assertEqual(record["would_try"], [])
        self.assertEqual(record["first_choice"], "")

    def test_an_omitted_privacy_class_still_applies_the_default_ceiling(self):
        """The divergence this class existed to prevent, and did not.

        `explain` guarded its privacy check with `if privacy_class`, which reads as
        tolerating a missing value and is the opposite: `normalise(None)` is
        CONFIDENTIAL, so the guard discarded the default ceiling. The result was a
        surface reporting Perplexity as first choice for a `current_web` request
        that the routing loop refuses there — naming a provider the request cannot
        reach.

        The two privacy tests above could not catch it: both pass `"RESTRICTED"`, a
        truthy value, so neither enters the branch. The gates were pinned; the
        gate's *input* was not.

        `_api_key` and `_breaker_should_skip` must be healthy for this to be
        observable at all. With no key set every provider lands on
        `not_configured`, `would_try` is empty either way, and the assertion passes
        in the broken tree — the same unearned zero as asserting a conjunction is
        false when one term was already false for an unrelated reason.
        """
        with _Patched():
            record = evidence.explain("what is the bitcoin price right now")

        self.assertEqual(record["category"], "current_web")
        self.assertEqual(record["plan"][0], "perplexity",
                         "the lane must still lead with Perplexity, or this test "
                         "is no longer measuring what it claims")

        gates = {step["provider"]: step["gate"] for step in record["steps"]}
        self.assertEqual(gates["perplexity"], "privacy_refused")
        self.assertEqual(record["first_choice"], "openai")
        self.assertEqual(record["would_try"], ["openai", "claude", "meta"])

    def test_a_json_requirement_is_reported_as_the_loop_would_apply_it(self):
        """`require_json` was unrepresentable here, so `capability_unmet` refusals
        were invisible on the surface that explains routing.

        The loop declines a provider with no JSON dialect *before reading its
        credential*. `scam_shield` and `undx_capability_planner` both route this
        way, so a chain explained without the flag omits refusals that will happen.
        """
        with _Patched():
            plain = evidence.explain("hello", privacy_class="PUBLIC")
            strict = evidence.explain("hello", privacy_class="PUBLIC",
                                      require_json=True)

        unmet = [step["provider"] for step in strict["steps"]
                 if step["gate"] == "capability_unmet"]
        self.assertTrue(unmet, "no provider was declined for the JSON requirement")
        for name in unmet:
            with self.subTest(provider=name):
                self.assertFalse(undx_router.PROVIDERS[name].structured_output)
                self.assertIn(name, plain["would_try"],
                              "this provider must be reachable without the "
                              "requirement, or the flag is not what excluded it")
                self.assertNotIn(name, strict["would_try"])

    def test_the_declared_domain_reorders_the_explained_plan(self):
        """`_domain_ordered` must be applied here because both routing loops apply
        it, and a surface that skips it explains a differently ordered request.

        Latent rather than live at the time of writing: `undx_call_domain._PREFERENCE`
        is empty, so `routing_preference` returns `()` for every domain and
        `_domain_ordered` is a no-op — the omission produced no wrong output. That is
        precisely why it needs a test with a preference *supplied*. Asserting on the
        real table would pass with the call absent, which is the vacuous gate this
        mission keeps finding.
        """
        with _Patched(), mock.patch.object(
                undx_call_domain, "routing_preference",
                lambda domain: ("deepseek",) if domain == "SECURITY" else ()):
            plain = evidence.explain("hello", privacy_class="PUBLIC")
            scoped = evidence.explain("hello", privacy_class="PUBLIC",
                                      call_domain="SECURITY")

        # The preferred provider has to already be in this lane. `_domain_ordered`
        # partitions its input, so a name that is not there finds nothing to move
        # and the fixture would prove nothing.
        self.assertIn("deepseek", plain["plan"])
        self.assertNotEqual(plain["plan"][0], "deepseek",
                            "this fixture only means something if the domain has "
                            "something to move")
        self.assertEqual(scoped["plan"][0], "deepseek")
        self.assertEqual(scoped["first_choice"], "deepseek")
        self.assertEqual(sorted(scoped["plan"]), sorted(plain["plan"]),
                         "a domain may reorder a settled plan and may never widen "
                         "it: the result must be a permutation of its input")

    def test_every_gate_used_is_in_the_published_set(self):
        for reason in ("privacy_refused", "budget_exceeded", "not_configured",
                       "circuit_open", "disabled", "single_provider_mode"):
            with self.subTest(reason=reason):
                self.assertIn(reason, evidence.EXCLUSION_REASONS)


class DisclosureTest(unittest.TestCase):

    def test_refusal_sentences_are_off_by_default(self):
        """Budget and privacy refusals quote configuration back — a dollar
        figure, a month-to-date total, a ceiling name."""
        with _Patched(_budget_refusal="cost_budget: $50.00 of $50.00 spent"):
            record = evidence.explain("hello")
        for step in record["steps"]:
            self.assertNotIn("detail", step)

    def test_detail_is_available_when_asked_for(self):
        """`PUBLIC` declared for the same reason as the budget test above: without
        it the first step's detail is a privacy ceiling sentence, not a dollar
        figure, and this asserts on the budget gate's wording."""
        with _Patched(_budget_refusal="cost_budget: $50.00 of $50.00 spent"):
            record = evidence.explain("hello", privacy_class="PUBLIC", detail=True)
        self.assertIn("$50.00", record["steps"][0]["detail"])

    def test_explaining_contacts_no_provider(self):
        """This is what makes it safe to run from an admin screen."""
        with _Patched():
            with mock.patch.dict(undx_router.CALLERS, {
                    name: mock.Mock(side_effect=AssertionError("called a provider"))
                    for name in undx_router.CALLERS}):
                record = evidence.explain("what happened in the news today")
        self.assertTrue(record["plan"])


class CoverageTest(unittest.TestCase):

    def test_every_routing_lane_is_reported(self):
        lanes = evidence.coverage()["lanes"]
        self.assertEqual(set(lanes), set(undx_router.LANE_PRIORITIES))

    def test_current_web_is_reported_as_uncovered(self):
        """The one lane no benchmark can ever speak to. A coverage report that
        omitted it would leave its ordering looking unexamined rather than
        deliberately unexaminable."""
        self.assertIn("current_web", evidence.coverage()["uncovered"])

    def test_thin_lanes_are_named_rather_than_rounded_up(self):
        report = evidence.coverage()
        self.assertTrue(report["thin"])
        for lane in report["thin"]:
            with self.subTest(lane=lane):
                self.assertLess(report["lanes"][lane]["cases"],
                                evidence.MIN_LANE_CASES)
                self.assertFalse(report["lanes"][lane]["sufficient"])


class ContradictionTest(unittest.TestCase):

    def _result(self, separated_pairs):
        """A benchmark result where each (winner, loser) pair is separated."""
        providers = {}
        case_ids = [case.id for case in corpus.CASES]
        winners = {w for w, _ in separated_pairs}
        losers = {l for _, l in separated_pairs}
        for name in winners | losers:
            outcomes = {}
            for index, case_id in enumerate(case_ids):
                # Losers fail the first nine cases; winners pass everything.
                passed = name in winners or index >= 9
                outcomes[case_id] = {"answered": True, "passed": passed,
                                     "failed": [] if passed else ["exact"]}
            providers[name] = {"score": 1.0 if name in winners else 0.5,
                               "outcomes": outcomes, "answered": len(case_ids),
                               "attempted": len(case_ids), "cost_usd": 0.0,
                               "median_latency_ms": 10}
        return {"corpus_version": corpus.CORPUS_VERSION, "case_ids": case_ids,
                "providers": providers}

    def test_a_table_order_contradicted_by_evidence_is_reported(self):
        """`security` ranks claude ahead of groq. A run where groq is
        separated above claude has to surface as a contradiction."""
        report = evidence.contradictions(self._result([("groq", "claude")]))
        self.assertFalse(report["ok"])
        lanes = {row["lane"] for row in report["findings"]}
        self.assertIn("security", lanes)
        row = next(r for r in report["findings"] if r["lane"] == "security")
        self.assertEqual(row["ranked_ahead"], "claude")
        self.assertEqual(row["benchmark_favours"], "groq")

    def test_one_verdict_both_supports_and_contradicts_a_specialised_table(self):
        """The property that makes `supported` more than decoration.

        Of the 21 provider pairs in `LANE_PRIORITIES`, only eight are ordered
        the same way in every lane they share — the rest deliberately invert,
        because that inversion *is* what lane specialisation means. So a single
        corpus-wide verdict about groq and claude contradicts the five lanes
        ranking claude first and supports the two ranking groq first, at the
        same time, from the same number.

        A reader who sees only `findings` would read that as "the table is
        wrong". It is one measurement, of general instruction adherence,
        arriving at a table that does not claim to be about general
        instruction adherence.
        """
        report = evidence.contradictions(self._result([("groq", "claude")]))
        contradicted = {row["lane"] for row in report["findings"]}
        endorsed = {row["lane"] for row in report["supported"]}
        self.assertTrue(contradicted)
        self.assertTrue(endorsed)
        self.assertIn("security", contradicted)
        self.assertIn("automation", endorsed)
        self.assertFalse(contradicted & endorsed)

    def test_an_order_supported_by_evidence_is_reported_too(self):
        """Otherwise the only thing a benchmark can ever say is 'you are wrong'.

        `claude > meta` is one of the eight pairs the table orders consistently
        — five lanes, no inversions — so a verdict favouring claude endorses
        every lane that ranks them and contradicts none.
        """
        report = evidence.contradictions(self._result([("claude", "meta")]))
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["supported"])
        self.assertTrue(all(row["benchmark_favours"] == "claude"
                            for row in report["supported"]))

    def test_an_unseparated_pair_contributes_nothing(self):
        """The guard that stops noise becoming a routing argument."""
        result = self._result([("groq", "claude")])
        with mock.patch.object(evidence.undx_benchmark, "compare",
                               return_value={"better": "", "p_value": 0.5,
                                             "shared_cases": 21}):
            report = evidence.contradictions(result)
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["supported"], [])
        self.assertEqual(report["separated_pairs"], 0)

    def test_a_run_without_a_corpus_version_is_refused(self):
        report = evidence.contradictions({"providers": {}, "case_ids": []})
        self.assertFalse(report["ok"])
        self.assertIn("corpus version", report["reason"])

    def test_every_finding_carries_its_lane_sample_size(self):
        """The number that stops a corpus-wide result reading as a lane fact."""
        report = evidence.contradictions(self._result([("groq", "claude")]))
        for row in report["findings"]:
            with self.subTest(lane=row["lane"]):
                self.assertIn("lane_cases", row)
                self.assertIn("lane_is_covered", row)
                self.assertFalse(row["lane_is_covered"])

    def test_the_scope_is_labelled_corpus_wide(self):
        report = evidence.contradictions(self._result([("groq", "claude")]))
        self.assertEqual(report["scope"], "corpus-wide")

    def test_a_skipped_provider_is_not_compared(self):
        result = self._result([("groq", "claude")])
        result["providers"]["gemini"] = {"score": None, "outcomes": {},
                                         "answered": 0, "attempted": 21,
                                         "cost_usd": 0.0,
                                         "median_latency_ms": None}
        report = evidence.contradictions(result)
        named = {row["ranked_ahead"] for row in report["findings"]}
        named |= {row["ranked_behind"] for row in report["findings"]}
        self.assertNotIn("gemini", named)

    def test_it_reports_and_does_not_reorder(self):
        """§1 requires a person. A module that rewrote the table from last
        night's benchmark would be promoting a provider on a cron's judgement."""
        before = {lane: list(order)
                  for lane, order in undx_router.LANE_PRIORITIES.items()}
        evidence.contradictions(self._result([("groq", "claude")]))
        self.assertEqual(undx_router.LANE_PRIORITIES, before)


if __name__ == "__main__":
    unittest.main()
