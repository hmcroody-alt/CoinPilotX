"""The non-chat capability table (§20-27), and the distinctions it has to keep.

Most of these tests exist to pin a *difference* rather than a value, because the
table's whole purpose is to stop three pairs of things from collapsing into each
other: unknown price vs. free, declared-empty vs. absent, and paid vs. actually
billed. Each collapse reads as a tidier table and loses the finding that motivated
it.
"""

from __future__ import annotations

import unittest

from services import undx_capabilities as cap
from services import undx_cost, undx_embedding_service


class TableShapeTest(unittest.TestCase):
    def test_every_kind_is_a_call_kind_the_ledger_recognises(self):
        """The two vocabularies must be the same vocabulary.

        A capability named `embeddings` while the ledger column says `embedding`
        would meter perfectly and report into a bucket no budget reads. The
        constants are imported rather than retyped precisely so this cannot drift,
        and this asserts that the import is what is happening.
        """
        for kind in cap.CAPABILITIES:
            with self.subTest(kind=kind):
                self.assertTrue(
                    undx_cost.is_known_call_kind(kind),
                    f"{kind!r} is not a call kind the ledger will accept",
                )

    def test_chat_is_deliberately_absent(self):
        """Chat belongs to `undx_router.PROVIDERS`, and must not be described twice.

        This is the one entry whose absence is a design decision rather than an
        omission: `ProviderConfig` already carries the chat model, its structured
        output dialect and its reasoning overhead, and a second description of the
        same providers here is how the two would start disagreeing about which
        model is the default.
        """
        self.assertNotIn(undx_cost.CALL_KIND_CHAT, cap.CAPABILITIES)
        self.assertNotIn(undx_cost.CALL_KIND_UNKNOWN, cap.CAPABILITIES)

    def test_kinds_with_no_providers_are_declared_rather_than_missing(self):
        """Empty and absent answer different questions.

        An absent key cannot distinguish "this codebase does not transcribe
        anything" from "nobody looked". The census had to establish that
        separately for each, so the result is recorded as an empty tuple - which
        also means a future adapter shows up as a change to this table instead of
        as a new untracked line item.
        """
        for kind in (undx_cost.CALL_KIND_TRANSCRIPTION, undx_cost.CALL_KIND_RERANK,
                     undx_cost.CALL_KIND_MODERATION):
            with self.subTest(kind=kind):
                self.assertIn(kind, cap.CAPABILITIES)
                self.assertEqual(cap.CAPABILITIES[kind].providers, ())

    def test_every_provider_declares_a_pricing_unit(self):
        """Four kinds bill on four different things.

        A metering call site that assumed tokens would be wrong by six orders of
        magnitude on a per-image charge, and wrong in the cheap direction, which
        is the one nobody investigates.
        """
        for kind, entry in cap.CAPABILITIES.items():
            for provider in entry.providers:
                with self.subTest(kind=kind, provider=provider.name):
                    self.assertIn(provider.pricing_unit,
                                  {cap.UNIT_MILLION_TOKENS, cap.UNIT_MILLION_CHARACTERS,
                                   cap.UNIT_IMAGE, cap.UNIT_QUERY})

    def test_a_priced_provider_says_where_the_price_came_from(self):
        """A number with no provenance cannot be distinguished from a guess.

        This repository has already been bitten by model names that outlived their
        source; a price is the same hazard with a dollar attached.
        """
        for kind, entry in cap.CAPABILITIES.items():
            for provider in entry.providers:
                if provider.prices:
                    with self.subTest(kind=kind, provider=provider.name):
                        self.assertTrue(provider.price_source.strip())

    def test_an_unpriced_provider_does_not_claim_a_source(self):
        """The converse, so `price_source` cannot become decorative."""
        for kind, entry in cap.CAPABILITIES.items():
            for provider in entry.providers:
                if not provider.prices:
                    with self.subTest(kind=kind, provider=provider.name):
                        self.assertEqual(provider.price_source, "")


