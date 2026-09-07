# CJ secret requirements

Discovery snapshot: 2026-09-07. Documentation only; no credentials requested, read from local secret files, created, tested, deployed, or changed.

## Proven provider credentials

Names below are provider JSON fields, not new environment variables. The proposed storage layout is a design requirement, not an implemented store.

| Name | Purpose | Scope / ownership | Expiry and rotation | Proposed storage | Railway required? | Mobile / UNDX exposure |
| --- | --- | --- | --- | --- | --- | --- |
| `apiKey` | Exchange for tokens using POST `https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken` | Merchant-owned CJ API authorization; never a universal PulseSoc key under recommended model B | No fixed API-key expiry or documented programmatic key rotation in reviewed API. Merchant creates/replaces/revokes in CJ account; verify invalidation behavior with CJ | Encrypted credential reference attached to tenant-owned supplier connection; least-privileged connector worker only | Backend needs secret-store access eventually; not a separate Railway variable per merchant. Nothing needed for this mission | NEVER |
| `accessToken` | `CJ-Access-Token` header on authenticated business endpoints | Token's CJ account; shop selector is not a separate credential or wallet | Conflicting documentation: opening auth paragraph says 180 days; its field table, refresh section and token guide say 15 days. Read actual `accessTokenExpiryDate`, with clock skew; do not hard-code either | Encrypted at rest; short-lived restricted backend cache; redact HTTP headers and exception context | Available to backend workers through connection lookup, not mobile configuration | NEVER |
| `refreshToken` | POST `https://developers.cjdropshipping.com/api2.0/v1/authentication/refreshAccessToken` with JSON `refreshToken` | Same account/connection boundary; coordinate multiple connections sharing an account | Documented nominal 180 days, use actual `refreshTokenExpiryDate`. Store refreshed pair atomically. If invalid/expired, reconnect using authorized API-key exchange; do not keep retrying | Encrypted secret store; never browser storage, analytics, prompt context, trace baggage or queue payload | Backend secret-store access only | NEVER |
| `openId` | Account reference **and CJ webhook HMAC key** | Account-sensitive value returned by token exchange; also exposed in some CJ webhook payloads | No independent signing-key generation/rotation endpoint or expiry documented. Token refresh must not be assumed to rotate it. Compromise requires CJ-assisted remediation and webhook revalidation | Treat as a secret, preserve exact decimal string losslessly; encrypted with credential bundle. Use a keyed digest/internal identifier for limiter/cache grouping | Backend webhook verifier must retrieve stored account value securely; no standalone `CJ_WEBHOOK_SECRET` is documented | NEVER, including normalized account responses |
| `platformToken` (conditional, not MVP-required) | Optional create-order header documented by Shopping; may be empty when not required | Applicable platform authorization only; relationship to merchant credential must be confirmed with CJ | Shopping says obtained the same way as CJ access token but gives no independent platform delegation or expiry contract | If CJ requires it for an approved flow, encrypted backend credential reference with explicit scope; do not collect speculatively | No change for this mission; only needed later if account contract requires it | NEVER |

