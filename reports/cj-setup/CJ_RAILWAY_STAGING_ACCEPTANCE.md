# PULSESOC CJ STAGING ACCEPTANCE

> Provider-approval continuation: the authorized eight-question request is now
> verified in Sent mail. Approval has not been received and the gate remains OFF.
> See [CJ_LIVE_SANDBOX_ACCEPTANCE.md](CJ_LIVE_SANDBOX_ACCEPTANCE.md) for current
> evidence. Infrastructure evidence below remains historical to its stated run.

## FINAL VERDICT: PARTIAL

Observed 2026-09-07. Isolated Railway infrastructure, real PostgreSQL acceptance,
staging vault configuration and a reachable backend are proven. **Real CJ
auth/catalog/sandbox-order/webhook acceptance and the shipping Marketplace
import bridge remain incomplete.** No real merchant credential was acquired.

The previous staging-authorization blocker is closed. The existing
`CJ_HOSTED_CREDENTIALS_APPROVED` provider-approval guard remains OFF; no approval
or exception was fabricated to pass live tests.

## Reconciliation

- Current local main: `b32cc2bed3d08611fe2efb8fc9c7f9addb1e0be8`.
- Remote main rechecked with `git ls-remote`: `9b02f28f634c365760012ad470dcd906cc42bc3f`.
- CJ original base: `9b02f28f634c365760012ad470dcd906cc42bc3f`.
- Preserved implementation: `f97cc2a11d006821c71f287da3ef004e4f615247`.
- Local foundation merge: `edb3295e504ff6c37f27d4d7d01c7f99d382c300`.
- Acceptance branch: `codex/cj-staging-acceptance`, isolated worktree
  `/private/tmp/cj-staging-acceptance.xXCEjH`.
- **NEEDS MANUAL RECONCILIATION** for import: shipping commerce uses
  `marketplace_listings`/`seller_transactions`; the foundation's fixture path uses
  `business_os_mkt_*`. No second public ledger was seeded to claim integration.
- `b32cc2be` committed concurrent Marketplace supplier work during this mission.
  It remains untouched on main. No further merge merely to deploy, no main push,
  no production deployment. Exact overlap: `CJ_MAIN_RECONCILIATION_REPORT.md`.

## Railway staging

This is a **separate project**, not a duplicated production environment. Railway
service creation can affect other non-fork environments in the same project;
separate-project isolation avoids that risk. No production config/data was copied.

| Resource | Exact identity |
| --- | --- |
| Project | `pulsesoc-cj-staging` / `34d4cb5c-f3db-40bf-926e-2eaa80a91659` |
| Environment | `pulsesoc-cj-staging` / `3a3f2632-bfc1-4ef4-b95a-e99e278d0fc1` |
| Backend | `pulsesoc-staging-backend` / `db4ebbfb-68ea-42b0-8545-61dd075ac8b3` |
| PostgreSQL | `pulsesoc-staging-postgres` / `2c0900e4-1f72-46f5-98d5-77cf5407e5ac` |
| Worker | `pulsesoc-staging-supplier-worker` / `29ca7c4b-e6e9-406d-9237-96a5f007cf38` |
| Redis | Not created; supplier coordination uses PostgreSQL |
| Region/replicas | SFO, one replica each |
| Caps | Backend 1 vCPU/1 GB; worker 0.5 vCPU/0.5 GB; PostgreSQL 0.5 vCPU/0.5 GB |
| PostgreSQL image/version | Railway PostgreSQL SSL 18; live PostgreSQL 18.6 |

Backend:
<https://pulsesoc-staging-backend-pulsesoc-cj-staging.up.railway.app>

Verified health (HTTP 200, no production redirect):

- <https://pulsesoc-staging-backend-pulsesoc-cj-staging.up.railway.app/health/cj-staging>
- <https://pulsesoc-staging-backend-pulsesoc-cj-staging.up.railway.app/health/ready>

Backend deployed SHA: `e4f2b52e480babf676fcccf0e9e3456d16c13ed0`.
Successful deployment: `6276a4ee-44a7-4ce9-a118-9b5f11e3cdfc`.
**686 running application files match Git blob hashes: zero missing/different.**

Worker source SHA: `5ac0158be0c864ac06c4b31caf42840c7064ae26`.
**686 file hashes match.** The only application-file difference from the backend
is the operations helper retiring temporary public DB test access; supplier,
database and runtime code are identical. Its source label was aligned to those
verified bytes before redeployment. Restart evidence appears below.

