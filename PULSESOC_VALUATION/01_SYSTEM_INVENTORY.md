# 01 — SYSTEM INVENTORY

Read-only recon. Date: 2026-08-27. Branch: `release/full-sweep-20260826`.

Status vocabulary:
- **PV** — Production Verified (works, evidenced, exercised)
- **WP** — Working But Partial (real, gaps or gates)
- **BNV** — Built But Not Verified (code exists, no proof of operation)
- **BLD** — Building (scaffolded, gated, or explicitly "coming soon")
- **PLN** — Planned (infrastructure only)

---

## 0. MEASURED SCALE (VERIFIED FACT)

Git-tracked source only, measured 2026-08-27:

| Area | Files | LOC |
|---|---:|---:|
| `services/` | 577 | 192,105 |
| Root Python (`bot.py` = 118,039) | 13 | 122,519 |
| `tests/` (backend) | 364 | 112,158 |
| `scripts/` | 701 | 77,105 |
| `mobile-native/` TS+TSX | 891 | 262,921 |
| `mobile/` (legacy, dormant) | 116 | 8,696 |
| Web static JS/CSS/HTML | 96 | 57,611 |
| `pulse_communications_v2/` | 10 | 6,792 |
| Markdown | 945 | 121,200 |

**Total non-doc source ≈ 845,000 LOC.**

Other measured counts:
- `550` `CREATE TABLE` statements in `bot.py` (VERIFIED). Note: `CLAUDE.md` claims "~170 tables in `AUTO_PK_TABLES`" — **stale**. `AUTO_PK_TABLES` now appears only inside comments (`bot.py:83006`, `bot.py:83012`); the constant is no longer the schema authority.
- `532` environment keys in `.env.example` (VERIFIED).
- `179` modules in `services/business_os/` (55,606 LOC).
- `53` modules in `services/sentinel/`.
- `301` mobile test files, 67,271 LOC of mobile test code.
- `1,816` commits, first `2026-05-06`, last `2026-08-26` — **project age ≈ 3.7 months**. `122` branches.

---

## 1. MOBILE (`mobile-native/`, Expo 54 / RN 0.81.5)

App identity: `PulseSoc`, version `1.0.1`, iOS build `17`, bundle `com.pulsesoc.app`, Android package `com.pulsesoc.nativeapp`.

| Family | Status | Evidence |
|---|---|---|
| Home / Feed | **PV** | `src/navigation/AppNavigator.tsx:229`; `src/api/feed.ts` (`listFeed`, `reactToPost`, `deletePost`) |
| Reels | **PV** | Tab route `AppNavigator.tsx:236`; `src/screens/ReelsScreen.tsx` |
| Messaging / Chat | **PV** | `AppNavigator.tsx:239,370`; `src/api/messenger.ts` |
| Voice + Video Calls | **PV** | `react-native-agora@4.6.2`; `src/calls/callKitBridge.ts`; regression test `src/calls/__tests__/callAudioOwnershipRegression.test.ts` |
| Live streaming | **PV** | `src/screens/LiveStudioScreen.tsx`; `src/live/liveAudioPublisher.ts`, `liveRuntime.ts` |
| Profile | **PV** | `AppNavigator.tsx:242,88`; `src/api/profile.ts` |
| Settings | **PV** | 13 subscreens `AppNavigator.tsx:104–118`; `src/settings/store.tsx` |
| Search | **PV** | `AppNavigator.tsx:232` |
| Notifications | **PV** | `AppNavigator.tsx:240`; `src/core/unreadCounts.ts`; badge coordination `:261–265` |
| Groups | **PV** | `AppNavigator.tsx:234` |
| Advertising manager | **PV** | 11 ad screens; 14 ad API modules in `src/api/` |
| Statuses / Stories | **WP** | `AppNavigator.tsx:238,377`; `src/api/status.ts` |
| Marketplace / checkout | **WP** | Screens routed `:70–72`; `MARKETPLACE_CART_ENABLED` / `MARKETPLACE_BOOST_ENABLED` gates off |
| Crypto | **WP** | Screens routed `:26–30`; no mobile test coverage found |
| Creator tools | **WP** | Studio/Planner/Growth/Progress routed `:57,64–65`; depth unverified |
| AI / UNDX surfaces | **WP** | `PulseAiScreen` `:241`; `UndxActionCenterScreen`, `UndxCapabilitiesScreen` `:121–122` |
| Music | **WP** | `MusicScreen.tsx`; catalog + attach-to-post; licensing backend unconfirmed |
| Premium / subscriptions | **BLD** | UI built; `DIGITAL_COMMERCE_ENABLED` **defaults OFF** for App Store compliance (`src/api/config.ts:12–15`) |
| Business OS | **BLD** | Routed, but `useLaunchGate()` puts subsections behind "Coming Soon" |
| Presence | **BLD** | `PresenceHubScreen` behind `useLaunchGate()` (`AppNavigator.tsx:91`) |
| Spatial console | **PLN** | Flag-gated, `src/spatial/flags.ts` |

