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
