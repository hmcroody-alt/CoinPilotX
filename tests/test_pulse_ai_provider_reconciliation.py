"""There is one router. The second one grounds and verifies, and nothing else.

U5, U6 and U7 of `UNDX_PROVIDER_CALLSITE_CENSUS.md` — three direct provider calls that
all lived in `services/pulse_ai_provider_router.py`, a module that was a complete second
implementation of `undx_router`: its own five-provider table, its own `PULSE_AI_*_MODEL`
defaults, its own key lookup, its own timeout, and three hand-rolled transports.

This file is shaped differently from the two migration tests before it, because §13 asks
a different question. U1 and U2 asked "did this call site stop calling a vendor". Here
that is necessary and nowhere near sufficient: a module can stop posting to
`api.openai.com` and still be a second router, still holding the table that decides which
model PulseSoc uses. So roughly half of what follows is about *absence of a second
authority* rather than absence of a request.

Three things are worth saying about why particular assertions exist.

**The disagreement was not hypothetical.** The deleted table defaulted Claude to
`claude-3-5-haiku-latest` and Gemini to `gemini-1.5-flash`. Both were retired upstream and
returned 404 to every request. `undx_router` had already been corrected to
`claude-haiku-4-5` and `gemini-flash-lite-latest`. So the busiest chat surface in the
product was burning two providers per turn before reaching one that answered, and the only
symptom was that UNDX felt slow. `test_the_model_for_every_provider_comes_from_one_table`
is the assertion that would have caught it, and it is written as "this module names no
model at all" rather than "this module does not name those two".

**Two things had to survive, and they are not transport.**
`prepare_undx_model_request` prepends four canonical system blocks and then verifies all
four are present, failing closed. `undx_identity_violation` checks the answer and gets one
regeneration. Grounding a request and verifying its answer belong to the caller;
`undx_router` deliberately does not know what a UNDX turn is supposed to sound like. A
consolidation that swallowed them would have been a regression dressed as cleanup, so
`GroundingSurvivedTest` pins the four blocks, the fail-closed behaviour, and the fact that
the joined system prompt reaching the router *leads* with the identity block.

**A capability hint is not provenance and is not a privacy class.** `task` — "cyber",
"technical", "web", "fast" — is inferred upstream from a safety classifier. It says which
providers suit the work. It says nothing about where the request came from or what the
content is worth protecting at. Phase 4 established that distinction the hard way, so
`TaskIsAHintNotAPermissionTest` asserts `task` reaches `providers=` and reaches neither
`call_domain` nor `privacy_class`, for every task string the module recognises.

Run: python3 -m pytest tests/test_pulse_ai_provider_reconciliation.py
"""

import ast
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="provider_reconciliation_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"

import undx_router  # noqa: E402

from services import (  # noqa: E402
    pulse_ai_provider_router as provider_router,
    undx_call_domain,
    undx_privacy,
)
from tests import undx_source_probe as probe  # noqa: E402


def _grounded(question: str = "Who are you?") -> list[dict[str, str]]:
    return [{"role": "user", "content": question}]


def _envelope(response: str, provider: str = "claude", **extra) -> dict:
    """A success envelope in `route_structured_request`'s exact shape."""
    envelope = {
        "ok": True,
        "response": response,
        "provider": provider,
        "source": undx_router.PROVIDERS[provider].label if provider in undx_router.PROVIDERS else provider,
        "model": f"{provider}-test-model",
        "citations": [],
        "usage": {},
        "attempts": [{"provider": undx_router.PROVIDERS[provider].label, "status": "success"}],
        "call_domain": undx_call_domain.CALL_DOMAIN_MESSAGING,
        "call_domain_known": True,
        "latency_ms": 41,
    }
    envelope.update(extra)
    return envelope


def _refusal(*statuses: str, error: str = "no configured provider answered") -> dict:
    labels = list(undx_router.PROVIDERS)
    return {
        "ok": False,
        "response": "",
        "error": error,
        "attempts": [{"provider": undx_router.PROVIDERS[labels[index]].label, "status": status}
                     for index, status in enumerate(statuses)],
        "call_domain": undx_call_domain.CALL_DOMAIN_MESSAGING,
        "call_domain_known": True,
        "latency_ms": 3,
    }


# --------------------------------------------------------------------- mechanism


