# 05 — STRATEGIC BUYER ANALYSIS

**Reality check before the analysis.** With zero users and zero revenue (`04` §D1), PulseSoc is not currently an acquisition target for most of the buyers below. This section assesses **who would care and why**, and what they would pay — both today, and in the conditional scenarios where traction exists. Today's realistic transaction is an **asset sale or acqui-hire**, not a strategic acquisition.

---

## THE TWO SCARCE ASSETS

Across all buyer classes, the same two things drive interest. Everything else is replaceable.

**1. Working real-time infrastructure with scar tissue.** Agora ingest → Mux recording → HLS replay, plus 1:1 WebRTC calling with CallKit/VoIP, plus — critically — the audio ownership arbitration regime (`config/realtime-audio-protected-paths.json`, 14 categories, 30+ forbidden patterns, 15 test files, physical baseline at commit `ce03e160`). Teams do not buy this to save the code; they buy it to skip the six months of production audio outages that produced the constraints.

**2. Breadth under one auth/data model.** Feed, reels, statuses, chat, calls, live, marketplace, ads, business tooling, and an AI layer sharing one identity system and one schema. Most companies have one or two of these. Integration is the expensive part, and it is done.

**The consistently over-claimed asset** is UNDX's "intelligence." Sophisticated buyers will find within an hour that the corpus is inert and retrieval is keyword scoring (`03` §2–3). Leading with UNDX as an AI moat invites a credibility loss that damages the rest of the negotiation. Lead with governance patterns instead — those survive scrutiny.

---

## BUYER CLASS ANALYSIS

### A. Social media company

**Why they'd care.** A shipped-quality native RN app with feed + reels + statuses + chat + live already integrated. Fastest route to a second-brand or regional app without a two-year build.

**What they'd value most.** The mobile app (263k LOC, ~193 screens), live + calls infrastructure, the audio regime, i18n gating.

**Discount.** Heavy. They have platform teams and would rewrite the backend into their own infrastructure; a 118k-line Flask monolith is a liability to them, not an asset. Zero users means zero network value — the one thing they actually buy. **−60 to −75%.**

**Premium.** Only if the founder joins and only for the real-time expertise. **+20–30% on an acqui-hire structure.**

**Verdict today:** unlikely buyer. Their bar is users, and there are none.

---

### B. AI company

**Why they'd care.** Not for UNDX's intelligence — for the **governed action surface**. A registry of 98 capabilities against a live consumer product, with construction-level enforcement (`services/undx_policy.py:44–48`), two-tier approval, deterministic policy, kill switches, and 20 read-back verifiers (`services/undx_verification.py:797–820`) is a ready-made environment for agent evaluation and safety research. Real consumer apps with governed mutation surfaces are genuinely scarce.

**What they'd value most.** The governance layer, the capability registry, and the fact that a real app sits underneath to act upon.

**Discount.** Steep on everything else — commerce, crypto, ads are irrelevant to them. And the missing execution log (`03` §6) undercuts the pitch badly: the safety machinery has never run in production. **−50 to −65%.**

**Premium.** **+30–50%** specifically for the governance patterns and the founder's judgment in designing them, if the execution log gets populated first.

**Verdict today:** the most *intellectually* interested buyer, and the one most likely to see through an overstated AI pitch. Fix the execution-evidence gap before approaching.

---

### C. Fintech / crypto company

**Why they'd care.** Stripe Connect onboarding, an 11-state settlement machine with separated fee/seller/tax ledgers, multi-secret HMAC webhook verification, and a crypto subsystem — wrapped in a social surface. "Social layer for a fintech app" is a recurring strategic want.

**What they'd value most.** `services/marketplace_settlement_service.py:18–32`, `services/stripe_webhook_verification.py`, the payout state machine.

**Discount.** Severe, and specific: **card payments are switched off** (`services/marketplace_payment_pause.py:49–57`), Stripe is in **test mode**, and no live charge has ever settled. A payments buyer's first diligence question is "show me the transaction volume," and the answer is zero. The crypto subsystem is explicitly bookkeeping-only (`services/business_os/crypto/api.py:11`) — no custody, no execution, so no regulatory asset. **−60 to −75%.**

**Premium.** **+15–25%** if card payments were live with even modest real volume — this is the single highest-leverage change for this buyer class.

**Verdict today:** poor fit until real money moves.

---

### D. Creator-economy company

**Why they'd care.** Reels + live + statuses + creator studio/planner/growth tools + an ads manager + a marketplace for creator commerce. That is a full creator monetisation stack in one codebase.

**What they'd value most.** Live streaming with replay, the creator tool suite, ad wallet and billing (`services/pulse_ad_payments.py`, 1,854 LOC), marketplace listings across 6 product types.