**Native depth (VERIFIED):** two first-party native modules — `modules/pulse-now-playing/` (iOS lock-screen controls, Swift) and `modules/pulse-video-mixer/`. Two custom Expo config plugins. One RN patch (`react-native+0.81.5.patch`). iOS entitlements include Apple Pay merchant `merchant.com.pulsesoc.app`; associated domain `applinks:pulsesoc.com`; background modes audio + fetch + remote-notification.

**Correction to `CLAUDE.md`:** the doc describes a LiveKit WebRTC patch and LiveKit as the calls/live provider. **There is no LiveKit dependency.** The sole RTC provider is `react-native-agora@4.6.2` (VERIFIED in `mobile-native/package.json`). Mux is used server-side only.

---

## 2. BACKEND — COMMERCE & BUSINESS

| System | Status | Evidence |
|---|---|---|
| Marketplace listings (6 product types) | **PV** | `services/marketplace_listing_types.py` (561 LOC) |
| Seller onboarding (Stripe Connect) | **PV** | `services/payment_provider.py:129,152,169` — real `stripe.Account.create()` |
| Orders + fulfillment state machine | **PV** | `services/marketplace_fulfillment.py` (379 LOC) |
| Stripe webhook verification | **PV** | `services/stripe_webhook_verification.py` — HMAC, multi-secret, replay protection |
| Webhook processing | **PV** | `bot.py:99762–99920` — signature validation, duplicate detection |
| Settlement (11-state payout machine) | **PV** | `services/marketplace_settlement_service.py:18–32` |
| Ad wallet + billing | **PV** | `services/pulse_ad_payments.py` (1,854 LOC), 8 transaction types |
| Cash / pickup / in-person checkout | **PV** | `services/marketplace_payment_pause.py:19–30`, `:60–63` — **0 bps platform fee** |
| Cart (Phase 2) | **WP** | `services/marketplace_cart_routes.py` (990 LOC), mobile flag gate pending |
| Seller bank payouts | **BNV** | `services/seller_payouts.py` (933 LOC) calls `stripe.Payout.create()`, but depends on `BUSINESS_OS_LEDGER` flag; **no production payout evidence** |
| Ads delivery / serving intelligence | **BNV** | 47 modules under `services/business_os/ads_intelligence/`; serving maturity unproven |
| Returns / refunds | **BLD** | `services/marketplace_returns_routes.py` (471 LOC); inbox still stubbed |
| **Marketplace card checkout** | **PAUSED** | `services/marketplace_payment_pause.py:49–57` — `marketplace_card_payments_paused()` **hard-returns `True`** |
| Crypto subsystem | **BNV** | `services/business_os/crypto/api.py:11` — "informational only: nothing here executes a trade"; gated by `BUSINESS_OS_CRYPTO` |

**Money-movement reality check (VERIFIED):** the platform can complete a **cash/pickup** transaction end-to-end — cart → cash checkout → `seller_transactions` row → seller notification → in-person settlement → ledger earnings. It **cannot** currently take a card payment in Marketplace; that path is hard-gated off. Seller→bank payout code is real but flag-dependent and unevidenced in production. Stripe is configured in **test mode** (`acct_1TTVo7FP8qvvGWBI`, sandbox webhook `we_1U2tZEFP8qvvGWBIk57epzj2`). **No live charge has ever been processed.**

