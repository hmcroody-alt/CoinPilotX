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
import pathlib
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

GAME = {"home_team": "Home", "away_team": "Away", "home_score": 1, "away_score": 2,
        "league_label": "Test League", "status": "Q3", "state": "in"}
BASE_TEXT = "Deterministic read. Risk: elevated."


def _envelope(text, **extra):
    envelope = {"ok": True, "response": text, "provider": "claude", "model": "m",
                "attempts": [], "latency_ms": 5}
    envelope.update(extra)
    return envelope


class RoutedNotPostedTest(unittest.TestCase):
    """§11-12, read off the source: no direct provider transport survives here."""

    SOURCE = pathlib.Path(bot.__file__).read_text(encoding="utf-8")

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(cls.SOURCE)
        cls.func = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "sports_edge_ai_analysis")

    def test_the_function_routes_through_undx_router(self):
        attributes = [node.func.attr for node in ast.walk(self.func)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertIn("route_structured_request", attributes)

    def test_the_function_performs_no_http_of_its_own(self):
        """`requests.post`, `urlopen`, or anything else that could reach a vendor.

        Checked by AST inside this one function rather than by grepping the file,
        because `bot.py` legitimately posts to Stripe, Telegram, Brevo and Mux
        elsewhere and a file-wide grep would either pass vacuously or forbid those.

        The check is on the *receiver*, not the verb. The first draft banned the bare
        attribute name `get` and caught four `envelope.get(...)` dict reads, which is
        the same mistake as a test that fires on prose: `get` is not a transport, it is
        a word transports happen to use. `requests` is a transport, and it is the only
        one `bot.py` imports, so naming the receiver is both narrower and stricter —
        `requests.post` is caught whichever verb it uses, and a dict is never caught.
        """
        transports = {"requests", "httpx", "urllib", "http", "aiohttp", "session",
                      "undx_router_http", "openai", "anthropic"}
        found = []
        for node in ast.walk(self.func):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Name) and target.id in {"urlopen", "Request"}:
                found.append(target.id)
                continue
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr in {"urlopen", "Session"}:
                found.append(target.attr)
                continue
            root = target
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id.lower() in transports:
                found.append(f"{root.id}.{target.attr}")
        self.assertEqual(found, [], f"direct transport survived the migration: {found}")

    def test_no_provider_credential_or_model_default_is_read_here(self):
        """The `OPENAI_API_KEY` gate and the `OPENAI_MODEL` default are both gone.

        The second is one of the four competing model defaults §25-27 removes:
        `undx_router.PROVIDERS` is the authority, and a second default that agrees
        today is a second default that drifts tomorrow.
        """
        read = [arg.value for node in ast.walk(self.func)
                if isinstance(node, ast.Call)
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and arg.value.isupper() and "_" in arg.value]
        self.assertEqual(read, [], f"environment read at the call site: {read}")

    def test_the_old_openai_named_function_is_gone_everywhere(self):
        self.assertNotIn("openai_sports_edge_analysis", self.SOURCE)

    def test_bot_py_no_longer_names_the_openai_chat_endpoint(self):
        """The census's U1 was the only `api.openai.com` literal in this file."""
        self.assertNotIn("api.openai.com", self.SOURCE)


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
        tree = ast.parse(pathlib.Path(bot.__file__).read_text(encoding="utf-8"))
        func = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "sports_edge_ai_analysis")
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
