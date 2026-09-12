"""The Sports Edge read goes through the router, and says what it is while doing it.

This was the first of the unrouted chat call sites in
`UNDX_PROVIDER_CALLSITE_CENSUS.md` to be migrated, and it is the simplest: one
caller, a free-text answer, graceful degradation already built in. That makes it the
right place to pin the shape every later migration has to match — declared privacy
class, declared call domain, preserved sampling parameters, preserved safety
post-processing, and a failure that costs a paragraph rather than a reply.

The docstring on `sports_edge_ai_analysis` makes two claims a reviewer would
otherwise have to take on trust: that the content is genuinely PUBLIC, and that
`user_id` never reaches the prompt. Both are asserted here, because a privacy class
defended only by a comment is a privacy class one edit away from being wrong.

Runs against a temp sqlite file so nothing can touch coinpilotx.db.

Run: python3 -m pytest tests/test_sports_edge_routing.py
"""

import ast
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="sports_edge_routing_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
# Nothing here touches a table: the router is patched and `is_pro` is patched.
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"

import bot  # noqa: E402

from services import undx_call_domain, undx_privacy  # noqa: E402
from tests import undx_source_probe as probe  # noqa: E402

GAME = {"home_team": "Home", "away_team": "Away", "home_score": 1, "away_score": 2,
        "league_label": "Test League", "status": "Q3", "state": "in"}
BASE_TEXT = "Deterministic read. Risk: elevated."


def _envelope(text, **extra):
    envelope = {"ok": True, "response": text, "provider": "claude", "model": "m",
                "attempts": [], "latency_ms": 5}
    envelope.update(extra)
    return envelope


