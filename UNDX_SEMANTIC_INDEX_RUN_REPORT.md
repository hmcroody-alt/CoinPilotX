# UNDX semantic retrieval — canonical index + frozen holdout

Sections D, E, F, G, H of the PROCEED directive. Run on the temporary Railway service,
2026-08-30, deployment `4e15ad60-4a5a-4cd9-b8d5-7c18395a8443`, source
`hmcroody-alt/CoinPilotX@main` = `18c6022145a5162cffb79c37644fdb1ca33f4e49` (contains the
`base64_int8` wire-contract fix).

---

## Why the first run measured nothing

The earlier run on this service (`b03b7c7b`) reported hybrid exactly equal to lexical and
semantic exactly 0.0, and the acceptance script computed `KEEP_OFF` from those numbers.
That was not a finding about retrieval quality. It was an artifact: all three bulk
provider calls returned HTTP 429, zero documents reached `undx_semantic_index`, and a
benchmark over an empty index necessarily scores semantic at zero and returns hybrid
unchanged from lexical.

The cause was batch size, not the account. `scripts/undx_semantic_live_acceptance.py`
hands the whole corpus to `embed.embed_texts`, which batches to the provider's documented
ceiling of 512 inputs per request. The shared client then retries with backoff capped at
two seconds — correct behaviour inside a user-facing request, where a fast lexical
fallback beats a slow answer, and wrong behaviour for a bulk load, which spends three
attempts inside 1.6 seconds and gives up.

`scripts/undx_paced_canonical_index.py` (new, in this repo) does the bulk load with its
own pacing instead of weakening the shared client: 16 documents per request, 1.5 s
between requests, backoff in tens of seconds, and an abort after four consecutive refused
chunks. With that pacing the provider returned **zero 429s across 105 requests**.

It also refuses to run the benchmark over an empty index. `build_index()` in the
acceptance script returns `status: PASS` whenever `index_documents` returns at all —
including the run where every provider call failed — which is the false-success surface
Stage 20 asks to be guarded against, sitting inside the acceptance script itself. That is
a separate defect worth fixing in `scripts/undx_semantic_live_acceptance.py`.

---

## D — Temporary indexing infrastructure

| | |
|---|---|
| Service | `TEMP-undx-canonical-index-DELETE-AFTER` (`9d6d2618-859d-4f07-ba9f-8635ff468639`) |
| Project | `coinpilotx-alert-worker` (`111b3838-09d4-4f13-8b8b-6ed332bad06f`) |
| Environment | production (`8bf01340-99d0-49be-a951-abffc17aa4d3`) |
| Source | `hmcroody-alt/CoinPilotX@main` `18c60221` |
| Domain | none generated |
| Restart policy | NEVER |
| Secrets | `DATABASE_URL` and `PERPLEXITY_API_KEY` by Railway reference only |
| Not present | OpenAI, Groq, Claude, Stripe, payment, LiveKit keys |

No production worker was taken offline. No user traffic reached this service.

## E — Index run

| Metric | Value |
|---|---|
| Documents attempted | 1,673 |
| Documents indexed | 1,673 |
| Documents failed | 0 |
| Newly embedded | 1,672 |
| Served from cache | 1 |
| Tokens (provider-billed) | 63,078 |
| Provider calls | 105 |
| Failed calls | 0 |
| HTTP 429 | 0 |
| Timeouts | 0 |
| Budget blocks | 0 |
| Duration | 189.2 s |
| Estimated cost | $0.000252 |

Pre-flight estimate was 1,673 documents / 72,408 tokens / $0.00029 — the estimator
over-counts by design, and the real bill came in under it.

Probe: PASS, 256 dimensions, 453.4 ms, unit-normalised, no provider error.

Corpus is `canonical_documents()` only: the sanitised source-derived platform manifest,
content class `canonical_public`. No private messages, no account state, no user content
was sent to the provider.

**One discrepancy worth recording.** The indexer wrote 1,673 documents but
`undx_semantic_index` holds **1,667**. `doc_id` is the primary key and the indexer deletes
by `doc_id` before inserting, so six manifest entries share a `doc_id` with an earlier
entry and collapse onto it. Six of 1,673 is not material to the benchmark below — it makes
the measured recall a floor rather than a ceiling — but the manifest should not be
producing duplicate ids, and that is worth a look independently of this mission.

Two telemetry fields are reported as zero and should not be read as facts:
`cache_hits` and `cache_misses` come from counters the indexing path does not increment.
The real cache figure for this run is the `served_from_cache: 1` returned by the indexer
itself.

## F — Frozen 74-case holdout, real Perplexity embeddings

The frozen control was not altered. 70 positive cases, 4 negative controls, identical
matcher across all three modes.

