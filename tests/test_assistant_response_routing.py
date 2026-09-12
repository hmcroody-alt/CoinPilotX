"""The general-purpose assistant goes through the router, and five callers say where.

U2 of `UNDX_PROVIDER_CALLSITE_CENSUS.md`, and the first migration where the shape
pinned by `tests/test_sports_edge_routing.py` is not enough on its own. U1 had two
callers of one kind and could state its call domain as a fact. This one is reached
from a website API route, a Telegram handler, a menu dispatcher carrying its own
`channel`, a message router, and a wrapper with no callers — so the thing worth
protecting is not just "it routes" but "each caller declares its own provenance, and
the Telegram one does not silently become GENERAL".

Two assertions here exist because the migration *changed* behaviour and the change
needs to be the deliberate one:

* Failure now returns `fallback_response` rather than an apology naming a vendor. The
  old `OPENAI_API_KEY` branch returned strictly less than the exception handler right
  below it — no market data at all — so a deployment holding a Claude key got the
  worse of the two answers. Pinned so nobody "restores" it.
* `routed` and `source` are asserted to disagree with each other exactly when they
  should, because two callers publish that source to a user and both previously
  derived it from whether a credential existed.

Runs against a temp sqlite file so nothing can touch coinpilotx.db.

Run: python3 -m pytest tests/test_assistant_response_routing.py
"""

import ast
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="assistant_routing_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"

from services import (  # noqa: E402
    ai_router,
    ai_service,
    command_router,
    intelligence,
    undx_call_domain,
    undx_privacy,
)
from tests import undx_source_probe as probe  # noqa: E402

BOARD = {"summary": {"btc_price": 60000, "eth_price": 3000, "market_trend": "mixed",
                     "risk_level": "Medium", "average_change_24h": 0.4},
         "markets": [{"symbol": "BTC"}], "updated_at": "now", "source": "test"}


def _envelope(text, **extra):
    envelope = {"ok": True, "response": text, "provider": "claude", "source": "Claude",
                "model": "m", "attempts": [], "latency_ms": 5,
                "call_domain": "GENERAL", "call_domain_known": True}
    envelope.update(extra)
    return envelope


