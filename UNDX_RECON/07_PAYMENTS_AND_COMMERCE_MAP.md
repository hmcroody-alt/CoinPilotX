# Stage 7 — Payment & Commerce Knowledge Map

**Scope:** every path by which money enters, moves inside, or leaves PulseSoc.
**Method:** read-only source audit. Every claim below carries a `file:line`.
Where a document and the code disagree, both are quoted and the code wins —
sections that say "the doc says" are describing intent, not behaviour.

---

## 0. HEADLINE — the payout claim, VERIFIED OR REFUTED

A sibling agent reported two things. Both are wrong, and the second is wrong in
an interesting way.

### 0.1 "`mobile-native/src/launch/readiness.ts` has 24 `EXPO_PUBLIC_*` flags defaulting OFF, six of them payments" — **REFUTED**

`readiness.ts` is 151 lines and contains **zero** occurrences of `EXPO_PUBLIC`,
zero `process.env` reads, and no flags of any kind. It is a hardcoded, frozen
four-row deny-list of unfinished UI modules:

| id | state | line |
|---|---|---|
| `business:events` | `BUILDING` | `mobile-native/src/launch/readiness.ts:65` |
| `business:customers` | `COMING_SOON` | `:71` |
| `business:team` | `COMING_SOON` | `:78` |
| `presence:businessOs` | `BUILDING` | `:96` |

Its default is the opposite of what was claimed — an id **absent** from the
table is `READY` (`:113-115`), and the header states this is deliberate:
*"the mission guard is 'do not blindly lock an already-production-ready
feature', so the table is an explicit deny-list produced by an audit of every
backing route, not an allow-list"* (`:24-27`). It also gates one route by name,
`BusinessOsEvents` (`:142-144`). None of the four rows is a payments module.

The env-flag system the sibling was probably thinking of lives elsewhere:
`mobile-native/src/core/envFlag.ts` (`envFlagOn()`, truthy set
`["1","true","on","yes"]`) with the payments accessors in
`mobile-native/src/api/paymentsHub.ts:120-259`. The authoritative count is in
the test that pins it: `expect(ACCESSORS).toHaveLength(20)` —
`mobile-native/src/core/__tests__/envFlag.test.ts:210`. **Twenty, not 24**, and
the same file records the shrink history 23 → 21 → 20 at `:203-209`.

### 0.2 "No endpoint initiates a payout anywhere in the codebase" — **REFUTED**

It is refuted by the code, and — more usefully — it is refuted *by the
codebase's own changelog*. That sentence used to be true and was retired on
purpose. Two independent places say so in near-identical words:

`mobile-native/src/api/paymentsHub.ts:186-200`

```ts
/**
 * ... This answered false for as long as no endpoint initiated a payout.
 * That is no longer true: POST /api/pulse/payments/seller/payouts exists and
 * moves money to the seller's connected Stripe account.
 */
export function payoutInitiationIsLive(): boolean {
  return true;
}
```

Note it is a hardcoded `return true`, not an env read — the flag
`EXPO_PUBLIC_PAYMENTS_PAYOUT_INITIATION` was **deleted**, and the test explains
why: retired *"because the Stripe payout rail is live (`api/sellerPayouts`)"*
(`mobile-native/src/core/__tests__/envFlag.test.ts:87-91`).

It is also genuinely wired, not a stranded helper:
`payoutInitiationIsLive()` is called at
`mobile-native/src/screens/BusinessOsPaymentsScreen.tsx:852`, `:857` and `:1007`,
where it gates whether the withdraw module renders. And the route is a
module-level decorator (`bot.py:19955`), **not** inside a `try/except` route
pack, so it cannot silently 404 in production. Its only guards are auth and
abuse control: `api_account_user()` → 401; POST additionally
`pulse_ads_verify_write()` → 403 and
`pulse_ads_rate_limited("seller_payout_request", 10, 3600)` → 429.
**No feature flag anywhere in the path.**

> **⚠ DOC DRIFT — two repo docs still assert the refuted claim.**
> `docs/business_os/FLAG_REGISTRY.md:215` still lists `PAYOUT_INITIATION` as
> `off` with the justification *"No endpoint initiates a payout anywhere in the
> codebase"*, and `docs/business_os/PAYMENTS_SCREEN_REBUILD.md:55` repeats it.
> Both are stale. **A corpus built by reading `docs/` rather than code would
> teach UNDX to tell sellers that payouts do not exist.** These two files should
> be corrected in the repo before any corpus pass ingests `docs/`.

**The full chain, end to end, every hop verified:**

```
mobile-native/src/api/sellerPayouts.ts:150   requestSellerPayout()
        │  mints an idempotency key at :177 (mintPayoutKey)
        ▼
POST /api/pulse/payments/seller/payouts
bot.py:19956   api_pulse_seller_payouts()
        │  write-verify + rate limit 10/3600 + int amount_cents + payout_key
        ▼
services/business_os/payments/seller_payouts.py:471   request_payout()
        │  double-entry move: seller_payable:<uid> → seller_payout_pending:<uid>
        │  (funds are FENCED here — this is what makes double-spend impossible)
        ▼
bot.py:19917   pulse_submit_seller_payout()
        │  args = _bos_payouts.build_stripe_payout_args(payout)   (:578)
        ▼
services/payment_provider.py:272   create_payout()
        ▼
services/payment_provider.py:281   stripe.Payout.create(
                                       **kwargs,
                                       stripe_account=...,          # Connect
                                       idempotency_key=...)
        ▼   ... money leaves the platform ...
bot.py:100336   payout.* + transfer.* webhook branch
        ▼
services/business_os/payments/seller_payouts.py:694   apply_stripe_payout_event()
        │  paid      → seller_payout_pending → platform:payouts_settled  (:809-820)
        │  failed    → reverse back to seller_payable                    (:822)
        │  returned  → reversal + CRITICAL incident                      (:846-868)
        ▼
DONE. Ledger balances.
```

