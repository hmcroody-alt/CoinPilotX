# 04 — RISK DISCOUNTS

Discounts applied against the replacement-cost anchor to reach fair-market value. Each is evidence-cited. Discounts are applied multiplicatively, not additively, since they compound.

---

## D1. NO TRACTION — **the dominant discount**

**VERIFIED FACT.** Zero production users evidenced. Zero revenue. `coinpilotx.db` holds 1,357 local accounts of which **1,331 (98%) are synthetic** test fixtures (`smoke*`, `*_audit_*`, `phase2tester`, `@example.com`), corroborated by `APP_REVIEW_READINESS_REPORT.md:14,65`. `business_os_mkt_orders` = 0 rows. `ledger_transactions` = 0 rows. `ad_campaigns` = 0 rows.

**Why it dominates.** Every other asset in this repo is an *input* to a business. Users are the only evidence that the business exists. A social platform's entire thesis is network effects; at zero users the network is worth zero and only the machinery has value. There is also no proof of demand — no evidence anyone outside the founder has wanted this.

**Discount: −85% to −92%** against replacement cost.

---

## D2. NO REVENUE, AND THE MONEY PATH IS PARTLY SWITCHED OFF

**VERIFIED FACT.** `services/marketplace_payment_pause.py:49–57` — `marketplace_card_payments_paused()` **hard-returns `True`**. Marketplace card checkout cannot start. Cash/pickup is the only live lane and carries a **0 bps platform fee** (`:60–63`) — i.e. the one working commerce path is deliberately **non-monetising**.

Stripe is in **test mode** (`acct_1TTVo7FP8qvvGWBI`, sandbox webhook `we_1U2tZEFP8qvvGWBIk57epzj2`). No live charge has ever been processed. Premium is flag-gated **off** (`DIGITAL_COMMERCE_ENABLED` default false, `src/api/config.ts:12–15`). StoreKit is blocked on an unaccepted Paid Apps Agreement and unlinked banking. Seller→bank payout depends on the undeployed `BUSINESS_OS_LEDGER` flag with no production payout evidence.

**Interpretation.** The revenue architecture is genuinely built — Stripe Connect calls are real (`services/payment_provider.py:129,152,169`), webhook verification is production-grade, the settlement machine is sophisticated. But **not one of the three monetisation rails (marketplace card, Premium, IAP) is switched on.** A buyer cannot validate unit economics, take rate, conversion, or churn against a single real transaction.

**Discount: −25%.**

---

## D3. FOUNDER DEPENDENCY / BUS FACTOR = 1

**VERIFIED FACT.** `git shortlog -sne --all` over 1,816 commits resolves to **one human**. Identities: `HM Cherie` (940 + 178 = 1,118), `PulseSoc Engineer <engineer@pulsesoc.local>` (652 — agent-authored under founder direction), `UNDX <undx@pulsesoc.com>` (15), `Claude <noreply@anthropic.com>` (2), plus 22 commits across `roodcher@gmail.com` / `hmcroody@gmail.com` aliases of the same person.

There is no second engineer. Project age is **3.7 months** (2026-05-06 → 2026-08-26).

**Why this is severe here.** The codebase is 845k LOC across a 118k-line monolith, 550 tables, 1,538 routes, and a real-time audio regime whose constraints are documented but whose *reasoning* is substantially in one person's head. Acquirer diligence will treat this as key-man risk on a system too large for one person to hand over quickly. Any deal will be structured with heavy retention/earnout — which reduces headline price.

**Discount: −30%.**

---

## D4. NOT SHIPPED — APP STORE PATH BLOCKED

**VERIFIED FACT.** An App Store listing exists (App ID `6777591572`, name "PulseSoc", seller **"ROODY CHERIE"** — a personal developer account). `eas.json` has `ascAppId` configured; app is version `1.0.1`, iOS build `17` — build 17 implies real TestFlight iteration.

Blockers per `APP_REVIEW_READINESS_REPORT.md` and `reports/pulsesoc_native_app_store_release_duns_2026-07-20.md`: Apple organisation membership unverified, D-U-N-S `134170024` unverified, listing owned by an individual while docs claim entity "COINPLOTXAI INC.", **no Apple Distribution certificate** (only "Apple Development: ROODY CHERIE (HB5FV6P922)"), Release builds signed with `aps-environment=development` and `get-task-allow=true`.

**CORRECTION — one claimed blocker is false.** The readiness report asserts a bundle-ID mismatch blocks the upgrade path. `mobile-native/app.config.js:6` resolves non-development profiles to `com.pulsesoc.app`, **matching the listing**. `com.pulsesoc.nativeapp` is the Android package. This blocker is stale; the signing and org-verification blockers are real.

**Discount: −20%.**

---

## D5. UNFINISHED AND GATED SYSTEMS

**VERIFIED FACT.** Explicitly incomplete or switched off: marketplace card checkout (paused); cart (`MARKETPLACE_CART_ENABLED` gate); marketplace boost (`MARKETPLACE_BOOST_ENABLED` false); Premium (`DIGITAL_COMMERCE_ENABLED` false); returns inbox (stubbed); Business OS subsections and Presence Hub behind `useLaunchGate()` "Coming Soon"; spatial console flag-gated; crypto gated by `BUSINESS_OS_CRYPTO` and explicitly bookkeeping-only (`services/business_os/crypto/api.py:11`); group calls absent; live gifting/reactions absent; music licensing backend unconfirmed.

47 ads-intelligence modules exist with **unproven serving maturity** — a large LOC block that cannot be counted as delivered.

**Discount: −15%** (already partly reflected by scoping the verified-subset replacement cost in `02`).

---

## D6. CI/CD AND OBSERVABILITY GAPS