class NoSecondRouterTest(unittest.TestCase):
    """The module stopped being a router, not just stopped calling OpenAI.

    Every check here is scoped to the whole module rather than to one function, because
    a second provider table is a module-level fact. That is the opposite scoping from
    `tests/test_sports_edge_routing.py`, where `bot.py` legitimately posts to Stripe,
    Telegram, Brevo and Mux and a file-wide transport ban would forbid four working
    integrations. Here nothing in the file has any business making a request.
    """

    @classmethod
    def setUpClass(cls):
        # Deliberately not `.resolve()`. The mutation harness builds a sandbox of
        # symlinks and makes exactly one file real, so resolving walks back out to the
        # unmutated original — which is how two mutations against
        # `tests/test_assistant_response_routing.py` survived while appearing checked.
        cls.path = pathlib.Path(provider_router.__file__)
        cls.tree = probe.parse(cls.path)
        cls.literals = probe.string_literals(cls.tree)

    def test_the_probe_can_see_this_module_at_all(self):
        """Guards every absence assertion below against a silently empty walk.

        `assertEqual(offenders, [])` passes just as happily when the tree is empty as
        when the file is clean, and the two are indistinguishable from the result.
        """
        self.assertGreater(len(self.literals), 40)
        self.assertIn(provider_router.UNDX_IDENTITY_REQUIRED_PHRASE, self.literals)

    def test_the_module_performs_no_http_of_its_own(self):
        found = probe.transport_calls(self.tree)
        self.assertEqual(found, [], f"transport call survived the reconciliation: {found}")

    def test_requests_is_not_even_imported_any_more(self):
        """Stronger than banning the calls, and it is the check that stays true.

        A module that still imports `requests` is one line away from posting again, and
        the line that does it will be written by somebody who saw the import and
        reasonably concluded this file was allowed to. Three transports were deleted
        here; leaving the import behind would preserve the invitation.
        """
        self.assertNotIn("requests", probe.imported_names(self.tree))

    def test_no_vendor_endpoint_survives_as_a_string_it_could_request(self):
        endpoints = ("api.openai.com", "api.anthropic.com", "api.deepseek.com",
                     "api.groq.com", "generativelanguage.googleapis.com",
                     "/chat/completions", "/v1/messages", ":generateContent")
        offenders = [f"{endpoint} in {literal[:60]!r}"
                     for literal in self.literals
                     for endpoint in endpoints if endpoint in literal]
        self.assertEqual(offenders, [], f"vendor endpoint still requestable: {offenders}")

    def test_no_provider_credential_is_read_here(self):
        """Asserted as "reads no credential", not as "does not read these six names".

        The deleted table looked keys up through nine environment variables across five
        providers, including two spellings of the Gemini one. Enumerating them in a test
        would document the old table rather than forbid a new one.
        """
        read = probe.environment_reads(self.tree)
        credentials = [name for name in read
                       if "API" in name.upper() or "KEY" in name.upper() or "TOKEN" in name.upper()]
        self.assertEqual(credentials, [], f"provider credential read here: {credentials}")

    def test_the_model_for_every_provider_comes_from_one_table(self):
        """§25-27. The module names no model, so it cannot disagree about one.

        This is the assertion that would have caught the live bug. The deleted table said
        `claude-3-5-haiku-latest` and `gemini-1.5-flash`; both had been retired upstream
        and 404'd on every call, while `undx_router.PROVIDERS` already held working
        replacements. A test that pinned those two strings as forbidden would have needed
        editing the next time a model was retired. "Names none" needs editing never.
        """
        model_shaped = [literal for literal in self.literals
                        if any(vendor in literal.lower() for vendor in
                               ("gpt-", "claude-", "gemini-", "deepseek-", "llama-",
                                "sonar", "muse-", "undx-core"))]
        self.assertEqual(model_shaped, [],
                         f"model default declared outside undx_router.PROVIDERS: {model_shaped}")
        env_reads = [name for name in probe.environment_reads(self.tree)
                     if "MODEL" in name.upper()]
        self.assertEqual(env_reads, [], f"model env read here: {env_reads}")

    def test_there_is_no_second_provider_table(self):
        """A table is a module-level collection keyed or ordered by provider name.

        Checked by looking for provider names among the module's literals rather than for
        an assignment called `PROVIDERS`, because the thing that matters is whether this
        file has an opinion about which providers exist — under any variable name.

        `_task_preference` is the deliberate exception and the reason this test reads the
        preference lists out of the live function instead of the source: those names are
        an *ordering hint*, they are filtered against `undx_router.PROVIDERS` before use,
        and a name that table does not know is dropped. An ordering over a set somebody
        else owns is not a second answer to which providers exist.

        Asked two ways, because each alone has a hole the other covers. The literal scan
        catches a name the hints do not contain — `meta`, `perplexity` — but is blind to a
        table built from the five names the hints *do* contain, since it cannot tell which
        function a literal came from. The structural scan catches that: it looks for a
        module-level collection of provider names, which is what a table is and what
        `_task_preference`'s function-local lists are not. The mutation harness found this
        hole by rebuilding `PROVIDER_ORDER = ["openai", "claude", "gemini"]` at module
        scope and surviving.
        """
        known = set(undx_router.PROVIDERS)
        hints = set()
        for task in ("cyber", "technical", "web", "fast", "general"):
            hints.update(provider_router._task_preference(task))
        self.assertTrue(hints, "the task preference lists are empty; this check is vacuous")
        self.assertLessEqual(hints, known,
                             "a task preference names a provider undx_router does not have")
        stray = sorted({literal for literal in self.literals if literal in known} - hints)
        self.assertEqual(stray, [],
                         f"provider named outside the ordering hints: {stray}")

        tables = []
        for node in self.tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            named = {element.value for element in ast.walk(node)
                     if isinstance(element, ast.Constant) and element.value in known}
            if len(named) > 1:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                label = ", ".join(t.id for t in targets if isinstance(t, ast.Name))
                tables.append(f"{label or '<expr>'} names {sorted(named)}")
        self.assertEqual(tables, [], f"module-level provider table rebuilt: {tables}")

    def test_the_retired_candidate_left_no_pointable_endpoint_behind(self):
        """§11-12. `UNDX_CANDIDATE` was the one chat endpoint an operator could aim.

        It composed its URL as `f"{base}/chat/completions"` from
        `UNDX_CANDIDATE_BASE_URL`, which means no URL-literal scanner could ever see
        where it pointed. That is exactly the shape §12 prohibits, and it is why the
        retirement is a removal rather than a migration.

        `UNDX_CANDIDATE_ENABLED` is still read on purpose — see `candidate_enabled` —
        so this checks for the base URL, which is the part that could carry traffic.
        """
        self.assertNotIn("UNDX_CANDIDATE_BASE_URL", probe.environment_reads(self.tree))
        self.assertNotIn("UNDX_CANDIDATE_API_KEY", probe.environment_reads(self.tree))

    def test_execution_leaves_through_exactly_one_seam(self):
        """One place to read, one place to intercept.

        Two seams would be two sets of arguments to keep in agreement, and the privacy
        class and call domain are among those arguments — so a second call site is a
        second chance to omit them. The regeneration path is the reason this is worth
        asserting: it is a *second* routed request, and it goes through the same helper.
        """
        routed = [node for node in ast.walk(self.tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "route_structured_request"]
        self.assertEqual(len(routed), 1,
                         f"{len(routed)} direct calls to the router; expected one seam")
        seam = probe.function(self.tree, "_route")
        self.assertEqual(len([node for node in ast.walk(seam)
                              if isinstance(node, ast.Call)
                              and isinstance(node.func, ast.Attribute)
                              and node.func.attr == "route_structured_request"]), 1,
                         "the one routed call is not the one inside `_route`")


# --------------------------------------------------------------------- §4 and §5


class EveryRoutedCallDeclaresItselfTest(unittest.TestCase):
    """§4 and §5: a privacy class and a call domain on every routed request."""

    def _sent(self, *, task="general", call_domain=None, envelope=None, messages=None):
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            captured["positional"] = args
            return envelope if envelope is not None else _envelope("I'm UNDX.")

        with mock.patch.object(undx_router, "route_structured_request", side_effect=capture):
            result = provider_router.generate_response(
                messages if messages is not None else _grounded(),
                correlation_id="t", task=task, user_id=77, call_domain=call_domain)
        return captured, result

    def test_the_privacy_class_is_declared_and_is_confidential(self):
        """§4 forbids lowering it to make routing possible.

        An in-app UNDX turn carries what the person typed plus their retrieved memory
        and knowledge items. CONFIDENTIAL is the floor. If a ceiling excludes every
        provider the right outcome is the refusal envelope, which is asserted below —
        not PUBLIC.
        """
        sent, _ = self._sent()
        self.assertEqual(sent.get("privacy_class"), undx_privacy.SENSITIVITY_CONFIDENTIAL)

    def test_the_privacy_class_is_a_constant_not_a_string_literal(self):
        """§35's typo family: a misspelt class name is a *value*, not an error.

        `UNDX_SHADOW_MAX_PRIVACY_CLASS=PUBIC` once cleared CONFIDENTIAL content for
        routing, because an unrecognised name ranks as SECRET, which is right for a
        request and inverted for a ceiling. A literal spelt correctly today passes every
        behavioural assertion in this file, so the constant is pinned structurally.
        """
        tree = probe.parse(pathlib.Path(provider_router.__file__))
        declared = [node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "MESSENGER_PRIVACY_CLASS"
                            for t in node.targets)]
        self.assertEqual(len(declared), 1, "MESSENGER_PRIVACY_CLASS is not declared once")
        self.assertIsInstance(declared[0], ast.Attribute,
                              "the privacy class is a bare literal, not the module constant")
        self.assertEqual(declared[0].attr, "SENSITIVITY_CONFIDENTIAL")

    def test_the_call_domain_is_declared_and_is_messaging(self):
        sent, _ = self._sent()
        self.assertEqual(sent.get("call_domain"), undx_call_domain.CALL_DOMAIN_MESSAGING)
        self.assertTrue(undx_call_domain.is_known(sent.get("call_domain")),
                        "the declared domain is not one the taxonomy recognises")

    def test_a_caller_may_override_the_domain_but_not_the_privacy_class(self):
        """The asymmetry is §5: a domain reorders, a privacy class permits.

        `generate_response` has one production caller today, so MESSAGING is a fact
        rather than a guess — but a second surface must be able to say it is something
        else. What it must never be able to do is say the content is less sensitive.
        """
        sent, _ = self._sent(call_domain=undx_call_domain.CALL_DOMAIN_PRIVATE_OFFICE)
        self.assertEqual(sent.get("call_domain"), undx_call_domain.CALL_DOMAIN_PRIVATE_OFFICE)
        self.assertEqual(sent.get("privacy_class"), undx_privacy.SENSITIVITY_CONFIDENTIAL)
        self.assertNotIn("privacy_class", provider_router.generate_response.__code__.co_varnames,
                         "privacy_class is a parameter; a caller can lower the ceiling")

    def test_the_user_id_reaches_the_router_so_the_spend_has_an_owner(self):
        sent, _ = self._sent()
        self.assertEqual(sent["positional"][0], 77,
                         "the turn's cost would be attributed to nobody")

    def test_the_regeneration_request_declares_the_same_things(self):
        """The second call is a routed call, so §4 and §5 apply to it identically.

        A retry that forgot its privacy class would be the easiest gap in the file to
        miss: it only runs when a model has already broken identity, which is rare, and
        it returns the answer the user actually sees.
        """
        calls = []
        replies = iter(["My name is Pulse AI.", "I'm UNDX, PulseSOC's intelligence companion."])

        def capture(*args, **kwargs):
            calls.append((args, kwargs))
            return _envelope(next(replies))

        with mock.patch.object(undx_router, "route_structured_request", side_effect=capture):
            result = provider_router.generate_response(_grounded(), correlation_id="t", user_id=9)
        self.assertTrue(result["identity_regenerated"], "no regeneration happened")
        self.assertEqual(len(calls), 2, "regeneration did not issue a second routed request")
        for index, (args, kwargs) in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(kwargs.get("privacy_class"), undx_privacy.SENSITIVITY_CONFIDENTIAL)
                self.assertEqual(kwargs.get("call_domain"), undx_call_domain.CALL_DOMAIN_MESSAGING)
                self.assertEqual(args[0], 9)


