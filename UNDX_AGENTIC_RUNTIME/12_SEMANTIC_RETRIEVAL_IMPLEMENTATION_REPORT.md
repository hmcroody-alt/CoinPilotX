# 12 — Semantic Retrieval Implementation Report

**Mission:** integrate Perplexity Embeddings as an additive semantic retrieval layer beside
UNDX's existing lexical retrieval.
**Date:** 2026-08-30
**Branch:** `release/full-sweep-20260826` @ `f1759800`
**Scope honoured:** Perplexity **Embeddings API only**. Nothing was purchased, enabled or
integrated from Perplexity Agent, Search or Router; Meta Model API; or NVIDIA NIM.

---

## Headline

The integration is **built, tested and inert**. Every line of it ships in the `off` state,
where `undx_semantic_retrieval.retrieve()` returns a result byte-identical to today's
`undx_platform_knowledge.retrieve()`. Thirty-nine tests cover the degraded paths.

Three things could not be done from this environment and remain owner steps: **creating the
Perplexity account, purchasing credit, and generating the API key**. Entering payment
credentials and creating accounts are actions I will not perform on your behalf. Because the
key does not exist, **semantic and hybrid retrieval quality were not measured**, and this
report says so rather than substituting a number from the stand-in embedder used for
plumbing tests. That follows your own instruction: *do not declare semantic retrieval
superior merely because it exists.*

The lexical baseline **was** measured, on a 74-case holdout, and it is worse than it looks
from the inside. That measurement is the most useful artefact in this mission.

---

## Required return fields

| Field | Value |
|---|---|
| **PERPLEXITY ACCOUNT** | NOT CREATED — owner step. Target: org `CoinPlotXAI Inc.`, project `PulseSoc UNDX`. |
| **CREDITS PURCHASED** | NONE — owner step. Minimum practical top-up is sufficient; see cost section. |
| **MODEL** | `pplx-embed-v1-0.6b`, configurable via `UNDX_EMBEDDING_MODEL`. No silent substitution anywhere. |
| **RAILWAY SECRET** | NOT SET — owner step. Variable name: `PERPLEXITY_API_KEY`. |
| **SECRETS EXPOSED** | **0.** 4,063 tracked and new files scanned; zero `PERPLEXITY_API_KEY` assignments, zero provider-key-shaped literals in anything I wrote. |
| **VECTOR STORAGE** | Existing PulseSoc database via `services/db.py`. Two new tables. No vector database purchased, no pgvector, no numpy. |
| **DOCUMENTS INDEXED** | 1,673 canonical documents built → 1,667 distinct index rows (six manifest entries share a `doc_id`; the upsert collapses them). 56 `public: false` entries excluded, overlap with the index = 0. |
| **EMBEDDINGS CACHED** | Pass 1: 1,673 embedded. Pass 2 over identical material: **0 embedded, 1,673 served from cache.** |
| **LEXICAL BASELINE** | Recall@1 **0.300** · Recall@3 **0.386** · Recall@5 **0.414** · MRR **0.350** · p50 **1.29 ms** · p95 **1.82 ms** (70 positive cases). |
| **SEMANTIC RESULT** | **NOT MEASURED** — owner-blocked on the API key. |
| **HYBRID RESULT** | **NOT MEASURED** — owner-blocked on the API key. |
| **RECALL IMPROVEMENT** | **NOT MEASURED.** Unknown, and deliberately left unknown rather than estimated. |
| **P50 / P95 LATENCY** | Lexical measured: 1.29 ms / 1.82 ms. Semantic adds one provider round trip plus a 6 ms in-process scan at 256 dimensions. |
| **ESTIMATED MONTHLY COST** | One-time full index: 72,408 tokens ≈ **$0.00029**. Queries: ~10 tokens each; 100,000 uncached queries/month ≈ **$0.004**. Budget guard default: $5/month. |
| **PROVIDER OUTAGE FALLBACK** | **PASS** — verified for 503, 429, timeout, malformed response, missing key, and empty index. |
| **UNDX WITHOUT PERPLEXITY** | **FULLY OPERATIONAL.** This is the default and current state. |
| **SHADOW** | Implemented and tested. **Not promoted.** |
| **QA COHORT** | Implemented and tested, reusing `UNDX_AGENT_QA_USER_IDS`. **Not promoted.** |
| **PRODUCTION** | Implemented and tested. **Not promoted.** `UNDX_SEMANTIC_RETRIEVAL_STAGE=off`. |
| **COMMIT** | **BLOCKED.** `.git/index.lock` exists and cannot be removed from this environment (`unlink: Operation not permitted`); `.git/objects` is likewise unwritable. All work is on disk in the working tree. |
| **PUSH** | **BLOCKED** — same cause. |
| **FINAL VERDICT** | **READY FOR OWNER KEY.** Code complete, inert, reversible by one variable. |

