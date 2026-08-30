# UNDX EXTERNAL PROVIDER ACQUISITION — INTERIM REPORT

Mission: acquire the external agent / model APIs UNDX needs.
Status: **PARTIAL — BLOCKED ON BROWSER ACCESS.**
Date: 30 August 2026. Organization target: CoinPlotXAI Inc. Project: PulseSoc UNDX.

Every claim below is marked either VERIFIED (I opened the official page and read it) or
UNVERIFIED (I could not open the page; the figure comes from a search extraction and must
not be treated as fact). No payment information was entered. No account was created. No
key was generated. No secret appears anywhere in this file.

---

## 1. WHAT UNDX ALREADY HAS — measured from the repository, not assumed

This is Stage 4 done first, because it decides which of the three providers can possibly be
worth money.

**Cognition — already covered five ways.** `undx_router.py` defines `PROVIDERS` with OpenAI,
Claude, Gemini, DeepSeek and Groq, each with its own key env var and model env var, routed
server-side so keys never reach the browser. `COUNCIL_AGENT_PROVIDER_MAP` already assigns
eight agent roles across those five. A sixth general-purpose chat provider adds a
procurement relationship and no capability.

**Web search — already covered four ways plus a fallback.** `services/pulse_ai_web_search.py`
declares Brave, Bing, SerpAPI and Tavily, with a DuckDuckGo instant-answer fallback that is
always available. A fifth search provider is a duplicate unless it is materially better or
cheaper, which has not been demonstrated.

**Embeddings — absent.** A repository-wide grep for `text-embedding`, `embeddings.create`,
`sentence-transformers`, `faiss` and `pgvector` across `services/` and the root modules
returns nothing. `services/embed_service.py` is unrelated: it normalises external media
URLs for feed rendering. UNDX has no vector representation of anything.

**Reranking — absent.** No reranker of any kind.

**Retrieval — lexical, not semantic.** `services/undx_knowledge_map.py` retrieves by route
shape matching and segment comparison over a declared capability registry. It is
deterministic and auditable, which is a virtue, but it cannot answer a question phrased in
words the registry does not contain.

**Model-level guardrails — absent.** `services/security_guard.py` and
`services/schema_guard.py` are code-level validators, not a safety classifier.

**Conclusion for Stage 4.** The genuine gaps are **embeddings**, **reranking** and
optionally a **safety classifier**. Chat inference and web search are saturated. Any
provider proposal that sells UNDX another chat model or another search index is buying a
duplicate, which the mission forbids.

---

## 2. NVIDIA — VERIFIED, AND THE ANSWER IS NO FOR NOW

Source opened: `https://docs.api.nvidia.com/nim/docs/product` (General NIM FAQ, page
modified 6 August 2026) and `https://docs.api.nvidia.com/nim/docs/retrieval-test`
(Retriever NIMs, modified 6 August 2026). Seed blog from the owner's message also read.

| Field | Finding |
| --- | --- |
| PRODUCT | NVIDIA NIM microservices; NVIDIA API Catalog at build.nvidia.com; NeMo Retriever NIMs |
| OFFICIAL URL | `https://build.nvidia.com/explore/discover` (catalog); `https://docs.api.nvidia.com/nim/docs` (docs) |
| HOSTED OR SELF-HOSTED | Both. NVIDIA-hosted endpoints on DGX Cloud for prototyping; Docker containers for self-hosting |
| EMBEDDINGS | Yes — NeMo Retriever text embedding NIM (e.g. NV-EmbedQA-Mistral7B-v2) |
| RERANKING | Yes — NeMo Retriever text reranking NIM |
| API SHAPE | OpenAI-API-compatible (VERIFIED: "expose an API compatible with the OpenAI API standard") |
| FREE ACCESS | Free NVIDIA Developer Program membership grants hosted API endpoints and self-hosting on up to 16 GPUs |
| **COMMERCIAL TERMS** | **Developer Program access is "for prototyping, research, development and testing purposes only."** Production is defined as "any non-testing activity including activity serving real end-users." |
| PRICING | NVIDIA AI Enterprise licence required for production. **From $4,500 per GPU per year, or ~$1 per GPU per hour in the cloud.** Priced per GPU, not per NIM, and identical regardless of GPU size |
| TRIAL | Free 90-day NVIDIA AI Enterprise evaluation licence |
| GPU REQUIREMENT | Self-hosted NIM requires CUDA on an NVIDIA-Certified System |
| **RECOMMENDED** | **NO — DEFER** |

**Why defer.** PulseSoc serves real end-users, so the free tier's licence does not cover it
by NVIDIA's own definition. Production means AI Enterprise at $4,500/GPU/year plus GPU
capacity PulseSoc does not have and Railway does not provide. The mission's instruction —
"Do not buy GPU infrastructure unless there is a proven requirement" — is decisive: the
requirement is not proven, because a hosted per-token embedding API can fill the same gap
with no floor cost. NVIDIA becomes worth revisiting only if embedding volume grows large
enough that a fixed GPU cost beats per-token pricing, or if data residency forces
self-hosting. Both are future conditions, neither is today's.