Supporting surface, all real routes:
`GET /api/pulse/payments/seller/connect/status` (`bot.py:20014`),
`pulse_seller_connect_state()` (`bot.py:19863`),
admin `GET /api/pulse/finance/payouts` (`bot.py:20034`).
State machine `ALLOWED_TRANSITIONS` at `seller_payouts.py:64`, handled event set
at `:79`, tables `seller_payout_requests` / `seller_payout_events` at `:133`.

### 0.3 Where the chain **can** still stop — three conditions, all environmental

The rail is structurally complete. It is not unconditionally live. In order of
likelihood:

1. **`STRIPE_SECRET_KEY` unset.** `bot.py:19917` short-circuits before Stripe:
   ```python
   if not (os.getenv("STRIPE_SECRET_KEY") or "").strip():
       return {"submitted": False, "reason": "stripe_not_configured"}
   ```
   The payout row is already created and the seller's funds are already fenced
   in `seller_payout_pending:<uid>`. It parks there as `pending`. **There is no
   retry worker.** I grepped the workers (`*_worker.py`) and the Procfile: no
   process re-attempts pending submissions. Money fenced out of a seller's
   available balance with no automatic path forward is the single sharpest edge
   in the whole payment system. Recovery is manual —
   `reconcile_seller_payouts(stale_after_days=...)`
   (`services/business_os/payments/reconciliation.py:522`) will *flag* it, but
   that function itself only runs from an admin button (see §9.3).
2. **No usable Connect account.** `request_payout()` rejects with
   `no_connected_account` or `payouts_disabled` (both HTTP 409,
   `seller_payouts.py:471`+). Client-side mapping at
   `mobile-native/src/api/sellerPayouts.ts:193` (`payoutErrorKey`).
3. **`seller_payable:<uid>` never credited** → `insufficient_balance`, 409.
   Which credit path fed it matters, and only one of the two is on by default —
   see §5.4.

**Verdict: the payout endpoint exists, is wired to `stripe.Payout.create`, and
settles through webhooks into a balanced ledger. Whether it moves money today
is a question about Railway environment variables and Stripe Connect
onboarding, not about missing code.**

---

## 1. MONEY ARCHITECTURE OVERVIEW

Two rails in, one ledger in the middle, one rail out.

```
                    ═══════════ MONEY IN ═══════════

  ┌──────────────────────────┐        ┌───────────────────────────────┐
  │  STRIPE  (web + Android) │        │  APPLE IAP / StoreKit  (iOS)  │
  │  services/payment_       │        │  services/pulse_payment_      │
  │    provider.py           │        │    router.py                  │
  ├──────────────────────────┤        ├───────────────────────────────┤
  │ Checkout Sessions   :200 │        │ premium monthly/annual   :138 │
  │ PaymentIntents      :250 │        │ ad-credit tier1..5       :130 │
  │ Transfers           :261 │        │ bundle com.pulsesoc.app  :151 │
  │ Payouts             :272 │        │ appAccountToken binding  :154 │
  │ Refunds             :289 │        └───────────────┬───────────────┘
  └────────────┬─────────────┘                        │
               │                                      │
   webhook  bot.py:99620 ──┐              ┌── verify  bot.py:18805 (ad credits)
   (5 URL aliases,         │              │           bot.py:18840 (premium)
    multi-secret :99637)   │              │   ASSN v2  bot.py:25391 (flag-gated,
               │           │              │            404 when off :25420)
               ▼           ▼              ▼
        ┌───────────────────────────────────────────────────┐
        │   ROUTER — services/pulse_payment_router.py:81     │
        │   route_payment(); AMBIGUOUS INTENT IS REFUSED :94 │
        │   (Apple 3.1.1 / 3.1.3(e) compliance boundary)     │
        └───────────────────────┬───────────────────────────┘
                                ▼
        ┌───────────────────────────────────────────────────┐
        │   DOUBLE-ENTRY LEDGER — integer cents, no floats   │
        │   services/business_os/ledger/ledger.py            │
        │   post_entry(idempotency_key=...) · get_balance()   │
        │   overdraft guard under row lock                   │
        ├───────────────────────────────────────────────────┤
        │ mkt_order_escrow:<order_id>                        │
        │ seller_payable:<uid>       ← accrual               │
        │ seller_payout_pending:<uid>← fenced for payout     │
        │ platform:marketplace_revenue   platform:payouts_settled │
        │ liability:marketplace_tax      platform:rewards_expense │
        │ external:stripe_marketplace    platform:marketplace_intake │
        └───────────────────────┬───────────────────────────┘
                                ▼
                    ═══════════ MONEY OUT ═══════════
        stripe.Payout.create → seller's connected account
        (§0.2 chain) · settled by payout.* webhook bot.py:100336
```

**Parallel, non-ledger money systems** (they do NOT post to the double-entry
ledger; they keep their own balances):

- **Ad wallets** — `services/pulse_ad_payments.py`, tables `pulse_ad_wallets`,
  `pulse_ad_wallet_transactions`, `pulse_ad_wallet_funding_sessions`,
  `pulse_ad_invoices`, `pulse_ad_receipts`, `pulse_ad_wallet_events`. §6.
- **Creator wallets** — `services/creator_economy_service.py`, tables
  `creator_wallets`, `creator_ledger_entries`, `creator_transactions`. §7.

That is three balance systems, and they do not reconcile to each other
automatically. See §9.1.

---

## 2. STRIPE

### 2.1 The provider wrapper

