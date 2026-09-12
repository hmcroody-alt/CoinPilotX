"""A shadow answer must not be able to reach anything, and must not claim a winner."""

from __future__ import annotations

import ast
import os
import pathlib
import types
import unittest
from unittest import mock

from services import undx_shadow as shadow

MODULE_PATH = pathlib.Path(shadow.__file__)

#: A string no comparison could invent. If it appears in anything `observe`
#: returns, stores, or reports, a shadow completion escaped the module.
SENTINEL = "zzqx-shadow-leak-sentinel-7714"


_PRICED_USAGE = {"provider": "groq", "cost_usd": 0.001}


def _router(*, text=SENTINEL, raises=None, providers=("groq", "claude"),
            enabled=True, key="k", breaker_open=False, omni=True,
            usage=_PRICED_USAGE):
    """A router stand-in with the surface `undx_shadow` actually touches.

    Deliberately not the real `undx_router`: a test that imported it would be
    one env var away from making a paid call, which is the thing this whole
    mission is about not doing by accident.
    """
    calls: list[str] = []

    def caller(system_prompt, message, history, timeout, **kwargs):
        calls.append(message)
        if raises is not None:
            raise raises
        return {"text": text, "model": "m", "source": "S", "usage": dict(usage)}

    stub = types.SimpleNamespace(
        PROVIDERS={name: object() for name in providers},
        CALLERS={name: caller for name in providers},
        DEFAULT_UNDX_SYSTEM_PROMPT="sys",
        omni_router_enabled=lambda: omni,
        provider_enabled=lambda name: enabled,
        _model=lambda name: "m",
        _api_key=lambda name: key,
        _breaker_should_skip=lambda name: breaker_open,
        _record_provider_success=lambda name: None,
        _record_provider_failure=lambda name, status, error="": None,
        _record_usage=lambda usage: None,
        _safe_error=lambda exc: "redacted",
        calls=calls,
    )
    return stub


def _env(**overrides):
    """Env for a shadow that is switched on and always sampled."""
    base = {"UNDX_SHADOW_ENABLED": "true", "UNDX_SHADOW_PROVIDER": "groq",
            "UNDX_SHADOW_SAMPLE_RATE": "1.0",
            "UNDX_SHADOW_MAX_PRIVACY_CLASS": "PUBLIC",
            "UNDX_SHADOW_LEDGER_ENABLED": "false"}
    base.update(overrides)
    return mock.patch.dict(os.environ, base, clear=False)


def _observe(router, **kwargs):
    # PUBLIC is declared explicitly, because an unclassified request normalises
    # to CONFIDENTIAL and is refused by the default shadow ceiling — see
    # `DefaultConfigurationTest`. A test that left it unset would be exercising
    # the privacy gate while believing it was exercising something else.
    args = {"request_id": "r1", "lane": "research", "primary_provider": "claude",
            "primary_text": "The answer is 42.", "primary_latency_ms": 100,
            "message": "what is it?", "privacy_class": "PUBLIC"}
    args.update(kwargs)
    return shadow.observe(router, **args)


