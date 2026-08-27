# DELIVERABLE 1 — COMPLETE RECON REPORT

**PULSESOC — UNDX FULL RECON OPERATIONS / KNOWLEDGE EXTRACTION PHASE**
Read-only reconnaissance. **No production code was changed.** No training data was written.

Workspace: `/Users/hmcherie/Desktop/CoinPilotX`
Branch observed: `codex/premium-crypto-intelligence`
Date of pass: 25–26 August 2026

---

## 0. THE TEN DELIVERABLES

| # | Deliverable | File | Lines |
|---|---|---|---|
| 1 | Complete Recon Report | `00_COMPLETE_RECON_REPORT.md` | *this file* |
| 2 | System Knowledge Map | `02_SYSTEM_KNOWLEDGE_MAP.md` | 987 |
| 3 | API Knowledge Map | `03_API_MAP.md` | 2,728 |
| 4 | Database Knowledge Map | `04_DATABASE_KNOWLEDGE_MAP.md` | 2,112 |
| 5 | User Journey Map | `05_USER_JOURNEY_MAP.md` | 500 |
| 6 | Security Knowledge Map | `06_SECURITY_KNOWLEDGE_MAP.md` | 674 |
| 7 | Feature Status Map | `09_FEATURE_STATUS_MAP.md` | 355 |
| 8 | UNDX Capability Map | `08_UNDX_CAPABILITY_MAP.md` | 603 |
| 9 | Questions / Answers Collection | `10_QUESTIONS_AND_ANSWERS.md` | — |
| 10 | Unknown Areas Requiring More Investigation | `11_UNKNOWN_AREAS.md` | — |

Supporting: `01_IDENTITY_AND_PRODUCT_MAP.md` (Stages 1–2, 32 product areas) and
`07_PAYMENTS_AND_COMMERCE_MAP.md` (Stage 7). Thin first-pass drafts that were superseded by
the full versions are kept in `_superseded/` rather than deleted.

Total: roughly 10,000 lines of source-backed reconnaissance.

---

## 1. WHAT PULSESOC IS

Legal entity **CoinPlotXAI Inc.**, primary product **PulseSoc**, founder **Roody Cherie,
Founder & CEO** — these three facts are the only company facts the codebase treats as
canonical, and they live in exactly one place, `services/undx_company_identity.py`.

The product self-describes across seven categories: social platform, creator economy, business
platform, marketplace, advertising platform, communications ecosystem, artificial intelligence
platform.

The repository folder is still named `CoinPilotX` after the original crypto-bot product.
Crypto now survives as a subsystem — 107 routes, portfolio and alerting, premium-gated — inside
a much larger social platform. Anyone reading the repo name and inferring the product will be
wrong.

**Sixteen categories of company fact are explicitly unverifiable** and must never be answered:
revenue, valuation, user count, growth, retention, funding rounds, investors, partnerships,
customer names, employees, founder biography/education/prior employment, campaign performance,
market share, licensing or catalog agreements, **production-readiness of any specific feature**,
and **Android availability**.

That list is itself the most interesting artifact in the codebase. A company that puts
"production-readiness of any specific feature" on its own do-not-assert list has thought
carefully about the difference between shipped and working.

---

## 2. ARCHITECTURE AT A GLANCE

**A Flask monolith of 117,902 lines with 1,713 route registrations** (AST-counted; a raw
`grep -c '.route('` returns 1,715 because `bot.py:28650` and `:28705` are
`ai_router_service.route(...)` calls, not decorators), plus 285 service modules that hold the
actual business logic, plus a React Native app of 111 screens.

| System | Location | Status |
|---|---|---|
| Flask monolith | `bot.py` (app object `bot:app`, alias of `webhook_app`) | Active |
| Domain services | `services/` — 285 modules | Active |
| Business OS | `services/business_os/` | Active, mixed readiness |
| Communications V2 | `pulse_communications_v2/` | Active foundation |
| Native app | `mobile-native/` (Expo 54, RN 0.81.5, React 19, TS 5.9) | Active |
| Legacy app | `mobile/` (Expo 51) | Present, not the target |
| UNDX | `undx_*.py` root modules + ~25 `services/undx_*.py` + `services/undx_brain/` | Active, guarded |
| Workers | `undx_worker`, `email_worker`, `ads_worker`, `alert_worker` in Procfile | Active |
| Sentinel | `services/sentinel/` — 46 docs, 55 modules, 22 tables | **Never registered** |

