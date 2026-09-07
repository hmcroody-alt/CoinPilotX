# Commerce / Dropshipping Foundation Map

Phase 1–2 deliverable for the mission *"PulseSoc Dropshipping-Ready Marketplace + Business OS
Store Foundation."* The mission's own §0 orders this first:

> Do not start by adding random fields to the current product screen. First map the EXISTING
> commerce architecture.

This document is that map. It exists to stop three specific failures:

1. Building a second commerce system next to the one that already sells things.
2. Implementing a mission section whose requirement the repository already satisfies.
3. Anchoring a supplier design on a table that holds no merchandise.

All three were live risks when this was written. The third had already happened — see
§2.

Method: every claim below is either a file:line citation I read directly, or a production
query I ran. Claims I did **not** verify myself are marked *(unverified)* and must not be
built on. Nothing here was taken on the word of a summary.

Status: **mapping only. No commerce code was changed by this document.**

---

## 1. There are four product ledgers

Four tables can hold a product. Each has exactly one INSERT site:

| Table | Sole writer | Reached by |
|---|---|---|
| `marketplace_listings` | `bot.py:91374` (route `bot.py:91299`) | native app + web, `/api/pulse/marketplace/*` |
| `business_os_mkt_products` | `services/business_os/marketplace/service.py:288` | `/api/business-os/marketplace/*`, `templates/business_os.html` |
| `business_os_store_products` | `services/business_os/store/service.py:615` | `templates/business_os_commerce.html` |
| `pulsesoc_seller_products` | `services/pulsesoc_dashboard_centers.py:1103` | legacy dashboard, `bot.py:8567` |

They do not join. There is no foreign key, no shared identifier, and no reconciliation
between any pair of them.

## 2. Only one of the four holds merchandise — and it is not the one the prior design assumed

The `/api/business-os/marketplace/*` routes are gated (`bot.py:25421`, predicate
`_business_os_marketplace_enabled` at `bot.py:25357`) and return 404 when the flag is off.
`.env.example:541,547` ships `BUSINESS_OS_MARKETPLACE=` and `BUSINESS_OS_STORE=` empty, so a
local checkout has them dark.

**That local default does not describe production.** Both flags are set truthy on Railway
(service `CoinPilotX`), along with `BUSINESS_OS_ORDERS`, `BUSINESS_OS_LEDGER`,
`BUSINESS_OS_CREATOR_COMMERCE`, and `BUSINESS_OS_MERCHANT_AUTOMATION`. Inferring "dark" from
`.env.example` is wrong; the business_os commerce surface is reachable in production today.

So the question "which ledger is canonical" cannot be answered from flags. I answered it
from data. Production row census:

```
marketplace_listings               6 rows
marketplace_product_media          8 rows
marketplace_sellers                1 rows
seller_transactions               29 rows

business_os_mkt_products           0 rows
business_os_store_products         0 rows
pulsesoc_seller_products           0 rows
business_os_mkt_orders             0 rows
business_os_mkt_order_items        0 rows
marketplace_orders                 0 rows
```

**`marketplace_listings` is the only populated product ledger, and `seller_transactions` is
the only populated order ledger.** The other three product tables are *enabled and empty* —
reachable code with no merchandise in it. That is a different and more dangerous condition
than "dark": nothing 404s, so a reader can exercise the API, get a clean response, and
conclude the table is canonical.

**Correction to a prior mission's output.** `reports/cj-discovery/CJ_PULSESOC_DATA_AUTHORITY_MAP.md`
cites `services/business_os/marketplace/service.py:268` as "product creation" and builds its
authority model on that row shape. That is the empty ledger. The shape it assumes —
`price_cents INTEGER`, `inventory_qty` nullable-meaning-untracked — is **not** the shape of
the table that actually sells (`price_label TEXT`, `quantity` nullable-meaning-out-of-stock;
see §4 and §5). Those documents remain useful as supplier-contract design. Their *field-level*
authority mapping does not describe the live product row and must not be implemented against it.

