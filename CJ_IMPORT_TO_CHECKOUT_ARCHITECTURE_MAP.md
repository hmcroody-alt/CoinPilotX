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
| 12 | Supplier fulfillment | merchant-initiated; destination + lane read from stage 11, never from the request | `services/business_os/suppliers/fulfillment.py` | `business_os_supplier_intents` + `business_os_supplier_outbox` |

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
11. ~~**The buyer's order timeline reads a field that is never served.**~~
    `mobile-native/src/api/ordersDashboard.ts:216-234` — `variantOf(deliveryType)`
    switched on a `delivery_type` taken off the *order* payload. Measurement
    (`scripts/probe_order_lane.py`) showed the field is absent at the top level
    *and* on the joined listing: `pulse_buyer_order_response` names its listing
    columns explicitly and `delivery_type` is not among them. So the argument was
    always `undefined`, the `"pickup"` branch was unreachable, every order in the
    app rendered the shipping strip, and `escrowPresentable` — which is
    `variant === "pickup"` — was permanently false, making the escrow safety
    panel unreachable UI. The seller's copy had its own version:
    `variantOf(String(order.item_type || ""))` passed a row kind into a parameter
    named `deliveryType`. Fixed by serving the lane checkout had already frozen
    onto the order, not by adding a seventh derivation; see "The fourteenth seam"
    below.

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

## The fourteenth seam: the order forgot which lane it was placed on

The thirteenth seam fixed how a *listing* declares its lane. This is the order
side, and it is a different mistake with the same shape — with one twist that
makes it worse: the answer was already on the wire.

`mobile-native/src/api/ordersDashboard.ts` picks which progress strip a buyer
reads:

```ts
function variantOf(deliveryType?: string): OrderTimelineVariant {
  const d = String(deliveryType || "").toLowerCase();
  return d === "pickup" || d === "local" ? "pickup" : "shipping";
}
```

and fed it `order.delivery_type || order.listing?.delivery_type`. Its own comment
said the payloads "do not always carry" the field. `scripts/probe_order_lane.py`
replaced *always* with a number. Publishing a pickup-only listing through
`/api/pulse/marketplace/listings/create`, buying it through
`/api/pulse/payments/checkout`, and reading `/api/pulse/orders`:

| what the app asks for | what the payload has |
| --- | --- |
| `order.delivery_type` | absent |
| `order.listing.delivery_type` | absent |
| `order.fulfillment_kind` | absent |
| `order.listing.listing_type` | `physical` |
| `order.listing.listing_metadata.delivery_options` | `pickup` |
| `metadata_json.fulfillment.kind` | `pickup` |

Not "not always". Never. `pulse_buyer_order_response` names its listing columns
explicitly and `delivery_type` is not among them, and nothing adds a top-level
one. The argument was `undefined` for every order the app has ever rendered.

So `variantOf` had one reachable branch. Three consequences, in increasing order
of seriousness:

1. Every order — pickup, shipping, digital, booking — drew the shipping strip.
   A buyer who arranged to collect an item in person was told it was "Being
   packed", then "On its way", and never that it was ready.
2. `escrowPresentable` is `ordersEscrowIsLive() && variant === "pickup"`. With
   the flag fully on it was still false for every order, so the escrow safety
   panel was **unreachable UI** and the flag gating it gated nothing. The
   seventh corollary again: a guard nothing can satisfy.
3. The seller's copy was broken independently:
   `variantOf(String(order.item_type || ""))` — a row kind passed into a
   parameter named `deliveryType`. `item_type` reads `marketplace_product` on
   every marketplace row, so that path was shipping-only by construction too.

### Why the existing test could not have caught it

`ordersDashboard.test.ts` had a whole `describe` block for escrow gating, built
on this fixture:

```ts
const pickupBuyer = { id: 1, amount_cents: 100, status: "paid",
                      listing: { delivery_type: "pickup" } };
```

The fourteenth corollary, unchanged and now on a second subsystem: a payload the
server cannot produce. The block was green while the feature it covered was
unreachable.

The `cross-view consistency` test is the more interesting failure. It asserts
that a buyer order and a seller order with the same id resolve to the same
variant — exactly the right property — and it passed because *both* derivations
were shipping-only. Two broken readers agreeing on the wrong answer is what it
was measuring. An agreement assertion is only worth its name on an input that
could make the two disagree, which is why the replacement iterates the lanes and
also pins that the two sides differ where they must (`counterpartyName`,
`raw.seller` vs `raw.buyer`) — otherwise the fix for the agreement is to have one
function call the other, which the battery duly proposes as mutation 15.

Nor was this on the ledger. `ORDERS_MOCK_DATA_GAPS` enumerates seven things the
live payload cannot answer, and names "pickup lifecycle states" among them — the
sub-phases *within* the pickup strip. That the strip itself could never be
selected was not on the list. The eleventh corollary, exactly: an enumeration
cannot notice what was never on it.

### The fix is a read, not a derivation

The obvious repair — send the listing's lane on the order — would have been a
seventh derivation of the fact the thirteenth seam exists to stop having seven
of, and it would have been wrong twice over.

Checkout already freezes the answer. `bot.py` resolves the kind, then calls
`resolve_choice` to settle it against the buyer's answer, then
`marketplace_fulfillment.snapshot(kind, details)` into
`seller_transactions.metadata_json`. That function's docstring already said why
it exists: so an order read back next year "still says where it was going ...
even if the seller has since edited the listing." `services/marketplace_cart_routes.py`
writes the identical key from the cart lane.

And `pulse_buyer_order_response` parses that metadata — `json.loads(raw["metadata_json"])`
— and had never read the key.

The frozen value is better than a fresh derivation on two counts that a listing
lookup cannot recover:

- **It is settled.** A listing offering both lanes resolves to
  `shipping_or_pickup`; only the buyer's answer at checkout narrows it.
  Re-deriving recovers the ambiguity, not the choice.