class TaskIsAHintNotAPermissionTest(unittest.TestCase):
    """`task` reorders providers. It does not describe provenance or sensitivity.

    Phase 4's lesson, applied one module over. `task` is inferred upstream from a safety
    classifier and a topic guess — "this turn is about security" — and inference is not
    declaration. Letting it set `call_domain` would make a guess about content look like
    a fact about origin; letting it near `privacy_class` would let "this question is
    about security" become "therefore it may go wherever security questions go".
    """

    TASKS = ("cybersecurity", "technical", "web_search", "fast", "pulse_ai_messenger", "")

    def _sent(self, task):
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            captured["positional"] = args
            return _envelope("I'm UNDX.")

        with mock.patch.object(undx_router, "route_structured_request", side_effect=capture):
            provider_router.generate_response(_grounded(), correlation_id="t", task=task, user_id=1)
        return captured

    def test_every_task_reaches_providers_and_not_the_domain(self):
        for task in self.TASKS:
            with self.subTest(task=task):
                sent = self._sent(task)
                self.assertEqual(sent.get("call_domain"), undx_call_domain.CALL_DOMAIN_MESSAGING,
                                 f"task {task!r} leaked into the call domain")
                self.assertEqual(sent.get("privacy_class"), undx_privacy.SENSITIVITY_CONFIDENTIAL,
                                 f"task {task!r} changed the privacy class")

    def test_a_recognised_task_actually_reorders_the_chain(self):
        """Otherwise the hint is dropped and only the *absence* above is tested.

        Needs a configured provider to have anything to order, so the two the security
        lane names are given keys. Without this, `providers=None` for every task would
        satisfy the test above perfectly while silently discarding the preference.
        """
        with mock.patch.dict(os.environ, {"CLAUDE_AI_API": "k1", "OPENAI_API_KEY": "k2"}, clear=False):
            sent = self._sent("cybersecurity")
            self.assertEqual(sent.get("providers"), ["claude", "openai"],
                             "the security lane's ordering hint never reached the router")

    def test_an_unrecognised_task_expresses_no_preference(self):
        """`None`, not a partial list — so `undx_router` classifies the text itself.

        Its lane priorities are maintained against live provider behaviour; a stale
        guess from this module would override them with worse information.
        """
        with mock.patch.dict(os.environ, {}, clear=True):
            sent = self._sent("pulse_ai_messenger")
            self.assertIsNone(sent.get("providers"))

    def test_an_operator_order_wins_over_the_task_preference(self):
        """`PULSE_AI_PROVIDER_ORDER` is an explicit instruction; a task is a guess.

        Kept because "stop using Groq" during an incident should not require a deploy.
        """
        with mock.patch.dict(os.environ, {"PULSE_AI_PROVIDER_ORDER": "gemini,claude",
                                          "CLAUDE_AI_API": "k1", "Gemini_AI_API": "k2"}, clear=False):
            self.assertEqual(provider_router.configured_providers_for_task("cybersecurity"),
                             ["gemini", "claude"])

    def test_a_typo_in_the_operator_order_costs_only_that_name(self):
        """A misspelt provider name must cost the hint it names and nothing else.

        Two things go wrong without the filter, and the second is worse than the reason
        the filter was written. `route_structured_request` filters `providers` against its
        own table and falls back to classification if nothing survives, so one bad entry
        would silently discard the good ones alongside it — that was the expected cost.
        But `undx_router._api_key` raises `KeyError` on a name `PROVIDERS` does not have,
        and this function calls it on every surviving name. An unfiltered typo therefore
        throws out of the messenger turn: one character in an environment variable an
        operator edits during an incident, and UNDX stops answering entirely.
        """
        with mock.patch.dict(os.environ, {"PULSE_AI_PROVIDER_ORDER": "cluade,gemini",
                                          "CLAUDE_AI_API": "k1", "Gemini_AI_API": "k2"}, clear=False):
            self.assertEqual(provider_router.configured_providers_for_task("general"),
                             ["gemini", "claude"])


