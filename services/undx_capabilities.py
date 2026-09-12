"""Declared inventory of the non-chat AI work this codebase pays for (§20-27).

`undx_router.PROVIDERS` is the authority on **chat** models and stays that way. It
cannot describe the rest of the AI spend, and not because nobody got round to it:
`ProviderConfig` is shaped around a chat completion - one `model_env`, a
`structured_output` dialect, a `reasoning_overhead_tokens` allowance - and an
embeddings endpoint, an image generation, a web search and a translation share
none of those fields. Bolting them on would make every chat provider carry six
columns that mean nothing to it. So this is a second table, deliberately, and the
two are joined only by the call-kind vocabulary in `services.undx_cost`.

What this module is for: **§22 asks that there be no unclassified AI spend.** The
obstacle was never enforcement, it was that nothing enumerated the spend. The web
search adapters had been running for ten weeks with no AI classification at all
(census R-d), the image pipeline has an hourly cap, a daily cap and a circuit
breaker but no dollar accounting whatsoever, and translation is billed per
character by a module six `TRANSLATION_*` flags govern, none of which governs its
cost (census N-d). A table that lists them is the thing that makes "is this
metered?" a question with an answer.

Three rules this table follows, each of which is a place the convenient choice is
wrong:

**An unknown price is UNKNOWN, not zero (§34).** Most entries below have an empty
`prices` mapping. That is the honest state of this repository's knowledge, not an
omission to be filled in with a plausible number: the one priced entry carries
figures read from published pricing on a recorded date, and inventing the others
to make a dashboard add up would convert "we do not know what images cost" into a
confident wrong total. `price_micro_usd` returns `None` for these, and the ledger
records the call under `uncosted_calls` - a floor with an explicit count of what
the floor excludes, which is readable, where a fabricated sum is not.

**Known-zero is not the same as unknown.** DuckDuckGo's Instant Answer endpoint
takes no key and charges nothing, so its price is `0.0` *as a measured fact* and
it is still recorded. §34 forbids treating an unknown as free; it does not require
pretending a free thing might be expensive. Leaving it out entirely would have
been the worse option, because then the ledger's call counts would be incomplete
and the one search provider that actually works in production (all three
production successes were DuckDuckGo) would be the one missing from the records.

**The same unknown rounds in opposite directions depending on the question.** When
*blocking* spend, an unpriced model should be assumed expensive - that is what
`undx_embedding_service._UNKNOWN_MODEL_PRICE_USD` does by charging the highest
known rate, and it is right to. When *reporting* spend, the same unknown must not
be reported as though it were incurred. This module serves the reporting
direction, so it answers `None`; it deliberately does not offer a
worst-case number, because a caller that got one would have no way to tell it from
a measurement.

Nothing here executes a request. Adapters keep their own HTTP, their own retry
policy and their own failure vocabulary - moving those would be a rewrite of four
working subsystems to no benefit, and §2-3 warns specifically against turning
specialised workflows into generic ones by accident. What routes through here is
the *declaration*: which kinds exist, who serves them, what a unit costs, and
which credential decides whether the call can happen at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from services import undx_cost
from services.undx_cost import (
    CALL_KIND_EMBEDDING,
    CALL_KIND_IMAGE,
    CALL_KIND_MODERATION,
    CALL_KIND_RERANK,
    CALL_KIND_RESEARCH,
    CALL_KIND_TRANSCRIPTION,
    CALL_KIND_TRANSLATION,
    MICRO_PER_USD,
)

# --------------------------------------------------------------- pricing units

#: What one billable unit *is*, per kind. Named rather than assumed because the
#: four kinds below bill on four different things, and a metering call site that
#: guessed would be wrong by six orders of magnitude in either direction.
UNIT_MILLION_TOKENS = "million_tokens"
UNIT_MILLION_CHARACTERS = "million_characters"
UNIT_IMAGE = "image"
UNIT_QUERY = "query"

#: How many of the provider's own billing units make up one priced unit above.
#: `price_micro_usd` divides by this, so a caller passes the natural count it
#: already has - tokens, characters, images, queries - and never does the
#: arithmetic itself.
_UNITS_PER_PRICED_UNIT = {
    UNIT_MILLION_TOKENS: 1_000_000,
    UNIT_MILLION_CHARACTERS: 1_000_000,
    UNIT_IMAGE: 1,
    UNIT_QUERY: 1,
}


@dataclass(frozen=True)
class CapabilityProvider:
    """One endpoint that can serve one call kind."""

    name: str
    label: str
    #: Credentials that enable this provider, in the order the adapter reads them.
    #: A tuple because Bing genuinely accepts two different variable names and
    #: `BING_SEARCH_API_KEY or BING_SEARCH_V7_SUBSCRIPTION_KEY` is the live
    #: expression at `pulse_ai_web_search.py:184` - collapsing it to one name here
    #: would make this table disagree with the code it describes.
    key_envs: tuple[str, ...] = ()
    #: The endpoint, as a literal, so that §11-12's "no direct provider base URLs
    #: outside adapters" has a single place to point at for the non-chat kinds.
    endpoint: str = ""
    model_env: str = ""
    default_model: str = ""
    pricing_unit: str = ""
    #: model -> USD per priced unit. **Empty means unknown, never free.** The
    #: docstring of this module explains why that distinction is load-bearing.
    prices: dict[str, float] = field(default_factory=dict)
    #: Where `prices` came from and when. Empty when `prices` is empty; a price
    #: with no provenance is indistinguishable from a guess, and this repository
    #: has been bitten by numbers that outlived their source.
    price_source: str = ""
    #: False only for endpoints that charge nothing, and only where that has been
    #: checked. Not a default - see `test_no_capability_is_paid_by_omission`.
    paid: bool = True

    def key_configured(self, env: dict[str, str] | None = None) -> bool:
        source = os.environ if env is None else env
        return any(str(source.get(name, "") or "").strip() for name in self.key_envs)

    def configured_model(self, env: dict[str, str] | None = None) -> str:
        source = os.environ if env is None else env
        if self.model_env:
            chosen = str(source.get(self.model_env, "") or "").strip()
            if chosen:
                return chosen
        return self.default_model


@dataclass(frozen=True)
class Capability:
    kind: str
    label: str
    providers: tuple[CapabilityProvider, ...]

    def provider(self, name: str) -> CapabilityProvider | None:
        for candidate in self.providers:
            if candidate.name == name:
                return candidate
        return None


CAPABILITIES: dict[str, Capability] = {
    CALL_KIND_EMBEDDING: Capability(
        CALL_KIND_EMBEDDING, "Embeddings",
        (
            # The only entry in this table with real prices, and the only one whose
            # spend is currently bounded by anything: `undx_embedding_service`
            # carries a monthly budget. That budget is per-process and therefore
            # per gunicorn worker, which is a separate defect from this one.
            CapabilityProvider(
                "perplexity", "Perplexity Embeddings",
                key_envs=("PERPLEXITY_API_KEY",),
                endpoint="https://api.perplexity.ai/v1/embeddings",
                model_env="UNDX_EMBEDDING_MODEL",
                default_model="pplx-embed-v1-0.6b",
                pricing_unit=UNIT_MILLION_TOKENS,
                prices={
                    "pplx-embed-v1-0.6b": 0.004,
                    "pplx-embed-v1-4b": 0.03,
                    "pplx-embed-context-v1-0.6b": 0.008,
                    "pplx-embed-context-v1-4b": 0.05,
                },
                price_source="Perplexity published pricing, read 2026-08-30",
            ),
        ),
    ),
    CALL_KIND_IMAGE: Capability(
        CALL_KIND_IMAGE, "Image generation",
        (
            # `gpt-image-1` at 1024x1536, quality "medium". Deliberately unpriced:
            # this repository has no dated record of what that costs, and a per-image
            # price is the kind of number that is easy to half-remember and wrong by
            # a factor of five. The controls that do exist here are real but are not
            # money - an hourly cap, a daily cap and a failure circuit breaker bound
            # the *rate*, which is why the spend could grow without any of them
            # tripping.
            CapabilityProvider(
                "openai", "OpenAI Images",
                key_envs=("OPENAI_API_KEY",),
                endpoint="https://api.openai.com/v1/images/generations",
                model_env="PULSE_INSIGHT_IMAGE_MODEL",
                default_model="gpt-image-1",
                pricing_unit=UNIT_IMAGE,
            ),
        ),
    ),
    CALL_KIND_RESEARCH: Capability(
        CALL_KIND_RESEARCH, "Web search",
        (
            # Tried in this order by `pulse_ai_web_search.search()`. Four are paid and
            # unpriced; the fifth is free. None of the four has ever run in production
            # - every one returned `config_missing` on all 76 recorded attempts,
            # because two of the funded credentials are stored under names no file
            # reads (census R-c/R-d). So "paid" here describes the contract, not the
            # invoice: the real spend to date is zero, by accident rather than by
            # control, which is exactly why metering lands before the credential names
            # are fixed.
            CapabilityProvider(
                "brave", "Brave Search",
                key_envs=("BRAVE_SEARCH_API_KEY",),
                endpoint="https://api.search.brave.com/res/v1/web/search",
                pricing_unit=UNIT_QUERY,
            ),
            CapabilityProvider(
                "bing", "Bing Web Search v7",
                key_envs=("BING_SEARCH_API_KEY", "BING_SEARCH_V7_SUBSCRIPTION_KEY"),
                endpoint="https://api.bing.microsoft.com/v7.0/search",
                pricing_unit=UNIT_QUERY,
            ),
            CapabilityProvider(
                "serpapi", "SerpApi",
                key_envs=("SERPAPI_API_KEY",),
                endpoint="https://serpapi.com/search.json",
                pricing_unit=UNIT_QUERY,
            ),
            CapabilityProvider(
                "tavily", "Tavily",
                key_envs=("TAVILY_API_KEY",),
                endpoint="https://api.tavily.com/search",
                pricing_unit=UNIT_QUERY,
            ),
            # Keyless and free. `paid=False` and a price of exactly zero are both
            # measurements here, not defaults - see the module docstring on why
            # known-zero and unknown must not share a representation. This is also
            # the only search provider that has ever returned a result in
            # production, which is the reason it is recorded rather than skipped.
            CapabilityProvider(
                "duckduckgo_instant", "DuckDuckGo Instant Answer",
                endpoint="https://api.duckduckgo.com/",
                pricing_unit=UNIT_QUERY,
                prices={"": 0.0},
                price_source="keyless public endpoint, charges nothing",
                paid=False,
            ),
        ),
    ),
    CALL_KIND_TRANSLATION: Capability(
        CALL_KIND_TRANSLATION, "Machine translation",
        (
            # Billed per character, and the host is a literal while the path is
            # composed from the project id - which is why §11-12 says "including
            # composed URLs" and why no AI-call detector in this repo had ever
            # flagged it. Two different credentials configure it: a service account
            # JSON or a plain API key, and `GoogleConfig.configured` requires the
            # project id plus either one.
            CapabilityProvider(
                "google", "Google Cloud Translation v3",
                key_envs=("GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON",
                          "GOOGLE_CLOUD_TRANSLATION_API_KEY"),
                endpoint="https://translation.googleapis.com/v3/",
                pricing_unit=UNIT_MILLION_CHARACTERS,
            ),
        ),
    ),
    # Declared and empty on purpose. An absent key in this dict is ambiguous
    # between "this codebase does not do that" and "nobody checked", and the
    # census had to answer that question for each of them: transcription was
    # confirmed genuinely empty, and reranking and moderation are model
    # capabilities nothing here calls as a separate billed endpoint. Recording the
    # emptiness is what makes a future adapter's arrival visible as a change to
    # this table rather than as a new untracked line item.
    CALL_KIND_TRANSCRIPTION: Capability(CALL_KIND_TRANSCRIPTION, "Transcription", ()),
    CALL_KIND_RERANK: Capability(CALL_KIND_RERANK, "Reranking", ()),
    CALL_KIND_MODERATION: Capability(CALL_KIND_MODERATION, "Moderation", ()),
}


def capability(kind: str) -> Capability | None:
    return CAPABILITIES.get(str(kind or "").strip().lower())


def provider_for(kind: str, provider: str) -> CapabilityProvider | None:
    found = capability(kind)
    return found.provider(str(provider or "").strip().lower()) if found else None


def price_micro_usd(kind: str, provider: str, units: float,
                    *, model: str = "") -> int | None:
    """Cost of `units` billable units, in integer micro-USD, or `None` if unknown.

    `None` is a real answer and callers must handle it as one: it means this
    repository does not know the price, and the ledger's contract is to count that
    call under `uncosted_calls` rather than to add zero to a dollar total. Returning
    `0` here would make an unpriced image generation indistinguishable from a free
    DuckDuckGo query, and those two need to look different in a report.

    Integer micro-USD for the same reason the ledger uses it: float dollars do not
    survive summation across a month of calls priced at four thousandths of a cent.
    """
    entry = provider_for(kind, provider)
    if entry is None or not entry.prices:
        return None
    rate = entry.prices.get(model if model else "")
    if rate is None and len(entry.prices) == 1 and "" in entry.prices:
        rate = entry.prices[""]
    if rate is None:
        # The provider is priced but this *model* is not. Still unknown - falling
        # back to a sibling model's rate would report a number nobody measured.
        return None
    per = _UNITS_PER_PRICED_UNIT.get(entry.pricing_unit, 1)
    return int(round(float(rate) * MICRO_PER_USD * (float(units) / per)))


def configured_providers(kind: str, env: dict[str, str] | None = None) -> tuple[str, ...]:
    """Providers for `kind` that could actually be reached right now.

    A provider with no `key_envs` at all is always reachable - that is what keyless
    means, and DuckDuckGo is the case. It is not an oversight in the table, so this
    must not filter it out.
    """
    found = capability(kind)
    if not found:
        return ()
    return tuple(p.name for p in found.providers
                 if not p.key_envs or p.key_configured(env))


def unpriced_providers() -> tuple[tuple[str, str], ...]:
    """Every (kind, provider) whose price this repository does not know.

    The point of being able to ask: §22's "no unclassified AI spend" is satisfiable,
    but "no *unpriced* AI spend" is not satisfiable from inside the repo - it needs
    published prices someone has read on a date. This enumerates the remaining work
    instead of letting each missing price stay invisible in its own module.
    """
    out: list[tuple[str, str]] = []
    for kind, entry in CAPABILITIES.items():
        for provider in entry.providers:
            if not provider.prices:
                out.append((kind, provider.name))
    return tuple(out)


def record_spend(kind: str, provider: str, *, units: float = 0, model: str = "",
                 input_tokens: int = 0, output_tokens: int = 0) -> dict[str, Any]:
    """Record one non-chat call in the shared ledger under its own `call_kind`.

    This is the whole point of the table: an adapter calls it with the count it
    already has - tokens, characters, images, queries - and does not need to know
    what a unit costs, whether the price is known, or how the ledger represents an
    unknown. Four modules metering themselves would have produced four slightly
    different answers to those questions, which is how "no unclassified AI spend"
    becomes true in each module and false overall.

    `units` is in the provider's own billing unit for this kind. The conversion is
    `_UNITS_PER_PRICED_UNIT`'s job, so a per-image charge cannot be read as
    per-token.

    Never raises. `undx_cost.record` already guarantees that a bookkeeping failure
    does not fail a request that succeeded - the money is spent either way - and
    this adds nothing that could throw on top of it. Specifically: an unknown price
    is not an error, it is a recorded call with `cost_micro_usd=0` and
    `uncosted_calls=1`.
    """
    cost_micro = price_micro_usd(kind, provider, units, model=model)
    return undx_cost.record({
        "provider": provider,
        "model": model,
        "call_kind": kind,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "reasoning_tokens": 0,
        # None here is load-bearing and must not become 0: it is what makes the
        # ledger count this call under `uncosted_calls` instead of adding a dollar
        # amount nobody was charged. A genuinely free endpoint reaches this line
        # with an integer 0 instead, and the two are recorded differently.
        "cost_micro_usd": cost_micro,
    })


def describe_for_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "kinds": {
            kind: {
                "label": entry.label,
                "providers": [
                    {
                        "name": p.name,
                        "paid": p.paid,
                        "pricing_unit": p.pricing_unit,
                        "priced": bool(p.prices),
                        "price_source": p.price_source,
                        "configured": (not p.key_envs) or p.key_configured(env),
                        "model": p.configured_model(env),
                    }
                    for p in entry.providers
                ],
            }
            for kind, entry in CAPABILITIES.items()
        },
        "unpriced": [f"{kind}:{provider}" for kind, provider in unpriced_providers()],
    }


__all__ = [
    "CAPABILITIES", "Capability", "CapabilityProvider",
    "UNIT_MILLION_TOKENS", "UNIT_MILLION_CHARACTERS", "UNIT_IMAGE", "UNIT_QUERY",
    "capability", "provider_for", "price_micro_usd", "configured_providers",
    "unpriced_providers", "describe_for_report",
    # Re-exported, not redefined. Adapters need to name their own call kind when
    # they meter, and importing it from here rather than from `undx_cost` keeps a
    # call site's imports to the one module it is already talking to - while the
    # single definition stays in the ledger, which is what has to agree with the
    # `call_kind` column.
    "CALL_KIND_EMBEDDING", "CALL_KIND_IMAGE", "CALL_KIND_RESEARCH",
    "CALL_KIND_TRANSLATION", "CALL_KIND_TRANSCRIPTION", "CALL_KIND_RERANK",
    "CALL_KIND_MODERATION",
]