Both cj-discovery documents are explicitly marked "proposed, not implemented," so nothing was
built on the error. It would have been, next.

## 3. What the live product row actually is

DDL `bot.py:109230`, plus ~30 `add_columns_if_missing` at `bot.py:109242-109276`:

```sql
CREATE TABLE IF NOT EXISTS marketplace_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, seller_user_id INTEGER, title TEXT,
    description TEXT, category TEXT, price_label TEXT DEFAULT 'Request access',
    status TEXT DEFAULT 'active', created_at TEXT, updated_at TEXT)
```

Added columns include `approval_status`, `quantity`, `currency`, `delivery_type`,
`product_type`, `listing_type`, `listing_metadata_json`, `cover_image_url`, `gallery_json`,
`refund_policy`, `estimated_delivery`, `moderation_*`, `review_version`.

Ownership is `seller_user_id`, enforced by `AND seller_user_id=?` on every mutating UPDATE —
verified at all five sites: media `bot.py:53993`, metadata `:54079`, pause `:54118`, submit
`:54157`, delete `:54192`. Creation additionally requires an **approved** row in
`marketplace_sellers` — `bot.py:91341-91348` reads the seller row and returns 400 with no seller,
403 when `status != "approved"`.

There is **no** `merchant_id` or `store_id` on the live product row. The cj-discovery map warned
about exactly this ("do not claim a new tenant/store identifier already exists"); the warning is
correct. Tenancy today is a user id.

## 4. Money is stored as prose, and only one of the two write paths normalizes it

`price_label` is free text. The create route takes it raw:

```python
price = clean_html(payload.get("price_label") or "Request access")[:80]   # bot.py:91316
```

Every read that needs an amount re-derives it by regex: `parse_price_label_to_cents`
(`bot.py:4362`), called at checkout (`services/marketplace_cart_routes.py:228`) and on
payment paths (`bot.py:89565, 89572, 89579`).

There *is* a canonicalizer — `marketplace_normalize_price_label` (`bot.py:4380`) — whose
docstring states the intent precisely:

> the stored label is rebuilt from the parsed minor units so the text the seller sees and the
> amount the buyer is charged can never drift apart

It has exactly one caller: **`bot.py:53933`, the edit path.** The create path does not call it.

So the round-trip invariant binds when a seller edits a listing and not when a seller creates
one. This asymmetry pre-dates this mission and is load-bearing for it: **an importer is
create-shaped, so it would enter through the one path where the price invariant does not hold.**
The parser is defensive — it consumes thousands separators (a fixed bug noted in its own
comment, where `"$2,500.00"` charged $2.00), floors negatives to 0, and caps at
`MAX_PRICE_LABEL_CENTS` — but "defensive parse of merchant prose" is not a foundation for
computing margin from a supplier cost.

## 5. Inventory has no "unknown"

`inventory_available` (`services/marketplace_listing_lifecycle.py:45`):

```python
raw = listing.get("quantity")
if raw is None:
    return False
```

and the SQL mirror in `public_sql` (`:118`) uses `COALESCE(l.quantity,0)>0`.

**NULL means out-of-stock, not unknown.** Stockless product types
(`digital|course|service|event|booking`) short-circuit to available.

This is a real gap and a sharp one. Supplier-sourced stock is *routinely* unknown — the last
sync failed, the provider is rate-limiting, the item is new. Today the schema can only say
"zero," which silently converts a sync outage into a storefront that looks sold out. The
distinction the mission asks for (unknown ≠ zero) genuinely does not exist and cannot be
expressed by the current column.

## 6. Draft-before-live already exists, and is enforced at read

Mission §3 says: *NEVER: IMPORT → LIVE PRODUCT IMMEDIATELY.*

The repository already guarantees this, structurally. Creation cannot produce a live product:

```python
status = "draft" if submission_action == "draft" else "pending_review"          # bot.py:91364
approval_status = "draft" if submission_action == "draft" else "pending_review" # bot.py:91365
```

