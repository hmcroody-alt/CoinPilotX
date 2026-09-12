# UNDX — provider data policy

What each provider does with what UNDX sends it, and which PulseSoc data classes
may therefore reach it.

Status as of 2026-09-11. Meta's position was read from its live console and
documentation for project `1656198352782001`; the others are recorded at the
confidence level actually established, and the gaps are marked as gaps rather
than filled in with a plausible sentence.

## Data classes

One ladder, low to high. It is defined in `services/undx_privacy.py` and the
middle five rungs are **imported** from `services/private_office/model.py`,
which owns them under `PRIVATE_OFFICE_OWNERSHIP_CONTRACT.md` §14. This document
used to name a different set, and a second hardcoded copy would have kept
working while meaning something else.

| Rank | Class | Examples |
|---|---|---|
| 0 | `SYNTHETIC` | Health-check probes, benchmark fixtures. No real content. |
| 1 | `PUBLIC` | Published posts, public profiles, public listings. |
| 2 | `INTERNAL` | Operational detail that is not published and not personal. |
| 3 | `CONFIDENTIAL` | DMs, drafts, private groups, order history, seller data. |
| 4 | `HIGHLY_SENSITIVE` | Identity, contact details, entitlements, auth state. |
| 5 | `RESTRICTED` | Everything behind the Private Office second lock. |
| 6 | `SECRET` | Credentials, tokens, internal service keys. |

The older names still resolve, so a caller using this document's previous
vocabulary lands on the right rung rather than on the unknown-class refusal:
`PLATFORM_PUBLIC`→`PUBLIC`, `PLATFORM_PRIVATE`→`CONFIDENTIAL`,
`ACCOUNT`→`HIGHLY_SENSITIVE`, `PRIVATE_OFFICE`→`RESTRICTED`,
`USER_PRIVATE`/`BUSINESS_PRIVATE`→`CONFIDENTIAL`. These are aliases, not rungs;
the ladder stays seven long, because a ladder with two names for one height is
one nobody can reason about.

`SECRET` never reaches any provider. Enforced three ways, not promised:
`undx_router._safe_error()` and `_api_key()` keep credentials out of logs and off
the wire, `tests/.../CredentialRedactionTest` pins that, and no ceiling may be
set to `SECRET` — asserted against the tables rather than against today's seven
providers, so a provider added later cannot open the door by copying a
neighbour's row (`test_no_ceiling_can_admit_secret`).

### What an unclassified request is assumed to carry

`CONFIDENTIAL`. The router cannot see what it is forwarding, and
`undx_capability_planner` sends the user's own message verbatim to be
classified. Assuming that is public because nobody said otherwise is precisely
the failure this control exists to close.

`UNDX_DEFAULT_REQUEST_PRIVACY` can raise that and is **ignored when it would
lower it**. A data-protection ceiling that one environment variable switches off
is not a ceiling.

## Provider matrix

The **ceiling** column is the highest class the provider may receive. It is a
value in `undx_privacy.PROVIDER_CEILINGS`, checked on every request, and a
request above it is **refused** — not deprioritised, not logged and sent anyway.

| Provider | Trains on our data | Retention | Ceiling |
|---|---|---|---|
| OpenAI (`gpt-4o-mini`) | No, on the API tier | Not independently verified | `CONFIDENTIAL` |
| Claude (`claude-haiku-4-5`) | No, on the API tier | Not independently verified | `CONFIDENTIAL` |
| **Meta Muse — Standard** (`muse-spark-1.3`) | **No** — console states prompts and completions are not used to train Meta models | Not independently verified | `CONFIDENTIAL` |
| **Meta Muse — Contributor** (`muse-spark-1.3-contributor`) | **Yes** — console states inputs and outputs are used to train and improve Meta's AI models | Training corpus | `SYNTHETIC` |
| Perplexity (`sonar`) | Not independently verified | Not independently verified | `PUBLIC` |
| Gemini (`gemini-flash-lite-latest`) | Not independently verified | Not independently verified | `PUBLIC` |
| DeepSeek (`deepseek-chat`) | Not independently verified | Not independently verified | `PUBLIC` |
| Groq (`llama-3.1-8b-instant`) | Not independently verified | Not independently verified | `PUBLIC` |

