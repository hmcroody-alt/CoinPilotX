"""Meta Muse and Perplexity as UNDX router providers.

Two of these tests exist because of something the live Meta API did, not because
of something a diff looked like it might do.

Muse Spark reasons before it answers, and the reasoning is billed against the
same `max_tokens` budget as the answer. Measured against the live API, a
two-letter reply at `reasoning_effort=high` spent 381 completion tokens, 370 of
them reasoning; at `max_tokens=32` the budget ran out first and the provider
returned `content: null` with `finish_reason: "length"`. The router's extractor
called `.strip()` on that, the AttributeError was caught by the per-provider
`except Exception`, logged as a generic `response_failed`, and the request failed
over to OpenAI. Muse would have appeared permanently broken while being perfectly
healthy and under-budgeted, and `route_structured_request`'s 320-token default
would have made that the normal case rather than the edge case.

So asserting "Meta is in PROVIDERS" is not enough. The tests that matter are the
ones that drive a response through the extraction path.

Run: .venv/bin/python3 -m pytest tests/test_undx_router_multi_provider.py
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import undx_router  # noqa: E402


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise undx_router.requests.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        return self._payload


def _chat(content, finish_reason="stop"):
    return {"choices": [{"message": {"content": content, "role": "assistant"}, "finish_reason": finish_reason}],
            "model": "muse-spark-1.3"}


def _env(**overrides):
    """Patch env with every provider credential cleared first.

    Inherited keys would let a test that means to exercise one provider silently
    exercise whichever others happen to be configured on the machine.
    """
    base = {config.key_env: "" for config in undx_router.PROVIDERS.values()}
    base["Gemini_AI_API"] = ""
    base["GEMINI_AI_API"] = ""
    base.update(overrides)
    return mock.patch.dict(os.environ, base)


class ProviderRegistrationTest(unittest.TestCase):
    def test_meta_and_perplexity_are_reachable_through_the_router(self):
        for provider in ("meta", "perplexity"):
            self.assertIn(provider, undx_router.PROVIDERS)
            # Registration without a caller is the failure that looks like
            # success: provider_priority plans the provider, then the loop
            # KeyErrors into the generic handler on every request.
            self.assertIn(provider, undx_router.CALLERS, f"{provider} has no caller")

    def test_meta_reads_the_railway_variable_name_not_the_vendor_default(self):
        """The reference config named `MODEL_API_KEY`; Railway holds `META_MODEL_API_KEY`.

        A router reading the other name finds nothing, reports Meta as
        unconfigured, and skips it forever without an error anywhere.
        """
        self.assertEqual(undx_router.PROVIDERS["meta"].key_env, "META_MODEL_API_KEY")

    def test_muse_and_sonar_aliases_resolve(self):
        self.assertEqual(undx_router._normalize_provider("muse"), "meta")
        self.assertEqual(undx_router._normalize_provider("Meta"), "meta")
        self.assertEqual(undx_router._normalize_provider("pplx"), "perplexity")

    def test_meta_is_not_the_default_provider(self):
        """§7: Muse joins as an available specialist, not as the global default."""
        with _env(UNDX_DEFAULT_AI_PROVIDER=""):
            self.assertEqual(undx_router.default_provider(), "openai")

    def test_status_logging_names_every_provider(self):
        """The hardcoded five-provider log line survived a sixth provider silently."""
        with _env(META_MODEL_API_KEY="x" * 48), self.assertLogs(level="INFO") as captured:
            undx_router.log_provider_status()
        line = "\n".join(captured.output)
        for provider in undx_router.PROVIDERS:
            self.assertIn(provider, line, f"{provider} missing from the provider status line")


class EmptyCompletionTest(unittest.TestCase):
    """The reasoning-budget failure, at the exact layer that used to crash."""

    def test_null_content_raises_a_named_error_not_an_attribute_error(self):
        with self.assertRaises(ValueError) as caught:
            undx_router._provider_text("meta", None, "length")
        message = str(caught.exception)
        self.assertIn("Meta Muse", message)
        self.assertIn("budget", message, "the error must name the cause, not just the symptom")

    def test_null_content_without_a_length_finish_still_fails_cleanly(self):
        with self.assertRaises(ValueError):
            undx_router._provider_text("meta", None, "stop")

    def test_whitespace_only_content_is_not_an_answer(self):
        with self.assertRaises(ValueError):
            undx_router._provider_text("openai", "   \n  ", "stop")

    def test_a_real_answer_passes_through_stripped(self):
        self.assertEqual(undx_router._provider_text("meta", "  ok  ", "stop"), "ok")

    def test_the_meta_call_path_does_not_raise_attribute_error_on_null_content(self):
        """Drive it through `_call_meta`, which is where the crash actually lived."""
        with _env(META_MODEL_API_KEY="k" * 48), \
                mock.patch.object(undx_router.requests, "post", return_value=_FakeResponse(_chat(None, "length"))):
            with self.assertRaises(ValueError):
                undx_router._call_meta("sys", "hello", [], 25)

    def test_an_exhausted_budget_does_not_report_success_with_empty_text(self):
        """Failing over is correct here; returning `ok` with no answer is not."""
        with _env(META_MODEL_API_KEY="k" * 48, UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1",
                  UNDX_DEFAULT_AI_PROVIDER="meta"), \
                mock.patch.object(undx_router.requests, "post", return_value=_FakeResponse(_chat(None, "length"))):
            result = undx_router.route_undx_request(1, "plan the next repository migration step")
        self.assertFalse(result["ok"])
        self.assertNotIn("response", {k: v for k, v in result.items() if v})


class TokenBudgetTest(unittest.TestCase):
    def test_meta_gets_headroom_for_reasoning_tokens(self):
        self.assertGreater(
            undx_router._effective_max_tokens("meta", 320),
            320,
            "a 320-token budget is spent entirely on reasoning before the answer starts",
        )

    def test_providers_without_reasoning_overhead_are_untouched(self):
        for provider in ("openai", "claude", "groq", "perplexity"):
            self.assertEqual(undx_router._effective_max_tokens(provider, 900), 900)

    def test_the_headroom_reaches_the_wire(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return _FakeResponse(_chat("ok"))

        with _env(META_MODEL_API_KEY="k" * 48), mock.patch.object(undx_router.requests, "post", fake_post):
            undx_router._call_meta("sys", "hi", [], 25, max_tokens=320)
        self.assertGreater(captured["max_tokens"], 320)


class ReasoningEffortTest(unittest.TestCase):
    def test_every_call_declares_an_effort(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return _FakeResponse(_chat("ok"))

        with _env(META_MODEL_API_KEY="k" * 48), mock.patch.object(undx_router.requests, "post", fake_post):
            undx_router._call_meta("sys", "hi", [], 25)
        self.assertIn("reasoning_effort", captured)
        self.assertIn(captured["reasoning_effort"], undx_router.META_REASONING_EFFORTS)

    def test_an_unrecognised_effort_is_replaced_rather_than_sent(self):
        """The live API rejects an unknown effort with HTTP 400 for the whole call.

        Sending a typo through would turn one bad environment variable into a
        total Meta outage, which the failover then hides as slowness.
        """
        with _env(META_MUSE_REASONING_EFFORT="aggressive"):
            self.assertIn(undx_router._meta_reasoning_effort(), undx_router.META_REASONING_EFFORTS)

    def test_a_valid_effort_is_honoured(self):
        with _env(META_MUSE_REASONING_EFFORT="minimal"):
            self.assertEqual(undx_router._meta_reasoning_effort(), "minimal")


class TimeoutTest(unittest.TestCase):
    def test_meta_gets_its_configured_budget_rather_than_the_module_default(self):
        """The first live Meta call took 26.7s against a 25s module default."""
        with _env(META_MUSE_TIMEOUT_MS="60000"):
            self.assertGreaterEqual(undx_router._timeout("meta", 25), 60)

    def test_a_provider_budget_never_shortens_the_callers(self):
        with _env(META_MUSE_TIMEOUT_MS="1000"):
            self.assertEqual(undx_router._timeout("meta", 25), 25)

    def test_a_provider_with_no_declared_budget_keeps_the_callers(self):
        with _env():
            self.assertEqual(undx_router._timeout("openai", 12), 12)

    def test_a_non_numeric_budget_falls_back_instead_of_crashing(self):
        with _env(META_MUSE_TIMEOUT_MS="sixty seconds"):
            self.assertGreaterEqual(undx_router._timeout("meta", 25), 25)


class KillSwitchTest(unittest.TestCase):
    def test_a_disabled_provider_is_not_planned_even_with_a_valid_key(self):
        """§73: `META_MUSE_ENABLED=false` must take Muse out of rotation at once."""
        with _env(META_MODEL_API_KEY="k" * 48, META_MUSE_ENABLED="false",
                  UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1"):
            ordered = undx_router.provider_priority({"category": "repository"})
        self.assertNotIn("meta", ordered)

    def test_the_same_provider_is_planned_when_the_switch_is_on(self):
        with _env(META_MODEL_API_KEY="k" * 48, META_MUSE_ENABLED="true",
                  UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1"):
            ordered = undx_router.provider_priority({"category": "repository"})
        self.assertIn("meta", ordered)

    def test_an_absent_switch_means_enabled(self):
        with _env():
            self.assertTrue(undx_router.provider_enabled("gemini"))
            self.assertTrue(undx_router.provider_enabled("openai"))

    def test_disabling_one_provider_leaves_the_rest_routable(self):
        with _env(META_MODEL_API_KEY="k" * 48, META_MUSE_ENABLED="false",
                  UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1"):
            ordered = undx_router.provider_priority({"category": "repository"})
        self.assertIn("openai", ordered)

    def test_health_distinguishes_switched_off_from_never_configured(self):
        """They call for opposite fixes, so they must not share one label."""
        with _env(META_MODEL_API_KEY="k" * 48, META_MUSE_ENABLED="false"):
            self.assertEqual(undx_router.provider_health("meta"), "Disabled")
        with _env(META_MUSE_ENABLED="true"):
            self.assertEqual(undx_router.provider_health("meta"), "Missing API Key")


class FreshnessRoutingTest(unittest.TestCase):
    def test_a_question_about_now_is_classified_as_current_web(self):
        self.assertEqual(
            undx_router.classify_request("what is the latest news on stablecoin regulation")["category"],
            "current_web",
        )

    def test_freshness_outranks_a_larger_count_of_other_signals(self):
        """One freshness term against many repository terms must still route fresh.

        Signal counting would hand this to a model answering from training data,
        and a stale answer to this question is well-formed and confident.
        """
        message = "what is the current recommended python file layout for a repo with code, git commits and debug tooling"
        self.assertEqual(undx_router.classify_request(message)["category"], "current_web")

    def test_perplexity_leads_the_current_web_lane(self):
        with _env(UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1", UNDX_DEFAULT_AI_PROVIDER="openai"):
            ordered = undx_router.provider_priority({"category": "current_web"})
        self.assertIn("perplexity", ordered)
        self.assertLess(ordered.index("perplexity"), ordered.index("openai"))

    def test_an_ordinary_build_request_is_not_dragged_into_the_research_lane(self):
        """A freshness lane that catches everything is a freshness lane for nothing."""
        self.assertNotEqual(
            undx_router.classify_request("add a dark mode toggle to the settings page")["category"],
            "current_web",
        )


class PerplexityCitationTest(unittest.TestCase):
    PAYLOAD = {
        "choices": [{"message": {"content": "Stablecoin rules changed in March."}, "finish_reason": "stop"}],
        "search_results": ["https://example.invalid/a", "https://example.invalid/b"],
        "model": "sonar",
    }

    def test_sources_survive_the_provider_adapter(self):
        with _env(PERPLEXITY_API_KEY="p" * 53), \
                mock.patch.object(undx_router.requests, "post", return_value=_FakeResponse(self.PAYLOAD)):
            result = undx_router._call_perplexity("sys", "what changed", [], 25)
        self.assertEqual(len(result["citations"]), 2)

    def test_sources_survive_the_router_envelope(self):
        """The adapter keeping them is no use if the envelope drops them.

        An unattributed research answer is indistinguishable from an invented
        one, and only the envelope reaches the caller.
        """
        with _env(PERPLEXITY_API_KEY="p" * 53, UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1",
                  UNDX_DEFAULT_AI_PROVIDER="perplexity"), \
                mock.patch.object(undx_router.requests, "post", return_value=_FakeResponse(self.PAYLOAD)):
            result = undx_router.route_undx_request(1, "what is the latest on stablecoin regulation")
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "perplexity")
        self.assertEqual(len(result["citations"]), 2)

    def test_citations_are_present_and_empty_for_an_ungrounded_provider(self):
        """So a caller can render attribution without branching on provider name."""
        with _env(OPENAI_API_KEY="sk-" + "o" * 40, UNDX_DEFAULT_AI_PROVIDER="openai"), \
                mock.patch.object(undx_router.requests, "post", return_value=_FakeResponse(_chat("hello"))):
            result = undx_router.route_undx_request(1, "say hello")
        self.assertTrue(result["ok"])
        self.assertEqual(result["citations"], [])


class CredentialRedactionTest(unittest.TestCase):
    """`_safe_error` walks PROVIDERS, so new providers must inherit redaction.

    tests/protection/test_undx_router_credentials.py predicted this exact risk:
    "the next provider added to this module will be written by copying an
    existing one". These are that prediction, checked.
    """

    def test_a_meta_key_is_redacted_from_logged_exception_text(self):
        secret = "LLM_" + "m" * 44
        with _env(META_MODEL_API_KEY=secret):
            cleaned = undx_router._safe_error(Exception(f"401 Unauthorized for token {secret}"))
        self.assertNotIn(secret, cleaned)

    def test_a_perplexity_key_is_redacted_from_logged_exception_text(self):
        secret = "pplx-" + "p" * 48
        with _env(PERPLEXITY_API_KEY=secret):
            cleaned = undx_router._safe_error(Exception(f"rejected credential {secret}"))
        self.assertNotIn(secret, cleaned)

    def test_neither_new_provider_sends_its_key_in_a_query_string(self):
        captured = []

        def fake_post(url, **kwargs):
            captured.append((url, kwargs.get("params")))
            return _FakeResponse(_chat("ok"))

        with _env(META_MODEL_API_KEY="k" * 48, PERPLEXITY_API_KEY="p" * 53), \
                mock.patch.object(undx_router.requests, "post", fake_post):
            undx_router._call_meta("sys", "hi", [], 25)
            undx_router._call_perplexity("sys", "hi", [], 25)
        for url, params in captured:
            self.assertNotIn("key=", url)
            self.assertIsNone(params)


class MalformedCredentialTest(unittest.TestCase):
    """A provider variable set to a config blob instead of a key.

    Found in production, not imagined: `GROQ_AI_API` held a JSON document that
    contained an `api_key` field. The value could not be used as an HTTP header,
    so every request raised, and the exception - quoting the offending header
    value, key included - was written to the application log once per call.
    Whole-value redaction did not fire because the logged text was a *slice* of
    the variable, never equal to it.

    Two independent defences, because either alone is one edit from being undone:
    the value never reaches the HTTP layer, and if some other path logs it
    anyway, the redactor now matches the embedded token rather than the wrapper.
    """

    BLOB = (
        '{\n  "custom_models": [\n    {\n      "model": "openai/gpt-oss-120b",\n'
        '      "api_key": "gsk_EXAMPLENOTREALaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n'
        '      "provider": "generic-chat-completion-api"\n    }\n  ]\n}'
    )
    EMBEDDED = "gsk_EXAMPLENOTREALaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_a_multiline_value_is_never_offered_as_a_credential(self):
        with _env(GROQ_AI_API=self.BLOB):
            self.assertEqual(undx_router._api_key("groq"), "")

    def test_the_malformed_value_never_reaches_the_http_layer(self):
        """The plan may still list the provider; the request must not be made.

        This is the property that matters. `requests` is what quoted the header
        value into an exception, so a credential that is never handed to
        `requests` cannot be leaked by it however the routing plan is built.
        """
        calls = []

        def fake_post(url, **kwargs):
            calls.append(kwargs.get("headers", {}))
            return _FakeResponse(_chat("ok"))

        with _env(GROQ_AI_API=self.BLOB, UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1",
                  UNDX_DEFAULT_AI_PROVIDER="groq"), \
                mock.patch.object(undx_router.requests, "post", fake_post):
            result = undx_router.route_undx_request(1, "quick status")

        self.assertFalse(result["ok"], "no provider was configured, so nothing should have answered")
        self.assertEqual(calls, [], "a malformed credential was sent to the HTTP layer")
        groq_attempts = [a for a in result["router"]["attempts"] if a["provider"] == "Groq"]
        self.assertEqual([a["status"] for a in groq_attempts], ["not_configured"])

    def test_health_says_malformed_rather_than_missing(self):
        """Otherwise the fix looks like "set the variable" - and it is set."""
        with _env(GROQ_AI_API=self.BLOB):
            self.assertEqual(undx_router.provider_health("groq"), "Malformed API Key")

    def test_a_key_embedded_in_a_larger_value_is_redacted_from_logs(self):
        with _env(GROQ_AI_API=self.BLOB):
            leaked = f"Invalid header value: 'Bearer {self.BLOB[:220]}"
            cleaned = undx_router._safe_error(Exception(leaked))
        self.assertNotIn(self.EMBEDDED, cleaned)

    def test_a_json_named_credential_is_redacted_even_when_unconfigured(self):
        """The blob may be logged by a path that never consulted the env var."""
        with _env():
            cleaned = undx_router._safe_error(
                Exception('upstream rejected {"api_key": "gsk_UNRELATEDbbbbbbbbbbbbbbbbbbbbbbbb"}')
            )
        self.assertNotIn("gsk_UNRELATEDbbbbbbbbbbbbbbbbbbbbbbbb", cleaned)

    def test_fragment_redaction_does_not_eat_ordinary_error_text(self):
        """A redactor that destroys the diagnosis gets deleted by whoever debugs next."""
        with _env(GROQ_AI_API=self.BLOB):
            cleaned = undx_router._safe_error(Exception("Connection reset by peer"))
        self.assertIn("Connection reset by peer", cleaned)

    def test_short_words_inside_a_blob_are_not_treated_as_credentials(self):
        with _env(GROQ_AI_API=self.BLOB):
            cleaned = undx_router._safe_error(Exception("the provider field was rejected"))
        self.assertIn("provider", cleaned)

    def test_a_well_formed_key_still_works(self):
        with _env(GROQ_AI_API="gsk_" + "z" * 48):
            self.assertTrue(undx_router._api_key("groq"))
            self.assertEqual(undx_router.provider_health("groq"), "Online")


class ClaudeEndpointTest(unittest.TestCase):
    """Claude must reach Anthropic, on a model ID that still exists.

    Two independent faults were live in production at once, and only one of them
    was visible. The router asked for `claude-3-5-haiku-latest`, which Anthropic
    has retired, so every Claude call 404'd and the provider was dead in the
    `security` and `research` chains - while the credential was valid the whole
    time. That is the loud one.

    The quiet one is that this environment also sets ANTHROPIC_BASE_URL to
    `https://api.meta.ai` and ANTHROPIC_MODEL to `muse-spark-1.3-contributor`,
    left behind by the Claude Code CLI. Honouring those - the obvious "make the
    adapter configurable" refactor - would route everything addressed to Claude
    into Meta's Contributor tier, whose console states inputs and outputs train
    Meta's models. The 404 is what has been *preventing* that. Fixing the model
    ID without pinning the endpoint would convert a dead provider into a silent
    data-governance breach, which is the worse of the two outcomes.
    """

    ANSWER = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}

    def _capture(self):
        return mock.patch.object(undx_router.requests, "post",
                                 return_value=_FakeResponse(self.ANSWER))

    def test_default_model_is_a_live_id_not_the_retired_one(self):
        self.assertEqual(undx_router.PROVIDERS["claude"].default_model, "claude-haiku-4-5")
        self.assertNotIn("3-5-haiku", undx_router.PROVIDERS["claude"].default_model)

    def test_claude_posts_to_anthropic_even_when_base_url_redirects_elsewhere(self):
        with _env(CLAUDE_AI_API="sk-ant-" + "y" * 40,
                  ANTHROPIC_BASE_URL="https://api.meta.ai",
                  CLAUDE_MODEL=""), self._capture() as post:
            undx_router._call_claude("sys", "hello", [], 30)
        url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertNotIn("meta.ai", url)

    def test_ambient_anthropic_model_cannot_swap_in_the_contributor_tier(self):
        """ANTHROPIC_MODEL is not CLAUDE_MODEL, and must not be read as it."""
        with _env(CLAUDE_AI_API="sk-ant-" + "y" * 40,
                  ANTHROPIC_MODEL="muse-spark-1.3-contributor",
                  CLAUDE_MODEL=""), self._capture() as post:
            undx_router._call_claude("sys", "hello", [], 30)
        sent = post.call_args.kwargs["json"]["model"]
        self.assertEqual(sent, "claude-haiku-4-5")
        self.assertNotIn("contributor", sent)

    def test_an_operator_can_still_override_the_model_through_claude_model(self):
        """Pinning the endpoint must not also freeze the model ID.

        The retired default is exactly why this override needs to keep working:
        the next retirement should be fixable with an environment variable.
        """
        with _env(CLAUDE_AI_API="sk-ant-" + "y" * 40,
                  CLAUDE_MODEL="claude-sonnet-4-5"), self._capture() as post:
            undx_router._call_claude("sys", "hello", [], 30)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "claude-sonnet-4-5")


class GeminiModelTest(unittest.TestCase):
    """Gemini's default must be a model that answers, on a budget it can finish in.

    `gemini-1.5-flash` was retired upstream, which is why Gemini 404'd. The
    replacement was picked by measurement rather than by taking the newest ID.
    Across two samples minutes apart, `gemini-flash-lite-latest` ran ~4x faster
    than `gemini-flash-latest` (0.9s/2.9s against 3.9s/11.0s). Both suffer the
    same transient HTTP 503s, so availability did not separate them and is not
    claimed to. Gemini is never first in any chain in `provider_priority`, so it
    is only reached once another provider has already failed and that budget is
    already spent; the faster model is the better tail.

    The trap underneath: ListModels advertises models this key cannot call.
    `gemini-2.5-flash` and `gemini-2.5-flash-lite` are both listed and both 404
    on generateContent, so "it is in the list" is not evidence of anything.
    """

    ANSWER = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}

    def test_default_model_is_not_the_retired_one(self):
        self.assertNotIn("1.5", undx_router.PROVIDERS["gemini"].default_model)
        self.assertEqual(undx_router.PROVIDERS["gemini"].default_model, "gemini-flash-lite-latest")

    def test_gemini_budget_exhaustion_is_named_not_crashed_on(self):
        """Gemini spells it MAX_TOKENS where OpenAI spells it length.

        Both mean the budget ran out before the answer started, and both used to
        reach `None.strip()`. The message has to point at the budget, or the next
        person reads `response_failed` and goes looking for an outage.
        """
        with self.assertRaises(ValueError) as caught:
            undx_router._provider_text("gemini", None, "MAX_TOKENS")
        message = str(caught.exception)
        self.assertIn("token budget", message)
        self.assertIn("MAX_TOKENS", message)

    def test_openai_style_length_is_still_named(self):
        with self.assertRaises(ValueError) as caught:
            undx_router._provider_text("meta", None, "length")
        self.assertIn("token budget", str(caught.exception))

    def test_a_real_answer_is_untouched_by_the_budget_branch(self):
        self.assertEqual(undx_router._provider_text("gemini", " ok ", "MAX_TOKENS"), "ok")

    def test_the_key_travels_as_a_header_never_as_a_query_parameter(self):
        """A `?key=` lands in proxy logs and in the text of request exceptions."""
        with _env(Gemini_AI_API="AIza" + "q" * 35), mock.patch.object(
                undx_router.requests, "post", return_value=_FakeResponse(self.ANSWER)) as post:
            undx_router._call_gemini("sys", "hello", [], 30)
        url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
        self.assertNotIn("key=", url)
        self.assertIn("x-goog-api-key", post.call_args.kwargs["headers"])
        self.assertIsNone(post.call_args.kwargs.get("params"))

    def test_the_model_id_is_interpolated_into_the_path(self):
        with _env(Gemini_AI_API="AIza" + "q" * 35, GEMINI_MODEL="gemini-flash-latest"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(self.ANSWER)) as post:
            undx_router._call_gemini("sys", "hello", [], 30)
        url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
        self.assertIn("gemini-flash-latest:generateContent", url)


if __name__ == "__main__":
    unittest.main()
