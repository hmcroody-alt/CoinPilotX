# 11 — EXTERNAL PROVIDER ACQUISITION: FINAL REPORT

**Mission:** Acquire the external agent / model APIs UNDX needs
**Organisation:** CoinPlotXAI Inc. · **Project:** PulseSoc UNDX · **Environment:** Production
**Date:** 30 August 2026
**Supersedes:** `10_PROVIDER_ACQUISITION_INTERIM.md` (which recorded this mission as BLOCKED)

**FINAL VERDICT: READY FOR OWNER PAYMENT — one provider, one product, $50 one-time.**

**API KEYS CREATED: 0. SECRETS EXPOSED: 0. PAYMENT SUBMITTED: NONE.**

---

## 0. The short version

Three providers were evaluated. Two are eliminated. The survivor sells one thing UNDX
actually lacks, and it is cheap enough that the cost model is close to a rounding error.

| Provider | Verdict | Reason |
| --- | --- | --- |
| NVIDIA | **DEFER** | Free tier explicitly excludes production. Paid tier is per-GPU infrastructure, which the mission forbids without proven need. |
| Meta | **DEFER** | Model API only. Duplicates cognition UNDX already has five providers for. |
| Perplexity | **BUY — Embeddings API only** | The only genuinely absent capability in the entire repository. |

The headline finding is an inversion of the mission's framing. Every provider's *headline*
product — a chat model, a web search API, an agent runtime — is something UNDX already has,
sometimes several times over. The one thing UNDX does not have is the product none of them
advertise: text embeddings. That is the entire purchase.

---

## 1. What UNDX already has (Stage 4 — deduplication)

Measured from the repository, not assumed.

**Cognition is saturated.** `undx_router.py` already routes across five providers —
OpenAI, Claude, Gemini, DeepSeek and Groq — server-side, so keys never reach the browser.
`COUNCIL_AGENT_PROVIDER_MAP` assigns eight agent roles across them (architect→claude,
research→gemini, builder→openai, optimization→deepseek, rapid_response→groq,
testing→openai, security→claude, documentation→openai). A sixth chat provider adds
procurement surface and no capability.

**Web search is saturated.** `services/pulse_ai_web_search.py` declares Brave, Bing,
SerpAPI and Tavily with a DuckDuckGo Instant fallback. A fifth search provider is a
duplicate.

**Orchestration is owned and must stay owned.** The governed chain — policy evaluation,
confirmation mint/redeem, tool gateway, canonical domain service, verifier, receipt — is
PulseSoc's own and is the thing the mission exists to protect. Any provider selling an
"agent runtime" is competing with it, not completing it, and is rejected on that basis
alone regardless of price.

**Three genuine gaps.** A grep across `services/` and the root modules for
`text-embedding|sentence-transformers|embeddings.create|faiss|pgvector` returns **zero
hits**. `services/embed_service.py` is a false friend — despite the name it performs
"deterministic external embed normalization for PulseSoc surfaces," turning external URLs
into media-shaped objects for feed rendering. It has nothing to do with vectors.
`services/undx_knowledge_map.py` retrieves lexically (`_route_shape`, `_segments_match`,
`_route_matches`) with no cosine similarity anywhere. So:

1. **Embeddings — absent.** Retrieval over the UNDX knowledge corpus is string matching.
2. **Reranking — absent.** Nothing scores or reorders retrieved candidates.
3. **Model-level guardrail — absent.** Note this is defence-in-depth, not a missing
   control: PulseSoc's policy and authorization layers already sit inside the governed
   chain and are the real enforcement point.

`.env.example` confirms the shape from the other direction. It declares `OPENAI_API_KEY`,
`CLAUDE_AI_API`, `Gemini_AI_API`, `DEEPSEEK_AI_API`, `GROQ_AI_API`, `BING_SEARCH_API_KEY`,
`BRAVE_SEARCH_API_KEY`, `SERPAPI_API_KEY` and `TAVILY_API_KEY`. There is no embedding key,
no reranking key, and no Perplexity, NVIDIA or Meta key of any kind.

---

## 2. Perplexity

**RECOMMENDED UNDX ROLE: retrieval substrate for the UNDX knowledge corpus. Embeddings
API only. Nothing else on this platform is purchased.**

