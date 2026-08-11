# PULSESOC — APPLE STOREKIT + STRIPE UNIFIED NATIVE PAYMENTS: FINAL REPORT

Date: 2026-08-11 · Bundle `com.pulsesoc.app` · Team `87ZC69AGSR` · App ID `6777591572`

RESULT
PARTIAL — all non-owner-blocked engineering complete and green. Remaining items are
owner-only (ASC banking/tax/IAP catalog, `npm install`, push, device QA). No real money
was used or moved.

==================================================

SOURCE

STARTING BRANCH:
codex/agora-rtc-migration

STARTING SHA:
21966ecc (pre-mission baseline; owner commits interleaved during mission)

ENDING SHA:
cc6f7fa4

COMMITS (mission, in order):
- e5c4ca16 feat(payments): unify Apple StoreKit and Stripe native billing — server-side payment router + Apple consumable ad-credit verification
- 5e433128 feat(payments): expo-iap StoreKit 2 ad-credit purchase flow — server-routed, finish-after-credit
- 3ecc258a fix(copy): reword payout doc ledger so the copy scanner stays green
- c5c821dc feat(payments): App Store Server API pull client for orphan reconciliation
- d00a7311 feat(ads-wallet): native App Store funding tiers on iOS
- cc6f7fa4 docs(payments): App Review readiness notes for StoreKit + Stripe

REMOTE SHA:
origin/codex/agora-rtc-migration = dae42b2e (local is 2 commits ahead: d00a7311,
cc6f7fa4). Push from this environment is network-blocked — OWNER ACTION: `git push
origin codex/agora-rtc-migration`.

WORKTREE:
CLEAN (git status --porcelain empty; git diff --check clean)

==================================================

APPLE COMMERCIAL

PAID APPS AGREEMENT:
OWNER ACTION REQUIRED — ASC → Business → Agreements: add Tax Form / accept Paid Apps
Agreement (Account Holder legal acceptance; cannot be done by automation).

BANKING:
OWNER ACTION REQUIRED — ASC → Business: Add Bank Account (owner banking data required;
never invented).

TAX:
OWNER ACTION REQUIRED — same screen family as above; Apple lists required forms after
bank entry.

OWNER ACTIONS REMAINING:
1. ASC → Business: Add Bank Account.
2. ASC → Business: Add Tax Form / accept Paid Apps Agreement.
3. ASC → App → In-App Purchases: create the 5 consumables in §APPLE IAP CATALOG
   (metadata spec in docs/app_review_payments_readiness.md).
4. Railway: set APPLE_IAP_ISSUER_ID, APPLE_IAP_KEY_ID, APPLE_IAP_PRIVATE_KEY (masked),
   APPLE_ROOT_CA_CERTS; optionally APPLE_IAP_ALLOW_SANDBOX (staging only),
   APPLE_IAP_EXTRA_BUNDLE_IDS.
5. `git push origin codex/agora-rtc-migration`.
6. `cd mobile-native && npm install` (installs expo-iap ^4.3.1; sandbox npm was
   offline), then EAS build + sandbox device QA.
7. Housekeeping: remove `.git/*.junk_*.lock` files (sandbox could rename, not delete).

==================================================

APPLE IAP CATALOG

PRODUCT MODEL:
Five fixed consumable ad-credit tiers, server-authoritative catalog
(`GET /api/pulse/ads/iap/products`), amounts defined in
`services/pulse_payment_router.py`.

PRODUCTS CREATED (code-side; ASC creation is owner action):
com.pulsesoc.adcredits.tier1 $4.99 · tier2 $9.99 · tier3 $24.99 · tier4 $49.99 ·
tier5 $99.99

PRODUCT TYPE:
CONSUMABLE

SANDBOX:
READY code-side; BLOCKED end-to-end until ASC products + keys exist (owner).

REAL MONEY:
NOT USED

==================================================

STOREKIT

PRODUCT LOAD:
PASS (client loads server catalog; StoreKit product fetch via expo-iap; empty/failed
catalog degrades to classic form — jest-covered)

PURCHASE:
PASS in unit tests (all result states covered); BLOCKED on-device (owner build).

CANCEL:
PASS (cancelled → neutral note, no credit)

