# PulseSoc Store — Seller Commerce Workspace: Architecture Map

Written before implementing, per the mission's §102. Everything here was read or
run, not inferred from the UI. Where a claim is behavioural it names the probe
that produced it.

Companion document: `CJ_IMPORT_TO_CHECKOUT_ARCHITECTURE_MAP.md`, which maps the
supplier import → checkout → fulfilment path. That map stays authoritative for
CJ; this one covers the seller's own product-management surface. They overlap at
exactly one place — the readiness question — and that overlap is this mission's
central problem.

---

## 1. The headline finding: there are four answers to "can this be sold?"

The mission's §5 asks for *one server-authoritative readiness result*, and §81
for *ONE readiness engine*. Both are written as requirements. They are better
read as descriptions of a live defect, because the codebase currently holds four
separate authorities on that question, in three different vocabularies, over two
different product tables.

| # | Authority | Object it judges | Vocabulary | Who reaches it |
|---|---|---|---|---|
| 1 | `services/business_os/suppliers/drafts.py:521` `_validate` | `marketplace_listings` row, supplier-imported | `{publishable: bool, problems: [SCREAMING_CODE]}` — 13 codes | the CJ import/publish path |
| 2 | `services/business_os/marketplace/listing_drafts.py:168` `_completeness` | `business_os_mkt_listing_drafts` scratchpad | `{ready: bool, missing: ["section.field"]}` | the native composer |
| 3 | `services/business_os/marketplace/service.py:249` `_validate_product_input` + `:404` `transition_product` | `business_os_mkt_products` row | a raised `MarketplaceError(code, status)` | the real decider behind #2 |
| 4 | `mobile-native/src/api/storeDashboard.ts:592` `storeReadiness` + `:147` `listingHealth` | a client-side copy of `marketplace_listings` rows | a 5-rung ladder + 5 health states | **the Store screen the merchant actually opens** |

A fifth authority judges the neighbouring question — whether a *paid sale* can be
ordered from the supplier — and is mapped in the CJ document:
`suppliers/fulfillment.py:1016` `BLOCKERS` (`SUPPLIER_SKU_MISSING`,
`SUPPLIER_COST_UNKNOWN`, …).

Authority #4 is the one with merchant consequences, and it reads none of #1, #2
or #3. It cannot: the endpoint it loads returns no verdict to read.

### Why the fourth authority exists

`mobile-native/src/api/marketplace.ts:299` `listMarketplaceSellerListings` calls
`GET /api/pulse/marketplace/seller/listings`, which is `bot.py:54400`. That route
selects 28 columns from `marketplace_listings` and renders them through
`pulse_marketplace_listing_payload`. There is **no** `publishable`, no
`blockers`, no `warnings`, no `checkout_ready` anywhere in the payload.

So the client had no verdict to render and computed one. The module docstring at
`storeDashboard.ts:585` is candid about it: *"Everything below is read from data
the screen already loads — no new call and no new field."* That was a reasonable
local decision. It is the wrong global one, and it is what §5 exists to correct.

`storeDashboard.ts:104` even owns a threshold of its own,
`LOW_STOCK_THRESHOLD = 5`, which no server code knows about.

### The irony worth recording

`listing_drafts.py:16` already states this mission's principle, in the repo, in
prose:

> `completeness` is an HONEST server-computed checklist — the client renders
> exactly what the server says is missing, never its own guess

The principle was written down. It was then not applied to the surface that
matters, because that surface loads a different endpoint over a different table.
Writing a rule in a docstring does not propagate it.

---

## 2. Two product tables that never meet

| Table | References in `services/` + `bot.py` | Read by |
|---|---|---|
| `marketplace_listings` (+ `marketplace_listing_variants`, `marketplace_product_sources`, `marketplace_product_media`) | 120 | the mobile Store screen, every buyer surface, the whole CJ supplier path |
| `business_os_mkt_products` (+ `business_os_mkt_listing_drafts`) | 28 | the Business OS catalog engine and the native composer only |

