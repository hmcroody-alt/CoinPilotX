# Required structured-data mutations for Agent 12

Agent 5 → Agent 12 (adversarial / mutation testing), 2026-10-03.
Branch `search-os/agent-05-structured-data`.

Eighteen mutations. **Every one of them must fail.** A mutation that passes is
not a curiosity — it is the precise statement of a gate that does not exist,
and the four marked **NO GATE** below are exactly that, found by writing this
list rather than by assuming it.

Each row names the mutation, the file to apply it to, and the gate Agent 5
believes will catch it. Where Agent 5 verified the gate by actually applying the
mutation, it says **verified**. Where it is a belief from reading the test, it
says **expected** — treat those as the claims most worth checking, because the
difference between "a test exists" and "a test fails on this" is the whole
discipline here.

Run the gates with `/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python -m pytest`.
System `python3` lacks the dependencies and will fake a pass by collecting
nothing.

---

## A. Serialisation and injection (1–2)

**1. Terminate the script element from a seller's title.**
Set `marketplace_listings.title` to `</script ><svg onload=alert(1)` and render
`/pulse/marketplace/<id>`.
*Gate:* `tests/test_structured_data_sinks.py::test_the_serialiser_still_escapes_the_one_character_that_matters`.
**verified** — and note *why* `<` is the only character that matters: inside a
raw-text `script`, `</script` plus any whitespace closes the element, and the
`>` that finishes an injected tag can be supplied by the page's own following
markup. Nothing strips `<` from a listing title on the way in
(`services/business_os/suppliers/importer.py:443`) or out
(`services/marketplace_web.py:147` collapses whitespace only), so
`serialise_graph`'s escaping is the **only** control standing between a
supplier-supplied title and the page.

**2. Put a bare `<` in supplier description text.**
Same, via `description` rather than `title`, through the storefront's
`ensure_ascii=True` path.
*Gate:* same test, which asserts both `ensure_ascii` modes — because the
storefront asks for the non-default one and a fix that covered only the default
would leave the marketplace open. **verified**

---

## B. Fabricated identity (3–5)

**3. Invent a brand.** Add `"brand": {"@type": "Brand", "name": "PulseSoc"}` to
`marketplace_web.product_jsonld`. "PulseSoc" is the tempting filler and it is
false: PulseSoc is the marketplace, not the manufacturer.
*Gate:* `tests/test_marketplace_storefront.py::test_product_structured_data_carries_no_rating_and_no_review`
(which also asserts `brand`). **expected**

**4. Invent a GTIN.** Add `"gtin13"` to `marketplace_web.product_jsonld`.
*Gate:* `tests/test_marketplace_storefront.py::test_product_structured_data_claims_nobody_elses_identifier`.
**verified** — and this gate did not exist until this branch. The only
identifier-invention assertion in the repo was in `test_marketplace_seo.py`,
against `marketplace_seo`, which is **not the module that renders the live
page**. A `gtin13` added to the live renderer shipped green. Two modules, one
of them tested, is the shape of several findings on this branch.

**5. Invent an MPN.** As above with `"mpn"`.
*Gate:* same test. **verified**

Background for all three: brand, GTIN and MPN are absent from all 20,247
supplier snapshots. There is nothing to backfill, so every value here would be
manufactured. `g:identifier_exists=no` in the Merchant feed is the correct
declaration of that and is already emitted.

---

## C. Fabricated reputation (6–7)

**6. Convert engagement into a rating.** Derive
`aggregateRating.ratingValue` from a listing's like count.
*Gate:* `tests/test_marketplace_storefront.py::test_product_structured_data_carries_no_rating_and_no_review`
and `tests/test_marketplace_seo.py::test_no_rating_is_invented`. **expected**

**7. Convert a PulseDrop or a comment into a `review`.**
*Gate:* same two tests. **expected**

A likes count is not a rating of the product and a comment is not a review of
it. Fabricated ratings are the most heavily penalised structured-data abuse
Google names, and the penalty is domain-wide rather than page-wide.

---

## D. Price and availability truth (8–12)