`services/payment_provider.py` is the only module that touches the `stripe`
SDK. Every function guards on `_stripe_ready()` and returns
`setup_required(...)` rather than raising when keys are missing — this is why a
Stripe-less environment degrades to "nothing happens" instead of 500s.

| Function | Line | Stripe call |
|---|---|---|
| `create_checkout_session` | `:200-247` | `stripe.checkout.Session.create` |
| `create_payment_intent` | `:250` | `stripe.PaymentIntent.create` |
| `create_transfer` | `:261` | `stripe.Transfer.create` |
| `create_payout` | `:272` | `stripe.Payout.create` (`:281`) |
| `create_refund` | `:289` | `stripe.Refund.create` |
| `verify_webhook_signature` | `:303` | `stripe.Webhook.construct_event` |

Gate: `PAYMENT_PROVIDER_ENABLED` + `STRIPE_SECRET_KEY`.

### 2.2 Checkout vs PaymentIntent vs PaymentSheet — when each is used

Both are constructed in the same function, `cart_checkout()`
(`services/marketplace_cart_routes.py:592`), and the branch is by client:

- **PaymentIntent** (`:811`) — native/mobile. Client confirms with the Stripe
  PaymentSheet. Returns a client secret.
- **Checkout Session** (`:854`) — web. Returns a hosted URL.

Premium and ad-wallet funding use Checkout Sessions
(`create_funding_session` → `attach_checkout_session`,
`services/pulse_ad_payments.py:608`, `:637`).

### 2.3 Connect

Destination charges: `application_fee_amount` + `transfer_data.destination`
set from `seller_destination_account_id()` (`bot.py:87335`), which returns
`""` when the seller's account is not chargeable. When it returns empty the
cart records `payout_state = "ledger_pending_onboarding"`
(`marketplace_cart_routes.py:889`) instead of `"connect_routed"` (`:842`) —
i.e. the sale completes, the money sits on the platform, and the seller is owed
it in the ledger until they onboard. Lazy onboarding is also triggered from
rewards claim (`bot.py:20151`).

`account.updated` webhook at `bot.py:100346` keeps `payouts_enabled` current.
Connect account state surfaced by `pulse_seller_connect_state()`
(`bot.py:19863`) and `GET /api/pulse/payments/seller/connect/status`
(`bot.py:20014`). Table `seller_payout_accounts` created at `bot.py:107524`.

### 2.4 Webhook endpoint(s)

**Five URL aliases** all land on the same handler —
GET variants `bot.py:99606-99611`, POST variants `bot.py:99620-99625`.

Signature verification tries **multiple secrets in turn** (`bot.py:99637`). The
comment above it at `:99631-99636` records why: an ads-billing endpoint
(`pulsesoc-ads-billing-live`) returned 400 for nine days because it was
verifying against one secret while Stripe signed with another. Multi-secret
verification is the fix, and it is load-bearing.

Then: ledger inbox under `BUSINESS_OS_LEDGER` (`:99683-99698`), then dedupe
(`:99699`, `:99703`).

### 2.5 Event types and what each one does

| Event | Line | Effect |
|---|---|---|
| `checkout.session.completed` | `:99709` | dispatch by metadata → 6 sub-branches, below |
| ↳ ad wallet funding | `:99718` | `credit_wallet_from_stripe_session` |
| ↳ marketplace cart | `:99731` | settle at `:99771` |
| ↳ `transaction_id` | `:99776` | premium entitlement sync `:99782` |
| ↳ `seller_transaction_id` | `:99792` | settlement `:99820` |
| ↳ founder premium | `:99824` | founder plan grant |
| ↳ Pro activation | `:99856` | Pro flag |
| `invoice.paid` / `invoice.payment_succeeded` | `:99556`, `:100015` | subscription renewal |
| `invoice.payment_failed` | `:99582` | dunning / entitlement risk |
| `checkout.session.expired`, `async_payment_failed` | `:99932` | release reservations |
| `customer.subscription.*` | `:99978` | premium lifecycle |
| `payment_intent.succeeded` | `:100062` | native checkout settle |
| `payment_intent.payment_failed` | `:100237` | failure path |
| `payout.*` + `transfer.*` | `:100336` | → `apply_stripe_payout_event` / `apply_stripe_transfer_event` (`:694`, `:892`) |
| `account.updated` | `:100346` | Connect capability refresh |
| legacy combined branch | `:100353` | see below |

The **legacy combined branch** (`:100353`) is the compatibility tail and it
writes to a *different* payout table: raw `INSERT` into legacy `seller_payouts`
at `:100374`, plus `marketplace_payout_scheduler.apply_provider_event`
(`:100377`), `pulse_ad_payments.reverse_wallet_funding` (`:100389`),
`creator_economy_service.handle_refund` (`:100418`), and
`pulse_apply_marketplace_charge_refund` (`:100444`). Note the table-name
collision — `seller_payouts.py:26-29` explicitly documents that the modern
module's tables are `seller_payout_requests`/`seller_payout_events` precisely
*because* `seller_payouts` was already taken by this legacy path.

### 2.6 Idempotency — five mechanisms, all different

1. Stripe-native `idempotency_key` passed to `Payout.create` (`payment_provider.py:281`).
2. Client-minted `payout_key` (`sellerPayouts.ts:177`) → `bot.py:19956`.
3. Stored-response replay for carts — `marketplace_cart_checkout_keys`, keys at
   `marketplace_cart_routes.py:844` / `:891`.
4. Ledger `post_entry(idempotency_key=...)`, keys derived from Stripe object
   ids so replaying a webhook is a no-op.
5. Webhook dedupe table (`bot.py:99699`, `:99703`).
6. DB-unique keys on `pulse_ad_wallet_transactions.idempotency_key`
   (`pulse_ad_payments.py:512`) and `pulse_ad_wallet_funding_sessions`
   (`:616`).