# --------------------------------------------------------------------- grounding


class GroundingSurvivedTest(unittest.TestCase):
    """Four system blocks, verified present, and they lead the prompt that is sent.

    This is what the module is *for* now, and the thing a consolidation could most
    easily have swallowed on the grounds that it looked like prompt plumbing.
    """

    def _system_prompt(self, messages=None):
        captured = {}

        def capture(user_id, system_prompt, user_content, **kwargs):
            captured.update(system_prompt=system_prompt, user_content=user_content,
                            history=kwargs.get("history"))
            return _envelope("I'm UNDX, PulseSOC's intelligence companion.")

        with mock.patch.object(undx_router, "route_structured_request", side_effect=capture):
            provider_router.generate_response(messages if messages is not None else _grounded(),
                                              correlation_id="t", user_id=5)
        return captured

    def test_the_identity_block_leads_the_prompt_the_provider_receives(self):
        """Checked as a prefix, which is what "highest-level system message" meant.

        The pre-reconciliation audit asserted `payload[0]["content"] == IDENTITY_BLOCK`
        against a message list. One joined system prompt cannot be checked that way, and
        "contains" would be satisfied by the block appearing last, after anything a
        knowledge item or an addendum had to say.
        """
        sent = self._system_prompt()
        self.assertTrue(sent["system_prompt"].startswith(provider_router.UNDX_IDENTITY_BLOCK),
                        "the identity block is not first in the routed system prompt")

    def test_all_four_grounding_blocks_reach_the_provider(self):
        from services import undx_company_identity, undx_fact_policy
        sent = self._system_prompt()
        required = {
            "identity": provider_router.UNDX_IDENTITY_REQUIRED_PHRASE,
            "company": undx_company_identity.COMPANY_IDENTITY_REQUIRED_PHRASE,
            "capability": "UNDX capability state",
            "fact policy": undx_fact_policy.FACT_POLICY_REQUIRED_PHRASE,
        }
        for label, phrase in required.items():
            with self.subTest(block=label):
                self.assertIn(phrase, sent["system_prompt"])

    def test_a_missing_grounding_block_fails_closed_without_calling_a_provider(self):
        """Fail closed, and fail *before* spending money.

        An ungrounded UNDX answers capability questions by fabricating availability,
        which is worse than not answering — and paying a provider to produce it is worse
        again.
        """
        called = []
        with mock.patch.object(provider_router, "UNDX_IDENTITY_BLOCK", "identity missing"), \
                mock.patch.object(undx_router, "route_structured_request",
                                  side_effect=lambda *a, **k: called.append(1)):
            result = provider_router.generate_response(_grounded(), correlation_id="t")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "identity_configuration_error")
        self.assertEqual(called, [], "a provider was called with an ungrounded request")

    def test_a_caller_inserted_system_message_survives_the_split(self):
        """`pulse_ai_service` inserts a fifth system block at index 1.

        Either a cyber-safety addendum or a "live search was unavailable, say so" notice.
        Taking only the first system message would drop it; taking only the last would
        drop the identity grounding. Both are the kind of bug that produces a confident,
        well-formed, wrong answer.
        """
        sent = self._system_prompt([
            {"role": "system", "content": "SAFETY-ADDENDUM-SENTINEL"},
            {"role": "user", "content": "is this a scam"},
        ])
        self.assertIn("SAFETY-ADDENDUM-SENTINEL", sent["system_prompt"])
        self.assertTrue(sent["system_prompt"].startswith(provider_router.UNDX_IDENTITY_BLOCK))

    def test_the_conversation_survives_as_history_and_the_last_turn_is_the_question(self):
        """The capability that was the whole reason a second router existed.

        `route_structured_request` hardcoded `history = []` until this phase, so a caller
        holding a multi-turn conversation could not express it and grew its own transport
        instead. If the split dropped history the migration would look complete and UNDX
        would have quietly lost its memory of the current conversation.
        """
        sent = self._system_prompt([
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ])
        self.assertEqual(sent["user_content"], "follow up")
        self.assertEqual(sent["history"], [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ])

    def test_a_conversation_not_ending_on_the_user_is_refused_not_repaired(self):
        """Reordering it would answer a different conversation. Silently.

        And inventing an empty final user turn would ask a model to speak unprompted,
        which is how a plausible non-answer gets stored as the assistant's reply.
        """
        called = []
        with mock.patch.object(undx_router, "route_structured_request",
                               side_effect=lambda *a, **k: called.append(1)):
            result = provider_router.generate_response(
                [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                correlation_id="t")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "final_user_turn_required")
        self.assertEqual(called, [])

    def test_the_messenger_voice_is_preserved_not_inherited(self):
        """§3. 0.35/850 came from the deleted transports; the router defaults to 0.0/320.

        Inheriting the router's structured-output defaults would have turned every UNDX
        conversation into a terse machine answer — a behavioural regression with no error,
        no failing test and no log line.
        """
        captured = {}
        with mock.patch.object(undx_router, "route_structured_request",
                               side_effect=lambda *a, **k: (captured.update(k), _envelope("I'm UNDX."))[1]):
            provider_router.generate_response(_grounded(), correlation_id="t", user_id=1)
        self.assertEqual(captured.get("temperature"), 0.35)
        self.assertEqual(captured.get("max_tokens"), 850)

    def test_the_configured_timeout_still_reaches_the_router(self):
        """`PULSE_AI_PROVIDER_TIMEOUT_SECONDS` is an operator's dial and still works."""
        captured = {}
        with mock.patch.dict(os.environ, {"PULSE_AI_PROVIDER_TIMEOUT_SECONDS": "31"}, clear=False), \
                mock.patch.object(undx_router, "route_structured_request",
                                  side_effect=lambda *a, **k: (captured.update(k), _envelope("I'm UNDX."))[1]):
            provider_router.generate_response(_grounded(), correlation_id="t", user_id=1)
        self.assertEqual(captured.get("timeout"), 31)