**8. Collapse a price range into a single invented `Offer`.**
Take one of the three range-priced live listings and emit
`Offer { price: lowPrice }` instead of `AggregateOffer`.
*Gate:* `tests/test_marketplace_storefront.py` covers `PriceView.as_schema_offer`;
`services/marketplace_web.py:384` is the authority. **expected**

This one has a business temptation behind it, which is why it is on the list:
`AggregateOffer` has **no `price` property**, so a range-priced listing cannot
satisfy the Merchant-listing required `offers.price` and is excluded from the
feed. Publishing the low price would add three items to the feed and would be a
misrepresentation finding. Do not increase feed coverage by lying.

**9. Default unknown stock to `InStock`.**
Remove the `if in_stock is not None` guard at `services/marketplace_web.py:1445`.
*Gate:* `tests/test_marketplace_seo.py::test_a_row_with_no_inventory_is_out_of_stock`
and the storefront suite. **expected**

`availability` is only *recommended*, so omitting it costs no eligibility.
Asserting `InStock` about unknown stock costs the buyer an order that cannot
ship.

**10. Make the visible price and the JSON-LD price disagree.**
Render the page from `price_label` while the node prices from variants, or the
reverse.
*Gate:* **NO GATE for the general case.** This is the one Agent 11 is being
asked to monitor and it is worth being precise about what does and does not
exist. `services/marketplace_seo.py:290`
`price_label_contradicts_variants` **does** detect the feed-versus-page form of
it and removes the row from the feed — measured on 2026-10-01 across the 35
items the live feed served, four rows disagreed, listing 36 advertising $38.00
against a $2.29 variant. But nothing asserts that the *rendered HTML's* visible
price equals the *same page's* JSON-LD price. Agent 5 measured it as true
across all 41 live product pages (zero pages where the schema price is absent
from the visible text) and that measurement is a snapshot, not a gate.

**11. Make the JSON-LD price and the Merchant feed price disagree.**
Change `merchant_center_feed`'s source from `price_label` to a constant.
*Gate:* `tests/test_merchant_center_feed.py` plus the eligibility guard above.
**expected** — and note the structural fact underneath: the page node prices
through `marketplace_web.derive_price` (variants first, then label) and the
feed prices through `marketplace_seo.parse_price` (label only). These are
genuinely different authorities. They agree across all 36 live feed rows, and
that agreement is **by design, not by luck**: `eligibility` refuses a row whose
label does not parse *and* a row whose label contradicts its variants, so the
feed-eligible set is precisely the subset where the two authorities concur.
Removing either refusal reopens this.

**12. Emit a Premium price while the authorities still disagree.**
Re-add a `Product`/`Offer` for PulseSoc Premium at any of 999, 1900, 9999, or
the Stripe amount.
*Gate:* `tests/test_app_schema.py` — the floor test asserts `"offers" not in
node` for every node in those graphs, so **any** substitution fails
structurally rather than against a blocklist of numbers. **verified**

See `04_premium_price_authority.md`. The charged amount is not in this
repository: the live checkout lane builds its Stripe session from
`STRIPE_PRICE_ID`, a Price object id. A mutation that "fixes" the price by
choosing one is the exact failure the deletion prevents.

---

## E. Entity scope (13–15)

**13. Project a private or deleted product into public schema.**
Flip a listing to a non-public status and confirm the Product node stops being
served.
*Gate:* `services/search_visibility.py` is the eligibility authority and
`tests/protection/test_sitemap_entries_are_indexable.py` holds the path half.
**expected** — but read the trap first: `search_visibility` answers a
*path*-level question and `marketplace_seo.eligibility` answers a *record*-level
one. A mutation that satisfies one does not satisfy the other, and the two
modules say so about themselves. A deleted row 404s before reaching either.

**14. Publish `variant_key` as a public `sku`.**
*Gate:* the `sku` assertion in
`tests/test_marketplace_storefront.py::test_product_structured_data_carries_what_it_can_prove`
pins `pulsesoc-listing-<id>`. **expected**

`variant_key` is not a variant id: 2,571 distinct values across 3,797 rows, not
URL-safe, and it embeds option text. It identifies nothing outside this
database.

