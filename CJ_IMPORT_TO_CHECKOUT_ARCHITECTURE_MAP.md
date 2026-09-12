# CJ → PulseSoc: import to checkout, stage by stage

Every claim here was measured — by running the chain against a temp database, or
by querying production read-only — not read off a screen. Where a stage is
untested or unreachable it says so rather than guessing.

Date: 2026-09-10. Production has exactly one supplier-sourced listing (id 14,
the CJ upholstered bed, `CJFU2755187`), one variant, zero orders.

---

## The one-line summary

A supplier product is not a sellable listing. Between the two sit eleven stages,
two separate price representations, two separate media representations, and one
authority (moderation) that the merchant does not control. The pipeline was
broken at the seam where the merchant's world hands off to the buyer's.

---

## Stage table

| # | Stage | Source of truth | Owning service | Owning table |
|---|-------|-----------------|----------------|--------------|
| 1 | CJ catalog | CJ | `services/business_os/suppliers/cj.py`, `discovery.py` | — (remote) |
| 2 | Supplier DTO | normalizer | `services/business_os/suppliers/normalize.py` | — (in memory) |
| 3 | Import cart | merchant selection | `services/business_os/suppliers/import_cart.py` | `business_os_supplier_import_cart` |
| 4 | Import | importer | `services/business_os/suppliers/importer.py` | `marketplace_listings` + `marketplace_product_sources` + `marketplace_listing_variants` |
| 5 | Merchant review | merchant | `services/business_os/suppliers/drafts.py` | as above |
| 6 | Publication gate | `drafts._validate` | `services/business_os/suppliers/drafts.py` | — |
| 7 | Publish | `drafts.publish` | `services/business_os/suppliers/drafts.py` | `marketplace_listings.status/quantity/price_label/cover_image_url` |
| 8 | Moderation | **separate authority** | admin surfaces | `marketplace_listings.approval_status` |
| 9 | Buyer discovery | `lifecycle.is_public` / `public_sql` | `marketplace_listing_lifecycle.py` | `marketplace_listings` ⋈ `marketplace_sellers` |
| 10 | Cart | `price_label` | `marketplace_cart_routes.py` | `marketplace_cart_items` |
| 11 | Checkout → order | Stripe + `pulse_upsert_marketplace_order` | `bot.py`, `marketplace_cart_routes.py` | `seller_transactions` → `marketplace_orders` |
| 12 | Supplier fulfillment | merchant-initiated | `services/business_os/suppliers/fulfillment.py` | `business_os_supplier_fulfillment_intents` |

---

## The seam that was broken

**The merchant and the buyer store price in different places, and nothing joined
them.**

- Merchant prices `marketplace_listing_variants.price_cents` (via
  `drafts._set_prices`, which deliberately writes that column and no other, so a
  price edit cannot blank supplier cost).
- Buyer is charged from `marketplace_listings.price_label`, parsed by
  `bot.parse_price_label_to_cents`, in `cart_routes._listing_price_minor`.
- `marketplace_variants` is imported by the supplier package **and by nothing
  else** — verified by grep. The buyer has no variant selector and no path to
  the merchant's number.

Measured before the fix, on the real chain (import → price every variant at
$20.00 → publish → approve):

```
price_label   : ''
is_public     : True          <- publicly discoverable
denial_code   : ''            <- nothing reported wrong
cart charges  : 0 minor units
add-to-cart   : REFUSED "This item is not priced for checkout."
```

Publicly listed, unbuyable, no signal to the merchant, and a message to the buyer
that blames the item. **Every dropship product ever published ended there.**

Fixed in `202c6b26`: `publish()` writes `price_label` from the variants. Measured
after: `cart charges 2000 minor units, add-to-cart accepted`.

Publishing can only write a price it could honestly honour, so three states the
gate used to wave through are now named:

| code | what it stops |
|---|---|
| `MISSING_PRICE` (tightened `all` → `any`) | a blank variant being sold at its neighbour's price |
| `VARIANT_PRICE_SPREAD` (new) | one listing-level price silently chosen from several |
| `PRICE_ABOVE_CHECKOUT_LIMIT` (new) | `parse_price_label_to_cents` **clamps** to `MAX_PRICE_LABEL_CENTS`; `_set_prices` accepts 10× that, so between the limits the card is charged $999,999.99 |

Repricing a *live* listing writes the label too — otherwise the draft screen
shows the new number while the cart keeps charging the old one.

---

## Blockers, by stage

**Publication blockers** (`drafts._validate`, all reported at once, never one at
a time): `MISSING_TITLE`, `MISSING_CATEGORY`, `NO_VALID_MEDIA`,
`NO_VARIANTS_SELECTED`, `MISSING_PRICE`, `VARIANT_PRICE_SPREAD`,
`PRICE_ABOVE_CHECKOUT_LIMIT`, `NEGATIVE_MARGIN`, `UNKNOWN_INVENTORY`,
`SUPPLIER_DISCONNECTED`, `PROVIDER_PRODUCT_UNAVAILABLE`, `RESTRICTED_PRODUCT`.

**Discovery blockers** (`lifecycle.public_denial_code`): `SELLER_UNAVAILABLE`
(includes a seller with no public store name), `ITEM_UNAVAILABLE`,
`OUT_OF_STOCK`.

**Checkout blockers** (`cart_routes`): own listing, cart full, `price_minor <= 0`,
goods policy, below Stripe's minimum charge, fulfillment lane unchosen.

**Fulfillment blockers** (`suppliers/fulfillment.create_intent`):
`connection_not_ready`, `shop_binding_required` (connect no longer requires
choosing a CJ shop; fulfillment does, because `_validate_observed` proves a
placed order came back on the shop we bound), sandbox assertion.

---

## Where the real listing actually stands

Production listing 14 — the CJ upholstered bed, SKU `CJFU2755187` — measured by
running the real evaluator against production's own rows
(`scripts/measure_listing14_readiness.py`, read-only):

| gate | field | state |
|---|---|---|
| `_validate` | all twelve codes | **clear — `publishable: True`** |
| variant | `price_cents` 46574 / `cost_cents` 29109 | priced, `HEALTHY` margin |
| variant | `stock_state` `IN_STOCK`, qty 132 | `AVAILABLE` |
| supplier | `sync_state` `SYNCED`, `DROPSHIP` | connected |
| seller | `status` `approved`, store `M&W Store` | passes `public_sql` |
| listing | `status` `draft` | **needs `drafts.publish`** |
| listing | `approval_status` `pending_review` | **needs moderation** |
| listing | `price_label` `''`, `quantity` 0, `cover_image_url` NULL | all three written by `publish` |

So the product is not blocked on anything the merchant has failed to supply. It
is two state transitions from a buyer: **publish**, which fills the three
buyer-facing columns in one statement, and **moderation approval**, which is a
separate authority on purpose. After both, `is_public` is `True`,
`public_denial_code` is `""`, and the label parses to 46574 cents — the
transition is asserted end to end by
`test_a_published_approved_import_is_purchasable_on_every_field_a_buyer_reads`.

**Both transitions have since been made** (2026-09-11, user-authorised,
`scripts/publish_and_approve_listing14.py`). Measured after, against the real
discovery predicate rather than a page:

| asked | answer |
|---|---|
| `public_sql` returns id 14 | **yes**, alongside 8 and 13 |
| `is_public` | `True` |
| `public_denial_code` | `""` |
| `price_label` / `quantity` / `cover_image_url` | `$465.74` / 1 / set |

The listing is live. Note what it took: the approval half could not be done
through the admin UI at all, for the reason in "The fifth seam" below.

Two numbers in that last row were the seventh seam, sitting in plain sight for
three passes over this document: **`quantity` was 1 against 132 units in CJ's
warehouse**, and `provider_variant_id` on its source row was NULL, which
`create_intent` refuses outright. So the listing was live, approved, findable,
buyable — and sold one unit of something nothing could ship. Under the guard
added in that pass it also stopped being `publishable`, which is the correct
answer to what it was.

