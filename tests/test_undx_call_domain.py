"""A domain may reorder providers. It may never widen what a call is allowed to do."""

from __future__ import annotations

import ast
import inspect
import pathlib
import unittest
from unittest import mock

import undx_router

from services import undx_call_domain as cd

MODULE_PATH = pathlib.Path(cd.__file__)
SOURCE = MODULE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


class PermissionsMayNotUseDomainTest(unittest.TestCase):
    """§5, enforced by keeping the privacy vocabulary out of the file.

    The rule is "routing may use domain, permissions may not". A comment saying so
    survives exactly until somebody needs a quick exception. These three tests make
    the exception impossible to write without deleting a test, which is a reviewable
    act in a way that adding a branch is not.
    """

    def test_the_module_does_not_import_privacy(self):
        """It cannot consult a ceiling it cannot reach.

        Every name an import introduces is checked, not just the module half. The
        first version of this test inspected `node.module` for `ImportFrom` and
        `alias.name` only for plain `import`, which let through
        `from services import undx_privacy` — where `node.module` is `"services"`
        and the banned word lives in the alias. That is how every other module in
        this repo writes its imports, so the hole was directly over the likeliest
        line anybody would type. It survived the mutation check in
        `scripts/undx_call_domain_mutation_check.py`; this version does not.

        Relative imports are covered by the same collection: `from . import
        undx_privacy` has `node.module is None`, which the old `and node.module`
        guard skipped silently rather than flagged.
        """
        introduced: list[str] = []
        for node in ast.walk(TREE):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                introduced.append(getattr(node, "module", None) or "")
                introduced.extend(alias.name for alias in node.names)
                introduced.extend(alias.asname or "" for alias in node.names)

        for banned in ("privacy", "undx_router"):
            for name in introduced:
                with self.subTest(banned=banned, name=name):
                    self.assertNotIn(banned, name)

    def test_the_module_names_no_privacy_class_anywhere(self):
        """Not even in prose, which is the stricter of the two checks below.

        Naming a class is the first half of comparing to one, and unlike a provider
        name there is no legitimate reason for this file to write one down — the
        rule can be explained in the general ("a privacy class", "the ceilings")
        without ever instantiating the vocabulary. So the cheap substring check is
        the right one here: it costs the module nothing it needs.
        """
        lowered = SOURCE.lower()
        for klass in ("synthetic", "confidential", "restricted", "highly_sensitive",
                      "secret", "internal"):
            with self.subTest(term=klass):
                self.assertNotIn(klass, lowered)

    def test_no_provider_name_reaches_the_module_as_data(self):
        """Checked against literals rather than characters, deliberately.

        The threat is `_PREFERENCE = {"SCAM_SHIELD": ("openai", ...)}` — a promotion
        nobody benchmarked, arriving as a string literal that looks like config. An
        AST check catches exactly that and nothing else.

        The substring form is stronger on paper and was what this test did first. It
        failed, on the `_PREFERENCE` comment naming `openai` as the anti-example it
        exists to forbid. That failure is the argument against it: a test satisfiable
        by deleting the explanation of why the rule exists will eventually be
        satisfied that way, and then the rule is a bare empty dict with no reason
        attached. Prose that names the trap is how the next reader learns it.

        Docstrings are excluded for the same reason, on the precedent of
        `test_undx_canary.py::test_one_switch_has_one_implementation` — "discussing
        it in a docstring is fine and expected".
        """
        docstrings = set()
        for node in ast.walk(TREE):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

        literals = [node.value.lower() for node in ast.walk(TREE)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings]
        self.assertTrue(literals, "no literals found — the walk is broken, not clean")

        for provider in ("openai", "claude", "anthropic", "gemini", "groq",
                         "deepseek", "perplexity", "meta", "muse"):
            with self.subTest(term=provider):
                offenders = [text for text in literals if provider in text]
                self.assertEqual(offenders, [], f"provider literal in source: {offenders}")

    def test_no_public_function_answers_a_permission_question(self):
        """No allow/deny/ceiling surface exists to be called by mistake.

        Checked by name against the module's actual public functions rather than by
        reading the docstring, so a later `def may_route(...)` fails here instead of
        being noticed in review or not.
        """
        public = {node.name for node in TREE.body
                  if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
        self.assertEqual(public, {"normalise", "is_known", "routing_preference", "readiness"})
        for name in public:
            for banned in ("allow", "permit", "deny", "ceiling", "class_for", "may_"):
                self.assertNotIn(banned, name)

    def test_a_preference_can_reorder_but_never_enlarge(self):
        """Every name a preference could emit is a name routing already knew.

        This is the property that keeps the table on the routing side of the rule:
        an ordering hint intersected with an admitted set cannot add to that set. The
        table is empty today, so this asserts the invariant that must survive the
        first entry rather than a fact about the current contents.
        """
        for domain in cd.CALL_DOMAINS:
            with self.subTest(domain=domain):
                self.assertIsInstance(cd.routing_preference(domain), tuple)
        emitted = {name for domain in cd.CALL_DOMAINS
                   for name in cd.routing_preference(domain)}
        from undx_router import PROVIDERS
        self.assertTrue(emitted <= set(PROVIDERS),
                        f"preference names a provider the router does not have: "
                        f"{emitted - set(PROVIDERS)}")


class NoPromotionTest(unittest.TestCase):
    """§32: the default objective is consolidation, not model promotion."""

    def test_the_preference_table_is_empty_until_a_benchmark_fills_it(self):
        """Guards the state, and says what to do when this test becomes wrong.

        This is not an assertion that the table must stay empty forever. It is a
        tripwire: filling it is a provider promotion, which the mission allows only
        with benchmark, safety, cost and latency evidence behind it. When that
        evidence exists, delete this test in the same commit that adds the entry and
        cite the benchmark run in the message — so the promotion arrives as a
        decision rather than as a diff nobody read.
        """
        self.assertEqual(cd._PREFERENCE, {})
        self.assertEqual(cd.readiness()["declared_preferences"], 0)

    def test_no_domain_changes_routing_today(self):
        for domain in cd.CALL_DOMAINS:
            with self.subTest(domain=domain):
                self.assertEqual(cd.routing_preference(domain), ())


class VocabularyTest(unittest.TestCase):

    def test_the_eight_domains_the_brief_names(self):
        self.assertEqual(set(cd.CALL_DOMAINS), {
            "GENERAL", "SECURITY", "MESSAGING", "SCAM_SHIELD",
            "TELEGRAM", "PRIVATE_OFFICE", "COMMERCE", "RESEARCH"})

    def test_domains_do_not_rank(self):
        """A set, not an ordered ladder.

        Privacy classes rank because `rank(x) > rank(ceiling)` is how a request gets
        refused. If domains ranked, that comparison would be expressible for them
        too, and the rule at the top of the module would depend on nobody writing
        it.

        Looks for the three shapes a ladder actually arrives in rather than for the
        characters "rank": a named rank table or function, an ordinal taken from the
        tuple with `.index`, and any domain mapped to a number. The first version of
        this test checked the source text and failed on the comment explaining why
        domains do not rank — the word has to be sayable here precisely because the
        mechanism must not be.
        """
        ranked_names = [node.name for node in ast.walk(TREE)
                        if isinstance(node, ast.FunctionDef) and "rank" in node.name.lower()]
        self.assertEqual(ranked_names, [], f"a rank function exists: {ranked_names}")

        ranked_targets = [target.id for node in ast.walk(TREE)
                          if isinstance(node, (ast.Assign, ast.AnnAssign))
                          for target in (node.targets if isinstance(node, ast.Assign)
                                         else [node.target])
                          if isinstance(target, ast.Name) and "rank" in target.id.lower()]
        self.assertEqual(ranked_targets, [], f"a rank table exists: {ranked_targets}")

        indexing = [node for node in ast.walk(TREE)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "index"]
        self.assertEqual(indexing, [], "a domain's tuple position is being read as an ordinal")

        #: The type that refuses to be compared, and the reason it is not a tuple.
        self.assertIsInstance(cd._KNOWN, frozenset)

        #: No domain maps to a number, in any table the module carries.
        for name, value in vars(cd).items():
            if not isinstance(value, dict):
                continue
            for key, mapped in value.items():
                if key in cd.CALL_DOMAINS:
                    with self.subTest(table=name, domain=key):
                        self.assertNotIsInstance(mapped, (int, float))

    def test_an_undeclared_domain_is_general(self):
        for empty in (None, "", "   "):
            with self.subTest(value=empty):
                self.assertEqual(cd.normalise(empty), "GENERAL")
                self.assertTrue(cd.is_known(empty))

    def test_case_and_separators_are_tolerated(self):
        for raw in ("scam_shield", "Scam-Shield", " SCAM SHIELD ", "scam-shield"):
            with self.subTest(raw=raw):
                self.assertEqual(cd.normalise(raw), "SCAM_SHIELD")
                self.assertTrue(cd.is_known(raw))

    def test_a_typo_is_visible_rather_than_silently_default(self):
        """The lesson the privacy ceiling taught, applied before it costs anything.

        `UNDX_SHADOW_MAX_PRIVACY_CLASS=PUBIC` ranked as SECRET and cleared traffic it
        should have refused, and nothing in any report said so. The shape of that bug
        is a name off the ladder inheriting a default that means the opposite of what
        the operator typed.

        Here a typo can only cost a routing hint, so it is not dangerous — but it is
        still invisible, and invisible is how the expensive version starts. So
        `normalise` returns the typo as written instead of folding it into GENERAL,
        and `is_known` reports it.
        """
        for typo in ("SCAM_SHEILD", "TELEGRAN", "GENERALL", "RESEARCH_", "SECURTY"):
            with self.subTest(typo=typo):
                self.assertFalse(cd.is_known(typo))
                self.assertEqual(cd.normalise(typo), typo.upper())
                # And it costs a preference rather than the call.
                self.assertEqual(cd.routing_preference(typo), ())

    def test_a_typo_is_distinguishable_from_a_deliberate_general(self):
        self.assertTrue(cd.is_known("GENERAL"))
        self.assertFalse(cd.is_known("GENERAL_"))

    def test_readiness_publishes_the_vocabulary_and_not_the_table(self):
        report = cd.readiness()
        self.assertEqual(report["default_call_domain"], "GENERAL")
        self.assertEqual(len(report["call_domains"]), 8)
        self.assertIsInstance(report["declared_preferences"], int)


class DomainReorderIsNotWideningTest(unittest.TestCase):
    """`undx_router._domain_ordered` is where §5's rule is honoured or lost.

    The tests above keep the *vocabulary* of permissions out of the domain module.
    These keep the *effect* out of the router: they populate `_PREFERENCE` with
    entries the real table does not have yet, because the invariant has to be proven
    against a table with contents, and the shipped table is empty. When the first
    benchmarked entry lands, these tests already cover it.
    """

    ADMITTED = ["openai", "claude", "gemini"]

    def _ordered(self, preference, domain, admitted=None):
        with mock.patch.dict(cd._PREFERENCE, preference, clear=True):
            return undx_router._domain_ordered(
                list(self.ADMITTED if admitted is None else admitted), domain)

    def test_a_preference_moves_an_admitted_provider_to_the_front(self):
        self.assertEqual(
            self._ordered({"SECURITY": ("gemini",)}, "SECURITY"),
            ["gemini", "openai", "claude"])

    def test_a_preference_cannot_add_a_provider_that_privacy_refused(self):
        """The test this whole module exists for.

        `perplexity` is capped at PUBLIC because its prompt becomes a live search
        query, so a request carrying anything private reaches `_domain_ordered` with
        perplexity already absent from the admitted list. A preference naming it must
        find nothing to move rather than put it back.
        """
        self.assertEqual(
            self._ordered({"RESEARCH": ("perplexity",)}, "RESEARCH"),
            self.ADMITTED)

    def test_a_preference_naming_an_unknown_provider_is_inert(self):
        self.assertEqual(
            self._ordered({"SECURITY": ("nonesuch", "gemini")}, "SECURITY"),
            ["gemini", "openai", "claude"])

    def test_no_preference_leaves_the_plan_exactly_as_it_was(self):
        self.assertEqual(self._ordered({}, "SECURITY"), self.ADMITTED)
        self.assertEqual(self._ordered({}, None), self.ADMITTED)

    def test_an_empty_admitted_list_stays_empty(self):
        """Nothing admitted means nothing to reorder, not a chance to supply one."""
        self.assertEqual(
            self._ordered({"SECURITY": ("openai", "claude")}, "SECURITY", admitted=[]),
            [])

    def test_the_result_is_always_a_permutation_of_what_was_admitted(self):
        """The property, checked against every provider name the router knows.

        A preference listing *all seven* providers for *every* domain is the most
        widening table that could be written. It still cannot widen anything, because
        the result is assembled by partitioning the admitted list rather than by
        concatenating the preference onto it.
        """
        everything = tuple(undx_router.PROVIDERS)
        greedy = {domain: everything for domain in cd.CALL_DOMAINS}
        for domain in list(cd.CALL_DOMAINS) + ["SCAM_SHEILD", "", None]:
            for admitted in ([], ["openai"], ["claude", "openai"], self.ADMITTED):
                with self.subTest(domain=domain, admitted=admitted):
                    result = self._ordered(greedy, domain, admitted=admitted)
                    self.assertEqual(sorted(result), sorted(admitted))
                    self.assertEqual(len(result), len(set(result)))

    def test_a_stranger_choosing_the_domain_can_only_choose_an_order(self):
        """TELEGRAM is the attacker-influenced domain, so it gets its own test.

        Not because the code path differs — it does not — but because "an attacker
        picks this value" is the reason the rule exists, and a test named after the
        threat survives a refactor that a test named after the function might not.
        """
        greedy = {domain: tuple(undx_router.PROVIDERS) for domain in cd.CALL_DOMAINS}
        for attacker_choice in ["TELEGRAM", "PRIVATE_OFFICE", "SECURITY", "../../etc", "*"]:
            with self.subTest(claimed=attacker_choice):
                self.assertEqual(
                    sorted(self._ordered(greedy, attacker_choice)),
                    sorted(self.ADMITTED))

    def test_both_router_entry_points_accept_a_declared_domain(self):
        """A signature check, so a caller declaring a domain cannot be silently dropped."""
        for func in (undx_router.route_undx_request, undx_router.route_structured_request):
            with self.subTest(func=func.__name__):
                self.assertIn("call_domain", inspect.signature(func).parameters)

    @staticmethod
    def _call_lines(tree: ast.AST, name: str) -> dict[str, list[int]]:
        """Line numbers of every simple-name call inside the named function.

        Ordering is compared by line number, not by position in `ast.walk`, which
        is breadth-first and would have made these assertions pass for reasons
        unrelated to source order.
        """
        func = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == name)
        lines: dict[str, list[int]] = {}
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                lines.setdefault(node.func.id, []).append(node.lineno)
        return lines

    def test_the_domain_is_consulted_before_the_gate_ladder(self):
        """`_domain_ordered` must run on a plan that is already settled.

        It has to come after the kill switch and any explicit `providers=` list,
        and before the ladder that refuses providers — so the list it partitions
        is the list that will actually be tried.

        This used to assert `_domain_ordered` preceded `_privacy_refusal` *inside*
        each entry point. The ladder now lives in `_gate`, so a direct
        `_privacy_refusal` call here would mean the extraction had been partly
        undone. The property is unchanged; only the name of the thing that must
        come second has moved, and it is pinned in `_gate` by the test below.
        """
        source = pathlib.Path(undx_router.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for name in ("route_undx_request", "route_structured_request"):
            lines = self._call_lines(tree, name)
            with self.subTest(func=name):
                self.assertIn("_domain_ordered", lines,
                              f"{name} does not reorder by declared domain at all")
                self.assertIn("_gate", lines,
                              f"{name} does not consult the shared gate ladder; a "
                              f"hand-written copy is how the four transcriptions "
                              f"drifted apart in the first place")
                self.assertLess(min(lines["_domain_ordered"]), min(lines["_gate"]),
                                "the plan must be reordered before the ladder that "
                                "refuses providers, not after it")

    def test_the_gate_ladder_checks_privacy_before_it_reads_a_credential(self):
        """§5's rule, pinned where the ladder actually lives.

        Privacy first so that a provider which must not see this content is never
        consulted about whether it could have, and the capability check second for
        the same reason — a provider that cannot answer the question being asked is
        not asked for its key either. Budget, credential and breaker follow.

        Asserting the whole order here rather than one pair is only possible
        because there is now one ladder. While it was transcribed four times this
        test could pin two entry points and say nothing about the two surfaces that
        predicted them, one of which had already dropped the privacy gate entirely
        for callers that declared no class.
        """
        source = pathlib.Path(undx_router.__file__).read_text(encoding="utf-8")
        lines = self._call_lines(ast.parse(source), "_gate")

        for gate in ("_privacy_refusal", "_budget_refusal", "_api_key",
                     "_breaker_should_skip"):
            self.assertIn(gate, lines, f"_gate does not consult {gate}")

        privacy = min(lines["_privacy_refusal"])
        for later in ("_budget_refusal", "_api_key", "_breaker_should_skip"):
            self.assertLess(privacy, min(lines[later]),
                            f"the privacy ceiling must be checked before {later}")
        self.assertLess(min(lines["_budget_refusal"]), min(lines["_api_key"]),
                        "the budget is checked before a credential is read")
        self.assertLess(min(lines["_api_key"]), min(lines["_breaker_should_skip"]),
                        "the credential is read before the breaker is consulted")

    def test_the_gate_ladder_never_makes_the_privacy_check_conditional(self):
        """An omitted class must reach `_privacy_refusal`, not skip it.

        `undx_privacy.normalise(None)` is CONFIDENTIAL, so guarding the call with
        `if privacy_class` is not defensive handling of a missing value — it is what
        throws the default ceiling away. `undx_routing_evidence.explain` carried
        exactly that guard and therefore predicted Perplexity as the first choice
        for requests the real loop refuses there.

        Measured rather than read: with every provider keyed and no breaker resting,
        `_gate` must still refuse the PUBLIC-ceiling providers when the caller
        declared nothing at all.
        """
        with mock.patch.object(undx_router, "_api_key", lambda provider: "sk-test"), \
                mock.patch.object(undx_router, "_breaker_should_skip", lambda provider: False), \
                mock.patch.object(undx_router, "_budget_refusal",
                                  lambda snapshot, provider: ""):
            refused = {
                name: (undx_router._gate(name, privacy_class=None, budget={}) or {})
                for name in undx_router.PROVIDERS
            }

        ceiling_refused = sorted(n for n, entry in refused.items()
                                 if entry.get("status") == "privacy_refused")
        self.assertEqual(ceiling_refused,
                         ["deepseek", "gemini", "groq", "perplexity"],
                         "an omitted privacy class must be treated as CONFIDENTIAL, "
                         "which the PUBLIC-ceiling providers cannot receive")
        self.assertEqual(
            sorted(n for n, entry in refused.items() if not entry),
            ["claude", "meta", "openai"])


if __name__ == "__main__":
    unittest.main()
