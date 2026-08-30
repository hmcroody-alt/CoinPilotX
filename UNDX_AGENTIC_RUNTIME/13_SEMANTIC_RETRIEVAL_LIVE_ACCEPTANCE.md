# 13 — Semantic Retrieval Live Activation & Acceptance

**Mission:** activate Perplexity embeddings against the frozen benchmark and decide, on
evidence, whether semantic retrieval earns a place in UNDX.
**Date:** 2026-08-30
**Branch:** `release/full-sweep-20260826` @ `f1759800`
**Predecessor:** `12_SEMANTIC_RETRIEVAL_IMPLEMENTATION_REPORT.md`

---

## Headline

Your secret arrived. `PERPLEXITY_API_KEY` is confirmed present on the Railway service
`CoinPilotX` (web), and this report contains no trace of its value — I never requested it,
read it back, or printed it. That is Stage 1, complete.

Stages 2 through 6 — the real provider probe, the bulk index, and the real holdout —
**did not run**, and not for want of trying. The execution environment I work in reaches
the internet only through an allowlisted HTTP proxy, and `api.perplexity.ai` is not on the
allowlist. The proxy answers `HTTP 403` to `CONNECT api.perplexity.ai:443` while answering
`200` for `pypi.org` and `github.com`. The key exists in Railway; the network path exists
in Railway; the two have never been in the same place as this agent. So the honest state of
the mission is:

> **No semantic number and no hybrid number exists yet. None is reported here.**

Your ABSOLUTE RULE was that I must not tell you Perplexity improves UNDX because the API
responds — I must prove it against the frozen benchmark, and keep it off if it loses. The
same rule forbids the weaker fabrication I could have committed instead: running the
deterministic hash embedder used in the unit tests and presenting its output as a
measurement. I ran that harness, but only as a **structural dry run**, and its numbers are
labelled meaningless below and are excluded from every result field.

What this mission did produce is the thing that makes the live run a formality rather than
an expedition: a single, cost-bounded, reviewable command that performs Stages 2 through 9
end to end, halts at each gate, and needs nothing but `PERPLEXITY_API_KEY` in its
environment. It is validated as far as it can be validated without a provider. **The
remaining decision is yours, and it is about where to run it — not about whether the work
is ready.**

---

## Required return fields

| Field | Value |
|---|---|
| **PERPLEXITY SECRET PRESENT** | **YES** — `PERPLEXITY_API_KEY` present on Railway service `CoinPilotX` (web), id `ce41f7c5-b882-4aa7-81b3-06de73fded31`, confirmed via `list-variables` with `valuesRedacted: true`. Absent from `coinpilotx-undx-worker`. |
| **SECRET VALUE EXPOSED** | **NO** — never requested, never printed, never logged, never committed. Verified by leak scan (below). |
| **REAL PROVIDER AUTH** | **NOT MEASURED** — `api.perplexity.ai` unreachable from this environment (proxy 403). |
| **MODEL** | `pplx-embed-v1-0.6b` — configured, never substituted. Not yet contacted. |
| **VECTOR RETURNED** | **NOT MEASURED.** |
| **VECTOR DIMENSIONS** | Configured `256`. Provider-reported dimension **NOT MEASURED**. |
| **PROVIDER LATENCY** | **NOT MEASURED.** |
| **PROVIDER ERROR** | Transport-level only: `curl: (56) Received HTTP code 403 from proxy after CONNECT`. No request ever reached Perplexity. |
| **DOCUMENTS INDEXED** | **0 with real vectors.** Corpus enumerated and priced: **1,673 documents / 72,408 tokens**. |
| **TOKENS EMBEDDED** | 0 real. Estimated for a full index: 72,408. |
| **CACHE HITS / MISSES** | 0 / 0 — nothing embedded. |
| **PROVIDER CALLS** | **0.** |
| **FAILED CALLS** | 0 provider calls attempted, so 0 failed. |
| **INDEXING TIME** | **NOT MEASURED.** |
| **INDEX COST** | **$0.00 spent.** Estimated one-time cost of the full index: **$0.00029** at $0.004 / M tokens. |
| **LEXICAL RECALL@5** | **0.4143** — frozen control, unaltered. |
| **LEXICAL MRR** | **0.3500** — frozen control, unaltered. |
| **SEMANTIC RECALL@5 / MRR** | **NOT MEASURED.** |
| **HYBRID RECALL@5 / MRR** | **NOT MEASURED.** |
| **INDIRECT** | Lexical **0.0000** (0 of 8). Semantic/hybrid **NOT MEASURED**. |
| **HAITIAN CREOLE** | Lexical **0.125** → **NOT MEASURED**. |
| **FRENCH** | Lexical **0.125** → **NOT MEASURED**. |
| **SPANISH** | Lexical **0.250** → **NOT MEASURED**. |
| **ENGLISH** | Lexical **0.5435** → **NOT MEASURED**. |
| **NEGATIVE CONTROLS** | Lexical: **2 of 4 leak**, preserved undisturbed. Semantic/hybrid **NOT MEASURED**. |
| **AUTHORITY BOUNDARY** | **PROVEN** — 17 tests, 211 subtests, adversarial, in the most permissive stage. See Stage 7. |
| **OUTAGE FALLBACK** | **PROVEN** — timeout, 429, 5XX, malformed vector, dimension mismatch, budget exhausted, provider absent. See Stage 8. |
| **CACHE** | Content-hash cache implemented and unit-tested; **not exercised against a real provider**. |
| **BUDGET GUARD** | Implemented, default $5/month. **Note the defect in "What I would not sign off on" below.** |
| **SHADOW** | **NOT ENABLED.** No evidence exists to justify enabling it. Flag remains `off`. |
| **QA COHORT** | **NOT ENABLED.** |
| **GLOBAL PRODUCTION** | **DO NOT ENABLE.** |
| **COMMIT** | **DONE** — `1bf02e67`, 16 files, +5,918 / −2. Explicit paths only; no `git add -A`. |
| **PUSH** | **BLOCKED — no credentials.** Network to GitHub works; there is no SSH key and no token in this environment. `LOCAL SHA == REMOTE SHA` therefore **NOT VERIFIED**. See Stage 11. |
| **FINAL RECOMMENDATION** | **KEEP OFF.** Then run the live acceptance command and let the numbers decide. |

