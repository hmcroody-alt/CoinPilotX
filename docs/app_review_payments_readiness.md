# PulseSoc — App Review Payments Readiness (StoreKit + Stripe)

Status date: 2026-08-11. Branch `codex/agora-rtc-migration`, mission commits through
`d00a7311`. Bundle `com.pulsesoc.app`, Apple Team `87ZC69AGSR`, App ID `6777591572`.

## 1. Payment routing policy (Guideline 3.1.1 / 3.1.3 / 3.1.5)

- **Digital goods on iOS → Apple In-App Purchase (StoreKit 2 via expo-iap).**
  Ad credits are digital goods and are sold on iOS exclusively through IAP consumables.
  The server (`services/pulse_payment_router.py`) is the single authority for provider
  routing — the client never decides the final provider. Ambiguous classifications are
  flagged, not guessed (`routing_flagged` result surfaces a neutral message and blocks
  purchase).
- **Physical goods / real-world services → Stripe** (web checkout today; permitted under
  3.1.3(e) and 3.1.5(a) — IAP must not be used for physical goods).
- **Creator payouts → Stripe Connect** (backend foundation in `services/seller_money.py`
  / `services/payment_provider.py`; payouts are money-out, outside IAP scope).
- **Promotional credit → internal non-withdrawable ledger** (no cash-out path, no
  purchase required; not a payment instrument).
- **No external browser checkout is offered on iOS where a native IAP experience
  exists.** The Ads Wallet "Add Funds" card renders IAP tier buttons on iOS whenever the
  server catalog is available; the classic web-checkout form is only the non-iOS /
  catalog-unavailable fallback.

## 2. IAP product catalog (consumables)

| Product ID | Credit | ASC Apple ID | ASC status (2026-08-11) |
|---|---|---|---|
| `com.pulsesoc.adcredits.tier1` | $4.99 | 6800110602 | Prepare for Submission — price, en-US localization, review notes ✓ |
| `com.pulsesoc.adcredits.tier2` | $9.99 | 6800120648 | Prepare for Submission — price, en-US localization, review notes ✓ |
| `com.pulsesoc.adcredits.tier3` | $24.99 | 6800116824 | Prepare for Submission — price, en-US localization, review notes ✓ |
| `com.pulsesoc.adcredits.tier4` | $49.99 | 6800125742 | Prepare for Submission — price, en-US localization, review notes ✓ |
| `com.pulsesoc.adcredits.tier5` | $99.99 | 6800133055 | Prepare for Submission — price, en-US localization, review notes ✓ |

All five are CONSUMABLE, available in 175 countries, tax category "Match to parent
app". Review screenshots pending device build. ASC requires the first consumable to
be submitted with a new app version — attach all five to the next version.

Catalog is served by `GET /api/pulse/ads/iap/products`; the client filters to entries
with a product ID and positive amount, so an empty/failed catalog degrades gracefully.

## 3. Purchase and verification flow

1. Client (`mobile-native/src/payments/appleIapAdCredits.ts`) requests the product via
   StoreKit and obtains the signed transaction (JWS).
2. Client POSTs the JWS to
   `POST /api/pulse/ads/accounts/<id>/wallet/apple-iap/verify`.
3. Server verifies the JWS signature chain against Apple root CAs, checks bundle ID and
   environment, and credits the ad wallet ledger.
4. **Finish-after-credit:** the transaction is finished only after the server confirms
   the credit. If the app dies in the window between purchase and credit, a
   restore-on-mount pass re-drives unfinished transactions.
5. **Idempotency:** one verified Apple transaction maps to at most one ledger credit,
   enforced by a DB-level uniqueness constraint on the transaction identifier. Retries
   and restores are safe (`deduped` result).
6. **Refunds:** handled as compensating reversal ledger entries — originals are never
   mutated or deleted. App Store Server API pull client
   (`services/pulse_apple_server_api.py`) supports transaction lookup/refund history.

## 4. Sandbox testing notes for App Review

- All IAP purchases verify server-side; sandbox receipts are accepted when
  `APPLE_IAP_ALLOW_SANDBOX` is enabled (staging/review), so reviewers can complete
  purchases with a sandbox Apple ID.
- Path for reviewers: sign in → Business OS → Advertising → Ads Wallet → Add Funds →
  choose a tier → confirm StoreKit sheet → balance updates in the wallet header.
- No account funds, subscription, or recurring billing is involved; all products are
  consumables credited to an advertising ledger inside the app.
- Restore behavior: killing the app immediately after the StoreKit sheet completes and
  relaunching the Ads Wallet screen credits the purchase exactly once.

## 5. Server environment (names only)

Required in production for IAP verification: `APPLE_IAP_ISSUER_ID`,
`APPLE_IAP_KEY_ID`, `APPLE_IAP_PRIVATE_KEY`, `APPLE_ROOT_CA_CERTS`; optional:
`APPLE_IAP_ALLOW_SANDBOX`, `APPLE_IAP_EXTRA_BUNDLE_IDS`. Stripe: `STRIPE_SECRET_KEY`
plus webhook signing secret (webhook signatures verified via
`stripe.Webhook.construct_event`; invalid signatures rejected).

## 6. Owner actions outstanding before submission

1. ~~ASC Business: bank account + tax forms~~ DONE 2026-08-11 (Paid Apps Agreement
   ACTIVE; bank ACTIVE; U.S. W-9 ACTIVE. Optional: Brazil/Mexico tax forms).
2. ~~Create the five consumable IAP products in ASC~~ DONE 2026-08-11 (all five fully
   specced, Prepare for Submission). ASC's current flow bundles IAPs at submission time —
   there is no pre-attach step; submit them together with version 1.0.1.
3. ~~Set the Apple IAP environment variables on Railway~~ DONE (all five SET).
4. `cd mobile-native && npm install` (expo-iap ^4.3.1), then EAS build and device QA of
   the sandbox purchase + restore path; capture IAP review screenshots from that build.
5. **Push + deploy:** `git push origin codex/agora-rtc-migration`, then fast-forward
   `main` to it (`git push origin codex/agora-rtc-migration:main`). Production currently
   runs `main` WITHOUT the payments feature — `GET /api/pulse/ads/iap/products` 404s in
   prod until this lands. The local merge commit `f509b7eb` already contains all of main
   (conflicts resolved), so main fast-forwards cleanly.

Note: ASC version 1.0.1 (Developer Rejected, editable) has demo sign-in credentials and
review notes updated 2026-08-11 with the IAP reviewer path.

## 7. Test evidence

- Backend: `tests/business_os/test_iap_apple.py` 11/11,
  `tests/business_os/test_entitlements.py` 26/26, `tests/pulse_ads` suite 186 OK
  (includes 10 App Store Server API client tests).
- Mobile: jest 494 green across payments/api/i18n/copy suites; `npx tsc --noEmit` clean;
  i18n validation OK across 11 locales (9 new Ads Wallet IAP keys fully translated).
- Real-time audio protection gate: green for the mission commit range (no protected
  audio path touched).