| | LEXICAL (control) | SEMANTIC | HYBRID |
|---|---|---|---|
| Recall@1 | 0.3000 | 0.3000 | **0.3571** |
| Recall@3 | 0.3857 | 0.3857 | **0.4286** |
| Recall@5 | 0.4143 | 0.4571 | **0.5000** |
| MRR | 0.3500 | 0.3538 | **0.4029** |
| Misses | 41 | 38 | 35 |
| Negative leaks | 2 / 4 | **0 / 4** | 2 / 4 |

Recall@5 by language:

| | LEXICAL | SEMANTIC | HYBRID | Δ vs control |
|---|---|---|---|---|
| English | 0.5435 | 0.5435 | 0.6304 | +0.0869 |
| Spanish | 0.2500 | 0.3750 | 0.3750 | +0.1250 |
| French | 0.1250 | 0.2500 | 0.1250 | 0.0000 |
| Haitian Creole | 0.1250 | 0.2500 | 0.2500 | +0.1250 |

Recall@5 by category:

| | LEXICAL | SEMANTIC | HYBRID |
|---|---|---|---|
| exact | 1.0000 | 1.0000 | 1.0000 |
| terminology | 0.8750 | 0.7500 | 0.8750 |
| typo | 0.4286 | 0.7143 | 0.8571 |
| slang | 0.3333 | 0.5000 | 0.5000 |
| paraphrase | 0.2903 | 0.3226 | 0.3548 |
| indirect | 0.0000 | 0.0000 | 0.0000 |

The lexical column reproduces `data/undx/baseline_lexical_results.json` figure for figure,
which is the check that the harness itself did not drift between the freeze and this run.

Query-side latency: p50 152 ms, p95 217 ms over 148 semantic retrievals; embedding p50
127 ms, p95 174 ms. 12 semantic fallbacks across the run.

## G — Decision

All four promotion gates pass against the frozen control:

| Gate | Threshold | Measured | Result |
|---|---|---|---|
| Recall@5 materially better | ≥ +0.05 | **+0.0857** | PASS |
| MRR materially better | ≥ +0.03 | **+0.0529** | PASS |
| Multilingual or indirect materially better | ≥ +0.05 | **+0.125** (es, ht) | PASS |
| Negative controls no worse | ≤ 2 leaks | 2 | PASS |

Computed recommendation: **ENABLE_SHADOW**.

Per the directive: **KEEP SHADOW ACTIVE, mark READY_FOR_QA.**
`UNDX_SEMANTIC_RETRIEVAL_STAGE` stays at `shadow` in production. Nothing was globally
switched. The stage flag was set to `production` only inside the benchmark process, which
touched no deployment.

Three things the numbers say that the gate result does not:

**Indirect queries did not move at all.** 0.0 lexically, 0.0 semantically, 0.0 hybrid.
Whatever those eight cases need, embeddings are not it — the target documents are probably
not in the canonical corpus at all. Semantic retrieval should not be sold as a fix for
that category.

**Hybrid is worse than semantic alone on French** (0.125 vs 0.250). Fusion reintroduced
the lexical miss. On a holdout with eight French cases this is one case and inside the
noise, but it is the one slice where fusion actively costs something and it should be
watched rather than averaged away.

**Both negative-control leaks come from the lexical side.** Semantic alone leaked zero of
four; hybrid leaks the same two lexical does. That is consistent with the open lexical
false-positive defect (backlog #114) and means the leak budget is not being spent by the
new component.

## H — Teardown

There is no service-deletion tool on this Railway token — the available destructive tools
cover volumes, buckets, TCP proxies, and feature flags only. So the service has been
decommissioned in place rather than removed, and the deletion needs a hand:

- Start command replaced with an inert `echo … && exit 0`; nothing runs on a future deploy.
- Restart policy confirmed `NEVER`; the container has already exited.
- `PERPLEXITY_API_KEY`, `DATABASE_URL` and `DRIVER_GZ_B64` overwritten with the literal
  string `DECOMMISSIONED`. **The Railway references to the production key and database are
  severed** — the service no longer resolves either secret.
- No domain was ever generated.

**Manual step required.** Delete this service in the Railway dashboard:

> Project **coinpilotx-alert-worker** → service **TEMP-undx-canonical-index-DELETE-AFTER**
> (id `9d6d2618-859d-4f07-ba9f-8635ff468639`) → Settings → Delete Service.

It is idle and holds no secrets in the meantime, but it should not stay.

---

## What this does and does not license

The index is real, durable, and paid for once: 1,667 canonical documents live in
`undx_semantic_index` in production Postgres, keyed by content hash, so re-indexing an
unchanged corpus costs nothing.

It licenses keeping semantic retrieval in shadow and moving to QA. It does not license
switching semantic retrieval on globally, and this run says nothing about the briefing
timezone fix, the dedupe fix, or agent QA — those are sections A, B, C and I, tracked
separately and still blocked on the git checkout.