| Field | Value |
| --- | --- |
| PRODUCT | Perplexity Embeddings API |
| OFFICIAL URL | `https://docs.perplexity.ai/docs/embeddings/quickstart` |
| API ENDPOINT | `https://api.perplexity.ai/v1/embeddings` |
| AUTH METHOD | `Authorization: Bearer $PERPLEXITY_API_KEY` |
| SUPPORTED MODELS | `pplx-embed-v1-0.6b` (1024-dim), `pplx-embed-v1-4b` (2560-dim), `pplx-embed-context-v1-0.6b`, `pplx-embed-context-v1-4b` |
| CONTEXT | 32K tokens per input; max 512 texts per request; 120,000 tokens total per request |
| STRUCTURED OUTPUT | base64 int8 (default) or packed binary; Matryoshka truncation 128–2560 dims |
| RATE LIMITS | Tier 0 **85 QPS**; Tiers 1–3 170 QPS; Tiers 4–5 335 QPS. Contextualized variants get 5× (415 / 835 / 1,670 QPS). |
| PRICING | `pplx-embed-v1-0.6b` **$0.004 / 1M tokens** · `pplx-embed-v1-4b` **$0.03 / 1M** · `pplx-embed-context-v1-0.6b` **$0.008 / 1M** · `pplx-embed-context-v1-4b` **$0.05 / 1M** |
| FREE CREDIT | **None documented.** No free tier appears anywhere in the official docs. Credits are prepaid. |
| BILLING | Prepaid credits, Stripe, at `https://console.perplexity.ai/project/billing`. Auto-reload available. If credits run out, keys are blocked (401). |
| BILLING PAGE | `https://console.perplexity.ai/project/billing` |
| API KEY PAGE | `https://console.perplexity.ai/project/keys` (project must be created first at `https://console.perplexity.ai/project/settings`) |
| DATA POLICY | **Zero data retention. "We do not retain any query data sent through the API and do not train on any of your data."** Compute hosted on AWS North America. SOC 2 Type II, 2025 HIPAA gap assessment, CAIQlite. |
| COMMERCIAL USE | Permitted. Standard pay-as-you-go; no production restriction of the kind NVIDIA imposes. |

**Three things worth knowing before signing off.**

The embeddings are **unnormalized**, which is unusual. They must be compared with cosine
similarity, or L2-normalized before being stored in a vector database that only supports
inner product. Getting this wrong produces silently wrong retrieval rather than an error.

**There is no SLA.** Asked directly whether it provides uptime or recovery-time assurances,
Perplexity's own FAQ answers: "We do not guarantee this at the moment." Embeddings must
therefore be cached and the retrieval path must degrade to the existing lexical matcher
rather than fail. This is a design constraint on the integration, not a reason to reject it.

**Everything else on the platform is a duplicate.** The Agent API is the renamed Sonar Chat
Completions and the new Router API is explicitly "unified access to open-weight models …
with zero markup" — which is, precisely, what `undx_router.py` already does. The Search API
duplicates the four search providers already wired in. Reranking is not offered at all. So
the purchase is deliberately narrow: **one product, not a platform.**

---

## 3. Meta

**RECOMMENDED FOR UNDX: NO — DEFER.**

Stage 1 asked which of five shapes Meta's offering takes. Answered from Meta's own
server-rendered page metadata: **A and E — direct Meta-hosted inference, and a model API
only.** There is no Meta agent API. Meta additionally publishes downloadable open weights.

| Field | Value |
| --- | --- |
| PRODUCT NAME | Meta Model API |
| OFFICIAL URL | `https://developer.meta.com/ai/products/meta-model-api/` · docs `https://ai.developer.meta.com/docs` |
| MODEL FAMILY | Muse Spark 1.2 (coding), Muse Image (generate/edit/compose), Muse Code (CLI agent), Muse Glimmer (open weights, self-hostable) |
| TOOL CALLING | **Yes** — Meta's own description: "higher first-attempt accuracy and more reliable tool calling" |
| CONTEXT LIMIT | **1M tokens** (Muse Spark 1.2) |
| PAID PRICING | **UNVERIFIED.** Search-derived figures ($1.25/1M input, $4.25/1M output, $0.15/1M cached input) could not be confirmed against an official pricing page and **must not be relied on**. |
| API BASE URL / RATE LIMIT / DATA RETENTION / TRAINING POLICY | **UNVERIFIED** — see §6. |
| RECOMMENDED FOR UNDX | **No.** |

The recommendation does not depend on the unverified price, which is why this report is not
blocked on it. Muse Spark is a *coding* model with tool calling. Its natural home in this
repository is the `builder` and `testing` council roles, both currently served by OpenAI —
so it is a substitution question inside an already-saturated layer, not a gap. Even at its
best it improves UNDX's ability to propose diffs against the repo. It does nothing for
PulseSoc's users.