---

## What was built

Four files, 2,403 lines, two of them modified rather than replaced.

`services/undx_embedding_service.py` (714 lines) is the provider edge — the single place
PulseSoc talks to an embedding provider. It owns batching, the retry ladder, cost accounting,
the monthly budget guard, base64 vector encoding, and ten telemetry counters. It never logs
the key, never logs embedded content, and `describe_for_report()` is structurally incapable of
printing the key: it emits the string `"set"` or `"unset"`.

`services/undx_semantic_retrieval.py` (859 lines) is the retrieval layer. It owns the two
tables, the canonical indexer, the vector scan, reciprocal rank fusion, and the authority
filter. `retrieve()` is a drop-in replacement for the existing function with an identical
signature, identical bounds and an identical return shape.

`services/pulse_ai_service.py` changed by **one expression**:

```python
knowledge[0:0] = (
    undx_semantic_retrieval.retrieve(body, user_id=int(user_id))
    or undx_platform_knowledge.retrieve(body)
)
```

The literal `undx_platform_knowledge.retrieve(body)` is preserved deliberately. Your release
gate `scripts/pulsesoc_undx_platform_knowledge_audit.py` greps for that exact string, and the
`or` is a real last-rung fallback rather than dead code kept to satisfy a grep. The gate still
passes all six checks.

`services/undx_brain/config.py` gained nine flags. `PERPLEXITY_API_KEY` is **not** among them,
because that catalog is rendered to operators and no catalog variable holds a secret.

### The chain, as specified

```
QUERY → EMBED QUERY → VECTOR RETRIEVAL → CANDIDATES → AUTHORITY FILTER → UNDX
```

### The fallback ladder, as specified

```
PERPLEXITY UNAVAILABLE → CACHE → EXISTING LEXICAL RETRIEVAL → UNDX
```

Every rung is a test, not a comment. Missing key, 503, 429, timeout, non-JSON body, short
vector list, wrong dimensionality, non-numeric vector, empty index, and a deliberately
sabotaged internal function all land on the lexical path, and in each case the served result
is asserted **equal to** `lexical.retrieve(query)` — not merely non-empty.

---

## The lexical baseline, measured

`scripts/undx_semantic_retrieval_benchmark.py` over `data/undx/semantic_retrieval_holdout.json`
— 70 positive cases plus 4 negatives, spanning exact names, paraphrases, slang, typos,
indirect requests and PulseSoc terminology, in English, Haitian Creole, French and Spanish.
Targets are real manifest entry names, not invented ones.

| | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| **Lexical (production today)** | 0.300 | 0.386 | 0.414 | 0.350 |

Recall@5 by category:

| exact | terminology | typo | slang | paraphrase | indirect |
|---|---|---|---|---|---|
| 1.000 | 0.875 | 0.429 | 0.333 | 0.290 | **0.000** |

Recall@5 by language:

| English | Spanish | French | Haitian Creole |
|---|---|---|---|
| 0.544 | 0.250 | 0.125 | 0.125 |

Three findings follow from this, and they are the reason the mission is worth finishing.

**The baseline scores 1.000 when you already know the answer and 0.000 when you don't.**
Every exact-name query resolves; every indirect query — *"my phone keeps buzzing all night
because of this app"* — misses entirely. A retriever that only works when the user already
knows the capability's internal name is a lookup table, not retrieval.

**The multilingual gap is structural, not a tuning problem.** The manifest's `search_text` is
English CamelCase identifiers repeated:

```
NotificationPreferences -> 'NotificationPreferences NotificationPreferencesScreen NotificationPreferences'
Marketplace             -> 'Marketplace MarketplaceScreen Marketplace'
```

There is no lexical overlap available between *"kijan pou m chanje paramèt notifikasyon yo"*
and that string. No threshold, stop-word list or scoring tweak reaches it. The non-English
scores above are not noise — French and Creole are at 0.125, and the residual comes from
loanwords, not from matching. This is precisely the gap embeddings exist to close, which is
why the 24 non-English cases are the most informative part of the holdout.

**The baseline answers questions it should decline.** Two of four negative controls leaked:

| query | returned |
|---|---|
| `recipe for beef bourguignon` | `performance_traces`, `platform_fee_rules`, `platform_payouts` |
| `who won the 1998 world cup` | `arena_world_events`, `arena_world_history`, `arena_world_state` |

The cause is that `undx_platform_knowledge` matches **substrings**, not tokens, and `for` is
not in its stop-word list — so `for` matches inside `plat**for**m_fee_rules`, and `world`
matches `arena_world_state`. I did **not** change this. It is pre-existing production
behaviour on the protected baseline path, your two existing negative controls still pass, and
silently altering the baseline mid-mission would have corrupted the very comparison this
benchmark exists to make. It is recorded here as a defect to fix deliberately, on its own.

