# PulseSoc CJ setup mission — implementation and acceptance

## Verdict: PARTIAL

The backend foundation is implemented and locally tested. It is not deployed,
connected to a real merchant, or accepted against CJ's live sandbox. No real CJ
credentials were generated/read/copied/stored during implementation. Do not
interpret fixture results as provider, Railway, PostgreSQL-runtime, or production
readiness proof.

## Baseline and isolation

- Branch: `feat/cj-supplier-gateway`.
- Base SHA: `9b02f28f634c365760012ad470dcd906cc42bc3f`.
- Worktree: `/private/tmp/cj-supplier-gateway.ZHIykF`.
- Shared `main` contained unrelated untracked work and was not edited. It
  independently advanced to `60bc9d589316db07953ba29db0520f30107dbe74` during
  this mission; those commits were not merged, reverted, or pushed by this task.
- Final implementation/evidence commit SHAs are in the final handoff. This
  document cannot contain its own commit SHA without a self-reference cycle.
- Implementation commit: `f97cc2a11d006821c71f287da3ef004e4f615247` —
  `feat(cj): add merchant-scoped sandbox supplier gateway`.
- No push, deployment, purchase, real order, supplier funding, mobile build,
  or RTC change was performed. No worktree was removed.

## CJ account

API app: observed installed in the preceding authorized Chrome workflow. The
Add API form was reached. API entry created: **NO**. API key securely stored:
**NO**. The one-time-secret boundary was respected because this new vault is
not deployed and a live authorized merchant/business/store mapping has not been
confirmed. No platform-wide `CJ_API_KEY` was added to Railway.

