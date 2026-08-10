# Mission 3 — Stripe Financial Wiring + Ads Billing: FINAL REPORT

**Date:** 2026-08-10
**STRIPE MODE USED FOR VERIFICATION: TEST**
**REAL MONEY USED: NO**

Stripe sandbox: `acct_1TTVo7FP8qvvGWBI` (COINPLOTXAI INC). All checkout flows used
Stripe's public test cards. No live keys, no live charges, no payouts, no transfers.

---

## Phase status (0–26): ALL COMPLETE

### Configuration (Phases 0–11) — done in prior sessions
- PRAGMA-on-Postgres defect fixed; wallet billing tables registered in `AUTO_PK_TABLES`
  (`services/db.py`, commit `1a522b98`) — confirmed working in production (checkout
  session id + URL now attach to funding-session rows).
- Webhook endpoint `we_1U2tZEFP8qvvGWBIk57epzj2` ("pulsesoc-ads-billing") →
  `https://pulsesoc.com/stripe-webhook`, enabled, 25 events.
- Root cause found and fixed: Railway `STRIPE_WEBHOOK_SECRET` held the OLD account's
  signing secret → 400 "No signatures found matching". Owner replaced it with the
  secret for this endpoint; redeploy `2e0dfc34` verified.

### Funding verification (Phases 12–19)

| Test | Result |
|---|---|
| $10.00 checkout, test card 4242 | **PASS** — webhook 200, FS4 → `credited`, exactly ONE funding transaction (TX 78, 1000¢, `posted`), wallet available=1000¢, lifetime_funded=1000¢, receipt AD-RCPT-4-20260810 paid + invoice AD-INV-4-20260810 |
| Duplicate webhook (Resend) | **PASS** — second delivery 200, funding TX count still 1, wallet unchanged. Idempotency holds |
| Declined card 4000-0000-0000-0002 | **PASS** — Stripe declined; FS5 stays `checkout_created`, zero credit, wallet unchanged |
| Below-minimum (<500¢): 400, 0, −100 | **PASS (with note)** — no sub-minimum session can exist. `safe_int` **clamps** to MIN_FUNDING_CENTS=500 rather than rejecting with 4xx (`services/pulse_ad_payments.py:612`). FS 6/7/8 created at 500¢, never paid, no wallet impact |
| Campaign spend drawdown | **PASS** — 9/9 tests (`tests.pulse_ads.test_wallet_spend_drawdown`): grants drawn before cash, no bucket driven negative, unaffordable spend pauses campaigns + writes no transaction, redelivered spend not double-charged |
| Promo/cash separation | **PASS** — promo credits consumed-not-counted test green; prod wallet shows promotional_credits=0, cash=1000¢, cleanly separated. Reversal suite 8/8 green (refund debits once, dispute chargeback, negative-balance-as-debt, partial refunds) |

**Full ads test suite: 116/116 OK** (`tests/pulse_ads`).

### Findings / notes
1. **Growth Center UI "$0.00" was stale render, not a bug** — the wallet tile is fed by
   `/api/pulse/growth`; the initial page load fetched before/with a cached pre-credit
   response. The portal's Refresh button re-fetches and renders **$10.00** correctly.
   Backend was always correct.
2. **Clamp-vs-reject:** below-minimum amounts are silently raised to $5.00 instead of
   returning a 400. Invariant safe; consider explicit rejection for UX honesty (optional
   follow-up, not done — out of mission scope, would touch payment code).
3. Benign log line "Stripe user resolution failed customer_id=None" — credit resolves
   via `client_reference_id=pulse_ad_wallet:<fs_id>`; no action needed.
4. FS rows 5–8 remain `checkout_created` (never paid) — harmless; Stripe checkout
   sessions expire automatically after 24h.
5. Repo root housekeeping (stale `.fuse_hidden*` files) untouched, as instructed.

### Stripe Connect readiness inventory (Phase 20) — NO live payouts
- Provider boundary: `services/payment_provider.py` — Express accounts
  (`stripe.Account.create`, capabilities card_payments+transfers), onboarding links
  (`stripe.AccountLink`, type `account_onboarding`), status polling
  (`payouts_enabled` / `charges_enabled` / requirements).
- Persistence: `services/business_os/payments/connect_accounts.py` (mapping table with
  payouts_enabled/charges_enabled/details_submitted flags; migrates legacy
  `seller_payout_accounts`); `account.updated` webhook applied at `bot.py:96272`.
- Payouts: `services/business_os/payments/seller_payouts.py` is **ledger-only** —
  requests are recorded and posted `seller_payable → seller_payout_pending` with
  idempotency keys; no `stripe.Transfer`/`stripe.Payout` call fires from this path.
- Missing for live Connect: `STRIPE_CONNECT_CLIENT_ID` (unset — fine; Express flow
  doesn't need it until OAuth-style onboarding is used). No live payout was created.

### Railway variable audit (Phase 21) — names + presence only
SET: STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET,
PULSE_ADS_BILLING_ENABLED, PAYMENT_PROVIDER_ENABLED, APP_BASE_URL, DATABASE_URL,
STRIPE_PRICE_ID, STRIPE_FOUNDER_PRICE_ID, STRIPE_PREMIUM_PRICE_ID,
STRIPE_PREMIUM_PLUS_PRICE_ID, NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY.
MISSING (all optional/legacy, unused by ads billing): STRIPE_CONNECT_CLIENT_ID,
STRIPE_PRODUCT_ID, STRIPE_PRO_PRICE_ID, STRIPE_PRO_LINK, STRIPE_FOUNDER_PRODUCT_ID,
STRIPE_PRO_ACCESS_DAYS.
Key mode check: **SK_MODE=TEST**; webhook secret prefix valid. No values printed,
logged, or screenshotted anywhere in this mission.

### Native owner QA prep (Phases 22–23)
iOS in-app wallet funding is intentionally 403-gated (`bot.py:17535`) pending iOS
billing compliance — funding is web-portal-only. Owner QA checklist:
1. iOS app → Ad Wallet screen: balance shows $10.00 (read path), funding button
   directs to web portal (no in-app purchase).
2. Web portal (pulsesoc.com/pulse/ads#wallet): balance $10.00, receipt
   AD-RCPT-4-20260810 listed, Add Funds opens Stripe Checkout (Sandbox banner).
3. Protected systems untouched — no audio/livestream/calls/Messenger/UNDX/Mux/R2/
   marketplace paths modified (git tree clean; only prior `services/db.py` commit).

### Commit/push (Phase 25)
No new code changes this session; working tree clean at `1a522b98` (already pushed to
`main` by owner and deployed as `2e0dfc34`). Nothing to push.

---

**STRIPE MODE USED FOR VERIFICATION: TEST**
**REAL MONEY USED: NO**