class RoutedNotPostedTest(unittest.TestCase):
    """§11-12, read off the source: no direct provider transport survives here.

    Every check in this class asks what the module *does* — via
    `tests/undx_source_probe.py` — rather than what words appear in it. The first
    draft of this file did not, and three of its checks passed only because
    `bot.py`'s new docstring happened not to repeat one particular string. See the
    probe's own module docstring: a protection test that fires on the paragraph
    explaining the rule makes deleting that paragraph the cheapest way to green.
    """

    @classmethod
    def setUpClass(cls):
        cls.tree = probe.parse(bot.__file__)
        cls.func = probe.function(cls.tree, "sports_edge_ai_analysis")
        cls.literals = probe.string_literals(cls.tree)

    def test_the_probe_can_see_this_module_at_all(self):
        """Guards the `assertNotIn` loops below: a broken walk finds nothing either way.

        `bot.py` is 120k lines, so if the literal list came back empty the absence
        checks would all pass while proving precisely nothing. Anchored on a constant
        this migration introduced, so the guard also fails if the wrong file is parsed.
        """
        self.assertGreater(len(self.literals), 1000)
        self.assertIn(bot.SPORTS_SAFETY_LINE, self.literals)

    def test_the_function_routes_through_undx_router(self):
        self.assertIn("route_structured_request", probe.attribute_calls(self.func))

    def test_the_function_performs_no_http_of_its_own(self):
        """`requests.post`, `urlopen`, or anything else that could reach a vendor.

        Scoped to this one function rather than to the file, because `bot.py`
        legitimately posts to Stripe, Telegram, Brevo and Mux elsewhere and a
        file-wide check would either pass vacuously or forbid four working
        integrations. The probe checks the *receiver*, not the verb — see its
        docstring for why banning the attribute name `get` was the wrong shape.
        """
        found = probe.transport_calls(self.func)
        self.assertEqual(found, [], f"direct transport survived the migration: {found}")

    def test_no_provider_credential_or_model_default_is_read_here(self):
        """The `OPENAI_API_KEY` gate and the `OPENAI_MODEL` default are both gone.

        The second is one of the four competing model defaults §25-27 removes:
        `undx_router.PROVIDERS` is the authority, and a second default that agrees
        today is a second default that drifts tomorrow.

        Asserted as "reads *no* environment variable", not as "does not read these
        two names". Stronger, shorter, and it needs no edit when somebody invents a
        fifth model default — this function has no business reading configuration at
        all now that the router owns provider selection.
        """
        read = probe.environment_reads(self.func)
        self.assertEqual(read, [], f"environment read at the call site: {read}")

    def test_the_old_openai_named_function_is_gone_everywhere(self):
        """No definition and no reference — the rename left nothing dangling.

        Was `assertNotIn("openai_sports_edge_analysis", SOURCE)`, which would fire on
        a docstring recording the rename. What actually matters is that no code path
        can still reach the old name, so that is what is checked: no `def`, no call,
        no attribute access.
        """
        old = "openai_sports_edge_analysis"
        defined = [node.name for node in ast.walk(self.tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == old]
        referenced = [node for node in ast.walk(self.tree)
                      if (isinstance(node, ast.Name) and node.id == old)
                      or (isinstance(node, ast.Attribute) and node.attr == old)]
        self.assertEqual(defined, [], "the pre-migration function is still defined")
        self.assertEqual(referenced, [], f"{len(referenced)} live references to the old name")

    def test_no_vendor_chat_endpoint_survives_as_a_string_it_could_request(self):
        """The census's U1 held the only vendor chat URL in this file.

        Checked against evaluated string literals rather than raw file text, so the
        module stays free to *name* the endpoint it no longer calls. `bot.py` has to
        keep talking about this migration somewhere, and the docstring is the right
        place for it.

        One assertion rather than a `subTest` per literal: `bot.py` evaluates ~26k
        strings, and 78k subtests cost 26 seconds to prove a single absence. The
        offenders list carries the same diagnostic information the subTest name would
        have, and only when there is something to diagnose.
        """
        endpoints = ("api.openai.com", "api.anthropic.com", "api.deepseek.com")
        offenders = [f"{endpoint} in {literal[:60]!r}"
                     for literal in self.literals
                     for endpoint in endpoints if endpoint in literal]
        self.assertEqual(offenders, [], f"vendor chat endpoint still requestable: {offenders}")


class DeclaredIntentTest(unittest.TestCase):
    """§4 and §5: what this call says about itself, captured from the router call."""

    def _route(self, response_text="Analysis body.", **envelope_extra):
        captured = {}

        def fake(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _envelope(response_text, **envelope_extra)

        with mock.patch.object(bot, "is_pro", return_value=True), \
                mock.patch.object(bot.undx_router, "route_structured_request", side_effect=fake):
            result = bot.sports_edge_ai_analysis(4242, GAME, BASE_TEXT)
        return result, captured

    def test_the_privacy_class_is_declared_and_is_public(self):
        _, captured = self._route()
        self.assertEqual(captured["kwargs"]["privacy_class"], undx_privacy.SENSITIVITY_PUBLIC)

    def test_the_call_domain_is_declared_and_is_telegram(self):
        """Provenance, not content. Both callers are Telegram handlers."""
        _, captured = self._route()
        self.assertEqual(captured["kwargs"]["call_domain"],
                         undx_call_domain.CALL_DOMAIN_TELEGRAM)
        self.assertTrue(undx_call_domain.is_known(captured["kwargs"]["call_domain"]))

    def test_both_declarations_are_constants_and_not_string_literals(self):
        """A misspelt class must be an AttributeError at import, not a value.

        `UNDX_SHADOW_MAX_PRIVACY_CLASS=PUBIC` ranked as SECRET and cleared traffic it
        should have refused. A literal `"PUBILC"` here would fail the same way: silently,
        and in the safe-looking direction for a request while being wrong.
        """
        func = probe.function(probe.parse(bot.__file__), "sports_edge_ai_analysis")
        call = next(node for node in ast.walk(func)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "route_structured_request")
        declared = {kw.arg: kw.value for kw in call.keywords}
        for name in ("privacy_class", "call_domain"):
            with self.subTest(argument=name):
                self.assertIsInstance(declared[name], ast.Attribute,
                                      f"{name} is a bare literal, not a named constant")

    def test_the_sampling_parameters_and_budget_survived(self):
        _, captured = self._route()
        self.assertEqual(captured["kwargs"]["timeout"], 20)
        self.assertEqual(captured["kwargs"]["temperature"], 0.32)
        self.assertEqual(captured["kwargs"]["max_tokens"], 700)

    def test_the_system_instruction_is_preserved_verbatim(self):
        """"never certainty-based" is the safety posture, not a stylistic note."""
        _, captured = self._route()
        self.assertEqual(captured["args"][1], bot.SPORTS_EDGE_SYSTEM_PROMPT)
        self.assertIn("never certainty-based", bot.SPORTS_EDGE_SYSTEM_PROMPT)
        self.assertIn("cautious, ethical, analytical", bot.SPORTS_EDGE_SYSTEM_PROMPT)

    def test_the_prompt_still_demands_the_safety_line_and_forbids_guarantees(self):
        _, captured = self._route()
        prompt = captured["args"][2]
        self.assertIn("without guaranteeing outcomes", prompt)
        self.assertIn(bot.SPORTS_SAFETY_LINE, prompt)

    def test_the_user_id_never_reaches_the_prompt(self):
        """The claim that makes PUBLIC honest, asserted rather than asserted-in-prose.

        PUBLIC admits all seven providers; CONFIDENTIAL admits three. The whole
        defence of the wider class is that the text carries public game data and no
        identity, so `user_id` reaching the prompt would silently turn a correct
        classification into §4's named failure.
        """
        _, captured = self._route()
        self.assertEqual(captured["args"][0], 4242, "the router still needs it for budgeting")
        self.assertNotIn("4242", captured["args"][2])
        self.assertNotIn("4242", captured["args"][1])


class SafetyLineTest(unittest.TestCase):

    def _answer(self, model_text):
        with mock.patch.object(bot, "is_pro", return_value=True), \
                mock.patch.object(bot.undx_router, "route_structured_request",
                                  return_value=_envelope(model_text)):
            return bot.sports_edge_ai_analysis(7, GAME, BASE_TEXT)

    def test_it_is_appended_when_the_model_omits_it(self):
        result = self._answer("Sharp read with no caution at all.")
        self.assertTrue(result.endswith(bot.SPORTS_SAFETY_LINE))

    def test_it_is_not_duplicated_when_the_model_includes_it(self):
        result = self._answer(f"Careful read.\n\n{bot.SPORTS_SAFETY_LINE}")
        self.assertEqual(result.count(bot.SPORTS_SAFETY_LINE), 1)

    def test_it_survives_a_provider_that_returns_only_whitespace_around_it(self):
        result = self._answer(f"   {bot.SPORTS_SAFETY_LINE}   ")
        self.assertEqual(result, bot.SPORTS_SAFETY_LINE)


class GracefulDegradationTest(unittest.TestCase):
    """A missing paragraph, never a missing reply.

    The caller renders a complete deterministic Sports Edge read and only swaps in
    the model's text when there is some. So every failure mode here has exactly one
    correct answer — `None` — and none of them may propagate.
    """

    def _with_router(self, **patch):
        return mock.patch.object(bot.undx_router, "route_structured_request", **patch)

    def test_a_router_that_refused_every_provider_returns_none(self):
        refused = {"ok": False, "error": "No provider is permitted to receive PUBLIC content.",
                   "attempts": [{"provider": "OpenAI", "status": "privacy_refused"}]}
        with mock.patch.object(bot, "is_pro", return_value=True), \
                self._with_router(return_value=refused):
            self.assertIsNone(bot.sports_edge_ai_analysis(7, GAME, BASE_TEXT))

    def test_a_router_over_budget_returns_none(self):
        with mock.patch.object(bot, "is_pro", return_value=True), \
                self._with_router(return_value={"ok": False, "error": "spend limit", "attempts": []}):
            self.assertIsNone(bot.sports_edge_ai_analysis(7, GAME, BASE_TEXT))

    def test_a_router_that_raises_returns_none_rather_than_propagating(self):
        with mock.patch.object(bot, "is_pro", return_value=True), \
                self._with_router(side_effect=RuntimeError("boom")):
            self.assertIsNone(bot.sports_edge_ai_analysis(7, GAME, BASE_TEXT))

    def test_an_empty_answer_returns_none_rather_than_a_bare_safety_line(self):
        """Otherwise a provider returning nothing would replace a full read with a
        disclaimer, which reads as a failure to the user and as a success to the
        metrics."""
        for empty in ("", "   ", None):
            with self.subTest(response=empty):
                with mock.patch.object(bot, "is_pro", return_value=True), \
                        self._with_router(return_value=_envelope(empty)):
                    self.assertIsNone(bot.sports_edge_ai_analysis(7, GAME, BASE_TEXT))


class GateTest(unittest.TestCase):
    """The entitlement check still happens before any money is spent."""

    def test_a_free_user_is_not_routed_at_all(self):
        with mock.patch.object(bot, "is_pro", return_value=False), \
                mock.patch.object(bot.undx_router, "route_structured_request") as route:
            self.assertIsNone(bot.sports_edge_ai_analysis(7, GAME, BASE_TEXT))
        route.assert_not_called()

    def test_an_anonymous_caller_is_not_routed_at_all(self):
        for missing in (None, 0, ""):
            with self.subTest(user_id=missing):
                with mock.patch.object(bot.undx_router, "route_structured_request") as route:
                    self.assertIsNone(bot.sports_edge_ai_analysis(missing, GAME, BASE_TEXT))
                route.assert_not_called()


if __name__ == "__main__":
    unittest.main()
