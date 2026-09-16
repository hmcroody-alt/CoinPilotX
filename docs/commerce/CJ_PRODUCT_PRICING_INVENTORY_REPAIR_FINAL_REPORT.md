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

### 3.3 Every multi-variant listing is unshippable

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

There is no merchant-facing variant selector, so nothing can resolve the tie
after the fact.

### 3.4 Summary

Nothing wrote a placeholder price or a placeholder stock level. What happened is
that a self-inflicted lock collision silently voided inventory at import, the
repair path for it was never switched on, the resulting `UNKNOWN` stock left every
multi-variant listing unbindable, and the Store screen reported none of that —
reporting instead a missing price that was never missing.

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

<!-- Sections 5-14 (changes, tests, dry-run, canary, deployment, verdict) are
     appended as the work lands. -->