Final worker deployment `bdecc3b9-341e-462c-ab37-1f51c7dd199b`: **SUCCESS**.
Restart/redeploy changed boot ID from `1c6a61cd6d4d416ab06970b795c84686` to
`2cc8cc6353b940a19a20fbaa525ed45f`. Four post-restart ticks committed with the
verified `5ac0158b` SHA; public supplier tables remained empty. Final backend
health saw a fresh heartbeat (6 seconds old), all route packs registered and
PostgreSQL/schema/vault checks true. Provider state remained correctly deferred.

Deployment repairs:

1. Removed generated admin password from a legacy startup log **before first
   database bootstrap**. Authenticated one-time retrieval remains unchanged.
2. Railway HTTP health probes initially redirected toward the production origin.
   A staging-only entry point now uses the fixed staging origin and checks only
   exact read-only health paths before redirection. All merchant routes retain
   authentication, CSRF and TLS handling. Production hooks/config were not changed.
3. Canceled two incomplete mission-owned source uploads after confirming no build
   existed. A retry succeeded. No production fallback was used.

## Real PostgreSQL acceptance: PASS

The full backend bootstrap and explicit supplier/supporting schema ensures ran
on the new instance and reported PASS. Indexes and uniqueness were checked.
Remote test connectivity used TLS. Both application services use verified
references to the staging database's **private** Railway URL, not production.

`tests/staging/test_cj_postgres.py` asserts PostgreSQL and READ COMMITTED isolation;
there is no SQLite fallback. Each case creates an exclusively owned random
`cj_acceptance_*` schema. **Provider HTTP is forbidden: real DB, synthetic CJ.**

**32/32 passed twice:** local-to-staging TLS in 558.86 seconds; inside the Railway
supplier container over private networking in 26.63 seconds.

Coverage: additive/idempotent schema/indexes; encrypted references; tenant access;
concurrent account claims; refresh lease/version/identity fencing; key rotation
and stable account index; quota admission and 429 deadlines; catalog singleflight;
immutable unique intents; concurrent outbox claims; sandbox assertion; UNKNOWN
after timeout/provider success; no blind retry; expired SENDING recovery; exact
readback; canonical-order separation; webhook dedup/conflicts and readback
scheduling; bounded worker processing and persisted synchronization.

Engine-specific defect fixed: `CompatCursor` did not implement SQLite-compatible
cursor iteration. Supplier `for row in conn.execute(...)` reads failed on
PostgreSQL. The small shared-wrapper change preserves named/indexed rows and
consumption; a focused cursor test and 48 SQL/savepoint regressions pass.

Three synthetic schemas left by the interrupted first harness were removed by
exact inspected names. No public/customer schema was removed. Improved teardown
closes retained connections and bounds SQL/lock waits. Remaining test schemas: 0.
Temporary public PostgreSQL TCP proxy removed; verified proxy list empty.
The helper now supports only private-network `pgremote` acceptance.

## Vault / worker

- Staging keyring, active ID and stable account-index key were generated in memory
  and submitted through Railway stdin/API variables. No raw values in chat,
  prompts, source, screenshots, report or log output.
- Backend/worker keyrings match. No global CJ_API_KEY or production provider key.
- An in-container probe encrypted/decrypted synthetic credentials with the actual
  staging vault; added a process-local rotation key; verified old-key reads,
  new-key writes and unchanged account fingerprint. Persisted Railway keyring
  and index key were **not rotated** by that probe.
- Dedicated command: `python cj_staging_runtime.py worker`, wrapping the existing
  bounded `supplier_worker.run_tick`, independent of all production workers.
- PostgreSQL heartbeat persists boot ID, sequence, state, timestamp and SHA.
  Multiple 60-second ticks committed; early deferred ticks took about 11 ms.
- Current provider tick status is **deferred**, not CJ healthy. The approval guard
  is closed. Process/database health is not mislabeled as provider acceptance.

| Staging flag | Value |
| --- | --- |
| BUSINESS_OS_SUPPLIERS_CJ | ON |
| CJ_NETWORK_ENABLED | ON, still blocked by provider-approval guard |
| CJ_RECONCILIATION_ENABLED | ON, still blocked by provider-approval guard |
| CJ_ENVIRONMENT_MODE | SANDBOX |
| PRODUCTION_CJ_FULFILLMENT_ENABLED | OFF |
| REAL_CJ_FUNDING_ENABLED | OFF |
| CJ_SUBSCRIPTION_MUTATIONS_ENABLED | OFF |
| CJ_HOSTED_CREDENTIALS_APPROVED | OFF; no unsupported approval claim |