Deploy: Railway, nixpacks (Python 3.11 + ffmpeg), gunicorn 2 workers × 4 threads.
Periodic work runs in worker `while` loops — 19 jobs — not APScheduler.

**Two architectural facts shape everything downstream.**

First, **optional route packs register inside `except Exception` blocks**. One broken feature
cannot block boot. The price is that a subsystem can silently disappear in production and the
only evidence is a boot log. This is why `03_API_MAP.md`'s 2,007 route registrations are a
ceiling rather than a fact about what is serving traffic.

Second, **there is no migration framework**. Schema is created imperatively via
`CREATE TABLE IF NOT EXISTS` scattered across `bot.py` and `services/`, with `AUTO_PK_TABLES`
(`services/db.py:143`, 354 entries) papering over SQLite/PostgreSQL primary-key differences.
Every schema change is hand-rolled and must be idempotent.

---

## 3. THE DATABASE, MEASURED

Read directly from the live `coinpilotx.db`:

```
775 tables      384 non-empty     1,072 indexes    449 unique
0 foreign keys  0 views           0 triggers
```

Code declares **835** table names. **65 exist only in code** (declared, never created).
**5 exist only in the database**: `comm_v2_pinned_messages` (0 rows),
`business_os_confirmation_grants`, and three `seller_application_*` tables.

> **⚠ CORRECTED IN VERIFICATION.** An earlier draft of this report claimed 776 tables, 813
> code-declared names, 57 code-only, 23 db-only, and — most seriously — that **twenty
> `comm_v2_*` messaging tables holding 1,411 real messages had no `CREATE TABLE` statement
> anywhere in the repository.** That was wrong. All nineteen `comm_v2_*` tables except
> `comm_v2_pinned_messages` are declared in `pulse_communications_v2/models.py`
> (`COMM_V2_TABLES`, executed by `ensure_schema()` at `:410`, called from `service.py:214`).
> The database extraction pass never scanned `pulse_communications_v2/`. The corrected
> db-only count of 5 follows directly. The 776 figure counted `sqlite_sequence`, an SQLite
> internal.

**Zero foreign keys across 775 tables** means every referential guarantee in PulseSoc is
enforced in Python. Roughly **263 of 459** ownership-bearing tables have no index led by their
ownership column — so the ownership model is both unenforced by the database and unindexed for
the queries that check it. (The exact ratio depends on which columns are counted as
ownership-bearing; an earlier pass reported 237 of 467 using an unpublished column list. The
direction is robust; the precise numerator is not.)

---

## 4. WHAT UNDX ACTUALLY IS

UNDX = **"Unknown Destination X"** (`docs/undx_manual.md:7`). It is **two systems that share a
name**, and conflating them would be the most damaging error a training corpus could make:

**(a) The governed user-facing agent runtime.** A capability allowlist, a fixed nine-step tool
gateway, deterministic server-side policy, and audit receipts.

**(b) A premium engineering console** at `/pulse/premium/undx`, with an Execution Kernel that
proposes diffs against the repo and a localhost Desktop Connector that can `git push`.

### The agent's real shape

**87 registered capabilities** (`services/undx_capability_registry.py`, counted by import):

```
70 read_only  |  13 reversible_write  |  4 consequential_write
75 never-confirm  |  7 contextual  |  5 always-confirm
85 self_account_only  |  2 other_user_target  (social.follow, social.unfollow)
```

**UNDX cannot send a message, publish a post or a reel, spend money, move money, change a
security setting, place a call, go live, or take a moderation action.** Messaging capabilities
are read-and-draft-only: `messages.draft` and `messages.suggest` prepare responses **unsent**,
and both are classified `read_only`. UNDX composes; the person sends.