---

## 3. MEDIA / REAL-TIME

| System | Status | Evidence |
|---|---|---|
| Live streaming (Agora ingest → Mux record → HLS replay) | **PV** | `services/mux_live_service.py`; `media_worker.py:648–719` |
| 1:1 voice/video calls + CallKit/VoIP | **PV** | `src/calls/useNativeCallRoom.ts`; `callKitBridge.ts` |
| Audio ownership / protection regime | **PV** | `config/realtime-audio-protected-paths.json` (14 categories); 15 test files; physical baseline recorded `reports/realtime_audio_verified_baseline.md`, commit `ce03e160`, 2026-08-02 |
| Cloudflare R2 storage + signed URLs | **PV** | `services/media_storage.py:96–130` (boto3) |
| Reels transcode / thumbnails | **WP** | `media_worker.py` job types `process_video`, `generate_thumbnail`; ffmpeg in nixpacks image |
| Push (APNs + FCM) | **WP** | `src/api/push.ts:55–61,116–139`; registration verified, send path unverified |
| Music | **BNV** | ~500 LOC; licensing/royalty backend unconfirmed |
| Group calls | **Absent** | No multi-party conference logic found |
| Live gifting / reactions | **Absent** | No dedicated service found |

**Deployment correction:** `CLAUDE.md` states the Procfile runs only `web`, `undx_worker`, `email_worker`. **Verified Procfile runs six processes:** `web`, `undx_worker`, `email_worker`, `ads_worker`, `alert_worker`, `media_worker`. This materially strengthens the live-replay and crypto-alert pipelines. Only `pulse_worker` and `telegram_worker` are undeployed.

---

## 4. UNDX (AI LAYER)

Total surface **23,777 LOC**. `undx_worker` is deployed (Procfile line 2).

| Component | Status | Evidence |
|---|---|---|
| Provider router (multi-LLM + fallback) | **PV** | `undx_router.py` (503 LOC), `route_undx_request()` `:437–502` |
| Capability registry (98 capabilities) | **PV** | `services/undx_capability_registry.py` |
| Policy engine / kill switches | **PV** | `services/undx_agent_policy.py` — deterministic, server-side, no LLM in the loop |
| Verification (20 read-back verifiers) | **WP** | `services/undx_verification.py:797–820`; no evidence of verification runs |
| Execution kernel (approval-gated writes) | **WP** | `undx_execution_kernel.py` (846 LOC), phrase `APPROVE UNDX WRITE` `:28` |
| Brain layer (mission classification, file ranking) | **BNV** | `undx_brain_layer.py:246–319` — keyword scoring only |
| Knowledge corpus | **INERT** | `UNDX_TRAINING/` (372 KB), `UNDX_RECON/` (1.0 MB) — **no runtime loader** |

**Critical findings.** (a) The corpus is not read at runtime; it is offline documentation, and `UNDX_TRAINING/01_IDENTITY.yaml:1` states it is auto-generated. (b) There is **no vector store, no embeddings, no chunking** — retrieval is keyword scoring plus prompt stuffing (`undx_brain_layer.py:246–283`, with hardcoded bonuses such as `+4` for `bot.py`). (c) `undx_execution_log.jsonl` **does not exist anywhere on disk** (VERIFIED by filesystem search). The only execution evidence is `.undx/desktop_connector_log.jsonl` — 246 entries, 2026-05-31 → 2026-07-25 — and **every entry targets `.undx_connector_audit_workspace`, a sandboxed directory, never the real repo.** UNDX's write path has been exercised in a sandbox and never in production. (d) Only 3 UNDX test files exist.

---

## 5. SECURITY / INFRASTRUCTURE