class _RoutedCase(unittest.TestCase):
    """Shared plumbing: the market board is stubbed, the router is captured."""

    def _route(self, response_text="Answer body.", envelope=None, **call_kwargs):
        captured = {}

        def fake(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _envelope(response_text) if envelope is None else envelope

        with mock.patch.object(intelligence.market_data, "live_market_board",
                              return_value=BOARD), \
                mock.patch.object(intelligence.undx_router, "route_structured_request",
                                  side_effect=fake):
            result = intelligence.assistant_response_envelope(4242, "Is BTC safe?",
                                                              **call_kwargs)
        return result, captured


class RoutedNotPostedTest(unittest.TestCase):
    """§11-12, read off the source: no direct provider transport survives here.

    Every check in this class is against a mechanism rather than against the characters
    in the file, via `tests/undx_source_probe.py`. The first draft was not, and it failed
    five times on this module's own docstring — which explains, at length, that
    `OPENAI_API_KEY` and `api.openai.com` are gone. A test that fires on the explanation
    of a rule makes deleting the explanation the cheapest way to green.
    """

    @classmethod
    def setUpClass(cls):
        cls.tree = probe.parse(intelligence.__file__)
        cls.literals = probe.string_literals(cls.tree)

    def test_the_probe_can_see_this_module_at_all(self):
        """Guards every `assertNotIn` below: a broken walk finds nothing either way."""
        self.assertTrue(self.literals)
        self.assertIn(intelligence.FALLBACK_SOURCE_LABEL, self.literals)

    def test_the_module_no_longer_imports_a_transport(self):
        """Whole-module, unlike U1's function-scoped check.

        `bot.py` needed the narrower check because it legitimately posts to Stripe,
        Telegram, Brevo and Mux elsewhere in the same file. This module has exactly one
        job and no such excuse, so the stronger check is the correct one — and `requests`
        being unimportable here is a better guarantee than any assertion about how it is
        used.
        """
        imported = probe.imported_names(self.tree)
        for banned in ("requests", "httpx", "urllib", "aiohttp"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, imported)

    def test_the_module_performs_no_http_of_its_own(self):
        self.assertEqual(probe.transport_calls(self.tree), [])

    def test_it_routes_through_the_router(self):
        self.assertIn("route_structured_request", probe.attribute_calls(self.tree))

    def test_no_environment_variable_is_read_here_at_all(self):
        """Stronger than banning two names, and it cannot fire on prose.

        `OPENAI_API_KEY` gated availability and `OPENAI_MODEL` was one of the four
        competing model defaults §25-27 removes — `undx_router.PROVIDERS` is the
        authority on both questions now. But rather than list the two variables this
        file used to read, this asserts it reads *none*, which is true, is checkable
        against `os.getenv`/`os.environ` call nodes, and does not need updating when
        somebody invents a fifth model default.
        """
        self.assertEqual(probe.environment_reads(self.tree), [])

    def test_no_vendor_endpoint_survives_as_a_string_it_could_request(self):
        """A docstring may say `api.openai.com`. A literal may not be one.

        The docstring above says it, deliberately, because U2 is only comprehensible
        as "this used to post there". The mechanism being tested is whether any string
        the module actually evaluates is a provider endpoint.
        """
        for literal in self.literals:
            with self.subTest(literal=literal[:50]):
                self.assertNotIn("api.openai.com", literal)
                self.assertNotIn("api.anthropic.com", literal)

    def test_no_user_visible_string_names_a_vendor(self):
        """The apology naming OpenAI is gone, and nothing replaced it.

        It leaked which vendor the deployment was expected to hold, and — see
        `FallbackIsBetterThanTheApologyTest` — it returned strictly less to the user
        than the failure path immediately below it.
        """
        for literal in self.literals:
            with self.subTest(literal=literal[:50]):
                self.assertNotIn("OpenAI", literal)


class DeclaredIntentTest(_RoutedCase):
    """§4 and §5: what this call says about itself, captured from the router call."""

    def test_the_privacy_class_is_declared_and_is_confidential(self):
        """CONFIDENTIAL, not PUBLIC, and the difference is four providers.

        U1 could justify PUBLIC because its prompt carried a scoreboard feed. This one
        carries `question` — free text the user wrote — so CONFIDENTIAL is the floor,
        and §4's "do not lower a classification to make routing possible" is precisely
        the pressure that exists here: PUBLIC would admit seven providers instead of
        three.
        """
        _, captured = self._route()
        self.assertEqual(captured["kwargs"]["privacy_class"],
                         undx_privacy.SENSITIVITY_CONFIDENTIAL)

    def test_both_declarations_are_constants_and_not_string_literals(self):
        """A misspelt class must be an AttributeError at import, not a value.

        `UNDX_SHADOW_MAX_PRIVACY_CLASS=PUBIC` ranked as SECRET and cleared traffic it
        should have refused. `call_domain` is exempt from this check by design: it is a
        forwarded parameter here, not a declaration, and the declarations live at the
        five call sites — which `EachCallerDeclaresItsOwnProvenanceTest` covers.
        """
        tree = ast.parse(pathlib.Path(intelligence.__file__).read_text(encoding="utf-8"))
        call = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "route_structured_request")
        declared = {kw.arg: kw.value for kw in call.keywords}
        self.assertIsInstance(declared["privacy_class"], ast.Attribute,
                              "privacy_class is a bare literal, not a named constant")

    def test_the_sampling_parameters_and_timeout_survived(self):
        _, captured = self._route()
        self.assertEqual(captured["kwargs"]["timeout"], 20)
        self.assertEqual(captured["kwargs"]["temperature"], 0.35)

    def test_the_pro_token_split_survived(self):
        """850 for Pro, 320 for free. A single budget would silently reprice the tier."""
        _, free = self._route(pro=False)
        _, paid = self._route(pro=True)
        self.assertEqual(free["kwargs"]["max_tokens"], 320)
        self.assertEqual(paid["kwargs"]["max_tokens"], 850)

    def test_the_system_instruction_is_preserved_verbatim(self):
        _, captured = self._route()
        self.assertEqual(captured["args"][1], intelligence.ASSISTANT_SYSTEM_PROMPT)
        for clause in ("Never guarantee profits", "Never ask for seed phrases",
                       "Market Snapshot", "Momentum Read", "Risk Level",
                       "What to Watch", "Safer Next Step", "Disclaimer"):
            with self.subTest(clause=clause):
                self.assertIn(clause, intelligence.ASSISTANT_SYSTEM_PROMPT)

    def test_the_live_context_and_question_both_reach_the_prompt(self):
        _, captured = self._route()
        self.assertIn("Is BTC safe?", captured["args"][2])
        self.assertIn("Live context", captured["args"][2])

    def test_the_user_id_never_reaches_the_prompt(self):
        """The router needs it for budgeting; the provider must not see it.

        CONFIDENTIAL is a claim about what the text carries, and an identifier in the
        prompt would make it a different claim without changing the label.
        """
        _, captured = self._route()
        self.assertEqual(captured["args"][0], 4242)
        self.assertNotIn("4242", captured["args"][2])
        self.assertNotIn("4242", captured["args"][1])

    def test_the_declared_domain_is_forwarded_untouched(self):
        _, captured = self._route(call_domain=undx_call_domain.CALL_DOMAIN_TELEGRAM)
        self.assertEqual(captured["kwargs"]["call_domain"],
                         undx_call_domain.CALL_DOMAIN_TELEGRAM)

    def test_no_declared_domain_is_forwarded_as_none_not_invented(self):
        """`undx_call_domain` owns the default, so this module must not guess one.

        Two defaults that agree today are two defaults that drift tomorrow — the same
        reasoning that removes `OPENAI_MODEL` from this file.
        """
        _, captured = self._route()
        self.assertIsNone(captured["kwargs"]["call_domain"])

    def test_the_string_form_forwards_the_domain_too(self):
        """Four of the five callers use the string form, so it is the load-bearing one.

        Checked separately because every other assertion in this class goes through
        `assistant_response_envelope` directly. That left `assistant_response`'s own
        forwarding untested, and a mutation that dropped the keyword from its one-line
        body survived the entire file — the four callers that matter most were the four
        nothing was watching.
        """
        captured = {}

        def fake(*args, **kwargs):
            captured.update(kwargs)
            return _envelope("Answer body.")

        with mock.patch.object(intelligence.market_data, "live_market_board",
                              return_value=BOARD), \
                mock.patch.object(intelligence.undx_router, "route_structured_request",
                                  side_effect=fake):
            intelligence.assistant_response(
                4242, "Is BTC safe?",
                call_domain=undx_call_domain.CALL_DOMAIN_TELEGRAM)
        self.assertEqual(captured["call_domain"], undx_call_domain.CALL_DOMAIN_TELEGRAM)