### 2.7 Refunds

`create_refund` (`payment_provider.py:289`). Consumers:
`marketplace_settlement_service.apply_refund()` (`:176`), invoked from
`bot.py:51942-51965`; ad-wallet `reverse_wallet_funding()`
(`pulse_ad_payments.py:865`); creator `handle_refund()`
(`creator_economy_service.py:300`). `pulse_ad_payments.py:873` notes that
`pulse_ad_refunds` — *"the table the staff finance panel counts"* — previously
had no writer at all, i.e. refunds were happening and the finance panel showed
zero.

### 2.8 Disputes

**No `charge.dispute.*` branch exists in the webhook handler.** I grepped the
full `bot.py:99606-100448` range. Chargebacks are not ingested, not reflected
in the ledger, and not surfaced to sellers. See §9.

### 2.9 Payouts

See §0.2. Table `seller_payout_requests`, 7 client-visible statuses
(`sellerPayouts.ts:29-37`), status chips at `:278`.

---

## 3. APPLE IAP / STOREKIT

### 3.1 Product IDs — `services/pulse_payment_router.py`

**Premium** (`APPLE_PREMIUM_PRODUCTS`, `:138`):

| Product ID | Cents |
|---|---|
| `com.pulsesoc.premium.monthly` | 999 |
| `com.pulsesoc.premium.annual` | 9999 |

**Ad credits** (`APPLE_ADCREDIT_PRODUCTS`, `:130`) — tier1..tier5 at
499 / 999 / 2499 / 4999 / 9999, App Store app id `6777591572`.

Bundle: `APPLE_BUNDLE_ID = "com.pulsesoc.app"` (`:151`).

### 3.2 The routing rule — this is a compliance boundary, not a preference

`route_payment()` (`:81`) decides Stripe vs Apple. Crucially, **an ambiguous
intent is refused rather than defaulted** (`:94-97`). That is the Apple
Guideline 3.1.1 / 3.1.3(e) line: digital goods consumed in-app must go through
IAP, physical goods must not. Guessing in either direction is a rejection.

The same rule appears at the cart: `cart_checkout()` refuses an
iOS + digital-item combination outright (`marketplace_cart_routes.py:631`).

### 3.3 Verification

- **Client-submitted, live:** StoreKit 2 signed-transaction JWS verified
  **offline** against injected trust anchors.
  - `POST /api/pulse/ads/accounts/<id>/wallet/apple-iap/verify` — `bot.py:18805`
  - `POST /api/pulse/payments/apple/premium/verify` — `bot.py:18840`
  - `GET /api/pulse/ads/iap/products` — `bot.py:18795`
- **Server-to-server pull:** `services/pulse_apple_server_api.py` — App Store
  Server API with an ES256 client JWT (`build_client_jwt()` `:84`,
  `is_configured()` `:58`, `CONFIG_VARS` `:41`). Used for orphan
  reconciliation. The module states plainly: *"Nothing in this module writes
  money."*
- **ASSN v2 (Apple push notifications):**
  `POST /webhook/business-os/iap/apple` — `bot.py:25391`
  `POST /webhook/business-os/iap/google` — `bot.py:25420`
  **Both return 404 when `BUSINESS_OS_IAP` is off** (`_business_os_iap_enabled()`,
  `bot.py:25386`). Ad-credit projection at `:25410`.

### 3.4 Live vs stubbed — the honest split

| Capability | Status |
|---|---|
| Purchase → entitlement grant (client verify) | **LIVE** — `bot.py:18840` |
| Ad-credit purchase → wallet | **LIVE** — `bot.py:18805` |
| `appAccountToken` → user binding | **LIVE** — `pulse_payment_router.py:154` |
| Orphan reconciliation (pull) | **LIVE if configured** — read-only by design |
| **Renewal / cancellation / refund via ASSN** | **DARK unless `BUSINESS_OS_IAP=1`** — endpoint 404s |
| Restore purchases | grant path is idempotent — see §4.3 |

The gap that matters: with `BUSINESS_OS_IAP` off, a **subscription that lapses
or is refunded by Apple produces no server-side event**. Entitlement expiry
then depends entirely on stored expiry timestamps and the next client verify.

---

## 4. PREMIUM

### 4.1 The four competing authorities

`services/business_os/entitlements/premium.py:10-27` documents this itself —
four systems have historically claimed to answer "is this user premium?". The
migration flag `BUSINESS_OS_ENTITLEMENTS` takes `off` | `shadow` | `canonical`,
and **`off` means the legacy system is authoritative** (`:30-37`).

`BUSINESS_OS_ENTITLEMENTS` is unset by default. **Therefore the canonical
registry is, today, not the authority — the legacy service is.**

| Layer | Module | Role |
|---|---|---|
| Canonical registry | `services/business_os/entitlements/premium.py` | intended truth; `PREMIUM_ACCESS = "premium.access"` `:55`; `PREMIUM_CAPABILITIES` `:60` (7 keys); `PREMIUM_PLAN_KEYS` `:95` |
| Facade | `services/business_os/entitlements/facade.py` | `check()` — migration-aware dispatcher |
| Legacy service | `services/premium_entitlement_service.py` | authoritative while flag is off; states at `:3-8` that it *is not* the authority |
| Capability engine | `services/premium_capability_engine.py` | UI-facing capability catalogue |

### 4.2 Tiers and prices — the two tables disagree

`services/premium_entitlement_service.py`:
`FOUNDER_PRICE_CENTS = 499` (`:40`), `PREMIUM_VALUE_CENTS = 999` (`:41`),
`PLAN_DEFINITIONS` (`:71`): `free` / `founder_premium` @499 /
`premium_plus` @999 marked `"coming_soon"`.