Twelve capabilities are `intentionally_disabled`, and they cluster with real coherence around
four things: **authentication** (`auth.login`, `auth.password.reset`, `auth.session.revoke_all`,
`security.two_factor.set`), **money** (`premium.checkout.start`, `marketplace.purchase`,
`business.merchant.apply`), **identity-bearing broadcast** (`live.sessions.start`,
`calls.audio.place`, `calls.video.place`), and **authority over other people**
(`moderation.action.apply`, `moderation.queue.list`).

The recorded reasons are worth reading in full in `10_QUESTIONS_AND_ANSWERS.md` §4 — they are
specific rather than boilerplate. "Disabling 2FA on an injected instruction is a takeover
primitive." "Would terminate the caller's own session mid-conversation, leaving the receipt
unreadable." "Media the agent did not see must not be published."

### Why injection cannot escalate

`services/undx_agent_policy.py` is deterministic and **consults no LLM by design**. Its
documented property is the load-bearing one:

> **"There is no code path from message content to `Decision.allow`."**

Evaluation order: HIGH_RISK unreachable → cohort and master switch → per-capability withdrawal
→ read/write gates → **ambiguity refused** (`resolved_resource_count != 1`) → confirmation
policy. The gateway's nine-step order is itself treated as a security property, and the
confirmation token is **burned before execution**, not after.

### Fail-closed grounding

Identity, company, and fact blocks are asserted into every provider request with sentinel
phrases (`"CoinPlotXAI Inc."`, `"UNDX fact discipline"`). If a block is missing, the request
raises `PulseAIProviderError` rather than proceeding ungrounded. Five fact classes —
CURRENT_VERIFIED, CURRENT_UNVERIFIED, ROADMAP_APPROVED, HISTORICAL, UNKNOWN — carry fixed
presentation rules, and the UNKNOWN fallback declines the metric while immediately offering
what it can do instead.

Provider routing is server-side across five providers (OpenAI, Claude, Gemini, DeepSeek,
Groq), so keys never reach the browser.

### The finding that matters most — and it is bigger than first thought

**`/api/undx/chat` executes no tools at all.** Traced during verification:
`bot.py:28795` → `undx_openai_response` (`bot.py:28772`) → `undx_router.route_undx_request()`
with `UNDX_SYSTEM_PROMPT`. Text in, text out. It touches **neither** registry, and the route is
gated by `require_super_user_api()`.

That is true of `/api/undx/chat` specifically. **It is not true of UNDX as a whole**, and an
earlier version of this section said so — wrongly.

> **⚠ MAJOR CORRECTION — this section previously concluded that
> `PRODUCTION_TOOL_REGISTRY` is "dead code reachable from no route," and that UNDX has
> "no tool execution path." A second adversarial verification pass proved that false.**
>
> The governed agent runtime **is wired to production routes** — just not through `bot.py`,
> which is why a `bot.py`-scoped search missed it. The live path is in the comm_v2 blueprint:
>
> ```
> pulse_communications_v2/routes.py:629   POST /api/pulse-ai/message
>   → routes.py:638   pulse_ai_service.send_message(...)
>   → services/pulse_ai_service.py:726   undx_agent_runtime.handle(..., confirmation_token=...)
>       (inside _agent_turn, :690-756)
>   → services/undx_tool_gateway.execute
>
> pulse_communications_v2/routes.py:811   POST /api/pulse-ai/actions/confirm
>   → pulse_ai_service.py:1413-1470   _agent_confirm
>   → pulse_ai_service.py:1454-1459   undx_tool_gateway.execute(...)   ← direct mutating execution
> ```
>
> The blueprint is registered at `bot.py:1247` via
> `_load_route_pack("pulse_communications_v2", "pulse_communications_v2.routes")`. There are
> **13 `/api/pulse-ai/*` routes**, including `missions/<id>/cancel`, `tools/simulate`,
> `actions/confirm` and `actions/cancel`. A second driver exists in `undx_worker.py:19,88`,
> which imports `undx_mission_runtime` and calls `poll_once()` on a loop.
>
> `bot.py`'s only reference to the registry is `/health/undx` at `bot.py:115256` —
> introspection only. **That is what produced the false negative:** the search stopped at
> `bot.py` and never reached the blueprint. `len(PRODUCTION_TOOL_REGISTRY) == 103` is correct;
> the literal at `undx_policy.py:41` holds 50 keys and packs merge the rest at import.

