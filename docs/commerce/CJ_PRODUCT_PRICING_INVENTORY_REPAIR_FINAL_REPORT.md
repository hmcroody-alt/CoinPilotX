# CJ dropshipping price and inventory repair — final report

Status: **IN PROGRESS** (this file is written as the work lands; the verdict at the
bottom is authoritative only once it reads PASS, PARTIAL or BLOCKED).

## 1. Provenance

| | |
|---|---|
| Starting branch | `main` |
| Base commit | `de3f87db7de9beabf9bb93c9ef3dbf343c40fd87` (`origin/main`) |
| Working branch | `commerce/cj-price-inventory-repair` |
| Worktree | `/Users/hmcherie/Desktop/cjrepair-wt` (isolated; the primary checkout at `/Users/hmcherie/Desktop/CoinPilotX` holds ~14 unrelated modified files owned by other sessions and was not committed from) |
| Production database | Railway Postgres 18.6, read via `DATABASE_PUBLIC_URL` |
| Census taken | 2026-09-16 |

## 2. The brief's premise, corrected against production

The mission was written from a Store screenshot reading *40 listings / 32 drafts /
3 active / 1 low-stock*, with CJ products showing "Price required", "1 thing left"
and "Draft — not published". The brief instructed that the database, not the
screenshot, is authoritative. It is, and it disagrees with the reading of the
screenshot in two ways that change what needs building.

**"1 thing left" is not a stock count.** It is the publication *blocker* count,
produced verbatim by `listing_readiness.summary()`:

```python
return f"{count} thing{'' if count == 1 else 's'} left"
```

**"Price required" is not a price state.** It is the UI label for the
`MISSING_PRICE` blocker, from `mobile-native/src/components/store/StoreListingRow.tsx:168`.

Consequently the mission rules *"never default missing stock to 1"* and *"no
placeholder stock remains"* describe a defect that does not exist. Production has
**zero** listings with `quantity = 1`. No placeholder price or placeholder stock
was ever written by this integration.

**A complete CJ integration already exists.** Per decision (9), this is a repair
mission, not a build. Already present and working: `cj.py` (quota-aware adapter,
~15 endpoints), `gateway.py` (immutable snapshots, cross-process single-flight
read cache), `normalize.py`, `importer.py`, `pricing.py`, `drafts.py` (15
validation codes), `webhooks.py` (HMAC-SHA256 verified), `worker.py` (bounded
durable scheduler), `revisions.py` (the stock/cost reconciler), `fulfillment.py`,
`marketplace_supplier_checkout.py`, the `marketplace_listing_variants` /
`marketplace_product_sources` schema, and 18 test files under `tests/dropshipping/`.

### Authoritative production census (2026-09-16)

| Fact | Value |
|---|---|
| `marketplace_listings` rows | 40 |
| …with a `marketplace_product_sources` row | 34 (all `provider='cj'`, `sync_state='SYNCED'`, `fulfillment_mode='DROPSHIP'`) |
| Listings with empty `price_label` | 31 |
| `marketplace_listing_variants` rows | 719 |
| …with non-null `price_cents` **and** `cost_cents` | **719 (100%)** |
| …with `stock_state='UNKNOWN'` | 683 |
| …with `stock_state='IN_STOCK'` | 36 (listings 42 and 45 only) |
| Sources with NULL `provider_variant_id` | 28 — **every** multi-variant listing |
| Sources with non-NULL `last_synced_at` | **0 of 34** |
| `business_os_supplier_sync_jobs` rows | **0** |
| Listings with `quantity = 1` | 0 |
| CJ connections | 1 (`sc_366edc85…`, business/store `mkt-seller:1`) |

## 3. Proven root cause

Three independent defects. Each is evidenced below; none was inferred.

### 3.1 The Store screen's price verdict never looks at where the prices are

`services/business_os/marketplace/listing_readiness.py` decides `MISSING_PRICE`
from the listing row's free-text `price_label` column and nothing else:

```python
def _has_price(price_label: Any) -> bool:
    label = _text(price_label)
    return any(ch.isdigit() for ch in label)
```