Sources: [Authentication](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [Get Token](https://developers.cjdropshipping.com/en/api/start/token.html), [Webhook mechanism: signature authentication](https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication).

Conditional header evidence: [Shopping, Create Order V2](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html). Neither the optional header nor global platform-token errors establish a usable OAuth onboarding contract.

## Exact authentication lifecycle

1. The merchant installs the CJ **API** app, then creates an API authorization with API Key Name, API Store Name and Type **API Key** in CJ's account UI. This is bring-your-own-key onboarding, not a proven OAuth consent redirect. No CJ login password needs to be collected by PulseSoc for this documented flow.
2. Token exchange sends JSON `{apiKey}` over HTTPS to the URL above. Store token pair, `openId`, `createDate`, `accessTokenExpiryDate`, and `refreshTokenExpiryDate`; timestamps are metadata, not secrets by themselves.
3. Refresh under a per-account distributed lock before expiry; atomically compare-and-swap the complete returned credential bundle. The documented 24-hour server cache can return the same pair for repeated **get and refresh** requests by one account. This is a cache behavior, not a 24-hour expiry or guaranteed rotation mechanism. If refreshed credentials still cannot cover the next safe operation window, defer and alert; never spin.
4. `POST https://developers.cjdropshipping.com/api2.0/v1/authentication/logout`, authenticated by `CJ-Access-Token`, invalidates both tokens. Do not invoke it during routine health checks. Impact on other API keys/shops/connections sharing that account needs CJ confirmation before offering global revoke from one store.
5. Disconnect in PulseSoc immediately stops dispatch and new webhooks for that connection locally. Provider revoke/logout is a distinct explicit operation with account-wide blast-radius checks. Previously created supplier obligations remain in a restricted reconciliation path, not erased.

The token guide uses the label “Auth2.0,” and global errors mention exchange codes and redirect/callback URIs. The reviewed public authentication page does **not** provide the authorization endpoint, client-registration contract, scopes or callback exchange sequence for a deployable delegated OAuth integration. This report does not assert that CJ has no partner authorization product. Obtain its documented contract if CJ offers one. [Token guide](https://developers.cjdropshipping.com/en/api/start/token.html), [global errors](https://developers.cjdropshipping.com/en/api/api2/standard/ps-code.html).

## Webhook verification and credential risk

Exact documented computation: standard Base64 (keep padding) of HMAC-SHA256 using UTF-8 string `openId` as key and the **unaltered raw request-body bytes** as message; compare to the `sign` HTTP header with constant-time comparison. It is not an API-key HMAC, not a bearer token, and not a separately issued webhook secret. Select the stored connection credential from trusted routing, never select a key supplied by the webhook body. Reject a mismatched body account reference after verification. Preserve numeric identifiers as strings to avoid JavaScript precision loss.

This design couples identity to signing authority: an account ID disclosed in a payload can permit forged signatures. An HMAC check alone is therefore insufficient for high-impact state transitions. Require bounded body sizes, durable inbox/deduplication, connection/order/product ownership checks, read-back from CJ before fulfillment or financial consequences, and a quarantine path. There is no documented timestamp-based replay window, independent secret rotation or authoritative sender IP list to invent.

CJ's security notice specifically cautions against disclosing `openId`, raw pushes and receiver URLs to third-party integration providers. A hosted PulseSoc multi-merchant integration requires written CJ clarification of authorized credential custody and webhook processing. Merchant consent alone must not be represented as proof of CJ's SaaS permission. [Official webhook security notice](https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication).

## Tenant and infrastructure boundary

- Every operation requires authenticated PulseSoc actor → authorized business/store → owned active supplier connection → valid provider authorization. Background jobs use an explicitly authorized service principal and the same tenant checks; no job may trust a raw connection ID.
- Secret-store key includes environment, merchant/business and connection identity. Cross-tenant joins require explicit ownership predicates. Shared-account rate/cache coordination must not share private catalog, prices, addresses, orders or tokens with another merchant.
- Opaque webhook route IDs should be random, redacted and connection-bound. The public endpoint address is operational configuration, not a CJ-issued secret; it is not a substitute for signature verification.
- Backend encryption/KMS credentials are platform infrastructure requirements, not additional CJ credentials. Reuse the existing approved secret system after a separate implementation review. Do not invent `CJ_CLIENT_SECRET`, a webhook signing password or merchant-specific Railway variables.
- Audit credential access by actor/connection/operation and key version without raw values. Redact provider request headers, token responses, `openId` in responses/events, addresses and bodies before logging or support export. Retain only the minimum encrypted event payload needed for recovery.
- Never put raw CJ secrets in mobile bundles, native config, public APIs, browser local storage, source control, model prompts, UNDX tool results, URLs or analytics. UNDX receives normalized facts and policy-limited action proposals; the gateway owns execution authorization.

## Sandbox separation

CJ documents an **order-level** `isSandbox=1` flag on the same API host, not a separate sandbox hostname/key family. Omitted flag is real order mode. Simulated orders do not deduct real balance or create real fulfillment; sandbox mutation endpoints only accept sandbox orders. This does not establish that every catalog, shop, webhook, sourcing or dispute mutation is sandbox-isolated. Use a dedicated approved test account/connection, immutable sandbox mode, a dispatch allowlist and explicit sandbox assertions in a later authorized test mission. No secret or test call is needed now. [Sandbox](https://developers.cjdropshipping.com/en/api/start/sandbox.html).

## Open credential questions for CJ

Resolve access-token TTL conflict; effective account-versus-API-key token/logout scope; API-key revoke/rotation behavior; safe `openId` rotation after exposure; supported hosted SaaS custody/delegation; webhook signing-key isolation; account creation/shop count limits and dedicated test-account setup. These are provider/account questions, not permission to request credentials before the contract is approved.