So the honest description is **two distinct UNDX surfaces**, and they must not be conflated:

| Surface | Route | Behaviour | Gate |
|---|---|---|---|
| Chat | `/api/undx/chat` (`bot.py:28795`) | Text in, text out. **No tool execution.** Touches neither registry | `require_super_user_api()` |
| Agent | 13 × `/api/pulse-ai/*` (`pulse_communications_v2/routes.py`) | **Executes tools** via `undx_tool_gateway.execute`, incl. mutating writes on confirm | Runtime policy + confirmation tokens |

The 87-capability governed runtime is real, tested, carefully governed **and live**. Any
conclusion elsewhere in this recon that rests on "no route executes tools" is void and must be
re-derived against the `/api/pulse-ai/*` surface before a training corpus is built.

**Separately, the agent runtime's flags default off.** `grep -c 'UNDX_AGENT' .env.example`
returns 0, but — correcting an earlier draft — the flags are *not* confined to the test
harness: they are a declared env contract in `services/undx_brain/config.py:644-654`
(`UNDX_AGENT_ENABLED`, `_READS_ENABLED`, `_WRITES_ENABLED`, `_DISABLE_WRITES`, `_QA_USER_IDS`)
with `required: True`, defaults, consumers, and rollout stages, and they are read at
`bot.py:115356+`. `user_enabled()` still requires explicit cohort membership — "Empty means
nobody, never everybody" — so the conclusion holds even though the original evidence for it
was wrong.

### The finding that is most dangerous — with its severity corrected downward

`undx_desktop_connector.py` (1,171 lines) exposes repository write and `git push` with **no
authentication inside the connector** — the only gate there is hardcoded, source-visible
approval phrases — and sets `Access-Control-Allow-Private-Network: true` at line 177.

> **⚠ CORRECTED IN VERIFICATION.** An earlier draft described this as "proxied from the public
> Flask app," which implies unauthenticated reachability that **does not exist**. The connector
> binds `127.0.0.1` only (`:1167`), and the `bot.py:28893` proxy requires `undx_kernel_user()`
> → `require_super_user_api()` plus a path allowlist. Still the sharpest edge in the
> repository, and still worth a defence-in-depth look, but not an open door.

Recorded, not acted on; the mission forbade changes.

---

## 5. THE THREE-WAY STATUS VOCABULARY

The codebase maintains three separate status vocabularies and the corpus must not merge them.

**Capability lifecycle** (what the agent advertises): AVAILABLE / LIMITED / TRAINING /
PLANNED / DISABLED.

**Implementation status** (`services/undx_knowledge_map.py`, 155 records):

```
verified 84 | service_missing 23 | implemented_unverified 14
partially_implemented 14 | intentionally_disabled 12 | unsupported 8
```

The module qualifies its own strongest word: **"'Verified' is a claim about executed code, not
about reading the source."**

**Fact class** (what may be asserted about the company): CURRENT_VERIFIED /
CURRENT_UNVERIFIED / ROADMAP_APPROVED / HISTORICAL / UNKNOWN.

`service_missing` deserves particular attention: 23 capabilities the *product* has but the
*agent* cannot reach, almost all for the same structural reason — the logic lives inside a
Flask request handler that reads `flask.request` directly, so there is no callable operation
taking a `user_id`. This is the clearest technical-debt signal in the repo, and it is
self-documented.

---

## 6. GATING — THREE INDEPENDENT SYSTEMS

1. **Launch readiness deny-list** — `mobile-native/src/launch/readiness.ts`, 150 lines, four
   rows. (Note: it contains **zero** `EXPO_PUBLIC_*` references; an earlier draft claimed
   otherwise and has been corrected.)
2. **Build-time env flags** — **20** `EXPO_PUBLIC_*` flags, all default off, read through
   `envFlagOn` in `mobile-native/src/core/envFlag.ts`; count pinned at
   `envFlag.test.ts:210`. **Five** payments flags off (not six — see §7), two orders flags off,
   six messages flags off.
3. **Hard-coded `false` constants that are not flags and cannot be switched on at runtime** —
   `HUB_LIVE_CARDS` (`businessOs.ts:117`), `MARKETPLACE_OFFERS_ENABLED`,
   `MARKETPLACE_CART_ENABLED`, `MARKETPLACE_BOOST_ENABLED` (`marketplaceOffers.ts:80-103`).

