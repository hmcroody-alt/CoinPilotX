"""The privacy ceiling: which provider may receive how sensitive a request.

The control is one comparison, so these tests are mostly about the ways a single
comparison can be true and useless. Three in particular:

* it can be bypassed, because `provider_priority()` is not the only thing that
  decides which providers get tried;
* it can be *inverted* by a fallback, because `dict.get(name, 0)` on an unknown
  class name declassifies instead of refusing;
* it can go stale, because Meta's two tiers share a credential and a base URL
  and differ only by the model ID in the request body.

Each of those has a test below that fails when the corresponding line is
removed. Run:

    .venv/bin/python3 -m pytest tests/test_undx_privacy_ceiling.py
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import undx_router  # noqa: E402
from services import undx_privacy  # noqa: E402
from services.private_office import model as office_model  # noqa: E402


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


def _chat(content="ok"):
    return {"choices": [{"message": {"content": content, "role": "assistant"}, "finish_reason": "stop"}],
            "model": "test-model"}


def _env(**overrides):
    base = {config.key_env: "" for config in undx_router.PROVIDERS.values()}
    base["Gemini_AI_API"] = ""
    base["UNDX_DEFAULT_REQUEST_PRIVACY"] = ""
    base.update(overrides)
    return mock.patch.dict(os.environ, base)


def _all_keys(**overrides):
    """Every provider credentialed, so nothing is skipped for being unconfigured.

    A test about privacy that accidentally exercises "not_configured" proves
    nothing about privacy.
    """
    base = {config.key_env: "k" * 40 for config in undx_router.PROVIDERS.values()}
    base["Gemini_AI_API"] = "k" * 40
    base["UNDX_DEFAULT_REQUEST_PRIVACY"] = ""
    base.update(overrides)
    return mock.patch.dict(os.environ, base)


class LadderTest(unittest.TestCase):
    def test_the_shared_rungs_are_the_private_office_ones(self):
        """Drift detector, and the reason this module imports rather than copies.

        `PRIVATE_OFFICE_OWNERSHIP_CONTRACT.md` makes the Office the canonical
        owner of this ladder. If someone adds a rung there, the router's ranks
        must move with it - a second hardcoded copy would keep working while
        meaning something different, which is the worst available outcome for a
        comparison that decides where user content goes.
        """
        self.assertEqual(
            undx_privacy.PRIVACY_CLASSES[1:-1], office_model.SENSITIVITIES,
            "the middle rungs must be exactly the Private Office ladder, in its order")

    def test_the_ladder_is_ordered_low_to_high(self):
        ranks = [undx_privacy.rank(name) for name in undx_privacy.PRIVACY_CLASSES]
        self.assertEqual(ranks, sorted(ranks))
        self.assertLess(undx_privacy.rank("SYNTHETIC"), undx_privacy.rank("PUBLIC"))
        self.assertLess(undx_privacy.rank("RESTRICTED"), undx_privacy.rank("SECRET"))

    def test_an_unknown_class_ranks_at_the_top_not_the_bottom(self):
        """The single most important line in the module.

        A `dict.get(name, 0)` here would rank a typo as SYNTHETIC and send it
        everywhere. The failure would be silent, permanent, and visible only to
        someone reading the provider's logs.
        """
        self.assertEqual(undx_privacy.rank("CONFIDENTAIL"),  # deliberate typo
                         undx_privacy.rank("SECRET"))
        self.assertFalse(undx_privacy.is_known("CONFIDENTAIL"))
        for provider in undx_router.PROVIDERS:
            self.assertFalse(
                undx_privacy.provider_accepts(provider, "CONFIDENTAIL"),
                f"{provider} accepted an unrecognised class name")

    def test_the_documented_vocabularies_map_onto_the_ladder(self):
        """The data-policy document's names must not fall through to the unknown rung."""
        self.assertEqual(undx_privacy.normalise("PLATFORM_PUBLIC"), "PUBLIC")
        self.assertEqual(undx_privacy.normalise("PLATFORM_PRIVATE"), "CONFIDENTIAL")
        self.assertEqual(undx_privacy.normalise("PRIVATE_OFFICE"), "RESTRICTED")
        self.assertEqual(undx_privacy.normalise("USER_PRIVATE"), "CONFIDENTIAL")
        for alias in undx_privacy.PRIVACY_ALIASES:
            self.assertTrue(undx_privacy.is_known(alias), f"{alias} does not resolve")

    def test_class_names_are_case_and_whitespace_insensitive(self):
        self.assertEqual(undx_privacy.normalise("  public "), "PUBLIC")
        self.assertEqual(undx_privacy.normalise("Private_Office"), "RESTRICTED")


