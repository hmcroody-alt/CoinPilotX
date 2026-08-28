# 02 — REPLACEMENT COST MODEL

**Question answered:** what would it cost a competent engineering organisation to rebuild PulseSoc, as it exists today, from zero?

**Question NOT answered:** what PulseSoc is worth. Replacement cost is an *upper bound on the engineering component* of value and is routinely 3–10× the actual market price of a pre-revenue asset. See `00_CURRENT_ECOSYSTEM_VALUATION.md`.

---

## METHOD

LOC is a poor direct cost proxy here, because a meaningful share of this codebase was AI-assisted and generated at a velocity (1,816 commits in 3.7 months, largely single-author) that no human team matches. Costing 845k LOC at industry rates would produce an absurd number.

So the model estimates **person-months (PM) to reproduce the delivered capability**, scoped to what recon verified as actually working, then prices PM at three labour rates:

| Scenario | Team profile | Fully-loaded cost / PM |
|---|---|---|
| **LOW** | Offshore / nearshore, lean senior team, rebuild verified subset only | $8,000 |
| **MID** | Blended onshore + nearshore product team | $14,500 |
| **HIGH** | US senior in-house or top-tier agency, full scope incl. unverified systems | $22,000 |

**VALUATION ASSUMPTION:** rates are fully loaded (salary, benefits, overhead, management). PM counts assume a team that already knows the domain and is not doing discovery — i.e. rebuilding *to spec*, with PulseSoc itself as the spec. Building this without a spec would cost materially more.

---

## DOMAIN ESTIMATES

| # | Domain | Scope basis | LOW (PM) | MID (PM) | HIGH (PM) |
|---|---|---|---:|---:|---:|
| 1 | **Native mobile app** | 891 files / 262,921 LOC; ~193 screens; 14 tabs; i18n-gated; Agora + Stripe + CallKit wiring | 26 | 40 | 52 |
| 2 | **Backend / API** | `bot.py` 118k LOC + `services/` 192k LOC; ~1,538 routes; 550 tables | 32 | 48 | 62 |
| 3 | **Real-time: live + calls** | Agora ingest, Mux record/replay, audio ownership arbitration, interruption recovery | 12 | 17 | 24 |
| 4 | **Marketplace / commerce** | Listings (6 types), orders, fulfilment, cart, settlement 11-state machine, returns | 12 | 18 | 24 |
| 5 | **Business OS** | 179 modules / 55,606 LOC incl. 47 ads-intelligence modules — *heavily discounted, mostly unverified* | 12 | 20 | 34 |
| 6 | **AI / UNDX** | 23,777 LOC; 98 capabilities; policy + verification + kernel | 8 | 12 | 18 |
| 7 | **Security platform (Sentinel)** | 53 modules; identity, financial risk, incidents, supply chain | 6 | 9 | 14 |
| 8 | **Payments integration** | Stripe Connect, webhooks (multi-secret HMAC), ledger, payouts, StoreKit | 6 | 9 | 13 |
| 9 | **Crypto subsystem** | Alerts, watchlists, portfolio, premium intelligence — bookkeeping only | 2 | 3 | 5 |
| 10 | **Infrastructure / DevOps** | Railway, nixpacks, 6 worker processes, R2/CDN, push (APNs+FCM) | 5 | 8 | 12 |
| 11 | **QA / release engineering** | 364 backend test files (112k LOC) + 301 mobile test files (67k LOC); protection suite; audio gate | 12 | 18 | 26 |
| 12 | **Design / PM / i18n / docs** | 945 markdown files; policy corpus; ads architecture spec; runbooks | 7 | 12 | 18 |
| | **TOTAL** | | **140** | **214** | **302** |

---

## COST OUTPUT

| Scenario | Person-months | Rate / PM | **Replacement cost** |
|---|---:|---:|---:|
| **LOW** | 140 | $8,000 | **≈ $1.1M** |
| **MID** | 214 | $14,500 | **≈ $3.1M** |
| **HIGH** | 302 | $22,000 | **≈ $6.6M** |

**Rounded headline: LOW $1.1M / MID $3.1M / HIGH $6.6M.**

Sanity check: MID ≈ 17.8 person-years. For a platform spanning a native social app, a 1,538-route backend, live streaming, WebRTC calling, a marketplace, an ads stack, and an AI layer, ~18 person-years is credible-to-conservative. A from-scratch effort *without* this codebase as the spec would plausibly run 25–35 person-years.