## CJ / sandbox / webhook / quota

| Gate | Result |
| --- | --- |
| Real API entry / merchant-store mapping / ingestion | Not created; no production identity copied |
| Access token / refresh / settings / shops / connectionHealth | NOT RUN against CJ |
| Catalog search/detail/variants/inventory/warehouses | NOT RUN against CJ |
| Freight, units, currency, restrictions | Fixtures only; no live quote |
| Sandbox order | NOT CREATED |
| isSandbox=1 | Caller + adapter tests pass; no live order proof |
| Real balance deducted | $0 caused by this task; no before/after balance measurement claimed |
| Real shipment / production order | 0 created |
| Unknown-write recovery | PostgreSQL + synthetic-provider PASS; live CJ NOT RUN |
| Webhook route | Deployed; unknown connection/invalid signature probe returns 403 |
| Merchant callback URL | Not configured; no connection-specific URL invented |
| Actual delivery/ACK timing | NOT RUN; no unsupported sandbox-delivery limitation claim |
| Raw-body signature/dedup/conflict/readback | PostgreSQL fixture PASS, not actual CJ delivery |
| Points / live QPS / Retry-After | NOT OBSERVED; quota not intentionally exhausted |

Route shape: `/api/provider-webhooks/suppliers/cj/<connection_id>`. No real
merchant connection exists. Public staging supplier connection, vault, intent
and outbox tables were queried: **all 0**. Fixture rows used disposable schemas.

