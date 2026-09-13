#!/usr/bin/env python3
"""Prove the spend-accounting tests fail when the accounting is broken.

§50, applied to money. The tests in `tests/test_undx_cost_budget.py` and
`tests/test_undx_capabilities.py` assert things like "this call contributed zero
dollars", and an assertion that a number is zero is the easiest kind in this repo
to satisfy by accident — a ledger that records nothing at all passes most of them.
So every mutation here is paired with a test that distinguishes **zero because we
measured zero** from **zero because we do not know**, which is the one distinction
§34 exists to protect and the one that reads as harmless cleanup when removed.

A separate harness from `undx_classification_subject_mutation_check.py` on purpose.
That script's subject is which provider a request reaches; this one's is what a
month's spend report says. Sharing the `MUTATIONS` list would mean every
accounting change reran fourteen routing suites to learn nothing, and the reverse.
`build_sandbox` *is* shared, by import rather than by copy, for the reason written
in its own docstring: a duplicated copy with a depth-two assumption once truncated
real files in the working tree.

Three of these mutations are shapes that would plausibly survive code review:

  * `cost_micro or 0` reads as defensive coercion. It converts every unknown price
    into a measured $0.00, which is precisely the claim §34 forbids, and it does it
    without changing a single dollar total — only the count of calls the total
    excludes.
  * Returning early when the price is unknown reads as "do not record garbage". It
    drops the call count for every unpriced provider, so `image` and `translation`
    spend would report as *no calls made* rather than as unpriced calls.
  * Delegating the dollar branch to `to_micro_usd` reads as removing duplication.
    That function returns 0 for unparseable input by documented contract and leaves
    the uncosted decision to its caller, so delegating makes a malformed price free
    in one input form and unknown in the other.

Run: .venv/bin/python3 scripts/undx_spend_accounting_mutation_check.py
     .venv/bin/python3 scripts/undx_spend_accounting_mutation_check.py --only "known zero"
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

COST = "services/undx_cost.py"
CAPS = "services/undx_capabilities.py"
EMBED = "services/undx_embedding_service.py"
IMAGE = "services/pulse_ai/automated_image_pipeline.py"
SEARCH = "services/pulse_ai_web_search.py"
TRANS = "services/translation_providers.py"

COST_TESTS = "tests/test_undx_cost_budget.py"
CAPS_TESTS = "tests/test_undx_capabilities.py"
EMBED_TESTS = "tests/undx_agent/test_embedding_wire_contract.py"
IMAGE_TESTS = "tests/test_pulse_insight_image_pipeline.py"
SEARCH_TESTS = "tests/test_pulse_ai_web_search_spend.py"
TRANS_TESTS = "tests/test_translation_spend.py"

#: (label, file, old, new, test that must fail, test file)
MUTATIONS = [
    (
        # The collapse this whole column exists to prevent. DuckDuckGo is keyless
        # and free, so its 0 is a measurement; gpt-image-1 has no published price,
        # so its 0 would be a guess. `if micro:` makes them the same row.
        "cost: treat a known-zero price as an unknown one",
        COST,
        '    micro = usage.get("cost_micro_usd")\n    if micro is not None:\n',
        '    micro = usage.get("cost_micro_usd")\n    if micro:\n',
        "test_a_known_zero_and_an_unknown_price_are_not_the_same_row",
        COST_TESTS,
    ),
    (
        # Looks like deduplication. `to_micro_usd` returns 0 for junk by contract
        # and documents that its caller owns the uncosted decision, so this makes
        # an unparseable dollar price free while an unparseable micro price stays
        # unknown - the two input forms disagreeing about the same bad input.
        "cost: delegate the dollar branch to to_micro_usd",
        COST,
        '    try:\n        return int(round(float(usd) * MICRO_PER_USD)), 0\n'
        '    except (TypeError, ValueError):\n        return 0, 1\n',
        '    return to_micro_usd(usd), 0\n',
        "test_a_malformed_price_is_unknown_in_both_forms",
        COST_TESTS,
    ),
    (
        # The pre-refactor state, restored. `_apply` feeds the process mirror and
        # `record` feeds the durable row; deriving the pair separately means the
        # mirror never learns the micro form, so during a database outage the
        # degraded path reports non-chat spend as $0.00 uncosted while the durable
        # path had been recording it as money. Visible only during an outage.
        "cost: let the process mirror derive its own cost again",
        COST,
        'def _apply(bucket: dict[str, int], usage: dict[str, Any]) -> None:\n'
        '    cost_micro, uncosted = _cost_fields(usage)\n',
        'def _apply(bucket: dict[str, int], usage: dict[str, Any]) -> None:\n'
        '    cost_micro = to_micro_usd(usage.get("cost_usd"))\n'
        '    uncosted = 1 if usage.get("cost_usd") is None else 0\n',
        "test_the_mirror_and_the_ledger_agree_about_the_micro_form",
        COST_TESTS,
    ),
    (
        # Unrecognised is not chat. Mapping it there hides non-chat spend inside
        # the one number everybody already reads, which is worse than a column
        # named `unknown` that looks like the defect it is.
        "cost: normalise an unrecognised call kind to chat",
        COST,
        '    return text if text in KNOWN_CALL_KINDS else CALL_KIND_UNKNOWN\n',
        '    return text if text in KNOWN_CALL_KINDS else CALL_KIND_CHAT\n',
        "test_an_unrecognised_kind_is_not_laundered_into_chat",
        CAPS_TESTS,
    ),
    (
        # Reads as defensive coercion against a None the type checker complained
        # about. Changes no dollar total anywhere - it only stops the ledger from
        # recording that the total excludes this call.
        "capabilities: coerce an unknown price to zero before recording",
        CAPS,
        '        "cost_micro_usd": cost_micro,\n',
        '        "cost_micro_usd": cost_micro or 0,\n',
        "test_an_unpriced_provider_is_recorded_and_counted_as_uncosted",
        CAPS_TESTS,
    ),
    (
        # Reads as "do not write rows we cannot price". Loses the call count for
        # every unpriced provider, so image and translation spend would report as
        # no calls rather than as unpriced calls - the gap becomes invisible
        # instead of merely unquantified.
        "capabilities: skip recording when the price is unknown",
        CAPS,
        '    cost_micro = price_micro_usd(kind, provider, units, model=model)\n',
        '    cost_micro = price_micro_usd(kind, provider, units, model=model)\n'
        '    if cost_micro is None:\n        return {}\n',
        "test_an_unpriced_provider_is_recorded_and_counted_as_uncosted",
        CAPS_TESTS,
    ),
    (
        # The tempting fix for the case that caught the author of these tests: a
        # model name the table has not heard of. Guessing the cheapest sibling
        # rate reports a figure nobody measured, and reports it as measured.
        "capabilities: price an unknown model at its cheapest sibling's rate",
        CAPS,
        '    if rate is None:\n        # The provider is priced but this *model* is not.',
        '    if rate is None:\n        rate = min(entry.prices.values())\n'
        '    if False:\n        # The provider is priced but this *model* is not.',
        "test_an_unknown_model_on_a_priced_provider_is_uncosted",
        CAPS_TESTS,
    ),
    (
        # Must stay GREEN. The distinction between a known zero and an unknown
        # price is stated in prose immediately above the code that implements it,
        # and prose cannot fail - so if rewording it turns anything red, a test
        # is matching on a comment instead of on behaviour.
        "capabilities: reword the comment about the load-bearing None (must stay GREEN)",
        CAPS,
        '        # None here is load-bearing and must not become 0: it is what makes the\n',
        '        # Passing None through matters here and it must not turn into 0: that is\n',
        None,
        CAPS_TESTS,
    ),
    (
        # `if reported_cost_usd:` reads identically to `is not None` at a glance and
        # differs on exactly one value. A provider stating it charged nothing has
        # told us something; treating 0.0 as "said nothing" moves a measured zero
        # into the unpriced column, which is the same collapse as the first
        # mutation in this list arriving from the other direction.
        "capabilities: read a reported zero as no report at all",
        CAPS,
        '    if reported_cost_usd is not None:\n',
        '    if reported_cost_usd:\n',
        "test_a_reported_zero_is_a_measured_zero",
        CAPS_TESTS,
    ),
    (
        # Precedence inverted. The table is a price someone read on a date and the
        # report is a measurement of this call, so trusting the table means a price
        # change nobody has noticed yet makes the ledger quietly wrong while every
        # test about "is it priced" stays green.
        "capabilities: prefer the price table over the provider's own figure",
        CAPS,
        '            reported_usable = cost_micro >= 0\n',
        '            reported_usable = False\n',
        "test_a_reported_cost_beats_the_table",
        CAPS_TESTS,
    ),
    (
        # The disagreement this branch was written to remove. The embedding adapter
        # returns None for an unusable report and so lands on the table; if this
        # layer recorded unknown instead, the same garbled response would be costed
        # differently depending on which layer noticed it, and only one of the two
        # is covered by any caller's tests.
        "capabilities: record an unusable report as unknown instead of using the table",
        CAPS,
        '    if not reported_usable:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model)\n',
        '    if reported_cost_usd is None:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model)\n',
        "test_an_unusable_report_falls_back_to_the_table_not_to_unknown",
        CAPS_TESTS,
    ),
    (
        # "Fall back to the table" implemented as "fall back to zero" - the shape
        # that passes every priced-provider test in the suite and silently reports
        # unpriced image spend as free.
        "capabilities: fall back to zero rather than to the table",
        CAPS,
        '    if not reported_usable:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model)\n',
        '    if not reported_usable:\n        cost_micro = price_micro_usd('
        'kind, provider, units, model=model) or 0\n',
        "test_an_unusable_report_on_an_unpriced_provider_is_still_unknown",
        CAPS_TESTS,
    ),
    (
        # The state this adapter was in before this phase: the provider reports what
        # it charged and the adapter throws it away, pricing from a table instead.
        "embedding: discard the provider's reported cost again",
        EMBED,
        '                reported_cost_usd=_reported_cost_usd(body),\n',
        '',
        "test_the_provider_reported_cost_beats_the_price_table",
        EMBED_TESTS,
    ),
    (
        # Not "stop metering" - meter under the wrong kind. Dropping the call is
        # obvious in a report that suddenly has no embedding row; folding it into
        # chat is invisible, because chat is the number everyone already reads and
        # it is supposed to be the large one.
        "embedding: meter the call as chat",
        EMBED,
        '                undx_capabilities.CALL_KIND_EMBEDDING,\n',
        '                undx_capabilities.CALL_KIND_CHAT,\n',
        "test_a_successful_batch_is_recorded_as_embedding_not_chat",
        EMBED_TESTS,
    ),
    (
        # Meter a different number from the one the budget restrains on. Both
        # figures exist at this line and they agree today only because the same
        # variable is passed to both, which is the point.
        "embedding: meter the batch size instead of the billed tokens",
        EMBED,
        '                input_tokens=billed,\n',
        '                input_tokens=len(indices),\n',
        "test_the_metered_token_count_is_the_one_the_budget_restrains_on",
        EMBED_TESTS,
    ),
    (
        # The state this call site was in before this phase: a paid image provider
        # that billed without leaving a trace. The mutation is deletion rather than
        # corruption because that is the real regression risk here - the site is
        # behind `AUTOMATED_IMAGES_ENABLED = False`, so nothing in production
        # notices if a future edit drops it.
        "image: stop metering image generations entirely",
        IMAGE,
        '        undx_capabilities.record_spend(\n'
        '            undx_capabilities.CALL_KIND_IMAGE, self.name, units=1, model=self.model,\n'
        '        )\n',
        '',
        "test_a_generated_image_is_metered_under_its_own_kind",
        IMAGE_TESTS,
    ),
    (
        # Same laundering as the embedding case. An image folded into chat is
        # invisible; an image missing from the report is at least a hole with a
        # shape.
        "image: meter the generation as chat",
        IMAGE,
        'undx_capabilities.CALL_KIND_IMAGE, self.name',
        'undx_capabilities.CALL_KIND_CHAT, self.name',
        "test_a_generated_image_is_metered_under_its_own_kind",
        IMAGE_TESTS,
    ),
    (
        # Move the metering above the base64 validation. Reads like an improvement
        # - "count the request, we were billed for it either way" - and it is a
        # defensible position, but it silently redefines the image count from
        # "pictures we received" to "requests we sent" without renaming anything.
        # The census records the billing edge as a known gap instead.
        "image: count an attempt that decoded to nothing as an image received",
        IMAGE,
        '        try:\n'
        '            content = base64.b64decode(encoded, validate=True)\n'
        '        except Exception as exc:\n'
        '            raise ImagePipelineError("image_provider_invalid_base64") from exc\n',
        '        undx_capabilities.record_spend(\n'
        '            undx_capabilities.CALL_KIND_IMAGE, self.name, units=1, model=self.model,\n'
        '        )\n'
        '        try:\n'
        '            content = base64.b64decode(encoded, validate=True)\n'
        '        except Exception as exc:\n'
        '            raise ImagePipelineError("image_provider_invalid_base64") from exc\n',
        "test_a_failed_generation_is_not_recorded_as_an_image_received",
        IMAGE_TESTS,
    ),
    (
        # Price the default model regardless of what the deploy is pointed at. Reads
        # as a simplification and is invisible today, because OpenAI Images has an
        # empty price table so every model is equally unpriced. It stops being
        # invisible the day one image model is priced and another is not.
        "image: price the default model instead of the configured one",
        IMAGE,
        'units=1, model=self.model,',
        'units=1, model="gpt-image-1",',
        "test_the_model_priced_is_the_effective_model_not_the_default",
        IMAGE_TESTS,
    ),
    (
        # The state this module was in before this phase: four paid vendors billing
        # per query with no record anywhere.
        "search: stop metering search queries entirely",
        SEARCH,
        '    undx_capabilities.record_spend(\n'
        '        undx_capabilities.CALL_KIND_RESEARCH, provider, units=1,\n'
        '    )\n',
        '    return\n',
        "test_a_successful_query_is_recorded_as_research_not_chat",
        SEARCH_TESTS,
    ),
    (
        "search: meter the query as chat",
        SEARCH,
        'undx_capabilities.CALL_KIND_RESEARCH, provider, units=1,',
        'undx_capabilities.CALL_KIND_CHAT, provider, units=1,',
        "test_a_successful_query_is_recorded_as_research_not_chat",
        SEARCH_TESTS,
    ),
    (
        # Attribute every query to one vendor. Leaves the total call count and the
        # total dollar figure *exactly* right, so nothing about the month's bottom
        # line looks wrong - only the answer to "which vendor should we drop".
        "search: attribute every query to a single provider",
        SEARCH,
        'undx_capabilities.CALL_KIND_RESEARCH, provider, units=1,',
        'undx_capabilities.CALL_KIND_RESEARCH, "brave", units=1,',
        "test_a_query_billed_before_a_later_provider_succeeded_is_still_recorded",
        SEARCH_TESTS,
    ),
    (
        # Meter before the status check. Reads as "count the attempt" and inflates
        # the month with 401s, 429s and 5xxs - refusals nobody was billed for. The
        # damage scales with how broken the vendor is, so it is worst during the
        # incident when the number is being read.
        "search: bill refusals as well as accepted queries",
        SEARCH,
        '    if not (200 <= response.status_code < 300):\n'
        '        return {"ok": False, "reason": "provider_rejected", "status_code": response.status_code}\n'
        '    # Before `response.json()`, not after: a 2xx whose body will not parse was\n'
        '    # still a query Brave accepted and billed. Parsing is our problem, not theirs.\n'
        '    _record_query_spend("brave")\n',
        '    _record_query_spend("brave")\n'
        '    if not (200 <= response.status_code < 300):\n'
        '        return {"ok": False, "reason": "provider_rejected", "status_code": response.status_code}\n',
        "test_a_rejected_query_is_not_recorded_as_spend",
        SEARCH_TESTS,
    ),
    (
        # Meter below the parse. Reads as tidier - record once the data is in hand -
        # and it makes a vendor having a bad serialization day look like a vendor we
        # stopped using, while they keep invoicing.
        "search: drop a billed query whose response body would not parse",
        SEARCH,
        '    _record_query_spend("brave")\n    data = response.json()\n',
        '    data = response.json()\n    _record_query_spend("brave")\n',
        "test_a_two_hundred_whose_body_will_not_parse_is_still_billed",
        SEARCH_TESTS,
    ),
    (
        # The most plausible shape of all: meter the searches that worked. `ok` in
        # this module means *results were found*, not *the vendor answered*, so this
        # silently stops counting every 200-with-an-empty-body - and those are the
        # queries most likely to be retried, which is where spend concentrates.
        "search: meter only the queries that returned results",
        SEARCH,
        '    _record_query_spend("brave")\n'
        '    data = response.json()\n'
        '    results = [\n'
        '        _clean_result(item.get("title"), item.get("url"), item.get("description"), "brave")\n'
        '        for item in ((data.get("web") or {}).get("results") or [])[:MAX_RESULTS]\n'
        '    ]\n',
        '    data = response.json()\n'
        '    results = [\n'
        '        _clean_result(item.get("title"), item.get("url"), item.get("description"), "brave")\n'
        '        for item in ((data.get("web") or {}).get("results") or [])[:MAX_RESULTS]\n'
        '    ]\n'
        '    if results:\n'
        '        _record_query_spend("brave")\n',
        "test_a_query_that_found_nothing_is_still_a_query_we_paid_for",
        SEARCH_TESTS,
    ),
    (
        # Skip the free provider because it costs nothing. Reads as an obvious
        # optimisation and it destroys the call counts for the only search provider
        # that has ever returned a result in production: a known zero is a
        # measurement and belongs in the record, which is the distinction §34 rests
        # on read in the other direction.
        "search: skip the free provider because its price is zero",
        SEARCH,
        '    _record_query_spend("duckduckgo_instant")\n',
        '',
        "test_duckduckgo_is_a_measured_zero_and_not_an_unknown",
        SEARCH_TESTS,
    ),
    (
        # Meter above the credential check. Four of the five providers are
        # unconfigured in production, so this charges four phantom queries for every
        # real search - a 5x overstatement of the search bill, from a line that looks
        # like it was simply placed at the top of the function.
        "search: bill a query for a provider with no credentials",
        SEARCH,
        '    key = _env("BRAVE_SEARCH_API_KEY")\n    if not key:\n',
        '    _record_query_spend("brave")\n'
        '    key = _env("BRAVE_SEARCH_API_KEY")\n    if not key:\n',
        "test_an_unconfigured_provider_is_not_billed",
        SEARCH_TESTS,
    ),
    (
        # The state this module was in before this phase.
        "translation: stop metering translation entirely",
        TRANS,
        '    undx_capabilities.record_spend(\n'
        '        undx_capabilities.CALL_KIND_TRANSLATION, provider, units=len(text or ""),\n'
        '    )\n',
        '    return\n',
        "test_a_translation_is_recorded_as_translation_not_chat",
        TRANS_TESTS,
    ),
    (
        # Worse than dropping it. Google serves chat models elsewhere in this repo,
        # so translation folded into `chat` lands in a plausible-looking row on a
        # provider that genuinely has chat spend.
        "translation: meter the request as chat",
        TRANS,
        'undx_capabilities.CALL_KIND_TRANSLATION, provider, units=len(text or ""),',
        'undx_capabilities.CALL_KIND_CHAT, provider, units=len(text or ""),',
        "test_a_translation_is_recorded_as_translation_not_chat",
        TRANS_TESTS,
    ),
    (
        # Count the request instead of the characters. Reads as a simplification and
        # is *invisible today*, because the provider is unpriced so every call costs
        # $0.00 either way. It becomes a silent 1000x understatement the moment
        # someone reads Google's rate into the table - which is why the test installs
        # a rate rather than asserting against the real one.
        "translation: bill one unit per request instead of per character",
        TRANS,
        'units=len(text or ""),',
        'units=1,',
        "test_the_billed_characters_are_the_ones_we_sent",
        TRANS_TESTS,
    ),
    (
        # Bill the translated text. The expansion ratio varies by language pair, so
        # the overstatement is largest on the pairs used most and no single wrong
        # figure ever appears twice - there is nothing for a human to notice.
        "translation: bill the translated text instead of the source",
        TRANS,
        '        _record_character_spend(self.name, text)\n'
        '        translations = response.get("translations") or []\n',
        '        translations = response.get("translations") or []\n'
        '        _record_character_spend(\n'
        '            self.name, str((translations[0] if translations else {}).get("translatedText") or ""))\n',
        "test_the_billed_characters_are_the_ones_we_sent",
        TRANS_TESTS,
    ),
    (
        # Discount markup. Tidier number, wrong bill - Google charges for the tags.
        "translation: strip html markup before counting characters",
        TRANS,
        '    undx_capabilities.record_spend(\n'
        '        undx_capabilities.CALL_KIND_TRANSLATION, provider, units=len(text or ""),\n'
        '    )\n',
        '    import re\n'
        '    billable = re.sub(r"<[^>]+>", "", text or "")\n'
        '    undx_capabilities.record_spend(\n'
        '        undx_capabilities.CALL_KIND_TRANSLATION, provider, units=len(billable),\n'
        '    )\n',
        "test_html_markup_counts_as_characters",
        TRANS_TESTS,
    ),
    (
        # Skip detection because "it is not a translation". It is billed per
        # character at the same rate, and every piece of unknown-language content
        # pays for both - so this halves the apparent cost of the most common path.
        "translation: stop metering language detection",
        TRANS,
        '        _record_character_spend(self.name, text)\n'
        '        languages = response.get("languages") or []\n',
        '        languages = response.get("languages") or []\n',
        "test_language_detection_is_billed_on_the_same_footing",
        TRANS_TESTS,
    ),
    (
        # Meter inside the retry loop. Multiplies the bill by the provider's
        # flakiness, which is the opposite of what a cost report is for, and it does
        # it only during incidents - so the report is wrong exactly when it is read.
        "translation: bill every retry attempt rather than the accepted request",
        TRANS,
        '                if response.status_code < 400:\n                    return response.json()\n',
        '                if response.status_code < 400:\n'
        '                    return response.json()\n'
        '                _record_character_spend("google", str(payload or ""))\n',
        "test_a_retried_request_is_billed_once",
        TRANS_TESTS,
    ),
    (
        # Meter the metadata lookup too, "for completeness". `getSupportedLanguages`
        # is free and is not a translation, and the ledger has no operation
        # dimension - so this pollutes the call count, which is currently the *only*
        # signal for this provider because the price is unknown and the character
        # volume is not persisted.
        "translation: count the free language list as translation spend",
        TRANS,
        '        response = self._request("GET", "/supportedLanguages", params={"displayLanguageCode": display_language})\n',
        '        response = self._request("GET", "/supportedLanguages", params={"displayLanguageCode": display_language})\n'
        '        _record_character_spend(self.name, display_language)\n',
        "test_listing_supported_languages_is_not_translation_spend",
        TRANS_TESTS,
    ),
    (
        # Meter above the credential check, where a "record every attempt" reading
        # would put it. Charges for requests that never leave the process.
        "translation: bill a request that was never sent",
        TRANS,
        '        payload: dict[str, Any] = {\n            "contents": [text],\n',
        '        _record_character_spend(self.name, text)\n'
        '        payload: dict[str, Any] = {\n            "contents": [text],\n',
        "test_an_unconfigured_provider_is_not_billed",
        TRANS_TESTS,
    ),
    (
        # The defect the shared budget replaced, restored in one line. Reads like a
        # simplification - "we already track our own tokens, why round-trip the
        # database" - and it is invisible in any single-process test, which is every
        # test that existed before. In production it divides the ceiling by the number
        # of processes that embed: eight.
        "budget: go back to counting only this process's own tokens",
        EMBED,
        '    recorded = undx_capabilities.month_spend(undx_capabilities.CALL_KIND_EMBEDDING)\n',
        '    recorded = {}\n',
        "test_spend_by_another_worker_counts_against_this_workers_budget",
        EMBED_TESTS,
    ),
    (
        # `max` looks like belt-and-braces once the ledger is trusted, so taking the
        # ledger figure alone reads as removing a redundant guard. It is the one
        # direction that can make the budget *weaker* than the dict it replaced: a
        # write that failed still bumped the local count, and an empty or
        # mid-rollover ledger then reports less than this worker knows it spent.
        "budget: let the ledger figure win outright instead of the larger of the two",
        EMBED,
        '        "spend_usd": max(ledger_usd, local_usd),\n'
        '        "tokens_embedded": max(ledger_tokens, local_tokens),\n',
        '        "spend_usd": ledger_usd,\n'
        '        "tokens_embedded": ledger_tokens,\n',
        "test_the_ledger_can_tighten_the_budget_but_never_loosen_it",
        EMBED_TESTS,
    ),
    (
        # §34 pointed the other way, which is exactly why this one is dangerous. The
        # ledger reports an unpriced call as $0.00 plus `uncosted_calls=1` because
        # inventing a cost would report money nobody was charged - correct for a
        # report, catastrophic for a ceiling. Deleting the uplift reads as deleting a
        # guess; what it does is make an unrecognised model free to spend without
        # limit, and a model rename is the likeliest way for one to appear.
        "budget: trust the ledger's $0.00 for a model with no published price",
        EMBED,
        '    if recorded.get("spend_is_a_floor"):\n',
        '    if False:\n',
        "test_an_unpriced_model_is_charged_at_the_highest_known_rate",
        EMBED_TESTS,
    ),
    (
        # The arithmetic the rewrite fixed, reinstated. Summing tokens and pricing the
        # total at one rate is the obvious way to write this and reads as tidier than
        # adding two dollar figures. It re-prices every call already made at whatever
        # `UNDX_EMBEDDING_MODEL` is set to now, so a mid-month model switch moves the
        # recorded past - and because the configured model is the cheap one, it moves
        # it downwards.
        "budget: price the whole month's tokens at the currently configured rate",
        EMBED,
        '    return position["spend_usd"] + estimated_cost_usd(tokens) > limit\n',
        '    return estimated_cost_usd(position["tokens_embedded"] + tokens) > limit\n',
        "test_spend_by_another_worker_counts_against_this_workers_budget",
        EMBED_TESTS,
    ),
    (
        # Provenance, dropped. A figure covering one worker because the database was
        # unreachable and a figure covering the deployment are different claims, and
        # `remaining_usd` is fiction in the first case. Hard-coding the reassuring
        # answer reads as a tidy-up of a field nothing appears to branch on.
        "budget: report a degraded per-process figure as the shared one",
        EMBED,
        '        "shared": position["source"] == "ledger",\n',
        '        "shared": True,\n',
        "test_an_unreachable_ledger_falls_back_to_this_processs_own_count",
        EMBED_TESTS,
    ),
    (
        # The reporting side of the same pair, mutated instead of the blocking side.
        # `month_spend` is the only thing that tells the guard its dollar figure is a
        # floor; asserting it never is reads as simplifying a flag, and silently
        # disables the uplift above without touching the guard at all.
        "capabilities: stop telling the caller the spend figure is a floor",
        CAPS,
        '        "spend_is_a_floor": uncosted > 0,\n',
        '        "spend_is_a_floor": False,\n',
        "test_an_unpriced_model_is_charged_at_the_highest_known_rate",
        EMBED_TESTS,
    ),
    (
        # Zero as "no budget configured" is the documented opt-out and the default.
        # Treating it as a $0.00 ceiling reads like closing a loophole and would
        # refuse every embedding call in a deployment that never set the variable.
        "budget: read an unset budget as a ceiling of zero",
        EMBED,
        '    if limit <= 0:\n        return False\n',
        '    if limit < 0:\n        return False\n',
        "test_a_zero_budget_is_still_an_opt_out",
        EMBED_TESTS,
    ),
    (
        # The isolation that stops this whole subsystem's tests from writing into the
        # developer's own database. It is one assignment with no effect on any passing
        # test, so nothing but this pairing keeps it from being deleted - and once the
        # budget reads the ledger, the residue it prevents is spend the guard counts.
        #
        # Anchored on the absolute-path assertion rather than the more pointed
        # "outside the repository" one, because that second check cannot see the
        # mutation *from inside this harness*: `build_sandbox` symlinks
        # `tests/test_dev_database_isolation.py` back at the real repo, so the
        # `realpath(__file__)` that test derives its repo root from resolves to the
        # real checkout while pytest's working directory is the sandbox. The
        # containment check is the one that matters in a normal run and it does fail
        # there — verified by hand — but in here it would be a false pass, and a
        # mutation harness that reports a false pass is worse than one that skips.
        "conftest: stop redirecting the unconfigured-SQLite fallback",
        "tests/conftest.py",
        '    platform_db.LOCAL_SQLITE_FILE = _FALLBACK_DB_PATH\n',
        '    pass\n',
        "test_the_fallback_is_absolute",
        "tests/test_dev_database_isolation.py",
    ),
]


def main() -> int:
    only = ""
    args = sys.argv[1:]
    if args and args[0] == "--only":
        if len(args) < 2:
            print("--only needs a substring", file=sys.stderr)
            return 2
        only = args[1]

    selected = [m for m in MUTATIONS if not only or only in m[0]]
    if not selected:
        print(f"no mutation label contains {only!r}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for label, target, old, new, expect, tests in selected:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = build_sandbox(pathlib.Path(tmp), target)
            path = sandbox / target
            source = path.read_text(encoding="utf-8")
            if source.count(old) != 1:
                failures.append(f"{label}: anchor matched {source.count(old)}x, expected 1")
                print(f"BAD {label}: anchor matched {source.count(old)}x")
                continue
            path.write_text(source.replace(old, new), encoding="utf-8")

            env = dict(os.environ, PYTHONPATH=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", tests, "-q", "--no-header",
                 "-p", "no:cacheprovider"],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=900,
            )
            output = proc.stdout + proc.stderr
            died = proc.returncode != 0

            if expect is None:
                verdict = "GREEN (correct)" if not died else "FAILED (should have been allowed)"
                if died:
                    failures.append(f"{label}: a comment is not supposed to trip "
                                    f"anything\n{output[-1500:]}")
            elif not died:
                verdict = "SURVIVED"
                failures.append(f"{label}: suite stayed green")
            elif expect not in output:
                verdict = f"died, but not on {expect}"
                failures.append(f"{label}: expected {expect} to fail\n{output[-1500:]}")
            else:
                verdict = f"caught by {expect}"
            marker = "BAD" if failures and failures[-1].startswith(f"{label}:") else "ok "
            print(f"{marker} {label}: {verdict}")

    print()
    if failures:
        print(f"{len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1
    scope = f" (filtered to {only!r}; {len(MUTATIONS)} exist)" if only else ""
    print(f"All {len(selected)} mutations behaved as specified{scope}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