class SuppressionTest(unittest.TestCase):
    """§35, enforced structurally rather than by a flag somebody can delete."""

    def setUp(self):
        shadow.reset_for_tests()

    def test_the_shadow_answer_does_not_appear_in_the_observation(self):
        """The whole module, in one assertion.

        The candidate returns a string nothing else could produce. If that
        string survives into the value `observe` hands back, then a shadow
        completion has reached a caller — and a caller that has it will
        eventually render it, store it, or act on it.
        """
        with _env():
            observation = _observe(_router(text=SENTINEL))
        self.assertIsNotNone(observation)
        self.assertNotIn(SENTINEL, repr(observation))
        for name, value in observation.as_row().items():
            with self.subTest(field=name):
                self.assertNotIn(SENTINEL, str(value))

    def test_the_shadow_answer_does_not_appear_in_a_report(self):
        with _env():
            _observe(_router(text=f"{SENTINEL} and the answer is 42."))
            built = shadow.report("groq")
        self.assertNotIn(SENTINEL, repr(built))

    def test_the_observation_carries_no_field_that_could_hold_text(self):
        """A field list is a security boundary, so it is asserted, not described.

        `text`, `response`, `answer`, `excerpt`, `sample`, `completion` — the
        plausible names for the field somebody adds when they want to "just
        eyeball a few". Adding one is allowed; adding one silently is not.
        """
        forbidden = ("text", "response", "answer", "excerpt", "sample",
                     "completion", "content", "body", "output", "message")
        for name in shadow.OBSERVATION_FIELDS:
            with self.subTest(field=name):
                self.assertNotIn(name, forbidden)

    def test_the_declared_field_tuple_matches_the_dataclass(self):
        """Otherwise the test above asserts against a stale list."""
        self.assertEqual(
            shadow.OBSERVATION_FIELDS,
            tuple(shadow.ShadowObservation.__dataclass_fields__.keys()))

    def test_the_module_imports_nothing_that_can_write_or_confirm(self):
        """No writes, no confirmations, no external actions — by import graph.

        A shadow that never imports the gateway cannot execute a capability, a
        shadow that never imports the agent runtime cannot mint a confirmation,
        and a shadow that never imports the memory services cannot promote
        anything into them. Those are the four §35 prohibitions, and this is
        the cheapest place to make all four unreachable rather than merely
        unreached.
        """
        forbidden = {
            "services.undx_tool_gateway", "services.undx_agent_runtime",
            "services.undx_agent_tools", "services.undx_capability_registry",
            "services.undx_verification", "services.undx_agent_runs",
            "services.undx_semantic_retrieval", "services.undx_knowledge_map",
            "services.undx_personal_intelligence_service",
        }
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        leaked = imported & forbidden
        self.assertEqual(leaked, set(), f"shadow reaches a write path: {leaked}")

    def test_the_comparison_function_returns_no_text(self):
        """`_compare` is the only place both completions are in scope at once."""
        result = shadow._compare(f"{SENTINEL} 42", f"{SENTINEL} 42")
        self.assertNotIn(SENTINEL, repr(result))
        self.assertEqual(set(result), {"length_ratio", "token_overlap",
                                       "numbers_agree"})