**One legitimate near-term use:** the 90-day AI Enterprise trial is free and would let UNDX
benchmark NeMo Retriever embeddings against a hosted alternative before committing to
either. That is evaluation, not production, and it costs nothing.

---

## 3. PERPLEXITY — PRODUCT SURFACE VERIFIED, PRICING NOT

Sources opened: `https://www.perplexity.ai/api-platform` (official product page, © 2026) and
`https://www.perplexity.ai/help-center/en/articles/10354847-api-payment-and-billing`
(official help centre, updated 23 July 2026).

Perplexity's API Platform ships **four** products, not one:

**Agent API** (VERIFIED) — "Model-agnostic platform for search and agentic workflows." It
orchestrates agentic workflows across supported frontier models with built-in web search,
URL fetching and reasoning controls. Perplexity's Sonar Chat Completions has been renamed to
Agent API; a migration document exists. Quickstart:
`https://docs.perplexity.ai/docs/agent-api/quickstart`.

**Search API** (VERIFIED) — real-time ranked web results, domain filtering, multi-query
search, content extraction. "Low-latency hybrid search, combining semantic methods, LLM
ranking, and human feedback."

**Embeddings API** (VERIFIED) — "Generate high-quality text embeddings for semantic search,
RAG, and machine learning applications."
Quickstart: `https://docs.perplexity.ai/docs/embeddings/quickstart`.

**Billing** (VERIFIED) — pay-as-you-go credits purchased in the API Console at
`https://console.perplexity.ai/`, Billing tab, "Add payment method" and "Buy more credits."
Automatic Top Up replenishes when the balance drops below $2. Refunds are possible within
14 days of purchase if the credits are unused; used credits are non-refundable. A Perplexity
subscription is **not** required for API access.

| Field | Finding |
| --- | --- |
| API KEY / CONSOLE PAGE | `https://console.perplexity.ai/` |
| BILLING PAGE | `https://console.perplexity.ai/` → Billing |
| PRICING PAGE | `https://docs.perplexity.ai/docs/getting-started/pricing` |
| API TERMS | `https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service` |
| **PRICING** | **UNVERIFIED — see blocker B2** |
| RECOMMENDED UNDX ROLE | Embeddings API — candidate for MUST HAVE NOW, pending price verification. Agent API and Search API — duplicates of the existing five-provider router and four-provider search stack; DEFER |

**Unverified figures, recorded here so they are not lost, and explicitly not to be relied
on:** search extraction from the official pricing page reports Sonar Pro token pricing of
$3 per 1M input and $15 per 1M output, plus a per-request fee that varies by search context
size (Low / Medium / High), and states that citation tokens are no longer billed except for
Sonar Deep Research. **No embeddings price was obtainable at all.** Since the embeddings
product is the only Perplexity product I am recommending, the one number that matters is
precisely the one I could not verify.

---

## 4. META — PRODUCT CONFIRMED, EVERYTHING ELSE UNVERIFIED

Sources opened: `https://developer.meta.com/ai/products/meta-model-api/` and
`https://ai.developer.meta.com/docs`. Both returned **page metadata only** — the document
bodies are rendered client-side by JavaScript, which the fetch tool does not execute, and
the browser tool is denied read permission on these domains (blocker B1).

What the official page metadata does establish, and this is Meta's own text rather than a
search result:

> "Build with Muse Spark through Meta Model API, generate and edit images with Muse Image,
> run the Muse Code CLI, or download Muse Glimmer open weights and run them on your own
> hardware."

So the Stage 1 question — is Meta offering direct inference, a developer-account API, a
partner-hosted arrangement, an agent API, or a model API — resolves as follows, on official
evidence: **Meta offers direct Meta-hosted inference through Meta Model API (a model API,
not an agent API), plus downloadable open weights (Muse Glimmer) for self-hosting.** There
is no evidence of a Meta agent runtime that would compete with UNDX's own orchestration
layer, which is the correct outcome architecturally — UNDX must remain the planner.

| Field | Finding |
| --- | --- |
| PRODUCT NAME | Meta Model API |
| OFFICIAL URL | `https://developer.meta.com/ai/products/meta-model-api/` |
| DOCS | `https://ai.developer.meta.com/docs` |
| MODEL FAMILY | Muse Spark (text), Muse Image (image), Muse Code (coding + CLI agent), Muse Glimmer (open weights) |
| KEY CREATION PAGE | `https://ai.developer.meta.com/docs/api-keys/` |
| TERMS | `https://ai.developer.meta.com/legal/terms-of-service` |
| COST GUIDANCE | `https://developer.meta.com/ai/docs/deployment/cost-projection/` |
| SAFETY MODEL | Llama Guard 4 model card published at `https://developer.meta.com/ai/docs/model-cards-and-prompt-formats/llama-guard-4/` |
| TOOL CALLING / STRUCTURED OUTPUT / CONTEXT / RATE LIMITS / DATA RETENTION / TRAINING POLICY | **UNVERIFIED** |
| **PRICING** | **UNVERIFIED — see blocker B1** |
| RECOMMENDED FOR UNDX | **NO for cognition** (sixth chat provider is a duplicate). Llama Guard 4 is worth evaluating for the guardrail gap, but no price or terms were verifiable |