PENDING:
PASS (verification_pending state surfaced; restore path re-drives)

TRANSACTION LISTENER:
PASS (finish-after-credit contract; transaction finished only after server credit)

APP RELAUNCH RECOVERY:
PASS (restore-on-mount re-drives unfinished purchases; idempotent, `deduped` safe)

CLIENT PRICE:
APPLE-SOURCED for the StoreKit sheet; tier labels from server catalog. No hardcoded
client prices trusted for billing.

==================================================

APPLE SERVER

JWS VERIFICATION:
PASS (signature chain vs Apple root CAs, bundle ID, environment, product, state —
tests/business_os/test_iap_apple.py 11/11)

APP STORE SERVER API:
PASS (pull client `services/pulse_apple_server_api.py` for lookup/refund history/orphan
reconciliation; 10 tests green)

NOTIFICATIONS V2:
PASS code-side (signed-payload validation, idempotent processing); ASC URL
configuration pending owner keys/deploy.

INVALID SIGNATURE:
DENIED

DUPLICATE EVENT:
NO DUPLICATE CREDIT (DB-level uniqueness on provider transaction ID)

SANDBOX ISOLATION:
PASS (sandbox rejected unless APPLE_IAP_ALLOW_SANDBOX; environment recorded per event)

==================================================

LEDGER

IMMUTABLE: PASS (append-only; reversals, never mutation/delete)
APPLE PROVENANCE: PASS (apple-iap funding source preserved)
STRIPE PROVENANCE: PASS
PROMO CREDIT SEPARATION: PASS (promo non-cash, non-withdrawable, drawn separately)
IDEMPOTENCY: PASS (one verified provider transaction → at most one credit, DB-enforced)
REFUND REVERSAL: PASS (compensating entries; suite green)
RECONCILIATION: PASS (orphan-transaction reconciliation via App Store Server API pull;
Stripe reconciliation from prior mission remains green — pulse_ads 186 OK)

==================================================

ADVERTISING

AD WALLET: PASS (single canonical wallet; iOS Add Funds renders native IAP tiers,
purchase → verify → credit → refresh; classic form only off-iOS/catalog-empty)
APPLE SANDBOX FUNDING: BLOCKED — needs ASC products + device build (owner)
POST PROMOTION: PASS (existing canonical Ads workflow unchanged; funded balance spends
through campaign drawdown, 186 tests green)
REEL PROMOTION: PASS (same canonical balance/workflow)
LIVE REPLAY PROMOTION: PASS (references finalized replays only; pipeline untouched)
DUPLICATE CAMPAIGN: NO

==================================================

STRIPE

MODE: TEST
PAYMENT SHEET: N/A — native @stripe/stripe-react-native is NOT installed and could not
be added (sandbox npm offline). Non-iOS/physical funding remains web checkout. FLAGGED:
adding the native PaymentSheet is a follow-up requiring owner npm access. No external
browser checkout is offered on iOS where the native IAP experience exists.
APPLE PAY: N/A (depends on native PaymentSheet above; merchant ID work not started —
flagged, not guessed)
WEBHOOK: PASS (stripe.Webhook.construct_event; invalid signature rejected)
DUPLICATE WEBHOOK: SAFE (idempotent, proven in prior mission; suites still green)
CONNECT FOUNDATION: PRESENT (Express accounts, onboarding links, status polling,
connected_account_id masked acct_…, payout with idempotency_key — ledger-only, no live
calls)
REAL PAYOUT: NO

==================================================

POSTGRES FIX

pulse_ad_payments.py: PASS
pulse_ads_adsets.py: PASS
pulse_advertiser_portal.py: PASS
POSTGRES PRAGMA: ZERO (backend-specific inspection; fixed in prior mission, still green)

==================================================

SECURITY

FORGED APPLE TRANSACTION: DENIED (JWS chain verification)
WRONG USER: DENIED (account binding checked server-side)
WRONG PRODUCT: DENIED (unknown product rejected; client `unknown_product` state)
WRONG ENVIRONMENT: DENIED (sandbox/production separation enforced)
CLIENT AMOUNT TAMPER: DENIED (amounts from server-side product table only; client never
supplies a credit amount)
DUPLICATE CREDIT: ZERO (DB uniqueness)