**Discount.** No creators, no content, no GMV. Creator platforms are valued almost entirely on creator supply. **−65 to −80%.**

**Premium.** **+20–35%** if even a small cohort of active creators existed — this class responds to traction more elastically than any other.

**Verdict today:** high conceptual fit, poor transactional fit.

---

### E. Commerce / marketplace company

**Why they'd care.** Social commerce is a durable strategic theme. Listings, orders, fulfilment, cart, returns, seller onboarding, settlement, plus a documented policy corpus (`docs/marketplace_*`: fee, payout, returns, compliance, prohibited goods, seller standards) — the last of which is legal/ops work most engineering teams underestimate.

**What they'd value most.** The policy corpus and the settlement machine, more than the code.

**Discount.** Zero orders, zero GMV, card checkout paused, cash-only path deliberately charges **0 bps** — the only working commerce lane generates no revenue by design. **−60 to −75%.**

**Premium.** **+15–25%** for the operating-policy documentation, which is unusually complete for a project this young.

---

### F. Telecom / media company

**Why they'd care.** Telcos periodically buy consumer super-app stacks for bundling — messaging + calls + live + payments under one identity system is precisely the super-app shape, and PulseSoc has it.

**What they'd value most.** Breadth, the native app, calls/live infrastructure, white-label potential.

**Discount.** Slowest, most procurement-heavy buyer class. Will demand security audit, SLAs, DR, and compliance evidence — where the gaps are real (no observability, no automated backup evidence, no RBAC, only 2 CI workflows). **−55 to −70%.**

**Premium.** **+25–40%** — the highest of any class — *if* compliance and operational maturity were credible, because breadth is exactly what they want and they pay for time-to-market.

**Verdict:** highest theoretical premium, highest bar to clear.

---

### G. Private equity / strategic technology buyer

**Why they'd care.** They wouldn't, today. PE buys cash flows. There are none.

**Discount.** Not applicable — no thesis. A technology holding company might buy the asset opportunistically at distressed pricing purely for the code. **−80 to −90%.**

**Premium.** None absent revenue.

---

## SUMMARY MATRIX

| Buyer class | Core interest | Discount | Premium | Fit today |
|---|---|---|---|---|
| Social media | Native app, live/calls | −60 to −75% | +20–30% (acqui-hire) | Low |
| AI company | Governance + action surface | −50 to −65% | +30–50% | **Highest** |
| Fintech / crypto | Settlement + Connect | −60 to −75% | +15–25% | Low |
| Creator economy | Reels/live/creator stack | −65 to −80% | +20–35% | Low |
| Commerce | Policy corpus + settlement | −60 to −75% | +15–25% | Low |
| Telecom / media | Super-app breadth | −55 to −70% | +25–40% | Low, high ceiling |
| PE / holding | Distressed code asset | −80 to −90% | — | None |

---

## STRATEGIC BUYER VALUE — TODAY

**VALUATION ASSUMPTION.** A motivated strategic buyer pays above pure asset value for time-to-market and scarce expertise, but in a pre-traction deal the price is dominated by team retention rather than technology. Most realistic structures are acqui-hire with retention/earnout.

| | Strategic buyer value (today) |
|---|---:|
| **LOW** | **$250,000** |
| **MID** | **$700,000** |
| **HIGH** | **$1,800,000** |

MID assumes an AI or telecom buyer, founder retained on a 2-year package, priced primarily on real-time expertise plus governed-agent patterns. HIGH assumes a competitive process with two interested parties and a founder who is credible as a hire — rare but not implausible given demonstrated velocity (1,816 commits, 3.7 months).

**Note the spread against fair-market value** (`00`): strategic value is roughly **3–4× asset value**, which is normal. It is also **contingent on the founder transacting as part of the deal.** A pure code sale without the founder collapses toward the asset-value floor, because the tacit knowledge behind 550 tables and a 14-category audio regime does not transfer in a repository.

---

## POSITIONING GUIDANCE

**Lead with:** working real-time infrastructure and the audio protection regime (verifiable, scarce, expensive to earn); platform breadth under one data model; the operating-policy corpus; agentic governance patterns.

**Do not lead with:** UNDX as an AI moat (the corpus is inert and retrieval is keyword scoring — a technical buyer finds this in an hour); LOC counts (845k invites the "AI-generated volume" question); the `*_REPORT.md` corpus as evidence of traction (it evidences engineering work, which is a different claim).

**Disclose early:** paused card payments, test-mode Stripe, zero users, bus factor of 1, protection suite absent from CI. Every one of these surfaces in diligence within a day. Volunteering them costs a little price and buys disproportionate credibility; being caught on them costs the deal.