class SecretTest(unittest.TestCase):
    def test_secret_reaches_no_provider(self):
        for provider in undx_router.PROVIDERS:
            self.assertFalse(
                undx_privacy.provider_accepts(provider, "SECRET"),
                f"{provider} accepted SECRET")

    def test_no_ceiling_can_admit_secret(self):
        """Structural, not per-provider.

        `provider_accepts` admits anything at or below the ceiling, so SECRET
        becomes reachable the moment any ceiling is set to SECRET. Asserting
        against the tables rather than against today's seven providers means a
        provider added later cannot open the door by copying a neighbour's row.
        """
        for provider, ceiling in undx_privacy.PROVIDER_CEILINGS.items():
            self.assertLess(undx_privacy.rank(ceiling), undx_privacy.rank("SECRET"),
                            f"{provider}'s ceiling admits SECRET")
        for model, ceiling in undx_privacy.MODEL_CEILINGS.items():
            self.assertLess(undx_privacy.rank(ceiling), undx_privacy.rank("SECRET"),
                            f"{model}'s ceiling admits SECRET")


class ProviderCeilingTest(unittest.TestCase):
    def test_every_configured_provider_has_a_declared_ceiling(self):
        """Written off PROVIDERS, so a provider added later fails here first."""
        missing = [p for p in undx_router.PROVIDERS if p not in undx_privacy.PROVIDER_CEILINGS]
        self.assertEqual(missing, [], f"providers with no declared privacy ceiling: {missing}")

    def test_an_undeclared_provider_is_synthetic_only(self):
        """The fallback has to make a new provider useless, not universal.

        If an unclassified provider defaulted to something usable, the next one
        added would arrive pre-approved for user content and the test above
        would be the only thing standing in the way - a test someone can delete
        in the same commit that adds the provider.
        """
        self.assertEqual(undx_privacy.provider_ceiling("brand-new-vendor"),
                         undx_privacy.PRIVACY_SYNTHETIC)
        self.assertTrue(undx_privacy.provider_accepts("brand-new-vendor", "SYNTHETIC"))
        self.assertFalse(undx_privacy.provider_accepts("brand-new-vendor", "PUBLIC"))

    def test_perplexity_is_capped_at_public_because_it_searches(self):
        self.assertTrue(undx_privacy.provider_accepts("perplexity", "PUBLIC"))
        self.assertFalse(undx_privacy.provider_accepts("perplexity", "CONFIDENTIAL"))

    def test_no_provider_may_receive_private_office_data(self):
        for provider in undx_router.PROVIDERS:
            self.assertFalse(
                undx_privacy.provider_accepts(provider, "PRIVATE_OFFICE"),
                f"{provider} accepted PRIVATE_OFFICE data")
            self.assertFalse(undx_privacy.provider_accepts(provider, "RESTRICTED"))


class MetaTierTest(unittest.TestCase):
    """The ceiling follows the model, because Meta's two tiers share everything else.

    Standard and Contributor are reached with the same credential, the same base
    URL and the same adapter. The only thing that differs is the model ID in the
    body - and the vendor's stated position on training. A ceiling keyed on the
    provider alone is therefore one environment variable away from blessing
    exactly what it was written to prevent.
    """

    STANDARD = "muse-spark-1.3"
    CONTRIBUTOR = "muse-spark-1.3-contributor"

    def test_standard_tier_accepts_confidential(self):
        self.assertTrue(undx_privacy.provider_accepts("meta", "CONFIDENTIAL", self.STANDARD))

    def test_contributor_tier_is_synthetic_only(self):
        self.assertEqual(undx_privacy.provider_ceiling("meta", self.CONTRIBUTOR),
                         undx_privacy.PRIVACY_SYNTHETIC)
        self.assertFalse(undx_privacy.provider_accepts("meta", "PUBLIC", self.CONTRIBUTOR))
        self.assertFalse(undx_privacy.provider_accepts("meta", "CONFIDENTIAL", self.CONTRIBUTOR))

    def test_the_router_reads_the_model_it_is_about_to_send(self):
        """Not the default. Flipping META_MUSE_MODEL must move the ceiling with it.

        This is the test that fails if `_privacy_refusal` passes no model, or
        passes a hardcoded one, and it is the reason that argument exists.
        """
        with _env(META_MUSE_MODEL=self.CONTRIBUTOR):
            self.assertTrue(undx_router._privacy_refusal("meta", "CONFIDENTIAL"))
            self.assertFalse(undx_router._privacy_refusal("meta", "SYNTHETIC"))
        with _env(META_MUSE_MODEL=self.STANDARD):
            self.assertFalse(undx_router._privacy_refusal("meta", "CONFIDENTIAL"))


