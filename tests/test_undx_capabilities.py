"""The non-chat capability table (§20-27), and the distinctions it has to keep.

Most of these tests exist to pin a *difference* rather than a value, because the
table's whole purpose is to stop three pairs of things from collapsing into each
other: unknown price vs. free, declared-empty vs. absent, and paid vs. actually
billed. Each collapse reads as a tidier table and loses the finding that motivated
it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

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


class RecordSpendTest(unittest.TestCase):
    """`record_spend` against a real ledger file, because the claim being made is
    about what a spend report will say — not about what the pricing function
    returns, which :class:`PriceArithmeticTest` already covers.

    Every assertion here reads the ledger back through `month_snapshot`, since
    that is the only path any consumer uses. A test that asserted on
    `record_spend`'s return value alone would pass with the upsert broken.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        undx_cost.reset_for_tests()
        env = mock.patch.dict(
            os.environ,
            {"DATABASE_URL": "sqlite:///" + os.path.join(self._dir.name, "ledger.db")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(undx_cost.reset_for_tests)

    def _kinds(self):
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["source"], "ledger",
                         "these assertions are about the durable row, not the mirror")
        return snapshot["kinds"]

    #: A priced model that is deliberately *not* the configured default. The
    #: default is pinned as a literal exactly once in this file, inside
    #: :class:`NoDuplicateDefaultsTest`, because it is a component of the
    #: embedding cache key; repeating it here would make that pin stop being the
    #: single place a rename has to be noticed. Using a non-default model also
    #: exercises the branch that looks a model up rather than falling through to
    #: a single unnamed rate.
    PRICED_MODEL = "pplx-embed-context-v1-4b"  # $0.05 per million tokens

    def test_a_priced_call_lands_as_money_under_its_own_kind(self):
        """Perplexity embeddings are the only priced non-chat provider in the
        table, so this is the one place the money can be asserted rather than
        just the row. 1,000,000 tokens at $0.05/M = $0.05 = 50,000 micro-USD."""
        cap.record_spend(cap.CALL_KIND_EMBEDDING, "perplexity",
                         units=1_000_000, model=self.PRICED_MODEL,
                         input_tokens=1_000_000)
        row = self._kinds()[cap.CALL_KIND_EMBEDDING]
        self.assertEqual((row["calls"], row["cost_micro_usd"], row["uncosted_calls"]),
                         (1, 50_000, 0))
        self.assertEqual(row["input_tokens"], 1_000_000)

    def test_an_unpriced_provider_is_recorded_and_counted_as_uncosted(self):
        """§34, at the point where it would be easiest to cheat. `gpt-image-1`
        has no published price in this table, and the tempting shapes are both
        wrong: skipping the call loses the count, and pricing it at 0 reports a
        free image. The row exists, contributes nothing to the dollar total, and
        says so."""
        cap.record_spend(cap.CALL_KIND_IMAGE, "openai", units=1, model="gpt-image-1")
        row = self._kinds()[cap.CALL_KIND_IMAGE]
        self.assertEqual((row["calls"], row["cost_micro_usd"], row["uncosted_calls"]),
                         (1, 0, 1))

    def test_free_and_unknown_stay_distinguishable_within_one_kind(self):
        """Both DuckDuckGo and Tavily are `research`, both add $0.00, and only
        `uncosted_calls` separates them. Asserted inside a single kind because
        that is where they are summed together and where the distinction would
        actually be lost — two calls, one dollar total of zero, but exactly one
        of them unknown.
        """
        cap.record_spend(cap.CALL_KIND_RESEARCH, "duckduckgo_instant", units=1)
        cap.record_spend(cap.CALL_KIND_RESEARCH, "tavily", units=1)
        kinds = self._kinds()[cap.CALL_KIND_RESEARCH]
        self.assertEqual((kinds["calls"], kinds["cost_micro_usd"],
                          kinds["uncosted_calls"]), (2, 0, 1))

        providers = undx_cost.month_snapshot()["providers"]
        self.assertEqual(providers["duckduckgo_instant"]["uncosted_calls"], 0)
        self.assertEqual(providers["tavily"]["uncosted_calls"], 1)

    def test_a_provider_not_in_the_table_is_still_recorded(self):
        """Fails open on the *accounting* side deliberately. An adapter calling
        with a name the table has not heard of is a table defect, and the useful
        outcome is a row flagged unknown-price that someone can find, not a
        silently dropped call. `unpriced_providers()` is the declared-gap list;
        this is the undeclared-gap case."""
        cap.record_spend(cap.CALL_KIND_RESEARCH, "some_new_search_api", units=3)
        row = self._kinds()[cap.CALL_KIND_RESEARCH]
        self.assertEqual((row["calls"], row["uncosted_calls"]), (1, 1))

    def test_an_unrecognised_kind_is_not_laundered_into_chat(self):
        """The failure §22 is about. A typo in a `call_kind` must not make
        non-chat spend arrive inside the chat total, where it would be invisible
        precisely because chat is the number everyone already looks at."""
        cap.record_spend("embeddings", "perplexity", units=1_000)
        kinds = self._kinds()
        self.assertIn(undx_cost.CALL_KIND_UNKNOWN, kinds)
        self.assertNotIn(undx_cost.CALL_KIND_CHAT, kinds)

    def test_one_provider_billed_under_two_kinds_accumulates_separately(self):
        """OpenAI is the realistic case: images today, and the obvious next
        non-chat kinds (transcription, moderation) are declared empty against it
        already. The ledger's key is (month, provider, kind), so this is the
        assertion that the third column is actually part of it."""
        cap.record_spend(cap.CALL_KIND_IMAGE, "openai", units=1, model="gpt-image-1")
        cap.record_spend(cap.CALL_KIND_IMAGE, "openai", units=1, model="gpt-image-1")
        cap.record_spend(cap.CALL_KIND_MODERATION, "openai", units=1)
        kinds = self._kinds()
        self.assertEqual(kinds[cap.CALL_KIND_IMAGE]["calls"], 2)
        self.assertEqual(kinds[cap.CALL_KIND_MODERATION]["calls"], 1)
        self.assertEqual(undx_cost.month_snapshot()["providers"]["openai"]["calls"], 3,
                         "the provider axis must still be the total across kinds")

    def test_a_reported_cost_beats_the_table(self):
        """The table is a price someone read on a date; a report is a measurement
        of the call. Perplexity's embeddings response carries one, so this is a
        real input. Asserted with a figure the table cannot produce for these
        units, so a green result cannot mean the two happened to agree."""
        cap.record_spend(cap.CALL_KIND_EMBEDDING, "perplexity",
                         units=1_000_000, model=self.PRICED_MODEL,
                         reported_cost_usd=0.02)
        row = self._kinds()[cap.CALL_KIND_EMBEDDING]
        self.assertEqual((row["cost_micro_usd"], row["uncosted_calls"]), (20_000, 0))

    def test_a_reported_zero_is_a_measured_zero(self):
        """Distinct from the table returning None. A provider stating it charged
        nothing has told us something, and it must not be laundered into the
        unpriced column — nor must `0.0` be mistaken for "no report" by a falsy
        check, which is the bug this pins."""
        cap.record_spend(cap.CALL_KIND_IMAGE, "openai", units=1,
                         model="gpt-image-1", reported_cost_usd=0.0)
        row = self._kinds()[cap.CALL_KIND_IMAGE]
        self.assertEqual((row["cost_micro_usd"], row["uncosted_calls"]), (0, 0),
                         "an unpriced provider that reports $0 is costed, not unknown")

    def test_an_unusable_report_falls_back_to_the_table_not_to_unknown(self):
        """One answer to "the report is unusable", shared with the embedding
        adapter's `_reported_cost_usd`, which returns None for the same inputs and
        so arrives here as no report at all. If this recorded unknown instead, the
        same garbled response would cost differently depending on which of the two
        layers noticed it first.

        Negative is in the list because a refund is not representable here and
        subtracting it would understate the month.

        Asserted as a *delta* per subtest. `reset_for_tests()` clears the process
        mirror but not the ledger file, so the absolute figure grows by 50,000 each
        time round and a fixed expectation would pass only on the first iteration —
        which is how the first version of this test failed.
        """
        for bad in ("not a number", object(), -5.0):
            with self.subTest(reported=bad):
                before = self._kinds().get(cap.CALL_KIND_EMBEDDING) or {}
                cap.record_spend(cap.CALL_KIND_EMBEDDING, "perplexity",
                                 units=1_000_000, model=self.PRICED_MODEL,
                                 reported_cost_usd=bad)
                after = self._kinds()[cap.CALL_KIND_EMBEDDING]
                self.assertEqual(
                    after["cost_micro_usd"] - before.get("cost_micro_usd", 0), 50_000)
                self.assertEqual(
                    after["uncosted_calls"] - before.get("uncosted_calls", 0), 0)

    def test_an_unusable_report_on_an_unpriced_provider_is_still_unknown(self):
        """The fallback is to the table, not to zero — so when the table has
        nothing either, the call is unknown. Both halves of the rule in one place,
        because "fall back to the table" read on its own could be implemented as
        "fall back to 0" and pass every other test in this class."""
        cap.record_spend(cap.CALL_KIND_IMAGE, "openai", units=1,
                         model="gpt-image-1", reported_cost_usd="???")
        row = self._kinds()[cap.CALL_KIND_IMAGE]
        self.assertEqual((row["cost_micro_usd"], row["uncosted_calls"]), (0, 1))

    def test_a_bookkeeping_failure_does_not_raise_into_the_caller(self):
        """Adapters call this after the provider has already been paid. If it
        could throw, metering a working request would be a way to break it."""
        with mock.patch.object(undx_cost, "_connect", side_effect=RuntimeError("gone")):
            result = cap.record_spend(cap.CALL_KIND_EMBEDDING, "perplexity",
                                      units=1_000_000, model=self.PRICED_MODEL)
        self.assertEqual(result["cost_micro_usd"], 50_000,
                         "the mirror still has to know what was spent")
        self.assertGreaterEqual(undx_cost.stats()["write_failures"], 1)

    def test_zero_units_is_a_call_at_zero_cost_not_an_unknown(self):
        """A priced provider asked for nothing costs nothing, and that is known.
        Distinguished from the unknown case because a retry loop that records
        units=0 on a failed attempt would otherwise inflate `uncosted_calls` and
        make the priced provider look unpriced."""
        cap.record_spend(cap.CALL_KIND_EMBEDDING, "perplexity",
                         units=0, model=self.PRICED_MODEL)
        row = self._kinds()[cap.CALL_KIND_EMBEDDING]
        self.assertEqual((row["calls"], row["cost_micro_usd"], row["uncosted_calls"]),
                         (1, 0, 0))

    def test_an_unknown_model_on_a_priced_provider_is_uncosted(self):
        """Written because the first draft of this class got it wrong: every
        money assertion used a model name that does not exist, and all of them
        failed with `uncosted_calls=1`. The behaviour was correct and the test
        was not — a priced provider does not make its models priced, and falling
        back to a sibling's rate would report a figure nobody measured.

        Pinned with the same invented name, because the value of the finding is
        that a model *rename* (a new Perplexity generation, a typo in an env
        override) silently moves spend into the unpriced column instead of
        raising. `unpriced_providers()` will not show it either — the provider is
        priced. This is the one uncosted case with no declared home, and the only
        signal is the count itself going up.
        """
        cap.record_spend(cap.CALL_KIND_EMBEDDING, "perplexity",
                         units=1_000_000, model="pplx-embed-large")
        row = self._kinds()[cap.CALL_KIND_EMBEDDING]
        self.assertEqual((row["calls"], row["cost_micro_usd"], row["uncosted_calls"]),
                         (1, 0, 1))
        self.assertNotIn((cap.CALL_KIND_EMBEDDING, "perplexity"),
                         cap.unpriced_providers())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