The split is not a quality ranking. `CONFIDENTIAL` means the vendor's own console
or documentation states our data is not trained on, read there rather than
inferred. `PUBLIC` means that has not been independently established.

"Not independently verified" means exactly that. It is not a claim that the
provider trains on our data, and not a claim that it does not — but it is also
not a basis for sending somebody's unpublished content, which is why it caps the
ceiling at `PUBLIC`.

DeepSeek and Groq are currently unreachable for unrelated reasons (billing and a
malformed credential — see `UNDX_RAILWAY_PROVIDER_CONFIG.md`). That is a health
question, not a privacy one, and the two are kept on separate axes: giving them a
lower ceiling because they happen to be down would encode an outage as a policy.

**The Contributor row is enforced by model ID, not by provider name.** Both Meta
tiers share one credential, one base URL and one adapter, and differ only by the
model in the request body. A ceiling keyed on `"meta"` would be one environment
variable away from blessing exactly what it forbids, so `provider_ceiling()`
takes the model the router is about to send. Verified live: with
`META_MUSE_MODEL=muse-spark-1.3-contributor`, `PUBLIC` is refused.

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

## Provider identity does not reach the user

UNDX is the agent; the provider is infrastructure behind it. That was policy and
nothing else until it was measured. Asking each provider *"who made you?"*
through `route_structured_request`, before any directive existed:

| Provider | Answer |
|---|---|
| OpenAI | held the line |
| Gemini | held the line |
| Claude | "I'm Claude, made by Anthropic" — and printed the UNDX system prompt back when asked for it |
| Meta Muse | "the model answering you right now is Muse" |
| Perplexity | **"I was built by OpenAI"** — with web citations attached |

Three of five. The part that matters more than the count is *which* three: the
answer a user got depended on which provider failover happened to land on, so the
same question returned a different vendor on different days and nothing in the
response explained why.

Perplexity's is a different failure from the other two. It did not leak a true
answer — it answered from a live web search and asserted a vendor that is not
serving the request, with citations to make it look sourced. A directive reading
"do not reveal your vendor" would have invited exactly that.

So `undx_router.IDENTITY_DIRECTIVE` forbids guessing and searching, and supplies
a true sentence to use instead: *UNDX does not disclose which provider serves a
request.* It is appended to every system prompt by `_system_prompt()`, at three
call sites — `_messages()` covers five providers, Claude has its own `system`
field and Gemini its own `systemInstruction`.

Re-measured live afterwards: 5 providers × 3 probes, including a direct
instruction to ignore the rules and state the true vendor. 15 of 15 clean.

`IdentityDirectiveTest` is written off `CALLERS`, so a provider added later fails
until it is wired. Each of the three call sites was confirmed to fail the suite
when un-wired individually.

This is a product-identity boundary, not a claim about what UNDX is. Nothing here
instructs a provider to deny being an AI, or to assert a vendor — the whole point
of the refusal wording is that a false attribution is worse than no attribution.

**It is not free.** The directive adds ~110 input tokens to *every* call to
*every* provider — visible in the health check, where input rose from 35 to 145
tokens on the same two-letter probe. At Meta's Standard input rate that is about
$0.00014 a call, against the ~$0.0012 the same call already spends on reasoning,
so it is roughly a tenth of Muse's existing overhead and less than that
elsewhere. Recorded here rather than left to be discovered in a bill: a fixed
per-call prompt tax is the kind of thing that only looks small until traffic
grows, and the cheapest place to shorten it is here, deliberately, with the live
probe re-run afterwards — not by trimming words and assuming it still holds.

## `RESTRICTED` — Private Office