class DefaultClassTest(unittest.TestCase):
    def test_an_unclassified_request_is_treated_as_confidential(self):
        """The router cannot see what it is forwarding.

        `undx_capability_planner` sends the user's own message verbatim to be
        classified. Assuming that is public because nobody said otherwise is the
        precise failure this module exists to close.
        """
        with _env():
            self.assertEqual(undx_privacy.normalise(None), "CONFIDENTIAL")
            self.assertEqual(undx_privacy.normalise(""), "CONFIDENTIAL")

    def test_the_env_override_can_raise_the_default_but_never_lower_it(self):
        """Otherwise the whole module is one `railway variables --set` from off."""
        with _env(UNDX_DEFAULT_REQUEST_PRIVACY="RESTRICTED"):
            self.assertEqual(undx_privacy.default_request_privacy(), "RESTRICTED")
        for lowered in ("PUBLIC", "SYNTHETIC", "INTERNAL"):
            with _env(UNDX_DEFAULT_REQUEST_PRIVACY=lowered):
                self.assertEqual(undx_privacy.default_request_privacy(), "CONFIDENTIAL",
                                 f"{lowered} lowered the assumed sensitivity of unclassified traffic")

    def test_a_nonsense_override_falls_back_to_the_default(self):
        with _env(UNDX_DEFAULT_REQUEST_PRIVACY="banana"):
            self.assertEqual(undx_privacy.default_request_privacy(), "CONFIDENTIAL")


class RoutingEnforcementTest(unittest.TestCase):
    """The ceiling has to hold on every path into `CALLERS`, not just the tidy one."""

    def setUp(self):
        undx_router.reset_provider_health()

    def test_naming_a_provider_explicitly_does_not_bypass_the_ceiling(self):
        """`providers=[...]` skips `provider_priority` entirely.

        That is the parameter the health check uses, and it is exactly how a
        caller would accidentally pin private text to a search-grounded
        provider. If the check lived in `provider_priority` this test would fail.
        """
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return _FakeResponse(_chat())

        with _all_keys(), mock.patch.object(undx_router.requests, "post", fake_post):
            result = undx_router.route_structured_request(
                1, "sys", "summarise this DM", providers=["perplexity"],
                privacy_class="CONFIDENTIAL")

        self.assertFalse(result["ok"])
        self.assertEqual(calls, [], "a refused provider was still called")
        self.assertEqual([a["status"] for a in result["attempts"]], ["privacy_refused"])

    def test_disabling_the_router_does_not_bypass_the_ceiling(self):
        """With UNDX_ROUTER_ENABLED off the plan collapses to the default provider
        without consulting `provider_priority` either. A control that a kill
        switch turns off is not a control."""
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return _FakeResponse(_chat())

        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="gemini"), \
                mock.patch.object(undx_router.requests, "post", fake_post):
            result = undx_router.route_structured_request(
                1, "sys", "summarise this DM", privacy_class="RESTRICTED")

        self.assertFalse(result["ok"])
        self.assertEqual(calls, [], "a refused provider was still called")

    def test_a_permitted_provider_still_answers(self):
        """The companion to every refusal test. A ceiling that refuses everything
        passes all the tests above and is useless."""
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_DEFAULT_AI_PROVIDER="openai"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hello"))):
            result = undx_router.route_structured_request(
                1, "sys", "hello", providers=["openai"], privacy_class="CONFIDENTIAL")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["provider"], "openai")

    def test_refusal_is_reported_as_a_refusal_not_as_an_outage(self):
        """A policy decision and a dead provider need different responses.

        Reporting this as 502 sends an operator to check provider uptime, and
        the fix that ends that search is widening a ceiling to clear an alert.
        """
        with _all_keys(UNDX_ROUTER_ENABLED="1"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat())):
            result = undx_router.route_undx_request(1, "read my private office notes",
                                                    privacy_class="PRIVATE_OFFICE")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 403)
        self.assertIn("RESTRICTED", result["error"])
        self.assertTrue(result["router"]["attempts"])
        self.assertTrue(all(a["status"] == "privacy_refused"
                            for a in result["router"]["attempts"]))

    def test_an_outage_is_still_reported_as_an_outage(self):
        """The other half: the refusal wording must not swallow real failures."""
        with _all_keys(UNDX_ROUTER_ENABLED="1"), \
                mock.patch.object(undx_router.requests, "post",
                                  side_effect=undx_router.requests.Timeout("slow")):
            result = undx_router.route_undx_request(1, "hello", privacy_class="PUBLIC")
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["status"], 403)

    def test_the_refusal_names_both_heights(self):
        """"privacy_refused" alone sends the reader here to work out which side
        was the surprise. It is usually the request, not the provider."""
        with _all_keys():
            reason = undx_router._privacy_refusal("perplexity", "CONFIDENTIAL")
        self.assertIn("CONFIDENTIAL", reason)
        self.assertIn("PUBLIC", reason)
        self.assertIn("perplexity", reason)

    def test_a_refused_provider_costs_no_request_and_no_breaker_failure(self):
        """A refusal is not a fault. Counting it as one would rest a healthy
        provider after three private requests and then report it as down."""
        with _all_keys(), mock.patch.object(
                undx_router.requests, "post",
                side_effect=AssertionError("must not be called")):
            for _ in range(5):
                undx_router.route_structured_request(
                    1, "sys", "private", providers=["gemini"], privacy_class="RESTRICTED")
        health = undx_router.provider_runtime_health()
        self.assertNotIn("gemini", health,
                         "a privacy refusal was recorded as a provider failure")