Semantic and hybrid rows are absent from the table above because they cannot yet be filled
honestly. The benchmark reports them as `NOT MEASURED` with `blocked_by:
["PERPLEXITY_API_KEY is unset", "semantic index is empty"]`, and that gate was verified to
flip correctly once an index exists.

---

## Vector storage: the decision and the evidence

**Decision: extend the existing database. Buy nothing.**

I measured before choosing. A brute-force cosine scan over the full 1,673-document corpus, in
pure Python with no numpy:

| dimensions | scan latency |
|---|---|
| 256 | 6.3 ms |
| 512 | 12.6 ms |
| 1024 | 25.3 ms |

A vector index exists to avoid an O(n) scan. At n = 1,673 the scan is already faster than the
network call that produced the query vector, so an index would optimise the cheaper half of
the operation. `numpy` is not in `requirements.txt` and pgvector is not installed; both would
have been new dependencies bought to solve a problem that does not exist at this scale.

Two tables were added through `ensure_schema()`, following the house pattern — this repo
creates schema imperatively in `bot.init_db()` and has no migration framework, so introducing
one here would have been a second pattern, not an improvement.

Vectors are stored as base64 **TEXT**, not BLOB/bytea. SQLite and PostgreSQL adapt binary
parameters differently through `services/db.py`'s `CompatConnection`; text is identical on
both. The storage cost of that choice is about 33%, on roughly 1.7 MB of vectors.

The conditions under which this decision should be revisited are written into the module, so
it is revisitable rather than permanent: roughly 10⁵ vectors, or the moment scan latency
exceeds the provider round trip.

---

## Authority boundary

You wrote: *embeddings provide similarity; embeddings provide zero authority.* That is
enforced structurally rather than by convention, in four places.

The corpus cannot carry authority, because it contains none. It is exactly the entries the
lexical path already serves — source-derived descriptions of what PulseSoc *is*. No account
state, no ownership edges, no permissions, no balances. `public: false` entries are excluded,
and a test asserts the intersection of private manifest ids with indexed ids is empty.

Content class is mandatory, and blank is rejected as loudly as forbidden. `IndexDocument`
requires an explicit `content_class`; `private_message`, `credential`, `payment_information`
and `precise_location` raise `ForbiddenContent`, and so does `""`. A pipeline that silently
drops private documents and a pipeline that silently sends them look identical from the
outside, so neither is permitted to be silent.

The output shape gains nothing. Hybrid results have exactly the keys
`["body", "category", "id", "title"]` — the same four the lexical path has emitted all along.
No downstream consumer can begin trusting a field that did not exist before, because no field
was added.

The similarity score is dropped before the prompt. This is the load-bearing one. A number
reading `0.94` travelling beside a claim is an invitation to treat the claim as verified. The
score is used for ranking inside the module and discarded at the boundary; a test asserts the
strings `score`, `similarity` and `confidence` appear nowhere in the rendered results.

**UNDX EXECUTION GOVERNANCE: UNCHANGED.** No capability, policy, gateway, confirmation or
authorization code was touched. Semantic retrieval answers *"what information is probably
relevant?"* and is architecturally incapable of answering *"is this allowed?"*

---

## Rollout and failure behaviour

`off → shadow → qa → production`, and **any unrecognised value resolves to `off`**.
`UNDX_SEMANTIC_RETRIEVAL_STAGE=prod` is reported as *"is not one of off, shadow, qa,
production"* and the system stays off; a misspelled variable name is surfaced by
`unknown_undx_brain_vars`. A rung reached by typo is a rung nobody decided to enter.

In `shadow`, the semantic path runs for real and the fused result is logged and discarded —
the user receives today's answer, asserted equal to `lexical.retrieve()`. In `qa`, only
`UNDX_AGENT_QA_USER_IDS` receive hybrid results; everyone else, including anonymous callers,
gets the lexical path. The existing cohort list is reused rather than introducing a second
list that could drift out of agreement with the first.

Retry behaviour is bounded and biased toward answering: backoff is capped at 2 s across at
most `UNDX_EMBEDDING_MAX_RETRIES` retries, because this sits inside a user-facing request and
the fallback answer is better than a slow one. `401`/`403` are never retried and the reason
string is deliberately non-specific, since a credential-rejection body may echo material that
should not reach a log line.

The budget guard runs **before** the request, not after. A runaway indexing batch of ~50,000
tokens against a $0.0001 budget raises with zero network calls. `0` disables the guard by
design, and there is a test asserting that too, so nobody later mistakes the opt-out for
enforcement.

Cache identity is `content + model + model version + dimensions + normalization version +
encoding version`. Changing any one of the last five produces a different key; whitespace does
not, because paying twice for the same text with a trailing space would be a real cost bug.

