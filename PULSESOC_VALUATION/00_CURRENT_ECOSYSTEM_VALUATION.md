# PULSESOC — CURRENT VALUATION REPORT

**Date:** 2026-08-27 · **Branch:** `release/full-sweep-20260826` · **Mode:** read-only recon, no code changed
**Supporting analysis:** `01_SYSTEM_INVENTORY.md` · `02_REPLACEMENT_COST_MODEL.md` · `03_UNDX_VALUE_ANALYSIS.md` · `04_RISK_DISCOUNTS.md` · `05_STRATEGIC_BUYER_ANALYSIS.md`

---

## BOTTOM LINE

PulseSoc is a **large, unusually broad, genuinely functional pre-launch platform with zero users and zero revenue.** The engineering is real and in places excellent. The business does not yet exist.

Valuation is therefore governed almost entirely by one fact — **no traction** — and only secondarily by the substantial asset underneath it. The gap between what was built and what has been validated is the entire valuation story.

**Most defensible number today: ~$200,000.**

---

## CURRENT STATE

**VERIFIED FACT.** 845,000 lines of non-doc source across a 118k-line Flask monolith (~1,538 routes, 550 tables), 577 service modules, a 263k-line React Native app (~193 screens), 179k lines of tests, and 121k lines of markdown. Built in **3.7 months** (2026-05-06 → 2026-08-26) across 1,816 commits by **one human developer** working with AI agents.

The app is version 1.0.1, iOS build 17, bundle `com.pulsesoc.app`, with a live App Store listing (App ID `6777591572`) under a personal developer account. The current build has **not** been submitted.

---

## MATURITY SCORECARD

| Dimension | Rating | Basis |
|---|---|---|
| **Product completeness** | **7 / 10** | Feed, reels, chat, calls, live, profile, settings, search, notifications, groups, ads manager all production-verified. Premium, cart, presence, Business OS sections, spatial gated off. Group calls and live gifting absent. |
| **Technical maturity** | **6 / 10** | Real integrations, clean code (0 bare `except:`, 2 TODOs in `bot.py`), 179k LOC of tests. Offset by a 118k-line monolith, duplicate Flask app construction (`bot.py:429`/`:1181`), no migration framework, try/except route registration that silently drops subsystems. |
| **AI maturity** | **4 / 10** | 98-capability registry and above-market governance are real. Knowledge corpus is **inert** (never loaded at runtime), retrieval is keyword scoring not embeddings, and `undx_execution_log.jsonl` **does not exist** — no production execution has ever occurred. |
| **Security maturity** | **6 / 10** | Clean secrets hygiene, bcrypt + strong password policy, CSRF, rate limiting, 53-module Sentinel suite. No visible RBAC, no observability, age gating and GDPR/CCPA handling UNKNOWN. |
| **Monetisation maturity** | **3 / 10** | Architecture built and sophisticated; **not one rail is switched on.** Marketplace card checkout hard-paused, Stripe in test mode, Premium flag off, StoreKit blocked on Paid Apps Agreement. The only live checkout lane (cash) charges **0 bps by design**. |
| **Traction maturity** | **0 / 10** | Zero production users evidenced. 98% of the 1,357 local accounts are synthetic. 0 orders, 0 ledger transactions, 0 ad campaigns. |

---

## VALUATION SUMMARY

### REPLACEMENT COST
*What a competent org would spend rebuilding this to spec.*

| | Full scope | Verified-working subset only |
|---|---:|---:|
| **LOW** | $1.1M | $0.70M |
| **MID** | **$3.1M** | **$1.9M** |
| **HIGH** | $6.6M | $4.0M |

Basis: 140 / 214 / 302 person-months at $8k / $14.5k / $22k per PM. MID ≈ 17.8 person-years. Method and per-domain breakdown in `02`.

### ASSET / IP SALE
*Code, architecture, brand and IP sold **without** the founder.*

| LOW | MID | HIGH |
|---:|---:|---:|
| **$50,000** | **$150,000** | **$350,000** |

Tacit knowledge behind 550 tables and a 14-category audio regime does not transfer in a repository. A founder-less code sale is the weakest structure available.