and buyer visibility requires both fields to have advanced (`is_public`,
`marketplace_listing_lifecycle.py:70`):

```python
normalized(listing.get("status")) in PUBLIC_STATUSES
and normalized(listing.get("approval_status")) in APPROVED_STATES
and normalized(listing.get("seller_status")) == "approved"
and not seller_identity_missing(listing)
and inventory_available(listing)
```

This is the important part: the guarantee lives in the **read predicate**, not in the writer's
discipline. A new import writer cannot accidentally publish, because publication is not
something a writer does — it is a property the reader tests. `public_sql` (`:105`) is the SQL
equivalent used by discovery, and `public_denial_code` (`:82`) gives buyers a stable reason.

**§3 requires no new mechanism.** An importer must simply write a row like any other create,
and it is already invisible. What §3 *would* need is a check that the two predicates stay in
lockstep — `is_public` and `public_sql` are the classic two-reader pair, defined separately.

## 7. Per-requirement verdicts

| Requirement | Verdict | Evidence |
|---|---|---|
| Canonical product authority exists | **EXISTS** | `marketplace_listings`, sole writer `bot.py:91374`, only populated ledger (§2) |
| Merchant/store tenant identifier | **GAP** | ownership is `seller_user_id`; no `merchant_id`/`store_id` on the live row (§3). `create_quote` accepts `store_id` (`marketplace_quote_service.py:23`) and no call site passes it |
| Publication state machine | **EXISTS** | `status` + `approval_status`, `is_public` (§6) |
| Import must not go straight live | **ALREADY SATISFIED** | enforced at read, not write (§6) |
| Fulfillment *kind* (shipping/digital/service/…) | **EXISTS** | `services/marketplace_fulfillment.py:42-53`, 11 kinds; `STOCKLESS_KINDS` at `:60`. This is a *logistics* kind — how a thing reaches a buyer — not where stock comes from |
| Fulfillment *mode* (stocked vs dropship) | **GAP** | no column; grep for `fulfillment_mode` → 0 hits |
| Product origin / supplier / provider identity | **GAP** | grep `supplier_id\|provider_product_id\|dropship` across `bot.py services/ migrations/` → 0 hits |
| External / import identity (`external_id`) | **GAP** | grep `external_product_id\|source_product_id\|import_source` → 0 hits |
| Cost price (for margin) | **GAP** | grep `cost_price\|cost_cents` → 0 hits; retail is prose (§4) |
| Addressable variants | **GAP** | **no variant table exists** — grep `CREATE TABLE …variant` across `bot.py services/ migrations/` → 0 hits. Only a JSON list in `listing_metadata_json`, capped at 12, physical listings only, each entry reduced to exactly `{"name","value"}` (`services/marketplace_listing_types.py:186-199`) — no id, SKU, price, or stock |
| Inventory: unknown ≠ zero | **GAP** | NULL → False (§5) |
| Media with explicit cover | **EXISTS** | `marketplace_product_media`, `bot.py:109278`; cover bound at `bot.py:91367` |
| Shipment / fulfillment entity | **GAP** | grep `CREATE TABLE …shipment\|fulfillment` → 0 hits. One order = one seller = one implicit shipment |
| Weight / dimensions / ship-from / HS code | **GAP** | grep `weight_grams\|length_cm\|hs_code\|ship_from\|origin_country` → 0 hits |
| Supplier-side return leg | **GAP** | returns are buyer↔seller only *(unverified)*; no way to model "refunded the buyer, must recover from the supplier" |
| Field-level ownership / override tracking | **GAP** | grep `override_json\|field_owner\|owned_fields\|locked_fields` → no commerce hits *(unverified)* |
| Supplier credentials provisioned | **ABSENT** | no `CJ_*`, `PRINTFUL_*`, `PRINTIFY_*`, `SUPPLIER_*` variable exists on Railway |

## 8. The four gaps that actually block dropshipping

Ranked by how much else depends on them.