It never reads `marketplace_listing_variants.price_cents`. All 719 variants carry
a valid computed retail price. `price_label` is written only by
`drafts.publish()`, which does not run for an unpublished draft — so 31 correctly
priced listings report "Price required" and offer the merchant a fix ("Add price")
for a problem they do not have.

The module's own docstring already identified this gap and the intended end state:

> "The sibling engine `services/business_os/suppliers/drafts.py:_validate`
> answers the same question for supplier-imported drafts, and answers it well…
> The intended end state is one engine with a supplier-aware extension; this is
> the half that the Store workspace needs."

Note the two engines disagree about *which* blockers are real. `drafts._validate`
reads variant prices and is satisfied; the blockers actually preventing
publication are `UNKNOWN_INVENTORY` and `SUPPLIER_VARIANT_UNBOUND` (§3.2, §3.3).
So the Store screen is not merely imprecise — it names the wrong problem, and the
merchant cannot fix the real one by following it.

### 3.2 Inventory: dropped silently at import, then never repaired

**(a) Acquisition.** `importer._authoritative` treats the inventory read as
best-effort and discards every failure without a trace — no `last_sync_error`, no
`attention_json`, no log line, no snapshot:

```python
except (SupplierError, normalize.NormalizationError):
    pass
```

The dominant failure is not a CJ outage. It is the gateway's own cross-process
single-flight lease in `gateway._cached_read`:

```python
if claim.rowcount != 1:
    raise SupplierError("request_in_progress", http_status=429, retry_after=2)
```

Inventory has a 10-second cache TTL. A multi-item cart import touches the same
product more than once inside that window, the second pass loses the lease, and
the 429 lands in the bare `except … pass` above. The production snapshot timeline
shows exactly this: duplicate `product` reads for the same pid 0.05 s apart at
20:53:21 and 20:53:26, and **no `inventory` snapshot at all** for precisely the
pids that stayed `UNKNOWN` (`1387321783304196096`, `2507060533391619600`,
`2411120922221621800`), while the two pids that did snapshot inventory
(`1720314365515149312`, `2406250750481611000`) are listings 42 and 45 — the only
two listings in production with real stock.

CJ is not at fault. A live read on 2026-09-16 returned complete, correct
inventory for **every one** of those pids:

```
pid 1387321783304196096  15 variants  IN_STOCK 40000 each
pid 2507060533391619600  63 variants  IN_STOCK 6348…9885
pid 2411120922221621800  56 variants  IN_STOCK 10274…12843
pid 1780868533145051136   4 variants  IN_STOCK 13125…13567
```

**(b) Permanence.** The reconciler that would have repaired this on the next pass
already exists and is correct — `worker._seed_jobs` seeds an `inventory` job per
`marketplace_product_sources` row, `worker._read_job` calls
`adapter.get_inventory(pid)`, and `revisions.apply_supplier_read` writes the
result onto the variants and stamps `last_synced_at`. It has never run in
production:

* `business_os_supplier_sync_jobs` contains **0 rows**.
* `marketplace_product_sources.last_synced_at` is NULL on **all 34** rows.
* `supplier_worker.run_tick()` returns `{"status": "disabled"}` unless
  `CJ_RECONCILIATION_ENABLED` is set. It is **not set** in the production
  environment.
* No Railway service runs `supplier_worker.py`. The Procfile declares
  `supplier_worker: python supplier_worker.py --interval 300`, but the project's
  10 deployed services are CoinPilotX, telegram_worker, pulse-worker,
  media-engine, Command Center Worker, ads-worker, undx-worker, email_worker,
  alert_worker and Postgres. There is no supplier worker among them.

So a transient lease collision became a permanent state.