### FAIR-MARKET VALUE TODAY
*Orderly sale, founder cooperating through transition, no retention commitment.*

| LOW | MID | HIGH |
|---:|---:|---:|
| **$65,000** | **$200,000** | **$450,000** |

Reconciles the $53,000 pure-discount floor from `04` upward for option value, the App Store listing, the operating-policy corpus, and the genuine scarcity of working real-time infrastructure.

### STRATEGIC BUYER VALUE
*Founder retained; buyer paying for time-to-market and scarce expertise.*

| LOW | MID | HIGH |
|---:|---:|---:|
| **$250,000** | **$700,000** | **$1,800,000** |

≈3–4× asset value, which is normal — and **contingent on the founder transacting as part of the deal.**

### UNDX CONTRIBUTION
*Embedded contribution, not standalone — UNDX is 40–60% PulseSoc-specific and not separately saleable.*

| LOW | MID | HIGH |
|---:|---:|---:|
| **$15,000** | **$60,000** | **$180,000** |

---

## TRACTION SCENARIOS

**VALUATION ASSUMPTION.** These are conditional projections, not forecasts. Each assumes *engaged* users with credible retention (D30 ≥ 25%), not registration counts. Low retention collapses every range toward its floor. Ranges express enterprise value, not asset value.

| Scenario | Value range |
|---|---|
| **10K active users** | **$750,000 – $2,500,000** |
| **50K active users** | **$2,500,000 – $8,000,000** |
| **100K active users** | **$6,000,000 – $20,000,000** |
| **Strong MRR + retention** (~$50–100k MRR, D30 ≥ 35%, card payments live) | **$8,000,000 – $25,000,000** |

The step from today to the 10K row is worth roughly **4–12×** the entire current valuation. No engineering work in this repository has comparable leverage.

---

## TOP 10 VALUE DRIVERS

1. **Working real-time infrastructure.** Agora ingest → Mux recording → HLS replay, plus 1:1 WebRTC calling with CallKit/VoIP, physically validated (`reports/realtime_audio_verified_baseline.md`, commit `ce03e160`).
2. **The audio ownership regime.** 14 protected categories, 30+ forbidden API patterns, 15 test files, a CI gate. Scar tissue that cannot be bought, only earned.
3. **Platform breadth under one auth and data model.** Social + commerce + ads + AI + crypto integrated, not bolted together.
4. **The native app.** 263k LOC, ~193 screens, i18n-gated, build 17 — real TestFlight iteration history.
5. **179,000 LOC of tests** (112k backend, 67k mobile) plus a 20-suite protection battery.
6. **Payments architecture.** Real Stripe Connect calls, multi-secret HMAC webhook verification, an 11-state settlement machine with separated fee/seller/tax ledgers.
7. **Operating-policy corpus.** Marketplace fee/payout/returns/compliance/prohibited-goods/seller-standards docs, a 10-document ads architecture spec, deployment and backup runbooks.
8. **Agentic governance patterns.** Registry-gated capability, deterministic server-side policy, two-tier approval, 20 read-back verifiers — above market.
9. **Six deployed worker processes**, not the three documented — ads, alerts and media pipelines are live.
10. **Demonstrated velocity.** 1,816 commits in 3.7 months, largely single-author. As a signal about the founder, this is itself an asset.

## TOP 10 VALUE DISCOUNTS

1. **Zero users, zero revenue.** −85 to −92%. Dominates everything else.
2. **Marketplace card payments hard-paused** (`services/marketplace_payment_pause.py:49–57`); the only live lane charges 0 bps.
3. **Stripe in test mode.** No live charge has ever been processed.
4. **Bus factor of 1.** 1,816 commits resolve to a single human.
5. **Not shipped.** No Apple Distribution certificate; org/D-U-N-S unverified; listing held by an individual.
6. **UNDX overclaim risk.** Corpus inert, retrieval is keyword matching, execution log absent.
7. **Protection suite absent from CI.** `.github/workflows/protection.yml` does not exist; only 2 narrow workflows do.
8. **No observability.** No Sentry, no structured logging, no worker health checks, no coverage tooling.
9. **Architectural fragility.** Duplicate Flask app construction; silent subsystem drops; 550 imperative `CREATE TABLE`s with no migration framework.
10. **No corporate substantiation in-repo.** No incorporation docs, cap table, trademarks, or financials.