**One governance flag for the record.** Meta advertises a `muse-spark-1.2-contributor`
tier, rate-limited by tokens in a rolling five-hour window, whose traffic reportedly may be
used to improve Meta's products. PulseSoc handles user-generated content. If Meta is ever
adopted, the contributor tier must be excluded by policy and the standard `muse-spark-1.2`
model ID pinned explicitly. Recording this now so the decision is not made by accident later.

---

## 4. NVIDIA

**RECOMMENDED: NO — DEFER. Eliminated on NVIDIA's own published terms.**

| Field | Value |
| --- | --- |
| PRODUCT | NVIDIA NIM / API Catalog / NeMo Retriever |
| OFFICIAL URL | `https://docs.api.nvidia.com/nim/docs/product` (modified 6 Aug 2026) |
| HOSTED OR SELF-HOSTED | Both; production use requires AI Enterprise licensing |
| FREE CREDITS | Developer Program access is **"for prototyping, research, development and testing purposes only."** Production is defined as **"any non-testing activity including activity serving real end-users."** |
| PRICING | AI Enterprise **from $4,500 per GPU per year, or ~$1 per GPU per hour in the cloud** — priced per GPU, not per NIM |
| GPU REQUIREMENTS | Yes — this is infrastructure procurement, not an API subscription |
| BEST UNDX ROLE | NeMo Retriever embeddings and reranking (the reranking gap) |

PulseSoc serves real end-users, so the free tier does not cover it by NVIDIA's own
definition. The paid tier is GPU infrastructure, and the mission states: *do not buy GPU
infrastructure unless there is a proven requirement.* There is no such proof, and at the
volumes modelled in §5 there will not be one.

**Preserved as a zero-cost option:** NVIDIA offers a free 90-day AI Enterprise trial. That
is a legitimate way to benchmark NeMo Retriever embeddings and reranking against Perplexity
before any further spend, and it is the recommended route if reranking is later prioritised.

---

## 5. Cost model (Stage 5)

Modelled from the verified per-token prices in §2. Assumptions are stated because they
drive the answer more than the prices do.

**Scope 1 — UNDX knowledge corpus only. No user content leaves PulseSoc.** Assumes a ~2M
token corpus (routes, capabilities, product knowledge) embedded once with a ~200k
token/month delta, and three retrieval-bearing UNDX turns per active user per month at
~40 tokens each.

| Active users | Tokens/month | `v1-0.6b` @ $0.004/1M | `v1-4b` @ $0.03/1M |
| --- | --- | --- | --- |
| 1,000 | 0.32M | **$0.001** | **$0.010** |
| 10,000 | 1.4M | **$0.006** | **$0.042** |
| 100,000 | 12.2M | **$0.049** | **$0.37** |
| 1,000,000 | 120.2M | **$0.48** | **$3.61** |

**Scope 2 — additionally embedding PulseSoc user content** (posts, reel captions), assuming
20 items per user per month at ~100 tokens each. **This is a separate owner decision, not
part of this recommendation** — see §7.

| Active users | Added tokens/month | `v1-0.6b` | `v1-4b` | Combined total (`v1-4b`) |
| --- | --- | --- | --- | --- |
| 1,000 | 2M | $0.008 | $0.06 | **$0.07** |
| 10,000 | 20M | $0.08 | $0.60 | **$0.64** |
| 100,000 | 200M | $0.80 | $6.00 | **$6.37** |
| 1,000,000 | 2,000M | $8.00 | $60.00 | **$63.61** |

**Throughput.** Even Scope 2 at 1M users is ~23M embed operations per month. Batched at the
documented 512 texts per request that is ~45,000 requests per month; entirely unbatched it
is roughly 9 QPS. **Tier 0's 85 QPS already carries one million users.** No tier upgrade is
required for throughput at any modelled scale — a point worth making explicitly, because it
means the purchase below is buying credits, not capacity.

---

## 6. What is verified and what is not

Verified means fetched from the provider's own documentation and quoted above.

**Verified:** all four Perplexity embedding prices; embedding rate limits at every tier;
input and batching limits; the endpoint, auth method and key-creation flow; the zero-data-
retention and no-training policy; SOC 2 Type II; the absence of any SLA; the absence of a
Perplexity reranking product. NVIDIA's production exclusion and per-GPU pricing. Meta's
product family, 1M-token context and tool-calling support.