- **It is historical.** The seller can edit, relist, or delete the item
  afterwards. The order still has to say where that parcel went.

So `marketplace_fulfillment.order_kind(metadata, listing)` is the inverse of
`snapshot`: frozen kind first, validated against `KINDS` so a corrupt snapshot
falls through rather than being echoed as fact; then the listing, for rows
written before the snapshot existed; then `""` — *not* `"shipping"`, because an
order that never recorded a lane should not have one invented for it at the
server, which is how the client came to trust a field that meant nothing.

Both serializers serve it as `fulfillment_kind`, and `variantOf` folds down from
it. The in-person kinds — `pickup`, `service_in_person`, `booking_in_person`,
`event_in_person` — take the pickup strip, because those are the orders whose
goods change hands rather than travelling, and they are exactly the orders for
which the escrow panel's advice about meeting a stranger is meaningful. Anything
undecided or unrecognised stays on shipping: the original comment had one sound
instinct, that pickup unlocks the safety panel and is therefore the worst thing
to guess, and that is kept.

One field was deliberately *not* added. `delivery_type` is still not selected for
the order's listing join, and `order_kind`'s docstring says why: an order
serializer normalises the listing type first, `effective_listing_type` never
returns empty, so `delivery_lane` cannot reach its column branch for any row a
serializer can hand over. Selecting it would put the misleading field back on the
wire for a branch that cannot execute — and back within reach of the next reader.

### What the battery measured

`scripts/mutation_order_lane.py`, 15 real mutations plus one inverted rename and
a no-op control. Two survived the first run, and unlike the thirteenth seam's two
survivors these were gaps in the assertions, not errors in the battery:

- **The client keeping `"local"` as a fallback survived.** No test passed a lane
  word as a `fulfillment_kind`. `order_kind` only ever emits a member of `KINDS`,
  and `local`/`meetup` are listing vocabulary, not kinds — so a client that
  accepts them is still speaking the old language, and a payload that regressed
  to sending them would be honoured silently instead of failing. Closed by
  pinning the vocabulary boundary.
- **Making `unifySellerOrder` delegate to `unifyBuyerOrder` survived**, which is
  the mutation described above: it satisfies an agreement assertion by removing
  one of the two things being compared. Closed by asserting the two sides still
  differ where a perspective must.

Final run: 15 of 15 real mutations caught, the inverted rename correctly ignored,
the no-op control correctly survived.

---

## The fifteenth seam: two strips for eleven kinds

The fourteenth seam made the settled `fulfillment_kind` reachable by the orders
dashboard. This is what the client did with it once it arrived.

`OrderTimelineVariant` was `"shipping" | "pickup"`. The server can freeze eleven
kinds onto an order — `services/marketplace_fulfillment.py` `KINDS` — so the fold
from eleven to two put everything that is not handed over in person onto the
parcel strip.

`scripts/probe_order_timeline_kinds.py` published one listing per lane through
`/api/pulse/marketplace/listings/create`, bought each through
`/api/pulse/payments/checkout`, and printed the kind the server froze beside the
words the app would put on the screen:

| lane published | server kind | strip | buyer reads |
| --- | --- | --- | --- |
| physical shipping | `shipping` | shipping | Order placed → Being packed → On its way → Delivered |
| physical pickup | `pickup` | pickup | Reserved → Pickup scheduled → Picked up → Complete |
| digital download | `digital` | **shipping** | Order placed → **Being packed** → **On its way** → Delivered |
| service remote | `service_remote` | **shipping** | Order placed → **Being packed** → **On its way** → Delivered |
| service in person | `service_in_person` | pickup | Reserved → Pickup scheduled → Picked up → Complete |
| event online | `event_online` | **shipping** | Order placed → **Being packed** → **On its way** → Delivered |
| event in person | `event_in_person` | pickup | Reserved → … |
| booking remote | `booking_remote` | **shipping** | Order placed → **Being packed** → **On its way** → Delivered |
| booking in person | `booking_in_person` | pickup | Reserved → … |

Four of nine measured, and enumerating all eleven kinds gives five that ship no
parcel and were told they were in the post: `digital`, `service_remote`,
`service_choice`, `event_online`, `booking_remote`.

Three consequences, established by reading the consumers rather than guessing:

1. The labels above. A buyer who downloaded a file was told it was being packed.
2. `previewShipBy` in `OrdersManagerScreen.tsx` invents a three-day ship-by
   countdown, and its only guard is `order.variant !== "shipping"` — so every
   digital and remote order got a fabricated shipping deadline.
3. `sellerActionsFor` offered the seller of a download "Mark packed" and a "Mark
   shipped" disabled with *"Add a tracking number before marking this order
   shipped"* — a precondition a downloadable file can never meet. The seventh
   corollary in its action form: a control with no reachable path to being usable.

### Two root causes, both already named in this document

**One derivation answering two different questions.** `escrowPresentable` was
`ordersEscrowIsLive() && variant === "pickup"`, which fused *which strip
describes this order's progress* with *do the buyer and seller end up in the same
room*. While there were exactly two strips the two questions had the same answer,
so nothing distinguished them. The moment an appointment needs its own strip they
diverge, and the fused version costs both sides: an in-person haircut can only be
given stranger-safety advice by also being described to the buyer as a parcel
awaiting collection, and a video consultation cannot be described as an
appointment without losing advice it never needed. The fix splits them into
`timelineVariantOf` and `orderIsInPerson`, which now disagree on three of the
eleven kinds — and disagreeing is the whole point.

**A second, coarser copy of a vocabulary that already existed.**
`mobile-native/src/api/marketplaceFulfillment.ts` already held
`MarketplaceFulfillmentKind`, `isScheduledKind`, `fulfillmentTypeLabel` and
`fulfillmentDestinationSummary` — the last of which tells the buyer at checkout
that a digital purchase is *"Delivered to your PulseSoc account"*, one screen
before the orders list said *"Being packed"*. `ordersDashboard.ts` kept its own
`IN_PERSON_KINDS` string `Set` instead, and the app contradicted itself across
two screens. It now imports the vocabulary, and `DIGITAL_STEPS` reuses the
checkout's exact wording rather than inventing a second account of where the
purchase went.