# --------------------------------------------------------------------- verification


class IdentityVerificationSurvivedTest(unittest.TestCase):
    """The output-side check, one regeneration, then the safe canonical reply."""

    def _route(self, replies, provider="claude"):
        it = iter(replies)
        calls = []

        def capture(user_id, system_prompt, user_content, **kwargs):
            calls.append({"system_prompt": system_prompt, **kwargs})
            return _envelope(next(it), provider)

        with mock.patch.object(undx_router, "route_structured_request", side_effect=capture):
            return provider_router.generate_response(_grounded(), correlation_id="t", user_id=1), calls

    def test_a_clean_answer_passes_through_untouched(self):
        result, calls = self._route(["I'm UNDX, PulseSOC's intelligence companion."])
        self.assertTrue(result["ok"])
        self.assertFalse(result["identity_regenerated"])
        self.assertEqual(len(calls), 1, "a clean answer paid for a second request")

    def test_a_broken_identity_is_regenerated_on_the_provider_that_broke_it(self):
        """The question is whether *this* model holds the line when told directly.

        Handing the retry to a different provider answers a different question, and then
        reports the second provider's success as the first one's correction — which is
        how a model that never holds identity keeps looking like one that does.
        """
        result, calls = self._route(["My name is Pulse AI.",
                                     "I'm UNDX, PulseSOC's intelligence companion."])
        self.assertTrue(result["identity_regenerated"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].get("providers"), ["claude"],
                         "the correction was not sent back to the provider that failed")
        self.assertIn("Regenerate the answer", calls[1]["system_prompt"])
        self.assertTrue(calls[1]["system_prompt"].startswith(provider_router.UNDX_IDENTITY_BLOCK),
                        "the correction displaced the identity block instead of following it")

    def test_a_second_violation_returns_the_safe_reply_and_never_the_violation(self):
        result, _ = self._route(["My name is Pulse AI.", "I am ChatGPT."])
        self.assertEqual(result["reply"], provider_router.UNDX_IDENTITY_SAFE_REPLY)
        self.assertNotIn("ChatGPT", result["reply"])
        self.assertNotIn("Pulse AI", result["reply"])

    def test_the_validator_still_catches_every_family_it_used_to(self):
        for unsafe in ("My name is Pulse AI.", "I am ChatGPT.", "I am not UNDX.",
                       "I don't know UNDX.", "My name is Orion.", "I am human.",
                       "I'm conscious."):
            with self.subTest(reply=unsafe):
                self.assertTrue(provider_router.undx_identity_violation(unsafe))

    def test_a_valid_reply_is_not_flagged(self):
        """Otherwise every assertion above would pass with a validator that says yes."""
        self.assertEqual(
            provider_router.undx_identity_violation(provider_router.UNDX_IDENTITY_SAFE_REPLY), "")


# --------------------------------------------------------------------- degradation


class RefusalIsNotAnOutageTest(unittest.TestCase):
    """The router distinguishes four ways a chain can end. So does the envelope.

    Collapsing them is not cosmetic: a privacy ceiling that excluded every provider needs
    a classification decision and a spend limit needs a budget decision, and both paged
    out as "all providers failed" means the third kind — an actual outage — eventually
    gets ignored as one of the first two.
    """

    def _failure(self, *statuses, error="no configured provider answered"):
        with mock.patch.object(undx_router, "route_structured_request",
                               return_value=_refusal(*statuses, error=error)):
            return provider_router.generate_response(_grounded(), correlation_id="t", user_id=1)

    def test_nothing_configured_is_an_operator_problem(self):
        result = self._failure("not_configured", "not_configured")
        self.assertEqual(result["reason"], "provider_config_missing")

    def test_a_privacy_refusal_is_the_system_working(self):
        result = self._failure("privacy_refused", "privacy_refused")
        self.assertEqual(result["reason"], "privacy_ceiling_refused")

    def test_a_budget_stop_is_a_spend_decision(self):
        result = self._failure("budget_exceeded", "budget_exceeded")
        self.assertEqual(result["reason"], "budget_exhausted")

    def test_a_mixed_or_failing_chain_is_an_outage(self):
        for statuses in (("timeout", "request_failed"),
                         ("privacy_refused", "timeout"),
                         ("circuit_open", "response_failed")):
            with self.subTest(statuses=statuses):
                self.assertEqual(self._failure(*statuses)["reason"], "all_providers_failed")

    def test_no_failure_path_returns_a_provider_error_to_the_user(self):
        """The curated message, never the router's `error` string.

        `_exhausted_reason` names privacy classes and spend limits. Useful in a log,
        nobody's business in a chat bubble.
        """
        result = self._failure("timeout", error="no provider may receive CONFIDENTIAL content")
        self.assertEqual(result["message"], provider_router.UNAVAILABLE_MESSAGE)
        self.assertNotIn("CONFIDENTIAL", result["message"])
        self.assertNotIn("reply", result, "a failure envelope must not carry a reply")