**Not verified:** Meta's pricing, rate limits, data retention and training-on-customer-data
policy. Perplexity's Agent API and Search API per-model pricing.

Both gaps have the same cause and neither blocks the decision. The Perplexity pricing page
and Agent API models page return ~91,000 and ~94,000 characters, above the fetch tool's
ceiling, and the overflow is written outside any path the sandbox can reach. Meta's pages
are client-rendered and return only metadata to a fetch; the browser extension is still
permission-denied on `developer.meta.com`. **The missing figures belong exclusively to
products this report recommends against buying.** Every number the recommendation rests on
was read from an official page.

The embeddings prices were recoverable because `docs.perplexity.ai` publishes an
`llms.txt` index and serves per-page markdown, so the small dedicated pages could be
fetched even though the aggregate pricing page could not. Worth noting that a search
summary asserted embeddings were billed at Sonar Pro's $3/$15 per 1M — roughly **750×** the
actual `v1-0.6b` price. Holding to official sources was not pedantry here.

---

## 7. UNDX provider architecture

```
USER
 └─> UNDX
      └─> PLANNER / COGNITION            <── existing 5-provider router (unchanged)
           │                              <── [NEW] Perplexity Embeddings, retrieval aid
           └─> PULSESOC CAPABILITY REGISTRY
                └─> POLICY / AUTHORIZATION
                     └─> CONFIRMATION
                          └─> TOOL GATEWAY
                               └─> DOMAIN SERVICES / WORKERS
                                    └─> CANONICAL STATE
                                         └─> VERIFICATION
                                              └─> OBSERVATION
                                                   └─> REPLAN / COMPLETE
```

Perplexity Embeddings attaches **beside** the planner, above the registry. It never touches
the governed chain. This is the safest possible shape for an external dependency and not by
accident: **an embeddings endpoint is structurally incapable of mutating PulseSoc state.**
It converts text to vectors and returns them. It cannot become the authority for an account
mutation because it has no vocabulary for one. Nothing in the capability registry, policy,
gateway, confirmation, ownership, verification or authorization path changes.

**Data-scope recommendation.** Start with Scope 1 — embed only UNDX's own knowledge corpus:
routes, capability specs, product documentation. No user content. Perplexity's zero-
retention and no-training policy is strong and independently attested, but sending PulseSoc
user posts and messages to a third party is a governance decision that belongs to the owner
and should be taken deliberately, on its own terms, not absorbed into a $50 infrastructure
purchase. Scope 1 delivers the retrieval improvement UNDX needs and costs under a dollar a
month at a million users.

---

## 8. Acquisition tiers (Stage 7)

**MUST HAVE NOW**

Perplexity Embeddings API. It is the only verified capability gap that materially improves
UNDX as a governed agentic system, and it replaces lexical string matching in
`undx_knowledge_map.py` with semantic retrieval — which is the difference between UNDX
finding the right capability and UNDX guessing.

Recommended model: **`pplx-embed-v1-0.6b`** to start. 1024 dimensions and 32K context are
ample for this corpus, and at $0.004/1M it is 7.5× cheaper than the 4b. Upgrade only if
retrieval quality measurably underperforms; the Matryoshka support means dimensions can be
tuned without re-procuring.

**HIGH VALUE NEXT**

Reranking — currently unfilled by any provider in this evaluation. Evaluate NeMo Retriever
reranking inside NVIDIA's free 90-day AI Enterprise trial before spending anything. Do not
purchase.

**DEFER**

Meta Model API (duplicate cognition). NVIDIA NIM / AI Enterprise (production-excluded free
tier; per-GPU paid tier). Perplexity Agent API, Router API and Search API (all duplicate
existing UNDX subsystems). Model-level guardrails (defence-in-depth; the governed chain is
the real control).

---

## 9. Payment handoff (Stage 9) — OWNER ACTION REQUIRED

**No payment information has been entered. No purchase has been made. No account has been
created. No API key exists.** The steps below are for the owner to perform.

- **PROVIDER:** Perplexity AI
- **PRODUCT:** Embeddings API (`pplx-embed-v1-0.6b`)
- **PLAN:** Pay-as-you-go prepaid credits. No subscription. No enterprise tier.
- **ESTIMATED COST:** **$50 one-time**, prepaid.
- **WHAT PAYMENT ENABLES:** API key issuance. Perplexity blocks keys with a zero credit
  balance, so some credit is required before the first call.
