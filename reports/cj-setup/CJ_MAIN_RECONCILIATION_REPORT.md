# CJ main reconciliation report

## Staging continuation addendum — 2026-09-07

Current local main advanced to `b32cc2bed3d08611fe2efb8fc9c7f9addb1e0be8`
(`feat(commerce): addressable variants and supplier provenance on marketplace_listings`).
This commits the concurrent Marketplace work described below. The acceptance
branch deliberately remains based on `edb3295e504ff6c37f27d4d7d01c7f99d382c300`;
no further merge was needed merely to deploy staging.

Read-only `git diff edb3295e main` confirms seven incoming paths:
`COMMERCE_DROPSHIPPING_FOUNDATION_MAP.md`, `bot.py`, `services/db.py`,
`services/marketplace_supplier_schema.py`, `services/marketplace_variants.py`,
`scripts/marketplace/supplier_variant_mutation_battery.py`, and
`tests/marketplace/test_supplier_variants.py`.
The incoming bot change is the Marketplace supplier-schema bootstrap hook;
the incoming db change is two auto-PK entries. Acceptance changes remove a raw
admin-password log and add DB-API cursor iteration, respectively, in separate
functions. Textual overlap is manageable, but the live listing/order authority
bridge still requires deliberate semantic reconciliation after live CJ catalog
acceptance. **NEEDS MANUAL RECONCILIATION** remains the import verdict.

All Marketplace commits and unrelated main untracked files remain untouched.
No main push, production deployment, or production configuration mutation was
performed. The newly authorized staging stack is in a separate Railway project,
not the pre-existing production project. See `CJ_STAGING_ACCEPTANCE_STATUS.md`
for current acceptance; the earlier inventory below is historical.

Final read-only main status also revealed a new, still-uncommitted ledger-authority
reconciliation in progress. Preserved without copying or staging:
`services/business_os/marketplace/schema.py`,
`services/business_os/suppliers/fulfillment.py`,
`services/business_os/suppliers/gateway.py`,
`services/business_os/suppliers/webhooks.py`,
`services/business_os/suppliers/worker.py`,
`services/marketplace_supplier_schema.py`, `services/marketplace_variants.py`,
`services/schema_guard.py`, `tests/business_os/test_cj_fulfillment.py`,
`tests/business_os/test_cj_gateway.py`, `tests/business_os/test_cj_webhooks.py`,
`tests/business_os/test_cj_worker.py`,
`tests/marketplace/test_supplier_ledger_authority.py`, and
`tests/marketplace_production_listings.py`. A future handoff must reconcile this
work explicitly; the staging deployment does not contain these changing files.

Observed 2026-09-07. This is current checkout/configuration evidence, not live CJ
or live production-database acceptance.

## Result

**SAFE TO MERGE the existing default-dark CJ foundation.** The user authorized
commit/push, and the foundation was merged locally without conflicts before the
new staging-only mission arrived. **NEEDS MANUAL RECONCILIATION for live
Marketplace import/order authority and the concurrent supplier work.** Rebase is
unnecessary; original commits remain reachable.

| Reference | SHA |
| --- | --- |
| CJ original base | `9b02f28f634c365760012ad470dcd906cc42bc3f` |
| CJ implementation | `f97cc2a11d006821c71f287da3ef004e4f615247` |
| CJ branch tip | `397c206e80c70ce18852abf055e10f1fa13a9188` |
| Main before integration | `60bc9d589316db07953ba29db0520f30107dbe74` |
| Local main merge | `edb3295e504ff6c37f27d4d7d01c7f99d382c300` |
| Remote main, verified with ls-remote | `9b02f28f634c365760012ad470dcd906cc42bc3f` |

The acceptance branch `codex/cj-staging-acceptance` was created at the merge in
`/private/tmp/cj-staging-acceptance.xXCEjH`, preserving concurrent changes in the
shared checkout. Its final report commit is recorded in the handoff.

## Git evidence and exact overlap

Executed status, branch inventory, porcelain worktree inventory, decorated
40-commit history, fetch, merge-base, per-side cherry comparison, file/diff
inspection and remote SHA lookup. No force operations, stash, cleanup, or broad
staging were used.