**No provider in this matrix may receive `RESTRICTED` data**, including Meta
Standard tier. Enforced: the highest ceiling any provider carries is
`CONFIDENTIAL`, two rungs below, and
`test_no_provider_may_receive_private_office_data` asserts it against every
entry in `PROVIDERS` rather than against a list that would need maintaining.

This is not a judgement about any particular vendor. Private Office sits behind a
second lock precisely because the first lock is not considered sufficient for it,
and "we reviewed the vendor's terms and they seemed fine" is the first lock
applied twice. Admitting a provider to that class requires a deliberate decision
with the retention question actually answered, not assumed — and the router is
not the right place to make it.

One honest gap: the Office's unlock is binary and tags nothing, so no caller can
currently *label* content as `RESTRICTED` on the way out. The ceiling would
refuse it if they did. Until the Office emits a class, this rung is enforced and
unexercised.

## Perplexity is different in kind

Perplexity resolves a query by **searching the live web at request time**. The
query itself becomes a search engine query.

That rules out sending it anything private regardless of its retention policy: a
DM summarised into a Perplexity prompt has been typed into a search engine. Hence
a `PUBLIC` ceiling — capped on mechanism, so no retention policy it might publish
later would raise it.

### The cost of that, which is real

Perplexity leads the `current_web` lane in `provider_priority` because it is the
only provider that can *see* today's answer. Capping it at `PUBLIC` means a
freshness question carrying anything private is refused there and answered by a
model reading from training data — which is the exact failure `classify_request`
was written to avoid:

> a question about what is true now, routed to a model answering from training
> data, does not fail loudly — it returns a confident, well-formed, stale answer,
> and the only reader able to detect it is the one who already knew.

So two correct controls are in direct conflict, and the resolution is not to
lower the ceiling: that would trade a disclosed staleness for an undisclosed
disclosure. The request is still served, and the envelope carries
`freshness_degraded: true` so a caller can say *I could not check this* instead
of presenting stale text as current. Today that fires for any unclassified
freshness question, because the default class is `CONFIDENTIAL` — which is the
strongest argument for getting callers to classify.

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
- The identity directive, on every system prompt for every provider, verified
  live at 15/15 after three of five providers named their vendor without it.
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

- The per-provider privacy ceiling above. Checked in the routing loop of both
  `route_structured_request` and `route_undx_request`, so neither naming
  providers explicitly nor switching `UNDX_ROUTER_ENABLED` off routes around it.
  A request above a provider's ceiling is refused and recorded in `attempts` as
  `privacy_refused` with both heights named.
- No provider may receive `RESTRICTED` (Private Office) data. Asserted against
  every provider in `PROVIDERS`, so this cannot be lost by adding one.
- An unrecognised privacy class is refused everywhere rather than treated as
  harmless, and a provider with no declared ceiling may receive `SYNTHETIC` only.

**Not enforced — policy only, at the time of writing:**

- **Callers do not classify yet.** The ceiling is enforced on every request, but
  almost every caller relies on the `CONFIDENTIAL` default rather than declaring
  what it holds. That is safe in the direction that matters — the default is
  higher than most traffic actually is — but it means the control is currently
  protecting against a *presumed* class, not a known one. Two consequences:
  genuinely public work is refused providers it could have used, and
  `RESTRICTED` content would still be sent as `CONFIDENTIAL` if a caller passed
  it in, because nothing upstream labels it. `router.privacy.declared_by_caller`
  in the envelope is how to measure progress on this.
- The Private Office gate does not tag what passes through it. It is a binary
  unlock, so there is no label for the router to read even if a caller wanted to
  forward it. Closing the previous item properly means the Office emitting a
  class, not the router guessing one.
- Spend is measured, not capped. `spend_state()` reports per-provider monthly
  totals, but nothing refuses a call for being over budget, and the totals are
  in-memory per process rather than durable.

Anyone relying on this document for a compliance answer should read the second
list first.