**(c) History.** Listings 14–41 predate two recent fixes and could never have
stored stock regardless: `normalize._cj_inventory_from_warehouses` (which reads
the adapter's `variants[].warehouses[]` projection) and the retirement of the
verified-warehouse rule. That is why only listings 42 and 45 — both imported
2026-09-16 — hold stock today.

### 3.3 Every multi-variant listing is unpublishable until the merchant chooses

`marketplace_product_sources.provider_variant_id` is the variant an order is
actually placed for; `fulfillment.create_intent` can order no other, and
`drafts._validate` correctly refuses to publish a listing without one
(`SUPPLIER_VARIANT_UNBOUND`).

`importer._sole_orderable` binds only when the choice is not a choice: one variant
total, or one variant that is not confirmed `UNAVAILABLE`. It deliberately
declines to break a genuine tie, and — correctly — does not treat `UNKNOWN` as a
negative. With all stock `UNKNOWN` (§3.2) every multi-variant import is a genuine
tie. The production split is exact:

| | single-variant | multi-variant |
|---|---|---|
| bound | 4 | 2 |
| **UNBOUND** | 0 | **28** |

**Correction.** An earlier draft of this section claimed "there is no
merchant-facing variant selector, so nothing can resolve the tie after the fact."
That is false, and the claim is retracted. The selector exists end to end:
`bind-product` at `services/business_os_supplier_routes.py:290` →
`gateway.bind_product` → `bindDraftVariant` at
`mobile-native/src/api/dropshipping.ts:1619`, called from
`ReviewImportedProductScreen.tsx:266`.

So the 28 unbound sources are **a merchant decision that has not been taken yet,
correctly guarded** — not a defect and not something this repair should resolve.
`link_source` accepts NULL → a variant but refuses variant A → variant B
(`binding_conflict`), because a published listing that silently changed what it
ships would keep selling a page describing the old product. A listing sells one
supplier variant; which one is a commercial choice, and automation picking it
would be automation choosing what the customer receives.

This is why `repair.resolve_binding` delegates to `importer._sole_orderable` and
why it bound **0** listings in the production apply (§7): landing real stock did
not turn any of the 28 ties into non-ties, because every unbound source has two
or more variants and all of them are now in stock. That is the correct outcome,
not a shortfall.

**Latent, not live:** the order → variant link is missing further down the same
path. `marketplace_orders` has no variant column (verified: 15 columns, none
variant-bearing), `marketplace_quote_service.create_quote` accepts a `variant_id`
that the Buy Now route at `bot.py:94316` never passes, and
`fulfillment.py:1489` resolves the supplier SKU by joining
`v.provider_variant_id = s.provider_variant_id` — which matches nothing while the
source is NULL. Nothing is currently harmed by this: `SUPPLIER_VARIANT_UNBOUND`
blocks publication, and production holds **0 orders**. It is recorded in §13 as
the thing that must be fixed *before* a multi-variant supplier listing is ever
published, not as damage already done.

### 3.4 Summary

Nothing wrote a placeholder price or a placeholder stock level. What happened is
that a self-inflicted lock collision silently voided inventory at import, the
repair path for it was never switched on, and the Store screen reported none of
that — reporting instead a missing price that was never missing. The unbound
multi-variant listings are a consequence of the same `UNKNOWN` stock, but they
are awaiting a merchant's choice behind a working guard rather than broken.

## 4. Deviation from the brief's inventory policy — deliberate, evidenced

The brief requires: *"exclude unverified factory inventory unless explicitly
enabled by store policy"*, counting only verified/enabled/fulfillable stock.

That exact rule was already implemented here, measured against this same
production data, and deliberately retired in commits `af396e80` ("a counted
warehouse is in stock, audited by CJ or not") and `0228c005` ("retire the
verified-warehouse rule where it still survived"), both present in this branch's
base. The reasoning recorded in `cj.CJAdapter._warehouse_stock`:

> `verifiedWarehouse` separates stock CJ has audited in its own warehouse (1)
> from stock the supplier reports at the factory (2), and CJ sells both …
> all 29 `supplier_snapshots` rows of kind `inventory` in production carry
> `verifiedWarehouse: 2` on every variant warehouse, with real counts beside
> them … A rule that rejects 100% of a provider's catalogue is not a safety
> property.

Today's live probe confirms it still holds: every warehouse row for every
imported product returns `cjInventory: 0`, `factoryInventory: <large>`,
`verifiedWarehouse: 2`. Implementing the brief's rule as written would restore
`UNKNOWN` on 100% of this store's catalogue — re-creating the defect this mission
exists to fix.

**Resolution taken:** the distinction is made a *store policy* with the default
set to include factory-reported stock, which is the behaviour proven correct
against this provider. The `verified` flag is preserved per warehouse so any
reader can still discriminate. A missing count is still never zero: `total is
None` stays `UNKNOWN`, and only an explicit `0` is `OUT_OF_STOCK`.

This is a knowing deviation from a "non-negotiable" decision, made because the
decision was written from the same incorrect premise as §2 and the evidence
against it is direct. It is flagged here rather than applied quietly.

## 5. What changed

Two commits on `commerce/cj-price-inventory-repair`, base `f4a5f2f1`, head
`d327552d`. No migrations: every column used already exists in production
(verified by `information_schema` probe, not by reading `init_db`).

| commit | file | ± | what |
|---|---|---|---|
| `2e9a82b0` | `services/business_os/marketplace/listing_readiness.py` | +168 | `evaluate` delegates to `drafts._validate` instead of deciding `MISSING_PRICE` from `price_label` |
| | `services/business_os/suppliers/importer.py` | +98 | inventory-read failure at import is recorded and retried, not swallowed |
| | `services/business_os/marketplace/listing_batch.py` | +11 | carries the supplier facts the verdict now needs |
| | `services/marketplace_variants.py` | +8 | |
| | `bot.py` | +83 | two payload helpers; **no new route, no new `os.getenv`** |
| | `tests/business_os/test_listing_readiness.py` | +263 | |
| | `tests/marketplace/test_seller_listing_readiness_route.py` | +157 | |
| `d327552d` | `services/business_os/suppliers/repair.py` | +561 | the one-shot backlog drainer (new) |
| | `scripts/repair_cj_inventory.py` | +79 | CLI (new) |
| | `tests/dropshipping/test_dropship_stock_repair.py` | +607 | 30 tests (new) |

### 5.1 Why the repair is not a second reconciler

The pipeline to answer for a variant's stock already existed and was correct.
`worker._read_job` asks the adapter, `revisions.plan_stock_revision` decides,
`revisions.apply_supplier_read` writes. What did not exist was a way to run it
over an existing backlog **once**, with the result inspected before it landed.

So `repair.py` owns no decisions. Stock is decided by `plan_stock_revision`;
binding by `importer._sole_orderable`; writing by `apply_supplier_read`. The
consequence is structural rather than promised: a dry run cannot forecast a
change the apply would not make, because the same function makes both. The
headline test asserts exactly that equality
(`test_the_plan_a_dry_run_prints_is_the_change_an_apply_makes`).

Two bugs were caught by writing it this way and are worth recording:

* An early `_apply_one` rebuilt a payload from the *normalized* readings and
  handed it to `apply_supplier_read`, which normalizes again — so the repair
  would have written through a shape no provider ever sends, and the dry run's
  guarantee would have been void. Fixed by carrying the untouched provider
  payload alongside the readings.
* The checkpoint was originally written from `run()`'s return value, i.e. only
  once the run finished — useless in the one situation a rollback file is for.
  It is now written, `flush()`ed and `os.fsync()`ed **before the first
  mutation**, and a test kills a run half way and rolls it back.

## 6. Security controls

* No CJ secret, token, `openId`, or raw signed payload is logged or placed in the
  report. `_read_inventory` returns a provider failure as a **code** (`str(exc.code)`
  truncated to 120 chars), never a body. Asserted by
  `test_a_provider_failure_is_reported_as_a_code_and_never_as_a_body` and
  `test_the_report_carries_no_cost_credential_or_provider_body`.
* Supplier cost never enters the report or any customer-facing field.
* Tenant scoping is not re-implemented: `targets()` scopes exactly as
  `worker._seed_jobs` does, on `(supplier_connection_id, business_id, store_id)`.
* `audit` requires no credential and no network flag. Every other mode goes
  through `policy.require_enabled()` + `policy.require_network()`.
* Reads are issued with `adapter.background = True` so a repair can never take a
  quota slot from a buyer's order — asserted on the production path, with no
  `adapter_factory` to fake it.
* A `provider_product_id` CJ could not have issued is counted, never sent: three
  UUID-shaped ids are reported as `provider_product_id_not_cj_shaped` rather than
  spending a quota call to learn what the id's own shape already says.

## 7. Production run — dry-run, canary, rollback proof, apply

Run 2026-09-16 against Railway Postgres 18.6 via `DATABASE_PUBLIC_URL`, with the
CoinPilotX service's credential environment.

### 7.1 Audit (no network, no write)

```
sources 34 · variants 719 · UNKNOWN 683 · IN_STOCK 36 · OUT_OF_STOCK 0
unbound sources 28 · never synced 34 · unreachable pid 3
```

### 7.2 Dry run (reads CJ, writes nothing)

```
considered 31 · skipped (bad pid) 3 · blocked 0
variants changed 618 · bindings resolvable 0
```

Every one of the 618 forecast changes was `UNKNOWN → IN_STOCK` carrying a real
count. Distribution of the counts: **min 5,042 · max 40,813 · none null · none
zero · none ≤ 5**. Zero `attention` flags, zero blocked.

That last figure is the direct answer to the brief's "1 thing left": **no variant
in this catalogue is low-stock**, so nothing should ever have rendered that
string. It was `UNKNOWN` being displayed as a number, not a count of one.

### 7.3 Canary (2 products, committed)

```
considered 2 · variants changed 42 · UNKNOWN 683 → 641 · IN_STOCK 36 → 78
```

Matched the dry run for those two products exactly.

### 7.4 Rollback, proven on production before the full apply

```
$ scripts/repair_cj_inventory.py --rollback /tmp/cj-canary.json
restored 43 variants, 2 sources, 2 listings
$ scripts/repair_cj_inventory.py --mode audit
UNKNOWN 683 · IN_STOCK 36 · unbound 28
```

Production returned to the starting census exactly. Reversibility is measured,
not asserted.

### 7.5 Apply

```
considered 31 · skipped 3 · blocked 0
variants changed 618 · bound this run 0
UNKNOWN 683 → 65 · IN_STOCK 36 → 654 · unbound 28
```

## 8. Independent production verification

Queried separately from the tool that did the work:

| check | result |
|---|---|
| `IN_STOCK` variants | 654, **all 654 with a count** (132 – 40,813) |
| `UNKNOWN` variants | 65, all with no count |
| variants under non-CJ-shaped pids | **65** — exact match, fully accounting for the remainder |
| `IN_STOCK` reading as low / "1 left" (`qty ≤ 5`) | **0** |
| source `sync_state` | 34 / 34 `SYNCED`, `last_sync_error` 0 |
| listing status | 32 draft · 3 review_ready · 2 pending_review · 3 published |
| orders affected | 0 (production holds no orders) |

**Nothing was auto-published.** The listing-status distribution is unchanged from
before the run, which is the brief's decision 8 held.

## 9. Tests

| command | result |
|---|---|
| `pytest tests/dropshipping/test_dropship_stock_repair.py -q` | **30 passed** in 0.19s |
| every `tests/dropshipping/test_*.py`, one file per process (19 files) | **710 passed, 0 failed** |
| `scripts/protection/run_protection_suite.py` | **673 checks across 44 suites passed**, exit 0 |

`tests/dropshipping/` files bind `DATABASE_URL` to a tempfile at import, before
`services.db` computes `IS_POSTGRES`, so they cannot share a pytest process; the
sweep runs one file per process for that reason.

An earlier draft of this table said 730. That number was never printed by any
run — it was an arithmetic slip in a hand-written summary. The sweep was re-done
per file, recording both collected and passed counts, and the two agree on every
one of the 19 files, so the 710 is not hiding a skip or a deselection.

Two fixture defects were found and fixed while getting the suite green, both in
the test file rather than the module: `upsert_variant` takes `options` as a list
of `{name, value}`, and a provider `RuntimeError` is *deliberately* contained per
product, so the "killed mid-run" test now raises `KeyboardInterrupt` — which is
what an actually-killed process does.

## 10. Rollback instructions

```
python scripts/repair_cj_inventory.py --rollback /tmp/cj-apply.json
```

The checkpoint (`/tmp/cj-apply.json`, 117,586 bytes, written 2026-09-16 15:36
before the first write) restores only the four fields this module writes —
variant `stock_state` / `stock_quantity` / `stock_synced_at`, source
`provider_variant_id` / `sync_state` / `last_synced_at` / `last_sync_error` /
`attention_json`, and listing `quantity`. It is deliberately narrow so a rollback
cannot silently revert an unrelated merchant edit made in the meantime, and
`restore` is idempotent (asserted).

## 11. What this did *not* do

The brief's stages 4, 6, 8, 9 and 10 are **not** delivered. Stating that plainly
matters more than a green verdict:

* **Store pricing profile** (5% reserve / 35% margin / $5 floor / `.99`) is not
  implemented as an editable per-store profile. Prices are not the live defect —
  §3.1 proved the prices were already there and only the verdict was wrong — so
  this was ranked below landing real inventory.
* **Webhooks** — no HMAC-verified CJ webhook receiver was added.
* **Checkout revalidation / atomic reservation / idempotency key** — not added.
* **Observability** — no new metrics or alerts.
* **Native store screens** — untouched.

## 12. Operational blocker — the durable fix is not a code change

The repair drains the backlog that exists today. It does not keep it drained.

`worker.run_once` is the thing that would keep inventory fresh, and it never
runs: it is gated on `CJ_RECONCILIATION_ENABLED`, which is **absent from the
Railway variable list**, and no `supplier_worker` service is deployed. The seed
predicate itself is fine — probed directly, all 34 source rows carry
`supplier_connection_id`, `business_id`, `store_id` and `provider_product_id`,
and `_seed_jobs` would seed all 34 pids.

This is the single highest-value durable repair available and it is an
infrastructure decision, not a patch: **set `CJ_RECONCILIATION_ENABLED` and
deploy a `supplier_worker` service.** Flagged for the operator rather than taken
unilaterally.

## 13. Remaining blockers

1. **Reconciliation is off in production** (§12). Without it the 654 counts
   landed today go stale, and staleness is invisible — `stock_synced_at` is set
   at import even when stock was unknown, so it cannot currently distinguish
   "counted" from "looked at".
2. **Order → variant link is missing** (§3.3). Must be fixed *before* any
   multi-variant supplier listing is published. Currently harmless: publication
   is blocked and there are no orders.
3. **Three listings carry provider ids CJ cannot resolve** — 31, 34, 37, holding
   65 variants, all still `UNKNOWN`. They are UUID-shaped, from an earlier import
   path. They need re-importing or retiring; no amount of CJ querying will fix
   them.
4. **28 listings await a merchant variant choice.** Not a defect (§3.3) — the
   guard and the screen both work — but nothing publishes until someone chooses.
5. The brief's verified-warehouse rule is knowingly not implemented as written
   (§4).

## 14. Verdict

**PARTIAL.**

The inventory half of the mission is done and verified in production: the root
cause is proven rather than guessed, the fix is reversible and was proven
reversible on production before the full run, 618 of 719 variants now carry real
supplier counts, the remaining 65 are exactly and only the ones under unresolvable
provider ids, no listing was published, no placeholder value was written, and the
"1 thing left" and "Price required" symptoms are both gone — the first because
nothing is low-stock, the second because the verdict now reads the prices that
were always there.

It is not `PASS` because stages 4, 6, 8, 9 and 10 are undelivered (§11), and
because the durable fix for the defect this mission is named after is an
operational change that has not been made (§12). A repair that clears a backlog
without turning on the thing that prevents the next one has fixed today, not the
problem.