1. **No addressable variant.** A supplier product is a set of variants; that is the unit that
   has a SKU, a cost, a stock level, and a provider id. PulseSoc's product is flat — the nearest
   thing is a 12-entry `{"name","value"}` JSON list that the validator strips to exactly those
   two keys (`marketplace_listing_types.py:194-197`), so it cannot carry an id even if a caller
   sent one. Every other supplier field has nowhere to attach until this exists. This is the
   foundation, and the largest single piece of missing schema.

   Corroborating detail: `create_quote` already accepts `variant_id` and `store_id`
   (`services/marketplace_quote_service.py:23-24`) and writes `variant_id` into the quote payload
   (`:62`). **All three call sites pass neither** — `bot.py:89629`, `marketplace_cart_routes.py:752`,
   `marketplace_offers_routes.py:551`. Both seams were anticipated and left empty; the money path
   is already shaped to carry a variant and a store, and nothing fills them.
2. **No provider identity, and money as prose.** Import requires a stable provider-side key to
   re-sync against, and an integer cost to compute retail from. Neither exists, and the one
   normalizer that protects retail price is not on the create path (§4).
3. **Inventory cannot say "unknown."** Without it, every sync failure is indistinguishable from
   a sell-out (§5).
4. **No shipment entity.** A supplier order that splits across warehouses has nowhere to live;
   today the model is one order, one seller, one implicit shipment.

## 9. What must not be built

- **No `Product V2`, `DropshippingProduct`, or `CJProduct` table.** There are already four
  product ledgers and three are empty. A fifth is the failure mode this mission names.
- **No writes to `business_os_mkt_products` / `business_os_store_products` as if canonical.**
  They are enabled and empty; treat them as a parallel stack, not a target.
- **No new publication gate.** §6 shows one exists and is stronger than a write-side check.
- **Nothing in Agora / audio / video / livestream / call paths.** RTC hard lock, mission §65.

## 10. Not established

Stated so it is not mistakenly relied on later:

- Whether the business_os commerce stack is *intended* to become canonical. It is enabled in
  production and empty. That is consistent with "staged for migration" and with "abandoned."
  Nothing I read decides it, and the row census cannot distinguish them.
- Whether the 6 live listings are real merchandise or test rows. Not inspected — deliberately,
  as row contents are user data and the census answered the structural question without them.
- Whether `is_public` and `public_sql` currently agree on every input. They are a two-reader
  pair defined independently; I read both and saw no divergence, but I did not test it.
- Every claim marked *(unverified)* in §7 comes from a sub-agent survey and was not confirmed
  at the cited line by me. Two remain: the buyer↔seller-only return model, and the absence of
  field-level override tracking. Both are *absence* claims supported by repo-wide greps I ran,
  so the risk is a differently-named mechanism, not a missed hit.
- One sub-agent conclusion was **wrong and is corrected here**: it inferred from `.env.example`
  that the business_os commerce stack is "built but dark." The flags are truthy in production
  (§2). Its verdict — that stack is not canonical — happens to survive, but for the opposite
  reason: not unreachable, just empty. Reasoning from `.env.example` to production behaviour is
  invalid in this repo and should not be repeated.

## 11. Standing correction to CLAUDE.md

`CLAUDE.md` says "i18n is gated — hardcoded strings fail CI." Verified false, twice over:

- `.github/workflows/` contains only `crypto-alert-persistence.yml` and `realtime-audio.yml`,
  and neither mentions `verify` or `i18n`. **No workflow runs the gate at all.**
- Even locally the gate is partial: `mobile-native/package.json:26` defines
  `"verify": "npm run typecheck && npm run i18n:validate && npm test"`. The hardcoded-string
  detector is a *separate* script (`"i18n:hardcoded"`, `:25`) that `verify` never invokes.

This matches the existing memory note that `protection.yml` does not exist. Do not rely on CI to
catch untranslated commerce strings — new commerce UI must be reviewed for them by hand.