class ClassificationSubjectTest(unittest.TestCase):
    """The routing decision is made from the question, not the board in front of it.

    ``_RoutedCase`` stubs a ~200-character board, which keeps the question comfortably
    inside the classifier's 2,600-character window. That is why this class does not reuse
    it: the fixture that makes the other tests readable is the one fixture under which
    this defect cannot occur, so reusing it would produce a test that passes in both
    trees. The live board measures ~8,850 characters against that window.

    The assertion is on the argument handed to the router, not on the answer. The request
    succeeded before this fix too — at CONFIDENTIAL the ceiling refuses four providers
    and collapses most lanes onto the same reachable chain, which is precisely the
    masking that let this run in production unnoticed. The one category it does not mask
    is ``security``, which leads with Claude: "is this wallet address a scam" was
    answered by OpenAI because a CoinGecko snapshot sat in front of the sentence.
    """

    #: Big enough to push the question out of the window, and carrying the cue the real
    #: board actually carries. Measured against live CoinGecko data, the *only* freshness
    #: term in an 8,864-character board is ``2026`` — out of its own ``updated_at``
    #: timestamp. Freshness wins unconditionally in `classify_request`, so the year the
    #: snapshot stamps on itself is what pinned every assistant request to `current_web`.
    #:
    #: Two things follow, and both are why this constant is written out rather than
    #: stubbed with ``"now"``. First, a fixture without a year classifies on the board's
    #: other words instead and reproduces a different bug than the one in production —
    #: the first draft of this test used ``updated_at: "now"``, matched `risk` in
    #: `risk_level`, classified `security`, and so agreed with the question by accident.
    #: Second, the live defect is **date-dependent**: `2026` and `2027` are cues and
    #: `2028` is not, so on 1 January 2028 this misroute changes category on its own with
    #: no diff. The date here is therefore fixed, not generated.
    BIG_BOARD = {"source": "test", "updated_at": "2026-09-12T14:39:53",
                 "observed_epoch": 1789249193.24877, "age_seconds": 0, "warning": None,
                 "summary": {"btc_price": 60000, "market_trend": "mixed",
                             "risk_level": "Medium", "average_change_24h": 0.4},
                 "markets": [{"id": f"coin-{i}", "symbol": f"SYM{i}",
                              "name": f"Coin number {i} on the board",
                              "price": 1000 + i, "change_24h": 0.1 * i,
                              "market_cap": 10_000_000 + i} for i in range(80)]}

    QUESTION = "is this wallet address a scam or is the token contract safe to approve"

    def _sent(self):
        captured = {}

        def fake(*args, **kwargs):
            captured["args"], captured["kwargs"] = args, kwargs
            return _envelope("Answer body.")

        with mock.patch.object(intelligence.market_data, "live_market_board",
                              return_value=self.BIG_BOARD), \
                mock.patch.object(intelligence.undx_router, "route_structured_request",
                                  side_effect=fake):
            intelligence.assistant_response_envelope(4242, self.QUESTION)
        return captured

    def test_the_fixture_actually_buries_the_question(self):
        """Asserted, because the rest of this class is vacuous without it."""
        sent = self._sent()["args"][2]
        self.assertGreater(len(sent), 2600)
        self.assertNotIn(self.QUESTION[:20], sent[:2600])

    def test_the_question_is_what_gets_classified(self):
        kwargs = self._sent()["kwargs"]
        self.assertEqual(kwargs["classify_text"], self.QUESTION)

    def test_the_two_subjects_route_to_different_providers(self):
        """The consequence, spelled out, so that dropping `classify_text` is not merely
        a cosmetic regression here. Read at PUBLIC because CONFIDENTIAL is what masks it
        in production — asserting the masked version would be asserting that the privacy
        gate works, which is a different test that already exists."""
        import undx_router

        captured = self._sent()
        sent, subject = captured["args"][2], captured["kwargs"]["classify_text"]
        with mock.patch.object(undx_router, "multi_model_mode", lambda: True), \
                mock.patch.object(undx_router, "router_enabled", lambda: True), \
                mock.patch.object(undx_router, "provider_enabled", lambda name: True):
            from_prompt = undx_router.provider_priority(undx_router.classify_request(sent))
            from_question = undx_router.provider_priority(undx_router.classify_request(subject))
        self.assertEqual(undx_router.classify_request(sent)["category"], "current_web")
        self.assertEqual(undx_router.classify_request(subject)["category"], "security")
        self.assertNotEqual(from_prompt[0], from_question[0])
        self.assertEqual(from_question[0], "claude")
        self.assertEqual(from_prompt[0], "perplexity")