| Domain | Status | Evidence |
|---|---|---|
| Password hashing + strength policy | **PV** | `services/auth_service.py:9,15` — werkzeug/bcrypt, 12+ chars, mixed case, symbol |
| CSRF + rate limiting | **WP** | `bot.py:2963–2972`, `:3008–3069`, `:2671,2745,2808` (HTTP 429) |
| Sentinel security suite | **WP** | 53 modules in `services/sentinel/` — identity, financial risk, incidents, supply chain, invariants |
| Secrets hygiene | **PV (clean)** | `.env`/`.env.*` gitignored with `!.env.example`; no committed secrets found; only public Apple Root CA in `certificates/` |
| Account deletion (compliance) | **PV** | `bot.py:5608`, `:6906–6923` |
| CI/CD | **WEAK** | **Only 2 workflows exist**: `realtime-audio.yml`, `crypto-alert-persistence.yml`, both narrowly path-scoped |
| Protection suite automation | **NOT IN CI** | `scripts/protection/run_protection_suite.py` + 20 suites in `tests/protection/` exist, but **`.github/workflows/protection.yml` does not exist** |
| Observability | **ABSENT** | No Sentry, no structured logging, no log aggregation found |
| Coverage tooling | **ABSENT** | No `.coverage`, `coverage.xml`, `pytest.ini`, or `tox.ini` |
| RBAC | **UNKNOWN** | No visible role-based authorization system; auth is inline, not decorator-enforced |
| Backup / DR | **UNKNOWN** | `docs/backup_and_restore_runbook.md` exists; no automated backup evidence |

**Correction to `CLAUDE.md`:** the doc claims CI at `.github/workflows/protection.yml` covering 21 subsystems. That workflow **does not exist**. The protection suite is a local/manual runner. This is a material diligence finding — the project's headline safety net is not enforced on merge.

**Fragility markers (VERIFIED):** duplicate Flask app construction at `bot.py:429` and `bot.py:1181` (second wins, first discarded — anything attached between is silently lost); 993 stale `.fuse_hidden*` files at repo root; imperative schema creation with no migration framework; try/except-wrapped route registration that can silently drop subsystems. Counter-signal: **0 bare `except:` blocks and only 2 TODO/FIXME markers in `bot.py`** — the code itself is unusually clean; the risk is architectural, not hygienic.

---

## 6. TRACTION (VERIFIED: NONE)

| Metric | Value | Source |
|---|---|---|
| Production users | **UNKNOWN / no evidence of any** | no production analytics in repo |
| Local dev users | 1,357, of which **1,331 (98%) synthetic** | `coinpilotx.db`; corroborated `APP_REVIEW_READINESS_REPORT.md:14,65` |
| Marketplace orders | **0** | `business_os_mkt_orders` |
| Ledger transactions | **0** | `ledger_transactions` |
| Ad campaigns | **0** | `ad_campaigns` |
| MRR / ARR / GMV | **UNKNOWN — no evidence of any revenue** | — |
| App Store status | Listing exists (App ID `6777591572`, seller "ROODY CHERIE"); **current build not submitted** | `eas.json` `ascAppId`; `APP_REVIEW_READINESS_REPORT.md` |

**Bundle-ID correction.** The readiness report claims a bundle-ID mismatch blocks the App Store upgrade path. This is **stale/incorrect for iOS**: `mobile-native/app.config.js:6` resolves non-development profiles to `com.pulsesoc.app`, which matches the live listing. `com.pulsesoc.nativeapp` is the *Android* package. The genuine blockers are Apple org membership / D-U-N-S verification, absent Apple Distribution signing (local Release builds are development-signed with `aps-environment=development`), and an unaccepted Paid Apps Agreement.

---

## 7. THE `*_REPORT.md` CORPUS

23 mission reports at repo root (plus more under `reports/`). Assessed as **genuine work product, not AI filler**: they cite specific transaction IDs (TX 78), Stripe webhook IDs, exact line-number code references, and — decisively — they record *failures* against their own briefs (e.g. referral→App Store attribution marked FAIL with "iOS gets the web landing page; zero UA branching"). Self-incriminating detail is strong evidence of authenticity. **Caveat:** they verify local code correctness only. None establishes production traction.

`docs/` contains real operating policy with commercial value: marketplace fee/payout/returns/compliance/prohibited-goods/seller-standards policies, a 10-document ads architecture spec, Sentinel security docs, and Railway + backup runbooks.