class PlanTest(unittest.TestCase):
    """Nothing is shadowed unless every gate says yes, in the router's order."""

    def test_off_by_default(self):
        """The expensive default has to be the one nobody can arrive at by
        forgetting to configure something."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(shadow.enabled())
            self.assertEqual(shadow.sample_rate(), 0.0)
            self.assertEqual(shadow.plan(_router(), "claude").reason, "disabled")

    def test_every_skip_reason_is_declared(self):
        """A reason outside the vocabulary raises rather than being aggregated."""
        with self.assertRaises(ValueError):
            shadow.ShadowPlan(provider="groq", run=False, reason="because")

    def test_a_provider_is_not_shadowed_against_itself(self):
        with _env():
            self.assertEqual(shadow.plan(_router(), "groq", "PUBLIC").reason,
                             "same_as_primary")

    def test_an_unknown_or_disabled_candidate_does_not_run(self):
        with _env(UNDX_SHADOW_PROVIDER="nosuch"):
            self.assertEqual(shadow.plan(_router(), "claude", "PUBLIC").reason,
                             "unknown_provider")
        with _env():
            self.assertEqual(shadow.plan(_router(enabled=False), "claude", "PUBLIC").reason,
                             "provider_disabled")

    def test_the_credential_and_breaker_gates_apply(self):
        with _env():
            self.assertEqual(shadow.plan(_router(key=""), "claude", "PUBLIC").reason,
                             "not_configured")
            self.assertEqual(
                shadow.plan(_router(breaker_open=True), "claude", "PUBLIC").reason,
                "circuit_open")

    def test_sampling_is_a_reason_of_its_own(self):
        """"We did not look" must never be aggregated as "nothing was wrong"."""
        with _env(UNDX_SHADOW_SAMPLE_RATE="0.1"):
            self.assertEqual(shadow.plan(_router(), "claude", "PUBLIC", roll=0.9).reason,
                             "not_sampled")
            self.assertTrue(shadow.plan(_router(), "claude", "PUBLIC", roll=0.05).run)

    def test_a_zero_sample_rate_does_not_shadow_even_when_enabled(self):
        with _env(UNDX_SHADOW_SAMPLE_RATE="0"):
            self.assertFalse(shadow.plan(_router(), "claude", "PUBLIC", roll=0.0).run)

    def test_an_unparseable_sample_rate_reads_as_zero_not_as_one(self):
        """A typo here doubles the bill on every request in production."""
        for raw in ("all", "100%", "", "nan"):
            with self.subTest(raw=raw), _env(UNDX_SHADOW_SAMPLE_RATE=raw):
                self.assertEqual(shadow.sample_rate(), 0.0)

    def test_a_sample_rate_above_one_is_clamped_not_rejected(self):
        with _env(UNDX_SHADOW_SAMPLE_RATE="5"):
            self.assertEqual(shadow.sample_rate(), 1.0)


class PrivacyTest(unittest.TestCase):
    """Copying a request to an unchosen vendor gets its own, lower, ceiling."""

    def test_a_class_above_the_shadow_ceiling_is_refused(self):
        with _env():
            for klass in ("CONFIDENTIAL", "HIGHLY_SENSITIVE", "RESTRICTED",
                          "SECRET"):
                with self.subTest(klass=klass):
                    self.assertEqual(
                        shadow.plan(_router(), "claude", klass).reason,
                        "privacy_class")

    def test_an_unrecognised_class_refuses_rather_than_declassifies(self):
        """Fail closed. A typo must not become a permission."""
        with _env():
            self.assertEqual(
                shadow.plan(_router(), "claude", "PLATFRM_PUBLIC").reason,
                "privacy_class")

    def test_the_default_ceiling_is_public(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(shadow.max_privacy_class(), "PUBLIC")

    def test_an_unset_class_is_not_treated_as_public(self):
        """"The caller said nothing" is not "the caller said this is public".

        `undx_privacy.normalise(None)` decides what silence means — it says
        CONFIDENTIAL — and this pins that shadowing inherits that decision
        rather than quietly substituting the friendlier reading.
        """
        from services import undx_privacy as privacy
        self.assertEqual(privacy.normalise(None), "CONFIDENTIAL")
        with _env():
            self.assertEqual(shadow.plan(_router(), "claude", None).reason,
                             "privacy_class")

    def test_a_misspelt_ceiling_permits_nothing_rather_than_everything(self):
        """The mutation run found this one as a live bug, not a missing test.

        `UNDX_SHADOW_MAX_PRIVACY_CLASS=PUBIC` — one absent letter — used to
        read as rank 6 and clear every class below SECRET, because the shared
        `UNKNOWN_CLASS_RANK` default is the *highest* rank. That default is
        right for a request (unidentified content is maximally sensitive) and
        exactly inverted for a ceiling, where the highest rank means "permit
        all". A typo raised the ceiling instead of lowering it.
        """
        for typo in ("PUBIC", "PUBLICC", "public-ish", "INTERNEL", "none"):
            with self.subTest(typo=typo), _env(UNDX_SHADOW_MAX_PRIVACY_CLASS=typo):
                for klass in ("SYNTHETIC", "PUBLIC", "CONFIDENTIAL", "SECRET"):
                    self.assertEqual(
                        shadow.plan(_router(), "claude", klass).reason,
                        "privacy_class", f"{typo} cleared {klass}")

    def test_a_ceiling_that_names_nothing_is_visible_in_readiness(self):
        """Otherwise a typo and a deliberately strict setting are one state.

        Both produce an empty report, and an operator staring at one has no way
        to tell "nothing qualified" from "the ceiling I set is not a class".
        """
        with _env(UNDX_SHADOW_MAX_PRIVACY_CLASS="PUBIC"):
            self.assertFalse(shadow.readiness(_router())["max_privacy_class_is_known"])
        with _env(UNDX_SHADOW_MAX_PRIVACY_CLASS="PUBLIC"):
            self.assertTrue(shadow.readiness(_router())["max_privacy_class_is_known"])

    def test_the_ceiling_is_case_and_whitespace_insensitive(self):
        """Failing closed must not become failing on ` public`.

        The fix above makes an unreadable ceiling refuse everything, which is
        only correct if the set of readable ceilings is the one an operator
        would expect to be able to type.
        """
        for spelling in ("confidential", "Confidential", " CONFIDENTIAL "):
            with self.subTest(spelling=spelling), \
                    _env(UNDX_SHADOW_MAX_PRIVACY_CLASS=spelling):
                self.assertTrue(shadow.readiness(_router())["max_privacy_class_is_known"])
                self.assertNotEqual(
                    shadow.plan(_router(), "claude", "PUBLIC").reason,
                    "privacy_class")

    def test_a_shadow_ceiling_cannot_be_raised_above_the_provider_ceiling(self):
        """Two independent gates, and the provider's own still applies.

        Setting UNDX_SHADOW_MAX_PRIVACY_CLASS=SECRET does not grant a provider
        permission it does not have — `provider_accepts` is consulted after.
        """
        with _env(UNDX_SHADOW_MAX_PRIVACY_CLASS="SECRET"), \
                mock.patch("services.undx_privacy.provider_accepts",
                           return_value=False):
            self.assertEqual(shadow.plan(_router(), "claude", "SECRET").reason,
                             "privacy_ceiling")

    def test_the_budget_gate_precedes_the_credential_gate(self):
        """Same order as the routing loop, so the two agree about why."""
        with _env(), mock.patch("services.undx_cost.refusal",
                                return_value="over budget"):
            self.assertEqual(shadow.plan(_router(key=""), "claude", "PUBLIC").reason,
                             "budget")


class KillSwitchTest(unittest.TestCase):
    """§39: the omni switch stops shadow traffic without stopping UNDX."""

    def setUp(self):
        shadow.reset_for_tests()

    def test_omni_off_stops_shadowing_even_when_shadow_is_enabled(self):
        """The switch an operator pulls in an incident is the last word.

        A shadow that kept running because its own flag was still set would
        make the documented recovery action ineffective, and the operator
        would find that out while the incident was in progress.
        """
        router = _router(omni=False)
        with _env():
            self.assertEqual(shadow.plan(router, "claude", "PUBLIC").reason,
                             "disabled")
            self.assertIsNone(_observe(router))
        self.assertEqual(router.calls, [])

    def test_the_shadow_flag_alone_also_stops_it(self):
        """Two independent stops, so neither is a single point of failure."""
        router = _router(omni=True)
        with _env(UNDX_SHADOW_ENABLED="false"):
            self.assertEqual(shadow.plan(router, "claude", "PUBLIC").reason,
                             "disabled")

    def test_an_explicit_plan_override_is_the_callers_responsibility(self):
        """`plan_override` bypasses the switches on purpose, and is named so.

        It exists for a caller that already ran `plan` and does not want the
        decision made twice and differently. That is a real need, and it is
        also the one hole in the kill switch — so it is pinned here rather than
        left as a surprise, and no production caller should construct a
        `ShadowPlan(run=True)` itself.
        """
        router = _router(omni=False)
        with mock.patch.dict(os.environ, {}, clear=True):
            observation = _observe(
                router,
                plan_override=shadow.ShadowPlan(provider="groq", run=True, reason=""))
        self.assertIsNotNone(observation)


class DefaultConfigurationTest(unittest.TestCase):
    """The shipped defaults shadow nothing, and that has to be legible."""

    def test_a_fully_enabled_shadow_still_refuses_unclassified_traffic(self):
        """The finding this module is built around, pinned so it cannot drift.

        Kill switch on, sample rate 1.0, candidate configured, ceiling at its
        default — and ordinary chat traffic is still not shadowed, because
        unclassified means CONFIDENTIAL and CONFIDENTIAL is above PUBLIC.

        That is two correct controls composing into an inert feature, which is
        the right outcome: enabling an experiment must not be the same act as
        deciding a user's private message may be copied to a vendor they never
        agreed to. If this test ever starts failing because someone raised the
        default ceiling, that is a disclosure decision and it should be
        reviewed as one rather than landing as a tuning change.
        """
        with _env():
            plan = shadow.plan(_router(), "claude", None)
        self.assertFalse(plan.run)
        self.assertEqual(plan.reason, "privacy_class")

    def test_readiness_names_the_gate_that_is_holding(self):
        """Otherwise an empty report reads as "the candidate had no problems"."""
        with _env():
            state = shadow.readiness(_router())
        self.assertTrue(state["enabled"])
        self.assertEqual(state["sample_rate"], 1.0)
        self.assertFalse(state["would_run_for_unclassified_traffic"])
        self.assertEqual(state["unclassified_blocked_by"], "privacy_class")
        self.assertEqual(state["default_request_class"], "CONFIDENTIAL")
        self.assertEqual(state["max_privacy_class"], "PUBLIC")

    def test_readiness_distinguishes_switched_off_from_gated(self):
        """"Nobody turned it on" and "it is on and refusing" are different
        problems with different fixes."""
        with mock.patch.dict(os.environ, {}, clear=True):
            off = shadow.readiness(_router())
        self.assertFalse(off["enabled"])
        self.assertEqual(off["unclassified_blocked_by"], "disabled")

    def test_raising_the_ceiling_is_necessary_but_not_sufficient(self):
        """Two gates, and the second one is the one nobody expects.

        Raising UNDX_SHADOW_MAX_PRIVACY_CLASS clears the shadow-specific gate.
        The candidate's *own* ceiling still applies, and for groq it refuses
        CONFIDENTIAL — so the reason changes from `privacy_class` to
        `privacy_ceiling` and the shadow still does not run. An operator who
        only knew about the first knob would raise it, see nothing happen, and
        conclude the feature is broken.
        """
        with _env(UNDX_SHADOW_MAX_PRIVACY_CLASS="CONFIDENTIAL"):
            plan = shadow.plan(_router(), "claude", None)
        self.assertFalse(plan.run)
        self.assertEqual(plan.reason, "privacy_ceiling")

    def test_only_a_provider_cleared_for_the_class_can_shadow_it(self):
        """The operational consequence, and it is an awkward one.

        Under the ceilings this fabric enforces, only openai, claude and meta
        accept CONFIDENTIAL — which is what unclassified traffic is. The other
        four stop at PUBLIC. So the providers most worth evaluating as cheaper
        alternatives are precisely the ones that may not see the traffic you
        would evaluate them on, and shadowing them is only possible against
        explicitly-public content.

        That is not a bug to route around. It is the privacy ceiling doing its
        job, and the honest consequence is that a cheap-provider migration has
        to be argued from the golden corpus rather than from production
        shadowing. Pinned here so the constraint is discovered by reading the
        tests rather than by an operator wondering why the report is empty.
        """
        from services import undx_privacy as privacy
        accepts = {name for name in ("openai", "claude", "gemini", "groq",
                                     "deepseek", "perplexity", "meta")
                   if privacy.provider_accepts(name, "CONFIDENTIAL", None)}
        self.assertEqual(accepts, {"openai", "claude", "meta"})

        with _env(UNDX_SHADOW_PROVIDER="meta",
                  UNDX_SHADOW_MAX_PRIVACY_CLASS="CONFIDENTIAL"):
            plan = shadow.plan(_router(providers=("meta", "claude")), "claude", None)
        self.assertTrue(plan.run, "meta accepts CONFIDENTIAL and should shadow it")

    def test_readiness_calls_no_provider(self):
        router = _router()
        with _env():
            shadow.readiness(router)
        self.assertEqual(router.calls, [])


class ObserveTest(unittest.TestCase):

    def setUp(self):
        shadow.reset_for_tests()

    def test_no_plan_means_no_call_and_no_observation(self):
        router = _router()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_observe(router))
        self.assertEqual(router.calls, [])

    def test_a_candidate_fault_never_raises_into_the_caller(self):
        """The user already has an answer; a candidate's fault is not theirs."""
        for error, expected in ((TimeoutError("t"), "timeout"),
                                (ConnectionError("c"), "request_failed"),
                                (ValueError("v"), "response_failed")):
            with self.subTest(error=type(error).__name__), _env():
                shadow.reset_for_tests()
                observation = _observe(_router(raises=error))
            self.assertIsNotNone(observation)
            self.assertEqual(observation.shadow_status, expected)
            self.assertIn(observation.shadow_status, shadow.SHADOW_STATUSES)

    def test_an_empty_completion_is_not_a_success(self):
        """200 with no content answered nothing; §19's conflation, again."""
        with _env():
            observation = _observe(_router(text="   "))
        self.assertEqual(observation.shadow_status, "empty")

    def test_a_failed_shadow_produces_no_comparison_numbers(self):
        """There is nothing to compare against, and 0.0 would read as
        "completely different" rather than as "never arrived"."""
        with _env():
            observation = _observe(_router(raises=TimeoutError("t")))
        self.assertIsNone(observation.length_ratio)
        self.assertIsNone(observation.token_overlap)
        self.assertIsNone(observation.numbers_agree)

    def test_a_failed_shadow_is_charged_to_provider_health(self):
        recorded: list[tuple] = []
        router = _router(raises=TimeoutError("t"))
        router._record_provider_failure = lambda n, s, error="": recorded.append((n, s))
        with _env():
            _observe(router)
        self.assertEqual([name for name, _ in recorded], ["groq"])

    def test_an_unpriced_response_is_recorded_as_unpriced(self):
        """`report` was tested on a row already marked unpriced; nothing tested
        that `observe` ever marks one. Five of the seven providers have no
        verified price, so this is the common path, not the edge.
        """
        with _env():
            observation = _observe(_router(usage={"provider": "groq"}))
        self.assertFalse(observation.shadow_priced)
        self.assertEqual(observation.shadow_cost_micro_usd, 0)
        self.assertFalse(shadow.report("groq", [observation])["cost_complete"])

    def test_a_priced_response_carries_its_cost_in_micro_usd(self):
        with _env():
            observation = _observe(_router(usage={"cost_usd": 0.001}))
        self.assertTrue(observation.shadow_priced)
        self.assertEqual(observation.shadow_cost_micro_usd, 1000)

    def test_a_zero_cost_is_priced_and_is_not_missing(self):
        """0.0 and None are different claims: "free" and "we do not know"."""
        with _env():
            observation = _observe(_router(usage={"cost_usd": 0.0}))
        self.assertTrue(observation.shadow_priced)
        self.assertEqual(observation.shadow_cost_micro_usd, 0)

    def test_a_failed_shadow_call_is_not_reported_as_priced(self):
        """No usage came back at all, so its cost is unknown, not zero."""
        with _env():
            observation = _observe(_router(raises=TimeoutError("slow")))
        self.assertEqual(observation.shadow_status, "timeout")
        self.assertFalse(observation.shadow_priced)

    def test_an_explicit_plan_overrides_the_environment(self):
        """So a caller that already decided does not decide twice and differ."""
        router = _router()
        with mock.patch.dict(os.environ, {}, clear=True):
            observation = _observe(
                router,
                plan_override=shadow.ShadowPlan(provider="groq", run=True, reason=""))
        self.assertIsNotNone(observation)
        self.assertEqual(router.calls, ["what is it?"])