class TheEventsLedgerStillGetsTheTruthTest(unittest.TestCase):
    """§41. `_record_provider_events` reads keys the router does not emit.

    A straight pass-through would write every attempt as `status='failed'` with a blank
    reason — including the successful one, because `ok` would simply be absent. The whole
    provider-events table would go quietly wrong while every test about routing passed.
    """

    def _attempts(self, envelope):
        with mock.patch.object(undx_router, "route_structured_request", return_value=envelope):
            return provider_router.generate_response(
                _grounded(), correlation_id="t", user_id=1)["attempts"]

    def test_the_successful_attempt_is_recorded_as_successful(self):
        attempts = self._attempts(_envelope("I'm UNDX."))
        self.assertEqual([item["ok"] for item in attempts], [True])
        self.assertEqual([item["provider"] for item in attempts], ["claude"])

    def test_a_provider_that_was_tried_and_failed_still_appears(self):
        """§41: fallback succeeding does not erase the failure that preceded it."""
        envelope = _envelope("I'm UNDX.", "claude")
        envelope["attempts"] = [{"provider": "OpenAI", "status": "timeout"},
                                {"provider": "Claude", "status": "success"}]
        attempts = self._attempts(envelope)
        self.assertEqual([(item["provider"], item["ok"], item["reason"]) for item in attempts],
                         [("openai", False, "timeout"), ("claude", True, "")])

    def test_the_ledger_stores_one_spelling_per_provider(self):
        """The admin dashboard does `GROUP BY provider`. Two spellings, two rows.

        The router reports display labels and the ledger has always stored lowercase
        keys, and "Meta Muse" does not lowercase into "meta" — which is why this is a
        lookup against `undx_router.PROVIDERS` rather than a `.lower()` call.
        """
        envelope = _envelope("I'm UNDX.", "meta")
        envelope["attempts"] = [{"provider": "Meta Muse", "status": "success"}]
        self.assertEqual([item["provider"] for item in self._attempts(envelope)], ["meta"])
        self.assertEqual(provider_router._provider_key("Meta Muse"), "meta")

    def test_the_three_statuses_this_ledger_could_not_previously_express(self):
        """Privacy, budget and breaker outcomes were unrepresentable here before.

        Not because they were dropped — because this module had no privacy ceiling, no
        budget and no breaker to produce them. They are new information in an existing
        table, which is the observable proof the consolidation added controls rather than
        just moving code.
        """
        with mock.patch.object(undx_router, "route_structured_request",
                               return_value=_refusal("privacy_refused", "budget_exceeded", "circuit_open")):
            attempts = provider_router.generate_response(
                _grounded(), correlation_id="t", user_id=1)["attempts"]
        self.assertEqual([item["reason"] for item in attempts],
                         ["privacy_refused", "budget_exceeded", "circuit_open"])
        self.assertTrue(all(item["ok"] is False for item in attempts))

    def test_latency_is_attributed_to_the_attempt_that_finished_the_chain(self):
        """Not to every attempt. The router times the chain, not each hop.

        Copying the total onto all of them would be a plausible-looking lie in a column
        somebody will eventually average.
        """
        envelope = _envelope("I'm UNDX.", "claude")
        envelope["attempts"] = [{"provider": "OpenAI", "status": "timeout"},
                                {"provider": "Claude", "status": "success"}]
        envelope["latency_ms"] = 900
        attempts = self._attempts(envelope)
        self.assertEqual([item["latency_ms"] for item in attempts], [0, 900])

    def test_the_regeneration_attempts_are_recorded_too(self):
        """A turn that cost two provider calls must not be billed as one.

        This is the path where the cost is highest and the visibility was lowest: the
        old code recorded only the attempt that returned.
        """
        replies = iter(["My name is Pulse AI.", "I'm UNDX, PulseSOC's intelligence companion."])
        with mock.patch.object(undx_router, "route_structured_request",
                               side_effect=lambda *a, **k: _envelope(next(replies))):
            attempts = provider_router.generate_response(
                _grounded(), correlation_id="t", user_id=1)["attempts"]
        self.assertEqual(len(attempts), 2, "the regeneration request was not recorded")


class AttributionSurvivesTheBoundaryTest(unittest.TestCase):
    """Who answered is observed, never assumed.

    The failure this repo has actually had: `route_undx_request`'s failure envelope
    hardcodes `provider: "openai"` and `source: "OpenAI"` regardless of who failed. One
    module over from here.
    """

    def test_the_answering_provider_and_model_are_reported_faithfully(self):
        for provider in ("claude", "gemini", "deepseek", "meta"):
            with self.subTest(provider=provider):
                with mock.patch.object(undx_router, "route_structured_request",
                                       return_value=_envelope("I'm UNDX.", provider)):
                    result = provider_router.generate_response(
                        _grounded(), correlation_id="t", user_id=1)
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["model"], f"{provider}-test-model")

    def test_the_model_is_not_looked_up_locally(self):
        """It comes from the envelope, so a router-side model change needs no edit here.

        Asserted with a model string the local code could not possibly produce.
        """
        with mock.patch.object(undx_router, "route_structured_request",
                               return_value=_envelope("I'm UNDX.", "claude", model="claude-sonnet-9-imaginary")):
            result = provider_router.generate_response(_grounded(), correlation_id="t", user_id=1)
        self.assertEqual(result["model"], "claude-sonnet-9-imaginary")