Ten counters are exported through `health()`: `embedding_requests`, `embedding_cache_hits`,
`embedding_cache_misses`, `embedding_provider_errors`, `embedding_429`, `embedding_timeouts`,
`embedding_budget_blocks`, `embedding_latency_ms` (p50/p95), `semantic_retrieval_latency_ms`
(p50/p95), and `semantic_fallback_count`. A test asserts the rendered snapshot contains
neither the key nor embedded content.

---

## Production acceptance matrix

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | API key configured securely | **OWNER BLOCKED** | `api_key='unset'`; no key anywhere in the repo |
| 2 | Secret leak scan | **PASS** | 4,063 files; 0 assignments; 0 key-shaped literals |
| 3 | Minimal live API request | **OWNER BLOCKED** | requires the production key |
| 4 | Canonical index built | **PASS** | 1,673 documents; 56 private excluded; overlap 0 |
| 5 | Query embedding | **OWNER BLOCKED** | requires the production key |
| 6 | Semantic retrieval | **OWNER BLOCKED** | requires a populated index |
| 7 | Hybrid retrieval | **OWNER BLOCKED** | requires a populated index |
| 8 | Lexical fallback identical at `off` | **PASS** | `retrieve() == lexical.retrieve()` |
| 9 | Provider offline → lexical | **PASS** | 503 → 3 bounded attempts → lexical |
| 10 | 429 → bounded retry → lexical | **PASS** | 3 calls, counter = 3, then lexical |
| 11 | Cache prevents repeat payment | **PASS** | pass 2 embedded = 0 |
| 12 | Multilingual queries | **PASS** | ht/fr/es never raise |
| 13 | Authority boundary | **PASS** | `AUTHORITY='none'`; 4 keys; no score |
| — | **UNDX execution governance** | **UNCHANGED** | no governance code touched |

**Regression check.** `tests/undx_agent` has 16 pre-existing failures on this branch
(`test_saved_post_write_pack`, `test_content_graph_intelligence_pack`,
`test_knowledge_map_grounding` citation drift). I verified these are unrelated by restoring
both modified files to their `HEAD` contents and re-running: **the same 16 fail**. My changes
introduce zero regressions. The platform-knowledge release gate passes all six checks.

---

## Owner steps

These are yours because I will not create accounts, enter payment credentials, or handle a
live secret.

1. Sign in at `console.perplexity.ai` and create the project `PulseSoc UNDX` under
   `CoinPlotXAI Inc.`
2. Add a payment method and purchase the **minimum** available credit. The measured workload
   is well under a cent per month; the $50 figure from report 11 is a floor imposed by the
   console, not a requirement of this integration. Do not enable recurring commitments.
3. Generate a key named `PulseSoc-UNDX-Production`. **It is displayed exactly once.**
4. Paste it directly into Railway as `PERPLEXITY_API_KEY`. Do not paste it into a file, a
   chat, a terminal, or this repository.
5. Set `UNDX_SEMANTIC_RETRIEVAL_STAGE=shadow` and let it run. Shadow costs money and changes
   nothing a user sees — that is the point.
6. Re-run `python3 scripts/undx_semantic_retrieval_benchmark.py`. The semantic and hybrid rows
   will populate themselves.
7. Promote to `qa`, then `production`, **only if** hybrid Recall@5 beats 0.414 on the same
   holdout. If it does not, the honest outcome is to leave the flag at `off` and keep the
   $50 of evidence.
8. Clear the stale `.git/index.lock` (`rm -f .git/index.lock`) and fix the `.git/objects`
   permissions, then commit and push. I could not.

---

## Files

| Path | Status |
|---|---|
| `services/undx_embedding_service.py` | new, 714 lines |
| `services/undx_semantic_retrieval.py` | new, 859 lines |
| `tests/undx_agent/test_semantic_retrieval.py` | new, 534 lines, 39 tests passing |
| `scripts/undx_semantic_retrieval_benchmark.py` | new, 296 lines |
| `data/undx/semantic_retrieval_holdout.json` | new, 70 positive + 4 negative cases |
| `services/pulse_ai_service.py` | modified — one expression |
| `services/undx_brain/config.py` | modified — nine flags |
| `.env.example` | modified — `PERPLEXITY_API_KEY=` and nine flags |

---

## Verdict

**READY FOR OWNER KEY.**

What is proven: the integration is inert by default, degrades to today's behaviour under
every failure mode I could construct, carries no authority into the prompt, indexes no private
material, cannot be entered by a typo, and cannot run away with your credits.

What is not proven, and cannot be until the key exists: that it retrieves better. The lexical
baseline is 0.414 Recall@5, collapsing to 0.000 on indirect questions and 0.125 in French and
Haitian Creole. That is the number semantic retrieval has to beat. If it doesn't, the flag
stays at `off`.