Measured: no statement in the repo mentions both table names. They are two
catalogs, not one catalog with two doors.

The mission's §43/§44 say keep `marketplace_listings` canonical and do not create
duplicate mapping authorities. The duplicate already exists and predates this
mission. **This mission must not attempt to merge them** — that is a data
migration with buyer-visible blast radius and no part of the brief asks for it.
What this mission must do is refuse to add a *fifth* authority, and target the
canonical table, because that is the one the Store workspace reads and the one
every buyer surface reads.

Consequence for §81: the one readiness engine has to live where
`marketplace_listings` lives, and the `business_os_mkt_products` evaluators (#2,
#3) stay where they are, serving the composer. Unifying those is out of scope and
is recorded here as known debt rather than quietly left unmentioned.

---

## 3. GAP 21 — the honest checklist is not honest (measured today, unfixed)

Probe: a native draft, every publish requirement satisfied, physical
fulfilment, and the seller answers the inventory question truthfully with zero.

```
CHECKLIST SAYS    {'ready': True, 'missing': []}
PUBLISH SAYS      REFUSED no_inventory / Cannot publish a physical product with no inventory.
DRAFT STATUS NOW  in_progress product: None
```

The merchant is told nothing is missing, presses Publish, and is refused.

Root cause: `_completeness` is a **forecast of another authority's decision**.
Its own comment at `listing_drafts.py:177` admits the shape of it —

> the catalog engine refuses to publish a PHYSICAL product with no inventory, so
> the checklist must say so up front rather than claiming ready and letting
> publish fail

— and then forecasts it wrongly. The checklist tests `inventory_qty is None`
(line 182); the decider tests `(inventory_qty or 0) <= 0`
(`service.py:404-408`). `0` is not `None`, so the checklist passes a value the
decider rejects. The comment states the exact failure mode it was written to
prevent, and the code prevents it only for the unanswered case, not the answered
case.

This is the same defect family as the CJ map's twenty-two entries: **a predictor
kept manually in sync with a decider drifts, and the drift is invisible until a
merchant hits it.** The fix is not to patch the `0` — patching it leaves the
forecast in place to drift again on the next rule. The fix is for the checklist
to *ask* the decider.

Not a regression from this mission, and not a fixture artifact: `0` is a value
the composer's own inventory section accepts
(`listing_drafts.py:146-151` permits any non-negative int).

Good news from the probe: the draft stayed `in_progress` with
`published_product_id: None`, so `create_product`'s row rolls back with the
uncommitted transaction. No orphan product. The defect is purely the false clear
— which is expensive enough.

### Its mirror image, already proven

`CJ_IMPORT_TO_CHECKOUT_ARCHITECTURE_MAP.md` records **GAP 20**: `list_obligations`
reports `can_place_supplier_order: False` with `SUPPLIER_SKU_MISSING` and
`SUPPLIER_COST_UNKNOWN` for an order on which `create_intent` *succeeds*.

Put the two side by side:

- Gap 21: the reader says **yes**, the writer says **no**. A false clear.
- Gap 20: the reader says **no**, the writer says **yes**. A false blocker.

Two authorities, two directions, same cause: something that reports readiness is
not the thing that enforces it. Both are in scope for this mission's §5/§81, and
neither is fixed by writing a new evaluator beside them.

---

## 4. What exists on the client

### Screens

| Screen | Path | Role |
|---|---|---|
| `SellerStoreRoute` | `src/screens/SellerStoreRoute.tsx:30` | dispatches on `isStoreDashboardRoute(params)` |
| `StoreDashboardScreen` | `src/screens/StoreDashboardScreen.tsx` | the current Store dashboard: KPIs, listing tabs, attention banner, setup checklist |
| `SellerStoreScreen` | `src/screens/SellerStoreScreen.tsx` | legacy panelled screen; **single-listing inline edit** (state at :51-57) |
| `SellerListingComposerScreen` | `src/screens/SellerListingComposerScreen.tsx` | 4-step add-listing wizard |
| `DropshippingProductsScreen` | `src/screens/dropshipping/DropshippingProductsScreen.tsx` | imported supplier products, draft/published filter |

Route type: `navigation/types.ts:367` — `SellerStore: { mode?: "overview" | "apply" |
"dashboard" | "profile" | "create" | "payouts" | "orders"; listingId?: number }`.
Mode→panel mapping: `navigation/sellerStoreMode.ts:27` `PANELS_BY_MODE`.

### Confirmed absences on the client

- **No selection mode.** No checkbox, no Select All, no selection-mode toggle
  anywhere in Store. The nearest relative is the dropshipping import cart, which
  is sequential add/remove, not multi-select.
- **No bulk anything** — no bulk edit, bulk publish, bulk pricing, bulk
  visibility.
- **No test coverage** for any of the above, because none of it exists.

So §16-§33 (selection mode, bulk action sheet, bulk editors) is new construction
rather than a rework. That is the cheaper half of this mission.

### Design tokens — already centralized, do not add a second file

`src/theme/storeLight.ts` is already the single source of Store trade dress, is
already PulseSoc green rather than the reference yellow (:28-41, with the
deliberate-decision rationale recorded in the docstring), and is already
structured as tokens rather than inlined per screen.

The mission's §3 token list maps onto it almost completely:

| §3 asks for | exists as | status |
|---|---|---|
| `store.background` | `storeLight.bg.page` | present |
| `store.surface` | `storeLight.bg.card` | present |
| `store.header` | `storeLight.bg.headerFrom` / `headerTo` | present |
| `store.action` | `storeLight.cta` (= `STORE_CTA_PULSESOC`) | present |
| `store.success` | `storeLight.status.success` | present |
| `store.warning` | `storeLight.status.warning` | present |
| `store.danger` | `storeLight.status.error` | present |
| `store.border` | `storeLight.border.hairline` | present |
| `store.selected` | — | **missing** (nothing is selectable yet) |
| `store.disabled` | — | **missing** (`theme/colors.ts:` dark theme has one; the light Store theme does not) |

The token work is therefore **two additions to the existing file**, not a new
`storeTokens.ts`. Creating a parallel token module would be this codebase's
signature mistake committed against its own palette.

One caution recorded in the file itself (`storeLight.ts:105-111`):
`accent.orange` is unused by Store but read by Insights through the `storeLight`
spread. Deleting it silently repaints a screen outside this mission's scope.

---

## 5. What exists on the server

### Route families

| Prefix | Registered at | Serves |
|---|---|---|
| `/api/pulse/marketplace/seller/...` | `bot.py:54400` (list), `:54890` (PATCH one), `:55116` pause, `:55147` resume, `:55189` delete | **the mobile Store screen** — canonical `marketplace_listings` |
| `/api/business-os/marketplace/...` | `bot.py:25651-26010` | the Business OS catalog over `business_os_mkt_products` |
| `/api/business-os/store/<business_id>/...` | `bot.py:24786-24982` | storefront/products/collections |

All registered as bare `@webhook_app.route` decorators in `bot.py`. No blueprint.

### Confirmed absences on the server

- **No batch endpoint for listings.** Every mutation above is single-entity.
- **No `batch_id`**, no batch result contract, nothing matching §39.
- **No `checkout_ready` for a listing.** The only `checkout_ready` in the repo
  (`bot.py:11988`, `:56169`, `:80288`) is the *Founder membership* checkout flag —
  unrelated, and a name collision to avoid.
- **No `blockers[]` / `warnings[]` on any listing read.**

### Prior art to reuse rather than reinvent

- **Idempotency**: `suppliers/fulfillment.py` already runs a real
  idempotency-key discipline with a `UNIQUE` column and a retirement gate, and
  its failure modes are documented in the CJ map through 22 defects and a 26-case
  mutation battery. §40's `batch_id` + `idempotency_key` should copy that shape,
  including its hard-won lesson that the guard must be *in the write statement*,
  not only in the read that precedes it.
- **Batch-with-per-item-outcomes**: `suppliers/importer.py:78` `MAX_BATCH = 25`
  returns per-item results already. §34's "14 PUBLISHED / 4 BLOCKED" is the same
  contract with a batch identity added.
- **Pricing**: `suppliers/pricing.py` already has `apply_rule` (:96),
  `margin_cents` (:131), `margin_percent` (:145), `margin_state` (:162). §83 says
  ONE pricing engine — this is it. Note the vocabulary gap: it returns
  `HEALTHY / MARGINAL / NEGATIVE / UNKNOWN`, while §14 asks for
  `HEALTHY / LOW_MARGIN / CRITICAL_MARGIN / NEGATIVE_MARGIN / UNKNOWN`. Extend
  the existing enum; do not define a second one beside it.
- **Merchant-override protection**: `services/marketplace_variants.py:712`
  `mark_overridden` and `:742` `sync_updates_allowed` already implement §45/§46.
  `suppliers/drafts.py:86` holds the whitelist:
  `EDITABLE = {"title", "description", "category", "media", "price_cents", "currency"}`.
  **Gap against §45**: cover image and visibility are not in it. To verify
  behaviourally before trusting either way.

---

## 6. Scope decisions taken here, with reasons

1. **One readiness engine over `marketplace_listings`, served to the client, and
   the client stops deriving.** `storeDashboard.ts`'s ladder and
   `LOW_STOCK_THRESHOLD` become renderers of a server verdict. This is §5 + §81
   and it is what closes authority #4.
2. **The engine must be the thing publish calls, not a forecast of it.** Gap 21
   is what a forecast costs. Publish asks the engine; the read asks the same
   engine; there is no second copy to drift.
3. **`business_os_mkt_products` is left alone.** Merging the two catalogs is a
   buyer-visible migration nobody asked for. Recorded as debt in §2 above.
4. **No new token file.** Two tokens added to `storeLight.ts`.
5. **Batch endpoints copy fulfillment's idempotency discipline**, because that
   discipline has already been attacked by a mutation battery and survived.
6. **Gap 20 is carried into this mission**, not dropped — it is §5/§81 stated as
   a defect, on the supplier side of the same seam.

## 7. Standing constraints this mission inherits

- CJ writes sandbox only; `REAL_CJ_FUNDING_ENABLED=OFF`; no real supplier spend
  (§66).
- RTC / Agora / audio / calls / livestream / camera / mic: **expected changes 0**
  (§98). Nothing in this mission's surface touches a protected path, so the
  real-time audio gate should inspect 0 files. Note that `package.json` edits
  trip that gate via `dependency_watch` — prefer no new dependency.
- No production App Store build (§99).
- Commit by explicit path only. Never `git add .`/`-A`, `reset --hard`,
  `clean -fd`, `stash`, `commit -a`, or force push.
- Buyer responses must never carry supplier cost, margin, credentials, tokens,
  `openId`, or `connection_id` (§64, and §27/§95 of the CJ mission).

## 8. Method note carried over from the CJ mission

Two rules earned there apply here unchanged, and both are about tests:

- **A test that restates the statement measures nothing.** A test earns its keep
  by executing the artifact that ships. Copying SQL or a predicate into the test
  and asserting on the copy reads as *more* rigorous than a call, and measures
  strictly less.
- **Defence in depth hides its own halves.** Where a guard is deliberately
  duplicated (read decides, write re-decides), deleting either copy leaves the
  suite green. Test each layer with the other removed from the picture.

Both are why this mission's §104 mutation tests matter more than its unit tests:
a duplicated authority is exactly the thing a passing suite fails to notice.
