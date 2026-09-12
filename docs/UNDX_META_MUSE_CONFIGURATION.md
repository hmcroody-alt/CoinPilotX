# UNDX — Meta Muse configuration

Every value below was read from the live Meta developer console or measured
against the live API on 2026-09-11. Nothing here is copied from a reference
configuration. Where a measured value contradicts the configuration this
integration started from, the measurement wins and the discrepancy is recorded.

**No API key appears in this document.**

## Project

| | |
|---|---|
| Project ID | `1656198352782001` |
| Team ID | `4066572620316728` |
| Console | https://dev.meta.ai/?project_id=1656198352782001&team_id=4066572620316728 |
| Billing | Pay-as-you-go, active. Card on file, $20 auto-recharge threshold. |
| Spend limit | **Not set.** See "Open risks". |

## API contract

| | Verified value | How |
|---|---|---|
| Base URL | `https://api.meta.ai/v1` | Live 200 response |
| Auth | `Authorization: Bearer <key>` header | Live 200 response |
| Surface used | `POST /chat/completions` (OpenAI-shaped) | Live 200 response |
| Also available | `POST /responses` | Live 200 response |
| Default model | `muse-spark-1.3` | Console → Models |
| Context window | 1,048,576 tokens | Console → Models |
| Native tool calling | Yes — OpenAI-shaped `tool_calls`, `finish_reason: "tool_calls"` | Live probe |
| Unknown model ID | HTTP 404 `model_not_found` | Negative control |

### Why chat completions and not the Responses API

The configuration this work started from specified `wire_api = "responses"`. Both
surfaces work. Chat completions is used because:

1. It is the shape the other five providers in `undx_router.py` already speak, so
   Muse joins the existing failover loop instead of needing a second
   response-parsing path.
2. The one thing Responses offers that would justify the second path —
   reasoning summaries — **is not actually delivered**. Asked with
   `reasoning: {"effort": "minimal", "summary": "auto"}`, the live API returned
   `summary: []`. Meta's own documentation says summaries are never guaranteed.

A parsing branch maintained for a field that arrives empty is a liability.

### Reasoning effort

Sent on every call as `reasoning_effort`. The accepted set was obtained from the
API's own rejection message, not from documentation:

```
none, minimal, low, medium, high, xhigh, max
```

`max` is Standard-tier `muse-spark-1.3` only. An unrecognised value returns
HTTP 400 for the whole request, so `undx_router._meta_reasoning_effort()`
validates against this list and falls back to `high` rather than forwarding a
typo — otherwise one bad environment variable becomes a total Meta outage that
failover then hides as slowness.

### The token-budget trap

**Reasoning tokens are billed against `max_tokens`, alongside the answer.**
Measured, asking for the two letters "ok":

| `max_tokens` | `reasoning_effort` | completion tokens | of which reasoning | content |
|---|---|---|---|---|
| 32 | minimal | 26 | 15 | `"ok"` |
| 32 | high | 32 | 29 | **`null`** |
| 900 | high | 381 | 370 | `"ok"` |

When the budget runs out during reasoning the API returns HTTP 200 with
`content: null` and `finish_reason: "length"`.

The router's previous extractor called `.strip()` on that value. The resulting
`AttributeError` was caught by the per-provider `except Exception`, logged as a
generic `response_failed`, and failed over. **Muse would have appeared
permanently broken while being perfectly healthy and merely under-budgeted** —
and `route_structured_request`'s 320-token default would have made that the
normal case, not the edge case.

Two mitigations, both in `undx_router.py`:

- `_effective_max_tokens()` adds `reasoning_overhead_tokens` (3000) to any
  budget bound for Meta.
- `_provider_text()` raises a `ValueError` naming the exhausted budget instead of
  crashing on `None`, for **every** provider.

Covered by `tests/test_undx_router_multi_provider.py::EmptyCompletionTest`.

### Latency and timeout

First call on a cold route: **26.7s**. Subsequent calls: 1.1–3.9s.

