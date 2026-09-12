# UNDX — provider data policy

What each provider does with what UNDX sends it, and which PulseSoc data classes
may therefore reach it.

Status as of 2026-09-11. Meta's position was read from its live console and
documentation for project `1656198352782001`; the others are recorded at the
confidence level actually established, and the gaps are marked as gaps rather
than filled in with a plausible sentence.

## Data classes

| Class | Examples |
|---|---|
| `SYNTHETIC` | Health-check prompts, benchmark fixtures. No real content. |
| `PLATFORM_PUBLIC` | Published posts, public profiles, public listings. |
| `PLATFORM_PRIVATE` | DMs, drafts, private groups, order history, seller data. |
| `ACCOUNT` | Identity, contact details, entitlements, auth state. |
| `PRIVATE_OFFICE` | Everything behind the Private Office second lock. |
| `SECRET` | Credentials, tokens, internal service keys. |

`SECRET` never reaches any provider. That is enforced, not promised: see
`undx_router._safe_error()` and `_api_key()`, and
`tests/test_undx_router_multi_provider.py::CredentialRedactionTest`.

## Provider matrix

| Provider | Trains on our data | Retention | May receive |
|---|---|---|---|
| OpenAI (`gpt-4o-mini`) | No, on the API tier | Not independently verified | `SYNTHETIC`, `PLATFORM_PUBLIC`, `PLATFORM_PRIVATE` |
| Claude (`claude-haiku-4-5`) | No, on the API tier | Not independently verified | `SYNTHETIC`, `PLATFORM_PUBLIC`, `PLATFORM_PRIVATE` |
| **Meta Muse — Standard** (`muse-spark-1.3`) | **No** — console states prompts and completions are not used to train Meta models | Not independently verified | `SYNTHETIC`, `PLATFORM_PUBLIC`, `PLATFORM_PRIVATE` |
| **Meta Muse — Contributor** (`muse-spark-1.3-contributor`) | **Yes** — console states inputs and outputs are used to train and improve Meta's AI models | Training corpus | `SYNTHETIC` **only** |
| Perplexity (`sonar`) | Not independently verified | Not independently verified | `SYNTHETIC`, `PLATFORM_PUBLIC` |
| Gemini (`gemini-flash-lite-latest`) | Not independently verified | Not independently verified | `SYNTHETIC`, `PLATFORM_PUBLIC` |
| DeepSeek / Groq | Not independently verified | Not independently verified | Currently unreachable — see `UNDX_RAILWAY_PROVIDER_CONFIG.md` |

"Not independently verified" means exactly that. It is not a claim that the
provider trains on our data, and not a claim that it does not. Where a cell below
drives an actual restriction, the restriction is stated on its own terms.

## The Meta tier decision

The configuration this integration started from named
`muse-spark-1.3-contributor`. That model is 95% cheaper because Meta's own
console states its inputs and outputs are used to train Meta's models.

**This integration defaults to the Standard tier**, and Contributor is restricted
to `SYNTHETIC` traffic. The discount is not a discount; it is the price.

Two further reasons, independent of the data question:

- Contributor is rate-limited to **100 RPM** per team against Standard's 3,000.
  A provider that becomes the default for a busy lane at 100 RPM is an incident
  waiting for a traffic spike.
- Contributor is "available in select countries". A provider whose availability
  depends on where the request originates is not a dependable fallback.

Switching is `META_MUSE_MODEL`, one environment variable. It should only be done
for traffic known to carry no user or customer data.

### The Contributor tier is already configured elsewhere in this environment

`META_MUSE_MODEL` is not the only route to it. The production service also
carries `ANTHROPIC_BASE_URL=https://api.meta.ai` together with
`ANTHROPIC_MODEL=muse-spark-1.3-contributor` and three
`ANTHROPIC_DEFAULT_*_MODEL` variables set to the same Contributor ID — Claude
Code CLI settings that leaked onto the deployed service.

No UNDX code reads them. But any component added later that talks to Anthropic
through the official SDK picks them up **by default, with no code change and no
review**, and its traffic lands in Meta's training corpus while every log line
and status page continues to say "Claude".

`undx_router._call_claude` therefore hardcodes `https://api.anthropic.com` and
reads `CLAUDE_MODEL` rather than `ANTHROPIC_MODEL`. That is a deliberate refusal
to be configurable, pinned by `ClaudeEndpointTest`, and the variables themselves
should be deleted from the service — see `UNDX_RAILWAY_PROVIDER_CONFIG.md`.

## `PRIVATE_OFFICE`

**No provider in this matrix may receive `PRIVATE_OFFICE` data**, including
Meta Standard tier.

This is not a judgement about any particular vendor. Private Office sits behind a
second lock precisely because the first lock is not considered sufficient for it,
and "we reviewed the vendor's terms and they seemed fine" is the first lock
applied twice. Admitting a provider to that class requires a deliberate decision
with the retention question actually answered, not assumed — and the router is
not the right place to make it.

## Perplexity is different in kind

Perplexity resolves a query by **searching the live web at request time**. The
query itself becomes a search engine query.

That rules out sending it anything private regardless of its retention policy: a
DM summarised into a Perplexity prompt has been typed into a search engine. Hence
`PLATFORM_PUBLIC` and `SYNTHETIC` only.

The reverse direction matters too. Perplexity returns content fetched from pages
nobody vetted, which under §61 and §62 is **untrusted input**. It carries no
authority, cannot alter policy or capability constraints, and must be treated as
data. `undx_router._call_perplexity` returns the sources alongside the prose for
this reason: an unattributed research answer is indistinguishable from an
invented one.

## What is enforced in code today

Honest accounting, because a policy document that describes aspirations as
controls is worse than no document.

**Enforced:**

- `SECRET` redaction from anything logged, by credential-shaped parameter name,
  by exact configured value, and by credential-shaped fragment within a larger
  value.
- A credential that cannot safely be sent as a header is never handed to the HTTP
  layer.
- Per-provider kill switches; a disabled provider is not planned.
- Meta defaults to the Standard tier.
- The last error a provider returned is stored in `provider_runtime_health()`
  **after** `_safe_error()` redaction. Runtime health is meant to be read by
  operators and may end up on a status surface; it is not a logging exemption.
  `GROQ_AI_API` is set to a JSON document containing a key and the transport
  exception quoted it, so anything that stores an error string has to store the
  redacted one. Pinned by
  `CircuitBreakerTest::test_the_recorded_error_is_the_redacted_one`.
- Per-provider token and cost accounting. Every adapter returns a normalised
  `usage` block, checked structurally by `EveryAdapterReportsUsageTest` so a
  provider added later cannot quietly omit it. Cost is only claimed for models
  with a vendor-verified price; everything else reports tokens and a null cost.

**Not enforced — policy only, at the time of writing:**

- Nothing in the router inspects a *data class*. There is no mechanism that stops
  `PRIVATE_OFFICE` content from being passed to `route_undx_request` and
  forwarded to any configured provider. The restrictions in the matrix above are
  currently upheld by the callers, and the context that reaches the router is
  compiled upstream by `services/undx_policy.py` and the Private Office gate.
- A per-provider `privacy_class` ceiling, checked in `provider_priority()`, is the
  obvious next control and does not exist yet.
- Spend is measured, not capped. `spend_state()` reports per-provider monthly
  totals, but nothing refuses a call for being over budget, and the totals are
  in-memory per process rather than durable.

Anyone relying on this document for a compliance answer should read the second
list first.