class DisclosureTest(_RoutedCase):
    """The required disclosure, and the two-condition check that avoids duplicating it."""

    def test_it_is_appended_when_the_model_omits_it(self):
        result, _ = self._route("Sharp read with no caution at all.")
        self.assertTrue(result["text"].endswith(intelligence.REQUIRED_DISCLOSURE))

    def test_the_not_financial_phrasing_satisfies_it(self):
        result, _ = self._route("Careful read. Remember this is not financial advice.")
        self.assertNotIn(intelligence.REQUIRED_DISCLOSURE, result["text"])

    def test_the_financial_advice_phrasing_also_satisfies_it(self):
        """The second of the two conditions, which is why they are not one.

        A model that writes "not investment or financial advice" never contains the
        substring "not financial", so collapsing the check would append a duplicate
        disclosure to a reply that already had one.
        """
        result, _ = self._route("Careful read. Not investment or financial advice.")
        self.assertNotIn(intelligence.REQUIRED_DISCLOSURE, result["text"])

    def test_the_check_is_case_insensitive(self):
        result, _ = self._route("Careful read. NOT FINANCIAL ADVICE.")
        self.assertNotIn(intelligence.REQUIRED_DISCLOSURE, result["text"])


class FallbackIsBetterThanTheApologyTest(_RoutedCase):
    """Every failure mode returns a full deterministic market read, and never raises.

    The old code had two failure paths with different quality: no `OPENAI_API_KEY`
    returned a bare apology, while a transport exception returned `fallback_response`
    with live market data in it. There is now one path, and it is the better one.
    """

    REFUSED = {"ok": False, "response": "",
               "error": "No provider is permitted to receive CONFIDENTIAL content.",
               "attempts": [{"provider": "Gemini", "status": "privacy_refused"}]}

    def _assert_is_the_deterministic_read(self, result):
        self.assertFalse(result["routed"])
        self.assertIsNone(result["provider"])
        self.assertEqual(result["source"], intelligence.FALLBACK_SOURCE_LABEL)
        self.assertIn("PulseSoc AI Assistant", result["text"])
        self.assertIn("Market Snapshot", result["text"])
        self.assertIn(intelligence.DISCLAIMER, result["text"])

    def test_a_router_that_refused_every_provider_falls_back(self):
        result, _ = self._route(envelope=self.REFUSED)
        self._assert_is_the_deterministic_read(result)

    def test_a_refusal_that_carries_text_still_falls_back(self):
        """`ok` is the verdict; `response` is just a field that happens to be empty today.

        Both of `undx_router`'s failure envelopes currently set `response` to `""` or
        omit it, which made the `envelope.get("ok")` guard look redundant — removing it
        entirely left this suite green. It is not redundant. The router puts its reason
        in `error`, and the sibling `route_undx_request` envelope already hardcodes
        `provider: "openai"` on failure, so an unguarded read is one refactor away from
        publishing a router error message to a user *attributed to a provider* as though
        a model had written it. The guard says what it means: a refusal is a refusal
        whatever came back with it.
        """
        result, _ = self._route(envelope={
            "ok": False,
            "response": "I cannot help with that, and here is a paragraph about why.",
            "error": "verification_failed",
            "provider": "gemini", "source": "Gemini", "attempts": [],
        })
        self._assert_is_the_deterministic_read(result)
        self.assertNotIn("here is a paragraph", result["text"])

    def test_a_router_over_budget_falls_back(self):
        result, _ = self._route(envelope={"ok": False, "error": "spend limit",
                                          "attempts": []})
        self._assert_is_the_deterministic_read(result)

    def test_an_empty_answer_falls_back_rather_than_returning_a_bare_disclosure(self):
        for empty in ("", "   ", None):
            with self.subTest(response=empty):
                result, _ = self._route(envelope=_envelope(empty))
                self._assert_is_the_deterministic_read(result)

    def test_a_raising_router_falls_back_rather_than_propagating(self):
        with mock.patch.object(intelligence.market_data, "live_market_board",
                              return_value=BOARD), \
                mock.patch.object(intelligence.undx_router, "route_structured_request",
                                  side_effect=RuntimeError("boom")):
            result = intelligence.assistant_response_envelope(7, "Is BTC safe?")
        self._assert_is_the_deterministic_read(result)

    def test_the_string_form_never_returns_empty(self):
        """Five callers put this straight into a reply, so "" is not an option."""
        for envelope in (self.REFUSED, _envelope(""), _envelope(None)):
            with self.subTest(envelope=envelope.get("ok")):
                with mock.patch.object(intelligence.market_data, "live_market_board",
                                       return_value=BOARD), \
                        mock.patch.object(intelligence.undx_router,
                                          "route_structured_request",
                                          return_value=envelope):
                    text = intelligence.assistant_response(7, "Is BTC safe?")
                self.assertTrue(text.strip())


