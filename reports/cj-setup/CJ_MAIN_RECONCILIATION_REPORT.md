# CJ main reconciliation report

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