# --------------------------------------------------------------------- status


class OneAuthorityForConfigurationTest(unittest.TestCase):
    """The status endpoint reports the table the request path actually uses.

    A status endpoint reading from a table the requests no longer consult is worse than
    no status endpoint, because it looks authoritative. That is precisely how both Claude
    and Gemini were reported here for weeks while 404ing on every call.
    """

    def test_the_envelope_shape_the_status_api_consumes_is_unchanged(self):
        status = provider_router.provider_status()
        self.assertEqual(set(status), {"ok", "providers", "candidate",
                                       "configured_count", "fallback_order"})
        for item in status["providers"]:
            with self.subTest(provider=item.get("provider")):
                self.assertEqual(set(item), {"provider", "configured", "model"})

    def test_every_provider_and_model_comes_from_the_router(self):
        status = provider_router.provider_status()
        self.assertEqual([item["provider"] for item in status["providers"]],
                         list(undx_router.PROVIDERS))
        for item in status["providers"]:
            with self.subTest(provider=item["provider"]):
                self.assertEqual(item["model"], undx_router._model(item["provider"]))

    def test_the_retired_candidate_is_reported_retired_rather_than_vanishing(self):
        """§3: capability may be removed, not dropped silently.

        Somebody who set `UNDX_CANDIDATE_ENABLED=true` made a deliberate choice.
        Discovering it stopped working from a silence is worse than from a status field.
        """
        with mock.patch.dict(os.environ, {"UNDX_CANDIDATE_ENABLED": "true"}, clear=False):
            candidate = provider_router.provider_status()["candidate"]
        self.assertTrue(candidate["retired"])
        self.assertTrue(candidate["enabled"], "an operator's flag is not being read back")
        self.assertFalse(candidate["configured"], "the retired candidate looks usable")

    def test_configured_means_a_usable_key_and_not_that_it_works(self):
        """`provider_configuration` was renamed from `provider_health` for this reason.

        A newline in a credential makes `undx_router._api_key` refuse to send it, so the
        provider is unconfigured here — which is the honest answer, because the
        alternative is an HTTP layer raising an exception that quotes the secret.
        """
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-good"}, clear=False):
            by_name = {item["provider"]: item for item in provider_router.provider_status()["providers"]}
            self.assertTrue(by_name["openai"]["configured"])
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-bad\nsecond-line"}, clear=False):
            by_name = {item["provider"]: item for item in provider_router.provider_status()["providers"]}
            self.assertFalse(by_name["openai"]["configured"],
                             "a credential the router refuses to send is reported configured")

    def test_a_provider_killed_by_its_switch_is_not_reported_configured(self):
        """A key is not consent. `UNDX_CLAUDE_ENABLED=false` takes Claude out of
        rotation without deleting the credential, and the status must agree with the
        request path about that or the switch looks like a failure."""
        with mock.patch.dict(os.environ, {"CLAUDE_AI_API": "k", "UNDX_CLAUDE_ENABLED": "false"},
                             clear=False):
            by_name = {item["provider"]: item for item in provider_router.provider_status()["providers"]}
            self.assertFalse(by_name["claude"]["configured"])
            self.assertNotIn("claude", provider_router.configured_providers_for_task("general"))


# --------------------------------------------------------------------- the other path


class TheTaskPathWasMigratedTooTest(unittest.TestCase):
    """`generate_task_response` has no production callers, and that is why.

    An unrouted call site nobody exercises is exactly the one that survives a migration.
    Then somebody needs a non-assistant model call, finds a helper that looks ready, and
    it becomes the next entry in the census.
    """

    def _sent(self, messages):
        captured = {}

        def capture(user_id, system_prompt, user_content, **kwargs):
            captured.update(user_id=user_id, system_prompt=system_prompt,
                            user_content=user_content, **kwargs)
            return _envelope("translated text")

        with mock.patch.object(undx_router, "route_structured_request", side_effect=capture):
            result = provider_router.generate_task_response(messages, correlation_id="t", user_id=4)
        return captured, result

    def test_it_routes_and_declares_a_privacy_class(self):
        sent, result = self._sent([{"role": "system", "content": "Translate."},
                                   {"role": "user", "content": "hola"}])
        self.assertTrue(result["ok"])
        self.assertEqual(sent.get("privacy_class"), undx_privacy.SENSITIVITY_CONFIDENTIAL)
        self.assertEqual(sent.get("call_domain"), undx_call_domain.CALL_DOMAIN_MESSAGING)

    def test_it_still_does_not_inject_the_assistant_identity(self):
        """Infrastructure work is not an assistant conversation.

        Translating a marketplace listing must not return "I'm UNDX" wrapped around it,
        and must not be grounded with UNDX's identity at all — the model would be
        answering as a character the output has no room for.
        """
        sent, _ = self._sent([{"role": "system", "content": "Translate."},
                              {"role": "user", "content": "hola"}])
        self.assertNotIn(provider_router.UNDX_IDENTITY_REQUIRED_PHRASE, sent["system_prompt"])
        self.assertEqual(sent["system_prompt"], "Translate.")

    def test_a_request_without_a_system_instruction_is_still_refused(self):
        """Preserved verbatim: unbounded task text with no instruction is not a task."""
        called = []
        with mock.patch.object(undx_router, "route_structured_request",
                               side_effect=lambda *a, **k: called.append(1)):
            result = provider_router.generate_task_response(
                [{"role": "user", "content": "hola"}], correlation_id="t")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "system_instruction_required")
        self.assertEqual(called, [])

    def test_its_failure_message_is_the_callers_and_not_the_assistants(self):
        """It takes `unavailable_message` because a translation failure is not UNDX
        being unavailable, and telling a user it is would name the wrong subsystem."""
        with mock.patch.object(undx_router, "route_structured_request",
                               return_value=_refusal("timeout")):
            result = provider_router.generate_task_response(
                [{"role": "system", "content": "Translate."}, {"role": "user", "content": "hola"}],
                correlation_id="t", unavailable_message="Translation is unavailable.")
        self.assertEqual(result["message"], "Translation is unavailable.")
        self.assertNotEqual(result["message"], provider_router.UNAVAILABLE_MESSAGE)