---

## WHAT WOULD DOUBLE THE VALUE

Roughly $200k → $400k. All achievable in weeks, none requiring new product surface.

1. **Ship to the App Store.** Resolve org/D-U-N-S verification, obtain a Distribution certificate, submit build 18. Converts "unshipped" to "live" — the single largest binary in the report.
2. **Turn on one monetisation rail and process real transactions.** Unpause marketplace card, or enable Premium. Even $500 of genuine volume changes the asset's category from unvalidated to validated.
3. **Wire `protection.yml` into CI.** The 20 suites already exist and are already passing locally; they are simply not gating merges. Hours of work.
4. **Fix the duplicate Flask assignment** (`bot.py:429`/`:1181`) and add Sentry plus worker health checks.
5. **Populate `undx_execution_log.jsonl`** by enabling gated UNDX writes, converting the governance story from design claim to operational record.

## WHAT WOULD 5X THE VALUE

Roughly $200k → $1M+. Months, not weeks.

1. **Reach ~10,000 genuinely engaged users with measurable D30 retention.** This alone does most of the work — see the scenario table.
2. **Establish real GMV or MRR**, even modest, with clean unit economics an acquirer can audit.
3. **Get to bus factor ≥ 2.** One additional engineer who can independently ship against the monolith and the audio regime.
4. **Produce audited-quality financials and clean IP ownership** — entity, cap table, trademark, App Store listing transferred off a personal account.
5. **Prove one differentiated wedge.** Breadth is currently the pitch, and breadth is a weak pitch. One system where PulseSoc is demonstrably best — most plausibly governed in-app agentic actions, backed by a real execution log — converts a broad clone into a category claim.

---

## FINAL MOST DEFENSIBLE NUMBER TODAY

# ≈ $200,000

Defensible range **$65,000 – $450,000**. Below $65k undervalues working real-time infrastructure, the App Store listing, and 179k LOC of tests. Above $450k cannot be defended to a buyer who asks for a user count and receives zero.

With the founder retained in a strategic transaction, **$700,000** is the appropriate mid-point.

---

## CONFIDENCE

**Findings: HIGH.** Every material claim is traced to a file, line, or command output, and the six independent recon passes were cross-checked. Four documented claims in `CLAUDE.md` were found to be **wrong** and corrected against the code:

| `CLAUDE.md` claim | Verified reality |
|---|---|
| LiveKit is the calls/live provider, with a LiveKit patch | **Agora** (`react-native-agora@4.6.2`); no LiveKit dependency exists |
| Procfile runs `web`, `undx_worker`, `email_worker` | Runs **six** processes, adding `ads_worker`, `alert_worker`, `media_worker` |
| CI at `.github/workflows/protection.yml`, 21 subsystems | That workflow **does not exist**; only 2 narrow workflows do |
| ~170 tables in `AUTO_PK_TABLES` | **550** `CREATE TABLE` statements; `AUTO_PK_TABLES` survives only in comments |

One claim in the project's own readiness report was also corrected: the asserted iOS bundle-ID mismatch is **stale** — `app.config.js:6` resolves production to `com.pulsesoc.app`, matching the listing.

**Valuation: MEDIUM.** The inputs are solid and the discount logic is explicit, but pre-revenue asset pricing is inherently wide, comparables are private and sparse, and the range spans roughly 7×. The *ordering* of the conclusions — asset < fair-market < strategic, all far below replacement cost — is high-confidence. The specific dollar figures are directional.

**Traction figures: HIGH confidence that they are zero.** This was checked directly against the local database and cross-referenced with the project's own readiness report. No production metrics exist anywhere in the repository. Production Postgres was **not** queried (out of scope for read-only recon), so a live user count is formally **UNKNOWN** — but no artefact anywhere in the repo suggests one exists.