---

## Stage 1 — Secret presence, verified without exposure

I read the Railway variable **names** for each service with values redacted at the API
level, and matched on the name alone.

| Service | `PERPLEXITY_API_KEY` |
|---|---|
| `CoinPilotX` (web, `ce41f7c5-b882-4aa7-81b3-06de73fded31`) | **present** |
| `coinpilotx-undx-worker` | absent |

`PERPLEXITY_API_KEY_PRESENT=true`. Nothing further was learned about the value and nothing
further needs to be.

The asymmetry is worth flagging now rather than discovering later: **the worker does not
have the key.** Whether that matters depends on where semantic retrieval is called from.
Today it is called from `services/pulse_ai_service.send_message`, which runs in the web
process, so the web service is the correct place for it. If UNDX agent runs later perform
retrieval inside `undx_worker.py`, the worker will need the variable too, and the symptom
of forgetting will not be an error — it will be a silent, permanent fallback to lexical.

---

## Stage 2 — Real provider probe: BLOCKED, with the block proven

I did not assume the network was unavailable; I measured it.

| Target through the sandbox proxy at `localhost:3128` | Result |
|---|---|
| `pypi.org:443` | `200 Connection established` |
| `github.com:443` | `200 Connection established` |
| **`api.perplexity.ai:443`** | **`403` from proxy after `CONNECT`** |

Direct DNS resolution fails entirely; the proxy is the only route out, and it is
domain-allowlisted. This is an environment boundary, not a code defect and not a key
problem. The mission's own gate — *"do not continue to bulk indexing unless this passes"* —
therefore holds the line at Stage 2, and Stages 3 through 6 are correctly unexecuted rather
than faked.

---

## Stage 3 — Index the approved corpus: cost bounded, not executed

The corpus was enumerated from the same `canonical_documents()` the indexer uses, so the
estimate describes the actual work rather than a guess about it.

| | |
|---|---|
| Documents | **1,673** |
| Estimated tokens | **72,408** |
| Price | $0.004 / M tokens (`pplx-embed-v1-0.6b`) |
| **Estimated one-time cost** | **$0.00029** |
| Monthly budget guard | $5.00 |

The cost is roughly three hundredths of a cent, incurred once — re-runs are served from the
content-hash cache unless the corpus or the model configuration changes. Cost is not a
reason to hesitate here. That is worth stating plainly, because it means the decision about
where to run this is about **deployment risk, not money**.

Content class is declared per document, never inferred, and the corpus is entirely
source-derived platform knowledge. **No private user data is in the index and none can
enter it without an explicit `content_class` declaration.**

---

## Stage 4-6 — Real holdout, multilingual, negative controls: NOT MEASURED

The frozen control stands untouched and re-verified this session:

```
identical_to_control: true
provenance_drift: []
cases_with_changed_rank: []
```

**Frozen lexical control** (`data/undx/baseline_lexical_results.json`, 70 positive cases,
4 negative, top-k 5):

| Metric | Value |
|---|---|
| Recall@1 | 0.3000 |
| Recall@3 | 0.3857 |
| **Recall@5** | **0.4143** |
| **MRR** | **0.3500** |
| Empty-result cases | 13 |
| Misses | 41 of 70 |
| Latency p50 / p95 | 1.30 ms / 1.84 ms |

**By category:**

| Category | Recall@5 |
|---|---|
| exact | 1.0000 |
| terminology | 0.8750 |
| typo | 0.4286 |
| slang | 0.3333 |
| paraphrase | 0.2903 |
| **indirect** | **0.0000** |