`services/business_os/entitlements/premium.py`:
`FOUNDER_PLAN_KEY = "pulse_premium_grandfathered"` (`:106`),
`FOUNDER_PRICE_CENTS = 499` (`:110`).

Apple sells `com.pulsesoc.premium.monthly` @999 and `.annual` @9999
(`pulse_payment_router.py:138`). **There is no 499 founder SKU in the Apple
table** — the founder tier is a Stripe/legacy-only concept
(`checkout.session.completed` founder branch, `bot.py:99824`).

### 4.3 Entitlement storage — three tables per grant

`grant_entitlement()` (`premium_entitlement_service.py:556`) writes **three**
tables. The comment at `:561-567` documents the bug that forced the current
shape: a naive insert meant **five restore-purchases produced five grants**.
The fix is select-then-update-or-insert. `has_entitlement()` (`:521`) reads in
order: `user_entitlements` → `premium_entitlements` → canonical bridge.
`revoke_entitlement()` at `:628`.

### 4.4 Capabilities

`services/premium_capability_engine.py` — 18 capabilities. Only four are
ACTIVE: `premium_identity` (`:21`), `creator_ai` (`:30`), `profile_aura`
(`:102`), `trust_visibility` (`:147`). Two are FUTURE (`cohosting_future`
`:165`, `elite_rooms_future` `:174`). **The remaining twelve are SCAFFOLDED** —
present in the registry, not implemented. `capability_registry()` (`:186`)
returns everything DISABLED under `PULSE_PREMIUM_DISABLED`.

### 4.5 Crypto intelligence gate

`services/crypto_premium_gate.py` is the single server-side gate for crypto
premium. Two keys: `CAP_CRYPTO_ADVANCED_ALERTS = "premium.crypto.advanced_alerts"`
(`:40`) and `CAP_CRYPTO_PORTFOLIO = "premium.crypto.portfolio_intelligence"`
(`:41`).

**Naming trap:** `.portfolio_intelligence` is a deliberate alias and is **not**
in `PREMIUM_CAPABILITIES` — the canonical tuple contains
`premium.crypto.portfolio`. `premium.py:78-92` documents the alias explicitly.
Anyone matching capability strings literally across the two modules will get a
false negative.

Design is sound: `has_crypto_capability()` (`:73`) resolves via
`entitlements.facade.check` and **denies on ImportError or any exception —
never fails open** (`:99-107`). `premium_required_response()` returns HTTP
**200** with `code="premium_required"`, never 403, so clients render an upsell
instead of an auth error. Owner bypass `_is_owner()` (`:59`) reuses the
existing `PULSESOC_OWNER_USER_IDS` allowlist rather than inventing a second
one.

---

## 5. MARKETPLACE COMMERCE — FULL LIFECYCLE

### 5.1 Seller onboarding

| Step | Endpoint | Line |
|---|---|---|
| Apply | `/api/pulse/marketplace/seller/apply` | `bot.py:86891` |
| Read application | GET | `bot.py:86959` |
| Save draft | POST | `bot.py:86984` |
| Upload KYC documents | POST | `bot.py:87028` |
| Remove document | POST | `bot.py:87100` |
| Submit for review | POST | `bot.py:87128` |
| Withdraw | POST | `bot.py:87199` |
| Connect onboarding | `pulse_seller_connect_state` | `bot.py:19863` |

Gate used at checkout: `approved_marketplace_seller_for_user`
(`marketplace_cart_routes.py:706`).

### 5.2 Listing lifecycle

Create `bot.py:89126` → media upload `:88867` / attach `:88988` → digital-file
upload `:89046` → submit for review `:89247`. Buyer side: search `:51579`,
seller listings `:51632`/`:52092`/`:52315`/`:52346`/`:52388`, save `:89316`,
report `:89287`. Download of purchased digital goods `:89080`.

### 5.3 Cart → checkout → order

Blueprint `pulse_marketplace_cart`, prefix `/api/pulse/marketplace/cart`,
registered at `services/marketplace_cart_routes.py:940`. Routes at `:370`
(add), `:387`, `:408`, `:491`, `:513`, `:533`, `:567`, `:591` (checkout).
Flags: `MARKETPLACE_CART_ENABLED`, `MARKETPLACE_SHIPPING_COUNTRIES`.

`cart_checkout()` (`:592`) in order:

1. Refuse iOS + digital (`:631`) — Apple compliance, §3.2.
2. Resolve approved seller (`:706`).
3. Fee rate `seller_fee_bps` (`:713`).
4. Per-line `create_quote` (`:716`) → `services/marketplace_quote_service.py`.
5. INSERT `seller_transactions` (`:738`).
6. If no Stripe key → **503 `blocked_stripe_not_configured`** (`:755-759`).
7. Decrement inventory + write `marketplace_inventory_reservations` (`:767-785`).
8. PaymentIntent (`:811`) **or** Checkout Session (`:854`).
9. Stamp `payout_state`: `"connect_routed"` (`:842`) or
   `"ledger_pending_onboarding"` (`:889`).
10. Store replay key (`:844` / `:891`).
11. On error: classify + **release reservation** (`:901-918`).

Reservation resolution: `capture_inventory_reservation()` (`:923`) on success,
`release_inventory_reservation()` (`:928`) on expiry/failure — driven by the
`checkout.session.expired` webhook branch (`bot.py:99932`).

### 5.4 Settlement — TWO paths, and only one is on by default

**Path A — legacy, NOT flag-gated, live:**
`services/marketplace_settlement_service.py`, entered from
`pulse_finalize_marketplace_settlement` (`bot.py:51905`).
`settle_paid_transaction()` (`:120`) splits three ways:

| Credit | Line |
|---|---|
| `seller_payable:{seller_id}` | `:148-153` |
| `platform:marketplace_revenue` | `:155-160` |
| `liability:marketplace_tax` | `:162-167` |

`PAYOUT_STATES` — 11 states — at `:18`; tables at `:77-102`; refunds at `:176`.

**Path B — Business OS escrow, flag-gated, DARK by default:**
`services/business_os/marketplace/service.py:44` —
`FLAG_ENV = "BUSINESS_OS_MARKETPLACE"`; `is_enabled()` (`:47`) treats **unset as
off**; `_require_enabled()` (`:101`) returns **503 `code="disabled"`**.
Escrow account `mkt_order_escrow:<order_id>`, seller credited at
`services/business_os/marketplace/orders.py:474`.

`orders.py:30-33` states the boundary precisely: crediting `seller_payable` is
**accrual only** — *"the actual bank/Stripe transfer … this module deliberately
does not attempt"*. That is correct design: accrual and disbursement are
separate concerns, and disbursement lives in `seller_payouts.py` (§0.2).

**Consequence for §0.3(3):** with `BUSINESS_OS_MARKETPLACE` unset, escrow does
not run, but Path A still credits `seller_payable`, so payouts still have a
funding source. The two paths must not both run for the same order.

### 5.5 Other lifecycle surfaces

Orders `bot.py:88243` / `:88291`; commercial terms `:88435`; returns
`services/marketplace_returns_routes.py`; offers
`services/marketplace_offers_routes.py`; scheduling
`services/marketplace_payout_scheduler.py` (driven from the legacy webhook
branch, `bot.py:100377`).

---

## 6. ADS BILLING

Prepaid wallet model — advertisers fund a balance, campaigns spend against it.
Master flag `PULSE_ADS_BILLING_ENABLED` (`services/pulse_ad_payments.py:71-72`),
separate from `stripe_ready()` which additionally requires `APP_BASE_URL`
(`:75-76`).

### 6.1 Funding

```
POST /api/pulse/ads/accounts/<id>/wallet/funding-session   bot.py:18639
   └→ create_funding_session()          pulse_ad_payments.py:608
      (idempotency_key unique, :616)
   └→ attach_checkout_session()          :637
                    │
          Stripe Checkout ─────────────┐
                                       ▼
      checkout.session.completed, metadata.purpose ==
      "pulse_ad_wallet_funding"                     bot.py:99718
   └→ credit_wallet_from_stripe_session()  pulse_ad_payments.py:682
      · dedupe on existing transaction     :758
      · UPDATE pulse_ad_wallets balance    :769
      · mark funding session credited      :791
      · INSERT pulse_ad_receipts           :809
```

iOS alternative: `POST /api/pulse/ads/accounts/<id>/wallet/apple-iap/verify`
(`bot.py:18805`), plus `services/pulse_apple_iap_credits.py`.

Promotional grants: `grant_promotional_credits()` (`:544`), idempotent via the
`pulse_ad_wallet_transactions` unique key (`:560`).

### 6.2 Spend

`spendable_balance_cents()` (`:464`), `campaign_can_spend()` (`:479`).
Concurrency is taken seriously here: `_begin_immediate()` (`:131`) and
`_release_spend_lock()` (`:156`) serialise spend, and mutations run inside
SQLite savepoints (`_wallet_event`, `:216-241`) so an event-log failure cannot
roll back a balance change.

### 6.3 Reversal

`reverse_wallet_funding()` (`:865`) — from `charge.refunded` etc. via the
legacy webhook branch (`bot.py:100389`). Supports **partial** reversal
(`status IN ('credited','reversed','partially_reversed')`, `:856`;
`reversed_cents` update at `:1011`). Can drive the balance to require an
incident — `_open_wallet_incident()` (`:166`), flushed at `:194`.

### 6.4 Surfaces

| Route | Line |
|---|---|
| `GET .../billing-summary` | `bot.py:18539` |
| `GET .../wallet` | `bot.py:18624` |
| `GET .../wallet/transactions` | `bot.py:19103` |
| `GET .../invoices` | `bot.py:19125` |
| `GET .../receipts` | `bot.py:19147` |
| `POST .../wallet/spending-limit` | `bot.py:19169` |
| `POST .../wallet/auto-topup` | `bot.py:19186` |

Schema created imperatively by `ensure_schema()` (`:284`) — `pulse_ad_invoices`
(`:294`), `pulse_ad_wallet_events` (`:346`), plus `_add_column_if_missing`
migrations for `daily_limit_cents`, `lifetime_limit_cents`,
`auto_topup_enabled/threshold/amount` (`:331-335`).

**Auto-topup is stored but I found no scheduler that acts on it.** The columns
and the endpoint exist; nothing periodically checks
`available_balance_cents < auto_topup_threshold_cents` and charges. Mobile-side
`adTopUpIsLive(billing)` (`paymentsHub.ts:240`) is env-gated.

---

## 7. CREATOR ECONOMY

A **third** balance system, independent of both the ledger and ad wallets.

### 7.1 Tables and flow

`creator_wallets` (`ensure_wallet()`, `services/creator_economy_service.py:75`),
`creator_ledger_entries` (`add_ledger_entry()`, `:103`, INSERT at `:125`),
`creator_transactions`, `creator_revenue_events`, `creator_tax_profiles`.

```
create_transaction()            :190
   └→ attach_checkout()         :244   links to seller_transactions by
                                        metadata_json LIKE '%"creator_transaction_id": N%'
   └→ mark_transaction_paid()   :267
        · seller wallet: entry_type="hold",  status="pending"   :292
        · platform wallet: entry_type="fee", status="posted"    :293
        · reconcile_wallet() both                               :294-295
        · platform_treasury_service.record_platform_fee_from_transaction()  :296
   └→ handle_refund()           :300   (from webhook bot.py:100418)
        · entry_type="refund", capped at net_amount_cents       :314
        · platform_treasury_service.record_refund_reversal()    :316
```

