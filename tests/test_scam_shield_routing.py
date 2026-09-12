"""A security control routed through the router, with the deterministic part on top.

U3 of `UNDX_PROVIDER_CALLSITE_CENSUS.md`: `services/scam_shield.py` held an API key
lookup, a model default and a `requests.post` to `api.openai.com`, which made the one AI
call in this product that is *itself* a security control also the one outside the privacy
ceiling, the spend ledger, provider health and the circuit breaker.

Three things make this migration different from U1, U2 and U5-U7, and they are why this
file is shaped the way it is.

**The answer has to be an object, and asking nicely is not the same as requiring it.**
This module `json.loads` the reply. The old code asked OpenAI for JSON with
`response_format={"type": "json_object"}` — a real requirement, honoured by the one
provider it could reach. A router with seven providers can only keep that promise for the
four that accept such a parameter, so `require_json=True` is a *capability filter* the
router applies before choosing, not a preference it forwards and hopes about. Routed to a
provider with no JSON mode, the reply would be prose, `json.loads` would raise, and
`analyze_text` would report "AI review unavailable" — a scam check silently degrading to
local-rules-only on every single request, with a reassuring note attached. That is worse
than the outage it imitates, which is why `TheRequirementIsEnforcedNotRequestedTest`
drives the real router down to a mocked transport rather than asserting on a mock's kwargs.

**Everything deterministic stays above the model (§16).** Score, risk level, red flags,
domain findings and address findings are computed before the model is asked and are not
sent to it. What comes back may *add* a scam type, *add* safe actions, and supply the
human-readable explanation. It cannot lower a score, clear a red flag, or downgrade a
level. `DeterministicControlsStayOnTopTest` feeds a model reply that tries all three,
because "merge the AI's fields into the result" reads tidier than the current code and
would hand a stranger's pasted text a vote on its own risk rating.

**The payload does not get to say who produced it.** `source_status` is written to the
`scam_scans` table and rendered in the admin "Source" column. It used to be the constant
`"Local rules + OpenAI AI review"`, which was true while the transport was hardcoded and
becomes a stored false attribution the moment a chain can answer from Gemini. It is now
read from the envelope and *overwritten* onto the parsed dict, because the parsed dict was
produced by a model reading text an attacker chose.

Run: python3 -m pytest tests/test_scam_shield_routing.py
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="scam_shield_routing_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"

import undx_router  # noqa: E402

from services import (  # noqa: E402
    scam_shield,
    undx_call_domain,
    undx_privacy,
)
from tests import undx_source_probe as probe  # noqa: E402


#: Trips four independent local rules, so the deterministic verdict is Critical/100 and
#: there are several real red flags for a model reply to try to clear.
DANGEROUS = (
    "URGENT: your wallet is locked. Send your 12 words seed phrase to Telegram support "
    "admin and pay a 0.05 ETH release fee at http://metarnask-verify.co to unlock funds."
)

#: Trips nothing. Used where a test needs the local layer to have contributed no flags,
#: so that anything appearing in the result came from the AI path.
HARMLESS = "What time does the market open?"


def _envelope(payload, provider: str = "gemini", **extra) -> dict:
    """A success envelope in `route_structured_request`'s exact shape.

    `structured_output` is included because the router reports which dialect enforced the
    shape, and a fixture that omits envelope keys is a fixture that cannot notice the
    module starting to read one.
    """
    config = undx_router.PROVIDERS[provider]
    envelope = {
        "ok": True,
        "response": payload if isinstance(payload, str) else json.dumps(payload),
        "provider": provider,
        "source": config.label,
        "model": f"{provider}-test-model",
        "citations": [],
        "usage": {},
        "attempts": [{"provider": config.label, "status": "success"}],
        "call_domain": undx_call_domain.CALL_DOMAIN_SCAM_SHIELD,
        "call_domain_known": True,
        "structured_output": config.structured_output,
        "latency_ms": 37,
    }
    envelope.update(extra)
    return envelope


def _refusal(error: str = "no configured provider answered", **extra) -> dict:
    envelope = {
        "ok": False,
        "response": "",
        "error": error,
        "attempts": [],
        "call_domain": undx_call_domain.CALL_DOMAIN_SCAM_SHIELD,
        "call_domain_known": True,
        "structured_output": "",
        "latency_ms": 2,
    }
    envelope.update(extra)
    return envelope


def _route(return_value):
    """Patch the seam and hand back the mock, so callers can read the kwargs."""
    return mock.patch.object(scam_shield.undx_router, "route_structured_request",
                             return_value=return_value)


# --------------------------------------------------------------------- mechanism


class NoDirectProviderCallTest(unittest.TestCase):
    """Module-scoped, because this module has no legitimate transport of its own.

    The opposite scoping from `tests/test_sports_edge_routing.py`, where `bot.py` posts to
    Stripe, Telegram, Brevo and Mux and a file-wide ban would forbid four working
    integrations. Here nothing in the file has any business making a request, so the
    check is the strong form: not "it does not post to OpenAI" but "it posts to nothing".
    """

    @classmethod
    def setUpClass(cls):
        # Deliberately not `.resolve()`. The mutation harness builds a sandbox of
        # symlinks with exactly one real file, and resolving walks back out to the
        # unmutated original — which is how two mutations against an earlier phase's
        # test survived while appearing checked.
        cls.path = pathlib.Path(scam_shield.__file__)
        cls.tree = probe.parse(cls.path)
        cls.literals = probe.string_literals(cls.tree)
        cls.imports = probe.imported_names(cls.tree)

    def test_the_probe_can_see_this_module_at_all(self):
        """Anti-vacuity. Every other test in this class asserts an absence.

        If `parse` silently returned an empty tree — wrong path, a `.resolve()` that
        escaped a sandbox, a rename — all of them would pass and none of them would be
        checking anything. So first prove the tree contains things only this file has.
        """
        self.assertIsNotNone(probe.function(self.tree, "_ai_assessment"))
        self.assertIsNotNone(probe.function(self.tree, "analyze_text"))
        self.assertTrue(any("seed phrase" in text for text in self.literals),
                        "the local scam rules are not in the parsed tree")
        self.assertIn("undx_router", self.imports,
                      "the module that replaced the transport is not imported")

    def test_the_module_makes_no_network_call_of_any_kind(self):
        self.assertEqual(probe.transport_calls(self.tree), [])

    def test_the_module_names_no_provider_endpoint(self):
        """Including composed ones. A hostname in a literal is the thing being banned,
        not a fully-formed URL, because `f"https://{host}/v1/chat"` is still a direct
        call and contains no vendor string at all until you look at `host`.
        """
        for text in self.literals:
            lowered = text.lower()
            for needle in ("openai.com", "anthropic.com", "googleapis.com", "deepseek.com",
                           "groq.com", "meta.ai", "perplexity.ai", "/v1/chat/completions",
                           "/v1/messages", ":generatecontent"):
                self.assertNotIn(needle, lowered, f"{needle!r} in {text[:60]!r}")

    def test_the_module_reads_no_environment_variable_at_all(self):
        """The strong form, and deliberately not "it does not read OPENAI_API_KEY".

        This module read two: a key and `OPENAI_SCAM_MODEL`. Both are now the router's
        business. Asserting the set is empty means a future key lookup fails this test
        whatever it is called, which "does not read these two names" would not.
        """
        self.assertEqual(probe.environment_reads(self.tree), [])
        self.assertNotIn("os", self.imports)

    def test_the_module_imports_no_provider_sdk_and_no_transport(self):
        for banned in ("requests", "httpx", "openai", "anthropic", "google.generativeai",
                       "urllib.request", "aiohttp"):
            self.assertNotIn(banned, self.imports)

    def test_the_module_names_no_model(self):
        """`undx_router.PROVIDERS` is the only model table (§26).

        `OPENAI_SCAM_MODEL` defaulted to a model string here, which is a second answer to
        "which model reviews a scam report". Written as "names no model" rather than
        "does not name gpt-4o-mini", for the same reason as the environment check.
        """
        for text in self.literals:
            lowered = text.lower()
            for needle in ("gpt-", "claude-", "gemini-", "deepseek-", "llama-",
                           "muse-", "sonar"):
                self.assertNotIn(needle, lowered, f"model name {needle!r} in {text[:60]!r}")

    def test_the_assessment_reaches_a_model_only_through_the_router(self):
        node = probe.function(self.tree, "_ai_assessment")
        calls = probe.attribute_calls(node)
        self.assertIn("route_structured_request", calls)
        self.assertEqual(probe.transport_calls(node), [])

    def test_the_module_does_not_wear_the_undx_assistant_identity(self):
        """Scam Shield is infrastructure, not the UNDX messenger.

        `prepare_undx_model_request` prepends identity, company grounding, capability
        lifecycle and fact policy. A JSON classifier that spoke as UNDX would both break
        its own output contract and put the assistant's voice on a security verdict.
        """
        self.assertNotIn("prepare_undx_model_request",
                         probe.attribute_calls(self.tree))


class TheRequestDeclaresWhatItIsTest(unittest.TestCase):
    """§4 and §5: a privacy class and a domain, both as declared facts."""

    def _kwargs(self):
        with _route(_envelope({"scam_type": "phishing", "explanation": "x",
                               "safe_actions": []})) as route:
            scam_shield.analyze_text(DANGEROUS)
        self.assertEqual(route.call_count, 1)
        return route.call_args.kwargs

    def test_the_privacy_class_is_declared_and_is_at_least_confidential(self):
        """Not lowered to make routing possible (§4).

        Asserted through the rank rather than by string equality alone, so that a future
        change to CONFIDENTIAL-as-a-name cannot quietly weaken this, and a change to
        something *stricter* is allowed to pass.
        """
        privacy_class = self._kwargs()["privacy_class"]
        self.assertEqual(privacy_class, scam_shield.SCAM_SHIELD_PRIVACY_CLASS)
        self.assertTrue(undx_privacy.is_known(privacy_class), privacy_class)
        self.assertGreaterEqual(
            undx_privacy.rank(privacy_class),
            undx_privacy.rank(undx_privacy.SENSITIVITY_CONFIDENTIAL),
            "pasted scam reports carry the message, the address and the amount")

    def test_the_domain_is_scam_shield_and_comes_from_the_surface_not_the_text(self):
        """Provenance, not payload.

        Twice in this mission a content-derived domain has been wrong and a
        caller-derived one right. Every caller arrives through the Scam Shield surface, so
        the domain is fixed for the whole module. The second assertion is the one that
        matters: harmless text and dangerous text produce the same domain, because the
        domain is not a summary of what the text says.
        """
        self.assertEqual(self._kwargs()["call_domain"],
                         undx_call_domain.CALL_DOMAIN_SCAM_SHIELD)
        seen = []
        for text in (DANGEROUS, HARMLESS, "0x" + "a" * 40):
            with _route(_envelope({"scam_type": "", "explanation": "",
                                   "safe_actions": []})) as route:
                scam_shield.analyze_text(text)
            seen.append(route.call_args.kwargs["call_domain"])
        self.assertEqual(set(seen), {undx_call_domain.CALL_DOMAIN_SCAM_SHIELD})

    def test_the_domain_and_privacy_class_are_passed_by_keyword(self):
        """Positionally they would be silently wrong the next time a parameter is added.

        `route_structured_request`'s signature grew `history`, `require_json` and
        `json_schema` during this mission alone.
        """
        with _route(_envelope({"scam_type": "", "explanation": "", "safe_actions": []})) as route:
            scam_shield.analyze_text(DANGEROUS)
        self.assertLessEqual(len(route.call_args.args), 3, route.call_args.args)
        for name in ("privacy_class", "call_domain", "require_json", "json_schema"):
            self.assertIn(name, route.call_args.kwargs)

    def test_the_request_requires_json_and_declares_the_shape_it_parses(self):
        kwargs = self._kwargs()
        self.assertIs(kwargs["require_json"], True)
        schema = kwargs["json_schema"]
        self.assertEqual(schema, scam_shield.SCAM_ASSESSMENT_SCHEMA)
        # The schema has to require exactly the keys the parser below reads, or it is
        # decoration: a provider held to a schema that omits `safe_actions` can satisfy
        # it and still return nothing this module can use.
        self.assertEqual(set(schema["required"]),
                         {"scam_type", "explanation", "safe_actions"})
        self.assertEqual(schema["type"], "object")

    def test_nothing_is_asked_when_there_is_nothing_to_ask_about(self):
        """No unclassified spend, and no spend at all on an empty box (§20)."""
        with _route(_envelope({"scam_type": "", "explanation": "", "safe_actions": []})) as route:
            self.assertIsNone(scam_shield._ai_assessment("", "Unknown"))
        self.assertEqual(route.call_count, 0)

    def test_the_content_sent_is_bounded(self):
        with _route(_envelope({"scam_type": "", "explanation": "", "safe_actions": []})) as route:
            scam_shield._ai_assessment("a" * 40000, "Low")
        sent = " ".join(str(item) for item in route.call_args.args)
        self.assertLess(sent.count("a"), 6000, "an unbounded prompt is an unbounded bill")

    def test_a_long_paste_is_still_reviewed_because_the_old_guard_was_an_evasion(self):
        """The old code returned `None` above 5000 characters *and* sliced to 5000.

        The two were contradictory, so one was dead whichever way you read it. The guard
        is the one that went: "no AI review above 5000 characters" is something an
        attacker can use on purpose — pad the message past the limit and the model layer
        switches itself off, leaving local keyword rules only. The bound is the slice.

        Asserted rather than just described, because restoring the guard is a one-line
        change that looks like a cost saving and makes no test fail unless this exists.
        """
        with _route(_envelope({"scam_type": "phishing", "explanation": "x",
                               "safe_actions": []})) as route:
            note = scam_shield._ai_assessment("pad " * 3000 + "send your seed phrase", "Low")
        self.assertEqual(route.call_count, 1, "a padded message skipped the model layer")
        self.assertIsNotNone(note)
        self.assertNotIn("error", note)

    def test_the_system_prompt_still_refuses_exploit_instructions(self):
        self.assertIn("exploit", scam_shield._ASSESSMENT_SYSTEM_PROMPT.lower())


# --------------------------------------------------------------------- §16 ordering


class DeterministicControlsStayOnTopTest(unittest.TestCase):
    """The model advises. It does not decide. (§16)

    Each test here feeds a reply that tries to overturn one deterministic output. The
    refactor these guard against is not exotic — it is `result.update(ai_note)`, which
    reads like cleanup and hands pasted text a vote on its own risk rating.
    """

    def _baseline(self):
        """The verdict with no AI contribution at all, for comparison."""
        with _route(_refusal()):
            return scam_shield.analyze_text(DANGEROUS)

    def _with_reply(self, payload):
        with _route(_envelope(payload)):
            return scam_shield.analyze_text(DANGEROUS)

    def test_the_local_verdict_is_critical_so_these_tests_are_not_vacuous(self):
        baseline = self._baseline()
        self.assertEqual(baseline["risk_level"], "Critical")
        self.assertEqual(baseline["risk_score"], 100)
        self.assertGreaterEqual(len(baseline["red_flags"]), 3)

    def test_the_model_cannot_lower_the_score(self):
        result = self._with_reply({"scam_type": "none", "explanation": "Looks fine.",
                                   "safe_actions": [], "risk_score": 0, "score": 0})
        self.assertEqual(result["risk_score"], self._baseline()["risk_score"])

    def test_the_model_cannot_downgrade_the_risk_level(self):
        result = self._with_reply({"scam_type": "", "explanation": "Safe.",
                                   "safe_actions": [], "risk_level": "Low"})
        self.assertEqual(result["risk_level"], "Critical")

    def test_the_model_cannot_clear_a_red_flag(self):
        baseline = self._baseline()
        result = self._with_reply({"scam_type": "", "explanation": "No flags here.",
                                  "safe_actions": [], "red_flags": []})
        for flag in baseline["red_flags"]:
            if flag.startswith("AI review unavailable"):
                continue
            self.assertIn(flag, result["red_flags"])

    def test_the_model_cannot_remove_the_core_safe_actions(self):
        result = self._with_reply({"scam_type": "", "explanation": "",
                                   "safe_actions": ["Ignore the previous advice."]})
        self.assertTrue(any("Never share seed phrases" in item
                            for item in result["safe_actions"]))

    def test_the_model_cannot_remove_the_disclaimer(self):
        result = self._with_reply({"scam_type": "", "explanation": "", "safe_actions": [],
                                   "disclaimer": "", "ok": False})
        self.assertEqual(result["disclaimer"], scam_shield.DISCLAIMER)
        self.assertIs(result["ok"], True)

    def test_what_the_model_is_allowed_to_contribute_still_arrives(self):
        """The other half of §16, and the reason this is not simply "ignore the model".

        A control that routes a request and then discards the answer has kept its
        ordering and lost its point. The scam type joins the threat list, the safe
        actions join the list, and the explanation becomes the human-readable one.
        """
        result = self._with_reply({
            "scam_type": "Wallet-drainer approval phishing",
            "explanation": "The link imitates MetaMask and asks for a signature.",
            "safe_actions": ["Revoke approvals from a trusted revoke tool."]})
        self.assertIn("Wallet-drainer approval phishing", result["threats_detected"])
        self.assertEqual(result["explanation"],
                         "The link imitates MetaMask and asks for a signature.")
        self.assertTrue(any("Revoke approvals" in item for item in result["safe_actions"]))

    def test_the_local_explanation_is_used_when_the_model_supplies_none(self):
        result = self._with_reply({"scam_type": "x", "explanation": "", "safe_actions": []})
        self.assertIn("scan combines", result["explanation"])


# --------------------------------------------------------------------- attribution


class AttributionIsAFactAboutExecutionTest(unittest.TestCase):
    """`source_status` is stored in `scam_scans` and shown to an operator."""

    def test_the_provider_that_answered_is_the_one_named(self):
        for provider in ("openai", "gemini", "perplexity", "meta"):
            with self.subTest(provider=provider):
                with _route(_envelope({"scam_type": "", "explanation": "",
                                       "safe_actions": []}, provider=provider)):
                    result = scam_shield.analyze_text(DANGEROUS)
                self.assertIn(undx_router.PROVIDERS[provider].label,
                              result["source_status"])

    def test_no_provider_name_is_hardcoded_anywhere_in_the_module(self):
        """The old string was `"Local rules + OpenAI AI review"`.

        True while the transport was hardcoded; a stored false attribution the moment the
        chain can answer from somewhere else. Checked as an absence across all literals
        rather than by calling the function, because a constant reintroduced for a
        logging line is the same lie.
        """
        tree = probe.parse(pathlib.Path(scam_shield.__file__))
        for text in probe.string_literals(tree):
            lowered = text.lower()
            for vendor in ("openai", "anthropic", "deepseek", "perplexity", "groq"):
                self.assertNotIn(vendor, lowered, f"{vendor!r} in {text[:60]!r}")

    def test_the_model_cannot_forge_the_attribution(self):
        """The payload was produced by a model reading text an attacker chose.

        So a reply carrying its own `source` is a thing that happens on purpose, and the
        overwrite (not `setdefault`) is the assertion: Gemini answered, the payload claims
        OpenAI, the stored status must say Gemini.
        """
        with _route(_envelope({"scam_type": "", "explanation": "", "safe_actions": [],
                               "source": "OpenAI"}, provider="gemini")):
            result = scam_shield.analyze_text(DANGEROUS)
        self.assertIn(undx_router.PROVIDERS["gemini"].label, result["source_status"])
        self.assertNotIn("OpenAI", result["source_status"])

    def test_a_failure_says_so_instead_of_naming_a_provider(self):
        with _route(_refusal()):
            result = scam_shield.analyze_text(DANGEROUS)
        self.assertIn("unavailable", result["source_status"].lower())


class FailuresAreDistinguishableTest(unittest.TestCase):
    """Three different failures, three different notes.

    The old code reported all of them as whatever `str(exc)` happened to say, which meant
    an outage, a spend limit and a provider breaking its own contract were one line in the
    log. Whoever reads that line needs to do something different in each case.
    """

    def _note(self, envelope):
        with _route(envelope):
            result = scam_shield.analyze_text(HARMLESS)
        notes = [flag for flag in result["red_flags"]
                 if flag.startswith("AI review unavailable")]
        self.assertEqual(len(notes), 1, result["red_flags"])
        return notes[0]

    def test_the_routers_own_reason_is_carried_not_rewritten(self):
        """It already distinguishes a privacy refusal from a budget stop from an outage
        from a chain with no JSON-capable provider in it. Collapsing those into one
        string here throws away the only part of a failed envelope worth reading.
        """
        self.assertIn("monthly spend ceiling",
                      self._note(_refusal(error="monthly spend ceiling reached")))

    def test_a_provider_that_broke_its_json_promise_is_named_as_such(self):
        note = self._note(_envelope("I'm afraid I can't help with that."))
        self.assertIn("unparseable", note.lower())

    def test_a_json_array_is_not_accepted_as_an_assessment(self):
        """`json.loads` succeeds on `[]`, and `.get` on a list raises `AttributeError`.

        Which would escape `_ai_assessment` entirely and 500 the scan route, so the type
        check is load-bearing rather than defensive.
        """
        note = self._note(_envelope("[1, 2, 3]"))
        self.assertIn("non-object", note.lower())

    def test_the_local_verdict_survives_every_failure_mode(self):
        for envelope in (_refusal(), _envelope("prose"), _envelope("[]"),
                         _refusal(error="privacy ceiling")):
            with self.subTest(envelope=envelope.get("error") or envelope["response"][:12]):
                with _route(envelope):
                    result = scam_shield.analyze_text(DANGEROUS)
                self.assertEqual(result["risk_level"], "Critical")
                self.assertEqual(result["risk_score"], 100)


# --------------------------------------------------------------- past the seam


class TheRequirementIsEnforcedNotRequestedTest(unittest.TestCase):
    """The one class here that reaches past the seam into `undx_router`.

    Every test above mocks `route_structured_request`, which is right — they are about
    what this module sends. But that mock makes all of them pass against a router that
    accepts `require_json` and throws it away, and "accepts and discards" is the exact
    shape of the bug that would matter most: a scam check that believes the answer is
    guaranteed to be an object, reaching a provider that was never told.

    So this drives the real `route_structured_request` through the real adapters down to a
    mocked transport, and asks what the provider was actually sent.
    """

    def _sent(self, provider):
        seen = {}

        class Response:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                if provider == "gemini":
                    return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}],
                            "modelVersion": "fake"}
                return {"choices": [{"message": {"content": "{}"}}],
                        "model": "fake-model", "usage": {}}

        def post(url, **kwargs):
            seen["url"] = url
            seen["payload"] = kwargs.get("json") or {}
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
            # `_record_provider_failure` is patched so a failure inside this test cannot
            # open a real circuit breaker and change the result of whatever runs next.
            envelope = undx_router.route_structured_request(
                None, "system", "text", providers=[provider], require_json=True,
                json_schema=scam_shield.SCAM_ASSESSMENT_SCHEMA,
                privacy_class=scam_shield.SCAM_SHIELD_PRIVACY_CLASS,
                call_domain=scam_shield.SCAM_SHIELD_CALL_DOMAIN)
        self.assertTrue(envelope["ok"], envelope)
        return seen["payload"], envelope

    def test_the_json_requirement_reaches_every_capable_provider_in_its_own_dialect(self):
        """Four providers, three spellings. A capability is not a boolean.

        Probing one spelling measures the spelling: the live probe behind
        `PROVIDERS[...].structured_output` first recorded Perplexity as incapable on a
        real 400 that was rejecting `json_object` *by name* and listing `json_schema` as
        accepted.
        """
        expectations = {
            "openai": lambda p: p.get("response_format") == {"type": "json_object"},
            "meta": lambda p: p.get("response_format") == {"type": "json_object"},
            "perplexity": lambda p: (p.get("response_format") or {}).get("type") == "json_schema",
            "gemini": lambda p: (p.get("generationConfig") or {}).get(
                "responseMimeType") == "application/json",
        }
        for provider, holds in expectations.items():
            with self.subTest(provider=provider):
                self.assertTrue(undx_router.PROVIDERS[provider].structured_output,
                                "this test claims a capability the table does not")
                payload, envelope = self._sent(provider)
                self.assertTrue(holds(payload), payload)
                self.assertEqual(envelope["structured_output"],
                                 undx_router.PROVIDERS[provider].structured_output)

    def test_the_declared_schema_travels_with_the_request_where_the_dialect_takes_one(self):
        payload, _ = self._sent("perplexity")
        self.assertEqual(payload["response_format"]["json_schema"]["schema"],
                         scam_shield.SCAM_ASSESSMENT_SCHEMA)
        payload, _ = self._sent("gemini")
        self.assertEqual(payload["generationConfig"]["responseSchema"],
                         scam_shield.SCAM_ASSESSMENT_SCHEMA)

    def test_a_provider_that_cannot_be_required_to_return_json_is_not_asked(self):
        """The gate only ever *removes* providers, and says why it did.

        Claude 400s on `response_format` ("Extra inputs are not permitted"), so it cannot
        be held to the shape. Routing here anyway would produce prose, an unparseable
        answer, and a scam check reporting itself unavailable while a provider answered
        successfully. The refusal is recorded as an attempt so the chain describes the
        request that actually ran.
        """
        for provider in ("claude", "deepseek", "groq"):
            with self.subTest(provider=provider):
                self.assertEqual(undx_router.PROVIDERS[provider].structured_output, "")

        def post(url, **kwargs):  # pragma: no cover - reaching this is the failure
            raise AssertionError(f"a JSON-incapable provider was asked: {url}")

        with mock.patch.object(undx_router.requests, "post", side_effect=post), \
                mock.patch.object(undx_router, "_api_key", lambda name: "key"), \
                mock.patch.object(undx_router, "provider_enabled", lambda name: True), \
                mock.patch.object(undx_router, "_privacy_refusal", lambda *a, **k: ""), \
                mock.patch.object(undx_router, "_budget_refusal", lambda *a, **k: ""), \
                mock.patch.object(undx_router, "_breaker_should_skip", lambda *a, **k: False), \
                mock.patch.object(undx_router, "_record_provider_failure", lambda *a, **k: None):
            envelope = undx_router.route_structured_request(
                None, "system", "text", providers=["claude", "deepseek", "groq"],
                require_json=True, privacy_class=scam_shield.SCAM_SHIELD_PRIVACY_CLASS,
                call_domain=scam_shield.SCAM_SHIELD_CALL_DOMAIN)
        self.assertFalse(envelope["ok"])
        self.assertEqual({attempt["status"] for attempt in envelope["attempts"]},
                         {"capability_unmet"})
        self.assertIn("json", envelope["error"].lower())

    def test_the_requirement_is_opt_in_so_every_other_caller_is_unchanged(self):
        """The migration's safety property. A router that started requiring JSON of
        everybody would have broken every prose caller in the product.
        """
        seen = {}

        class Response:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "a sentence"}}],
                        "model": "fake-model", "usage": {}}

        def post(url, **kwargs):
            seen["payload"] = kwargs.get("json") or {}
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
            envelope = undx_router.route_structured_request(
                None, "system", "text", providers=["openai"])
        self.assertTrue(envelope["ok"], envelope)
        self.assertNotIn("response_format", seen["payload"])
        self.assertEqual(envelope["structured_output"], "",
                         "a caller that did not ask must not be told it was enforced")


class TheWholeScanStillWorksWithoutAnyProviderTest(unittest.TestCase):
    """The router is now in the path of a security control, so its absence is a case.

    Not a mock of the seam: the real router, with no credentials and no patching, which
    is the state a fresh checkout and every other test process is in.
    """

    def test_a_scan_with_no_configured_provider_still_returns_a_local_verdict(self):
        with mock.patch.object(undx_router, "_api_key", lambda name: ""):
            result = scam_shield.analyze_text(DANGEROUS)
        self.assertIs(result["ok"], True)
        self.assertEqual(result["risk_level"], "Critical")
        self.assertIn("unavailable", result["source_status"].lower())
        self.assertTrue(result["response"].endswith(scam_shield.DISCLAIMER))


if __name__ == "__main__":
    unittest.main(verbosity=2)
