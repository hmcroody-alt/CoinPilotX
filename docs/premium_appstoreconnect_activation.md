# Premium Subscriptions — App Store Connect Activation

The PulseSoc Premium **entitlement + payment architecture is fully wired in code**.
The only remaining step is data entry in App Store Connect: create the
auto-renewable subscription products whose IDs the server already expects. This
document is the exact spec — nothing here is invented; every value is transcribed
from the code that consumes it.

> These are **separate** products from the advertising-credit consumables
> (`com.pulsesoc.adcredits.tier1`–`tier5`). Premium must NOT reuse the consumable
> products. Ad Credits and Premium are different money types and must stay so.

## Source of truth in the repo

| What | File |
| --- | --- |
| Apple productId → internal plan_key | `services/business_os/entitlements/iap_apple.py` |
| plan_key → price / interval | `services/business_os/entitlements/schema.py` (`_SEED_PLANS`) |
| plan_key → entitlements granted | `services/business_os/entitlements/schema.py` (`_SEED_CATALOG`) |
| StoreKit JWS verification + notifications | `services/business_os/entitlements/iap_apple.py` |

## Products to create in App Store Connect

Bundle: `com.pulsesoc.app` · App ID 6777591572
Subscription group: **PulseSoc Premium** (one group, so monthly ↔ annual can
cross-grade).

| Product ID (must match exactly) | Type | Duration | Price (USD) | Maps to plan_key |
| --- | --- | --- | --- | --- |
| `com.pulsesoc.premium.monthly` | Auto-renewable subscription | 1 month | $9.99 | `pulse_premium_monthly` |
| `com.pulsesoc.premium.annual`  | Auto-renewable subscription | 1 year  | $99.99 | `pulse_premium_annual` |

Entitlements both products grant (already seeded server-side):
`premium.profile.customization`, `premium.media.higher_quality`,
`premium.undx.advanced`.

### Free trial

The code recognises a `com.pulsesoc.premium.trial` plan, but Apple does **not**
model a free trial as a separate purchasable product — configure it as an
**Introductory Offer** (Free, e.g. 7 days) on the subscription group, applied to
the monthly and/or annual product. Do not create a standalone "trial" product.

## Required backend environment (Railway)

StoreKit verification needs the App Store Server API credentials set. Verify
these are present in production (names per `iap_apple.py`):

- Apple root CA anchors injected for the x5c chain verification.
- App Store Server API key (issuer id, key id, .p8) for
  status/notification lookups.
- `APPLE_IAP_ALLOW_SANDBOX` **unset/false in production** — a sandbox purchase
  must never mint production entitlement.
- App Store Server Notifications V2 endpoint pointed at the app's Apple
  notifications route (handled in `iap_apple.py` / `pulse_apple_iap_credits.py`).

## Activation checklist

1. [ ] Create the two products above with the **exact** product IDs.
2. [ ] Put both in one subscription group ("PulseSoc Premium").
3. [ ] Add the free-trial Introductory Offer (optional).
4. [ ] Fill localized display name, description, review screenshot, and submit
       for review (subscriptions are reviewed with the app or standalone).
5. [ ] Confirm the Railway env vars above are set in production.
6. [ ] Sandbox-test one purchase of each; confirm entitlements flip on and the
       server logs a single grant (idempotent).
7. [ ] Confirm a sandbox purchase does **not** grant production balance.

Until steps 1–2 are done, Premium purchase will fail at StoreKit with "product
not found" — the app code is correct; the products simply don't exist yet in
App Store Connect. **No code change is required to activate Premium.**

---

## Ready-to-paste field values (en-US)

Copy is authored to be accurate to the entitlements seeded in
`schema.py` (`premium.profile.customization`, `premium.media.higher_quality`,
`premium.undx.advanced`). Trim any field Apple flags for length — Apple enforces
per-field character limits (display name ~30 chars, description ~45).

### Subscription group
- Reference name: **PulseSoc Premium**
- (One group; monthly and annual are two billing options at the **same** level.)

### com.pulsesoc.premium.monthly
- Reference name: `PulseSoc Premium — Monthly`
- Duration: **1 month**
- Price: **$9.99** (select Apple's official price point for $9.99, all storefronts)
- Subscription level: **Level 1** (same level as annual)
- Display name: `PulseSoc Premium`
- Description: `Unlock Premium: profile customization, higher-quality media, and advanced AI.`

### com.pulsesoc.premium.annual
- Reference name: `PulseSoc Premium — Annual`
- Duration: **1 year**
- Price: **$99.99** (select Apple's official price point for $99.99, all storefronts)
- Subscription level: **Level 1** (same level as monthly)
- Display name: `PulseSoc Premium`
- Description: `A year of Premium: profile customization, higher-quality media, and advanced AI.`

> Annual "savings" wording: $9.99 × 12 = $119.88 vs $99.99 → ~17% / ~$19.89 saved.
> Only surface this if the product spec/UI already calls for it; do not invent a
> discount claim.

### Availability
All storefronts (global), matching the app's availability. Do not silently limit
to the United States.

### Tax category
Use the app's existing default digital-goods / software tax category. No custom
tax treatment.

### Free trial (optional)
Configure as an **Introductory Offer** on the group: Free, 7 days, new
subscribers. Do not create a separate "trial" product.

### App Review information (both products)
Review note (paste):

```
PulseSoc Premium is an auto-renewable subscription that unlocks premium
account features: profile customization, higher-quality media uploads, and
advanced UNDX AI. Monthly and annual are the same entitlement level, differing
only in billing period. Purchases are verified server-side via signed StoreKit 2
transactions (App Store Server API + Server Notifications V2); the app sets
appAccountToken to the user id so the entitlement binds to the correct account.
To test: sign in with the provided sandbox account, open Premium, and purchase
either option — premium features unlock immediately on verification.
```

- Review screenshot: a real capture of the in-app **PulseSoc Premium** purchase
  surface showing the plan(s), price, and subscribe CTA. **OWNER-SIDE BLOCKER**
  if not available — do not fabricate a mockup.

## Click-by-click runbook (App Store Connect)

1. App Store Connect → **Apps → PulseSoc → Subscriptions** (or Monetization →
   Subscriptions).
2. Under **Subscription Groups**, create/confirm group reference name
   **PulseSoc Premium**.
3. **Create Subscription** → reference name `PulseSoc Premium — Monthly`,
   product ID `com.pulsesoc.premium.monthly` → duration **1 Month**.
4. Set **Subscription Price** → $9.99 (all countries/regions).
5. **Localization (English U.S.)** → display name `PulseSoc Premium`,
   description as above.
6. **App Review Information** → paste review note; attach the Premium screenshot.
7. Repeat 3–6 for `com.pulsesoc.premium.annual` (duration **1 Year**, $99.99).
8. Confirm both show the **same** subscription level in the group.
9. Availability → all storefronts.
10. Status will read *Ready to Submit* / *Prepare for Submission*; these are
    typically submitted **with the next app version**. Do **not** submit the app
    version without explicit owner authorization.

## Prerequisites that only the account owner can satisfy
- Signed-in App Store Connect access (Apple ID + 2FA). An agent cannot log in.
- **Paid Applications Agreement** active, with banking + tax set — required
  before any in-app purchase can be created.
- A real Premium purchase-surface screenshot for App Review.