`reconcile_wallet()` (`:153`) recomputes balances by summing
`creator_ledger_entries` (`:169`) — a self-healing pattern, good.

**The `LIKE '%"creator_transaction_id": N%'` join** (`:260-261`, `:285-286`) is
a JSON-substring match against `seller_transactions.metadata_json`. It is
whitespace-sensitive and unindexed. If the metadata is ever serialised with
different spacing, the `UPDATE` silently matches zero rows and the
`seller_transactions` row never flips to `paid`.

### 7.2 Holding period

`holding_period_days()` (`:399-404`): env `MERCHANT_HOLD_DAYS` /
`TEACHER_HOLD_DAYS` / `CREATOR_HOLD_DAYS`, defaulting to **7 days for
merchants, 3 for everyone else**. This is what should flip a `hold`/`pending`
entry to `posted`/available.

### 7.3 The creator payout dead end

`creator_payouts` is `CREATE TABLE`'d **twice** — `bot.py:104640` and
`bot.py:107648` — with an index at `bot.py:112945`, and there is a page route
`/pulse/creator/payouts` → `pulse_creator_payouts_page` (`bot.py:52755`).

**Nothing ever inserts into it.** A repo-wide grep for
`INSERT INTO creator_payouts` / `UPDATE creator_payouts` returns exactly one
hit, in a test fixture (`tests/test_qa_account_classifier.py:270`). Admin
counters read `creator_payouts_placeholder` instead (`bot.py:91980`,
`bot.py:111422`).

Likewise, **no scheduler releases the `hold` entries** after
`holding_period_days`. So: creator earnings accrue into `creator_wallets`
correctly, and there is **no code path that pays them out**. The sibling
agent's claim, wrong about seller payouts, is **true here**.

Treasury side: `services/platform_treasury_service.py` —
`record_platform_fee_from_transaction()` (`:135`),
`record_refund_reversal()` (`:246`), `treasury_summary()` (`:284`).

---

## 8. FEES & TAKE RATE — actual numbers

### 8.1 What the doc says

`docs/marketplace_fee_policy.md` (8 lines, whole file) describes
`MARKETPLACE_STANDARD_V1`: **5% on merchandise net**, zero buyer-service fee,
zero listing fee, zero monthly fee, zero withdrawal fee; snapshotted terms on
existing orders; proportional fee reversal on partial refund; integer cents
only. It is explicitly **INACTIVE** pending owner approval, seller-disclosure
readiness, and an effective timestamp.

### 8.2 What the code charges

`bot.py:87324-87332`:

```python
def seller_fee_bps(cur, seller_type):
    cur.execute("SELECT fee_bps FROM platform_fee_rules WHERE seller_type=? AND status='active' LIMIT 1", (seller_type,))
    row = dict(cur.fetchone() or {})
    return int(row.get("fee_bps") or (1500 if seller_type == "teacher" else 1000))
```

**1000 bps = 10% standard, 1500 bps = 15% for teachers.**

Corroborated in three more places:
- `services/business_os/marketplace/orders.py:57` — `DEFAULT_FEE_BPS = 1000`.
- `services/creator_economy_service.py:55` — fallback
  `1000 if seller_type == "merchant" else 1500 if seller_type == "teacher" else 0`.
  Note the **0% fallback for every other seller type** — a seller type not
  named `merchant` or `teacher` with no DB rule is charged nothing.
- `services/marketplace_quote_service.py:42` calls
  `policy.quote(..., activate_proposed_policy=False)` — the proposed 5% policy
  is passed over, and quotes stamp `fee_policy_version` (`:77`) as the legacy
  value.

Fee arithmetic, integer-only:
`platform_fee = (merchandise_net_cents * rate) // 10_000`
(`marketplace_quote_service.py:44`), `seller_earnings` at `:45`.
`QUOTE_VERSION` `:13`, TTL **900 s** `:14`.

Creator path rounds differently: `int(round(gross * (fee_percent / 100))) + fixed`,
capped at gross (`creator_economy_service.py:69`) — **float arithmetic**, unlike
the marketplace's `// 10_000`. Two rounding regimes on the same platform fee.

### 8.3 `platform_fee_rules` is defined twice, with different schemas

`bot.py:104544` and `bot.py:107513`. One uses `status='active'`
(read by `bot.py:87326`), the other uses `active=1` **and** an `item_type`
column plus `fee_percent` (read by `creator_economy_service.py:43-51`, which
back-fills `fee_percent` from `fee_bps` at `:58-59`).

Whichever `CREATE TABLE IF NOT EXISTS` runs first wins; the second is a no-op.
**One of the two readers is therefore querying a column that may not exist.**
This is the concrete, testable version of the fee inconsistency.

### 8.4 Summary

| Rate | Where | Status |
|---|---|---|
| 5% merchandise net | `docs/marketplace_fee_policy.md` | **proposed, inactive** |
| 10% (1000 bps) | `bot.py:87327`, `orders.py:57` | **live default** |
| 15% (1500 bps) | `bot.py:87327` (teachers) | **live** |
| 0% | `creator_economy_service.py:55` (other types) | **live fallback** |
| Ads | prepaid wallet, no take rate | — |
| Premium | 100% platform (Apple takes 15–30% of IAP off-platform) | — |

---

## 9. GAPS & RISKS

Ordered by expected damage.

### 9.1 Three unreconciled balance systems