class FreshnessDegradationTest(unittest.TestCase):
    """Two correct controls in conflict, and the rule that the loser says so.

    Perplexity leads `current_web` because it is the only provider that can see
    today's answer. Its ceiling is PUBLIC because the prompt becomes a live
    search query. A freshness question carrying anything private is therefore
    refused there and answered from training data instead - the precise failure
    `classify_request` exists to avoid, now reachable through the privacy
    control rather than through a routing mistake.

    Lowering the ceiling to fix that would trade a disclosed staleness for an
    undisclosed disclosure. So the answer still gets served, and the envelope
    admits it is not grounded.
    """

    FRESH = "what is the latest on stablecoin regulation"

    def setUp(self):
        undx_router.reset_provider_health()

    def test_the_lane_is_still_the_freshness_lane(self):
        """Guards the premise. If `current_web` stops leading with Perplexity
        this whole test class is asserting something that no longer happens."""
        self.assertEqual(undx_router.classify_request(self.FRESH)["category"], "current_web")
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1"):
            self.assertEqual(undx_router.provider_priority({"category": "current_web"})[0],
                             "perplexity")

    def test_a_private_freshness_question_is_answered_but_flagged_stale(self):
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("Rules changed in March."))):
            result = undx_router.route_undx_request(1, self.FRESH, privacy_class="CONFIDENTIAL")

        self.assertTrue(result["ok"], "the request should still be served")
        self.assertNotEqual(result["provider"], "perplexity")
        self.assertTrue(result["freshness_degraded"],
                        "a stale answer was returned with nothing to say so")
        self.assertIn("Perplexity", result["router"]["privacy"]["refused"])

    def test_a_public_freshness_question_is_not_flagged(self):
        """The companion. A flag that is always true carries no information."""
        payload = {"choices": [{"message": {"content": "Rules changed in March."},
                                "finish_reason": "stop"}],
                   "search_results": ["https://example.invalid/a"], "model": "sonar"}
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(payload)):
            result = undx_router.route_undx_request(1, self.FRESH, privacy_class="PUBLIC")

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "perplexity")
        self.assertFalse(result["freshness_degraded"])
        self.assertEqual(result["router"]["privacy"]["refused"], [])

    def test_an_ordinary_question_is_never_flagged_stale(self):
        """Only the freshness lane can be degraded by losing grounding."""
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_DEFAULT_AI_PROVIDER="openai"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hello"))):
            result = undx_router.route_undx_request(1, "say hello", privacy_class="CONFIDENTIAL")
        self.assertTrue(result["ok"])
        self.assertFalse(result["freshness_degraded"])

    def test_the_envelope_records_whether_the_caller_classified(self):
        """`declared_by_caller` separates "this is CONFIDENTIAL" from "nobody said".

        Both route identically today. Only one of them is a decision, and the
        difference is what tells us whether callers have been migrated.
        """
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_DEFAULT_AI_PROVIDER="openai"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hello"))):
            silent = undx_router.route_undx_request(1, "say hello")
            spoken = undx_router.route_undx_request(1, "say hello", privacy_class="CONFIDENTIAL")

        self.assertEqual(silent["router"]["privacy"]["class"], "CONFIDENTIAL")
        self.assertFalse(silent["router"]["privacy"]["declared_by_caller"])
        self.assertTrue(spoken["router"]["privacy"]["declared_by_caller"])


if __name__ == "__main__":
    unittest.main()
