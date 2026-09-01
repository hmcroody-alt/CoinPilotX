# Apple Pay — Stage 0 / Stage 1 Audit

> **SUPERSEDED IN PART (2026-08-31).** See
> `APPLE_PAY_STAGE_A_I_VERIFICATION.md`. That report answers the pause root
> cause this document leaves open, and records a payment-invariant failure
> (abandoned-sheet inventory leak) not known when this was written. Stage 0 and
> Stage 1 findings below remain accurate.

**Date:** 2026-08-31
**Scope:** PulseSoc Marketplace (physical / real-world commerce only)
**Verdict:** **BLOCKED — owner action required. No code was written.**

---

## Headline

Apple Pay for the Marketplace is **already implemented** on the existing Stripe
foundation. It is not missing; it is switched off in three places, two of which
only the account owner can reach.

Directive Stage 0 asks whether a second payment stack is needed. The answer is
an unambiguous **no**. Building one would duplicate a working integration and
introduce a second order table, which Stage 10 forbids.

---

## Stage 0 — existing payment foundation

### Processor

Stripe, via PaymentIntents. Marketplace checkout creates the intent server-side
in `services/marketplace_cart_routes.py`, and the native app presents Stripe's
`PaymentSheet`. There is no second processor and no client-side charge creation.

### Apple Pay wiring that already exists

| Layer | Location | State |
|---|---|---|
| SDK dependency | `mobile-native/package.json:39` — `@stripe/stripe-react-native@0.61.0` | Present |
| Expo config plugin | `mobile-native/app.json:69-71` — `merchantIdentifier: merchant.com.pulsesoc.app` | Present |
| iOS entitlement | `ios/PulseSoc/PulseSoc.entitlements` — `com.apple.developer.in-app-payments` → `merchant.com.pulsesoc.app` | Present |
| Entitlement wired to build | `ios/PulseSoc.xcodeproj/project.pbxproj:422, 461` — `CODE_SIGN_ENTITLEMENTS` set on both configs | Present |
| Native Apple Pay init | `src/api/stripePaymentSheet.ts:164-180` — `initStripe({merchantIdentifier})` then `initPaymentSheet({applePay: {merchantCountryCode: "US"}, merchantIdentifier})` | Present |
| Backend merchant-id supply | `marketplace_cart_routes.py:271-281` → response field `apple_pay_merchant_id` | Present |
| Native availability check | `src/api/stripePaymentSheet.ts:105` `isPaymentSheetAvailable()`, called at `MarketplaceCheckoutScreen.tsx:339` | Present |
| Contract tests | `src/api/__tests__/stripePaymentSheet.test.ts:138-141` assert the merchant identifier reaches both init calls | Present |

The Apple Pay button itself is drawn by Stripe's `PaymentSheet`, which renders
Apple's own `PKPaymentButton`. Nothing in this repo imitates or hand-draws an
Apple Pay control, which is what Apple requires.

### Order, settlement, idempotency, refunds

- **Canonical order table:** `seller_transactions`. Cart, offers, and sheet-mode
  checkout all write to it. There is no Apple-Pay-specific table, and none is
  needed.
- **Idempotency:** two layers. Application level — `marketplace_cart_checkout_keys`
  with `UNIQUE(user_id, idempotency_key)`; a replay returns the stored response
  with `replayed: true` (routes lines 628-640, 825-833). Processor level — Stripe
  is passed `idempotency_key=marketplace-cart-sheet:{buyer_id}:{key}` (line 866).
  A duplicate tap cannot create a second charge.
- **No client-side success:** `stripePaymentSheet.ts:57-60` documents it, and
  `MarketplaceCheckoutScreen.tsx:403-406` acts on it — a `completed` sheet moves
  the UI to `processing`, never to paid. The screen then polls
  `getMarketplacePaymentOrder` until the webhook has settled the transaction.
  This already satisfies the required lifecycle: PassKit UI → processor →
  backend settlement → success UI.
- **Inventory:** reserved before the buyer reaches Stripe, keyed to the
  transaction, released on failure (lines 788-806).
- **Refunds:** `services/marketplace_returns_routes.py`, reached from native via
  `openReturn()`. Existing foundation; nothing Apple-Pay-specific required.

### Digital-vs-physical boundary — already enforced server-side

`marketplace_cart_routes.py:645-646`:

```python
if bot.ios_native_app_request() and any(l["fulfillment"] == "digital" for l in lines):
    return bot.ios_paid_digital_unavailable_response(api=True)
```

An iOS client cannot check out a digital line through Stripe at all. Premium and
Ad Credits run on a separate StoreKit layer (`src/payments/appleIapPremium.ts`,
`appleIapAdCredits.ts`, `PaymentController`). The boundary Directive Stage 4
asks for is structural, not a convention — Apple Pay physically cannot reach
digital goods on iOS.

---

## The actual blocker

`services/marketplace_payment_pause.py:49-57`:

