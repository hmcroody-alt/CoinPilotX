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

Both transitions are production writes and neither has been made.

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
2. **No buyer-side variant selection.** A multi-variant product can only be sold
   at a single price; `VARIANT_PRICE_SPREAD` now refuses the alternative rather
   than guessing, but the real fix is a variant selector on the product page.
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
4. **`vault.seal`/`unseal` wrap everything in `except Exception: raise
   VaultError() from None`**, which turns a caller's mistake into what looks
   like an infrastructure failure. Cost me a false alarm already.
5. **Fulfillment is unreachable for the current connection** —
   `external_shop_id` is unset, so `create_intent` raises
   `shop_binding_required`. This is an honest refusal, and §37 forbids placing a
   real CJ order regardless.
6. ~~**`bot.py:4379-4393` documents a constant that no longer exists**~~, and
   said native "already says 'Price at checkout' on the same card" — which
   native had stopped doing. Rewritten: `PRICE_LABEL_UNPRICED` and
   `MAX_PRICE_LABEL_CENTS` now each state what they are and name the file that
   pins them, and the deleted comment is recorded rather than quietly dropped,
   because a comment outliving its code is exactly how the parity claim above
   survived. Cosmetic in isolation; it is in this list because it is the same
   failure mode as the three seams.

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

## What kept coming back

Five defects in this chain, five different subsystems, one shape: **a number was
asserted rather than measured.**

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

In all five the suite was green, and in all five the green was about the halves
rather than the seam. Where a claim spans two components, this document now
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