**By language:**

| Language | Recall@5 |
|---|---|
| English | 0.5435 |
| Spanish | 0.2500 |
| **French** | **0.1250** |
| **Haitian Creole** | **0.1250** |

**Negative controls — 2 of 4 leak, and the defect is preserved deliberately:**

| ID | Query | Returned |
|---|---|---|
| ng-01 | *(control)* | empty ✓ |
| ng-02 | *(control)* | empty ✓ |
| **ng-03** | `recipe for beef bourguignon` | `performance_traces`, `platform_fee_rules`, `platform_payouts` |
| **ng-04** | `who won the 1998 world cup` | `arena_world_events`, `arena_world_history`, `arena_world_state` |

The `ng-03` leak has a specific and slightly embarrassing cause worth naming, because it
tells you what kind of matcher you actually have: `_terms()` strips tokens shorter than
three characters but `STOP_WORDS` does not contain `"for"`, and matching is **substring**.
So the query term `for` matches inside `plat`**`for`**`m_fee_rules`. Nothing semantic is
happening; a four-letter accident is. Per Stage 10, this is **not fixed yet** — fixing it
before the comparison would move the control under the thing it is meant to measure.

These four numbers — indirect 0.0000, Creole 0.125, French 0.125, negatives 2/4 — are the
strongest *prior* reason to expect embeddings to help. They are not evidence that
embeddings do help. That distinction is the entire mission.

---

## Stage 7 — Authority boundary: PROVEN

`tests/undx_agent/test_semantic_authority_attack.py` — **17 tests, 211 subtests, all
passing.** Every test drives an adversarial query through the *full* hybrid path, with a
real on-disk index over the real corpus, in stage `production` — the most permissive
configuration the code can be placed in. If the boundary holds there it holds everywhere,
because every other stage serves strictly less.

The attack corpus spans seven intents: administrator capability, another user's content,
privileged action, financial action, security action, disabled capability, and capability
the platform does not have. What is asserted:

- **Result shape is inert.** Exactly four keys — `body`, `category`, `id`, `title`. The
  similarity score is *dropped*, not rounded or hidden. Twenty-one forbidden keys
  (`score`, `confidence`, `authorized`, `permission`, `role`, `user_id`, `owner`,
  `verified`, `approved`, `token`, …) are absent from every result under every attack.
- **Bounds hold under attack**, including a query repeated 200× and a prompt-injection
  payload.
- **No source-path or schema disclosure** — file paths are stripped, `category` is always
  the constant `source_derived_platform_knowledge`, `id` is always `0`.
- **Stage gating survives attack**, including an attempt to self-promote into the QA cohort
  by passing a forged identifier.
- **Retrieval is not an executor.** This one is structural rather than behavioural: the
  module's AST is parsed and its real `Import`/`ImportFrom` nodes are checked against
  `undx_execution_kernel`, `undx_agent_policy`, `undx_capability`, `capability_registry`,
  `subprocess`, `importlib`; `Call` nodes are scanned for `eval`, `exec`, `compile`,
  `__import__`. A behavioural test can only prove the paths it happened to walk; this
  proves the module cannot reach the execution layer at all.
- **Unavailable capabilities are not invented** — asking for something PulseSoc does not do
  does not conjure a plausible-looking surface for it.

The claim being defended is narrow and I will not overstate it. An embedding model is a
similarity function; ask it to delete every user account and it will return the closest
administrative surfaces with a high score attached, because that is the only question it
was asked. What these tests prove is that **nothing downstream can read "returned by
retrieval" as "permitted to this caller"**, because the score never leaves the module and
the module cannot import anything that authorises. A real model changes *which* documents
come back. It cannot change whether the authority filter drops the score field.

`AUTHORITY = "none"` is not a comment. It is enforced by `_authority_filter`, and the
filter is what these 211 subtests attack.

---

## Stage 8 — Failure and outage behaviour: PROVEN

Covered by `tests/undx_agent/test_semantic_retrieval.py`: timeout, HTTP 429, 5XX, malformed
vector, dimension mismatch, budget exhausted, index empty, provider entirely absent. In
every case `retrieve_with_diagnostics` returns the lexical result and `retrieve()` does not
raise. The flag is fail-closed: an unrecognised stage value resolves to `off` rather than to
the nearest match, because a rung reached by typo is a rung nobody decided to enter.

**With the provider unavailable — which is the current, real state — UNDX is fully
operational and byte-identical to today.** That is not a consolation prize; it is the
property that makes the live run safe to attempt at all.

---

## Structural dry run — and why its numbers are not results

To prove the live runner executes rather than merely parses, I ran it end to end against the
real 1,673-document corpus and the real 70-case holdout, with the deterministic **hash**
embedder standing in for the provider.

