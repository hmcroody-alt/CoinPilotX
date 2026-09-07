# CJ implementation foundation map

Baseline inspected 2026-09-07. Branch `feat/cj-supplier-gateway`, base
`9b02f28f634c365760012ad470dcd906cc42bc3f`. The shared `main` checkout had
unrelated `.scratch_locks/`, `CAPITAL_INTEGRITY_HANDOFF.md`, and
`tests/test_pulsesoc_call_livekit_grants.py`. Implementation is isolated in a
linked worktree; those files are untouched. No push is authorized.

## Existing authorities reused

| Concern | Existing authority | Supplier integration boundary |
| --- | --- | --- |
| Merchant identity and membership | `services/business_os/business/service.py` effective role and owner | No supplier-owned membership table; business/store permission before provider access |
| Store identity | `services/business_os/store/schema.py` one `storefront_id` per business | Validate exact canonical storefront, never CJ storeName |
| Store authorization/audit | `services/business_os/store/service.py` store.read/manage and append-only store audit | Supplier mutations reuse authorization; audit never receives secret values |
| Customer orders/refunds | `services/business_os/orders/service.py` delegates to marketplace orders/refunds | CJ order IDs are fulfillment references, never canonical order/payment state |
| Payments and ledger | `services/business_os/payments/`, canonical ledger | No customer charge, balance transfer, refund, or supplier funding side effects |
| HTTP commerce routing | `commerce_gateway.py` and `business_os_commerce_routes.py` | Separate supplier route pack follows same canonical auth/CSRF convention; no second checkout |
| Shared request guards | `bot.py` security/abuse middleware | Keep rate/size gates; CJ-only bodies bypass generic JSON caching and suspicious-body telemetry sampling, then use bounded route parsing/HMAC |
| Durable provider receipt | `payments/webhook_inbox.py` | Verified, sanitized CJ receipt metadata only; no openId or credentials in the general inbox |
| Database portability | `services/db.py` | Additive `ensure_schema`, SQLite fixtures and PostgreSQL-compatible SQL |
| Encryption precedent | `services/private_office/field_crypto.py` AES-GCM/keyring/AAD design | Dedicated supplier secret-reference table and separate key namespace; never route CJ secrets to Private Office/UNDX |
| Background jobs | Existing workers plus durable domain reconciliation conventions | Bounded supplier worker entry point, disabled by default; no feed/RTC worker changes |

## New domain, not a duplicate authority

The supplier gateway owns connection metadata, encrypted credentials, immutable
supplier snapshots, explicit product bindings, fulfillment intents/outbox, and
provider reconciliation state. A supplier product can produce an import draft;
it cannot publish or overwrite a merchant's retail title, price, or policy.
One canonical customer order can create at most one supplier intent globally,
even when the same merchant owns multiple stores/connections. Split allocations
and replacement orders require a later explicit revision contract.

Marketplace customer-order items currently reference marketplace product IDs,
not `storefront_id`. An explicitly authorized supplier-product binding joins an
owned canonical product to one business/store/CJ connection. A matching seller
ID alone does not establish the missing store binding.

## Existing limitations preserved honestly

The current provider inbox's processing claim is not a distributed external-write
lease. It is reused for durable verified receipt only. Supplier external writes
use their own conditional claim and never resend an ambiguous create.

No existing runtime reads `CJ_API_KEY`; the prior secret report describes provider
JSON fields, not Railway environment variables. This implementation does not
introduce a platform-wide merchant credential. Railway may hold the supplier
vault encryption keyring; individual CJ API keys belong in encrypted,
merchant-scoped secret records.

The CJ hosted/delegated credential, three-accounts-per-IP, subscription replacement,
and funding amount-lock contracts are not proven by implementation. Network access
stays default-off, production fulfillment and funding are refused, automatic
subscription mutation stays gated, and synthetic sandbox tests are not live CJ
sandbox acceptance.

All new numeric epoch/lease columns use DOUBLE PRECISION rather than PostgreSQL
single-precision REAL. Full additive supplier schemas are registered in existing
Business OS bootstrap; the standalone supplier worker remains default-disabled.

## Real-account boundary

The API app was installed in the user's CJ account before this implementation
mission. The Add API form is available. No entry/key is generated until an
approved secure ingestion path and an authorized merchant/store binding are
available. An unpushed worktree is not a deployed credential vault.