class ComparisonTest(unittest.TestCase):
    """The numbers that replace the text, at the edges where they would lie."""

    def test_numbers_agree_only_when_the_sets_match(self):
        self.assertTrue(shadow._compare("it is 42", "the answer: 42")["numbers_agree"])
        self.assertFalse(shadow._compare("it is 42", "it is 43")["numbers_agree"])
        self.assertFalse(
            shadow._compare("42", "42 or 43")["numbers_agree"],
            "an answer that hedges between two numbers does not agree with one")

    def test_numbers_agree_is_none_when_neither_states_a_number(self):
        """False would mean "they disagreed about a fact", and they did not."""
        self.assertIsNone(shadow._compare("yes", "certainly")["numbers_agree"])

    def test_one_side_stating_a_number_is_a_disagreement(self):
        self.assertFalse(shadow._compare("about 42", "it varies")["numbers_agree"])

    def test_thousands_separators_do_not_create_a_disagreement(self):
        self.assertTrue(shadow._compare("1,200 users", "1200 users")["numbers_agree"])

    def test_length_ratio_is_none_rather_than_zero_when_a_side_is_empty(self):
        """0.0 would read as "the shadow said nothing" for the case where the
        *primary* said nothing, which points the reader at the wrong provider."""
        self.assertIsNone(shadow._compare("", "something")["length_ratio"])
        self.assertIsNone(shadow._compare("something", "")["length_ratio"])

    def test_token_overlap_is_a_fraction_of_the_union(self):
        self.assertEqual(shadow._compare("a b", "a b")["token_overlap"], 1.0)
        self.assertEqual(shadow._compare("a b", "c d")["token_overlap"], 0.0)
        self.assertEqual(shadow._compare("a b", "a c")["token_overlap"], 0.333)

    def test_overlap_ignores_case_and_punctuation(self):
        self.assertEqual(shadow._compare("Yes, it is.", "yes it is")["token_overlap"],
                         1.0)