[CJ's sandbox contract](https://developers.cjdropshipping.com/en/api/start/sandbox.html)
supports a future isSandbox=1 test with no real balance/fulfillment, but does not
prove a test was executed.

## Egress / custody gate

Observed outbound IP: backend `152.55.176.240`; worker `152.55.177.192`.
Configured backend pool: `162.220.232.251`, `152.55.176.240`, `152.55.177.193`.
Worker pool: `162.220.232.251`, `152.55.176.240`, `152.55.177.192`.
Both use conservative shared CJ_EGRESS_GROUP `pulsesoc-isolated-staging-sfo-pool`.
No proxy rotation or limit evasion. Railway says these IPs may be shared with
other customers: [official caveats](https://docs.railway.com/networking/static-outbound-ips).
Exclusive egress/three-users-per-IP scaling remains
**OPEN_RELEASE_BLOCKING_FOR_MULTI_MERCHANT_SCALE**, not by itself a single-merchant
sandbox blocker.

Separately, the binding implementation's provider-approval guard cannot be
truthfully enabled solely because infrastructure works. CJ's
[webhook security notice](https://developers.cjdropshipping.com/en/api/start/webhook.html)
warns against openId-containing material being shared with integration providers
and ERP plugins; it does not supply explicit hosted-custody approval here.
No broader contractual prohibition or approval is invented. The prepared
`CJ_SUPPORT_REQUEST_DRAFT.md` asks CJ to distinguish this one authorized hosted
sandbox merchant from later multi-merchant scale. **Not sent**; permission and
an approved recipient/channel are needed.

## Import / Marketplace

Shipping Marketplace search/import/draft/media/variants/inventory/pricing/shipping/
fulfillment mapping/duplicate prevention: **NOT CONNECTED / NOT ACCEPTED** by this
phase. The user requires live catalog normalization first. `b32cc2be`'s supplier
work is preserved, not blindly merged. Existing CJ snapshot/draft fixture tests
are not represented as live `marketplace_listings` integration.

No auto-publication, client-trusted supplier costs, public CJ calls, merchant
override changes or second public ledger. Import-specific mutation/media fallback
acceptance has not run.

## Security / regression

- Live unauthenticated connection read: 401; connect POST: 401.
- Live invalid webhook/unknown connection: 403. No production redirects.
- Authenticated merchant-A/B route isolation: local fixtures only; no real
  connected staging merchants to claim the live gate complete.
- In-memory scan of 1,457 backend log lines and current worker logs found no
  configured vault/session/DB secret matches, no plaintext bootstrap password
  marker, and neither submitted synthetic secret/body canary.
- Sentry DSN absent; no external Sentry proof claimed. UNDX runtime flags observed
  OFF; no CJ body sent to UNDX.
- Targeted CJ + commerce/store/marketplace/bootstrap/inbox/staging tests:
  **311 passed**, 30.12 s.
- SQL translation/savepoint/DB introspection/portability: **48 passed**, 13.58 s.
- Existing mutations: **8/8 killed**, passing baselines and assertion failures,
  rerun after cursor fix. Not setup errors.
- Real staging PostgreSQL: **32 passed twice**, separately from local regression.
- No full-repository, live-provider, physical-device or import PASS claimed.

Reproduce from the isolated worktree:

```sh
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/cj_staging_railway.py verify
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/cj_staging_railway.py pgremote
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/verify_cj_security_mutations.py
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/verify_cj_staging_source.py backend e4f2b52e480babf676fcccf0e9e3456d16c13ed0
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/verify_cj_staging_source.py worker 5ac0158be0c864ac06c4b31caf42840c7064ae26
```

## Billing / production / App Store / RTC

Recurring resources: **one backend, one supplier worker, one PostgreSQL/volume**.
No Redis, duplicate DB, unnecessary worker, CJ plan/points/inventory/payment.
Temporary TCP access removed; only mission-owned test schemas/incomplete uploads
cleaned up. No pre-existing resource deleted.

Early idle memory: ~317 MB backend, 43 MB worker, 207 MB PostgreSQL; ~0.002 total
vCPU. A rough usage extrapolation is **$6–10/month incremental**, not an invoice
or guarantee. Resource caps permit ~ $60/month compute at continuous saturation,
plus volume/egress. Existing account minimum/included credits apply. Observed
volume usage ~98 MB, allocated limit 50 GB (not claimed as actual consumption).
[Published rates](https://docs.railway.com/pricing/plans): RAM $10/GB-month, CPU
$20/vCPU-month, storage $0.15/GB-month, egress $0.05/GB.

Production changed/deployed/main pushed: **NO**. CJ fulfillment activation: **NO**.
Real supplier funding: **$0**. Real production orders: **0**.
New iOS/App Store build/upload: **NO**. Agora/audio/calls/live/camera/mic changes: **0**.

## Exact acceptance-phase paths

`bot.py` (password log only); `services/db.py` (cursor iteration only);
`cj_staging_backend.py`; `cj_staging_runtime.py`;
`scripts/cj_staging_railway.py`; `scripts/verify_cj_postgres_acceptance.py`;
`scripts/verify_cj_staging_source.py`; `tests/staging/test_cj_postgres.py`;
`tests/test_staging_boot_secret_hygiene.py`;
`reports/cj-setup/CJ_MAIN_RECONCILIATION_REPORT.md`;
`reports/cj-setup/CJ_STAGING_ACCEPTANCE_STATUS.md`;
`reports/cj-setup/CJ_RAILWAY_STAGING_ACCEPTANCE.md`;
`reports/cj-setup/CJ_SUPPORT_REQUEST_DRAFT.md` (prior acceptance phase, not sent).

## Commits

- `62799e723c4b0368f47694342571d2f1781d6207` — docs(cj): record staging inventory and marketplace reconciliation gates
- `a8a3bae8bfb33b652a3f68bb1c2d2d8ca8353555` — feat(cj): provision fenced staging runtime and PostgreSQL acceptance
- `6890c693ca1e24c5551488dafa94b99fff3e2f5f` — fix(cj): isolate staging origins and truthful Railway health probes
- `e4f2b52e480babf676fcccf0e9e3456d16c13ed0` — fix(db): support PostgreSQL cursor iteration and bounded supplier acceptance
- `5ac0158be0c864ac06c4b31caf42840c7064ae26` — docs(cj): retire public DB test access and reconcile new marketplace commit

Final report/source-verifier commit SHA is in the handoff; a report cannot contain
its own hash. No acceptance-phase commit was pushed.

## Genuine blockers / next action

1. Resolve the existing hosted-key/openId approval gate for one authorized sandbox
   merchant, or obtain explicit direction on an approved first-party test model.
   Next action: authorize the prepared CJ support request; do not send silently.
2. Select/map the real authorized merchant/store, ingest securely, then complete
   live auth/catalog/shipping/order/webhook/quota acceptance. Infrastructure is
   no longer the missing prerequisite.
3. After live catalog passes, reconcile with the shipping Marketplace import
   authority and finish draft/media/override/duplicate/security/mutation gates.
4. Shared egress/three-users-per-IP and broader custody remain production-scale
   blockers. No contractual approval claimed.

Printful/Printify not started: CJ staging + live sandbox + import has not met PASS.
