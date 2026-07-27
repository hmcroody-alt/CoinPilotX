# Business OS — Stage 4 Premium / IAP — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** unblock iOS/Android monetization with **server-side IAP receipt/notification verification** (Apple App Store Server Notifications v2 + Google Play RTDN) projected onto the single canonical entitlement ledger, with the full subscription lifecycle (subscribe / renew / grace / refund / revoke / expire) driving grants and revocations across devices.
**Pattern:** strangler — the canonical entitlement substrate built in the prior stage (`business_os_ent_*` + `service.sync_subscription_entitlements`) is reused unchanged; Stage 4 only adds the two IAP provider adapters that *land into* it, replacing the deliberate `AppleAppStoreAdapter` / `GooglePlayAdapter` stubs. Gated behind `BUSINESS_OS_IAP`. No legacy premium table is read or written.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 1 | Apple ASSN v2 JWS verifier (ES256 + x5c chain to injected trust anchor) | **PASS** |
| 2 | Apple notification → canonical lifecycle projection | **PASS** |
| 3 | Google Play RTDN decode + injected purchase-verifier boundary | **PASS** |
| 4 | Framework-agnostic IAP controllers + thin `bot.py` webhook routes | **PASS** |
| 5 | Full IAP test matrix + entitlement / marketplace / advertising / payments regression | **PASS** |
| 6 | This consolidated report | **PASS** |

**Google Play purchase verification** (the authenticated Play Developer API call that confirms a purchaseToken) is honestly **provider-side / NOT executed here** — see §5. Everything else, including the Apple cryptographic verification, is canonical and tested.

---

## 2. What was built

Three modules under `services/business_os/entitlements/` (plus two thin `bot.py` routes):

- `iap_apple.py` — real **ES256 JWS verification** of App Store Server Notifications v2. Verifies the signature against the `x5c` **leaf** certificate's public key, validates the leaf→intermediate→root chain (each link's signature, each cert's validity window) and requires the presented root to match an **injected** trust anchor (Apple Root CA G3 in prod). Rejects `alg != ES256` (no `none`, no alg-confusion), tampered payloads, wrong-key signatures, untrusted chains, and expired certs. Decodes the nested `signedTransactionInfo` / `signedRenewalInfo` JWS in place. `notificationType` → lifecycle intent (grant / grace / expire / revoke), `productId` → canonical `plan_key`, then lands via `upsert_provider_subscription` + `sync_subscription_entitlements` / `revoke_entitlement`.
- `iap_google.py` — Google Play **RTDN** Pub/Sub envelope decode, `notificationType` code → lifecycle intent, and projection — behind an **injected `purchase_verifier` boundary**. Because an RTDN is not proof on its own, this module grants nothing unless the verifier confirms an access-conferring purchase state. Idempotent per `purchaseToken`.
- `iap_api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_IAP` is off; flat `verification_failed` (never crypto internals) on a bad Apple signature; `503 not_configured` when Apple anchors are absent rather than any fabricated success; Google acknowledges the push but grants nothing when no verifier is wired.
- `bot.py` — 2 thin unauthenticated webhook routes (`/webhook/business-os/iap/apple`, `/webhook/business-os/iap/google`) that lazily import `iap_api.py`. Dark 404 when the flag is off.

---

## 3. Verification integrity (the part that must be right)

**Apple — cryptographic, self-contained, offline-provable.** The signed notification carries its own certificate chain; with only the trust anchor we can prove the payload came from Apple and was not altered. The test suite generates a throwaway EC root/intermediate/leaf chain, signs Apple-shaped JWS with it, and drives the *production* verifier — so every branch (valid, tampered, wrong key, untrusted root, expired leaf, non-ES256 alg) is exercised with real ES256 crypto, no Apple secrets, no network.

**Google — authoritative state lives behind an API call we do not fake.** The RTDN only names a `purchaseToken`; the truth is fetched from the Play Developer API. That call is the injected boundary. The adapter refuses to grant on an unverified token — proven by a test where the verifier returns `None` and no entitlement is granted.

**Lifecycle mapping (both providers, landing on the shared substrate):**

- subscribe / renew / restart → `sync_subscription_entitlements(status="active", period_end=…)` — grants every entitlement the plan confers.
- grace / billing-retry → active grant with `grace_until = period_end` (access kept, flagged).
- cancel / defer / pause / expire → left to lapse at `period_end` (the "cancellation keeps access until period end" rule).
- refund / revoke → `revoke_entitlement` scoped to that subscription's source reference — access stripped immediately.

**Verified invariants:** a mapped SUBSCRIBED/PURCHASED grants `premium.profile.customization`; GRACE keeps it; REFUND/REVOKE removes it; an unmapped product records the provider-sub row but grants nothing; a replayed notification yields exactly one provider-sub row and unchanged access (idempotent).

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**IAP — 26/26**

| Suite | Result |
|-------|--------|
| `test_iap_apple.py` (JWS verify + lifecycle) | 11/11 |
| `test_iap_google.py` (RTDN decode + verifier boundary + lifecycle) | 8/8 |
| `test_iap_api.py` (controller contract) | 7/7 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2, slices 1–7 + billing/reporting/admin/assistant/notifications/feed) | 22 | 218/218 |

**Total: 351 tests, 0 failures.** `python -m py_compile bot.py` → **COMPILE OK**. The 2 new IAP webhook routes have unique endpoint function names (`webhook_business_os_iap_apple`, `webhook_business_os_iap_google`); no duplicate paths.

---

## 5. Honest limitations

- **Google Play purchase verification is not executed.** The Play Developer API call that confirms a `purchaseToken` requires a service-account credential and a network request this environment does not perform. It is modeled as an **injected `purchase_verifier`**; the decode, mapping, and projection around it are canonical and tested, and the adapter grants nothing without a verified result. Wiring the real caller is an owner-side step.
- **Apple trust anchors are configuration, not code.** The verifier requires the operator to supply Apple Root CA G3 via `APPLE_ROOT_CA_CERTS`; absent that, the controller returns `503 not_configured` rather than trusting anything. The *cryptography* is fully implemented and tested against a self-generated chain.
- **The optional Apple pull path (App Store Server API transaction lookup)** is out of scope; only the push notification (self-contained, verifiable) is handled.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 2 new routes are verified structurally via `py_compile` and duplicate-endpoint scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.
- **No money moves here.** IAP settlement (Apple/Google collecting the card and remitting) is provider-side; this stage only projects *entitlement* state, never a ledger transfer.

---

## 6. Reversibility

With `BUSINESS_OS_IAP` unset the entire IAP surface is inert: both webhook routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The Apple/Google adapters are additive modules; the entitlement substrate and the legacy premium system are untouched. Rolling back is flag-off; nothing to migrate down.
