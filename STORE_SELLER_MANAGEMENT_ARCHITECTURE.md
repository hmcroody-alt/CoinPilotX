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
| 5 | `services/marketplace_listing_lifecycle.py:55` `inventory_available` | `marketplace_listings` row, at purchase time | `bool` | **checkout** (`bot.py:92730`) |

A sixth authority judges the neighbouring question — whether a *paid sale* can be
ordered from the supplier — and is mapped in the CJ document:
`suppliers/fulfillment.py:1016` `BLOCKERS` (`SUPPLIER_SKU_MISSING`,
`SUPPLIER_COST_UNKNOWN`, …).

Authority #4 is the one with merchant consequences, and it reads none of #1, #2
or #3. It cannot: the endpoint it loads returns no verdict to read.

### Authority #5 is different in kind, and that changes the job

#1–#4 are all *reporters*: they tell somebody what they think will happen.
Authority #5 is the **decider** — it is what runs when a buyer presses Buy, and
its answer is the one that becomes true. It was found late, while asking a
question that should have been asked first: *does `checkout_ready: false` for
unknown stock actually match what checkout does?*

That reframes §5 and §81. "One readiness engine" cannot mean five reporters
collapsed into one reporter, because a single reporter that disagrees with the
decider is still wrong — it is just wrong in one place instead of four. The
verdict has to be **bound** to the decider, and the binding has to be asserted,
not maintained by hand. A predictor kept manually in sync with a decider is a
defect with a delay on it.

It also revealed a trap. The two type authorities read the columns in **opposite
precedence**:

| Reader | Precedence | Consequence |
|---|---|---|
| `inventory_available` (checkout) | `product_type or listing_type` | a row written to `listing_type` with the legacy `TEXT DEFAULT 'digital'` intact reads as *digital* |
| `marketplace_listing_types.effective_listing_type` | `listing_type or product_type` | the same row reads as *physical* |
| `bot.py:19625` | `listing_type or product_type or delivery_type or "physical"` | a third rule again |

Measured against production before acting (read-only, `railway run --service
Postgres`): **14 listings, 0 rows where the two disagree, 0 rows with a NULL
quantity.** So this is a latent correctness bug, not a live outage — recorded
that way rather than dressed up. Worth noting separately: **7 of the 14 have
`quantity = 0`**, so they are genuinely not checkout-ready today.

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

## 3. GAP 21 — the honest checklist is not honest (FIXED, `ddef7c03`)

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

### How gap 21 was fixed

`service.publish_blockers(fulfillment_type=, inventory_qty=)` — one predicate,
two callers. `transition_product` raises the first blocker it returns; the
checklist lists all of them. Behaviour of the publish verb is unchanged (same
code, same message, same 409); only the checklist stopped guessing.

A note on what unification cost: once the checklist *asks* the engine, an
agreement test between them goes green even if the engine's rule is wrong,
because both sides move together. So the rule also needs a direct pin. Mutation D
— engine forgets that zero is empty — confirmed that concern was real: it was
caught by the direct pin, while the agreement test alone would have accepted it.

---

## 3b. GAP 22 / GAP 23 — the client's verdict (FIXED server-side)

Both are symptoms of authority #4 existing at all, and both are cured at the
source rather than patched on the client.

**GAP 22 — unknown stock renders as sold out.** `marketplace_listings.quantity`
is nullable, and a NULL means the seller does not track stock.
`pulse_marketplace_listing_payload` passes it through untouched via `**item`. Then
`mobile-native/src/api/marketplace.ts:645` does `quantity: Number(item.quantity || 0)`
and the distinction is gone; `storeDashboard.ts:167` maps the resulting `0` to
`"out_of_stock"`, which pushes the listing into the "out" tab, raises the red
attention banner, and excludes it from "active". A merchant who simply does not
count stock is told their product is unavailable. This is listed in the client's
own `STORE_MOCK_DATA_GAPS` (entry 7), whose stated fix is *"quantity preserved as
null through listing normalization"* — written down, never done.

**GAP 23 — a missing price is silence.** `StoreListingRow.tsx:126` renders nothing
when `price_label` is blank. That is safe from the "$0.00"/"Free" failure §12
forbids, but it gives the merchant no name for the gap, which §12 and §7 both
require.

### The fix: one server-authoritative verdict

`services/business_os/marketplace/listing_readiness.py` — `evaluate(listing, media=)`
returns `{publishable, checkout_ready, blockers[], warnings[]}`, attached to each
row by `/api/pulse/marketplace/seller/listings`.

Design decisions worth keeping:

- **It is not a fifth vocabulary.** Every code it shares with
  `suppliers/drafts.py` is spelled identically, and
  `test_the_vocabulary_matches_the_supplier_evaluator` fails if either side
  renames one. That test is the cheapest available guard against the fifth
  authority this map warned about.
- **Unknown ≠ empty.** `UNKNOWN_INVENTORY` and `OUT_OF_STOCK` are separate codes.
  Unknown blocks *checkout* without blocking *publication* — failing closed on the
  promise to a buyer, open on the merchant's right to list.