class AttributionIsExecutionNotConfigurationTest(_RoutedCase):
    """`routed` and `source` report what happened, not what was configured.

    Two callers publish `source` to a user. Both used to compute it from whether
    `OPENAI_API_KEY` existed, which routing turned from imprecise into wrong: the key
    can be set while Gemini answers, and unset while Claude answers perfectly well.
    """

    def test_a_successful_route_reports_the_provider_that_answered(self):
        result, _ = self._route(envelope=_envelope("Body.", provider="gemini",
                                                  source="Gemini"))
        self.assertTrue(result["routed"])
        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["source"], "Gemini")

    def test_source_falls_back_to_the_provider_when_the_label_is_missing(self):
        result, _ = self._route(envelope=_envelope("Body.", provider="groq", source=""))
        self.assertEqual(result["source"], "groq")

    def test_neither_source_publishing_caller_reads_a_credential(self):
        """The regression this class exists to prevent, checked at both call sites.

        `ai_router` explains the removal in a *comment*, which `ast.parse` never sees,
        and `command_router` explains it in one too. Both would fail a substring check
        and neither reads anything — which is the whole argument for checking call nodes
        instead of characters.
        """
        for module in (ai_router, command_router):
            with self.subTest(module=module.__name__):
                self.assertEqual(probe.environment_reads(probe.parse(module.__file__)), [])

    def test_neither_caller_hardcodes_a_vendor_in_the_source_it_publishes(self):
        for module in (ai_router, command_router):
            literals = probe.string_literals(probe.parse(module.__file__))
            self.assertTrue(literals, "no literals found — the walk is broken, not clean")
            for literal in literals:
                with self.subTest(module=module.__name__, literal=literal[:40]):
                    self.assertNotIn("OpenAI", literal)


