# PulseSoc CJ staging acceptance + import integration

## FINAL VERDICT: PARTIAL — staging authorization boundary

The existing foundation was preserved, reconciled locally, and reverified.
There is no approved staging environment accessible in the inspected Railway
inventory. Creating new billable services or repurposing production is not an
ordinary deployment to an already-approved staging target. Live acceptance has
not begun, and the mission's “once live catalog normalization passes” import
implementation prerequisite has not been met.

## Reconciliation

Current local main: `edb3295e504ff6c37f27d4d7d01c7f99d382c300`.
CJ original base: `9b02f28f634c365760012ad470dcd906cc42bc3f`.
Result: safe local default-dark merge; live Marketplace bridge needs manual
reconciliation. Final acceptance branch: `codex/cj-staging-acceptance`.
Final report SHA is supplied in the handoff. Details and exact overlap are in
`CJ_MAIN_RECONCILIATION_REPORT.md`.

## Staging inventory — read-only Railway evidence

Commands: `railway list --json`, `railway environment list --json`,
`railway status --json`, and a source-only projection of
`railway environment config --environment production --json`. Variable values
were not displayed or saved. No configuration mutation was performed.

- Project: `coinpilotx-alert-worker`, ID
  `111b3838-09d4-4f13-8b8b-6ed332bad06f`.
- Only environment: `production`, ID
  `8bf01340-99d0-49be-a951-abffc17aa4d3`.
- Existing backend: `CoinPilotX`, ID
  `ce41f7c5-b882-4aa7-81b3-06de73fded31`, public production domains.
- Existing `Postgres` belongs to production. It was **not** used for acceptance,
  seeded, schema-modified, cloned, or queried for merchant/customer data.
- Other accessible projects also list only environments named production;
  none has been designated or verified as PulseSoc staging.
- No staging backend, PostgreSQL, supplier worker, private URL, or traffic
  isolation was established. Deployed staging SHA: **none**.
- Redis: the existing supplier path coordinates through `services.db`, not
  Redis. No Redis acceptance is claimed, and no unrelated Redis dependency was
  introduced. A full backend staging deployment still needs its own dependency
  inventory.

## Acceptance matrix

| Area | Current result |
| --- | --- |
| Vault keys/rotation | Existing local tests pass; no staging keys provisioned or rotated |
| API entry | Not created; one-time-secret boundary preserved |
| Merchant/business/store mapping | No live staging identity selected or copied from production |
| Credential ingestion | Not attempted; approved HTTPS staging vault unavailable |
| Secret exposure | No real CJ key/token/openId acquired or displayed |
| Live access token/refresh/settings/shops | NOT RUN |
| Live search/details/variants/inventory/warehouses | NOT RUN |
| Shipping/units/currency/restrictions | Fixture-verified only; live freight NOT RUN |
| Sandbox order/create/readback | NOT RUN; no provider order created |
| Unknown-write recovery | Existing runtime/SQLite proof retained; live-provider proof NOT RUN |
| PostgreSQL schema/indexes/concurrency/leases/restarts | NOT RUN; release-blocking until staging exists |
| Webhook endpoint | Existing `/api/provider-webhooks/suppliers/cj/<connection_id>`; not deployed/configured for staging |
| Live callback/signature/dedup/ACK timing | NOT RUN; local HMAC/inbox tests pass |
| Live quota/points/QPS/429 | NOT RUN; no quota intentionally consumed or exhausted |
| Fixed egress/CJ_EGRESS_GROUP | NOT VERIFIED or provisioned |
| Three-users/IP multi-merchant scale | OPEN_RELEASE_BLOCKING_FOR_MULTI_MERCHANT_SCALE |
| CJ hosted custody | No contractual approval claimed; support draft prepared, not sent |
| Import UI/search/import/draft/media | Existing CJ search primitives retained; live import bridge NOT IMPLEMENTED |
| Variant/cost/inventory/shipping mapping | Requires live-normalization proof and concurrent Marketplace foundation reconciliation |
| Duplicate import/merchant override/public leakage mutations | NOT RUN; no new import integration exists yet |
| Public Marketplace live CJ calls | None added |
| Marketplace media/search/discovery acceptance | NOT RUN; no public surface changed |
| Tenant/vault/logs/UNDX boundaries | Existing local tests and mutation probes pass; no live staging/Sentry pipeline inspected |