- **WHY $50 SPECIFICALLY:** The *technical* minimum is far lower — §5 shows under $1/month
  at a million users on Scope 1, and Tier 0's 85 QPS already covers that load. $50 is
  recommended as a practical floor: it avoids repeated top-ups, funds years of usage at
  modelled volumes, and incidentally reaches Tier 1 (170 QPS) for headroom. **A smaller
  initial purchase is technically sufficient and would not compromise the integration.**
- **ANY FREE TIER:** None found in the official documentation.
- **ANY COMMITMENT:** None. Credits are prepaid and consumed; there is no recurring charge
  and no contract term.
- **CANCELLATION TERMS:** Stop calling the API and stop buying credits. Disable auto-reload
  to prevent further charges. Unused credits have previously been refundable within 14 days
  per Perplexity's help centre — **confirm current terms at purchase rather than relying on
  this report.**

**Setup sequence — Stage 8 naming.**

1. Sign in at `https://console.perplexity.ai`.
2. **Project settings** → `https://console.perplexity.ai/project/settings` → create the
   project. Name it **`PulseSoc UNDX`**. Enter organisation details as **CoinPlotXAI Inc.**
   with the registered address and tax details — these appear on invoices.
3. **Billing** → `https://console.perplexity.ai/project/billing` → **Add payment method**
   (this alone does not charge the card) → **Buy more credits** → $50.
4. Optionally enable **Auto reload** — but note this creates a standing charge
   authorisation, so set the threshold deliberately.
5. **API keys** → `https://console.perplexity.ai/project/keys` → **+ Generate API Key**.
   Name it **`PulseSoc-UNDX-Production`**.

**A note on step 5.** Perplexity has no separate "environment" concept — a project holds
keys directly, so the key name is what carries the Production designation. Name it
accordingly or the distinction is lost.

**The key is displayed exactly once and cannot be retrieved afterwards from the console or
from any endpoint.** Copy it before leaving the page.

---

## 10. Secrets handling

**SECRETS EXPOSED: 0. REQUIRED: 0.**

The key must never be pasted into this chat, committed to the repository, or written into
source. Set it directly in Railway:

- **Variable name:** `PERPLEXITY_API_KEY` — both official SDKs read this by default, so
  matching it avoids a wrapper.
- **Set via:** the Railway dashboard, or `set-variables`, entered by the owner.
- **Also add to `.env.example`** as a *name only, with no value*, matching the convention
  already used for the nine existing provider keys.

Rotation is supported programmatically via `POST /generate_auth_token` and
`POST /revoke_auth_token`, both on `https://api.perplexity.ai`. Perplexity recommends a
90-day rotation cadence.

---

## 11. Status against the mission's reporting fields

| Field | Status |
| --- | --- |
| MUST BUY NOW | Perplexity Embeddings API — $50 prepaid credits |
| OPTIONAL | None |
| DEFER | Meta Model API; NVIDIA NIM / AI Enterprise; Perplexity Agent, Router and Search APIs; model-level guardrails |
| TOTAL EXPECTED STARTING COST | **$50 one-time.** Recurring: **under $1/month at 1M users** (Scope 1, `v1-0.6b`) |
| PAYMENT PAGES READY | Yes — `https://console.perplexity.ai/project/billing`, owner-operated |
| API KEYS CREATED | **0** |
| RAILWAY VARIABLES READY | `PERPLEXITY_API_KEY` — declared, not set. Awaiting key. |
| SECRETS EXPOSED | **0** |
| ARCHITECTURE PRESERVED | Yes — no change to registry, policy, gateway, confirmation, ownership, verification or authorization |
| FINAL VERDICT | **READY FOR OWNER PAYMENT** |

---

## 12. Honest limitations

Two Meta fields and two Perplexity fields remain unverified, all four belonging to products
this report recommends against buying. Should the owner want Meta reconsidered, resolving it
requires browser-extension read permission on `developer.meta.com` and
`ai.developer.meta.com`; the fetch path cannot substitute, because those pages are
client-rendered.

The cost model's *prices* are verified; its *volumes* are assumptions, labelled as such in
§5. If PulseSoc's real per-user retrieval rate is ten times my estimate, the conclusion is
unchanged — ten times "under a dollar a month" is still under ten dollars a month. The
recommendation is robust to being substantially wrong about volume, which is the main
reason to be comfortable with it.

No purchase has been made and no payment information has been entered anywhere.