class EachCallerDeclaresItsOwnProvenanceTest(unittest.TestCase):
    """§5, at the five call sites rather than in the shared function.

    One function cannot honestly declare one domain for five callers, so the
    declaration moved outward. That makes "did every caller remember" the thing worth
    testing, and the Telegram one the thing worth testing hardest: it is the only
    attacker-influenced domain in the taxonomy, and a wrong domain produces no symptom
    at all, because §5 means it cannot widen anything.
    """

    BOT_SOURCE = None

    @classmethod
    def setUpClass(cls):
        # Deliberately not `.resolve()`. The mutation harness builds a sandbox of
        # symlinks and makes exactly one file real, so resolving this path walks back
        # out of the sandbox and reads the *unmutated* `bot.py` from the real repo —
        # which is how two mutations against these assertions survived while looking
        # like they had been checked. `intelligence.__file__` is the path Python
        # imported, so its unresolved grandparent is the tree actually under test.
        root = pathlib.Path(intelligence.__file__).parent.parent
        cls.BOT_SOURCE = (root / "bot.py").read_text(encoding="utf-8")

    def _declared_domains(self, source):
        """Every `call_domain=` value passed to an `assistant_response*` call."""
        declared = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if not name.startswith("assistant_response"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "call_domain":
                    declared.append(keyword.value)
        return declared

    def test_every_bot_py_call_site_declares_a_domain_as_a_named_constant(self):
        declared = self._declared_domains(self.BOT_SOURCE)
        self.assertEqual(len(declared), 2, "bot.py should have exactly two call sites")
        for value in declared:
            with self.subTest(value=ast.dump(value)[:60]):
                self.assertIsInstance(value, ast.Attribute,
                                      "a bare literal domain is a typo waiting to happen")

    def test_the_telegram_handler_declares_telegram_and_the_web_route_does_not(self):
        names = [value.attr for value in self._declared_domains(self.BOT_SOURCE)]
        self.assertIn("CALL_DOMAIN_TELEGRAM", names)
        self.assertIn("CALL_DOMAIN_GENERAL", names)

    def test_the_message_router_declares_a_domain(self):
        declared = self._declared_domains(
            pathlib.Path(ai_router.__file__).read_text(encoding="utf-8"))
        self.assertEqual([value.attr for value in declared], ["CALL_DOMAIN_GENERAL"])

    def test_the_uncalled_wrapper_forwards_rather_than_inventing_a_domain(self):
        """It has no callers, so it has no provenance to declare.

        Forwarding is the honest answer for a wrapper; declaring GENERAL here would be
        indistinguishable from a caller that genuinely is GENERAL.
        """
        declared = self._declared_domains(
            pathlib.Path(ai_service.__file__).read_text(encoding="utf-8"))
        self.assertEqual(len(declared), 1)
        self.assertIsInstance(declared[0], ast.Name)
        self.assertEqual(declared[0].id, "call_domain")

    def test_the_menu_dispatcher_translates_its_channel_into_a_domain(self):
        """Both directions, because a GENERAL default hides a missing translation.

        This is the rot the derivation exists to avoid: a table mapping known channels
        to domains, with GENERAL for everything else, gives a new `telegram_menu`
        channel the wrong domain and no symptom.
        """
        cases = {
            "web": undx_call_domain.CALL_DOMAIN_GENERAL,
            "web_chat": undx_call_domain.CALL_DOMAIN_GENERAL,
            "pulse_assistant": undx_call_domain.CALL_DOMAIN_GENERAL,
            "": undx_call_domain.CALL_DOMAIN_GENERAL,
            None: undx_call_domain.CALL_DOMAIN_GENERAL,
            "telegram": undx_call_domain.CALL_DOMAIN_TELEGRAM,
            "Telegram": undx_call_domain.CALL_DOMAIN_TELEGRAM,
            "telegram_menu": undx_call_domain.CALL_DOMAIN_TELEGRAM,
        }
        for channel, expected in cases.items():
            with self.subTest(channel=channel):
                self.assertEqual(command_router._call_domain_for(channel), expected)

    def test_the_menu_dispatcher_call_site_actually_uses_the_translation(self):
        """Having the helper is not the same as calling it.

        `_call_domain_for` was fully tested in both directions above while the call site
        was free to ignore it — a mutation that simply deleted the keyword argument
        survived the whole file. A translator nothing calls is a translator that is
        right about nothing.
        """
        declared = self._declared_domains(
            pathlib.Path(command_router.__file__).read_text(encoding="utf-8"))
        self.assertEqual(len(declared), 1, "the menu dispatcher has one routed call site")
        self.assertIsInstance(declared[0], ast.Call,
                              "the channel is not being translated, just omitted or fixed")
        self.assertEqual(declared[0].func.id, "_call_domain_for")

    def test_every_domain_any_caller_declares_is_a_known_one(self):
        """A misspelt domain loses its preference silently, which is why this exists."""
        for channel in ("web", "telegram", "pulse_assistant"):
            with self.subTest(channel=channel):
                self.assertTrue(undx_call_domain.is_known(
                    command_router._call_domain_for(channel)))


if __name__ == "__main__":
    unittest.main()