The third category is the one that misleads. A flag implies a decision pending; a hard-coded
`false` is a decision made. No cart, no offers, no boosting — and
`MARKETPLACE_REBUILD_REPORT.md` says the offer state machine is "complete and tested but has
nothing to talk to."

---

## 7. THE PAYOUT CORRECTION

`docs/business_os/FLAG_REGISTRY.md` states: *"No endpoint initiates a payout anywhere in the
codebase."* **This is historical and no longer true**, and the repo says so itself at
`mobile-native/src/api/paymentsHub.ts:186-200`.

Traced and re-verified end-to-end:

```
mobile-native/src/api/sellerPayouts.ts  requestSellerPayout()
  → POST /api/pulse/payments/seller/payouts        bot.py:19956
  → services.business_os.payments.seller_payouts   request_payout / get_payout
                                                    list_payouts / seller_balance_summary
  → pulse_submit_seller_payout  → provider → Stripe webhook → settled
```

Money can go out.

> **⚠ CORRECTED IN VERIFICATION — and the correction is itself instructive.** An earlier draft
> of this section said the code exists but "the client affordance
> `EXPO_PUBLIC_PAYMENTS_PAYOUT_INITIATION` is switched off." **That flag no longer exists.**
> `mobile-native/src/api/paymentsHub.ts:198` is `payoutInitiationIsLive() { return true; }`,
> and `envFlag.test.ts:207-209` explicitly records the flag as retired. **Nothing is switched
> off. Payouts are live.**
>
> This means §6's "six payments flags off" is also wrong — it is five env flags plus one
> hard-coded **`true`**. And §6's "hard-coded `false` constants" category missed the one
> hard-coded `true` in the codebase, which is precisely the one that contradicted this section.

The pattern is worth naming, because it happened three times in this recon and twice inside a
document written to warn about it: **a doc asserted an absence, the absence was disproven, and
the correction then under-corrected by assuming a flag must still be gating it.** Absence
claims decay fastest and should be re-derived from code every time, never inherited.

Applied inline in `01_IDENTITY_AND_PRODUCT_MAP.md`; full trace in
`07_PAYMENTS_AND_COMMERCE_MAP.md`.

---

## 8. `CLAUDE.md` IS STALE IN AT LEAST ELEVEN PLACES

Every item verified directly:

| `CLAUDE.md` says | Actual |
|---|---|
| branch `codex/emergency-live-audio-recovery` | `codex/premium-crypto-intelligence` |
| `bot.py` ~111k lines, ~1,538 routes | **117,902 lines, 1,715 routes** |
| 239 service modules | **285** |
| `AUTO_PK_TABLES` in `bot.py`, ~170 entries | `services/db.py:143`, **354 entries** |
| `.env.example` ~180 keys | **533** |
| `alert_worker` not in Procfile | **it is** |
| LiveKit for calls/live | **retired on native; Agora is the sole RTC provider.** Zero `LIVEKIT_*` keys in `.env.example`, though residual LiveKit `publish_state` strings persist in the web live path |
| APScheduler | **no first-party import** — worker `while` loops, 19 jobs. It *is* still pinned in `requirements.txt:2` |
| the Flask double-bind is at `bot.py:384` and `:1130` and discards config | **wrong on both counts** — it is at `:429` and `:1181`, and nothing is lost |

`CLAUDE.md` is the first file the next person or agent will read, and it is currently the least
reliable document in the repository. Fixing it was out of scope here — the mission prohibited
changes — but it is the highest-leverage cleanup available.

---

## 9. METHOD, AND ITS LIMITS

Seven subagents across three batches, each with a self-contained brief and its own markdown
deliverable, so raw findings never had to pass through a single context. Later agents were
briefed with earlier agents' verified findings — and in one case were explicitly asked to
**verify or refute** a prior claim, which is what caught the payout error. That check is the
reason this report can distinguish what was confirmed from what was merely repeated.

Three rules were enforced throughout:

- Cite `file:line`. Distinguish "the doc says" from "the code does". Mark unverifiable steps
  `UNVERIFIED`.