class UnknownIsNotFreeTest(unittest.TestCase):
    """§34, which is the rule this table exists to make mechanical."""

    def test_an_unpriced_provider_costs_none_not_zero(self):
        self.assertIsNone(cap.price_micro_usd(undx_cost.CALL_KIND_IMAGE, "openai", 1))
        self.assertIsNone(cap.price_micro_usd(undx_cost.CALL_KIND_RESEARCH, "tavily", 1))
        self.assertIsNone(
            cap.price_micro_usd(undx_cost.CALL_KIND_TRANSLATION, "google", 1_000_000))

    def test_a_genuinely_free_provider_costs_zero_and_that_is_different(self):
        """Known-zero and unknown must not share a representation.

        §34 forbids reporting an unknown as free. It does not require pretending a
        free thing might be expensive, and flattening the two would lose the fact
        that DuckDuckGo is the only search provider that has ever returned a
        result in production - it would be the one missing from the ledger.
        """
        free = cap.price_micro_usd(undx_cost.CALL_KIND_RESEARCH, "duckduckgo_instant", 1)
        unknown = cap.price_micro_usd(undx_cost.CALL_KIND_RESEARCH, "tavily", 1)
        self.assertEqual(free, 0)
        self.assertIsNone(unknown)
        self.assertIsNot(free, unknown)

    def test_a_priced_provider_with_an_unpriced_model_is_still_unknown(self):
        """Falling back to a sibling model's rate would report a measurement nobody made.

        The embedding provider has four known models at rates spanning 12x. An
        unrecognised fifth is not "probably like the others"; for *reporting* it is
        unknown. The budget guard in `undx_embedding_service` rounds the same
        unknown the other way on purpose, and the next test pins that.
        """
        self.assertIsNone(cap.price_micro_usd(
            undx_cost.CALL_KIND_EMBEDDING, "perplexity", 1_000_000,
            model="pplx-embed-v99"))

    def test_the_same_unknown_rounds_up_for_blocking_and_none_for_reporting(self):
        """The two directions, asserted together because neither is right alone.

        Reporting an unknown as the worst case invents spend; blocking on an
        unknown of zero fails open. So `undx_embedding_service` charges the highest
        known rate when deciding whether to refuse a call, and the capability table
        answers `None` when describing what was spent. A future simplification that
        unified them would break one of the two, and it would look like cleanup.
        """
        reporting = cap.price_micro_usd(
            undx_cost.CALL_KIND_EMBEDDING, "perplexity", 1_000_000, model="pplx-embed-v99")
        blocking = undx_embedding_service.estimated_cost_usd(1_000_000, model="pplx-embed-v99")
        self.assertIsNone(reporting)
        self.assertEqual(blocking, undx_embedding_service._UNKNOWN_MODEL_PRICE_USD)
        self.assertGreater(blocking, 0)

    def test_unknown_kinds_and_providers_are_unknown_not_free(self):
        self.assertIsNone(cap.price_micro_usd("embedding", "no-such-provider", 1))
        self.assertIsNone(cap.price_micro_usd("no-such-kind", "perplexity", 1))

    def test_unpriced_providers_enumerates_the_remaining_work(self):
        """"No unclassified spend" is reachable from inside the repo; "no unpriced
        spend" is not - it needs published prices read on a date. So the gap is
        enumerable rather than invisible, one entry per module that would otherwise
        hide its own missing price."""
        unpriced = dict.fromkeys(cap.unpriced_providers())
        self.assertIn((undx_cost.CALL_KIND_IMAGE, "openai"), unpriced)
        self.assertIn((undx_cost.CALL_KIND_TRANSLATION, "google"), unpriced)
        for paid_search in ("brave", "bing", "serpapi", "tavily"):
            self.assertIn((undx_cost.CALL_KIND_RESEARCH, paid_search), unpriced)
        # The two that are known, and so must not appear.
        self.assertNotIn((undx_cost.CALL_KIND_RESEARCH, "duckduckgo_instant"), unpriced)
        self.assertNotIn((undx_cost.CALL_KIND_EMBEDDING, "perplexity"), unpriced)