class ReportTest(unittest.TestCase):
    """Phase 13: say what was measured, and refuse to say what was not."""

    def setUp(self):
        shadow.reset_for_tests()

    @staticmethod
    def _observation(**overrides):
        row = {"request_id": "r", "lane": "research", "primary_provider": "claude",
               "shadow_provider": "groq", "primary_status": "success",
               "shadow_status": "success", "primary_latency_ms": 100,
               "shadow_latency_ms": 200, "numbers_agree": True}
        row.update(overrides)
        return shadow.ShadowObservation(**row)

    def test_no_report_ever_declares_a_winner(self):
        """The load-bearing assertion of Phase 13.

        A shadow-vs-primary win rate over ungraded traffic is a number with no
        referent, and it would be believed because it has the same shape as the
        benchmark's number — which is derived from a corpus that has right
        answers in it. There is no sample size that changes this, so the test
        tries the cases somebody would reach for: a sweep, an empty set, and a
        single observation.
        """
        for label, rows in (
            ("empty", []),
            ("one", [self._observation()]),
            ("a clean sweep", [self._observation(numbers_agree=False)] * 40),
            ("shadow always faster", [self._observation(shadow_latency_ms=1)] * 40),
        ):
            with self.subTest(case=label):
                built = shadow.report("groq", rows)
                self.assertIsNone(built["quality_verdict"])
                self.assertIn("ungraded", built["quality_verdict_reason"])
                self.assertIn("undx_benchmark", built["quality_verdict_reason"])

    def test_availability_is_answered_over_observed(self):
        rows = [self._observation()] * 3 + [self._observation(shadow_status="timeout")]
        built = shadow.report("groq", rows)
        self.assertEqual(built["observations"], 4)
        self.assertEqual(built["shadow_answered"], 3)
        self.assertEqual(built["availability"], 0.75)
        self.assertEqual(built["failures"], {"timeout": 1})

    def test_an_empty_set_reports_none_rather_than_zero(self):
        """Zero availability means "it failed every time"; it did not run."""
        built = shadow.report("groq", [])
        self.assertIsNone(built["availability"])
        self.assertIsNone(built["median_shadow_latency_ms"])
        self.assertIsNone(built["numeric_disagreement_rate"])
        self.assertEqual(built["observations"], 0)

    def test_the_disagreement_rate_has_its_denominator_beside_it(self):
        rows = [self._observation(numbers_agree=True),
                self._observation(numbers_agree=False),
                self._observation(numbers_agree=None)]
        built = shadow.report("groq", rows)
        self.assertEqual(built["numeric_comparisons"], 2)
        self.assertEqual(built["numeric_disagreements"], 1)
        self.assertEqual(built["numeric_disagreement_rate"], 0.5)

    def test_a_failed_shadow_is_not_counted_as_a_numeric_comparison(self):
        rows = [self._observation(shadow_status="timeout", numbers_agree=None)]
        built = shadow.report("groq", rows)
        self.assertEqual(built["numeric_comparisons"], 0)

    def test_latency_is_reported_for_both_sides(self):
        rows = [self._observation(primary_latency_ms=10, shadow_latency_ms=90),
                self._observation(primary_latency_ms=30, shadow_latency_ms=110)]
        built = shadow.report("groq", rows)
        self.assertEqual(built["median_primary_latency_ms"], 20)
        self.assertEqual(built["median_shadow_latency_ms"], 100)

    def test_an_unpriced_shadow_call_marks_the_cost_incomplete(self):
        """Same rule as the benchmark: unknown is not zero."""
        built = shadow.report("groq", [self._observation(shadow_priced=False)])
        self.assertFalse(built["cost_complete"])

    def test_token_overlap_is_labelled_weak_in_the_payload(self):
        """So a dashboard binding to it inherits the caveat, not just the docs."""
        self.assertTrue(shadow.report("groq", [])["token_overlap_is_weak"])

    def test_the_source_of_the_rows_is_published(self):
        """A caller that cannot tell a ledger read from a process-local
        fallback cannot tell quiet traffic from an unreachable database."""
        self.assertEqual(shadow.report("groq", [])["source"], "supplied")
        with _env():
            _observe(_router())
            self.assertEqual(shadow.report("groq")["source"], "process")


if __name__ == "__main__":
    unittest.main()