**Unverified figures, recorded and not relied on:** search extraction reports Meta Model API
pay-as-you-go at $1.25 per 1M input and $4.25 per 1M output for `muse-spark-1.2`, $0.15 per
1M cached input, $0.01 per image for Muse Image, and a rate-limited "contributor tier"
(`muse-spark-1.2-contributor`) whose traffic "may be used to improve our products." That
last clause, if real, would matter a great deal for a social platform handling user content
and must be read in the actual terms of service before any key is created.

---

## 5. PROVISIONAL PROVIDER ARCHITECTURE

External providers sit **above** the governance chain as cognition and retrieval suppliers.
None of them is ever the authority for a PulseSoc account mutation. The chain is unchanged:

```
USER → UNDX → PLANNER/COGNITION → PULSESOC CAPABILITY REGISTRY → POLICY/AUTHORIZATION
     → CONFIRMATION → TOOL GATEWAY → DOMAIN SERVICES/WORKERS → CANONICAL STATE
     → VERIFICATION → OBSERVATION → REPLAN/COMPLETE
```

| Layer | Supplier | Change |
| --- | --- | --- |
| Cognition / planning | OpenAI, Claude, Gemini, DeepSeek, Groq via `undx_router` | none — saturated |
| Web search | Brave, Bing, SerpAPI, Tavily, DuckDuckGo | none — saturated |
| **Embeddings** | **new — hosted, per-token** | **the real gap** |
| **Reranking** | **new — hosted, per-query** | **the real gap** |
| Guardrail classifier | candidate: Llama Guard 4 | evaluate only |
| GPU infrastructure | none | explicitly not purchased |

---

## 6. TIERS

**MUST HAVE NOW** — a hosted embeddings API, on per-token pricing, with no fixed floor.
Perplexity's Embeddings API is the leading candidate because the account, console and
billing path are already verified and PulseSoc would hold a single relationship covering
embeddings today and search later if ever needed. **Contingent on reading the embeddings
price**, which I could not.

**HIGH VALUE NEXT** — reranking, once embeddings are in production and retrieval quality can
actually be measured. Buying a reranker before there is anything to rerank is premature.

**DEFER** — NVIDIA AI Enterprise and all GPU infrastructure; Meta Model API as a cognition
provider; Perplexity Agent API and Search API. All three are duplicates or carry a fixed
cost floor with no proven requirement.

**TOTAL EXPECTED STARTING COST: cannot be stated.** Not because the analysis is incomplete
but because the one price that drives it — hosted embeddings — is on a page I could not
open. Producing a number here would mean inventing it, which the mission forbids.

---

## 7. BLOCKERS

**B1 — the browser cannot read the provider domains.** `developer.meta.com` returns
"Permission denied for reading page content on this domain" for both page reads and
screenshots. `docs.perplexity.ai` and `developer.nvidia.com` return "Navigation to this
domain is not allowed." Meta's pages are client-rendered, so the fetch tool returns metadata
only and cannot substitute. **Owner action: grant the Chrome extension read permission for
`developer.meta.com`, `ai.developer.meta.com`, `docs.perplexity.ai` and
`developer.nvidia.com`** — or move already-open tabs on those domains into the extension's
tab group.

**B2 — Perplexity's docs pages exceed the fetch size limit.** The pricing page returns
91,677 characters, above the tool's ceiling, and the overflow file is written outside every
path the sandbox can reach, so it cannot be chunked or queried. This is why Perplexity
pricing is UNVERIFIED despite the domain being fetchable. Resolving B1 resolves this too.

**B3 — Stages 8 through 10 cannot be reached by fetching at all.** Account creation, key
generation and the payment handoff are interactive. They require a working browser session
on the provider's console, so B1 gates them entirely.

---

## 8. STATUS AGAINST THE REPORTING FIELDS

| Field | Value |
| --- | --- |
| MUST BUY NOW | hosted embeddings — provider selection pending price verification |
| OPTIONAL | Llama Guard 4 evaluation; NVIDIA 90-day AI Enterprise trial (free, evaluation only) |
| DEFER | NVIDIA AI Enterprise production licence; GPU infrastructure; Meta cognition; Perplexity Agent + Search |
| TOTAL EXPECTED STARTING COST | **NOT ESTABLISHED** — blocked, see §6 |
| PAYMENT PAGES READY | Perplexity: `https://console.perplexity.ai/` → Billing. Meta: not reached. NVIDIA: not applicable |
| API KEYS CREATED | **0** |
| RAILWAY VARIABLES READY | none written. Naming would follow the existing convention in `undx_router.PROVIDERS` |
| SECRETS EXPOSED | **0** |
| **FINAL VERDICT** | **PARTIAL — BLOCKED. Not ready for owner payment.** |

No purchase should be made on the strength of this document. Two of the three providers are
already eliminated on verified evidence, which is real progress; the survivor's price is the
one fact still missing, and it is the fact the decision turns on.