class PriceArithmeticTest(unittest.TestCase):
    def test_million_token_rates_convert_to_micro_usd(self):
        """$0.004 per million tokens, so a million tokens is 4000 micro-USD."""
        self.assertEqual(
            cap.price_micro_usd(undx_cost.CALL_KIND_EMBEDDING, "perplexity",
                                1_000_000, model="pplx-embed-v1-0.6b"),
            4000)
        self.assertEqual(
            cap.price_micro_usd(undx_cost.CALL_KIND_EMBEDDING, "perplexity",
                                1_000_000, model="pplx-embed-v1-4b"),
            30_000)

    def test_micro_usd_is_fine_enough_for_a_realistic_single_call(self):
        """Integer micro-USD is the reason the ledger can add these up at all.

        The first version of this test asserted that a thousand tokens rounds away
        to nothing, and it was wrong: at $0.004 per million that is exactly 4
        micro-USD, comfortably representable. Worth keeping the corrected figure,
        because "the unit is too coarse for our smallest call" was a plausible
        objection to integer accounting and it turns out not to hold - a realistic
        embedding call is tens of units, not a fraction of one.
        """
        self.assertEqual(
            cap.price_micro_usd(undx_cost.CALL_KIND_EMBEDDING, "perplexity",
                                1_000, model="pplx-embed-v1-0.6b"),
            4)

    def test_a_genuinely_sub_unit_charge_is_zero_and_still_not_unknown(self):
        """One token costs four thousandths of a micro-USD, which floors to 0.

        That 0 is correct and it is *not* the same as an unknown price: the call is
        counted, and its cost is genuinely below the unit of account rather than
        unmeasured. The pair matters because `uncosted_calls` exists to say "this
        dollar total excludes N calls", and a sub-unit call must not inflate that
        count - it is costed, at zero.
        """
        floored = cap.price_micro_usd(undx_cost.CALL_KIND_EMBEDDING, "perplexity",
                                      1, model="pplx-embed-v1-0.6b")
        self.assertEqual(floored, 0)
        self.assertIsNotNone(floored)

    def test_per_query_and_per_image_units_are_not_divided_by_a_million(self):
        """The unit table is what stops a per-image charge being read as per-token."""
        self.assertEqual(
            cap.price_micro_usd(undx_cost.CALL_KIND_RESEARCH, "duckduckgo_instant", 5), 0)
        entry = cap.provider_for(undx_cost.CALL_KIND_IMAGE, "openai")
        self.assertEqual(entry.pricing_unit, cap.UNIT_IMAGE)


