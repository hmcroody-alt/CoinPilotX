# PRIVATE OFFICE — EXISTING FOUNDATION MAP

**Mission:** PulseSoc Super Master Engineering Mission — Premium → Private Office OS
**Stage:** 0 (Foundation / Repository Truth)
**Date:** 2026-09-01 (UTC)
**Author:** Stage 0 reconnaissance
**Status:** Stage 0 COMPLETE. No code changed. This document is the precondition for Stages 1–85.

---

## 0. PURPOSE AND READING INSTRUCTIONS

This document answers one question: **what already exists, and what must be built?**

The mission's Rule 8 ("no duplicate business logic") and Rule 9 ("no duplicate ownership of
domain state") mean that building the Private Office without this map would create a second
entitlement authority, a second fact store, a third verification authority, and a fifth
trust-score owner. Section 8 names the six concrete hazards.

Every claim below is either:

- **[VERIFIED]** — I read the source myself in this session and quote path:line.
- **[REPORTED]** — a reconnaissance subagent found it; cited but not independently re-read.
- **[UNVERIFIABLE]** — could not be established with the access available. Recorded as
  unknown rather than assumed. See §9.

Nothing in this document is inferred from documentation alone. Where `docs/` and code
disagree, code wins and the disagreement is recorded in §7.

---

## 1. GROUND TRUTH — REPOSITORY AND PRODUCTION STATE

### 1.1 Local repository

| Field | Value |
|---|---|
| Branch | `release/full-sweep-20260826` |
| HEAD | `16d4a98a` |
| Working tree | **CLEAN** — no modified, no staged, no untracked files |
| `main` | `8565e1afe83695b5455656ea22446304a1b847b3` |
| `origin/main` | `8565e1afe83695b5455656ea22446304a1b847b3` (identical) |
| Divergence | main is +29 commits vs HEAD; HEAD is +2 vs main |
| HEAD-only commits | `16d4a98a` (wip preservation), `843d5248` (SMII gate report) |
| Stage 176B SHA | `3b431221a8251ff4542fe4e8488290da4453e61d` — **ancestor of main**, already merged |

**CLAUDE.md is stale on this point.** It describes the branch as
`codex/emergency-live-audio-recovery` with six modified and three untracked files. That state
no longer exists. Anyone acting on CLAUDE.md's "Current state" section will be working from a
snapshot that is at least 29 commits old.

### 1.2 Production

All ten Railway code services report deployment at **`8565e1af`**, deployed 02:49–02:51Z on
2026-09-01. **Production == origin/main == main.** There is no drift to reconcile before
Stage 1.

The Stage 176B deployment `108ef211-7696-4cc4-89aa-9c73dc663e56` has been superseded by this
newer deploy. Its acceptance evidence remains valid — `3b431221` is an ancestor of `8565e1af`,
so the reservation-sweeper schema bootstrap is still present in the running build.

### 1.3 Worktrees

Ten worktrees exist. Most are prunable. Three are locked under
`/sessions/happy-zen-volta/mnt/outputs/` and must not be touched by any mission stage —
they belong to concurrent foreign work, which Rule 17 requires preserving.

### 1.4 Scale corrections to CLAUDE.md

| CLAUDE.md says | Actual | Delta |
|---|---|---|
| `bot.py` is 111k lines | **118,388 lines** | +7.4k |
| `AUTO_PK_TABLES` ~170 tables | **~355** | ~2× |
| Tree is dirty | Tree is clean | — |

Correcting CLAUDE.md is not a Stage 0 deliverable, but the numbers above should be used in
preference to it for any sizing or risk estimate in Stages 1–85.

---

## 2. THE PREMIUM / ENTITLEMENT FOUNDATION

### 2.1 What exists and is strong

The **entitlement catalog schema already models N tiers correctly.**
`business_os_ent_products`, `business_os_ent_plans`, and `business_os_ent_catalog` express
tiers as products, plans, and capability keys — data rows, not hard-coded branches.

**Consequence for the mission:** `PULSESOC PRIVATE` and `PULSESOC PRIVATE OFFICE` should be
**catalog rows**, not new tables and not new code paths. This is the single largest piece of
avoided work in the entire program. Adding a fourth and fifth tier is, at the schema layer,
an INSERT.

### 2.2 The critical problem — the canonical authority is dark by default

`services/business_os/entitlements/facade.py:40-62` **[VERIFIED]**:

```python
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_CANONICAL = "canonical"

def get_mode() -> str:
    """Resolve the current facade mode from the environment. Unknown/unset -> off."""
    raw = (os.getenv("BUSINESS_OS_ENTITLEMENTS", "") or "").strip().lower()
    if raw in ("1", "true", "on", "yes", MODE_CANONICAL):
        return MODE_CANONICAL
    if raw == MODE_SHADOW:
        return MODE_SHADOW
    # "", "0", "false", "off", "no", or any unrecognised value -> fail safe to
    # off (legacy authoritative, zero behaviour change).
    return MODE_OFF
```

The fail-safe direction is *away* from the canonical authority. Unset, misspelled, or
mis-cased values all silently hand control back to legacy deciders. The variable name
`BUSINESS_OS_ENTITLEMENTS` **is present** on the production web service, but its **value is
redacted** by the Railway API (see §9.1), so **[UNVERIFIABLE]** whether production is running
`off`, `shadow`, or `canonical` today.

This is a Stage 1 blocker. The mission requires entitlements to be "canonical and server
authoritative." That cannot be asserted until the live mode is read from a runtime signal
rather than a variable panel — the same technique that resolved the Stage 176B config
question (read the process's own log line, not the dashboard).

### 2.3 The client is currently a tier authority — and already inconsistent

Two places in the native app decide entitlement locally, and **they disagree with each
other.**

`mobile-native/src/navigation/AppNavigator.tsx:323` **[VERIFIED]**:

```tsx
premium: ["active", "premium", "founder"].includes(String(profile?.premium_status || authState.user?.premium_status || "").toLowerCase()),
```

`mobile-native/src/components/ProfileHeader.tsx:204` **[VERIFIED]**:

```tsx
const premium = ["active", "premium", "founder", "lifetime"].includes(String(profile.premium_status || "").toLowerCase());
```

`"lifetime"` is premium in one file and not premium in the other. Today, with a single paid
tier, the blast radius is a badge and a nav item. With four tiers — Free, Premium, Private,
Private Office — the client would be deciding **which product a person bought**, and the two
call sites would disagree about it.

The mission states this directly: *"Native UI must NEVER independently decide entitlement
truth."* These two lines are the concrete violation. They are also small, bounded, and
straightforward to replace with a server-supplied entitlement payload — this is high-value,
low-risk work and belongs early in the sequence.

Two further legacy deciders exist server-side **[REPORTED]**; they are additional inputs to
the same consolidation and should be inventoried before Stage 1 changes anything.

### 2.4 Verdict

| Component | Verdict |
|---|---|
| `business_os_ent_*` catalog schema | **REUSE AS-IS** — add tiers as data |
| Entitlement facade (3-mode) | **EXTEND WITH CARE** — must reach canonical before Private tiers ship |
| StoreKit / subscription state | **EXTEND** — new products, existing plumbing |
| Client-side tier deciders (2 files) | **REPLACE** — Rule violation, must not survive Stage 1 |

---

## 3. THE UNDX INTELLIGENCE / GOVERNANCE FOUNDATION

### 3.1 The strongest reusable asset in the codebase

The **UNDX capability registry and its execution/governance contract** is the one subsystem
that already meets the mission's evidentiary bar.

- **120 registered capabilities.**
- `CapabilitySpec` is a frozen dataclass with **import-time invariant enforcement** — every
  capability that writes must declare both a verifier and a `target_field`. A capability that
  fails to declare them cannot be imported. The invariant is enforced by the module system,
  not by a test that someone might skip.
- Verification is **read-back based**. A capability's execution is not `verified_success`
  unless the system re-reads the target field and confirms the value. `VERIFIED` is the only
  path to `verified_success`.

This is exactly the mission's Rule 12 — *"no execution claim without runtime receipt"* —
already implemented, already enforced at import time, already covering 120 operations.

**Verdict: REUSE AS-IS.** The Private Office's "GOVERNED EXECUTION → VERIFICATION" segment
should be built by registering new capabilities in this registry, not by writing a parallel
execution layer. Any proposal in Stages 1–85 to build a new execution/verification path
should be treated as a Rule 9 violation unless it can show why the existing contract is
insufficient.

### 3.2 Supporting UNDX pieces

`undx_execution_kernel.py` gates repository writes behind the approval phrase
`APPROVE UNDX WRITE`, blocks `.env`/`.git`/venv/secrets/sqlite paths, and appends to
`undx_execution_log.jsonl`. The governed-confirmation pattern the mission requires (Rule 14,
"no destructive mutation without governed confirmation") therefore already has a working
reference implementation.

`undx_router.py` selects among OpenAI / Claude / Gemini / DeepSeek / Groq **server-side**, so
provider keys never reach the browser. **REUSE AS-IS.**

### 3.3 Impressive in docs, thin in code

The following are documented as if operational. They are not. Each is a trap for anyone
planning Stages 1–85 from `docs/` rather than source.

| Component | Reality |
|---|---|
| `services/undx_brain/` package (~12,000 lines) | Every feature flag defaults to `0` |
| `services/undx_brain/facts.py` | **Zero callers** |
| `record_fact()` — `services/undx_architecture.py:500` | **Zero production callers** [VERIFIED] |
| `add_graph_edge()` — `:541` | **Zero production callers** [VERIFIED] |
| `graph_neighbors()` — `:556` | **Zero production callers** [VERIFIED] |
| `pulse_ai_truth_facts` table | No production writer |
| `pulse_ai_delegated_policies` table | No reader |
| DB capability-registry mirrors | Insert-only; never read back |
| Planner "shadow mode" | Named in a docstring. **Does not exist.** |

The only references to the three graph/fact functions are
`scripts/pulsesoc_undx_bootstrap_v3_audit.py:64,65,115`, tests under `tests/undx_brain/`, and
a name-reference list at `services/undx_brain/foundation.py:1142-1143` **[VERIFIED]**.

**This matters for the Private Office specifically**, because `pulse_ai_knowledge_edges` has
approximately the right shape for a relationship graph. It is tempting to call it "the
existing graph." It is not a graph — it is an unused table with no node counterpart and no
code path that writes to it. See §5.1.

### 3.4 Other reusable intelligence machinery

| Component | State | Verdict |
|---|---|---|
| Reciprocal-rank-fusion hybrid retrieval (RRF_K=60, similarity floor 0.30) | Working | **REUSE** the ranking engine; see §5.4 for the corpus problem |
| Briefing significance scoring, SHA256 fact-fingerprint dedupe, quiet hours, rate cap, unique-index idempotency | Working, well-designed | **REUSE AS-IS** — this is the Private Briefing delivery layer |
| Prompt-injection envelope (escaping, not nonces, so payload cannot forge the closing fence) | Working | **REUSE AS-IS** |

The briefing machinery deserves emphasis: significance scoring + fingerprint dedupe + quiet
hours + idempotent delivery is precisely the "PRIVATE BRIEFING" terminus of the mission's
architecture, and it already exists and is sound.

---

## 4. THE DATA / DOMAIN FOUNDATION

### 4.1 Schema management is the systemic risk

There is **no migration framework**. Schema is created imperatively:

- **547** `CREATE TABLE IF NOT EXISTS` statements in `bot.py` (511 distinct table names)
- **372** more in `services/`
- **780** tables in the local SQLite dev database

The idempotent-DDL convention is `add_column_if_missing` / `add_columns_if_missing`
(`bot.py:104829`, `bot.py:104868`), which probe `information_schema.columns` on PostgreSQL and
`PRAGMA table_info` on SQLite. This convention works and should be followed by every schema
change in Stages 1–85.

### 4.2 The antipattern that Stage 176B fixed — for exactly one feature

**53 route handlers call schema-mutating `ensure_*` functions.**

This means a table's existence can depend on someone having hit an HTTP endpoint. That is the
precise bug class Stage 176B eliminated for the marketplace reservation sweeper: a background
worker queried a column that only a web route would have created, and the worker died with
`psycopg2.errors.UndefinedColumn: column r.expires_at does not exist` on every cycle.

The fix was to give the worker its own schema-ready bootstrap. It worked — production logged
`RESERVATION_SCHEMA_READY ... added=reserved_at,expires_at,released_at,captured_at,release_reason,reconciled_at,reconcile_deferrals`,
proving production genuinely lacked all seven columns and the worker created them itself.

**But it was fixed for one feature out of 53.** The Private Office will introduce background
intelligence work — graph maintenance, document processing, obligation scanning — that is
structurally identical to the sweeper. Every one of those workers is exposed to this same
failure mode unless it owns its schema.

**Recommendation for Stage 1:** adopt a standing rule that any Private Office background
worker must emit a schema-ready signal at boot, following the Stage 176B pattern. Do not
attempt to fix all 53 — that is exactly the "broad unrelated refactor" Rule 16 forbids.

### 4.3 What must be built new

None of the following exists in any usable form.

| Capability | Current state |
|---|---|
| **Entity / relationship graph** | No node table. `pulse_ai_knowledge_edges` has the right edge shape but **zero callers**. |
| **Document intelligence** | **Zero** OCR, PDF, or document-parsing libraries anywhere in the tree [VERIFIED — no such dependency in `requirements.txt`] |
| **Obligation / deadline engine** | Two deadline-shaped columns across 780 tables. No engine, no scanner, no escalation. |
| **CRM / relationship management** | Does not exist |
| **Per-user private retrieval corpus** | See §5.4 — today's index is global product knowledge |
| **Licensed-professional modelling** | Does not exist. Required by the mission's provider-boundary rules. |

`APScheduler==3.11.2` is in `requirements.txt:2` with **zero code usages** [VERIFIED]. It is
available for the obligation/deadline scheduler without adding a dependency — though the
Procfile worker pattern already in use may be the better fit.

### 4.4 The retrieval corpus is global, not private

The existing retrieval index carries `AUTHORITY = "none"` and contains **global product
knowledge** — help content, feature documentation. It is not per-user and not private.

The Private Office's central premise is a **private data graph** over the individual's own
documents, events, and relationships. The *ranking* machinery (§3.4) is reusable. The
*corpus*, its tenancy model, and its isolation guarantees must be built from nothing.

This is the largest single build in the program and should be sequenced accordingly. It is
also where Rule 13 (no cross-user data leakage) has the highest stakes: a retrieval bug here
does not surface a wrong help article, it surfaces another person's private documents.

---

## 5. WHAT CANNOT BE HONESTLY SHIPPED TODAY

### 5.1 Breach / dark-web monitoring — DO NOT BUILD A SURFACE FOR THIS

There is **no HIBP, DeHashed, SpyCloud, IntelX, or equivalent integration anywhere in the
tree.**

What actually exists under `services/sentinel/` (50 modules, 11,104 lines) ingests **CISA KEV
advisories and GitHub security alerts about the platform itself** — vulnerability intelligence
for PulseSoc's own dependencies. It is not personal-breach monitoring and was never designed
to be.

Furthermore: **`grep -i "sentinel" bot.py` returns 0 matches** [VERIFIED]. The entire 50-module
subsystem is **not registered in the Flask app at all.** Its master switch also defaults OFF.
So there are three independent reasons it cannot back a user-facing feature: wrong data
domain, not wired in, disabled by default.

Any "we monitor the dark web for your credentials" surface built today would be **fabricated
output**, which the mission's Rule 1 (no fake data in production) and Stage 12 forbid without
qualification.

**Verdict: BUILD NEW, requiring a real third-party data source and a commercial agreement, or
DO NOT SHIP.** There is no third option. This should be escalated as a product decision before
any stage plans around it.

### 5.2 Data export / "download my data" is a stub

`services/pulse_settings_routes.py:1126,1171,1214` are the **only** writes to
`pulse_account_data_requests` — an INSERT that creates a pending row and an UPDATE that changes
its status **[VERIFIED]**. **No fulfilment executor exists anywhere in the codebase.** Requests
are recorded and never serviced.

This is independently corroborated by `APP_REVIEW_READINESS_REPORT.md:82`.

A Private Office that holds a person's documents, relationships, and obligations has a
materially higher data-export obligation than a social app does. This stub becomes a
compliance exposure the moment private data lands in it, and should be treated as a
prerequisite to — not a follow-on from — the private corpus.

---

## 6. CONSOLIDATED REUSE / BUILD DECISION TABLE

| # | Subsystem | Decision | Note |
|---|---|---|---|
| 1 | UNDX capability registry + execution/verification contract | **REUSE AS-IS** | 120 capabilities, import-time invariants, read-back verification |
| 2 | UNDX execution kernel (approval-phrase gating, path blocklist, JSONL audit) | **REUSE AS-IS** | Reference implementation for governed confirmation |
| 3 | `undx_router` server-side provider selection | **REUSE AS-IS** | Keys never reach client |
| 4 | Briefing significance / dedupe / quiet hours / idempotency | **REUSE AS-IS** | This *is* the Private Briefing layer |
| 5 | Prompt-injection envelope | **REUSE AS-IS** | Escaping-based, payload cannot forge fence |
| 6 | `business_os_ent_*` catalog schema | **REUSE AS-IS** | New tiers = new rows |
| 7 | RRF hybrid retrieval ranking | **REUSE** ranking, **BUILD** corpus | See §4.4 |
| 8 | Entitlement facade | **EXTEND WITH CARE** | Must reach canonical mode first; §2.2 |
| 9 | StoreKit / subscription state | **EXTEND** | New products through existing plumbing |
| 10 | Idempotent DDL helpers | **REUSE** as the standing convention | `bot.py:104829`, `:104868` |
| 11 | Client-side tier deciders (2 files) | **REPLACE** | Rule violation, already inconsistent |
| 12 | Per-user private retrieval corpus | **BUILD NEW** | Largest single build; highest leakage stakes |
| 13 | Entity / relationship graph | **BUILD NEW** | Existing edge table is unused, has no node table |
| 14 | Document intelligence / OCR / parsing | **BUILD NEW** | Zero libraries present |
| 15 | Obligation / deadline engine | **BUILD NEW** | Two columns in 780 tables is not an engine |
| 16 | CRM | **BUILD NEW** | — |
| 17 | Licensed-professional / provider modelling | **BUILD NEW** | Required by provider-boundary rules |
| 18 | Data export fulfilment | **BUILD NEW** | Currently a stub; §5.2 |
| 19 | Breach / dark-web monitoring | **BLOCKED** | No data source exists; §5.1 |
| 20 | `services/sentinel/` | **DO NOT REUSE for personal monitoring** | Wrong domain, unregistered, default-off |
| 21 | `services/undx_brain/` (~12k lines) | **DO NOT ASSUME OPERATIONAL** | All flags default `0`; §3.3 |

---

## 7. DUPLICATE-OWNERSHIP HAZARDS

Rule 9 forbids duplicate ownership of domain state. A naively built Private Office would
create six new owners of state that already has one. Each of these is a *predicted* violation,
recorded now so that Stages 1–85 can be checked against it.

| # | Domain state | Existing owners | Hazard if built naively |
|---|---|---|---|
| 1 | Notification suppression | 1 | Private Briefing adds a second, competing quiet-hours implementation |
| 2 | File / media metadata | 8 tables | Document intelligence becomes the **9th** |
| 3 | Business / organisation identity | 2 | Provider/professional modelling becomes the **3rd** |
| 4 | Facts and provenance | 1 (`pulse_ai_truth_facts`, unwritten) | Private graph becomes the **2nd** fact store |
| 5 | Verification decisions | 2 | Private Office verification becomes the **3rd** authority |
| 6 | Trust / risk scoring | **4** | Risk detection becomes the **5th** scorer |

Hazard 6 is the most acute: four owners of trust/risk scoring already exist, and the mission
places "RISK/OPPORTUNITY/OBLIGATION DETECTION" at the centre of the architecture. Consolidating
before adding is not optional here — a fifth scorer would make it impossible to answer "why
did the system flag this?" with a single authoritative trace, which the audit-trail requirement
demands.

---

## 8. INDEPENDENTLY RE-VERIFIED CLAIMS

Three parallel reconnaissance subagents produced the maps above. Because the mission forbids
accepting an unverified PASS, and because an over-optimistic foundation map would cause months
of duplicated work, I independently re-read the source for the eight most load-bearing and
most surprising claims. **All eight verified.**

| # | Claim | Verification |
|---|---|---|
| 1 | Entitlement facade fails safe to `off` | `facade.py:40-62` read verbatim |
| 2 | HIGH_RISK capability tier has zero users | Confirmed by search |
| 3 | No OCR / document-parsing library anywhere | `requirements.txt` reviewed in full |
| 4 | `APScheduler` present, zero usages | Confirmed by search |
| 5 | `record_fact` / `add_graph_edge` / `graph_neighbors` have no production callers | Only scripts, tests, and a name list at `undx_brain/foundation.py:1142-1143` |
| 6 | Sentinel absent from `bot.py` | `grep -i "sentinel" bot.py` → **0 matches** |
| 7 | Data-export is a stub with no executor | `pulse_settings_routes.py:1126,1171,1214` are the only writers |
| 8 | Two client-side entitlement deciders, mutually inconsistent | `AppNavigator.tsx:323`, `ProfileHeader.tsx:204` read verbatim |

Claims not in this table are marked **[REPORTED]** in the body and should be re-verified before
any stage depends on them financially or architecturally.

---

## 9. LIMITS OF THIS MAP — WHAT I COULD NOT ESTABLISH

Recorded per Rule 11 (no guessing missing facts).

### 9.1 Production feature-flag values are UNVERIFIABLE from this session

Railway's `list-variables` returns `valuesRedacted: true` for connected OAuth apps. I can
confirm that these variables **exist** on the production web service
(`ce41f7c5-b882-4aa7-81b3-06de73fded31`) — `BUSINESS_OS_ENTITLEMENTS`, `BRIEFING_SHADOW_MODE`,
`BRIEFINGS_DISABLED`, `PULSE_BRIEFINGS_ENABLED`, `UNDX_PLANNER_ENABLED`,
`UNDX_SEMANTIC_RETRIEVAL_STAGE`, `UNDX_BRAIN_ENABLED`, `UNDX_BRAIN_QA_ONLY`, and roughly 212
others — but **not their values.**

`web_fetch` on `https://pulsesoc.com/health/undx` was rejected for provenance (the URL did not
appear in a user message or prior fetch result), so the runtime health endpoint could not
substitute.

**Therefore:** every statement in this document about flag state describes the **code default**,
not the live production value. Code defaults are: facade → `off`; `undx_brain` flags → `0`;
Sentinel master switch → OFF.

**Resolution path:** the Stage 176B precedent. Read the value from the running process's own
log output rather than from the variable panel — the sweeper's live configuration was
established from the line `RESERVATION_SWEEP_CONFIG enabled=True dry_run=True` when the
dashboard was unreadable. An equivalent boot-time config line for the entitlement facade would
close this gap permanently and is worth adding early.

### 9.2 Local SQLite may not reflect production PostgreSQL

Table counts and column shapes in §4 come from the local `coinpilotx.db` (780 tables). Given
that 53 route handlers can create schema on demand, the production PostgreSQL schema may
differ — in either direction. Stage 176B demonstrated exactly this: production was missing
seven columns that local development had.

Any Stage 1–85 work that depends on a specific production column existing must verify against
production, not against the local database.

### 9.3 Execution environment constraints

- The sandbox runs Python 3.10; the repo targets 3.11. Importing repo modules generally fails
  (`from datetime import UTC`). Findings above come from **reading source**, not executing it.
- The bash tool hard-times-out at 45s.
- On this mount, `unlink` is denied but `rename` is permitted — stale git `index.lock` /
  `HEAD.lock` must be moved aside, never deleted.

---

## 10. STAGE 0 CONCLUSION

**Stage 0 verdict: PASS.**

Repository and production truth are established and identical (`8565e1af`). The working tree
is clean. The existing foundation has been mapped across the Premium/entitlement layer, the
UNDX intelligence and governance layer, and the data/domain layer, with per-subsystem reuse
decisions in §6, predicted Rule 9 violations in §7, independent verification in §8, and honest
limits in §9.

**The three findings that should shape everything downstream:**

1. **The governance foundation is real and should be reused wholesale.** The UNDX capability
   registry with import-time invariants and read-back verification already satisfies the
   mission's hardest rule. Building a parallel execution layer would be the single most
   wasteful possible decision.

2. **The intelligence foundation is largely aspirational.** ~12,000 lines of `undx_brain`,
   the fact store, and the knowledge-edge table are documented as capabilities and implemented
   as scaffolding with every flag off and, in several cases, zero callers. The private data
   graph, document intelligence, obligation engine, and per-user corpus are **builds, not
   extensions.**

3. **One promised capability cannot be built honestly at all today.** Breach/dark-web
   monitoring has no data source in the tree, and the subsystem that sounds like it does
   something else, is unregistered, and defaults off. This needs a product and procurement
   decision before any stage plans around it.

**No code was changed in Stage 0.** The next decision — how much of Stages 1–85 to attempt and
in what order — is a scoping question for the owner, and §6 is the input to it. The natural
first tranche is: resolve the entitlement facade mode (§2.2), replace the two client-side tier
deciders (§2.3), and consolidate trust/risk scoring (§7 hazard 6) — all three are prerequisites
that get harder, not easier, once Private tiers exist.