- **Stock never blocks publication.** A merchant restocking a live listing is the
  ordinary case; unpublishing it would cost the listing its ranking and reviews
  over a temporary fact.
- **Computed from the database row, not the serialized payload.** The serializer
  coerces `quantity` and defaults a blank price, so readiness must see the raw
  NULL. `test_an_untracked_quantity_reaches_the_verdict_as_unknown` asserts this
  through Flask against a row written as NULL, and would fail if anyone switched
  the call to the payload.
- **Attached in the seller route, not the serializer.**
  `pulse_marketplace_listing_payload` also feeds the public listing page and
  `/api/pulse/marketplace/search`. Attaching readiness there would have told every
  shopper which sellers have unpriced drafts and empty shelves.
  `test_a_buyer_facing_listing_carries_no_verdict` pins the separation.

### Two bugs the tests caught before this shipped

Recorded because both were *in the code written to prevent them*, which is the
pattern worth recognising:

1. **`_text(0)` is `""`.** The helper is `str(value or "").strip()`, so the blank
   check `_text(raw) == ""` reported an integer `0` as blank and every genuinely
   sold-out listing came back `UNKNOWN_INVENTORY`. That is `Number(x || 0)` — the
   client bug this module exists to fix — wearing a different costume. The blank
   test now runs on string spellings only.
2. **`product_type` and `delivery_type` are both `TEXT DEFAULT 'digital'`.** The
   first `_tracks_stock` matched on those two columns, so a physical lamp created
   by the modern write path (which sets `listing_type`) looked digital, and the
   entire inventory half of the verdict silently did nothing for every row in the
   store. Fixed by asking `services/marketplace_listing_types.effective_listing_type`
   — the existing owner of "which of the five types is this row" — instead of
   inventing a third precedence rule beside it and `bot.py:19625`.

Bug 2 is the more instructive: **the unit tests could not see it, because the
fixture passed `product_type` directly and so agreed with the engine's wrong
reading of the row.** Only the route test — which inserts a row and lets the
column defaults apply — failed. The fixture has since been corrected to carry
both columns with the values a real row carries, and
`test_the_default_column_values_do_not_make_everything_stockless` pins it.

### A third bug, found by binding the reporter to the decider

The two above were caught before the commit. This one was caught *after* it, by
writing the test that should have existed first:
`test_checkout_ready_never_promises_what_checkout_refuses` runs every verdict
past authority #5 and asserts the one direction that must never happen — the
verdict promising a purchase checkout will refuse. (The opposite is legitimate:
a missing price closes checkout for reasons that have nothing to do with stock.)

It failed immediately, on this module's own brand-new code:

> `listing_type='digital'` over `product_type='physical'` with `quantity` NULL →
> readiness said `checkout_ready: true`; `inventory_available` said no.

That is **gap 21's exact shape in code written to close gap 21** — a reporter
restating a decider's rule and drifting from it, this time within a day rather
than over a year. The cause was `_tracks_stock` taking its own type reading as
final while checkout used the opposite precedence.

The fix does not align the precedences — picking either one would just move which
rows are wrong. It **asks** instead:

```python
def _stockless_at_checkout(listing):
    return bool(_life.inventory_available(dict(listing, quantity=None), 1))

# stockless only when BOTH readings agree
return not (stockless_here and _stockless_at_checkout(listing))
```

Two details carry weight. Nulling the quantity first isolates the *type* half of
checkout's answer — handed a real quantity, `inventory_available` answers "is it
in stock", and a well-stocked row would look stockless, costing the merchant
their LOW_STOCK warning (mutant Q, now pinned). And requiring **agreement** fails
closed in both directions: the verdict never promises a sale the till refuses,
and at worst reports a stock state for something checkout would have sold anyway
— which shows the merchant a real inconsistency instead of hiding it.

A fourth issue fell out of the same fix: `LEGACY_STOCKLESS_PRODUCT_TYPES` had
been hand-written as `("course", "membership", "music", "ebook")`, read off the
admin dropdown at `bot.py:8710`. Checkout recognises only `course`; the other
three were fiction that would each have become a false clear. It is now *derived*
— `set(_life.STOCKLESS_TYPES) - set(_types.LISTING_TYPES)` — and
`test_nothing_is_called_stockless_here_that_checkout_still_counts` asserts the
rule rather than the list, so a guess cannot be reintroduced.

The lesson, stated plainly: **a fixture that agrees with the code's mistake
proves nothing, and a rule restated from another module is a guess until a test
makes the other module answer.**

A 17-mutant battery over the engine (`scripts/mutate_listing_readiness.sh`) has
no survivors; each mutant is `ast.parse`-verified and no-op-detected, because a
malformed mutant is not evidence. Mutants N, O, P and Q exist specifically to
defend the binding above — N (drop the agreement), O (loosen `and` to `or`),
P (restore the invented vocabulary), Q (stop isolating the type).

### Still open on the client

The server now answers; the client still derives. Retiring authority #4 —
`storeReadiness`, `listingHealth` and the client's private `LOW_STOCK_THRESHOLD`
— and rendering `readiness` instead is the remaining half of §5, along with §12's
"Price required" and §7's "N things left".

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