The double-entry ledger (`seller_payable` etc.), ad wallets
(`pulse_ad_wallets`), and creator wallets (`creator_wallets`) each maintain
independent balances with independent invariants. Only the first is
double-entry. No process proves the three sum to the platform's actual Stripe
balance. `reconcile_stripe_snapshot()` exists
(`services/business_os/payments/reconciliation.py:831`) but see §9.3.

### 9.2 Creator earnings can be accrued but never paid — **§7.3**

`creator_payouts` has no writer. No hold-release scheduler. This is a real
liability that grows monotonically.

### 9.3 Reconciliation is manual-only

`reconciliation.run_all()` (`:1054`) has exactly one production caller:
`bot.py:19833`, behind `POST /api/pulse/finance/reconcile` (`bot.py:19820`,
status at `:19844`). No cron, no worker, no Procfile entry. Every check below
runs only when a human clicks:

- `reconcile_ledger_balances` `:120`
- `reconcile_ad_wallets` `:188`
- `reconcile_webhook_inbox` `:370`
- `reconcile_suspense` `:462`
- `reconcile_seller_payouts` `:522` — including the negative-balance sweep
  (*"a negative `seller_payable:<uid>` balance means a seller was paid more
  than they earned"*, `:533`; query `:571`; incident `:670`)
- `reconcile_funding_sessions` `:690`
- `reconcile_stripe_snapshot` `:831`
- `reconcile_rewards` `:893`

Incident types: `PAYOUT_STATE_CONFLICT`, `ORPHAN_STRIPE_OBJECT`,
`NEGATIVE_BALANCE_DETECTED` (`services/business_os/payments/incidents.py`),
resolved via `POST /api/pulse/finance/incidents/<id>/status` (`bot.py:19792`).

### 9.4 Stuck payouts have no retry — **§0.3(1)**

Funds fenced in `seller_payout_pending:<uid>`, reason `stripe_not_configured`,
no automatic resubmission. Detected only by §9.3.

### 9.5 No dispute/chargeback handling — **§2.8**

No `charge.dispute.*` webhook branch. Disputed funds are clawed back by Stripe
with no ledger entry, guaranteeing drift.

### 9.6 Apple subscription lifecycle is dark by default — **§3.4**

`BUSINESS_OS_IAP` unset → ASSN endpoints 404 → refunds, cancellations,
billing-retry and grace-period events never reach the server.

### 9.7 Premium split-brain — **§4.1**

Four authorities, three tables written per grant, flag defaults to legacy.
Plus the `premium.crypto.portfolio` vs `premium.crypto.portfolio_intelligence`
alias trap (`premium.py:78-92` vs `crypto_premium_gate.py:41`).

### 9.8 Duplicate `CREATE TABLE` definitions

`platform_fee_rules` (`bot.py:104544` / `:107513`) — §8.3.
`creator_payouts` (`bot.py:104640` / `:107648`).
The `seller_payouts` name collision between legacy (`bot.py:100374`) and modern
(`seller_payouts.py:26-29`).
Root cause is architectural: `CLAUDE.md` notes there is no migration framework
and schema is hand-rolled in `bot.init_db()`.

### 9.9 Fee policy documented ≠ charged — **§8**

Sellers could reasonably be told 5% while being charged 10–15%. Two rounding
regimes (integer `// 10_000` vs float `round()`).

### 9.10 Fragile creator↔seller transaction join — **§7.1**

`metadata_json LIKE '%"creator_transaction_id": N%'` is whitespace-sensitive
and unindexed.

### 9.11 Ad auto-topup stored but not executed — **§6.4**

### 9.12 Known mock data in the payments UI

`PAYMENTS_MOCK_DATA_GAPS` (`mobile-native/src/api/paymentsHub.ts:255`)
self-documents "next payout date" as fabricated: *"no payout schedule is stored
or computed anywhere."* Env-gated-off surfaces: `instantPayoutIsLive` (`:204`),
`statementsAreLive` (`:209`), `taxDocumentsAreLive` (`:216`), `escrowCardIsLive`
(`:222`, noted as *"Business OS only, and that vertical is dark in
production"*).

### 9.13 Optional route packs registered in `except Exception`

Per `CLAUDE.md`, a payments route pack failing to register produces a 404 that
looks like a routing bug. Boot logs are the only signal.

---

## APPENDIX — flags that decide whether money moves

| Variable | Default | Effect when unset/off |
|---|---|---|
| `STRIPE_SECRET_KEY` | unset | payouts park `pending`; cart checkout 503 (`marketplace_cart_routes.py:755`) |
| `PAYMENT_PROVIDER_ENABLED` | unset | all provider calls return `setup_required` |
| `APP_BASE_URL` | unset | ads `stripe_ready()` false (`pulse_ad_payments.py:76`) |
| `PULSE_ADS_BILLING_ENABLED` | unset | ads billing off (`:72`) |
| `BUSINESS_OS_MARKETPLACE` | unset | escrow path 503 (`service.py:47`, `:101`) |
| `BUSINESS_OS_IAP` | unset | ASSN webhooks 404 (`bot.py:25386`) |
| `BUSINESS_OS_LEDGER` | unset | webhook ledger inbox skipped (`bot.py:99683`) |
| `BUSINESS_OS_ENTITLEMENTS` | unset (`off`) | legacy premium authoritative (`premium.py:30-37`) |
| `PULSE_PREMIUM_DISABLED` | unset | (when set) all capabilities DISABLED (`premium_capability_engine.py:186`) |
| `MARKETPLACE_CART_ENABLED` | — | cart routes |
| `MERCHANT_HOLD_DAYS` / `TEACHER_HOLD_DAYS` / `CREATOR_HOLD_DAYS` | 7 / 3 / 3 | creator hold period (`creator_economy_service.py:400-402`) |
| `PULSESOC_OWNER_USER_IDS` | — | crypto gate owner bypass (`crypto_premium_gate.py:59`) |