**15. Translate `option1` into `Color`.**
Emit `"color": row["option1"]` or a `ProductGroup` `variesBy` of `color`.
*Gate:* **NO GATE.** Every prod marketplace option is positional — `option1`,
`option2` — and PulseSoc invents the label at display time. Nothing establishes
that `option1` *is* colour; on some listings it is size and on others it is a
bundle count. Agent 3 owns option semantics and this mutation must stay failing
until that contract is adopted. Until then, see the ProductGroup hold in
`02_structured_data_state.md`.

---

## F. The gates themselves (16–18)

**16. Carry the app's `price: "0"` onto a page that does not print the claim.**
Call `app_page_graph(page, free_download_visible=True)` from the `/features`
hub or the `schema_graph` landing family, neither of which prints the free
claim.
*Gate:* `tests/test_app_schema.py::test_the_app_price_is_claimed_only_where_a_reader_can_read_it`,
parametrized over seven routes, asserting both that the claim is printed where
expected and that the `Offer` is present **iff** printed. **verified**

The near-miss that makes this worth a gate: `templates/seo_page.html:111` says
"Launch PulseSoc Free", which is a free *account* claim about the web product —
a different claim about a different entity. A reviewer grepping for "free"
would call that page covered.

**17. Make a dead serialiser path reachable again.**
Restore a caller of `bot._marketplace_public_product_response` or
`_marketplace_public_index_response`.
*Gate:* **NO GATE, and deliberately so.** These are retained rollback code,
orphaned by `32c65d21c`. They now route through `serialise_graph`, so restoring
them is safe *today* — the point of this mutation is that severity tracks
reachability, so a gate here would be asserting something about dead code. What
Agent 12 should check is that the rollback is still *wanted*; if it is restored
and a future refactor reintroduces a `json.dumps`, mutation 18 is the gate that
catches it.

This is also the standing severity correction: Agent 5 initially called the
escaping gap live stored XSS. It was not — production is served by a renderer
that already escaped. The reasoning that produced the wrong severity was
`grep -c u003c` returning 0 on the live page, which is invalid: benign content
needs no escapes, so an absence of escapes is not evidence of an absent
escaper.

**18. Introduce a new `application/ld+json` sink that bypasses the serialiser,
and get CI green.**
Add a template or route with `json.dumps(graph)` into a `<script
type="application/ld+json">`.
*Gate:* `tests/test_structured_data_sinks.py`. **verified** — proven falsifiable
by adding and then removing `templates/_sink_probe.html`.

This is the mutation the whole sentinel exists for. It is keyed on the **sink**
rather than the emitter because source-first enumeration cannot tell you when it
is finished: three outward passes from the schema functions each missed a site,
and the one they all missed (`templates/index.html`) is a hand-written node no
schema function reaches. Enumerating sinks terminates. The census has a closed
two-value classification — `SERIALISER` or `LITERAL` — and no exception
category, because the one file that needed a third (`marketplace_storefront`,
which held a correct independent copy of the escaping) now calls
`serialise_graph` instead.

---

## Summary of the four NO GATE findings

| # | Mutation with no gate | Owner of the gap |
|---|---|---|
| 10 | Visible HTML price vs same page's JSON-LD price | Agent 11 (drift) — measured true on 41/41 pages today, but as a snapshot |
| 15 | `option1` → a named option semantic | Agent 3 (catalog semantics), then Agent 5 |
| 17 | Dead rollback path reachable again | nobody, correctly — severity tracks reachability |
| 12\* | Premium's *charged* amount | not gateable from this repo at all; the amount is a Stripe Price object |

\* 12 has a gate against *emitting* a price. It cannot have a gate against the
price being *wrong*, because the repository does not contain it.

---

## What Agent 12 should not do

Do not fix the mutations that fail. A failing mutation is a working gate; the
output of this lane is the pass/fail table, not a patch. Do not weaken a gate to
make a mutation applyable — if a mutation cannot be expressed, that is a result
worth reporting. And do not add schema properties to make a mutation
interesting: the hard prohibition on inventing brand, GTIN, MPN, reviews and
ratings costs this site **no eligibility at all**, verified property-by-property
against Google's current requirement labels, so there is no richness argument on
the other side of it.