**Repaired 2026-09-12, user-authorised, `scripts/repair_listing14_binding.py`**
(dry-run by default; it refuses outright on a listing carrying more than one
variant, because which one it sells is the merchant's choice). It binds through
`gateway.bind_product` — which reads the product from CJ and refuses unless the
pair really exists there, so the binding is verified against the supplier rather
than asserted by the script — then re-publishes so the shelf's numbers are the
package's own. No CJ order, no spend. Measured after, through the functions that
gate the two things that were broken rather than through the script's own report:

| asked | before | after |
|---|---|---|
| `get_product_binding` | `product_binding_required` | returns the vid — `create_intent` can resolve it |
| `_validate` on production's rows | `['SUPPLIER_VARIANT_UNBOUND']` | `publishable: True`, no problems |
| `inventory_available(listing, 132)` | `False` | `True` (and 133 still `False`) |
| `public_sql` returns id 14 | yes | yes, `quantity` 132 |

`published_at` was restored afterwards. `publish` stamps it with now, which is
true of a new publication and false of this one — the product went on sale
earlier and never came off.

---

## Things that are correct and easy to break

- **Publish does not self-approve.** `is_public` needs status *and* approval;
  a publish path that set `approval_status` would be a merchant self-certifying
  past review.
- **Published quantity counts confirmed variants, not supplier stock.** The
  supplier's warehouse is not ours to oversell. A supplier outage lowers the
  number toward zero rather than inventing stock.
- **UNKNOWN ≠ out of stock.** All-unknown blocks publication; one confirmed
  variant is enough, the rest are simply not offered.
- **Unknown cost yields unknown price and unknown margin** — never zero, never
  100%. `suppliers/pricing.py` returns `None` throughout and names margin states
  rather than returning bare numbers.
- **Cost is merchant-private.** It reaches the draft screen and no buyer surface.
- **The reviewer's risk number is reviewer-private**, and `safety_score` is that
  number despite its name. It is stripped in
  `MARKETPLACE_REVIEWER_ONLY_FIELDS`, not merely left out of a field list —
  see the third seam for why those are not the same thing. Do not "restore" it
  to a buyer surface in inverted form either: moderation's approval is what a
  buyer is entitled to rely on, and a 0-100 integer they cannot check is not.

---

## The second seam: the number the app promises

The first seam was between two *tables*. The second is between two *languages*,
and it was worse, because this one had a comment claiming it did not exist.

`marketplaceListingPriceMinor` in the app puts an amount on the Pay button.
`parse_price_label_to_cents` on the server builds the Stripe charge. Both read
the same `price_label`, and the app's docstring said it used "the same regex".
It did not: the server's consumed thousands separators and the app's stopped at
the comma. The server *writes* separators —
`marketplace_normalize_price_label` formats with `,` — so every listing at or
above $1,000 was affected. Measured:

```
label          app shows      server charges
$999.99        99999          99999          <- correct
$1,000.00      100            100000         <- 1000x
$12,345.67     1200           1234567        <- 1000x
```

Above a comment reading "that number *is* the charge, not a running estimate".
Only Buy Now: the cart path totals `price_snapshot_minor`, a server number.
Every existing test of the function used $5.00 and $12.50.

Fixed in `4858385f`. The parity claim is no longer a comment — sixteen labels
live in `mobile-native/src/api/__tests__/fixtures/priceLabelParity.json`, read by
the app's suite and by `tests/test_marketplace_price_label_parity.py`. Changing
either parser alone reddens one suite; changing the table alone reddens both,
verified by doing it.

---

## The third seam: the number that meant the opposite of its name

The first seam was between two tables, the second between two languages. This
one is between a column and its own name, and it reached the buyer.

`marketplace_listings.safety_score` holds **risk**. All three writers store
`revenue_safety_engine.score_text(...)["risk_score"]` unchanged, the column
defaults to `0`, and the admin queue counts `safety_score >= 30` as risky — the
storage side is coherent. The teacher-application route, ~200 lines further down
`bot.py`, writes `max(0, 100 - risk_score)` into a column of the same name on a
different table. That is where the confusion came from.

Four buyer paths read it as safety: the server-rendered grid card, its
client-side twin used for search, the product page, and
`pulse_marketplace_listing_payload`, which put the raw number on the app's wire.
Measured over the real engine:

```
listing                                  risk   the pill said
Handmade oak dining table                   0   Safety 0
CJ upholstered bed (listing 14)             0   Safety 0
Crypto seed phrase vault                   44   Safety 44
Guaranteed profit trading bot (blocked)   100   Safety 100
```

The worst listing the engine can score advertised the best number. Production
has 7 listings, all scoring 0, so the two published rows read "Safety 0" and the
inversion was latent rather than realized.

Fixed in `9e5eec71` by **removal, not inversion**: a raw moderation integer is
not a buyer concept in either direction, and a listing only reaches these
surfaces because moderation approved it — the approval *is* the signal. The
merchant keeps the number, under a header that says "Review risk", on a page
that already prints a correctly-named "Risk Score".

One detail is worth more than the bug. `pulse_marketplace_listing_payload`
returns `{**row, ...}` — **its explicit keys are additions to the database row,
not a whitelist of it.** Deleting the `"safety_score"` line changed nothing;
the column was still on the wire, because a caller's `SELECT` put it there.
Only asserting against the served response caught that. Every column any future
query names is buyer-visible by default, so the strip now lives in
`MARKETPLACE_REVIEWER_ONLY_FIELDS` rather than in a field list.

---

## Known gaps, not yet fixed

1. ~~**`lifecycle.is_public` has no price condition.**~~ Resolved in `d5e68151`,
   but not by adding one. Unpriced listings are browsable *on purpose* — the
   serializer returns `""` rather than inventing a phrase, and both screens
   print no price line. Hiding them would delete that. What was wrong is that
   the app then offered to sell them: `canPurchaseMarketplaceListing` had no
   price condition, so an unpriced listing on a full shelf showed an enabled
   "Add to cart" and the server answered 400 `ITEM_UNAVAILABLE` — or, via Buy
   Now, took a delivery address first. `is_public` is left alone deliberately;
   *visible* and *buyable* are different questions and the code now says so.
2. ~~**No buyer-side variant selection.**~~ Recorded as a missing UI, which was
   the wrong diagnosis — it described the feature a shopper would notice and not
   the thing that was broken. A dropshipped listing sells **exactly one**
   supplier variant, the one `marketplace_product_sources.provider_variant_id`
   names, and until now no layer said so: publish priced the whole set, stocked
   the shelf with a count of that set, and the column that names the variant was
   NULL on every listing in production. See "The seventh seam" below. What is
   still owed is genuinely a selector — a merchant who wants to sell three
   colours needs three listings today, and `VARIANT_PRICE_SPREAD` still refuses
   the one-listing-many-prices shape rather than guessing — but that is a
   feature, not a defect, and it is no longer what stands between an imported
   product and a shippable order.
3. ~~**Production listing 14 has `cover_image_url = NULL`**~~ — the column is
   still NULL, but recording it that way described a symptom and hid the seam
   underneath it; see "The fourth seam" below. The structural half is fixed:
   `publish` now writes the column, so no dropship listing can become
   buyer-visible with a cover the buyer cannot see. The window *before*
   publication is now closed too; see "The ninth seam" below. The backfill script
   turned out to be moot — run dry against production it reports
   `0 listing(s) with metadata media and no cover column`, because the listing-14
   repair re-ran `publish`, which writes the column. **No production write was
   made, and none is needed.** What the backfill would have papered over was a
   reader disagreement, and that is what got fixed instead.
4. ~~**`vault.seal`/`unseal` wrap everything in `except Exception: raise
   VaultError() from None`**~~, which turned a caller's mistake into what looked
   like an infrastructure failure. Cost me a false alarm already — and the false
   alarm was the smaller half. See "The eighth seam" below.
5. ~~**Fulfillment is unreachable for the current connection**~~ —
   `external_shop_id` is unset, so `create_intent` raises
   `shop_binding_required`. The refusal is honest and is unchanged. What was
   wrong is that it was *terminal*: nothing could ever set the column on a
   connection that already existed. Fixed via `connections.bind_shop`; see "The
   sixth seam" below. §37 still forbids placing a real CJ order, and this
   changes nothing about that — the sandbox path is what these tests exercise.
6. ~~**`bot.py:4379-4393` documents a constant that no longer exists**~~, and
   said native "already says 'Price at checkout' on the same card" — which
   native had stopped doing. Rewritten once as prose that enumerated the six
   surfaces printing nothing instead, and the enumeration was accurate. It was
   also incomplete, which is the only way a hand-written list ever fails. See
   "The tenth seam" below: the checkout screen was a seventh surface, and it was
   inventing an amount on the receipt. Filed as cosmetic; it was not.
7. ~~**A published dropship listing cannot be approved.**~~ The admin Approve
   button 409'd on every listing `drafts.publish` produces, which is why listing
   14 needed a hand-written UPDATE. Fixed via `lifecycle.awaiting_moderation`;
   see "The fifth seam" below. The route now has tests, which it did not before.
   ~~That fix left the opposite half standing: Approve **accepting**, answering
   200, writing an audit entry, and producing nothing.~~ Three of four approval
   shapes went that way — suspended seller, unnamed storefront, quantity 0 —
   while the merchant's own dashboard read "Live" and no surface named the
   reason, which `public_denial_code` had known all along. See "The eleventh
   seam" below.
8. ~~**Buy Now charges for one unit no matter how many the buyer picked.**~~ Not
   found by reading this list — there was no eighth entry to read. The product
   screen's stepper multiplied the unit price out for display, the checkout
   summary showed the multiplied total, and `openMarketplaceCheckout` sent a
   body with no quantity field in it at all. The server had no parameter to
   read: it priced one unit, charged one unit, took one unit off the shelf, held
   one unit, and wrote `quantity: 1` into `marketplace_orders` — the column
   `fulfillment.create_intent` compares a supplier line against, so no
   multi-unit dropship order could ever be dispatched. Fixed in both lanes; see
   "The twelfth seam" below.
9. ~~**`marketplace_listings.delivery_type` defaults to `'digital'`.**~~ The column
   is `TEXT DEFAULT 'digital'` (`bot.py:112435`) and `resolve_kind` read it
   before the metadata, so a row written without it is a digital order: no
   address collected, no stock decrement, no reservation. Found while building a
   fixture for gap 8, which is the only reason it is written down: the probe
   listing was physical and the route called it digital. Filed as a legacy-rows
   concern; measuring it for the fix showed it was the smaller half of something
   much larger, because the column does not hold a delivery lane *at all*.
10. ~~**The delivery-options fallback in `resolve_kind` is unreachable.**~~ Because
    `delivery_type` is always populated, `option = delivery or meta.get("delivery_options")`
    can never reach its right-hand side. A seller who sets `delivery_options: "both"`
    could therefore never produce `shipping_or_pickup`, the lane chooser never
    appeared, and a pickup-only buyer was silently placed on shipping — precisely
    the outcome `resolve_choice`'s own comment says it exists to prevent. The
    docstring claimed it reads the metadata "rather than the delivery column
    alone", which the code could not do. `mobile-native/src/api/marketplaceFulfillment.ts`
    mirrored the bug faithfully. Fixed in lockstep across both languages and all
    six derivations; see "The thirteenth seam" below.
11. **The buyer's order timeline reads the same column.**
    `mobile-native/src/api/ordersDashboard.ts:216-234` — `variantOf(deliveryType)`
    switches on a `delivery_type` taken off the *order* payload, served from
    `services/marketplace_returns_routes.py:295`, so every order's progress
    strip reads as shipped whatever lane it was actually placed on. Not folded
    into gap 10: the order payload does not carry a resolved lane, and inventing
    one on the client would be a seventh derivation of the fact the thirteenth
    seam exists to stop having seven of. The fix is a server field.

---

## The fourth seam: two media stores, and the gate watched the wrong one

The merchant's side keeps media as an ordered list in
`listing_metadata_json.media`. That is what `_media_of` returns, and it is the
only thing `_validate` sees when it decides whether `NO_VALID_MEDIA` applies.

Every buyer surface renders the **column**. `pulse_marketplace_listing_payload`
assembles its media from `marketplace_product_media`, `cover_image_url`,
`media_url` and `gallery_json`, and consults the metadata list for nothing.

So the two halves were: a gate whose stated purpose is "better to refuse than to
ship a black card", and a buyer surface it was not looking at. A draft with five
photos passed the gate and published a card with none — the exact outcome
`NO_VALID_MEDIA` exists to prevent, reached through a *passing* validation.

Measured, not inferred. Running the real evaluator against production listing
14 — a CJ upholstered bed, SKU `CJFU2755187` — over production's own rows:

| what was asked | answer |
|---|---|
| `_media_of(listing)` | 5 `cf.cjdropshipping.com` URLs |
| `_validate(...)["publishable"]` | **`True`** |
| `marketplace_listings.cover_image_url` | **`NULL`** |
| `marketplace_product_media` rows | **0** |
| `gallery_json` / `media_url` / `video_url` | all `NULL` |

`2b9a36a2` had already made `importer._insert_listing` write both from one list,
and `update_draft` already did, so a draft imported by current code was safe.
That fix addressed the writers. It did not stop the gate from validating a
different store than the one it is guarding, which is why a row written by the
*old* importer still walked through it.

`publish` now writes `cover_image_url` from `media[0]`, by the same argument and
in the same statement as `price_label`: publication is where a product crosses
from the merchant's world into the buyer's, and both fields are one fact stored
on either side of that crossing. Three tests pin it, and two mutants — dropping
the column from the `UPDATE`, and writing `media[-1]` — are killed by them.

A third media gate exists and reads a third store: the ordinary seller route
`/api/pulse/marketplace/seller/listings/<id>/submit` requires a
`marketplace_product_media` row with `is_cover=1`. Dropship listings do not go
through it — `drafts.publish` is the only function in this pipeline that moves
`status` off `draft` — but it is worth knowing that "does this product have a
picture" currently has three implementations over three tables.

---

## The fifth seam: publication produced a state moderation refused to act on

Publication and moderation are two axes, and `is_public` requires both. So
`drafts.publish` sets `status='published'` and leaves moderation untouched —
correct, and it says so in its own return value, `awaiting_moderation: True`.
The listing is invisible to buyers until a moderator approves.

`/admin/marketplace-command` is the only moderation surface, and its approve
action asked the wrong axis:

```python
if action in {"approve", "reject", "request_changes"} and \
        previous_status not in {"pending_review", "review_ready"}:
    return api_error("Listing review state changed. Reload before deciding.", 409)
```

`published` is not in that set. So the Approve button returned 409 on exactly
the listings the supplier package produces, and nothing in that package moves a
published listing back to `pending_review`. The state was terminal in both
directions: no moderator could decide it, no merchant could leave it.

| shape | who produces it | Approve, before | after |
|---|---|---|---|
| `pending_review` / `pending_review` | seller submit route | 200 | 200 |
| `published` / `pending_review` | **`drafts.publish`** | **409** | **200** |
| `draft` / `pending_review` (column default) | importer | 409 | 409 |
| `published` / `approved` | already decided | 409 | 409 |

The guard is a *staleness* guard — it stops a moderator deciding from a page
loaded before someone else changed the row — and that is worth keeping. It was
reading "has a decision been recorded" off `status`, where the answer does not
live.

The fix is `lifecycle.awaiting_moderation`, and it is a conjunction rather than
the obvious swap. Keying on `approval_status` alone would be worse than the bug:
the column is `DEFAULT 'pending_review'`, so every untouched draft — unpriced,
no cover, quantity 0 — would read as awaiting review and a moderator could
publish it in one click, bypassing `_validate` entirely. So: no decision
recorded **and** the merchant has released it. Row 3 of that table is the line
being held, and `test_an_untouched_draft_is_still_not_approvable` is what holds
it.

The same predicate now feeds the queue's pending count, which had the same bug
independently: it asked `status` alone, so it could not see a dropship listing
awaiting review and would show a moderator zero work beside a listing it would
have accepted a decision for.

---

## The sixth seam: a refusal with no way to satisfy it

`create_intent` refuses to place a supplier order when the connection has no
bound CJ shop, and the refusal is right. `_validate_observed` proves a placed
order came back on *the shop we bound*; with nothing bound there is no such
proof to make, and skipping the check would look identical in every green test
and differ only in what it would accept.

What was wrong is that nothing could satisfy it. `external_shop_id` had exactly
one writer — `connect_cj` — and `connect_cj` refuses to change an existing
binding:

```python
if (existing["merchant_id"] != merchant or existing["external_account_id"] != account_ref
        or existing["external_shop_id"] != external_shop_id):
    raise SupplierConnectionError("Existing CJ account or shop cannot be silently replaced.",
                                  409, "connection_binding_conflict")
```

`""` is a value for the purpose of that comparison, so reconnecting *with* a
shop is refused as a replacement. The two rules are each correct and together
they close the door: the only account that could ever fulfil is one that named
its shop in the connect form, before it had any reason to know which shop it
wanted — and naming one there was made *optional* on purpose, because requiring
it made an ordinary CJ account unconnectable. Every connection created the
normal way could import, publish and sell, and then refuse every one of its own
orders, permanently.

Same shape as the fifth seam. One path produces a state; another path refuses to
act on it; no transition exists between them. There the state was
`status='published'`/`approval='pending_review'`; here it is
`external_shop_id=''`.

**The fix is `connections.bind_shop`** — bind a shop on an already-connected
connection, none-to-one only, verified live against the credential already in
the vault. The refusal in `create_intent` is untouched; this is its other half,
establishing the provenance it demands instead of assuming it away. A companion
`connection_shops` lists the choices, because `discover_shops` takes an API key
and after connecting the merchant has no copy of theirs to retype.

### The half that would have replaced one trap with another

Verifying the shop is *listed and active* — the check `connect_cj` performs — is
not enough, and shipping only that would have been the same defect one step
later. `dispatch` demands three things of the bound shop, and it demanded them
in a place nobody else could see:

```python
selected = [s for s in shops if s.get("shop_id") == intent["external_shop_id"] and s.get("status") == 1]
if len(selected) != 1 or not selected[0].get("name") or str(selected[0].get("platform")).lower() != "api":
    raise FulfillmentError("api_shop_binding_required")
if len([s for s in shops if s.get("name") == selected[0]["name"]]) != 1:
    raise FulfillmentError("ambiguous_shop_name")
```

A CJ "shop" may be a Shopify or Woo storefront. Only the one CJ's own API app
creates can receive an order placed over the API, and CJ addresses the order by
shop *name*, so two shops sharing a name have no unambiguous destination. A
merchant binding their Shopify storefront would have been told yes, and would
have found out at the first order — which is the wrong place to learn it.

So the predicate is now `fulfillment.dispatch_shop`, called by `dispatch` where
it always was and by `bind_shop` where the choice is made. Same conditions,
measured at both surfaces.

| shop shape | bind before | bind after | dispatch |
|---|---|---|---|
| listed, active, platform `API`, unique name | *unreachable* | accepted | sends |
| listed, active, platform `Shopify` | *unreachable* | `api_shop_binding_required` | `BLOCKED` |
| listed, `status != 1` | *unreachable* | `api_shop_binding_required` | `BLOCKED` |
| two shops sharing a name | *unreachable* | `ambiguous_shop_name` | `BLOCKED` |
| not listed under this credential | *unreachable* | `shop_not_authorized` | — |
| connection already bound to another shop | *unreachable* | `connection_binding_conflict` | — |

The write is conditional on the column still being empty
(`AND COALESCE(external_shop_id,'')=''`), so two concurrent binds cannot both
believe they won and "never silently replaced" is an atomic property rather than
a read-then-write hope. None-to-one is safe precisely *because* of the refusal
this seam is about: an intent cannot exist while the column is empty, so there
is no persisted intent whose `external_shop_id` a first bind could invalidate.

`tests/business_os/test_cj_shop_binding.py` asserts the whole path — refusal,
the failed reconnect that proves the trap was closed, bind, order, dispatch —
because that is the claim. Seventeen tests, four of them added with the screen
below, which is what forced the shop *list* to be read as carefully as the bind.

### Reachability, applied to the fix itself

The rule this seam produced says a guard is unfinished until something can
satisfy it, so it applies to `bind_shop` too. Grepping the callers of
`suppliers/cj` found exactly one — `mobile-native/src/api/dropshipping.ts` — and
it has no shop-binding call. A routed, tested service function nothing can reach
is the same dead end one layer up.

`scripts/cj_bind_shop.py` is the operator surface, on the
`publish_and_approve_listing14.py` pattern: dry run by default, `--apply` to
write, scope from argparse or environment and never a literal. It lists shops
with the dispatch predicate's verdict already applied, refuses locally before
calling `bind_shop` if the chosen shop is not fulfillable, and places no
supplier order. A merchant-facing screen was still owed; the section after next
is that screen.

### What production actually answers

Running that dry run against the live connection did not produce a shop list. It
produced CJ's refusal:

```
CJ refused the shop list: SUPPLIER_REJECTED endpoint=shop/getShops provider_code=0
```

Everything on our side worked — authorization, vault unseal, credential
hydration, the live call. CJ answered `shop/getShops` with business code `0`,
which is the case `_verify`'s docstring already describes: an account owning no
external storefront is answered with a code CJ's own documentation does not
list. Connecting survives it deliberately (importing needs no shop). Binding
cannot, because there is nothing to choose from.

So the *code* blocker is closed and the remaining one is external: the CJ
account owns no API-platform shop. Creating it is the same CJ console path the
API key came from — Apps → install **API** → Add API — and it is a merchant
action, not a deploy. Until it exists, `create_intent` will keep answering
`shop_binding_required`, and that answer is now correct rather than terminal.

### The screen, and the four defects shipping it naively would have added

Building the merchant surface meant measuring what a merchant would actually
meet on the way to it, not just wiring two routes to two buttons. Four things
were wrong, and three of them were only reachable *because* the screen existed.

**One: the ordinary case rendered as "Something went wrong."** `connection_shops`
re-raised CJ's no-storefront refusal as a 422. That is this merchant's exact live
state and so the most likely outcome of anyone opening the picker — and 422
matches none of `stateForError`'s status classes, so it fell to the bare error
copy. `connection_shops` now softens exactly one code, `SUPPLIER_REJECTED`, into
an empty list: CJ answered, and the answer was not a list. A throttle, an outage
or a dead credential keeps its own meaning, because "you own no shops" is an
instruction and printing it falsely sends the merchant to the wrong console.
`SupplierConnectionError` — *our* refusal of a list that was unsafe or malformed,
including one echoing the vaulted secret back at us — is never softened. This is
deliberately narrower than `_verify`'s rule in the same file: there the shop is
irrelevant to connecting, so any `SupplierError` is survivable; here the shop
list *is* the answer.

**Two through four: every binding refusal read as something else.**
`shop_not_authorized` is a 403 and so read as "you're not signed in to this store
any more". `shop_binding_required`, `shop_required`, `connection_binding_conflict`,
`api_shop_binding_required` and `ambiguous_shop_name` are 400s and 409s, which
match no status class at all, so all five read as the generic error. Each now has
a state and a sentence. They sit *ahead* of the status classes in `stateForError`,
which is the existing ordering rule and the whole reason the 403 case works.

And `connectionIsUsable` was true for an unbound connection, so the row said
"Connected and working" over a connection that refuses every order. That predicate
is unchanged — importing genuinely needs no shop, and narrowing it would brick
import for every account CJ has no storefront for. The new `connectionCanFulfil`
is the narrower question, and the row now distinguishes them in words: *"Connected.
Importing and publishing work; orders need a fulfilment shop."*

Two shapes in the picker itself are worth naming. A shop the server marks
unfulfillable renders with **no control at all**, not a disabled one — the verdict
is structural, so there is no state in which the row is tappable and the refusal
arrives afterwards, which is what used to happen one lost order later. And
`SHOP_BINDING_REQUIRED` is the one new state with no "Try again": it is a fact
about the connection, not a verdict on a list, so a second attempt returns the
identical answer forever. It routes to Suppliers instead.

`scripts/mutation_cj_shop_binding.py` holds the whole claim up: eleven mutations,
eleven killed. Each removes one invariant above — re-raise the no-storefront
rejection, soften every failure, soften our own refusal, drop either half of
`connectionCanFulfil`, trust a truthy `fulfillable`, forget a code mapping, let
the status decide first, offer a button on an unfulfillable shop, offer the picker
on a bound connection, give `SHOP_BINDING_REQUIRED` a retry.

---

## The seventh seam: one integer, two meanings, one line apart

This one was filed as gap #2, "no buyer-side variant selection", and that
description survived three passes over this document because it is what a
shopper would notice. It is not what was wrong. Two lines of `publish`:

```python
sellable = sum(1 for v in rows if variants.availability(v) == variants.AVAILABLE)
"UPDATE marketplace_listings SET status='published', quantity=?, ..."
return {..., "sellable_variants": sellable}
```

`sellable` is a count of **variants**. `quantity` is a ledger of **units** —
`marketplace_cart_routes.py:820` decrements it per unit reserved, line 1159
credits it back on release, and `lifecycle.inventory_available(listing, n)`
answers "may this buyer take n units" by comparing `n` against it. The same
integer fed both, and both readings were defensible at the line that produced
it. The merchant's screen was even right:
`ReviewImportedProductScreen.tsx:528` renders `sellableVariants` as *"N variants
are on sale"*. The shelf rendered that same number as units.

Production listing 14 is the measurement: one variant, **132 units in CJ's
warehouse, `quantity = 1`**. 131 units that existed, were paid for by nobody,
and no buyer could reach. Nothing errored. Nothing was red. The product simply
sold out after one.

### The half underneath it

Beside that, a second fact nobody had measured: **`provider_variant_id` was NULL
on every imported listing in production.** `fulfillment.create_intent` routes
every line through `gateway.get_product_binding`, which refuses outright when
that column is NULL. So every published dropship product was one a buyer could
add to a cart, pay for, and never receive — the sixth seam's shape again, at the
variant level instead of the shop level, and reachable by the same query: grep
the writers of the column, ask whether the ordinary path reaches one.

It did not. `bind-product` existed and had no caller. Import wrote NULL and the
merchant was never asked. This is why the fix is not "stock the shelf
correctly": stocking it correctly would have published 132 units of a thing that
still could not ship.

### The fix, and the one shape it had to avoid

A dropship listing sells the variant it names, and every number on it reads that
variant:

- **Import binds.** `SupplierProductScreen` already sends the merchant's variant
  selection, and `importer` now records it — but only when it is unambiguous
  (`len(chosen) == 1`). Several variants chosen means the merchant has not said
  which one this listing *is*, and a guess there is the defect wearing a
  different hat.
- **Publish writes units of the bound variant**, via `_sellable_units`, and
  keeps `sellable_variants` as a variant count in the return value. The two
  numbers are now two numbers.
- **Publication refuses an unbound DROPSHIP listing** with
  `SUPPLIER_VARIANT_UNBOUND`. That refusal is only fair to make because import
  now satisfies it without the merchant doing anything extra — the seventh
  corollary applied to the fix itself rather than discovered in the wreckage of
  it. `STOCKED` sources are exempt; they ship from the seller's own shelf and
  name no supplier variant.
- **Price, availability and the live-reprice path all read the bound variant.**
  A sibling at another price is catalogue, not an offer, so it can no longer
  raise `VARIANT_PRICE_SPREAD` on a listing that was never selling it.

The shape deliberately avoided: **binding at publish time from stock state.** It
is the obvious convenience — "A is out of stock today, bind B" — and it is
wrong, because `link_source` refuses to re-point an existing binding, so a
transient warehouse fact would become the permanent identity of the product.
Binding is a statement about *what the listing is*. Only the merchant's explicit
choice makes it.

`_sellable_units` has one deliberate asymmetry worth naming: "in stock, count
unknown" yields **1**, not unlimited and not zero. `variants.availability`
already trusts that state as AVAILABLE, so zero would discard a fact CJ gave us;
any number above one would be a number nobody told us.

### What the tests had to be

Both halves survive any test that asserts the code returns what the code
computes, so the assertions were put where the values land:
`lifecycle.inventory_available(listing, 40) is True` and `(listing, 41) is
False` read off the published row, and the binding is asserted by selecting
`provider_variant_id` out of `marketplace_product_sources` after an ordinary
import. `scripts/marketplace/dropship_binding_mutation_battery.py` pairs 15
plausible "simplifications" with the one suite meant to catch each — including
both readings of "no count" and both halves of import's `len(chosen) == 1` — and
all 15 are caught.

---

## The eighth seam: the handler that blinded the diagnostic built for it

Filed as gap #4, and the filing was accurate as far as it went: `vault.seal` and
`vault.unseal` each wrapped their whole body in

```python
    except Exception:
        raise VaultError() from None
```

so a caller's mistake came back as `credential_vault_unavailable`, 503, *try
again later*. That is the half that cost a false alarm — a retry that cannot
help, and an operator sent to check a deployment that was fine.

The other half is worse, and it is only visible from inside this package.
`business_os_supplier_routes._origin` exists, and its docstring says why: a code
like `MALFORMED_PROVIDER_RESPONSE` "is raised from a dozen separate validators
that all answer with the same opaque 502", so `_origin` walks the traceback and
reports the innermost frame inside `business_os/suppliers/` as `file:line`. It
is the mechanism this package already built for precisely the problem the vault
had. **And a blanket `except` is the one shape it cannot see through** — when
you re-raise at the handler, the handler *is* the innermost supplier frame, and
the coordinate points at the `raise` statement rather than at the check that
failed.

Measured, through the real `_origin`, before any change:

| cause | class | code / status | origin |
|---|---|---|---|
| bundle missing a field | `VaultError` | `credential_vault_unavailable` / 503 | `vault.py:127` |
| scope value is not a string | `VaultError` | `credential_vault_unavailable` / 503 | `vault.py:127` |
| wrong store (AAD refuses) | `VaultError` | `credential_vault_unavailable` / 503 | `vault.py:144` |
| sealing key retired by rotation | `VaultError` | `credential_vault_unavailable` / 503 | `vault.py:144` |
| corrupt ciphertext | `VaultError` | `credential_vault_unavailable` / 503 | `vault.py:144` |
| keyring absent — a real outage | `VaultError` | `credential_vault_unavailable` / 503 | `vault.py:127` |

Six causes, two coordinates, one answer. A genuine outage was byte-identical to
a caller's typo, and the diagnostic flag an operator would turn on to tell them
apart reported the two lines that catch rather than the six that fail.

### The fix, in two halves

**Raise where it happens.** Every check now raises on its own line, and the
remaining `try` blocks cover only the operations that really are infrastructure
— constructing the cipher, encrypting a validated bundle with a key from the
ring. `_origin` then separates the causes for free: no new wire field, no route
change, nothing for a caller to opt into.

**Say what kind of failure it is.** Three classes that differ by what the reader
does next, not by what went wrong:

| class | code | status | the reader's next move |
|---|---|---|---|
| `VaultError` | `credential_vault_unavailable` | 503 | an operator fixes the deployment; retry |
| `CredentialRequestInvalid` | `credential_request_invalid` | 500 | fix the call site; retrying is a promise that cannot come true |
| `CredentialUnusable` | `credential_unusable` | 409 | re-establish the supplier connection; neither retry nor code change helps |

All three remain `VaultError`, so every existing caller catches exactly what it
caught before — the two `connections` call sites and nine assertions in the
vault suite, unmodified and still green. `_error` already read `code` and
`http_status` generically via `getattr`, so the routes needed no change either.

The same probe now reports eleven causes at eleven distinct coordinates under
three verdicts. What `CredentialUnusable` deliberately does *not* separate is
"no such reference" from "that reference is someone else's": those share a code,
a status and a message, because distinguishing them on the wire is a way to
enumerate other tenants' credential references. Inside, they are two different
lines, which is exactly the distinction `_origin` is for.

### The half that would have made this a regression

A new status code is a new thing the app has never seen, and
`mobile-native/src/api/dropshipping.ts` classifies a failure before the screen
renders a sentence. It already matched `credential_vault_unavailable` — from a
previous fix, for a merchant told "your supplier isn't responding, try again
shortly" when the supplier had never been contacted. The two new codes matched
nothing, and 409 matches none of the status classes at the bottom of
`stateForError` either, so an unusable credential would have arrived as a bare
"Something went wrong": no cause, no button, and no hint that reconnecting fixes
it — the exact failure that function's docstring exists to prevent.

So `credential_unusable` joins `credential_missing` in `DISCONNECTED_CODES`,
because the merchant's move is the same one. `credential_request_invalid` is
deliberately left to fall through to `ERROR`, with a test saying so — it means
the bug is ours, and there is no action to offer someone for a mistake they did
not make. Server-side this change was an improvement in isolation; shipped
without this file it would have replaced a wrong sentence with a useless one.

### What the tests had to be

A test that asserts `pytest.raises(vault.VaultError)` is *satisfied by the
defect* — that is why nine of them existed while the defect did. So the suite
now pins the two things that were never claimed: what a failure means
(`tests/business_os/test_cj_vault.py`, one test per class, plus a real outage
still saying 503), and where it is reported from — a test that drives seven
causes through the routes' own `_origin` and asserts the seven coordinates are
distinct. That last one is the anti-vacuity test: it is the assertion that would
have failed before this change, and no other in the file would have.

`scripts/marketplace/vault_classification_mutation_battery.py` pairs 18
plausible tidy-ups with the suite meant to catch each — re-folding either
function into one handler, each class quietly returning to 503, the subclass
relationship being dropped, and `load` "helpfully" distinguishing *not yours*
from *no such reference*. All 18 are caught. Two survived the first run and both
were worth the run: one was a behaviour-preserving mutation that only *permitted*
a leak (widening `__init__`, with nothing passing a detail), and one proved a
pairing claim wrong — the `connections` assertions both delete an environment
variable, so they are outages that can only ever see the base class. The claim
was corrected rather than the judge widened.

---

## The ninth seam: two readers of one fact, and only one of them was right

The fourth seam was about a *gate* reading the wrong one of media's two stores.
This is the same two stores, one layer out: two **readers**, both merchant-facing,
both correct in isolation, disagreeing about the same product.

| Surface | Function | How it answered "what is the cover" |
| --- | --- | --- |
| Review Product (detail) | `drafts.get_draft` | `media[0] if media else None`, derived from `listing_metadata_json` |
| Dropshipping products (list) | `drafts.list_drafts` | `SELECT l.cover_image_url` — the column, raw |

Nothing forced the two to agree. Production listing 14 is what that looks like:
five `cf.cjdropshipping.com` URLs in the metadata, `cover_image_url` NULL,
so the merchant's own list drew a blank tile for a product that opened with five
photos. Nothing raised, nothing logged, and each reader was individually right.

The measurement that matters is that the gap had **four** places to reappear.
`media[0] if media else None` was written out independently in `get_draft`,
`update_draft`, `publish` and `importer._insert_listing`. All four agreed at the
time of writing, which is precisely why no test could tell.

### The invariant is not "the stores are equal"

That was the tempting fix and it is wrong, because the stores have writers
outside this package. `bot.py`'s seller listing-update route sets
`cover_image_url` from `marketplace_product_media` rows (`bot.py:54858`) and
writes `listing_metadata_json` from a separately validated payload
(`bot.py:54944`) — two independent writes, and a dropship listing is
seller-owned, so that route is reachable for it. A dropship listing can
legitimately end up with its picture in the column only.

So the fix is a single **reader** — `drafts._cover_of(listing)` — that prefers
the metadata (the store this package owns, and the list `get_draft` returns as
`media`, so the two fields of one payload cannot contradict each other) and falls
back to the column (the store the buyer renders), ending in `None` only when
there is genuinely no picture. `get_draft` and `list_drafts` both call it.

### The half that had to be refused

`_cover_of` is deliberately **not** used by the writers. `publish` still computes
`cover = media[0]` directly. A writer that "reconciles" with the column it is
about to overwrite preserves whatever stale value was already there — which is
the eighth seam's lesson applied to a function instead of a variable: *what to
store* and *what to show* are different questions, and one function answering
both is how a name acquires two meanings.

`list_drafts` now selects `l.listing_metadata_json` to answer the question and
`pop`s it before returning. The route is `{"ok": True, **result}` — it serialises
the row dicts verbatim — so under §27/§95 that `pop` is load-bearing, not tidiness.

### What the tests had to be

Every assertion is on the *disagreement*, never on one surface's value: a test
that the list is non-empty passes while showing a different picture than the
detail screen. So the suite pins list-and-detail agreement with the column
blanked, with the metadata blanked, and with the two stores deliberately set to
different URLs.

`scripts/marketplace/dropship_cover_mutation_battery.py` runs 12 plausible
tidy-ups; all 12 are caught. Five survived the first run and every one was a real
hole rather than a bad pairing — including three in `_media_of`, pre-existing code
no test had ever exercised: an unreadable metadata blob (which, had it raised,
would have blanked *every other product* on the merchant's list, because this read
is a list), and a JSON `null` inside the media array, which reaches the renderer
as a cover while satisfying every "is media non-empty" check in the package.

Two survivors were the run earning its keep in the other direction. The mutation
named "a merchant reorder stops moving the tile" survived because the *claim* was
wrong: every writer here keeps both stores equal, so after a reorder both
orderings of the fallback return the same value and no reorder test can separate
them. The name was corrected to what actually separates them — an outside writer
moving one store only. And "a non-string column is handed to the renderer" was
behaviour-preserving: the column is `TEXT`, so SQLite's affinity turns an integer
`0` into `'0'` and Postgres refuses it outright. It was replaced by the reachable
half of the same guard, whitespace — which is not hypothetical either, since a
whitespace-only column passes every "is the cover set" check written here,
including the `COALESCE(cover_image_url,'')` in the backfill script.

---

## The tenth seam: the list that could not notice what was never on it

Gap 6 was filed as cosmetic — a stale comment in `bot.py`, rewritten. Its own
stated lesson is why it was worth re-opening: *a claim written in prose gets
believed*. So every claim the rewrite makes was measured rather than re-read.

Four of the five hold.

- Both parity suites — Python and TypeScript — really do read
  `priceLabelParity.json`. The fixture is the shared artefact, not a sentence.
- The three ceiling constants (`bot.MAX_PRICE_LABEL_CENTS`,
  `drafts.MAX_CHECKOUT_PRICE_CENTS`, `MAX_PRICE_LABEL_MINOR` in TypeScript) all
  equal `99_999_999`, and each is pinned by something that fails if it moves —
  the last by fixture cases either side of it.
- The clamp window between `parse_price_label_to_cents` (which clamps) and
  `_set_prices` (which accepts ten times as much) is closed on all three write
  paths, not just the one the comment named: `_validate` at publication,
  `_live_price_label` on a supplier reprice, and
  `marketplace_normalize_price_label` on the seller edit.

The fifth is the seam. The comment listed, in prose, the six surfaces that print
nothing rather than prose for a listing with no price: the serializer, the web
grid, the web product page, the client-side search card, the app's grid, the
app's product page. Every entry was correct.

`MarketplaceCheckoutScreen` was the seventh, and it was not on the list because
nobody writing the list was thinking about checkout — an unpriced listing cannot
be bought, so checkout felt out of scope. Line 225:

```tsx
params.subtotalMinor != null ? formatMinor(...) : params.priceLabel || "Shown at checkout"
```

Both halves of that fallback put something in the amount slot that is not the
amount. `priceLabel` is the **unit** price — `handleBuyNow` multiplies it out
for exactly this reason, and then this screen put the bare label back under
"Item total", "Total" and "Amount paid", so an order for two displayed one
item's price as its total. And "Shown at checkout" is a promise that names the
screen the buyer is already standing on; on the confirmation view it rendered as
the value of **Amount paid**, after the money had moved.

Unreachable today — both callers pass `subtotalMinor`. Kept dead by convention
only: the param is optional and nothing pinned it. That is the same shape as the
sixth seam's `bind_shop`, in the mirror. There, a guard nothing could satisfy;
here, a fallback nothing could reach. Both are held in place by a fact about the
callers that no test states.

Alongside it, `priceFallback: "Price at checkout"` was still shipping in eleven
i18n catalogs, read by zero code — one `t()` call from returning the exact
phrase the grid card was fixed to stop saying.

### The half that would have replaced one claim with another

The obvious fix is to add checkout to the list. That is the defect again with a
longer list, and the next surface written will be missed the same way.

So the claim stops being an enumeration. `MarketplacePriceLabelRendering.test.tsx`
now walks `mobile-native/src/` and fails on any banned phrase in rendered copy,
and separately fails on any catalog key whose *name* offers to stand in for a
missing price. By key, not by phrase: ten translations of "Price at checkout"
are invisible to anyone searching for the English, and the key name is the only
language-independent part.

Two things this required getting right, both of which would have made the scan
lie rather than fail:

- **Comments must be stripped, not skipped.** Three files that have to pass this
  scan discuss the banned phrases in prose, including this fix's own
  explanation. Stripping is by regex over block and line forms, and it handles
  `{/* … */}`, because JSX writes its comments that way and a line-based check
  that looks for a leading `//` reads straight past them.
- **The scan must be proven to be reading something.** A stripper that returns
  `""` makes every phrase vanish and every assertion pass — it fails *open*. So
  a string that genuinely ships is looked for and must be found, in the file the
  scan most needs to read.

The heuristic was also deliberately narrowed. Its first draft caught
`priceLabelPlaceholder` and `pricePlaceholder` across all eleven locales — 22
hits, none of them defects. A field placeholder tells a seller what to type into
an empty box; a fallback stands in for a price on a buyer surface. Widening the
rule to cover both would have forced an exemption list, and an exemption list is
where a rule starts negotiating.

### What the tests had to be

Rendering, not source-matching. The screen's guards were then driven through
both stages a buyer reaches, including the confirmation receipt via the cash
lane, because "Amount paid" is the row where the old fallback did the most
damage and it is only worth guarding on the path that reaches it.

Rows are asserted **absent**, not blank: a `SummaryRow` with an empty value
still prints its label, so "Amount paid" over nothing is its own small lie. And
every case also checks that a *known* amount still prints, because a screen that
renders no amount ever would otherwise satisfy "invents nothing".

`scripts/mutation_checkout_price_surface.py` runs 11 mutations plus one
deliberate no-op control; all 11 are caught and the control survives. One real
survivor on the first run, and it was the assertion's fault rather than a bad
pairing: the product card's price guard, tested by searching for the price text,
could not tell an omitted element from one rendered as an empty string — both
find nothing — while an empty `<Text>` keeps the price font's weight and
margins. The assertion moved to the element.

The run also turned up a second live defect the guards had created. Removing the
total row left the buyer with *no* account of the amount, because the sentence
covering an unknown amount lived only on the card branch — and card payments are
paused, so on the one lane a buyer can actually use, an order with no subtotal
said nothing about the amount whatsoever. **A row removed for honesty still owes
the buyer the reason.**

---

## The eleventh seam: the approval that succeeded and changed nothing

Gap 7 was filed as *a published dropship listing cannot be approved*, and the
Approve-409 half was fixed: the guard now reads `lifecycle.awaiting_moderation`
instead of `status`, and the route has tests, which it did not before.

The residual is the same defect reflected. Approve no longer refuses wrongly —
it **accepts** wrongly. `/admin/marketplace-command` writes two columns:

```sql
UPDATE marketplace_listings SET status=?, approval_status=? WHERE id=?
```

Publication needs five. `marketplace_listing_lifecycle.is_public` requires the
seller account approved, the storefront named, the listing released
(published + approved), and stock available. The moderator's decision moves two
of those and the response says `"Listing updated."` regardless.

### What the measurement showed

Four listings, one moderator, one Approve each:

| listing | seller | stock | HTTP | message | visible to buyers after |
|---|---|---|---|---|---|
| healthy | approved, named | 3 | 200 | Listing updated. | **yes** |
| unnamed storefront | approved, no name | 3 | 200 | Listing updated. | no |
| suspended seller | suspended | 3 | 200 | Listing updated. | no |
| empty shelf | approved, named | 0 | 200 | Listing updated. | no |

Three of four approvals returned 200, wrote an audit entry, and produced
nothing. `public_denial_code` already knew the reason in all three cases —
`SELLER_UNAVAILABLE`, `SELLER_UNAVAILABLE`, `OUT_OF_STOCK` — and nobody on the
moderation path asked it.

### Refusing would have been the wrong fix

The obvious repair is to block Approve until all five conditions hold, and it
recreates gap 7's original bug one layer up. A moderator judges *content*. An
out-of-stock listing that can never be approved can never become sellable when
stock returns, because approval is the input to the stock check and not the
other way round. So the decision still lands; the response stops lying about
what it accomplished:

> Listing updated, but it is still not visible to buyers: the listing has no stock.

### The half that did more damage

The merchant's own dashboard read the same two columns and drew the same
conclusion the route did. `SellerStoreScreen`'s pill derived its state from
`publication_state` — `marketplace_listings.status`, lowercased, one of the five
conditions — and two independent defects fell out of that:

- The `out_of_stock` branch was **unreachable**. It searched for the substring
  `"stock"` in a column that only ever holds a listing status, so no payload in
  any shape could produce it. A branch nothing can satisfy is not a feature.
- `"published"` was missing from the screen's own list of live-ish values
  (`["active", "approved", "live"]`), so it fell through every branch and was
  returned unchanged — a lowercase neutral chip. **The live pill never rendered
  for the value `drafts.publish` actually writes.**

Net effect on the surface the merchant uses: all four rows above rendered
identically, and none of them rendered as live. Nothing in the app distinguished
a selling listing from a dead one.

### One table, three projections

The fix is not a second derivation. `PUBLICATION_RULES` is now the single
ordered table, and each rule carries the three strings its three consumers read:

| rule | buyer `denial_code` | merchant `seller_label` | moderator `moderator_note` |
|---|---|---|---|
| `seller_approved` | `SELLER_UNAVAILABLE` | Store offline | the seller account is not approved |
| `seller_named` | `SELLER_UNAVAILABLE` | Store name needed | the seller has no public store name |
| `released` | `ITEM_UNAVAILABLE` | Not published | the listing is not both published and approved |
| `in_stock` | `OUT_OF_STOCK` | Out of stock | the listing has no stock |

The buyer codes are the ones already on the wire; nothing on that column
changed. `seller_label` and `blocker_note` are new projections of a table that
already existed, which is the whole point — the merchant's chip and the
moderator's sentence are now downstream of the function that filters discovery,
not re-derivations beside it.

The server ships the verdict as `publication_blocker` and the screen reads it.
`live_blocker` answers only the *surprising* question — published and approved
but still unreachable — and returns `""` for drafts, paused and in-review
listings, which already have accurate labels and must keep them. A draft with
quantity 0 is a draft, not "out of stock": running every row through the
publication rules would replace the state the merchant needs to act on with one
that is true and useless.

### Absence of a column is not evidence

The trap in this fix is the naive version of it. `is_public` is a **gate** and
`seller_label` is a **description**, and they read silence in opposite
directions. Four payload call sites project different column sets, and only two
of them originally joined `marketplace_sellers` — so a rule evaluated against an
unprojected row has no evidence either way.

The predicates are three-valued: `True` (met), `False` (the row proves it
unmet), `None` (the row was not projected with the columns needed to judge).
`None` cannot be the sentinel for "absent", because the queries select
`COALESCE(ms.status,'missing')` — a falsy value there is real evidence, while a
missing key is none — hence `_UNPROJECTED`.

The gate's default is "no" and the description's default is "do not deny what
you cannot see". But the gate does **not** uniformly block on silence either:
`seller_named` deliberately passes when the store-name column is unprojected,
because that invariant binds in SQL via `public_sql`. So each rule carries an
explicit `passes_when_unknown` flag rather than one global policy — a uniform
"None means blocked" would have silently changed `is_public`'s pre-existing
behaviour for two of the four call sites.

Downgrading "Live" on a rule the row is merely *silent* about would have made
the live pill vanish for every merchant served by a query without a seller join.
That is the same bug as the one being fixed, pointed the other way.

### What the tests had to be

`tests/marketplace/test_marketplace_approval_visibility.py` (20 tests) posts
real decisions and then re-reads the row: every shape stays approvable, the
healthy one actually goes public (the control that makes the negatives mean
something), and each invisible one is reported as such. A stockless digital
listing with quantity 0 must stay public, which is why `DIGITAL` is in the
fixture set.

`SellerStorePublicationPill.test.tsx` (7 tests) renders the screen. One harness
line is load-bearing and is commented as such: the snapshot mock must carry
`live: true`, because the screen keeps its cached copy otherwise — without it no
listing reaches the tree and all seven assertions pass vacuously.

`scripts/mutation_approval_visibility.py` runs 17 real mutations plus a no-op
control across **both** runtimes, pytest and jest, in one script. All 17 caught,
control survives.

One survivor on the first run, and it was load-bearing in a way no test I had
written could see: removing the `STOCKLESS_TYPES` early return from
`_is_in_stock` changes nothing for any row that has a `quantity` key, because
`inventory_available` checks stockless types too. Its only effect is on a
digital row projected *without* `quantity` — where, absent the early return, the
rule returns `None`, the gate's default applies, and a digital product becomes
unpurchasable. The mutation was reporting a real gap in the fixtures, not a
redundant line.

---

## The twelfth seam: the number the screen showed and never sent

There was no gap 8 on the list. Gaps 1 through 7 were struck through, and by the
eleventh corollary's own rule — *an enumeration cannot notice what was never on
it* — the next one had to be measured rather than read. So the measurement was
the whole first half of this work: drive a buyer through the one lane nothing in
the repository had ever exercised, and compare what each layer said the order
was.

A buyer opens a $25.00 listing with ten in stock, steps the quantity to three,
and taps Buy Now.

| Layer | What it said the order was |
| --- | --- |
| `MarketplaceProductScreen` stepper | 3 units |
| `MarketplaceCheckoutScreen` summary | `×3`, **$75.00** |
| `openMarketplaceCheckout` request body | *no quantity field at all* |
| `/api/pulse/payments/checkout` charge | **$25.00** |
| `marketplace_listings.quantity` after | 10 → **9** |
| `marketplace_inventory_reservations.quantity` | **1** |
| `marketplace_orders.quantity` | **1** |

Every row below the second is a consequence of the third. The server had no
parameter to read, so it did not read one — `quantity=1` was written into the
quote, into the decrement, into the hold and into the metadata, four times, each
of them locally correct given the line above it.

### The damage is not the money

The charge is the visible half and the least serious: the buyer is undercharged,
notices, and complains. The other three are silent.

The **shelf** is the first. Three units were sold and one left the count, so the
other two stayed discoverable and buyable. Nothing reconciles this; the count is
the only record of what is left.

The **hold** is the second, and it is the one that bites on failure rather than
success. `release_inventory_reservation` returns stock by reading
`marketplace_inventory_reservations.quantity` — not the Stripe metadata, which
has three writers and no readers at all. A hold of 1 against a charge for 3 means
a declined card gives one unit back to a shelf that lost three, so a failed
payment leaves the listing permanently short.

The **order row** is the third, and it is the one that connects this to the CJ
chain. `fulfillment.create_intent` compares a supplier line against
`marketplace_orders.quantity` and refuses on `order_line_mismatch`. While that
column said 1 for every Buy Now order, a three-unit order could only ever be
dispatched as a one-unit order or refused outright. This gap sat directly on the
path the whole mission is about, and every stage around it had been fixed.

### The test that was the defect

`tests/test_marketplace_buy_now_checkout_contract.py` was the only file covering
this route, and it opens by explaining itself:

> the safest narrow test inspects the function body without importing the full
> application

So it read `bot.py` as text and asserted on strings in it. One of the strings was
`"quantity=quantity-1"`.

The assertion and the bug were the same characters. The test passed because the
defect was present, and would have failed had anyone fixed it. That is a
different failure mode from every other seam in this document: not a test that
could not see the defect, but a test holding it in place.

It also had a sibling, `'"quantities": "1"'`, pinning a Stripe metadata field —
and the same file's third test carries a long docstring about exactly this,
written when an earlier literal broke on a refactor that improved the behaviour:

> The literal left the route while the behaviour got strictly better — so the
> assertion failed and reported a missing inventory release on a path that has
> one, which is a worse outcome than no test at all.

The lesson had been learned, written down, and applied to one assertion in the
file while three others carried on reading source text. Both literals are now
gone, and the file says why.

Corroboration that nothing else could have caught it: **no test in the
repository posted to `/api/pulse/payments/checkout`.** Not one. The route that
takes money for every single-item purchase on the platform had exactly one test
file, and that file never called it.

### What the fix had to do that the obvious fix would not

Reading `payload["quantity"]` and multiplying is four lines and is wrong in three
ways.

*It oversells.* A shelf of two must refuse an order for three, so the pre-flight
asks `marketplace_listing_lifecycle.inventory_available(item, buy_quantity)` —
the same function the cart and the buy button ask, so the three lanes cannot
disagree about what "available" means — and the decrement itself is conditional
on the whole amount (`WHERE id=? AND quantity>=?`), because the pre-flight reads
a row fetched earlier in the request and another buyer can empty the shelf in
between. The refusal is advisory; the predicate is what holds.

*It invents a second ceiling.* `MAX_QTY_PER_LINE` is the cart's limit. Buy Now
now clamps to it, so one listing does not have two maximums depending on which
button was pressed.

*It breaks every order already in the ledger.* `pulse_upsert_marketplace_order`
derived its pair from `details["qty"]` and `amount_cents // quantity`, and both
halves were wrong: `qty` is a key only the cart lane ever wrote, and the division
is not a unit price for any order carrying shipping or tax. The replacement,
`marketplace_order_line`, reads `commercial_quote` — which states `quantity` and
`unit_price_minor` exactly, and is already frozen onto the transaction at
checkout — and falls back to the old pair for rows written before quotes existed.
An absent `qty` must keep meaning **one**, not "unknown", because that is what
every single-unit order in the ledger relies on. Making absence mean zero would
have been a larger bug than the one being fixed, shipped as its cure.

`marketplace_orders.unit_price_cents` was write-only — grepped, no readers
anywhere — which is why correcting it was safe to do in the same change.

### Both lanes, or neither

The server can now read a quantity that no client sends. `openMarketplaceCheckout`
takes it as its sixth argument and always writes it into the body — not only when
it is greater than one, because "the buyer chose one" and "this build cannot say"
must not arrive looking identical.

### What the tests had to be

Sixteen backend tests in a new file that **posts to the route**, plus sixteen
native tests across three files. The shape that matters is which file can see
what:

- `MarketplaceCheckoutQuantityHandoff.test.tsx` mocks `api/marketplace`
  wholesale, so it proves the *screen* hands over the number it displayed — and
  is structurally incapable of noticing what the API function does with it.
- `marketplaceCheckoutQuantityBody.test.ts` therefore exists to read the posted
  JSON. Deleting the `quantity` line from the request body survives the first
  file completely.
- The card lane is unreachable — `MARKETPLACE_CARD_PAYMENTS_PAUSED` returns
  before its call site — so no test that drives the UI can get there. Dropping
  the quantity from that one call site is invisible to every behavioural test
  and reappears the day card payments resume. It is covered the only way dormant
  code can be: by counting call sites against forwarded arguments, in
  `MarketplaceCheckoutInformationOrder.test.ts`, beside the identical count that
  file already keeps for `details`.

Two fixtures are load-bearing and would have hollowed out the suite silently.
`delivery_type='physical'` must be set explicitly — the column defaults to
`'digital'`, which skips the address, the decrement and the reservation
altogether. And a physical listing resolves to the `shipping` kind, which refuses
checkout without a valid address, so an incomplete `fulfillment_details` makes
every "did not oversell" assertion pass by way of HTTP 400. The first test
asserts `200` explicitly for that reason.

### What the battery caught that the suites did not

Seventeen mutations, one no-op control, and one **inverted** entry — a mutation
that reformats the decrement's SQL without changing its behaviour, which the
contract file must *not* notice. A battery that only rewards catching cannot
distinguish a test that measures behaviour from one that pins characters, and
pinning characters is the specific thing that went wrong here.

Five mutations survived the first run, and every one of them was a real hole
rather than a redundant line:

- the commit-time `quantity>=?` guard had no test, because the pre-flight refusal
  always fired first; it is now tested by stubbing the pre-flight to approve, so
  the last line of defence is exercised on its own;
- the quote's type check was satisfiable by a malformed quote whose keys were
  merely truthy, so the fixtures now carry a `"3"` and a `"2500"` and a `True`;
- and three client mutations survived because two of them were aimed at a module
  the screen suite mocks away and one at a lane the UI cannot reach — which is
  how the second and third native files came to exist.

The runner had its own version of the same bug. It dispatched to jest on
`.tsx`, and two of the new suites are `.ts` — so they went to pytest, which
collects nothing and exits 0, reporting every native mutation as a survivor for a
reason with nothing to do with the code. A harness that answers "survived" when
it never ran the suite is the measurement instrument making the assertion the
document is about.

Final run: 16 of 16 real mutations caught, the inverted mutation correctly
ignored, the no-op control correctly survived.

---

## The thirteenth seam: one column, six readers, and no lane in it

`marketplace_listings.delivery_type` has never contained a delivery lane.

It is `TEXT DEFAULT 'digital'` (`bot.py:112435`), and every writer stores the
**product type** in it. The publish route's INSERT (`bot.py:94434`) lists its
columns as `..., delivery_type, product_type, listing_type, ...` and its values
as `..., product_type, product_type, listing_type, ...` — the same bind twice.
The CJ importer (`services/business_os/suppliers/importer.py:242`) hardcodes
`'physical','physical'`. So for every listing in the table the column reads one
of the five words in `LISTING_TYPES`, and for the overwhelming majority it reads
`physical`.

The seller's actual choice is `listing_metadata.delivery_options`, validated
against `{pickup, shipping, both}` by `_take_enum`
(`services/marketplace_listing_types.py:180`). That key is *optional* —
`_take_enum` returns early when it is absent — so a physical listing with no lane
is an ordinary row, not an error, and it ships.

Six functions asked "how is this listing fulfilled". Asked of one real published
row — the Ball listing, `delivery_options: "pickup"` — they answered:

| reader | answer |
| --- | --- |
| grid card `listingFulfillment` | `unknown` |
| detail page `marketplaceListingFulfillment` | `pickup` |
| detail copy `marketplaceFulfillmentCopy` | `Local pickup` |
| cart line `_fulfillment` | `shipping` |
| server `resolve_kind` | `shipping` |
| checkout `resolveFulfillmentKind` | `shipping` |

Three measured damages, one root:

**The buyer is put on the wrong lane.** `option = delivery or meta.get("delivery_options")`
could not reach its right-hand side, because the left-hand side was always the
non-empty string `physical`. Every physical listing resolved to `shipping`. A
seller who chose local pickup only had their buyers asked for a delivery address
for an item nobody was going to post. And `shipping_or_pickup` had no input that
could produce it, which makes the checkout screen's lane chooser unreachable UI
and `resolve_choice`'s pickup branch dead code — a branch with a comment
explaining why it matters.

**Every physical card in the marketplace lost its buy button.**
`listingFulfillment` substring-matched the column for `ship`/`pickup`/`local`/
`meetup`/`digital`/`download`. `physical` contains none of them, so it returned
`unknown`, and `gridCardAction` returns `null` for `unknown`: no Add to cart, no
Make offer, on the entire catalogue. This is the loudest symptom in the chain and
it was invisible because the guard was firing on *everything*, which looks like a
design decision rather than a bug.

**Two contradictory lanes travelled in one payload.**
`MarketplaceProductScreen.handleBuyNow` sent `fulfillment: listingFulfillment(listing)`
and `fulfillmentKind: kind` two lines apart — the checkout screen reads the first
for its lane chooser and the second for its fields — and `kindFromParams`
(`if (kind) return kind;`) preferred the one that was wrong.

The asymmetry that names the whole defect: `marketplaceFulfillmentCopy` read the
metadata **first**, and special-cased the literal `"physical"` in the column.
Somebody writing the *label* noticed the column held a product type and worked
around it, locally, where they were standing. The function that decides what the
buyer is actually charged for never got the same treatment. The knowledge was in
the codebase the entire time, one file away from where it was needed.

**Why every suite was green.** Every test of the rule built its own listing dict
and put a lane word in `delivery_type`. No row in the database has ever looked
like that. The fixtures described rows that cannot exist, and the suites passed
on inputs production does not produce. One fixture contradicted *itself* —
`listing({ delivery_type: "pickup" })` over a fixture whose metadata said `both` —
and passed for the worst possible reason: the code read the column first, so the
test silently pinned whichever field the bug happened to prefer.

**The fix** is one rule per language. `delivery_lane` /
`deliveryLane` reads `delivery_options` first, returns `""` for a row that
declared a listing type (the column cannot outvote the seller), and falls back to
the column only for a pre-types row that has no other signal. All six readers
fold down from it; none derives anything. `_LANE_WORDS` and `LANE_WORDS` hold the
legacy spellings, and `tests/test_marketplace_fulfillment.py` reads both files to
prove they stay identical, because that is the one test that can.

The tests are the point. `tests/test_marketplace_delivery_lane.py` supplies no
listing dicts at all: it publishes through
`/api/pulse/marketplace/listings/create` and reads the row back out of the table,
so it asserts `row["delivery_type"] == "physical"` as a measured fact before
asserting anything downstream of it.
`marketplaceDeliveryLaneAgreement.test.ts` asserts the surfaces against *each
other* — the sentence against the kind, the lane against the fold-down of the
kind, `=== "both"` against `UNDECIDED_KINDS.includes(kind)` — rather than each
against its own expectation, which is the only form that can fail when six
readers drift.

Two fixture families had to be corrected rather than satisfied, each with an
in-test note saying what it had been asserting and why that was wrong.

`scripts/mutation_delivery_lane.py`: 18 real mutations, 1 inverted, 1 no-op
control. Two survived the first run and both were errors in the battery rather
than gaps in the suites — and both were worth the trip. The `physical → digital`
word-list mutation was pointed at the integration file, which publishes through
the real route and therefore always writes a listing type, so the column is never
consulted there and the mutation was invisible to it; the assertion that catches
it is the unit-level `delivery_lane("physical", {}, "") == ""`, the single most
load-bearing line in the rule. And the cart mutation *added* a column check in
front of the function, which is a no-op on every row the route writes, because
the column matches no lane word. The defect was never an extra branch. It was the
function answering from the column instead of from the kind — and a mutation has
to be the defect, not something adjacent to it, or its survival means nothing.

Final run: 18 of 18 real mutations caught, the inverted mutation correctly
ignored, the no-op control correctly survived.

---

## What kept coming back

Fourteen defects in this chain, fourteen different subsystems, one shape: **a
number was asserted rather than measured.**

- `publish()` never wrote `price_label`, and the publish test asserted `status`
  and `published_at` and stopped one column short.
- The app's parser claimed parity with the server's in a comment, and every test
  of it picked an example below the threshold where the formats diverge.
- `canPurchaseMarketplaceListing` claimed to answer "can this be bought" while
  only ever consulting stock, and every unpriced fixture in the render suite was
  also out of stock.
- `safety_score` was believed to be safety because of its name, and believed to
  be off the buyer's wire because two source comments and a test said so — the
  test asserting a key was absent from a fixture defined three lines above it.
- `_validate` asserted a listing had media by reading the store the merchant
  writes, never the column the buyer renders, and its own docstring — "better to
  refuse than to ship a black card" — named an outcome it could not observe.
- The admin approve guard asserted "a decision has already been recorded" by
  reading `status`, a column that answers a different question, and no test
  existed for the route at all.
- `bind_shop` did not exist, so the *only* assertion anyone had ever made about
  a bound shop was `dispatch`'s — and the existing test asserted the refusal
  (`shop_binding_required`) without ever asking whether the refusal could be
  satisfied. A test that a door is locked is not a test that it opens.
- `publish` asserted the shelf's stock by computing it — one integer that was a
  count of variants where it was produced and a count of units where it was
  read — and the publish tests asserted `quantity` equalled what that expression
  returns, which it always did. Nothing asked the buyer's own function whether
  132 units could be bought.
- `vault.seal` and `vault.unseal` asserted that every failure was an outage, by
  catching `Exception` and raising one class — and nine tests asserted
  `pytest.raises(VaultError)`, which the defect satisfies. The number here is a
  status code: 503, "try again later", returned for six causes of which four
  were not outages and two could never succeed on retry.
- `get_draft` and `list_drafts` each asserted the cover by deriving it, one from
  the metadata and one from the column, and every test of either asserted that a
  reader returns what that reader computes — which it always did. Four
  independent copies of `media[0] if media else None` agreed at the time of
  writing, and nothing ever asked two of them the same question.
- A comment asserted, by listing them, which surfaces refuse to invent a price.
  The list was six long, every entry correct, and the checkout screen was the
  seventh — filling "Amount paid" with a sentence. Nothing could fail, because
  the claim was prose and the surface it omitted had no test of its own.
- The moderation route asserted that a listing it approved was now sellable, by
  writing the two columns it owns out of the five publication requires, and
  answering `"Listing updated."` either way. Three of four approval shapes
  returned 200 and produced nothing. The merchant's dashboard made the same
  two-column inference and called them all the same thing — and `"published"`,
  the value publication actually writes, was not on its list of live values, so
  the live chip never rendered at all.
- Buy Now asserted that the buyer wanted one of whatever they were looking at.
  Not by computing it wrongly — by having no parameter at all, while the screen
  two layers up displayed `×3` and a total to match. The only test of the route
  read its source as text and asserted on the literal `quantity=quantity-1`,
  which is the defect spelled out, so the suite was green *because* the bug was
  there and would have gone red on the fix.
- Six readers asserted a listing's delivery lane by reading a column that has
  never held one. `delivery_type` is bound to `product_type` in the publish
  INSERT and hardcoded to `'physical'` by the importer, so the fallback to the
  seller's actual choice was unreachable and every physical listing shipped —
  including the pickup-only ones. Every test of the rule supplied a lane word in
  that column, describing a row the database cannot produce, and one fixture
  contradicted itself so quietly that it pinned whichever field the bug read
  first.

In all of them the suite was green, and in all of them the green was about the
halves rather than the seam. Where a claim spans two components, this document now
prefers a fixture both components read over a sentence describing them.

The fourth adds a corollary worth keeping separate, because it is about where a
test looks rather than what it checks: **a claim about what a server sends can
only be tested against a response.** Both native assertions on `safety_score`
were about objects this repository constructs. The one that would have failed —
reading the field off a served payload — did not exist, so a field that two
comments called impossible shipped to every marketplace client for four
endpoints at once. `tests/web_parity/` exists for exactly this, and the three
surfaces it already covered were the three that had been fixed *before* anyone
looked at bytes.

The fifth adds its own: **when one fact has two spellings, a gate must read the
spelling that ships.** Media had two — a metadata list and a column — and the
gate read the one the merchant writes rather than the one the buyer renders. The
tell is available without running anything: `_media_of` is called by `get_draft`
and `publish` and by nothing in `bot.py`, while `cover_image_url` is read in
`bot.py` at nine sites and written in this package at three. A guard whose input
shares no reader with the surface it protects is not guarding that surface. Both
corollaries reduce to the same instruction — **follow the value to the surface,
and put the assertion there.**

A second corollary, narrower and sharper: **a serializer that spreads its input
has no field list, only additions.** `{**row, ...}` reads like an allowlist and
is the opposite of one. Removing a key from it removes nothing.

The sixth adds the one that is really about process: **a handoff between two
authorities needs a test that crosses it.** Publish had tests. Moderation had
none — not one test in the repository posted to `/admin/marketplace-command`
before this. Each half was defensible alone, and the defect lived entirely in
the fact that the state one half produces is not one the other accepts. Every
seam in this document is the same story, which is why the working rule here is
now: **when two components hand something to each other, the test belongs on
the handoff, not in either component.**

The seventh sharpens that into something checkable without running anything:
**a guard is only finished when something can satisfy it.** `shop_binding_required`
had a test, a docstring defending it, and a comment explaining why loosening it
would be wrong — all true, all about the closed position. Grep for the writers
of the column a guard reads; if the only writer cannot be reached from the state
the guard rejects, the guard is not a guard, it is a dead end. Three of the seams
here are that exact query returning one row.

The eighth is the sharpest, because it needs no second component at all: **two
meanings sharing one variable is a defect even while the value is right.**
`sellable` was correct as a count of variants and correct as what the merchant's
screen renders; it became false the moment it was stored in a column whose
readers subtract from it. Neither line is wrong on its own, no handoff is
crossed, and no test of either can fail. The tell is a unit mismatch, and the
only place it is visible is where the value is consumed — which is why the
assertion that would have caught it is not on `publish` at all, but on
`lifecycle.inventory_available`. Generalised, and it is the same instruction the
fourth and fifth seams arrived at from the other direction: **name the unit, and
put the assertion at the surface that spends it.**

The ninth is about the other kind of value a function returns. **An error's
classification is a claim, and it decays the same way a number does.** "This
failed, so the vault is down" was asserted by a handler and measured by nobody;
the status code is the number, and it was wrong for four of the six things that
produced it. Two tells, both greppable. First: **`except Exception` around a
body with more than one way to fail is a classification, not a safety net** —
count the `raise`-worthy statements inside the `try`, and that is how many
distinct causes you have just collapsed into one answer. Second, and specific to
this repository because it already built the tool: **a diagnostic that reports
*where* an exception came from is defeated by re-raising it.** `_origin` was
written for exactly the six-causes-one-code problem and could not see into the
one module that had it worst. A test that only asserts `pytest.raises(SomeError)`
cannot notice either tell — it is satisfied by the defect — so the assertion has
to be on the pair the reader actually consumes: **the verdict and the
coordinate.**

The tenth closes the loop back to the fifth, which said a *gate* must read the
spelling that ships. Its sibling: **when two surfaces answer the same question,
the assertion is that they agree — never what either one returns.** Any test of
`get_draft`'s cover, or of `list_drafts`'s, is satisfied by the defect, because
each reader does return what it computes. The greppable tell needs no runtime at
all: **count the copies of a derivation.** Four spellings of
`media[0] if media else None` across one package is not duplication to tidy up
later, it is four places for the same fact to diverge, and the ones that agree
today are the ones nobody will notice diverging. Corollary to the corollary,
learned from the battery: **do not make the writers share the reader's
reconciliation.** A reader may consult both stores; a writer that does has merely
made "preserve the stale value" its default.

The eleventh is the one this document was most at risk from, since it is mostly
prose: **an enumeration of surfaces cannot notice the surface it never had.**
Counting copies of a derivation catches divergence between things you already
know about; it says nothing about the seventh reader nobody listed. The tell is
the form of the claim rather than its content — if a comment or a test names its
subjects one by one, ask what would have to happen for a new one to be added to
it, and the answer is always "somebody remembers". Prefer the check that walks.
Then close the two ways a walking check passes without looking: prove it read a
non-empty set, and prove its filter still lets a known-present string through. A
guard that fails open is worse than the prose it replaced, because prose does
not claim to have run.

The twelfth is the seventh seen in a mirror. That one said *a guard is only
finished when something can satisfy it*; this one says **an action is only
finished when its success means something.** `shop_binding_required` was a
refusal nothing could satisfy; Approve is an acceptance that satisfied nothing.
Both pass every test written about them, because a test of a refusal asserts the
refusal and a test of an acceptance asserts the 200 — and in each case the
assertion is on the half that works. The greppable form: **count the columns the
write touches against the columns the outcome requires.** Two against five here,
and the gap was not hidden anywhere; `public_denial_code` had been able to name
the reason the whole time, and no caller on the moderation path had ever asked
it.

Its sub-tell is where the naive fix goes wrong, and is worth more than the
corollary itself: **a gate and a description are different functions, and
sharing a rule table is not sharing a default.** Both consume the same four
rules, and they must read *silence* in opposite directions — a gate that cannot
see a column refuses, a label that cannot see a column declines to claim
anything about it. Collapse them and you get one of two new defects for free:
either an unjoined row becomes unbuyable, or a live listing is labelled broken
because nobody selected the column that would have proved it fine. Which is why
the predicates here are three-valued and every rule states its own
`passes_when_unknown` — the absence of a column is not evidence, and a uniform
policy for absence is an assertion about all four rules that only two of them
support.

The thirteenth is the one that indicts the tests rather than the code: **a test
that reads a function's source text is satisfied by whatever that source says,
including the bug.** Every other corollary here describes an assertion that
could not see the defect. This one describes an assertion that held it in place:
`assert "quantity=quantity-1" in CHECKOUT` passed because Buy Now hardcoded a
single unit, and the day someone fixed it, the test would have gone red and
reported the fix as the regression. The greppable tell is unusually literal —
**a test that reads a source file and asserts on substrings of it** — and the
question to ask of each such assertion is which way it fails: if the answer is
"it fails when the behaviour changes", it is pinned to characters, not contract.
Source-reading has one honest use, and the file that contained the worst
assertion also contains the best example of it: counting call sites against
forwarded arguments, to reach a lane the UI cannot execute. Structure is a fair
subject for a source test. Values are not.

The fourteenth is the thirteenth's other half, and it indicts the fixtures:
**a fixture describing a row the database cannot produce is not a test of the
system.** Every suite covering the delivery lane invented a listing dict with a
lane word in `delivery_type`, and the column has never held one in any row ever
written. The suites were not weak; they were measuring a different program. The
greppable tell is a question rather than a pattern — **for each field a fixture
sets, name the writer that sets it in production** — and where the answer is "no
writer does", the assertions downstream of it are unfalsifiable by anything real.
The remedy used here is the one the fourth seam already pointed at from the other
side: publish through the route and read the row back, so the fixture is a
measurement.

Its sub-tell is sharper and costs nothing to check: **when two fields in one
fixture disagree, the test pins whichever field the bug reads first.**
`listing({ delivery_type: "pickup" })` over metadata saying `both` passed for
exactly the reason it should have failed. A self-contradicting fixture does not
fail — it silently elects the current implementation as the specification, and it
will go red on the fix. Which is the thirteenth corollary again, arrived at
without reading a line of source: **ask which way an assertion fails, not whether
it passes.**

Its sub-tell is about the instrument rather than the subject: **a harness that
reports a result it never measured is the same defect, one level up.** The
battery for this seam dispatched to jest on the `.tsx` extension, handed two
`.ts` suites to pytest, and pytest collected nothing and exited 0 — so three
client mutations came back "survived" without a single assertion having run.
Survival and never-ran are the same observation unless something distinguishes
them, which is what the no-op control is for in one direction; the other
direction needs the runner to prove it executed the suite it named.