- Prefer **live introspection over grep**. The registry counts, status distributions, and
  database totals in this report come from importing the modules and querying the database,
  not from regex. This is how the capability count was corrected from 83 to 87.
- Row counts from the live database are evidence of whether a feature ever ran.

**What this method cannot do:** it cannot tell you which route packs mounted, whether provider
keys are valid, what the live Railway variables are, or how anything behaves on a physical
device. Those are in `11_UNKNOWN_AREAS.md` Tier 3, and they are categorical limits rather than
gaps that more reading would close.

---

## 10. WHAT MUST HAPPEN BEFORE ANY CORPUS IS WRITTEN

Two rounds of adversarial verification were run. The first found 12 errors; the second found
that one of my *corrections* was itself wrong, in the most consequential place. What remains,
in `11_UNKNOWN_AREAS.md` Tier 1:

1. **Which registry governs a given `/api/pulse-ai/*` call — the 87-capability registry or
   `PRODUCTION_TOOL_REGISTRY` (103 entries).** Both are reachable in production and they
   disagree: the former withholds send-message / create-post / create-reel, the latter exposes
   them. **This is now the top blocking question**, and it is more serious than the question it
   replaced. Until it is answered, no corpus may assert what UNDX is or is not permitted to do.
   See §4.

2. **Whether the agent runtime's enforcement is correctly configured in production.** The
   `UNDX_AGENT_*` flags are a declared contract at `services/undx_brain/config.py:644-654`
   with `required: True`, read at `bot.py:115356+`, and absent from `.env.example`. Now that
   the gateway is known to be **live** on 13 `/api/pulse-ai/*` routes, this stops being a
   question about whether a feature is dark and becomes a question about whether a *live*
   mutating surface has its safety configuration set. Reading the live Railway variables is
   the cheapest high-value check remaining, and its priority has risen accordingly.

3. **`undx_training_v6_source_corpus.yaml`** (1.43 MB, unread) — it may already assert things
   that contradict the registry. Expect reconciliation, not writing, to be the bulk of the
   corpus work.

**The methodological lesson, which matters more than any single fact above.** The false
"no route executes tools" conclusion came from searching `bot.py` and stopping. `bot.py` holds
1,713 of ~2,006 routes — enough that the shortcut feels safe and is not. The finding that
mattered most in this entire recon was in a blueprint. Any claim of the form *"nothing does X"*
must be tested against the full route inventory in `03_API_MAP.md`. More generally: **absence
claims decayed faster than any other class of claim in this work.** Payouts decayed twice in
the same direction (asserted absent → disproven → re-asserted as flag-gated → actually live);
the comm_v2 DDL "absence" was an artefact of never scanning the package; the tool-gateway
"absence" was an artefact of scope. Every absence claim in these documents should be
re-derived from code before it is relied on, never inherited.

---

## 11. CLOSING NOTE ON SCOPE

Per the mission's FINAL RULE — *"COLLECT FIRST. DO NOT WRITE TRAINING DATA YET. UNDX SHOULD
LEARN FROM VERIFIED PULSESOC REALITY, NOT FROM ASSUMPTIONS."*

No production code was changed. No training file, YAML corpus, or answer string was written.
`10_QUESTIONS_AND_ANSWERS.md` records **answer sources**, never answers. Every capability id
cited is a live key in `REGISTRY` or a live record in `RECORDS`. Permission values are the
literal `permission` field on `REGISTRY` entries and the literal `authorization_scope` field on
`RECORDS` — an earlier draft conflated the two field names.

Where two sources disagreed, both are recorded along with the evidence that settled it. Where
nothing settled it, it is in `11_UNKNOWN_AREAS.md` rather than resolved by inference.

**A note on the verification pass.** An independent agent was asked to find errors in this
material rather than confirm it, and found twelve — including the false `comm_v2` DDL claim,
an inverted payout conclusion, and the resolution of what this report had called its top
blocking question. Everything it disproved has been corrected in place with the correction
marked, rather than quietly overwritten, so that anyone building on this can see which claims
have already failed once. **Treat the remaining unverified claims accordingly: a document that
was wrong twelve times is not wrong zero times now.**

---

*End of Deliverable 1.*