```python
def marketplace_card_payments_paused() -> bool:
    return True
```

This is a **hard-coded literal**, not a flag. `marketplace_cart_routes.py:613`
returns `503 PAYMENT_UNAVAILABLE` before any PaymentIntent is created, so the
entire card lane — Apple Pay included — is unreachable. The native UI reflects
this honestly: `MarketplaceCheckoutScreen.tsx:572-575` shows "Card / Stripe"
disabled with a *Temporarily Unavailable* badge.

Only cash / local pickup / in-person settlement is currently live.

**I have not changed this.** Flipping it re-enables real money movement across
the whole Marketplace for every buyer and seller. That is a business decision
with settlement, tax, refund, and support consequences, and it belongs to you,
not to me.

---

## Stage 1 — Apple Developer prerequisites

I could not verify the Apple Developer account state. Determinations below are
split honestly between what the repository proves and what only you can confirm.

| Requirement | Status | Evidence |
|---|---|---|
| Merchant ID `merchant.com.pulsesoc.app` | **UNKNOWN — owner must confirm** | The identifier is declared in three repo locations, but repo text does not prove the identifier exists in Apple Developer → Identifiers → Merchant IDs. |
| Apple Pay Payment Processing Certificate | **UNKNOWN — owner must confirm** | Lives in Apple Developer + Stripe Dashboard. Not representable in this repo. Never inspected; no private key was read or printed. |
| Apple Pay capability on App ID `com.pulsesoc.app` | **UNKNOWN — owner must confirm** | Managed in the developer portal, not the repo. |
| Xcode entitlement + merchant identifier | **PRESENT** | `PulseSoc.entitlements` + `CODE_SIGN_ENTITLEMENTS` on both build configs. |
| `APPLE_PAY_MERCHANT_ID` in production | **DECLARED, value unverified** | The variable name is present on the Railway `CoinPilotX` production service. Values are redacted to this connection, so I cannot tell an empty string from a real one. `.env.example:828` ships it empty. |

No IDs or certificates were fabricated. No private key was read, requested, or
printed.

---

## Apple documentation

**APPLE DOCUMENTATION CONSULTED: NO.**

I attempted three routes and all failed:

1. `mcp__workspace__web_fetch` on the PassKit page returned only the
   JavaScript shell — "This page requires JavaScript."
2. Chrome browser tools refused: *"Navigation to this domain is not allowed"*
   for both `developer.apple.com` and `docs.developer.apple.com`.
3. Computer-use access, to read the page you already have open, timed out
   after 180s.

Rather than paraphrase Apple's requirements from memory and present that as
documentation-verified, I am reporting it as not consulted. The audit above
rests entirely on this repository's source, which I read directly.

This does not block Stage 0/1: the findings are about what PulseSoc currently
does, and the account-level questions are owner-only regardless of what the
documentation says.

---

## Owner actions required

**Apple Developer + Stripe (owner-only):**

1. Confirm `merchant.com.pulsesoc.app` exists under Identifiers → Merchant IDs.
   If it does not, create it with exactly that string — three committed files
   already reference it.
2. Confirm the Apple Pay capability is enabled on App ID `com.pulsesoc.app`.
3. Confirm an Apple Pay Payment Processing Certificate is registered. The normal
   path is Stripe Dashboard → Settings → Payments → Apple Pay, which issues the
   CSR you upload to Apple. Do not send me the certificate or its private key.
4. Confirm the production `APPLE_PAY_MERCHANT_ID` value is exactly
   `merchant.com.pulsesoc.app` and not empty. An empty value makes the sheet
   offer card only — no error, just no Apple Pay.

**Product decision (owner-only):**

5. Decide whether the Marketplace card pause should be lifted. Apple Pay cannot
   be demonstrated to an App Review reviewer while `marketplace_card_payments_paused()`
   returns `True` — the reviewer will reach a disabled control and a
   *Temporarily Unavailable* badge.

**Environment (blocking all verification):**

6. The Linux sandbox is still down (`useradd: input/output error`) and
   computer-use is unavailable. No gate has been run this session. Nothing here
   is claimed as compile-verified — this is source review only.

---

## Recommendation

When items 1-5 are resolved, the remaining engineering work is small and
additive, not a rewrite:

- Lift the pause behind a real flag rather than a literal `return True`, so it
  can be enabled for Marketplace without touching Premium, ads, or payouts.
- Add an Apple-Pay-specific availability signal so the checkout screen can tell
  "device has no cards" apart from "this build has no SDK" — Stripe's
  `isPlatformPaySupported` covers this and is not currently called.
- Add the reviewer-accessible physical-goods path required by Stage 14.
- Physical-device QA. **You must perform the real Apple Pay authorization
  yourself** — I do not execute real payments.

Estimated remaining work is hours, not days, because the integration already
exists.

**FINAL VERDICT: BLOCKED** — on owner account verification and the Marketplace
card-pause decision, not on missing code.