---

## REPLACEMENT COST OF THE **VERIFIED-WORKING SUBSET ONLY**

A buyer does not pay to reproduce gated, paused, or unverified systems. Restricting to what recon classified **PV** (production verified):

Included: mobile feed/reels/chat/calls/live/profile/settings/search/notifications/groups/ads-manager; backend marketplace listings, orders, fulfilment, seller onboarding, webhook verification, settlement machine, ad wallet, cash checkout; Agora+Mux live and calls; audio protection regime; R2 storage; UNDX router + capability registry + policy engine; auth and secrets handling; the test corpus.

Excluded: paused card checkout, flag-gated cart, unverified seller payouts, Business OS "coming soon" sections, ads serving intelligence, crypto, music licensing, Premium (flag off), presence, spatial, UNDX brain/corpus.

| Scenario | PM | **Verified-subset replacement cost** |
|---|---:|---:|
| LOW | 88 | **≈ $0.70M** |
| MID | 132 | **≈ $1.9M** |
| HIGH | 184 | **≈ $4.0M** |

This narrower figure — **~$1.9M mid** — is the more honest anchor for a buyer who intends to ship what exists rather than inherit the whole surface.

---

## WHAT MEANINGFULLY RAISES THE REBUILD COST

**VERIFIED FACT — hard-to-reproduce assets:**

1. **The real-time audio ownership regime.** `config/realtime-audio-protected-paths.json` defines 14 protected categories with 30+ forbidden API patterns, backed by 15 test files and a physical listening baseline (`reports/realtime_audio_verified_baseline.md`, commit `ce03e160`). A team rebuilding live + calls will rediscover every one of these constraints the expensive way — through production audio outages. This is the single most defensible engineering artefact in the repo.
2. **Stripe webhook verification.** `services/stripe_webhook_verification.py` implements multi-secret HMAC validation, motivated by a documented 9-day outage. Scar tissue that is cheap to copy and expensive to learn.
3. **The 11-state settlement machine.** `services/marketplace_settlement_service.py:18–32` with explicit allowed transitions, quote snapshots, and separated fee/seller/tax ledgers. Correct money-state modelling is disproportionately expensive to get right.
4. **179k LOC of tests** (112k backend + 67k mobile). Test suites of this size are rarely reproduced by acquirers and represent real embedded effort.
5. **The operating-policy corpus.** `docs/` marketplace fee, payout, returns, compliance, prohibited-goods and seller-standards policies plus a 10-document ads architecture spec. This is legal/product work, not engineering, and it is genuinely reusable.

## WHAT LOWERS IT

**VERIFIED FACT — cheap-to-reproduce or non-load-bearing:**

1. **UNDX knowledge corpus is inert.** 1.4 MB across `UNDX_TRAINING/` + `UNDX_RECON/` with no runtime loader. Zero rebuild cost, because there is nothing to rebuild — the value claimed is not being delivered.
2. **UNDX retrieval is keyword scoring**, not embeddings (`undx_brain_layer.py:246–283`). Days, not months.
3. **The provider router** (`undx_router.py`, 503 LOC) is a commodity multi-LLM abstraction.
4. **`scripts/` (701 files, 77k LOC)** are one-off audit/mission scripts with little reuse value; largely excluded from the estimate.
5. **Legacy `mobile/`** (8,696 LOC) is dormant and carries no value.
6. **Generated volume.** A substantial fraction of the 845k LOC is AI-produced at superhuman velocity. Costing it at human rates would overstate reproduction effort — which is precisely why this model is PM-based rather than LOC-based.

---

## CAVEAT ON USING THIS NUMBER

Replacement cost answers "what did it take to build?" Buyers of pre-revenue assets pay for **de-risked future cash flows**, not sunk engineering. With zero users and zero revenue (see `01_SYSTEM_INVENTORY.md` §6), an acquirer's alternative to buying is not "spend $3.1M rebuilding" — it is "don't build this at all." That asymmetry is why the fair-market figure in `00_CURRENT_ECOSYSTEM_VALUATION.md` sits at roughly **5–10% of MID replacement cost**, which is the normal ratio for pre-launch code assets.