The router's module-wide default is 25 seconds, which would have cut that first
call off. `META_MUSE_TIMEOUT_MS=60000` is therefore load-bearing, and
`_timeout()` lets a provider's own budget raise the caller's — never shorten it.

## Standard vs Contributor tier — a decision, not a default

Both tiers are live and both answer for this project.

| | Standard `muse-spark-1.3` | Contributor `muse-spark-1.3-contributor` |
|---|---|---|
| Input / 1M | $1.25 | $0.10 |
| Cached input / 1M | $0.15 | $0.002 |
| Output / 1M | $4.25 | $0.20 |
| Rate limit | 3,000 RPM / 4,000,000 TPM | **100 RPM** / 3,000,000 TPM |
| Training on your data | **No** | **Yes** |

Meta's console states it plainly for the Contributor tier: *"Inputs and outputs
are used to train and improve Meta's AI models."*

**The integration defaults to Standard.** The reference configuration named the
Contributor model, and adopting it would have sent PulseSoc user content — and
anything reaching UNDX through Private Office — into Meta's training corpus, at
a 30× lower request ceiling. The 95% discount is not a discount; it is the price.

Changing this is a one-line environment change (`META_MUSE_MODEL`), and it should
only be made for traffic that is known to carry no user or customer data.

## Railway variables

Names and purposes only. See `UNDX_RAILWAY_PROVIDER_CONFIG.md`.

| Variable | Status | Purpose |
|---|---|---|
| `META_MODEL_API_KEY` | PRESENT | Credential. Read by `undx_router.PROVIDERS["meta"].key_env`. |
| `META_MODEL_API_ENABLED` | PRESENT (`true`) | Account-level intent flag. |
| `META_MUSE_ENABLED` | PRESENT (`true`) | Kill switch (§73). Set `false` to drop Muse from rotation instantly without deleting the credential. |
| `META_MUSE_TIMEOUT_MS` | PRESENT (`60000`) | Per-provider timeout. Load-bearing — see latency above. |
| `META_MUSE_FALLBACK_ENABLED` | PRESENT (`true`) | Account-level intent flag. |
| `META_MUSE_MODEL` | not set | Optional model override. Unset means `muse-spark-1.3` (Standard). |
| `META_MUSE_REASONING_EFFORT` | not set | Optional. Unset means `high`. |

The reference configuration used `MODEL_API_KEY`. Railway uses
`META_MODEL_API_KEY`, and the router reads the Railway name. A router reading
the other name finds nothing, reports Meta unconfigured, and skips it forever
without an error anywhere — pinned by
`test_meta_reads_the_railway_variable_name_not_the_vendor_default`.

## Routing position

Muse is registered as an **available specialist, not the default** (§7). It
appears in the `repository` and `automation` chains behind the incumbents, and
`UNDX_DEFAULT_AI_PROVIDER` remains `openai`. Promoting it past a provider already
serving production is a decision for benchmark evidence, not for the commit that
first makes it reachable.

## Verification

```
railway run --service CoinPilotX -- .venv/bin/python3 scripts/undx_provider_health_check.py
```

Last run, 2026-09-11:

```
Meta Muse    CONNECTED  model=muse-spark-1.3 3527ms reply='ok'
```

The check routes through `undx_router.route_structured_request`, not through a
hand-written HTTP call. A smoke test that builds its own request verifies the
vendor's uptime and nothing about this application.

## Open risks

- **No spend limit is configured on the Meta project.** Pay-as-you-go is live
  with auto-recharge at $20. Setting a cap is an account change and needs an
  explicit decision.
- **Usage is unmetered on our side.** Meta returns `usage` on every response,
  including `reasoning_tokens` and `cached_tokens`, and the router currently
  discards it. Cost accounting per §70 is not yet built.
- Muse Voice Transcribe and Muse Image are available on this project and are
  **not** integrated. Voice in particular must stay clear of the real-time audio
  subsystem — see `docs/realtime_audio_change_policy.md`.