## Executed regression

Exact merge worktree:

```sh
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python -m pytest -q tests/business_os/test_cj_*.py tests/business_os/test_commerce_gateway.py tests/business_os/test_store_core.py tests/business_os/test_marketplace_core.py tests/business_os/test_ad_delivery_schema_bootstrap.py tests/business_os/test_ledger_and_webhook_inbox.py
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/verify_cj_security_mutations.py
```

308 passed. Existing 8/8 mutations killed. Native typecheck passed from
`/Users/hmcherie/Desktop/CoinPilotX/mobile-native` against the unchanged native
tree. These are targeted local gates, not the mission's broader live acceptance
or full repository PASS.

## Required staging approval and next safe execution

Approve an isolated Railway staging environment with a dedicated PostgreSQL,
backend, and supplier worker, or provide an already-approved non-production
target. Confirm permitted resource spend and an authorized staging merchant.
Do not duplicate production wholesale: that risks copying customer data,
production credentials, payment settings and scheduled jobs into staging.

Once approved, provision empty staging data and service-scoped vault keys through
secure subprocess/secret-store channels; keep all raw keys out of tool output,
screenshots, reports and model prompts. Verify HTTPS, worker, database constraints
and concurrency before generating the one-time merchant CJ key.

The existing `CJ_HOSTED_CREDENTIALS_APPROVED` network gate must not be silently
bypassed. The new mission distinguishes a single explicitly authorized sandbox
merchant from multi-merchant production scale; record a truthful, narrowly scoped
approval decision for that test, without claiming provider-wide SaaS approval.
Unresolved scale alone is not the reason work is paused: **no approved staging
target exists**. Do not use production to evade that prerequisite.

Only after live normalization succeeds should the accepted CJ DTO be connected
to the prepared live Marketplace draft/import authority. Preserve provider cost
privately, explicit connection/store identity, exact vid mapping, media
provenance, review-before-publication, idempotency and merchant override ownership.
Do not rebuild CJ or add another product/order ledger.

The prior main push is paused: production services are sourced from main and the
new mission restricts deployment to staging. No production deployment settings
were altered. Resolve that boundary explicitly before pushing main.

## Production, App Store and RTC

CJ production fulfillment activation by this task: **NO**.
Real supplier funding: **$0**. Real production or sandbox orders created: **0**.
Real CJ balance deductions caused by this task: **none**; no before/after balance
measurement is claimed. Real shipment creation: **none**.
Production flags, credentials and customer data: **unchanged by this task**.
App Store build/upload: **NO**. Agora/audio/calls/live/camera/mic changes: **0**.

## Exact files changed by this acceptance phase

- `reports/cj-setup/CJ_MAIN_RECONCILIATION_REPORT.md`
- `reports/cj-setup/CJ_STAGING_ACCEPTANCE_STATUS.md`
- `reports/cj-setup/CJ_SUPPORT_REQUEST_DRAFT.md`

The previously committed foundation was merged, not rebuilt. No active concurrent
Marketplace source or schema edit was included. Local commits and SHAs appear
in the handoff; reports cannot include their own final commit SHA.

## Remaining blockers

1. Approved, isolated staging infrastructure and permitted provisioning spend.
2. Authorized staging merchant/business/store mapping and secure ingestion.
3. Stable handoff/reconciliation of concurrent live Marketplace supplier work.
4. Live PostgreSQL, CJ sandbox and import acceptance after those prerequisites.
5. Explicit main-push versus production auto-deployment boundary.

Printful/Printify work is not started: CJ staging/import has not achieved PASS.