Pre-merge common ancestor was the CJ original base. Main's two unique commits
`0db35ce07ff71cc9eb6593d0f94388db0b519410` and
`60bc9d589316db07953ba29db0520f30107dbe74` changed only
`COMMERCE_DROPSHIPPING_FOUNDATION_MAP.md`. Neither touched the 31 CJ paths.
The two CJ commits were unique on the other side. File overlap: **zero** between
those committed changes. The merge preserved both documents and implementations.

CJ changes to `bot.py` were limited to route registration and two narrowly
scoped credential/raw-webhook-body privacy exclusions. All shared rate limits
remain in place. CJ's additive schema bootstrap is in
`services/business_os/schema_bootstrap.py`. No native or RTC change was merged.

## Semantic conflict: declared versus live commerce authority

The CJ foundation binds `business_os_mkt_products` in
`services/business_os/suppliers/gateway.py` and uses
`services/business_os/marketplace/orders.py` for `business_os_mkt_orders`.
This is real executable code, not merely a proposed contract.

The newer main commerce map reports a production census where the live products
and orders are instead `marketplace_listings` and `seller_transactions`; the
Business OS product/order ledgers are enabled but empty. The current code also
contains separate `/api/pulse/marketplace/*` and Business OS paths. The census is
evidence supplied by that committed report, **not a production database query
repeated by this mission**.

Consequently the earlier CJ report's use of “canonical” must not be read as proof
that imported CJ products or fulfillment intents are wired to the currently
shipping Marketplace. Preserve the supplier adapter/vault/outbox, but reconcile
the import bridge against the live listing authority before activation. Do not
seed a second product ledger merely to make acceptance tests pass.

## Concurrent, uncommitted work observed and preserved

The shared main checkout changed while this task was testing:

- `bot.py`: supplier-schema bootstrap hook in existing Marketplace initialization.
- `services/db.py`: auto-PK registration for supplier-source/variant tables.
- `services/marketplace_supplier_schema.py`: new variant/provenance schema owned
  beside `marketplace_listings`.
- `services/marketplace_variants.py`: new ownership-checked variant/provenance
  layer, integer private supplier cost, unknown stock, merchant override support.
- `tests/marketplace/test_supplier_variants.py`: accompanying tests appeared.

These files are active work, **not part of this merge or acceptance branch**.
They were inspected read-only; no content was copied, staged, committed or
overwritten. Existing `.scratch_locks/`, `CAPITAL_INTEGRITY_HANDOFF.md`, and
`tests/test_pulsesoc_call_livekit_grants.py` were also preserved.

The evolving source schema currently uses provider/product/seller uniqueness;
the requested import identity additionally requires connection and store. Its
variant identity is option-derived, while CJ mappings must retain exact vid.
The normalized CJ product DTO does not yet provide the full required import
media/description/category/brand/dimension contract. These are concrete bridge
requirements, not permission to create a parallel import system. Implement only
after the live normalization prerequisite and a stable concurrent-work handoff.

## Verification

- Merged main: 308 targeted backend tests passed; native `npm run typecheck`
  passed. No mobile files changed.
- Clean acceptance worktree at the exact merge: the same 308 tests passed
  (271 CJ plus 37 commerce/store/marketplace/bootstrap/inbox tests).
- Eight existing runtime security mutants: 8/8 killed with passing baselines
  and actual assertion failures, not setup/collection errors.
- No full-repository, PostgreSQL, live CJ, deployment or device PASS claimed.

## Push/deployment boundary

The newly supplied mission permits **staging-only deployment**. Railway's
current project has only `production`; its public `CoinPilotX` service and
multiple workers are sourced from this repository's `main`. The public service
owns `pulsesoc.com`, `www.pulsesoc.com`, and `coinpilotx.app`. A main push must be
treated as potentially production-deploying until its deployment behavior is
explicitly reconciled with that newer restriction. No production source/trigger
was changed to work around it. The local merge is committed; **no push occurred**.
