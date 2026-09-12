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
   buyer-visible with a cover the buyer cannot see. What remains is the window
   *before* publication — `list_drafts` selects `l.cover_image_url` with no
   fallback, so the merchant's own Dropshipping products list shows a blank tile
   for a product that has five photos. `scripts/backfill_dropship_cover_image.py`
   closes that, dry-run by default; the dry run reports exactly one affected row
   in all of production. **Not run — it is a production write.**
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
   native had stopped doing. Rewritten: `PRICE_LABEL_UNPRICED` and
   `MAX_PRICE_LABEL_CENTS` now each state what they are and name the file that
   pins them, and the deleted comment is recorded rather than quietly dropped,
   because a comment outliving its code is exactly how the parity claim above
   survived. Cosmetic in isolation; it is in this list because it is the same
   failure mode as the three seams.
7. ~~**A published dropship listing cannot be approved.**~~ The admin Approve
   button 409'd on every listing `drafts.publish` produces, which is why listing
   14 needed a hand-written UPDATE. Fixed via `lifecycle.awaiting_moderation`;
   see "The fifth seam" below. The route now has tests, which it did not before.

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
because that is the claim. Eleven tests; six mutations, six killed.

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
supplier order. A merchant-facing screen is still owed; this is what exists now.

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

## What kept coming back

Nine defects in this chain, nine different subsystems, one shape: **a number
was asserted rather than measured.**

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