class ConfigurationTest(unittest.TestCase):
    def test_bing_accepts_either_of_its_two_documented_variable_names(self):
        """`pulse_ai_web_search` reads `BING_SEARCH_API_KEY or
        BING_SEARCH_V7_SUBSCRIPTION_KEY`. Collapsing that to one name here would
        make the table disagree with the code it claims to describe."""
        self.assertEqual(
            cap.configured_providers(undx_cost.CALL_KIND_RESEARCH,
                                     env={"BING_SEARCH_API_KEY": "k"}),
            ("bing", "duckduckgo_instant"))
        self.assertEqual(
            cap.configured_providers(undx_cost.CALL_KIND_RESEARCH,
                                     env={"BING_SEARCH_V7_SUBSCRIPTION_KEY": "k"}),
            ("bing", "duckduckgo_instant"))

    def test_a_keyless_provider_is_always_reachable(self):
        """Not an oversight in the table - it is what keyless means. A filter that
        required a credential would drop the only search provider that works."""
        self.assertEqual(
            cap.configured_providers(undx_cost.CALL_KIND_RESEARCH, env={}),
            ("duckduckgo_instant",))

    def test_blank_and_whitespace_credentials_do_not_count_as_configured(self):
        for value in ("", "   ", "\n"):
            with self.subTest(value=repr(value)):
                self.assertEqual(
                    cap.configured_providers(undx_cost.CALL_KIND_RESEARCH,
                                             env={"TAVILY_API_KEY": value}),
                    ("duckduckgo_instant",))

    def test_the_names_no_code_reads_are_not_in_this_table(self):
        """`Tavily_AI_API` and `Serper_AI_API` are funded Railway variables that
        appear in zero files (census R-c). Adding them here would make the table
        claim a reachable provider that no adapter can reach, which is the same
        defect one layer up - and `Serper_AI_API` additionally names a different
        company from SerpApi, so it is not a rename.
        """
        declared = {name for entry in cap.CAPABILITIES.values()
                    for provider in entry.providers for name in provider.key_envs}
        self.assertNotIn("Tavily_AI_API", declared)
        self.assertNotIn("Serper_AI_API", declared)
        self.assertIn("TAVILY_API_KEY", declared)

    def test_search_providers_are_declared_in_the_order_the_adapter_tries_them(self):
        """The table is a description of running code, so the order is part of it.

        `pulse_ai_web_search.search()` iterates brave, bing, serpapi, tavily,
        duckduckgo. A table in a different order would silently misreport which
        provider a given query was expected to reach.
        """
        names = [p.name for p in cap.CAPABILITIES[undx_cost.CALL_KIND_RESEARCH].providers]
        self.assertEqual(names, ["brave", "bing", "serpapi", "tavily", "duckduckgo_instant"])

    def test_model_env_overrides_the_default_model(self):
        entry = cap.provider_for(undx_cost.CALL_KIND_EMBEDDING, "perplexity")
        self.assertEqual(entry.configured_model(env={}), "pplx-embed-v1-0.6b")
        self.assertEqual(
            entry.configured_model(env={"UNDX_EMBEDDING_MODEL": "pplx-embed-v1-4b"}),
            "pplx-embed-v1-4b")


class NoDuplicateDefaultsTest(unittest.TestCase):
    """§20-27 asks for the duplicated model defaults to go. This is the check that
    the embedding adapter reads the table instead of carrying its own copy."""

    def test_the_embedding_adapter_sources_its_constants_from_the_table(self):
        entry = cap.provider_for(undx_cost.CALL_KIND_EMBEDDING, "perplexity")
        self.assertEqual(undx_embedding_service.DEFAULT_MODEL, entry.default_model)
        self.assertEqual(undx_embedding_service.DEFAULT_ENDPOINT, entry.endpoint)
        self.assertEqual(undx_embedding_service.API_KEY_ENV, entry.key_envs[0])
        self.assertEqual(undx_embedding_service.PRICE_PER_MILLION_TOKENS_USD,
                         entry.prices)

    def test_the_adapters_price_table_is_a_copy_not_the_table_itself(self):
        """Sharing the values is the point; sharing the *object* is not.

        A module that mutated its own price dict - a test setting a rate, most
        likely - must not reach through and change what every other kind is
        costed at.
        """
        self.assertIsNot(undx_embedding_service.PRICE_PER_MILLION_TOKENS_USD,
                         cap.provider_for(undx_cost.CALL_KIND_EMBEDDING,
                                          "perplexity").prices)

    def test_the_default_model_is_unchanged_because_it_is_a_cache_key_component(self):
        """Pinned as a literal exactly once, here, and deliberately.

        Every stored vector's cache key contains this string. Sourcing the constant
        from the table removed three duplicate literals, but if that refactor had
        altered the value by one character it would have orphaned every cached
        vector in production with no error anywhere - a silent re-embedding bill
        rather than a failure. So one literal survives, in the test, where a change
        to it has to be deliberate.
        """
        self.assertEqual(undx_embedding_service.DEFAULT_MODEL, "pplx-embed-v1-0.6b")
        self.assertEqual(undx_embedding_service.DEFAULT_ENDPOINT,
                         "https://api.perplexity.ai/v1/embeddings")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