**VERIFIED FACT.** Only **two** GitHub workflows exist: `realtime-audio.yml` and `crypto-alert-persistence.yml`, both narrowly path-scoped. **`.github/workflows/protection.yml` does not exist**, contradicting `CLAUDE.md`. The 20-suite protection battery (`tests/protection/`) and its runner (`scripts/protection/run_protection_suite.py`) are **manual only** — the project's headline safety net does not gate merges.

No coverage tooling (`.coverage`, `coverage.xml`, `pytest.ini`, `tox.ini` all absent), so the real safety margin of 179k LOC of tests is unmeasurable. No Sentry, no structured logging, no log aggregation, no worker health checks.

**Why it matters commercially.** An acquirer inheriting a 118k-line monolith with no merge gates and no production observability is inheriting an operational liability. This is a standard diligence red flag and is cheap to fix, which is also why it is not fatal.

**Discount: −12%.**

---

## D7. ARCHITECTURAL FRAGILITY

**VERIFIED FACT.**
- **Duplicate Flask app construction** at `bot.py:429` and `bot.py:1181`. The second assignment silently discards the first; anything attached between the two lines is lost. A latent, silent, whole-subsystem failure mode.
- **Route packs registered inside `except` blocks** — a broken subsystem vanishes in production rather than failing loudly. `bot.py:2975–2982` documents a real instance: 42 admin endpoints that never called `verify_csrf`.
- **No migration framework.** 550 `CREATE TABLE` statements executed imperatively in `init_db()`; schema changes are hand-rolled and must be idempotent by discipline alone.
- **993 stale `.fuse_hidden*` files** at repo root.

**Counter-signal (genuinely positive).** `bot.py` contains **0 bare `except:` blocks and only 2 TODO/FIXME markers.** The code hygiene is unusually good. The risk is *architectural*, not sloppiness — which means it is fixable by refactor rather than rewrite.

**Discount: −10%.**

---

## D8. SECURITY & COMPLIANCE UNKNOWNS

**VERIFIED FACT — positives first.** Secrets hygiene is **clean**: `.env`/`.env.*` gitignored with `!.env.example`, no committed credentials found, only a public Apple Root CA in `certificates/`. Passwords use werkzeug/bcrypt with a 12+ character mixed-class policy (`services/auth_service.py:9,15`). CSRF and rate limiting exist and return real 429s. Sentinel is 53 modules deep. Account deletion is implemented (`bot.py:5608`).

**Gaps.** No visible RBAC — authorisation is inline in handlers, not decorator-enforced, so consistency across ~1,538 routes is unverifiable. No GDPR/CCPA data-export or retention routes found. Age gating UNKNOWN — material for a UGC social app facing App Store review. UGC moderation depth UNKNOWN. No automated backup/DR evidence.

**Discount: −10%.**

---

## D9. NO AUDITED FINANCIALS / NO CORPORATE SUBSTANTIATION

**VERIFIED FACT.** No incorporation documents, cap table, financial statements, trademark registrations, or domain-ownership proof in the repo. Entity "CoinPlotXAI Inc." appears in `.env.example` and reports; the App Store listing is held by an individual. No `PRIVACY_POLICY` / `TERMS_OF_SERVICE` files at repo root (a `/privacy` route exists at `bot.py:1762–1764`).

**INFERENCE:** these may well exist outside the repository — absence here is not proof of absence. But an acquirer prices what can be verified, and unverifiable IP ownership is a real transaction risk, particularly where the App Store asset sits under a personal account.

**Discount: −8%.**

---

## COMPOUNDED EFFECT

**VALUATION ASSUMPTION.** Starting from the verified-subset MID replacement cost of **$1.9M** (`02`), applying D1 at its mid-point (−88%) and then the remaining discounts multiplicatively:

```
$1.9M
 × 0.12   (D1 no traction, mid)          → $228,000
 × 0.75   (D2 no revenue / rails off)    → $171,000
 × 0.70   (D3 founder dependency)        → $119,700
 × 0.80   (D4 not shipped)               →  $95,760
 × 0.85   (D5 unfinished systems)        →  $81,396
 × 0.88   (D6 CI/observability)          →  $71,628
 × 0.90   (D7 fragility)                 →  $64,465
 × 0.90   (D8 security/compliance)       →  $58,019
 × 0.92   (D9 no financials)             →  $53,377
```

**Pure-discount floor ≈ $53,000.**

This is deliberately the *pessimistic* path — it treats the asset as nothing but discounted engineering. It ignores option value, the strategic scarcity of working real-time infrastructure, the App Store listing, and the operating-policy corpus. The reconciled figure in `00_CURRENT_ECOSYSTEM_VALUATION.md` sits above this floor for those reasons.

---

## RISKS THAT ARE **CHEAP** TO RETIRE

Ranked by value unlocked per unit of effort — the practical punch list:

1. **Ship to the App Store.** Resolve org/D-U-N-S verification and obtain an Apple Distribution certificate. Retires D4 almost entirely and is the precondition for retiring D1.
2. **Turn on one monetisation rail** and process real transactions. Retires most of D2 and begins generating the evidence D1 needs.
3. **Add `protection.yml` to CI.** The suite already exists; it simply is not wired. Hours of work, retires most of D6.
4. **Fix the duplicate Flask assignment** (`bot.py:429`/`:1181`). A one-line class of silent failure; retires the sharpest edge of D7.
5. **Populate `undx_execution_log.jsonl`** by enabling gated UNDX writes. Converts UNDX from design claim to operational claim (see `03` §9).
6. **Add Sentry + worker health checks.** Standard, fast, materially improves the diligence narrative.
7. **Recruit or document toward a bus factor > 1.** Slowest to fix, largest single lever on D3.
