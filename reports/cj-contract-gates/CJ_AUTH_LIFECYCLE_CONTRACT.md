# CJ authentication lifecycle contract

Checked 2026-09-07. Gate G2: **CLOSED_WITH_CONSERVATIVE_FALLBACK** for the proposed runtime lifecycle; provider answers about actual TTL and cross-key invalidation remain open. This does not close G1 hosted custody. No credential was requested, read, exchanged, refreshed or revoked.

## Verified wire contract

| Operation | Exact endpoint | Authentication/input | Successful data |
| --- | --- | --- | --- |
| Exchange | POST https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken | application/json with apiKey | accessToken, refreshToken, accessTokenExpiryDate, refreshTokenExpiryDate, createDate, openId |
| Refresh | POST https://developers.cjdropshipping.com/api2.0/v1/authentication/refreshAccessToken | application/json with refreshToken | Token pair and both expiration fields; openId is not present in the documented refresh schema |
| Logout | POST https://developers.cjdropshipping.com/api2.0/v1/authentication/logout | CJ-Access-Token | Boolean success; both tokens expire |

Business requests use CJ-Access-Token, not an invented Bearer scheme. API-key setup is in the CJ API app/account interface. The public account-settings link redirected to login; no live account settings were inspected. [Authentication](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [token guide](https://developers.cjdropshipping.com/en/api/start/token.html).

## Evidence versus unresolved lifecycle

| Question | Current evidence | Binding PulseSoc rule |
| --- | --- | --- |
| Actual access TTL | Opening auth prose says 180 days; field table/refresh section/token guide say 15. Old example timestamps are illustrative, not a current runtime observation | Do not choose either duration; actual TTL unproved |
| Refresh TTL | Nominal 180 days; response includes its expiry | Response timestamp governs; no 180-day constant |
| Expiry authority | accessTokenExpiryDate and refreshTokenExpiryDate are explicit fields | Parse offsets, normalize to UTC, reject missing/malformed/expired timestamps |
| Get/refresh caching | Same account receives cached pair within 24 hours; after 24 hours or logout a new pair is generated | Repeated calls need not rotate or extend anything; no refresh spin |
| New pair invalidates old pair? | Not specified | Persist one active version; drain/serialize in-flight credential use and reconcile failed writes; do not rely on overlap |
| Logout scope | Both tokens invalidated; cross-key/account/shop blast radius not specified | Explicit account-aware operation only; never health-check logout |
| Multiple API keys | Account cache is documented, independent token/quota partitions are not | One approved credential lineage per account initially; reject unrelated-tenant account sharing |
| Multiple shops | getShops uses the account token; no shop-specific token documented | Bind locally to owned shop IDs; shops do not get independent credentials by assumption |
| API-key revocation | No complete token invalidation timing/replacement contract published | Stop new dispatch on revocation/auth loss; do not automatically reacquire after intentional disconnect |
| openId lifecycle | Exchange account identifier and webhook key; refresh omits it | Preserve saved exact string until an authorized account read confirms identity; token refresh is not signing-key rotation |
| openId reset/rotation/account reset | No independent documented procedure | Compromise: disable event effects and new dispatch, quarantine, seek CJ remediation; never try account reset experimentally |

Sources: [Authentication](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [Settings](https://developers.cjdropshipping.com/en/api/api2/api/setting.html), [Shop](https://developers.cjdropshipping.com/en/api/api2/api/shop.html), [signing-key security](https://developers.cjdropshipping.com/en/api/start/webhook.html).

## Proposed deterministic lifecycle (not implemented)

1. Before secret resolution, verify actor/service principal → merchant → store → connection → allowed environment and capability. Bind the connection to an immutable credential lineage. Never expose raw account identifiers to UNDX/mobile.
2. Store the token bundle encrypted with a version and returned expiry metadata. Preserve openId losslessly, never through an IEEE-754 number. Reject unexpected account identity on reconnect; do not replace the account on active fulfillments.
3. Let `deadline = access_expiry - safety_window`. The configured safety window covers clock skew and operation duration; it is a local operational choice, not a provider TTL. Before a long operation require validity through its maximum allowed completion window.
4. If refresh is needed, acquire a lock on trusted internal account identity, not a client key or public openId. Re-read the bundle after locking. One worker calls refresh; others wait or defer within their deadlines.
5. Validate the complete response and commit the new pair/expiry atomically by compare-and-swap on bundle version. An old worker cannot overwrite a newer pair. Keep no stale queued copy of a token.
6. A cached response that does not extend safe validity causes deferral/manual attention, not a loop and not logout to force rotation. An ambiguous refresh response is not proof of rotation; recover through the approved lifecycle without issuing supplier writes.
7. A safe read failing authentication may trigger one coordinated refresh and bounded retry. A write failing or timing out may already have succeeded: reconcile the original operation first. Token refresh never makes an order/payment replay safe.
8. Invalid refresh requires an authorized reconnect. Explicit local disconnect increments connection generation and blocks queued jobs; it must not silently erase existing obligations or authorize continued API use after permission withdrawal.
9. CJ-assisted provider revoke is separate from local disconnect. Confirm shared-account blast radius, then revoke only with explicit authority. Existing obligations become manual recovery when credentials can no longer lawfully be used.

## Account-switch and two-merchant review

Merchant A cannot resolve B's connection, even when a guessed shop/order ID exists in CJ. A queued job carries trusted A/store/connection/version bindings and rechecks them on execution. Replacing credentials with a different openId creates a different connection, not an update to old fulfillment lineage. Same-account multi-store coordination is within one authorized owner only; same-account cross-merchant sharing is disabled. Provider authentication establishes a CJ account, never PulseSoc tenant membership.

## Required future tests

Test offset timestamps, missing/invalid expiries, a token expiring inside the operation window, cached refresh, two concurrent refreshers, stale compare-and-swap, explicit disconnect while queued, invalid refresh, ambiguous refresh, changed account identity, a 20-digit openId and a write succeeding before an auth/transport error. These are acceptance cases; no auth runtime PASS is claimed here.

CJ answers required: A1–A5 in [provider questions](CJ_PROVIDER_QUESTIONS.md). [Gate matrix](CJ_CONTRACT_GATE_MATRIX.md) records why lifecycle fallback is closed while hosted access remains blocked.