API payment required: **NONE for the documented starting API access**; quotas
apply. The current official points page gives a base pool, bounded endpoint
costs, response quota metadata, replenishment, and inactivity suspension. No API
plan, points, samples, balance, or inventory was purchased. [CJ points rules](https://developers.cjdropshipping.com/en/api/api2/standard/points.html),
[CJ API-key onboarding](https://developers.cjdropshipping.com/en/api/api2/api/auth.html).

## Implemented capabilities

| Area | Implemented contract | Current acceptance |
| --- | --- | --- |
| Supplier gateway | One CJ HTTP owner; scoped reads, immutable snapshots, explicit canonical product links, merchant-only import drafts | Local PASS |
| Connections | Merchant-owned API_KEY connection, canonical store/RBAC authority, verified account and active owned shop, cross-owner account claim denial | Local PASS |
| Credential authority | AES-256-GCM secret-reference vault; tenant-bound AAD; separate stable HMAC account index; keyring rotation; no plaintext fallback | Local PASS; operator secrets not provisioned |
| Auth | getAccessToken, metadata-driven expiry, refresh lease/version fence, preserve omitted refresh openId but reject changed identity | Local PASS; no live exchange |
| Health | Reachability/account/shop verification, expiry freshness, reauthentication, reactivation, provider unavailable, rate and quota state | Local PASS; not based on stored-key existence |
| Catalog | Categories, bounded List V2 search/filter/page, product details, exact pid/vid/SKU, variants and lookup | Local PASS |
| Inventory | Variant/country/warehouse separation, verified stock, UNKNOWN distinct from zero/in-stock; selected-stock recheck | Local PASS |
| Warehouses | Bounded normalized warehouse lookup | Local PASS |
| Shipping | FreightCalculateTip DTO and separate fee components/provider total, estimates not guarantees; immutable quote binds exact items and destination | Local PASS |
| Fulfillment | Immutable canonical-order intent plus fenced outbox, createOrderV2 payType=3/orderFlow=1, exact active API shop name resolved from owned shop ID | Local PASS; real sandbox untested |
| Sandbox | Caller and adapter separately refuse missing/non-integer isSandbox=1; non-SANDBOX/production activation refused | Local PASS |
| Unknown writes | Timeout/crash becomes UNKNOWN; independent order-reference readback validates shop, flag and exact lines; never infer absence from one not-found | Local PASS |
| Duplicate orders | Global canonical-order uniqueness plus idempotency/hash conflict checks, concurrent claims and expired SENDING leases | Local PASS |
| Supplier readback | Linked order status/tracking remains separate from canonical customer order/payment state | Local PASS |
| Funding | Balance read; explicit staged funding vocabulary; addCart/addCartConfirm/parent-order preparation/payBalance remain locked stubs | Safety PASS; staged writes not enabled |
| Webhooks | Exact raw-body HMAC-SHA256/Base64; official known-answer fixture; opaque connection route; tenant/resource checks; durable dedup/conflict handling | Local PASS; CJ delivery untested |
| Subscriptions | Bounded selected-shop list; subscribe/unsubscribe/configuration interfaces deny mutations pending contract/approval | Read foundation PASS; mutations gated |
| Reconciliation | Default-disabled bounded durable scheduler: health/tokens/shops/products/inventory/orders/tracking/subscription reads; selected-resource backfill; safe lag/backlog telemetry | Local PASS; no deployed scheduler |
| Point/IP limits | Shared database admission, configured fixed-egress group, maximum three accounts, conservative 1 QPS/account and 10 QPS/group, no proxy rotation | Local PASS; real fixed-egress topology unverified |
| Backoff | Full valid Retry-After preserved, bounded fallback, zero immediate retries, 429 points metadata retained, reserved fulfillment capacity | Local PASS |

CJ create writes target only the fixed allowlisted API host and include the
explicit sandbox flag. The public contract documents storeName for create V2;
it is resolved from the authenticated selected API shop, never used as local
tenant authority. Independent readback must actually expose matching shop,
sandbox, and product identity or the order remains UNKNOWN. [Shopping API](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html),
[sandbox contract](https://developers.cjdropshipping.com/en/api/start/sandbox.html).

## Routes and worker

All supplier routes are dark by default behind `BUSINESS_OS_SUPPLIERS_CJ`.
Cookie-authenticated POSTs require the existing CSRF header; authentication and
store ownership are checked before credentials/cache/provider access.

- `POST /api/business-os/suppliers/cj/connect`
- `POST /api/business-os/suppliers/cj/discover-shops`
- `GET /api/business-os/suppliers/cj/connections`
- `GET /api/business-os/suppliers/cj/connections/<connection_id>`
- `POST /api/business-os/suppliers/cj/connections/<connection_id>/<action>`:
  health, bind-product, import-drafts, fulfillment-intents; subscription writes
  remain gated.
- `POST /api/business-os/suppliers/cj/connections/<connection_id>/read/<operation>`:
  categories, search, product, variants, inventory, warehouses, shipping,
  subscriptions, balance. No arbitrary HTTP/path proxy.
- `GET /api/business-os/suppliers/cj/connections/<connection_id>/fulfillments/<intent_id>`
- `POST /api/provider-webhooks/suppliers/cj/<connection_id>`
- `GET /api/admin/business-os/suppliers/cj/health`: canonical admin permission;
  aggregate health only.
- `python supplier_worker.py --once`: bounded tick, disabled unless explicitly
  enabled. Without `--once`, the dedicated process uses a bounded interval.
  No existing RTC/media/alert worker or deployment configuration was modified.

## Security evidence and limits

Cross-merchant/store reads, tokens, shops, snapshots, order IDs and subscriptions
are denied. Ownership is rechecked after provider reads and before local writes.
An ownership transfer does not transfer another merchant's credentials.

Public DTOs are explicit allowlists. Internal secret bundles refuse ordinary JSON
serialization, and route responses reject dataclasses/non-JSON objects so Flask
cannot implicitly expand an AuthBundle. Provider credential echoes are rejected
before snapshots/cache/output. No supplier module logs request/response bodies or
imports a Sentry/analytics/UNDX sink. Shared bot middleware now exempts **only CJ
bodies** from generic JSON caching and suspicious-body sample logging; shared
rate limits remain intact. Executable tests run those actual hook functions and
check both CJ privacy and unchanged non-CJ XSS behavior. No complete production
WSGI/proxy/telemetry pipeline was exercised.

HMAC verifies exact bytes before parse/persistence. Since openId is the signing
key and can be present in CJ pushes, the general inbox stores only authenticated
routing metadata and an exact-body SHA-256, **not reconstructible raw bodies**.
Callbacks schedule idempotent readback; they cannot create/fund orders or mutate
canonical payment state. Duplicate message IDs with different bytes conflict.
[CJ webhook security contract](https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication).

The existing shared inbox processing lease is unsuitable for external financial
writes; it is reused only for scheduling. The new supplier outbox supplies the
external-write fencing. Once UNKNOWN, no automatic POST retry is implemented
because authoritative absence has not been proven. Split orders, replacement
intents, production funding, and automatic subscription mutations require later
explicit contracts. Quotes remain estimates, not payment amount-lock proof.

## Executed verification

From the isolated worktree, using the repository virtual environment:

```sh
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python -m pytest -q tests/business_os/test_cj_*.py
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python -m pytest -q tests/business_os/test_commerce_gateway.py tests/business_os/test_store_core.py tests/business_os/test_marketplace_core.py tests/business_os/test_ad_delivery_schema_bootstrap.py tests/business_os/test_ledger_and_webhook_inbox.py
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/verify_cj_security_mutations.py
```

- CJ suite: **271 passed** across adapter, transport, vault, auth, tenancy,
  real Flask route/CSRF boundaries, cache/snapshots, SQLite transactions,
  concurrent refresh/claims/dedup/quota, sandbox fixtures, webhook and worker.
- Existing commerce/store/marketplace/bootstrap/inbox regression subset:
  **37 passed**.
- Eight runtime mutants: **8/8 killed**, each with a passing unchanged-test
  baseline and actual assertion failures; setup/collection/runtime errors do
  not count. Seven required cases plus cryptographic AAD removal are included.
- Byte compilation of all changed Python modules and `bot.py`: PASS.
- `git diff --check`: PASS.
- PostgreSQL: SQL reviewed for portability and double-precision lease times;
  **no live PostgreSQL run**. Local PostgreSQL binaries unavailable and Docker
  daemon not running. No production database was queried for test data.
- No full-repository/native/device/deployment gate is claimed.

## Rollout gates — not performed

1. Obtain/record CJ approval for hosted delegated credential custody/webhooks and
   a legitimate fixed-IP/account plan. A boolean flag is not evidence of approval.
2. Review/reconcile this branch with current `main`, then explicitly authorize a
   backend deployment. Do not silently merge concurrent work or push this mission.
3. Through approved service-scoped secret provisioning, install
   `SUPPLIER_CREDENTIAL_KEYS`, `SUPPLIER_CREDENTIAL_KEY_ACTIVE`, and the separate
   stable `SUPPLIER_ACCOUNT_INDEX_KEY`. Preserve prior encryption keys until data
   rotation is proven; never blindly change the index key. Do not store merchant
   CJ keys as shared Railway environment variables.
4. Apply/verify additive schemas on staging PostgreSQL; test concurrency/restart
   semantics there. Configure one shared database and `CJ_EGRESS_GROUP` matching
   actual fixed egress. Default account pacing stays 1 QPS unless a later verified
   entitlement update is implemented; no assumed higher CJ tier.
5. Validate authenticated HTTPS connect ingestion and proxy/APM body-scrubbing,
   then identify the authorized merchant/business/store. Only then create the CJ
   API entry and transfer its one-time key directly into that approved vault
   flow, never chat, model prompts, Git, browser storage, or UNDX.
6. Enable the supplier/network/worker gates for an approved sandbox connection
   only; keep `CJ_ENVIRONMENT_MODE=SANDBOX` and production fulfillment OFF. Prove
   live auth/refresh/shops/catalog/quote, create-only/readback, timeout recovery,
   callback delivery, quotas and operator telemetry before declaring acceptance.
7. Keep funding and subscription mutations locked until their separate amount
   safety/replacement contracts are proven. This implementation does not call
   payBalanceV2 or the dedicated sandbox payment simulator.

Defaults: `BUSINESS_OS_SUPPLIERS_CJ`, `CJ_HOSTED_CREDENTIALS_APPROVED`,
`CJ_NETWORK_ENABLED`, and `CJ_RECONCILIATION_ENABLED` are OFF. Production
fulfillment and all real funding remain refused even if someone sets a flag.

## Final safety totals

Production CJ fulfillment enabled: **NO**. Real orders placed: **0**. Real
supplier funding: **$0**. Purchases: **0**. Mobile-native files/builds: **0**.
Agora/audio/call/live/camera/mic logic changes: **0**. Pushes: **0**.

Only genuine blockers remain: deployed secure ingestion plus live merchant
mapping; CJ hosted-custody/fixed-egress approval; PostgreSQL and real CJ sandbox
acceptance; explicit later funding/subscription safety proofs. There is no API
purchase-page blocker.

## Exact files changed

31 repository-relative paths, all in the isolated worktree named above:

```text
bot.py
reports/cj-setup/CJ_IMPLEMENTATION_FOUNDATION_MAP.md
reports/cj-setup/CJ_ROLLOUT_AND_ACCEPTANCE.md
reports/cj-setup/connection_evidence.md
scripts/verify_cj_security_mutations.py
services/business_os/schema_bootstrap.py
services/business_os/suppliers/__init__.py
services/business_os/suppliers/cj.py
services/business_os/suppliers/connections.py
services/business_os/suppliers/errors.py
services/business_os/suppliers/fulfillment.py
services/business_os/suppliers/gateway.py
services/business_os/suppliers/policy.py
services/business_os/suppliers/quota.py
services/business_os/suppliers/schema.py
services/business_os/suppliers/vault.py
services/business_os/suppliers/webhooks.py
services/business_os/suppliers/worker.py
services/business_os_supplier_routes.py
supplier_worker.py
tests/business_os/test_cj_adapter.py
tests/business_os/test_cj_connections.py
tests/business_os/test_cj_fulfillment.py
tests/business_os/test_cj_gateway.py
tests/business_os/test_cj_quota.py
tests/business_os/test_cj_request_boundary.py
tests/business_os/test_cj_routes.py
tests/business_os/test_cj_transport.py
tests/business_os/test_cj_vault.py
tests/business_os/test_cj_webhooks.py
tests/business_os/test_cj_worker.py
```