> **Every retrieval number from this run is meaningless as evidence.** A SHA-256 hash has no
> semantics. It is recorded here only because what it demonstrates is a property of the
> harness, not of the model.

```
index:    1,673 documents, 1,673 embedded, 4 provider calls, 0 failed
lexical:  recall@5 0.4143   mrr 0.3500   negative leaks 2
semantic: recall@5 0.0000   mrr 0.0000   negative leaks 4     <- hash embedder
hybrid:   recall@5 0.3857   mrr 0.2433   negative leaks 4     <- hash embedder
decision: KEEP_OFF   (all four gates false)
```

Two things here are worth more than they look:

1. **The lexical mode reproduced the frozen control exactly** — 0.4143 / 0.3500 / 2 leaks —
   measured through the live runner's own code path rather than through the freeze script.
   The comparison harness and the control agree.
2. **The harness cannot manufacture a win.** Given vectors with no meaning, semantic scored
   zero, hybrid fell *below* lexical because RRF fused in noise, and the decision function
   returned `KEEP_OFF` on all four gates. A benchmark that reports success when handed
   garbage is worthless. This one reports failure.

---

## Stage 9 — Shadow decision: KEEP OFF

`UNDX_SEMANTIC_RETRIEVAL_STAGE=off`. Unchanged.

The promotion bar is encoded as numbers rather than prose, so the decision cannot drift with
whoever reads the report:

| Gate | Threshold |
|---|---|
| Recall@5 gain over the frozen control | ≥ **+0.05** |
| MRR gain over the frozen control | ≥ **+0.03** |
| Multilingual **or** indirect materially better | ≥ +0.05 on Creole/French/Spanish or indirect |
| Negative controls no worse | ≤ **2** leaks (the control's own count) |

All four must pass. The gain is measured against the **frozen** control, not against a
lexical re-run in the same session, so a drifting lexical implementation cannot flatter the
result. With zero measurements, zero gates pass, and the recommendation is `KEEP_OFF` with
the rationale the runner itself prints: *paying for the provider is not evidence.*

---

## Stage 10 — Lexical defect: NOT FIXED, as instructed

The `for` → `plat`**`for`**`m` substring leak is diagnosed, documented, and deliberately
left in place. It is fixed only after the real three-way benchmark is frozen.

---

## What I would not sign off on

Three findings that are not blockers today but will be if the flag ever moves:

**The budget guard cannot fire on this corpus.** `estimated_cost_usd()` rounds to six
decimal places, and the full index costs $0.00029 — but a single small batch costs less than
$0.000001 and rounds to exactly `0.0`. A guard that accumulates zeros never trips. At this
corpus size the guard is decorative. It would matter if the corpus grew by three orders of
magnitude, and the failure mode would be silent.

**`configured_monthly_budget_usd()` treats `0` as "guard disabled", not "spend nothing."**
That is a defensible convention and an easy one to misread when setting a Railway variable
in a hurry.

**The worker lacks the key.** Harmless now, silent later — see Stage 1.

---

## Stage 11 — Source control

**Commit: done.** `1bf02e67` on `release/full-sweep-20260826`, parent `f1759800`, 16 files,
+5,918 / −2. Every path staged explicitly; **`git add -A` was never used**, so nothing
outside this mission entered the commit.

**Foreign work preserved.** `origin/main` carries one commit this branch does not have —
`ffbc4db0 fix(premium): surface expired subscription history for App Review`. It was not
merged, rebased over, reverted, or otherwise touched. My branch simply does not contain it,
which is the correct state for a feature branch and leaves the other work intact.

**The lock problem, and what it actually was.** The repository lives on a FUSE mount where
`create` and `rename` succeed but **`unlink` returns "Operation not permitted"**. Git's
locking protocol depends on being able to delete a lock file. So every git command that
touches the index leaves `.git/index.lock` behind, and the *next* command reports "another
git process seems to be running" — with no such process existing. The fix is rename, not
delete: each stale lock is verified zero-byte with no live git process and moved to
`.git/stale-locks-quarantine/` rather than removed, so nothing is destroyed and the
evidence survives.

The commit succeeded and then triggered git's automatic background `gc`, which failed
mid-flight for the same reason and left **89 stale `.lock` files scattered across nearly
every ref** — `HEAD.lock`, `refs/heads/main.lock`, every remote-tracking ref. That would
have blocked all future ref updates. All 89 were verified empty and quarantined, and
`gc.auto` is now set to `0` for this checkout, because a garbage collector that cannot
unlink cannot finish and will keep doing this. Five non-empty locks on unrelated
`codex/junk_*` branches were **left alone** — a non-empty lock may contain real content and
is not mine to judge.

Working tree is clean and the commit verifies: correct tree, correct parent, all 16 files
present in the object store.

**Push: blocked on credentials.** Not on network — the proxy reaches `github.com` and the
host key exchanged successfully. The remote is `git@github.com:hmcroody-alt/CoinPilotX.git`
over SSH, and this environment has **no SSH private key, no credential helper and no
token**; `ls-remote` returns `Permission denied (publickey)`. Obtaining or entering a
credential is not something I will do on your behalf, so the push stops here.

**`LOCAL SHA == REMOTE SHA` is therefore NOT VERIFIED, and I am not going to claim
otherwise.** The commit exists locally at `1bf02e67` and is intact. To publish it:

```bash
cd ~/Desktop/CoinPilotX
git push origin release/full-sweep-20260826    # NOT main — see below
git rev-parse HEAD && git rev-parse origin/release/full-sweep-20260826
```

I have deliberately written that as the feature branch rather than `main`. The branch's
upstream is configured as `origin/main`, so a bare `git push` would land these commits on
`main` — and `coinpilotx-undx-worker` builds from `main`, which makes that a **production
deploy**. Nothing here needs to deploy to be measured.

---

## The live run, in one command

`scripts/undx_semantic_live_acceptance.py` performs probe → estimate → index → holdout →
decision, halting at each gate:

```bash
export PERPLEXITY_API_KEY=...          # already in Railway; never in git, never in a report
python3 scripts/undx_semantic_live_acceptance.py --estimate-only   # no provider call, no spend
python3 scripts/undx_semantic_live_acceptance.py --probe-only      # one embedding, ~$0.000001
python3 scripts/undx_semantic_live_acceptance.py --confirm-spend --json acceptance.json
```

Without `--confirm-spend` it stops after the estimate and embeds nothing. It refuses to
index if the estimate exceeds `--max-index-cost-usd` (default $1.00). It sets the stage flag
**in its own process only** and touches no deployment. It writes no secret to its output;
`secret_value_exposed: false` is a field in the report it emits.

`--estimate-only` was verified in this environment (1,673 / 72,408 / $0.00029). `--probe-only`
and the full run require a network path to the provider.

---

## The one decision that is yours

The work is ready and the cost is negligible. What remains is **where** to execute it, and
every route has a cost you should weigh rather than one I should pick:

**(a) Run it yourself, locally.** Clone or pull the branch, export the key, run the command
above. Zero infrastructure change, zero deploy, no new attack surface, and you keep the key
on a machine you control. Slowest in wall-clock terms; fastest in every other sense. **This
is what I would choose.**

**(b) Authorise a temporary Railway service.** I can create one from the repo via
`create-deployment`. It would have network access and the key. The problem is asymmetric: I
can create the service but **there is no delete-service tool available to me**, so I would
be provisioning paid infrastructure I cannot clean up. You would have to remove it manually.

**(c) Authorise a push to `main` and a redeploy of the existing worker.** This is a
production deploy. Every flag involved defaults to `off`, so the behavioural blast radius is
genuinely near-zero — but "near-zero blast radius" and "production deploy to run an
experiment" are different claims, and I will not conflate them on your behalf.

I have deliberately not chosen. Options (b) and (c) both change production infrastructure to
satisfy a measurement, and that is a trade only the owner should make.

---

## Bottom line

**KEEP SEMANTIC RETRIEVAL OFF.**

The secret is in place, the integration is inert and proven safe under adversarial attack,
the outage path is proven, the control is frozen and re-verified, the corpus is priced at
three hundredths of a cent, and the acceptance run is a single reviewable command.

The only thing missing is the measurement — and the measurement is missing because the
network says so, not because the work is incomplete. When it runs, it decides. If hybrid
does not clear all four gates, this stays off permanently and the honest conclusion is that
Perplexity embeddings did not improve UNDX retrieval on this corpus. I would rather report
that than report a win I could not defend.

---
---

# Live Acceptance Execution Attempt — 2026-08-30

*This section records a second, separate mission: "run the real model." The architecture was
not rebuilt. Nothing above was re-derived. The only question asked here was whether the
existing runner could be executed somewhere that has both the key and a route to the
provider.*

**Result: BLOCKED. No provider call was made. No number in this report changed.**

## Stage 0 — Frozen control, re-verified before anything ran

```
provenance_drift       : []
cases_with_changed_rank: []
identical_to_control   : True
```

Recall@5 `0.4143`, MRR `0.3500`, Indirect `0.0000`, Haitian Creole `0.125`, French `0.125`,
negative-control leaks `2/4`. Untouched, and re-hashed against `baseline_lexical_results.json`
before any execution path was explored.

## Stage 1 — Source control: still BLOCKED, and re-tested rather than assumed

I re-tested with a read-only `git ls-remote`, not a push, so a failed attempt could not
mutate anything:

```
fatal: Could not read from remote repository.
  ssh dir : known_hosts        (no private key)
  helper  : none
  tokens  : 0                  (no GH_TOKEN / GITHUB_TOKEN / GIT_ASKPASS)
  netrc   : absent
```

Authenticated Git access is **still not available**. This is a credential absence, not a
network failure — `github.com` resolves and answers through the proxy, and the ED25519 host
key exchange completes; the handshake dies at `Permission denied (publickey)`.

| | |
|---|---|
| **BRANCH** | `release/full-sweep-20260826` |
| **LOCAL SHA** | `e1e3fe5f4761105a8baa6d2c30915c07b255cbb9` |
| **REMOTE SHA** | `ffbc4db0389707c9e702accbc7dadb01b37725a3` (`origin/main`, foreign App Review work, untouched) |
| **PUSH** | **BLOCKED — no credentials** |

Per instruction, this is reported separately and **has not been used to license a single
fabricated measurement.**

## Stage 2 — Railway execution path: NO QUALIFYING MECHANISM EXISTS

The mission set seven requirements. I checked every mechanism the Railway MCP surface
actually exposes against them.

**The Railway tool inventory contains no execution primitive.** There is no exec, no
`railway run`, no `railway ssh`, no one-off-command, and no shell tool. Every available
verb either mutates service configuration or reads telemetry. That is a factual property of
the toolset, not a permissions problem.

So the only ways to make code run are indirect, and each one breaks a stated prohibition:

| Mechanism | Why it fails |
|---|---|
| `update-service` → `startCommand` | Explicitly forbidden: "DO NOT modify the production start command." |
| `update-service` → `cronSchedule` | Railway cron runs *the service's own start command* on a schedule and expects the process to **exit**. Applying it to `CoinPilotX` would convert the live web service into a terminating job. This takes pulsesoc.com down. |
| `update-service` → `preDeployCommand` | Persistent config change, requires a production redeploy, and a non-zero exit **blocks the deployment**. A benchmark would become a gate on production deploys. |
| `create-service` / `create-deployment` | Forbidden: "DO NOT create another persistent Railway service." Compounded by the fact that no delete-service tool exists, so it could not be cleaned up. |
| `railway-agent` | Delegating unbounded infrastructure action to another agent to work around a prohibition is exactly "improvise production infrastructure." Not used. |

**And there is a prior blocker that makes all of the above moot.**

The `CoinPilotX` service builds from GitHub `hmcroody-alt/CoinPilotX`, branch **`main`**.
The acceptance runner is not on `main`:

```
$ git ls-tree origin/main -- scripts/undx_semantic_live_acceptance.py \
      data/undx/baseline_lexical_results.json services/undx_semantic_retrieval.py
(no output — none of the three files exist on main)

$ git ls-tree HEAD --name-only -- <same three paths>
data/undx/baseline_lexical_results.json
scripts/undx_semantic_live_acceptance.py
services/undx_semantic_retrieval.py
```

Every Railway execution mechanism runs code **from the deployed image**. The deployed image
is built from `main`. `main` contains neither the runner, nor the frozen control, nor the
semantic retrieval module. Even a perfect, fully-sanctioned execution channel would start a
process that immediately fails on `ModuleNotFoundError`.

The dependency chain is therefore closed:

> **run the real model** requires **code present in the Railway image**
> requires **a push to `main`** requires **Git credentials** — which do not exist here.

## Stage 3 — Secret presence, re-confirmed without exposure

`PERPLEXITY_API_KEY_PRESENT = true` on service `CoinPilotX`
(`ce41f7c5-b882-4aa7-81b3-06de73fded31`, environment `production`), read from the variable
**name** list returned by `get-service-config`. No value was requested or returned. No
length, prefix, suffix, or hash appears anywhere in this document.

`PERPLEXITY_API_KEY_PRESENT = false` in this execution sandbox.

## Stage 4 — Real provider probe: NOT RUN

Re-tested the route before concluding, in case the network posture had changed:

```
direct  https://api.perplexity.ai/  -> curl exit 56, no HTTP status
proxied http://localhost:3128       -> curl exit 56, no HTTP status
```

Per the mission — *"If this fails: STOP. Do not index."* — I stopped. No index was built, no
holdout was run, and no decision gate was evaluated.

## Stages 5-12 — NOT RUN

Cost estimate stands at **1,673 documents / 72,408 tokens / $0.00029**, unchanged and
computed offline. Everything downstream of the probe is untouched: no real index, no real
holdout, no category breakdown, no authority regression against a live embedding path, no
decision. Semantic retrieval remains `off`. Shadow was not enabled.

## Stage 13 — Lexical defect: still frozen, as instructed

`"recipe for beef bourguignon"` → `platform_fee_rules` remains unfixed.

## The exact limitation, stated once

> **There is no mechanism available to me that runs the acceptance code in an environment
> holding both `PERPLEXITY_API_KEY` and a route to `api.perplexity.ai`, without violating an
> explicit prohibition — and even if there were, the code is not in the deployed image,
> because the push is blocked on credentials I do not have.**

Two things would unblock this, both of which require you:

1. **Git credentials** (a deploy key or a `GH_TOKEN` in this environment) so
   `release/full-sweep-20260826` can be pushed. Then the code can reach `main`.
2. **A sanctioned execution decision** — because even with the code deployed, running it on
   Railway means either a temporary service you would have to delete yourself, or a config
   change to the production service. Both are yours to authorise, not mine to assume.

Alternatively, the runner is a single command and needs nothing but the key:

```
PERPLEXITY_API_KEY=... python3 scripts/undx_semantic_live_acceptance.py --confirm-spend --json
```

Run from any machine with the repo checked out at `e1e3fe5f` and normal internet access, it
performs probe → estimate → index → holdout → decision, halts at each gate, and is bounded
at `--max-index-cost-usd 1.00` against a corpus priced at $0.00029.

## FINAL VERDICT

| | |
|---|---|
| **BRANCH** | `release/full-sweep-20260826` |
| **LOCAL SHA** | `e1e3fe5f4761105a8baa6d2c30915c07b255cbb9` |
| **REMOTE SHA** | `ffbc4db0389707c9e702accbc7dadb01b37725a3` (unchanged) |
| **PUSH** | **BLOCKED** — no credentials |
| **REAL PERPLEXITY RESULT** | **NONE** |
| **FINAL VERDICT** | **BLOCKED** → semantic retrieval remains **KEEP OFF** |

No real Perplexity result, therefore no promotion. That is the rule, and it held.

---

# STAGE 14 — REAL PERPLEXITY LIVE ACCEPTANCE EXECUTION (2026-08-30)

> This section **supersedes the FINAL VERDICT above**, which was written while the push was
> still blocked. The branch was subsequently pushed by the owner, a sanctioned execution
> environment was found, and the accepted runner was executed **unmodified** against the real
> provider. Everything below is measured, not inferred, except where explicitly labelled
> *deduction*.

## The sanctioned execution path

The second blocker was "no environment contains branch code + secret + outbound access to
`api.perplexity.ai` simultaneously." It was resolved with **Option 1 — a Railway ephemeral
service**, created only after removability was confirmed in advance.

| Guard | How it was satisfied |
|---|---|
| Removable cleanly | Confirmed **before creation** — Railway supports service deletion on request, plus the owner click-path Project → service → Settings → Delete Service |
| No production impact | New service `TEMP-undx-semantic-acceptance-DELETE-AFTER`, created **empty** so the `Procfile` `web:` gunicorn line could never launch the monolith |
| No production DB mutation | `DATABASE_URL=sqlite:////tmp/undx_acceptance.db`. `services/db.py` `connect()` routes non-Postgres URLs to local SQLite, so the two index tables could only be written to an ephemeral container file — **stricter than the mission's allowance** of approved tables in production Postgres |
| Secret never seen | `PERPLEXITY_API_KEY = ${{CoinPilotX.PERPLEXITY_API_KEY}}` — a Railway reference resolved server-side. The value was never requested, returned, logged or printed. Runner reports `secret_value_exposed: false` |
| Runs once and exits | `restartPolicyType: NEVER`, no domain, no volume, no healthcheck, no traffic |
| Semantic retrieval not globally enabled | Service variable `UNDX_SEMANTIC_RETRIEVAL_STAGE=off`. The runner sets `production` **in-process only** (`undx_semantic_live_acceptance.py:395`) |

Production `CoinPilotX` was **not touched**: no start-command change, no cron, no
`preDeployCommand`, no restart, no merge to `main`.

### Two operational notes for anyone repeating this

1. The mission's literal command `--json` is invalid CLI — argparse requires a path argument
   (`scripts/undx_semantic_live_acceptance.py:335`). Supplying `--json /tmp/report.json` is
   **not a benchmark modification**: `finish()` prints the full report to stdout regardless
   (lines 349-353), which is what the deploy logs captured.
2. Railway `redeploy` re-runs the **prior deployment's config snapshot** and ignores an updated
   start command. Only a genuinely new deployment (re-calling `connect-service-source`) picks
   up changed service config.

## Provenance of the code under test

Railway built commit **`ce7dfa5e10a81749a3735f3abfcb70308bc118f2`** on branch
`release/full-sweep-20260826` — byte-identical to the REMOTE SHA supplied by the owner.
`1bf02e67`, `e1e3fe5f` and `5d3ed462` are all ancestors. The code that ran is the accepted
implementation, unmodified.

## RESULT — reproduced twice, independently

| Deployment | Started | Probe latency | Outcome |
|---|---|---|---|
| `1408130e-53b4-4ae2-8372-a350650059b2` | 17:46 UTC | **284.3 ms** | halted at probe |
| `d5849d34-0d49-4311-bbb9-a23558e4e3af` | 17:51 UTC | **343.3 ms** | halted at probe — identical error |

```
api_key_present : true
endpoint        : https://api.perplexity.ai/v1/embeddings
model           : pplx-embed-v1-0.6b
requested dims  : 256
status          : FAIL
auth            : FAIL          <- roll-up field, NOT a credential rejection (see below)
vector_returned : false
provider_error  : "provider returned an empty vector"
retryable       : false
halted_at       : "probe"
telemetry       : requests 1 | provider_errors 1 | 429 0 | timeouts 0 | budget_blocks 0
                  texts_embedded 0 | tokens_embedded 0 | estimated_cost_usd 0.0
```

### Connectivity SUCCEEDED. The embedding call failed. These are different findings.

A 284 ms and a 343 ms round trip to `api.perplexity.ai` means DNS, TCP, TLS and HTTP all
completed. The mission's `PROVIDER CONNECTIVITY` line is therefore **PASS**; what failed is
the embeddings request itself.

**Deduction from `services/undx_embedding_service.py` — the HTTP status was 200.** The error
raised is `"provider returned an empty vector"`, which is reachable from exactly one line
(`_parse`, line 599). Every other outcome raises a *different* message:

| Condition | Line | Message that would have appeared |
|---|---|---|
| 429 | 559 | `provider rate limited (429)` |
| 5xx | 561 | `provider server error (5xx)` |
| **401 / 403** | **565** | **`provider rejected the credential`** |
| any other non-200 | 567 | `provider returned <status>` |
| non-JSON body | 571 | `provider returned a non-JSON body` |
| `data` missing or wrong length | 588 | `provider returned N vectors for 1 inputs` |
| wrong vector length | 605 | `provider returned N dimensions, expected 256` |

None of those appeared. So the response was **HTTP 200**, JSON, an object, with `data` as a
list of exactly one dict — and that dict's `embedding` key was absent, empty, or not a list.

**The credential was therefore not rejected.** `auth: "FAIL"` in the report is a derived
roll-up of overall probe status; reporting it as an authentication failure would be wrong.

Leading hypothesis (**unverified**): the payload sends no `encoding_format`
(`embed_texts`, lines 659-663), so the provider's default applies. If Perplexity returns
base64 by default, `embedding` is a `str`, `isinstance(raw, list)` is `False`, and line 599
fires exactly as observed. A wrong model name or a different response key would produce the
same symptom. **This has not been confirmed on the wire and must not be reported as fact.**

## REQUIRED OUTPUT

| Measurement | Value |
|---|---|
| PROVIDER CONNECTIVITY | **PASS** — 284.3 ms / 343.3 ms round trips completed |
| EMBEDDING CALL | **FAIL** — HTTP 200, unparseable vector payload |
| MODEL | `pplx-embed-v1-0.6b` (requested; provider never returned a usable vector) |
| VECTOR DIMENSIONS | **NOT MEASURED** — 256 requested, none returned |
| PROBE LATENCY | **284.3 ms** (run 1), **343.3 ms** (run 2) |
| DOCUMENTS INDEXED | **0** |
| INDEX COST | **$0.00** |
| LEXICAL RECALL@5 | `0.4143` (frozen control, untouched) |
| SEMANTIC RECALL@5 | **NOT MEASURED** |
| HYBRID RECALL@5 | **NOT MEASURED** |
| LEXICAL MRR | `0.3500` (frozen control, untouched) |
| SEMANTIC MRR | **NOT MEASURED** |
| HYBRID MRR | **NOT MEASURED** |
| INDIRECT | `0.0000` → **NOT MEASURED** → **NOT MEASURED** |
| HAITIAN CREOLE | `0.125` → **NOT MEASURED** → **NOT MEASURED** |
| FRENCH | `0.125` → **NOT MEASURED** → **NOT MEASURED** |
| NEGATIVE CONTROLS | `2/4` → **NOT MEASURED** → **NOT MEASURED** |
| AUTHORITY SUITE | **NOT RUN** — runner halted before the authority phase |
| FAILURE FALLBACK | **PASS** — observed in production conditions: the provider failed, the runner halted fail-closed, spent $0.00, indexed nothing, and left the lexical path untouched |
| RUNNER DECISION | **HALTED AT PROBE** |
| SHADOW | **NOT ENABLED** |
| GLOBAL PRODUCTION | **NOT ENABLED** |

## VERDICT

**No usable vector was returned by Perplexity, therefore no promotion.** Semantic retrieval
remains **KEEP OFF**. The frozen lexical control is unchanged and remains the production path.

The mission's stop condition — *"no sanctioned environment combining branch code + secret +
outbound provider access"* — **no longer applies**; that environment was built, used, and
worked. The remaining blocker is narrower and different in kind:

> **REMAINING BLOCKER:** the embeddings request returns HTTP 200 with a payload the client
> cannot parse into a vector. This is a client/provider contract mismatch — model name,
> endpoint, or response encoding — **not** a credentials problem and **not** an access problem.
> Resolving it requires one bounded diagnostic capturing the raw 200 response shape
> (credential redacted). Until then, no semantic number exists and none may be invented.

