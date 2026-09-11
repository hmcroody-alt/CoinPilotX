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
| 1 | CJ catalog | CJ | `suppliers/cj.py`, `discovery.py` | — (remote) |
| 2 | Supplier DTO | normalizer | `suppliers/normalize.py` | — (in memory) |
| 3 | Import cart | merchant selection | `suppliers/import_cart.py` | `business_os_supplier_import_cart` |
| 4 | Import | importer | `suppliers/importer.py` | `marketplace_listings` + `marketplace_product_sources` + `marketplace_listing_variants` |
| 5 | Merchant review | merchant | `suppliers/drafts.py` | as above |
| 6 | Publication gate | `drafts._validate` | `suppliers/drafts.py` | — |
| 7 | Publish | `drafts.publish` | `suppliers/drafts.py` | `marketplace_listings.status/quantity/price_label` |
| 8 | Moderation | **separate authority** | admin surfaces | `marketplace_listings.approval_status` |
| 9 | Buyer discovery | `lifecycle.is_public` / `public_sql` | `marketplace_listing_lifecycle.py` | `marketplace_listings` ⋈ `marketplace_sellers` |
| 10 | Cart | `price_label` | `marketplace_cart_routes.py` | `marketplace_cart_items` |
| 11 | Checkout → order | Stripe + `pulse_upsert_marketplace_order` | `bot.py`, `marketplace_cart_routes.py` | `seller_transactions` → `marketplace_orders` |
| 12 | Supplier fulfillment | merchant-initiated | `suppliers/fulfillment.py` | `business_os_supplier_fulfillment_intents` |

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
3. **Production listing 14 has `cover_image_url = NULL`** — imported before
   `2b9a36a2`. One-row backfill from `listing_metadata_json.media[0]` is
   available and has not been run.
4. **`vault.seal`/`unseal` wrap everything in `except Exception: raise
   VaultError() from None`**, which turns a caller's mistake into what looks
   like an infrastructure failure. Cost me a false alarm already.
5. **Fulfillment is unreachable for the current connection** —
   `external_shop_id` is unset, so `create_intent` raises
   `shop_binding_required`. This is an honest refusal, and §37 forbids placing a
   real CJ order regardless.
6. **`bot.py:4379-4393` documents a constant that no longer exists**, and says
   native "already says 'Price at checkout' on the same card" — which native
   stopped doing. Comments that describe deleted code are how the parity claim
   in gap-note above survived for as long as it did. Cosmetic, but the same
   failure mode.

---

## What kept coming back

Three defects in this chain, three different subsystems, one shape: **a number
was asserted rather than measured.**

- `publish()` never wrote `price_label`, and the publish test asserted `status`
  and `published_at` and stopped one column short.
- The app's parser claimed parity with the server's in a comment, and every test
  of it picked an example below the threshold where the formats diverge.
- `canPurchaseMarketplaceListing` claimed to answer "can this be bought" while
  only ever consulting stock, and every unpriced fixture in the render suite was
  also out of stock.

In all three the suite was green, and in all three the green was about the
halves rather than the seam. Where a claim spans two components, this document
now prefers a fixture both components read over a sentence describing them.