class TheRouterCanActuallyCarryAConversationTest(unittest.TestCase):
    """The one assertion in this file that reaches past the seam into `undx_router`.

    Every other behavioural test here mocks `route_structured_request`, which is correct
    — they are about what this module sends. But that mock makes them all pass against a
    router that accepts `history` and throws it away, and "accepts and discards" is the
    precise shape of the bug this phase existed to fix. `history` was a hardcoded empty
    list; a caller holding a multi-turn conversation could not express it, so it grew its
    own five-provider table rather than lose the turns.

    So this drives a real `route_structured_request` through the *real* OpenAI adapter
    down to a mocked transport, and asks what the provider was actually sent. The first
    draft patched `CALLERS` with a fake adapter instead, and two of these three tests
    failed against correct code: `clean_history` does not run in
    `route_structured_request`, it runs inside each adapter, because the normalisation is
    dialect-specific — Gemini has to rename `assistant` to `model` and the others do not.
    A fake adapter therefore skips the cap it was being asked about. Testing one layer
    above the thing you care about reports the seam's shape as the product's behaviour.
    """

    def _sent_messages(self, history):
        seen = {}

        class Response:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "answered"}}],
                        "model": "fake-model", "usage": {}}

        def post(url, **kwargs):
            seen["messages"] = (kwargs.get("json") or {}).get("messages")
            return Response()

        with mock.patch.object(undx_router.requests, "post", side_effect=post), \
                mock.patch.object(undx_router, "_api_key", lambda name: "key"), \
                mock.patch.object(undx_router, "provider_enabled", lambda name: True), \
                mock.patch.object(undx_router, "_privacy_refusal", lambda *a, **k: ""), \
                mock.patch.object(undx_router, "_budget_refusal", lambda *a, **k: ""), \
                mock.patch.object(undx_router, "_breaker_should_skip", lambda *a, **k: False), \
                mock.patch.object(undx_router, "_record_usage", lambda *a, **k: None), \
                mock.patch.object(undx_router, "_record_provider_success", lambda *a, **k: None), \
                mock.patch.object(undx_router, "_record_provider_failure", lambda *a, **k: None):
            # `_record_provider_failure` is patched only so that a failure *inside this
            # test* cannot open the real OpenAI circuit breaker and change the result of
            # whatever runs next. The first draft did not, and a missing
            # `raise_for_status` on the fake response tripped the breaker three times
            # before the assertion was even reached.
            envelope = undx_router.route_structured_request(
                7, "system", "final question", history=history, providers=["openai"])
        self.assertTrue(envelope["ok"], envelope)
        return seen.get("messages") or []

    def test_the_turns_reach_the_provider(self):
        sent = self._sent_messages([
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ])
        self.assertEqual(
            [(item["role"], item["content"]) for item in sent[1:]],
            [("user", "first question"), ("assistant", "first answer"), ("user", "final question")],
            "the router accepts history and discards it, which is the bug it had")

    def test_a_caller_that_sends_no_history_is_byte_identical_to_before(self):
        """The migration's safety property: every pre-existing call is unchanged.

        `None` has to produce exactly what the hardcoded empty list produced, or this
        became a widening of behaviour for every existing caller rather than a new
        capability for one.
        """
        sent = self._sent_messages(None)
        self.assertEqual([item["role"] for item in sent], ["system", "user"])
        self.assertEqual(sent[-1]["content"], "final question")

    def test_the_history_cap_still_applies_so_this_is_not_a_prompt_smuggling_route(self):
        """A new parameter on a routed request is a new way to enlarge a prompt.

        `clean_history` caps at ten turns. Without that, a caller could put an unbounded
        conversation through a path whose `max_tokens` budget is enforced on the *output*.
        """
        sent = self._sent_messages(
            [{"role": "user", "content": f"turn {index}"} for index in range(40)])
        turns = [item["content"] for item in sent[1:-1]]
        self.assertEqual(len(turns), 10, "history is forwarded uncapped")
        self.assertEqual(turns[-1], "turn 39")

    def test_every_adapter_forwards_history_rather_than_only_the_one_tested_above(self):
        """Seven providers, one of them exercised behaviourally. This covers the other six.

        Standing up seven transport shapes to prove one parameter is forwarded would cost
        more than it is worth, but "the provider I happened to test keeps the
        conversation" is not the property the messenger needs — the whole point of a
        router is that the caller does not choose. So each adapter is read instead, and
        asked whether the `history` parameter it accepts is passed on to anything at all.
        An adapter that takes it and drops it would lose the conversation only on failover,
        which is the least observable moment available.
        """
        tree = probe.parse(pathlib.Path(undx_router.__file__))
        for name, caller in sorted(undx_router.CALLERS.items()):
            with self.subTest(provider=name):
                node = probe.function(tree, caller.__name__)
                self.assertIsNotNone(node, f"{caller.__name__} is not defined")
                forwarded = any(
                    isinstance(arg, ast.Name) and arg.id == "history"
                    for call in ast.walk(node) if isinstance(call, ast.Call)
                    for arg in list(call.args) + [kw.value for kw in call.keywords])
                self.assertTrue(forwarded,
                                f"{caller.__name__} accepts history and never passes it on")


class TheProductionCallerPassesWhatItHasTest(unittest.TestCase):
    """`pulse_ai_service` has `user_id` in scope, so omitting it would be a choice.

    Read from source because the alternative is standing up a whole messenger turn with
    a database, a conversation row and a safety classifier to observe one keyword.
    """

    @classmethod
    def setUpClass(cls):
        from services import pulse_ai_service
        # Not `.resolve()` — see `NoSecondRouterTest.setUpClass`.
        cls.tree = probe.parse(pathlib.Path(pulse_ai_service.__file__))

    def test_the_messenger_turn_attributes_its_spend_to_the_person(self):
        calls = [node for node in ast.walk(self.tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "generate_response"]
        self.assertEqual(len(calls), 1, "the messenger has one provider call site")
        keywords = {kw.arg for kw in calls[0].keywords}
        self.assertIn("user_id", keywords,
                      "the busiest chat surface bills its spend to nobody")


if __name__ == "__main__":
    unittest.main()