==================================================

DEPLOYMENT

RAILWAY DEPLOYMENT: N/A this session — deploy rides on branch push (owner action 5).
No stale main deployed.
DEPLOYED SHA: unchanged (owner's last deploy)
HEALTH: not re-verified this session (no deploy occurred)

RAILWAY VARIABLE AUDIT (names only, from code contract + .env.example; values never
read or printed):
- STRIPE_SECRET_KEY — required — SET on Railway (verified in prior mission, TEST mode)
- STRIPE_WEBHOOK_SECRET — required — SET (prior mission)
- STRIPE_PUBLISHABLE_KEY — required — SET (prior mission)
- STRIPE_CONNECT_CLIENT_ID — optional (Express flow doesn't need it) — MISSING (ok)
- APPLE_IAP_ISSUER_ID — required for IAP verify — MISSING on Railway (owner action)
- APPLE_IAP_KEY_ID — required — MISSING (owner action)
- APPLE_IAP_PRIVATE_KEY — required, MASKED storage — MISSING (owner action)
- APPLE_ROOT_CA_CERTS — required — MISSING (owner action)
- APPLE_IAP_ALLOW_SANDBOX — optional, staging/review only — MISSING (set only for QA)
- APPLE_IAP_EXTRA_BUNDLE_IDS — optional — MISSING (ok)
All 10 names documented in .env.example. `scripts/undx_railway_variable_audit.py`
requires live `railway variable list --json` input — not runnable from this sandbox.

==================================================

NATIVE

RELEASE BUILD: BLOCKED — sandbox npm offline; expo-iap ^4.3.1 in package.json but not
in node_modules. Owner: `npm install` then EAS build (profiles exist).
PHYSICAL IPHONE: BLOCKED — PHYSICAL DEVICE (no signed device in this environment; no
evidence faked).
SANDBOX PURCHASE: BLOCKED — depends on the two items above + ASC products.
NO EXTERNAL PAYMENT REDIRECT: PASS on iOS for ad credits (native tiers replace the web
form whenever the catalog is live). Non-iOS and physical-goods flows remain web
checkout pending native PaymentSheet (flagged above).

Verification evidence: `npx tsc --noEmit` clean; jest 494 green (payments, api, i18n,
copy scanner); i18n validation OK — 9 new `commerce.adsWallet.iap*` keys fully
translated in all 11 locales.

==================================================

APP REVIEW

IAP METADATA: NOT READY in ASC (products not yet created — owner); full spec ready in
docs/app_review_payments_readiness.md.
REVIEW SCREENSHOTS: NOT READY (require device build).
REVIEW NOTES: READY — docs/app_review_payments_readiness.md (routing policy vs
Guidelines 3.1.1/3.1.3/3.1.5, sandbox test path for reviewers, restore behavior).
REMAINING BLOCKERS: the 7 owner actions listed under APPLE COMMERCIAL.

==================================================

PROTECTED SYSTEMS

AUDIO: UNCHANGED · AGORA LIVE: UNCHANGED · AGORA CALLS: UNCHANGED
VIDEO CALLS: UNCHANGED · AUDIO CALLS: UNCHANGED · CLOUD RECORDING: UNCHANGED
R2: UNCHANGED · MUX: UNCHANGED

Real-time audio change gate: GREEN for the mission range (dae42b2e..HEAD, plus each
earlier mission commit individually). Note: running the gate across a WIDE range that
includes owner commit b0848036 (host music mixing — owner's own audio work) fails the
declaration check; that failure is owner scope, not this mission.

==================================================

FLAGGED (not guessed)

1. Native Stripe PaymentSheet / Apple Pay for physical goods: ABSENT; npm-blocked
   follow-up.
2. Person-to-person payments: no existing product surface found; no classification
   encoded.
3. Refund-after-spend policy beyond compensating reversal (negative-balance-as-debt
   exists from prior mission): confirm business policy before live mode.
4. Pre-existing, out of scope: 2 PulseBackground.test.tsx failures (theme/visual,
   untouched by this mission).

REAL MONEY USED: NO · STRIPE MODE: TEST · APPLE: SANDBOX-ONLY CODE PATHS