The map from kind to strip is typed as a total
`Record<MarketplaceFulfillmentKind, OrderTimelineVariant>` on purpose: a twelfth
kind added to the union fails the typecheck at the map, rather than falling
through a `Set` membership test onto the parcel strip. That is the eleventh
corollary — an enumeration cannot notice what was never put on it — bought with
a type rather than with a walking check, because here the compiler can walk.

`OrderTimeline.tsx` carried a second `variant === "pickup" ? … : …` of its own,
which is how a two-step digital strip would have been drawn against a four-step
reached index. Both now call `stepsForVariant`.

### The half that was refused

`pulse_buyer_order_response` already serves `digital_files` —
`[{name, download_url}]` — on every paid digital order (bot.py:93318), backed by
a real streaming route at
`/api/pulse/marketplace/digital-files/<id>/download` that verifies the requester
bought the listing, and pinned by a backend test at
`tests/test_marketplace_listing_types.py:522`. Greps found **zero readers**: not
in `mobile-native/src`, not in a template, not in a static script. The buyer
pays, the file sits on the wire, and no surface hands it over.

Shipping a download control anyway would have been the wrong fix. The route
authenticates through `api_account_user()` and the native app holds its token in
expo-secure-store rather than a browser cookie, so `Linking.openURL` would open a
401 in Safari; `pulseApi.ts` exposes no token accessor, and adding one is a
session-layer widening that has nothing to do with this seam. So the blocker is
declared as the eighth `ORDERS_MOCK_DATA_GAPS` entry with the specific backend
work named, and `DIGITAL_STEPS` says *"Delivered to your account"* rather than
*"Ready to download"* — true, and implying no control that does not exist.

### What the battery measured

`scripts/mutation_order_timeline.py`, 19 real mutations plus one inverted rename
and a no-op control, run against both suites at once — the derivation test and
the render test — because the seam lives exactly between them. All 19 caught on
the first run, which is the first time in this chain that has happened, and the
reason is that the tests were written from the probe's table rather than from the
code: the assertions are on the words a buyer reads, not on the variant string.

Also recorded, not fixed: `ordersAwaitingSeller` filters
`status !== "complete"`, and `normalizeStatus` never emits `"complete"` — plain
"complete"/"completed" falls through to `"pending"`. A dead clause in a count,
harmless today because the `"delivered"` clause covers the same orders.

---

## The sixteenth seam: a refusal nobody could answer

This one is not in the orders half of the pipeline at all. It is at the very
front — the step between importing a supplier product and having something that
can be published — and it stopped the ordinary import of any multi-variant CJ
product dead, with an error message that was the name of the error.

`scripts/probe_dropship_multivariant_publish.py` runs the real importer and the
real publish evaluator over the same product at three selection widths:

| variants the merchant selected | `marketplace_product_sources.provider_variant_id` | publishable | problems |
| --- | --- | --- | --- |
| one (deselected by hand) | `'PROBE-1-V1'` | **True** | — |
| two (the supplier screen's default) | `None` | **False** | `['SUPPLIER_VARIANT_UNBOUND']` |
| four (a t-shirt in four sizes, default) | `None` | **False** | `['SUPPLIER_VARIANT_UNBOUND']` |

The only case that worked was the one where the merchant had gone out of their
way to deselect variants. `SupplierProductScreen.defaultSelection` pre-selects
*every* in-stock variant, so the second and third rows are the ordinary path and
the first is the exception.

Five steps, each of them individually correct:

1. The import screen pre-selects every in-stock variant.
2. `importer._import_one` therefore passes `provider_variant_id=None` to
   `link_source`, because `len(chosen) != 1`. It refuses to guess which variant a
   buyer would receive, and that refusal is right.
3. `drafts._validate` appends `SUPPLIER_VARIANT_UNBOUND`, because the buyer's
   checkout has **no variant picker at all** and charges one listing-level price.
   An unbound dropship listing is one a buyer can pay for and nobody can ship.
   That refusal is also right.
4. `SUPPLIER_VARIANT_UNBOUND` was absent from `PUBLISH_PROBLEMS` in
   `mobile-native/src/api/dropshipping.ts` and from `PROBLEM_COPY` in
   `ReviewImportedProductScreen.tsx`. The screen renders an unrecognised code
   verbatim — deliberately, because a blank line is worse — so the merchant read
   the string `SUPPLIER_VARIANT_UNBOUND` under the heading "Before this can go
   live".
5. `bind-product`, the one operation that can satisfy the refusal, had **zero
   callers**. Greps across `mobile-native/src`, `templates/` and `static/`
   returned nothing.

So the guard was correct, its diagnosis was correct, and between them they
produced a merchant holding a draft that could never be published, told so in a
language they do not speak, with no control anywhere in the app that would have
changed it.

### Why the binding is not made per-variant

The obvious fix is to stop having a single listing-level binding: every chosen
variant already has its own `marketplace_listing_variants.provider_variant_id`,
fully populated by `importer._write_variants`, and `fulfillment.create_intent`
already receives `item["vid"]`. The payload supports it today.

It was refused, and for the same reason the guard exists. The buyer's checkout
offers no variant selector — `marketplace_variants` is imported by the suppliers
package and by nothing else, and the purchase path charges one listing price.
Per-variant fulfilment without a buyer-side picker would ship whichever variant
the code happened to pick, which is precisely the defect
`SUPPLIER_VARIANT_UNBOUND` was written to prevent. A one-variant-per-dropship-
listing contract with a merchant who *chose* the variant is weaker than a variant
picker and strictly stronger than a guess.

Note the two `provider_variant_id` columns, because confusing them produces a
listing that looks bound to every screen and is still NULL where `create_intent`
reads: `marketplace_listing_variants` has one per chosen variant and is always
populated; `marketplace_product_sources` has one per listing and is the binding.
`gateway.get_product_binding` resolves only the second, keyed on `listing_id`
with no variant argument.

### What the fix had to be

Not the guard. The answer to it:

- `get_draft`'s `supplier` block now serves `provider_product_id` and
  `provider_variant_id`. Merchant-private, on a payload that already carries
  `supplier_cost_cents`; §27/§95 are about what reaches a buyer, and nothing
  buyer-facing reads this function.
- `bindDraftVariant` in the mobile API layer, returning `void` on purpose. The
  draft is what every surface reads, and re-reading it is how the caller learns
  the refusal cleared. Trusting the bind response would be trusting a second copy
  of the verdict.
- `ReviewImportedProductScreen` asks *"Which variant are you selling?"*, in two
  steps — pick, then confirm — because `link_source` accepts NULL→a variant and
  answers `binding_conflict` to variant A→variant B. A single tap would make a
  mis-tap permanent. Once bound the screen *states* the answer instead of
  offering a chooser that cannot change it.
- The confirm path merges the server's `supplier`, `variants` and `validation`
  rather than calling `adopt`, so a merchant who retitled the product and then
  answered the refusal does not watch their typing vanish. And it re-reads the
  verdict rather than assuming it: binding succeeding is not the same claim as
  the draft being publishable.

Two mechanisms now hold the enumeration together, because it exists in two
languages and no compiler spans them. `PROBLEM_COPY` is a total
`Record<PublishProblem, …>`, so a code added to the union without copy fails the
typecheck — proved by injecting one, which produced `TS2741`, rather than
asserted. And `tests/dropshipping/test_publish_problem_copy.py` crosses the
boundary the compiler cannot: it enumerates the backend's codes from
`_validate.__code__.co_names` — the global names that function actually reads,
so a code declared and never appended does not count — and checks the mobile
list and the copy table name exactly those. It also asserts that the remedy has
a caller, because that is the half of this seam a copy check would have missed.

### The fixture that was measuring a different program

`DropshippingScreens.test.tsx` had `fulfillmentMode: "SANDBOX"` in its draft
fixture — an environment mode in a fulfilment-mode field, a value
`marketplace_product_sources` cannot hold; `MODE_STOCKED` and `MODE_DROPSHIP`
are the two. So all eighty-five tests built on it were exercising a listing that
is neither dropshipped nor stocked, which is exactly why none of them noticed
that a dropship listing needs a binding. The fourteenth corollary, second
sighting, three seams later.

### What the battery measured

`scripts/mutation_dropship_variant_binding.py`, 18 real mutations plus one
inverted rename and a no-op control, run against four checks: the cross-language
copy pin, the backend publish suite, the screen suite, and `tsc --noEmit`. Four
rather than one because two of the new defences are typecheck-only and two are
cross-language, and the report names which check caught each mutation — the
distribution is the argument for all four existing: mutations 1–4 and 15 were
caught only by the copy pin, 5–7 and 16–17 only by the publish suite, and 8–14
and 18 only by the screen suite.

Two survived the first run, and both were worth the run:

- Clearing the merchant's pick in the `catch` of a failed bind. The test asserted
  the error text and that the chooser was still on screen; it did not assert that
  the pick survived. So *"try again"* appeared beside a button that had gone
  disabled again — error with no way back to the action, the same shape as
  error-and-empty. The test now asserts the radio is still checked and the
  confirm button still enabled.
- Editing the confirm button's explicit `accessibilityState={{ disabled: … }}`
  changed nothing observable, because `Pressable` derives that state from the
  `disabled` prop and overrides whatever it is handed. The mutation was aimed at
  the copy that cannot win. That is the tenth corollary in miniature — two copies
  of one derivation, where the framework guarantees which one loses — so the
  redundant copy was deleted rather than tested, and the mutation re-aimed at
  `disabled`, where it is caught.

---

## The seventeenth seam: the sale that owed a supplier purchase to nobody

Gap 13 left a merchant able to bind a variant and publish a dropship listing.
That listing is for sale. This seam is the next question, and it is the one the
whole feature exists to answer: somebody buys it — then what?

`scripts/probe_dropship_paid_order_fulfillment.py` publishes a bound,
single-variant dropship listing through the real importer and the real publish
evaluator, then writes the paid `marketplace_orders` row exactly as
`bot.pulse_upsert_marketplace_order` projects one from a paid transaction, and
prints every supplier-side record that exists afterwards:

| after a paid sale | rows |
| --- | --- |
| `marketplace_orders` (`status='paid'`, `amount_cents=2000`) | 1 |
| `marketplace_product_sources` (`fulfillment_mode='DROPSHIP'`, variant bound) | 1 |
| `business_os_supplier_intents` | **0** |
| `business_os_supplier_outbox` | **0** |
| intents naming that order | **0** |

Nothing was missing from the sale. The binding a checkout path would need to
resolve is right there in the same probe output, holding every field a supplier
order requires — `supplier_connection_id`, `business_id`, `store_id`,
`provider_product_id`, `provider_variant_id`, and `supplier_cost_cents: 820`
against the buyer's `2000`. The money is collected, the margin is known, the
supplier is identified, and no record anywhere says a purchase is owed.

Six greps say why, and none of them is a bug on its own:

1. `fulfillment.create_intent` is the only writer of
   `business_os_supplier_intents`.
2. Its one production call site is the `fulfillment-intents` action in
   `services/business_os_supplier_routes.py`.
3. That action has zero callers in `mobile-native/src`, `templates/` or
   `static/` — the seventh corollary's second sub-tell, one seam after it was
   written: a writer reachable only from pytest is not reachable.
4. `worker.py` only claims intents that already exist; `supplier_worker.py` is
   not in the Procfile.
5. `bot.py` — where checkout lives — contains no reference to
   `marketplace_product_sources`, `supplier_binding`, `get_product_binding` or
   `fulfillment_mode`. The paid-order writer cannot see that a listing is
   dropshipped.
6. And the module could not be *asked*. Of what `fulfillment` exposes,
   `get_intent`, `dispatch` and `settle` are each keyed on an intent that
   already exists, and `claim` takes a lease over the same table. There was no
   function answering "which of my orders needs a supplier order placed?", so
   the absence was not observable from inside the layer that had it.

### Why the obligation is derived on read

The obvious fix is to call `create_intent` from the payment webhook. Measured
off its own bytecode, it will not go: `__code__.co_consts` holds a `300`-second
freshness window on the shipping quote, and the cost check is an exact
`int(total) != expected_supplier_cost_cents`, with `invalid_quote`,
`supplier_cost_unverified` and `supplier_cost_reapproval_required` among its
refusals. A webhook holds none of that. It has an order id and a payment; it has
no fresh quote and no merchant who has agreed to a number.

That is not an oversight in `create_intent` — it is what the function is.
It is a merchant *approval* action, and approval requires a merchant. So the
obligation is not written at payment time at all; it is **derived on read**, by
`list_obligations`, from the three records that already exist: the paid order,
the dropship binding, and the intent if one has been made. This is the
fifteenth corollary applied before the fact rather than after — the answer was
already in the data, unread — and it keeps the approval where it belongs
instead of manufacturing a fake one at checkout.

Two things in that query are only correct because they were measured:

- **`CAST(o.id AS TEXT)` on the join.** `marketplace_orders.id` is `INTEGER`;
  `business_os_supplier_intents.order_id` is `TEXT`, because `create_intent`
  writes `str(order_id)`. On PostgreSQL `i.order_id = o.id` is a type error; on
  SQLite it is worse — it silently matches nothing, so every obligation would
  read as never placed and the list would look right. No behavioural test on
  SQLite can see that, which is why two tests in this seam read the SQL literal
  rather than the result.
- **`LOWER(o.status) = 'paid'` is the whole paid vocabulary.**
  `pulse_upsert_marketplace_order` hardcodes `'paid'` in both its `VALUES` and
  its `ON CONFLICT … DO UPDATE`, and the DDL default `'pending_payment'` is the
  only other value the column has ever held. The tempting import was
  `marketplace_listing_types.PAID_ORDER_STATUSES`, three states wide — and that
  constant is the vocabulary of `seller_transactions` and
  `creator_transactions`, not of this table. A shared constant that belongs to a
  different table is the eighth corollary wearing a helpful name.

The state a merchant reads is
`outbox_state or ("UNKNOWN" if intent_id else "AWAITING_SUPPLIER_ORDER")`,
deliberately outside the outbox's own six-state vocabulary, because "no
supplier order has been placed" is not a state the outbox can hold — there is
no row. An intent with no outbox row reads `UNKNOWN`, which is the honest answer
and not the same answer.

### A name collision, and the fix that was not an exemption

The supplier's own word for where an order stands lives in the outbox column
`provider_status`. Shipping it to the client under that name failed
`mobile-native/src/entitlements/__tests__/noClientTierInference.test.ts`, which
lists `provider_status` among the raw membership fields no unlisted file may
hold — because on this platform that name means Stripe's *subscription* status.

Both obvious repairs were refused. Allowlisting `api/dropshipping.ts` would
exempt it for `premium_status` too, and narrowing the guard would trade a
permanent hole for a naming convenience. The wire field was renamed instead:
the column is still `provider_status`, the payload carries
`supplier_order_status`, and the SELECT aliases it with the reason written
above it. It is now pinned from three directions — the entitlement guard, a
backend test asserting `provider_status` is absent from the obligation, and a
battery mutation that removes the alias.

### The comment that asserted a failure mode its own code could not exhibit

The battery aimed a mutation at the `!connectionId` check in
`DropshippingOrdersScreen`, which a comment of mine said "must be checked
before the scope phases" or the screen would skeleton for ever. The mutation
survived, and the comment was the thing that was wrong: `useDropshippingScope`
always initialises to `{ phase: "loading" }` and reaches `ready` a microtask
later, so the first effect pass cannot see `ready` and the reordering is
behaviour-preserving in all four phases.

The remedy was not a test. Catching that mutation would require the hook to
answer synchronously, which it does not, and a test asserting the *order of
lines* would be the thirteenth corollary. So the mutation is recorded as
inverted with its reasoning, and the comment now says what is true — that the
ordering is currently cosmetic, that it stops being cosmetic the day the hook
answers from its cache, and that no test can hold it there meanwhile.

### The screen that made every assertion in the file pass over nothing

`DropshippingSyncScreen` had zero tests, while rendering the same gap list the
supplier-orders screen does. Every defect that list was introduced to prevent
could have been reintroduced there and nothing would have said so. It is the
same shape the supplier-orders screen had before this seam — a surface over
which a suite's assertions are all vacuously true — and the battery found it
by mutating the gap prose and watching nothing fail.

The gap note itself was the third root cause. `DROPSHIPPING_DATA_GAPS` claimed
this layer "can create and read a single intent by id" and merely lacked an
enumeration, when nothing reachable had ever created one; and it named the
table `business_os_supplier_fulfillment_intents`, which does not exist. The real
tables are `business_os_supplier_intents` and `business_os_supplier_outbox`. A
gap note is a claim like any other, and this one had never been read against the
schema.

**And neither had this document.** The first draft of this section said that
string "occurs exactly once in this repository — in that note", which was itself
asserted rather than measured: the grep returns row 12 of the stage table at the
top of this file, wrong since 2026-09-10 and propagated from the same note. The
sixteenth corollary says a comment whose subject lives elsewhere is a
hypothesis; a *count* of occurrences is the same kind of claim, and the cost of
checking it is one grep. Row 12 now names both real tables.

### What the battery measured

`scripts/mutation_dropship_supplier_obligations.py`, 20 mutations against four
checks: the cross-language copy pin, the backend obligation suite, the two jest
suites, and `tsc --noEmit`. **16 real mutations caught, 3 inverted correctly
ignored, 1 no-op control survived.** Four checks again because the distribution
is the argument: the join cast and the four filters are caught only by the
backend suite, the state enumeration only by the copy pin, the merchant-visible
behaviour only by jest.

Four findings the three suites had missed, and only two of them were test gaps:

- The stale-backlog test was vacuous. It asserted a row was gone after a failed
  refresh, but the list is rendered `data={stateBlock ? [] : rows}`, so the rows
  disappear whenever an error owns the screen whether or not the state was
  cleared. It now asserts the sandbox card — drawn from the header regardless of
  state — is gone too.
- `DropshippingSyncScreen`'s missing suite, above.
- Two of my own comments overstating the code beside them: the `!connectionId`
  ordering, and a client-side re-derivation of `supplier_order_placed`. The
  second cannot be caught honestly either — the server writes
  `supplier_order_placed = intent_id is not None`, so catching the mutation
  needs a fixture with `intent_id` set and the flag false, a row the backend
  cannot emit. The fourteenth corollary forbids exactly that fixture.

The battery's own metadata was a defect of the same family. The inverted set was
keyed by mutation *index*, so inserting the alias mutation mid-list renumbered
everything after it and silently relabelled two real mutations as inverted —
which is to say, stopped demanding that anything catch them. It is keyed by name
now, with assertions that the names are unique and that every declared name
exists.

---

## The eighteenth seam: an obligation nobody could discharge

The seventeenth seam ended with a merchant able to *see* that a paid sale owed a
supplier purchase. This one measured what they could do about it, and the answer
was nothing. Four blockers, none of them a symptom of the others.

**The destination was not on the obligation, and was one `json.loads` away.**
`create_intent` needs `shippingCountryCode`, `shippingCountry`, `shippingProvince`,
`shippingCity`, `shippingCustomerName` and `shippingAddress`. The buyer supplied
every one of them at checkout and `marketplace_fulfillment.snapshot` froze them
onto `seller_transactions.metadata_json` under `fulfillment.details`, already
cleaned, length-capped and tag-stripped by `validate_details`. `list_obligations`
joins `marketplace_orders` to that exact row — `seller_transaction_id` and
`seller_transactions.id` are both INTEGER, so the join needs no cast, unlike the
intent join four lines below it — and read past the column. The fourteenth
corollary's own shape, second occurrence in three seams: the fact was frozen on
the record the serializer was already holding.

**The obligation reported the product's supplier code where its only consumer
needs the variant's.** `marketplace_product_sources.external_sku` and
`marketplace_listing_variants.sku` are two adjacent columns from two levels of
one hierarchy, and the obligation reported the first while `create_intent`
matches the second. The root cause is a two-line divergence in the importer:
`_write_variants` reads `variant.get("external_sku")` (importer:286) and
`link_source` reads `product.get("external_sku")` (importer:330). CJ states a SKU
per variant, so in the ordinary single-variant import the product-level column is
NULL — which means the obligation displayed nothing, the merchant had nothing to
check against CJ, and the failure looked like missing data rather than the wrong
column.

**The endpoint that quotes freight had no server-side assembler, and therefore
zero callers.** `gateway.read("shipping", …)` appears nowhere in
`mobile-native/src`, `templates/` or `static/`. That is the seventh corollary's
second sub-tell exactly as it was written one seam earlier — but with a cause
worth separating: the request could only be assembled on the server, because it
needs the frozen address, the bound variant, the parcel weight and the
warehouse the stock actually sits in. A client cannot build it without being
handed the supplier's cost basis, which §27 forbids. So the endpoint was not
uncalled by oversight; it was uncallable by design, and the missing piece was
`quote_for_order`.

**And the destination `create_intent` did take, it took from the request body.**
A merchant-authenticated call could name any address at all. The address the
buyer paid to ship to was frozen on the transaction and nothing compared the
two. This is the first defect in the chain that is not a wrong number but a
wrong *authority*, and it is the same root cause wearing a different coat: the
server asserted the destination on its caller's word instead of measuring the
one the buyer paid for.

### A fifth shape: the table that existed in one language

`checkoutCountries.ts` held a 64-entry ISO-3166-1 → name map under a comment
saying "The server never sees them; it sees the ISO-3166-1 alpha-2 code, which is
the contract." True of the buyer's half of the wire and false of the supplier's:
CJ's create-order takes `shippingCountryCode` *and* `shippingCountry`, and the
second is a name. The sixteenth corollary again — a comment whose subject lives
in another language — but the remedy is not a comment fix. The server needs its
own copy of the table, and `test_country_names_match_the_picker`, named in the
comment I wrote beside it, did not exist until this seam. It does now, and pins
all 64 entries in both directions plus every spelling.

The two fallbacks are deliberately opposite and a test says so.
`countryName` in the picker answers the code itself, so an unrecognised country
the server *does* accept stays selectable; `country_name` on the server answers
`""`, so its caller can say the address is incomplete. Making the server match
the picker would send `XK` to a supplier as the name of a country.

### DESTINATION_INCOMPLETE is not defensive

`marketplace_fulfillment._REGION_REQUIRED` holds ten countries, so a buyer in the
United Kingdom completes an entirely valid checkout with no `address_region`,
while CJ requires `shippingProvince` unconditionally. A UK dropship sale is
therefore a real paid order that genuinely cannot be placed, and naming the
field is the difference between a merchant fixing it and a merchant watching a
row say "awaiting" forever. That is why there are seven blockers and not six.

### What the battery measured, including in its own author

Twenty-five mutations: twenty-two real, all caught; two inverted with their
reasoning; one no-op control. Two gave the wrong answer on the first run and
both were mine.

- **A guard that could not fire, defended by a comment claiming it could.**
  `supplier_destination` re-checked that the country code it was about to send
  was two characters long, commented as "what distinguishes a country this
  platform can ship to from one it can only spell". It distinguishes nothing:
  `country_name` answers `""` for any code its table does not hold, every key in
  that table is alpha-2, and the very next field assembled is that name — so a
  misshapen code was already refused one line later with the same blocker. No
  test could fail on its removal because its removal changes no outcome. The
  check is gone; the invariant it gestured at is now one assertion on the table
  itself, where it is true, and load-bearing on both sides because the picker's
  `toCountryOptions` silently drops any code whose length is not two.
- **A rename mutation reported as pinned when what it had found was its own
  `NameError`.** `supplier_destination` names its local four times and my
  companion-anchor tuple covered two. A half-done rename is caught for the wrong
  reason and prints as a pass. The runner now counts the identifier inside that
  one function and refuses to run the mutation while any reference survives —
  and counts it in the *code*, because the first version counted the docstring
  and reported a total rename as partial, which is the same error mirrored.

### What the tests had to be

The gap-14 obligation suite was not measuring the query that runs in production.
Its helper wrote `marketplace_orders` alone, so once the obligation started
reading the frozen address every sale in the file would have carried a
manufactured `DESTINATION_MISSING`. Worse, the suite could not run at all —
`seller_transactions` was absent from the fixture, and 24 of its 30 tests were
erroring on `no such table` while the file was nominally part of a green
directory. The helper now writes both halves, because production has both: the
transaction Stripe settles and the order projected off it. `details=None`,
`kind=` and `transaction=False` give every reachable database state a name, so a
blocker appearing on the default paid sale is a finding rather than the fixture's
fault.

The two-call flow is pinned by composition rather than by description:
`quote_for_order`'s `expected_supplier_cost_cents` is handed to `create_intent`
unchanged and the intent is asserted non-duplicate. A test that asserted 500 on
both sides separately would pass on two functions that disagree.

`tests/dropshipping/test_supplier_obligations.py` 54, `test_supplier_obligation_copy.py`
16, `tests/business_os/test_cj_fulfillment.py` 46. Directory total 384 with one
file per process; `npm run verify` 383 suites / 6571 tests; protection suite 327
checks across 28 suites.

---

## What kept coming back

Nineteen defects in this chain, nineteen different subsystems, one shape: **a
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
- The order timeline asserted a buyer's lane from `order.delivery_type`, a field
  no order endpoint has ever served — so the pickup branch was unreachable, every
  order rendered as shipped, and the escrow safety panel could not be reached at
  all. The settled lane was already frozen on the order, in the metadata the
  serializer was parsing and not reading. The test covering it supplied
  `listing: { delivery_type: "pickup" }`, and the cross-view test that should
  have caught the disagreement passed because both perspectives were broken in
  the same direction.
- The publish gate asserted, in a comment defending itself, that the ordinary
  import satisfies `SUPPLIER_VARIANT_UNBOUND` without the merchant doing
  anything. Every clause of that sentence was about a screen in another language,
  and the screen does the opposite: it pre-selects every in-stock variant, so the
  ordinary path is the refused one. The code was also missing from the mobile
  copy of the problem enumeration, so the refusal reached the merchant as its own
  identifier — and the only operation that could have answered it had no caller
  on any surface.
- A note listing this layer's remaining gaps asserted that it "can create and
  read a single intent by id" and only lacked an enumeration. Nothing reachable
  had ever created one: the sole writer's only route had zero callers on any
  surface, the dispatch worker is not in the Procfile, and checkout cannot see
  that a listing is dropshipped at all. So a buyer's money was collected against
  a known supplier cost and no record anywhere said a purchase was owed. The note
  also named a table that does not exist — and so, until this seam was written,
  did the stage table at the top of this document.
- The obligation asserted a supplier code by reading the column one level up the
  hierarchy from the one its only consumer matches on, which is NULL in the
  ordinary import — and asserted a destination by taking it from the request
  body, so a merchant-authenticated call could redirect a parcel the buyer paid
  to have sent elsewhere. The address was frozen on the row the query already
  reached. Meanwhile the endpoint that prices the freight had zero callers,
  because its request could only be assembled on the server and no server-side
  assembler existed. Every test was green: they asserted that a reader returns
  what that reader reads.

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

The fifteenth is the one that pays for all the others, because it turns the
whole list from a catalogue of mistakes into a place to look first: **before
adding a field, search the payload for the answer — it is often already there,
unread.** The order's settled lane had been written to `metadata_json` at
checkout by a function whose docstring existed to explain why, carried over the
wire on every order, and parsed by the very serializer that did not surface it.
The client, meanwhile, read a field that has never existed. Two components each
doing their half of the job correctly, joined by a key nobody read.

The greppable tell is a pair: **find the writer of a frozen record and count its
readers.** `snapshot()` had one writer per checkout lane and zero readers — a
value persisted for posterity that nothing had ever retrieved. A write with no
read is either dead code or a missing feature, and the docstring usually says
which; this one said, in as many words, that it was for reading an order back
later.

Its sub-tell is about the repair rather than the defect, and it is the reason
this seam did not become a seventh derivation: **the inverse of a freeze is a
read, not a recomputation.** Re-deriving a stored fact from its source looks
equivalent and is strictly weaker, because a freeze captures two things a source
cannot return — a *choice* that narrowed an ambiguity, and a *moment* before the
source was edited. When the two disagree, the frozen value is not the stale one.
It is the only one that was ever true.

An agreement assertion earns its own line here, because the cross-view test is
the second one in this document to pass for the wrong reason: **two readers
agreeing proves nothing on an input that cannot make them disagree.** The tenth
corollary said to assert that two surfaces agree rather than what either returns.
This is its necessary companion — vary the input across the axis the two readers
are supposed to be reading, and separately pin that they still differ where they
must. Otherwise the cheapest way to satisfy the agreement is to delete one of the
readers, which is a fix the suite will accept.

The sixteenth is about where a claim is written rather than what it claims:
**a claim about a screen, asserted in a backend comment, is not a measurement of
the screen.** The guard that blocked every multi-variant import was defended by
a comment saying the ordinary path satisfies it — a sentence about
`SupplierProductScreen.defaultSelection`, written in Python, in a file that
cannot import it, by someone who did not open it. It was false in the only
direction that mattered. The tell is grammatical: **a comment whose subject lives
in another language is a hypothesis, and the fix is a probe, not a rewording.**
Twelve lines of script running the real importer against the real evaluator
settled it in one run, and the comment there now quotes that output.

Its sub-tell is the eleventh corollary crossing a language boundary, where the
remedy the eleventh offers — prefer the check that walks — is unavailable:
**a second copy of an enumeration in another language has no compiler spanning
it, and the gap surfaces as a raw identifier on a user's screen.** Inside
TypeScript the fix is a total `Record` over the union, which the compiler can
walk. Across Python and TypeScript nothing can, so the check has to be written by
hand, and the honest way to enumerate the Python side is
`_validate.__code__.co_names` — the names the function actually reads — not a
grep of the declarations above it. Reading the *other* language as source text is
acceptable here for the same reason it was unacceptable in the thirteenth: what
is read is a literal array and the keys of a literal object. Data, not logic. A
test that read a function to decide what it does would be satisfied by whatever
that function said.

And its second sub-tell folds the seventh and twelfth into one sentence, because
this seam had both at once: **a guard whose only remedy has zero callers stops
the user exactly as hard as no guard — they just get a code instead of a broken
order.** `bind-product` existed, was correct, was tested on the server, and was
reachable from nothing. The seventh corollary says to grep for the writers of the
column a guard reads; the addition here is to then grep for the *callers of those
writers on a surface a user can touch*, because a writer reachable only from
pytest is not reachable.

The seventeenth turns the document on itself, because the last two seams were
caught by a battery aimed at prose I had written: **a comment that asserts a
failure mode its own code cannot exhibit is the same defect as a comment that
asserts a screen it cannot import.** The sixteenth corollary caught a Python
comment describing TypeScript. This one needs no second language — the
`!connectionId` ordering comment described a skeleton-for-ever that
`useDropshippingScope` makes unreachable, because the hook always initialises to
`loading`. Both are hypotheses in the imperative mood. The tell is that the
sentence contains a consequence: **if a comment says what *would* happen, either
a test can produce it or the sentence is a guess** — and the honest third option,
where no test can produce it because the code cannot, is to say so. Which is why
three mutations in this seam's battery are recorded as inverted with their
reasoning rather than deleted. A battery that quietly drops the mutations it
cannot catch is reporting a pass rate, not a measurement; one that keeps them and
explains each is the only kind whose "16 of 16" means anything.

Its sub-tell is about the instrument again, and it is the eighth corollary
reappearing inside a test harness: **metadata keyed by position silently
relabels its subjects when one is inserted.** The inverted set was a set of
indices; adding a mutation mid-list moved two real mutations into it, which is
not a cosmetic bookkeeping error — it is the battery ceasing to demand that
anything catch them, while still printing a clean report. Keyed by name, with
assertions that names are unique and that every declared name exists, the same
insertion is a no-op. **Anything that names a test's subjects by ordinal is one
edit away from asserting about the wrong one.**

And a small one worth its line because it cost a red suite: **a fixture value
invented rather than looked up puts the test on the branch you were not
testing.** A connection status of `NEEDS_REAUTH` is not a status this app knows
— `REAUTH_REQUIRED` is — so the screen read the connection as healthy and the
assertion landed on the happy path. The fourteenth corollary says to name the
writer of every field a fixture sets; the cheap version for an enumerated field
is to **ask the shared predicate about the fixture first**, in the test, so a
renamed status fails where it is wrong instead of quietly relocating the test.

The eighteenth is the first one about authority rather than arithmetic, and it is
the same defect with the stakes changed: **a fact the server already owns must
not arrive as a parameter, because a parameter is a claim and the owner is a
measurement.** `create_intent` took `shipping_destination` from its caller and
its caller took it from the request body, so the address the buyer paid to ship
to sat frozen on the transaction with nothing comparing the two. Every other
corollary here describes a green suite over a wrong number; this one was a green
suite over an authorization hole, and it read as a parameter because parameters
are how inputs look. The tell is ownership, not validation — no amount of
sanitising the field makes the caller entitled to name it. **If the record
answers the question, the request must not be allowed to.** The corresponding
test is a signature assertion rather than a behavioural one, because the honest
thing to pin is that the parameter cannot come back: `inspect.signature` over
`create_intent`, refusing `shipping_destination`, `destination`, `address` and
`shipping_address` by name.

Its sub-tell is the one this seam's battery found in its own author, and it is
the thirteenth corollary's inverse: **a branch no test can fail on is not
covered, it is unreachable — and the comment explaining its purpose is the only
evidence it ever had.** The thirteenth says a test that reads source text is
satisfied by whatever the source says. This says the same of a *guard*: the
two-character check on the country code was subsumed by the line after it, so
removing it changed no outcome, no suite could go red, and the sentence beside
it asserting what it distinguished was the entire case for its existence. Only
the mutation battery could find it, because the finding is "nothing failed" and
that is exactly what a passing suite looks like. Written while fixing an
instance of this same family, three files away. **The instrument that measures
whether an assertion is load-bearing is the only instrument that can tell you a
guard is decoration**, which is the argument for running the battery over code
written in the same commit rather than only over the code it was aimed at.
