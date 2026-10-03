# Structured data: state, findings, and open escalations

Agent 5 (structured data + search entity engine), 2026-10-03.
Branch `search-os/agent-05-structured-data`, on top of `5bdf4e431`.

Companion documents in this directory: `01_merchant_center_feed.md` (the feed,
which already existed), `03_agent_05_final_report.md` (the fleet-facing
summary), `04_premium_price_authority.md` (the price escalation, which is the
most important thing on this branch), `05_agent_12_required_mutations.md` (the
eighteen mutations that must fail).

Production was at `5bdf4e431` when every "live" claim below was measured
(`GET /api/service/health`, `commit` field). That is this branch's base, so
**none of the changes described here are deployed.** Everything under "Live
today" is still true of pulsesoc.com as you read this.

Method for live claims: one unauthenticated `GET` per URL with a browser user
agent, JSON-LD extracted from the response body and parsed. No claim here is
inferred from source alone; where a claim is about source only, it says so.

---

## What this agent owns, and the two things it refused to do

Owned: schema type selection, JSON-LD generation, entity `@id` architecture,
validation, schema-to-page consistency, structured-data security.

Not owned, and deliberately not decided here: what PulseSoc Premium costs, and
what the canonical URL of anything is. Both came up. Both are escalated below
rather than answered, because a price picked by the structured-data layer is
exactly the failure this layer exists to prevent.

---

## P0, live: three pages quote Google a price no reader can see

Live today on `/portfolio-intelligence`, `/ai-market-analysis` and
`/telegram-crypto-bot` — all three publish an identical node:

```
Product  name "PulseSoc Premium"
         offers.price      "14.99"
         offers.priceCurrency "USD"
         offers.url        "https://pulsesoc.com/#pricing"
         offers.availability InStock
```

Four things are wrong with it, and they are independent:

1. **No page shows that price.** `/pricing` answers 200 and contains no dollar
   amount at all; the string `14.99` does not appear in its HTML. Nor does it
   appear on any of the three pages carrying the node. The only reader who can
   see this price is Google. This is the one defect here that is not a judgment
   call: Google's structured-data policies say "Don't mark up content that is
   not visible to readers of the page", under *Quality guidelines > Content*,
   and open with "Your structured data must be a true representation of the page
   content."
2. **`$14.99` is a different product's price.** The canonical catalog
   (`services/business_os/entitlements/schema.py:62`) prices
   `crypto_pro_monthly` at `1499`. PulseSoc Premium's monthly plan is
   `pulse_premium_monthly` at **`999`** (same file, line 54).
3. **Neither number is what the checkout charges.** `bot.py:104820` reads
   `PULSE_PREMIUM_PRICE_CENTS`, default **`1900`**, and `.env.example:782`
   declares the same. So Premium has three prices depending on who you ask:
   999 in the catalog, 1900 at the till, $14.99 to Google.
4. **`offers.url` is a dead fragment.** There is no `id="pricing"` anywhere in
   the live homepage, so that URL resolves to the top of `/`.

   An earlier draft of this section called that "a Merchant Center
   disqualification on its own". It is not, and the overstatement is corrected
   rather than quietly dropped: `offers.url` is **recommended**, not required,
   for merchant listing experiences. The defect is real — a URL that does not
   reach the offer is wrong, and it is evidence that nobody checked the node
   against a page — but it disqualifies nothing by itself. Defects 1–3 are what
   matter.

Fixed on this branch by `fdb337296`, which **deletes the node** rather than
correcting the price. That is the fail-closed reading of the brief: the price
question has an owner and it is not this layer. `seo/schema.py:327` carries the
reasoning in-tree so the next person does not re-add it.

`bot.PRO_PRICE_MONTHLY = "$14.99/month"` (`bot.py:1198`) is left alone and is
now dead — after this branch it has zero readers in the repo, and it never
rendered on a page. It is listed here rather than deleted because it is a
pricing constant, not a schema one.

### The frozen invariant

> **UNKNOWN OR CONTRADICTORY PRICE TRUTH → NO PRICE STRUCTURED-DATA CLAIM.**

Substituting 999, or 1900, or whatever `PULSE_PREMIUM_PRICE_CENTS` holds, or
"starting at", or a number converted from one of those, does not satisfy it.
Those are all the same move — choosing between disagreeing authorities — and
the disagreement is the finding. Recorded in-tree at `seo/schema.py:407` so the
next person does not re-derive it, and enforced structurally rather than by
blocklist: `tests/test_app_schema.py` asserts `"offers" not in node` for every
node in those graphs, so **any** substituted number fails, including one nobody
has thought of yet.

### Escalation — owner of Premium pricing

**Escalated in full in `04_premium_price_authority.md`, and it got worse on
inspection.** The summary above says three numbers disagree. Tracing the full
chain — product identity → entitlement identity → display price → checkout
price → charged price → receipt → search projection — found **four**, and the
authoritative one is not in this repository:

- `999` / `9999` — the version-controlled entitlement catalog. Nothing that
  charges money reads it.
- `1900` — `PULSE_PREMIUM_PRICE_CENTS`, read by
  `/api/payments/checkout/premium/<plan_key>` (`bot.py:104826`), which charges
  it for **every** plan including the annual one.
- **unknown** — `/api/premium/checkout` (`bot.py:14760`) is the lane every
  Premium button in the product actually calls, and it builds its Stripe
  session from `STRIPE_PRICE_ID`, a **Price object id** (`bot.py:14619`). The
  amount lives in the Stripe dashboard. No test, gate or review in this repo
  can see it, and it can change without a commit.

And the display stage is **empty**: `/pricing` answers 200 with no dollar amount
at all, and `/pulse/premium` answers **302** to an unauthenticated request. So
no logged-out reader and no crawler has ever been shown a Premium price.

That makes the fail-closed deletion not merely the conservative choice but the
only available one. A structured-data layer cannot project a price that the
repository does not contain and no page displays.

The consumer-facing half does not go away when the node does.

---

## P1, source-only: the JSON-LD serialisers did not escape `<`

Every JSON-LD string on this domain lands inside a raw-text `<script>` element
through Jinja's `|safe`. Inside one of those, `<` is the only character that can
end the block early — `</script` plus any whitespace closes it, and the `>` that
finishes an injected tag can come from the page's own following markup. Eight of
the eleven emitters used a bare `json.dumps`.

**Severity, stated accurately: this was not reachable in production.** An
earlier note from this agent called it live stored XSS. That was wrong, and the
correction is published here deliberately:

- The live product page is rendered by `marketplace_web` /
  `marketplace_storefront`, which already escaped (`services/marketplace_storefront.py:2031`).
  Confirmed against production rather than by reading: the live Product node
  carries `sku: "pulsesoc-listing-163"` and `additionalProperty`, which only
  `services/marketplace_web.py:1424` emits and `marketplace_seo` never does.
- The two unescaped marketplace serialisers were reachable only from
  `bot._marketplace_public_product_response` and `_marketplace_public_index_response`,
  which have no callers — they are the retained rollback for the public-marketplace
  unification, orphaned by `32c65d21c`.
- The reasoning that produced the wrong severity was: `grep -c u003c` returned 0
  on the live page, so the live page must not be using the escaping renderer.
  That is invalid. Benign content needs no escapes, so an absence of escapes is
  not evidence of an absent escaper.

Hardened anyway, in `25e9b2647` / `4570a5f3b` / `0b92ae536`, because a rollback
is precisely when nobody re-reads the renderer being restored, and because "the
escaping lives in one place" was only true of three of eleven emitters.

### The emitter inventory, now closed

Closed by enumerating the **sink** — every `application/ld+json` occurrence in
the repo — rather than by following emitters outward. That matters: three
successive passes of the outward kind each missed a site, and the last one it
missed (`templates/index.html`) is recorded below. Enumerating the sink
terminates; enumerating from the source does not tell you when you are done.

Nine non-test elements carry an `ld+json` block. Eleven emitters produce the
strings that go into them, because `templates/index.html` builds one block from
three. **Every emitter is now the same one thing**, which is the state the
inventory was opened to reach:

| Count | Kind | Where |
|---|---|---|
| 9 | `serialise_graph(...)` | `seo/schema.py:241`, `:269`, `:305`, `:469`; `services/marketplace_seo.py:581`, `:697`; `bot.py:1808`, `:32868`; `services/marketplace_storefront.py:2030` |
| 2 | `serialise_graph(..., indent=2)` | `bot.py:2149` `organization_ld`, `bot.py:2160` `mobile_app_ld` |

Plus **two** hand-written literals with no interpolation at all, safe by
construction: `bot.py:34051` (the crypto-predictions `WebPage`) and
`templates/index.html:56` (a `WebApplication` node sitting in the same `@graph`
array as `organization_ld` and `mobile_app_ld`). An earlier draft of this table
said "one"; the second was found by the sink enumeration above, which is the
argument for having done it.

The third row this table used to have is gone.
`services/marketplace_storefront.py` held its own escaper — an independently
written, **correct** copy of the same `<`-replacement. It was consolidated onto
`serialise_graph` in `5f74ddec9` for a reason that is not about correctness: a
security property with two implementations has two chances to be dropped by a
refactor, and only one of them is the one anybody re-reads. The storefront's one
distinguishing property, `ensure_ascii=True`, is preserved as an argument rather
than silently taken away.

All eleven were verified byte-identical before and after, by rendering each
affected page on both revisions and diffing the extracted blocks. Of the code
commits, only `fdb337296` and `32f7f4d5b` change any output.

### The serialiser contract, frozen

`seo/schema.py:26` is the only sanctioned way to turn a graph into page output,
and its docstring now states the contract as seven checkable points rather than
as prose. In summary: the output is valid JSON that parses to **exactly** the
payload it was given; it is safe in the raw-text `script` context; `<` cannot
appear literally in it; non-ASCII survives in both `ensure_ascii` modes; nothing
is escaped twice; no caller passes pre-serialised markup through it; and the
rendered page still parses, asserted per page family.

Deliberately **defence in depth**, not a bet on upstream sanitisation. Nothing
strips `<` from a listing title on the way in or out — see the last entry under
"Open" — so this escaping is the only control on that path. But the contract
holds regardless of whether that stays true, because a serialiser that trusts
its input is one refactor away from being the hole.

One thing the sink enumeration ruled out that is worth stating, because the
failure mode is silent: `templates/index.html`, `privacy.html` and `terms.html`
interpolate `{{ organization_ld | safe }}` followed by a **comma** inside a
`@graph` array. If that variable were ever not passed, Jinja would render the
empty string and the block would become invalid JSON — a page that still
returns 200 with structured data that no parser accepts. Checked live: all
three parse, yielding `[Organization, MobileApplication, WebApplication]` on
`/` and `[Organization, WebPage, BreadcrumbList]` on both legal pages.

---

## The marketplace verdict — measured, and frozen

This is the finding the lane was pointed at, and it is a pass. Every number
below is from an unauthenticated `GET` against production at `5bdf4e431`, not
from reading source.

**Coverage.** All 42 URLs in `/sitemap-products.xml` fetched. 41 of 41 product
pages carry a `Product` node; the 42nd URL is the collection page, which
correctly carries `CollectionPage` + `ItemList` instead and no `Product`. One
URL initially reported a TLS handshake failure and succeeded on retry — a
transient blip, not a page defect, and recorded because an unexplained
FETCH_FAIL in a census is indistinguishable from a missing node.

| Measured across 41 live product pages | Result |
|---|---|
| Carry a `Product` node | 41 / 41 |
| Single `Offer` | 38 |
| `AggregateOffer` (range-priced) | 3 |
| `availability: InStock` | 40 |
| `availability` omitted (stock genuinely unknown) | 2 |
| **Pages where the schema price is absent from the visible text** | **0** |
| Pages carrying `brand`, `gtin`, `mpn`, `review` or `aggregateRating` | 0 |

`/pulse/marketplace` — one block, `@graph` of Organization, WebSite,
CollectionPage, ItemList (`numberOfItems` 23), BreadcrumbList.

A representative product page, `/pulse/marketplace/163` — `@graph` of
Organization, WebSite, WebPage, Product, BreadcrumbList, with `offers.price`
`"30.50"` matching the `$30.50` visible on the page and being the only dollar
amount on it.

**What makes the zero in that table the important row.** It is the one measured
fact that the policy "structured data must be a true representation of the page
content" actually turns on, and it is the opposite of the `$14.99` defect this
document opens with. Every price claim the marketplace makes to Google is a
price a reader can see.

**Availability fails closed.** `services/marketplace_web.py:1445` sets
`availability` only `if in_stock is not None`. The two pages without it are not
a gap — they are the guard working. `availability` is *recommended*, never
required, so the omission costs no eligibility, whereas asserting `InStock`
about unknown stock costs a buyer an order that cannot ship.

**`AggregateOffer` where catalog state requires it, and the cost of that.** The
three range-priced listings get `AggregateOffer` with `lowPrice`/`highPrice`
(`services/marketplace_web.py:384` `PriceView.as_schema_offer`). `AggregateOffer`
has **no `price` property**, so those three cannot satisfy the Merchant-listing
required `offers.price` and are excluded from the feed. That is correct and
deliberate: the alternative is publishing the low price as *the* price, which
would add three feed items and be a misrepresentation. Noted explicitly because
"three products missing from the feed" reads like a bug in a coverage report.

**Provenance-sensitive seller properties.** `seller` is emitted only when
`seller_store_name` exists; `sku` is `pulsesoc-listing-<id>`, PulseSoc's own
identifier for its own record, which claims nothing about a manufacturer;
`additionalProperty` is capped at 12. `brand`/`gtin`/`mpn` are structurally
absent because the columns do not exist across all 20,247 supplier snapshots.

### Frozen: do not add recommended fields for richness

The verdict above is the target state, not a baseline to improve on. Everything
absent from these nodes is *recommended only* — verified property-by-property
against Google's current docs — so adding it buys no eligibility and every
available value would have to be invented. More schema is not better search.

---

## Rich-result readiness, against Google's actual requirement labels

Checked against `developers.google.com/search/docs/appearance/structured-data/`
(`product-snippet`, `merchant-listing`, `sd-policies`) on 2026-10-03. This
section is the authority for every "required" / "recommended" word in this
document; earlier drafts used those words from memory, and one of them was
wrong (see defect 4 above).

The live `/pulse/marketplace/163` Product node passes **every required property
for both experiences**:

| Experience | Required | Live node |
|---|---|---|
| Product snippet | `name` | present |
| Product snippet | one of `review` / `aggregateRating` / `offers` | `offers` |
| Merchant listing | `name`, `image`, `offers` | all present |
| Merchant listing | `offers.price`, and it must be > 0 | `"30.50"` |
| Merchant listing | `offers.priceCurrency` | `"USD"` |

Everything this layer omits is **recommended**, never required: `brand`,
`gtin`, `mpn`, `review`, `aggregateRating`, `shippingDetails`,
`hasMerchantReturnPolicy`. That is the useful result here — the hard rule
against inventing those costs the site no eligibility at all. `availability` is
also only recommended, so the fail-closed omission at
`services/marketplace_web.py:1445` is compliant rather than a gap.

So the honest readiness answer is: eligible on the required properties,
deliberately thin on the recommended ones, and the thinness is not fixable from
this layer because the data does not exist.

---

## Closed on this branch: the app's `price: "0"`

Found by turning the policy I had just cited against my own remaining nodes,
which is the test that section should have to pass. The `MobileApplication` node
carried an `Offer` of `price "0"` onto pages that never printed the claim.

**Still not a second P0, and preserving that distinction matters.** The `$14.99`
node was invisible *and* wrong *and* contradicted by the checkout *and* pointed
nowhere. This was invisible and otherwise **true** — the app is genuinely free
to download, checkable against Apple's listing, and nothing contradicts it. So
the defect here is a *contract* defect, not a truthfulness one: the number is
right and the page does not say it.

Resolved by restricting the Offer-bearing node to the pages where the claim is
visibly represented, rather than by changing `0` or by adding copy. Measured
first, because the whole point is visibility:

| Surface | Prints the free-download claim | Carries the `Offer` |
|---|---|---|
| `/app` | yes — `templates/app_landing.html:26`, 7 visible hits | yes |
| `/features/<slug>` (all 8) | yes — `templates/feature_page.html:58`, unconditional | yes |
| `/` | no | **no** |
| `/features` hub | no | **no** |
| `/pricing` and the ~87 `schema_graph` landing pages | no | **no** |

Implemented as `mobile_app_schema(free_download_visible=False)` — a flag that
**defaults to no Offer**, so a new route that forgets it fails closed to the
truthful state rather than inheriting a claim. `bot.py` passes `True` at the two
route call sites whose templates print the line; the `/features` hub is left
alone deliberately, because the hub does not print it even though its children
do.

Two things made this the smallest correct solution rather than a judgement call:

- **The near-miss that rules out the landing family.**
  `templates/seo_page.html:111` says "Launch PulseSoc Free". That is a free
  *account* claim about the web product — a different claim about a different
  entity — and a reviewer grepping for "free" would wrongly call those pages
  covered.
- **It costs zero eligibility.** Google's software-app rich result requires
  `name`, `offers.price` **and** one of `aggregateRating`/`review`. These nodes
  deliberately carry neither a rating nor a review, so they were never eligible
  for that result. Removing the Offer where it is invisible forfeits nothing,
  and keeping it where it is visible forfeits nothing either.

Held by `tests/test_app_schema.py::test_the_app_price_is_claimed_only_where_a_reader_can_read_it`,
parametrized over seven routes and asserting both halves — that the claim is
printed where expected, and that the `Offer` is present **iff** printed. A test
that only checked the second half would pass if the visible copy were deleted.

---

## Agent 3's catalog contract: adopted as the authority, not force-wired

Agent 3 owns catalog semantics and provenance — brand, GTIN, MPN, variant
identity, option semantics, confidence and where each came from. Agent 5 adopts
that as the authority for every future enrichment of these nodes. Concretely:
**any later addition of brand, identifiers, variant identity or option meaning
must consume Agent 3's semantics rather than independently derive them.** This
layer has no business re-deriving a fact about a product from a column it can
see.

What this does **not** mean, and the restraint is deliberate:
`services/business_os/catalog_semantics.py` is **not** force-wired through the
schema builders on this branch. Agent 0 owns integration sequencing, and
threading a new dependency through the live product renderer to achieve
identical output is blast radius without a behaviour change. The duplicated
fail-closed behaviour — Agent 3 refusing to assert a brand, and
`marketplace_web.product_jsonld` structurally omitting one — stays, because both
are truthful and the duplication costs nothing while they agree. It becomes
worth consolidating the moment Agent 3 can supply a value, which is the point at
which the two would otherwise diverge.

---

## `ProductGroup` and variants: on hold, and the hold is the decision

Variants exist — 3,797 rows — and that is **not** a reason to emit
`ProductGroup`/`hasVariant`. Doing so because the data is present is the
"more schema" failure in its purest form, and it would require inventing most of
what the markup asserts.

Seven things have to be answered authoritatively first, and none of them is
Agent 5's to answer:

1. **Public variant identity.** `variant_key` is not a variant id: 2,571
   distinct values across 3,797 rows, not URL-safe, and it embeds option text.
2. **Stable grouping identity.** `item_group_id` must be stable across a
   re-sync. Nothing today guarantees that.
3. **Variant URL strategy.** Marketplace variant params are `opt_`-prefixed.
   Whether a variant has its own crawlable URL is Agent 2's canonical question.
4. **Option semantics.** Every production option is **positional** — `option1`,
   `option2` — and PulseSoc invents the display label. Nothing establishes that
   `option1` is colour; on some listings it is size, on others a bundle count.
   Agent 3 owns this.
5. **Merchant `item_group_id`** has to agree with whatever (2) resolves to.
6. **Variant-specific availability, price and media.** A listing has two price
   authorities already (see the Agent 11 handoff); per-variant claims multiply
   that.
7. **Canonical behaviour** under a variant selection — Agent 2.

Until those exist, the truthful representation of a range-priced listing is one
`Product` with an `AggregateOffer`, which is what ships. Keep it.

---

## Open, not addressed on this branch

- **`bot.py:34051`** hand-writes a `WebPage` node with no `@id`, bypassing
  `seo/schema.py` entirely. It joins nothing in the entity graph. Low value to
  fix; recorded so it is not mistaken for a `seo.schema` output.
- **BreadcrumbList carries no `@id`** on the live product page. Consolidation
  already works through `WebPage`, and Google does not require it, so this is an
  observation rather than a gap. Explicitly *not* fixed, per "more schema is not
  better search".
- **Nothing strips `<` from a listing title on the way in or on the way out.**
  Read, since it decides whether the escaping above is load-bearing or
  theoretical. `services/business_os/suppliers/importer.py:443` writes
  `product.get("title")` straight into `marketplace_listings.title` — the SQL is
  parameterised, so this is not an injection, but no filtering happens either,
  and `_validate` (line 311) only checks that a title is *present*. On the read
  side `services/marketplace_web.py:147` `_clean` collapses whitespace and
  nothing else. So a supplier-supplied title containing `</script ` reaches the
  `Product.name` verbatim and the serialiser's escaping is the **only** control
  standing between it and the page.

  That does not change the severity in the section above — the live renderer has
  that escaping — but it does mean the property is doing real work rather than
  guarding a hypothetical, and that the two renderers which lacked it were one
  rollback away from mattering. Whether supplier titles should also be filtered
  at the write boundary is a question for whoever owns the import, not for this
  layer; structured data should not be the thing that sanitises the database.
- **Bing and Schema.org's own documentation still not consulted.** Google's was,
  late in the session — see the section below, which is the authority for the
  requirement labels used in this document. Bing's product-markup requirements
  are not checked against anything here.

---

## Handoffs

**Agent 2 (canonical policy).** `offers.url` on the removed node pointed at
`https://pulsesoc.com/#pricing`, which does not exist. If a Premium offer URL is
ever reinstated, this layer needs a canonical from you, not a guess. The live
Product node's `url` and `mainEntityOfPage` already agree with the page's own
canonical; nothing is being contradicted today.

**Agent 4 (SSR).** All JSON-LD on this domain is server-rendered into the
initial HTML, including the marketplace. Nothing here depends on client
hydration, and `serialise_graph` is the single choke point if you move the
render path — route through it rather than re-serialising.

**Agent 6 (sitemaps).** The three pages carrying the bad `Offer` are all three in
`/sitemap-pages.xml` (87 URLs, reached from the `<sitemapindex>` at
`/sitemap.xml`), and all three are indexable: 200, `robots` `index,follow`,
self-canonical, not disallowed in `robots.txt`. So the price claim is being
offered to Google on pages Google is being invited to crawl. After `fdb337296`
they are still all of those things; they just stop making a price claim. No
sitemap change is needed for this.

**Agent 7 (Merchant Center).** An earlier draft of this handoff warned you not
to let a feed default `availability` to in-stock. That warning was written
without checking whether a feed existed. **It does, it is live, and it already
does this correctly** — `01_merchant_center_feed.md` in this directory
documents it and I had not read it. Correcting rather than deleting, because
the thing I was wrong about is worth knowing:

`GET /feeds/merchant-center.xml` answers 200 with 36 items. All 36 say
`in_stock`, which looks exactly like the default-to-in-stock failure and is
not one. `merchant_center_feed` delegates to `marketplace_seo.availability`,
which delegates in turn to the same `inventory_available` predicate that
decides whether a row may appear at all — so a zero-quantity listing 404s
instead of reaching the feed, and every row that arrives is genuinely in
stock. `_FEED_AVAILABILITY` is a mapping rather than a pass-through (Merchant
Center rejects the schema.org URL form) and **raises** on an unmapped value
instead of shipping one. That is the fail-closed design, already built.

Verified across 10 listings that the feed and the page agree: same
availability, same price. The one thing that looks like a discrepancy is not —
the feed writes `<g:price>30.50 USD</g:price>` where the JSON-LD writes
`price: "30.50"` plus `priceCurrency: "USD"`, which is the same claim in the
two formats each surface requires.

**The correction, stated permanently so it cannot drift back: THE MERCHANT FEED
ALREADY EXISTS.** It is live, it is certified, and it is fail-closed. Your lane
consumes Agent 3's semantics, Agent 5's structured truth, and the existing feed
architecture. It does not build a feed.

**Price and availability agreement, measured end to end.** All 36 live feed
items checked against their own product page's `Product` node:

```
feed items: 36    price + currency agree with the page's Offer: 36
price mismatches: 0
availability mismatches: 0
```

**The exclusions are correct and you should preserve them.** 41 product pages,
36 feed items, so five listings are excluded. Three (`/112`, `/89`, `/15`) are
range-priced: `AggregateOffer` has no `price`, Merchant listings require one,
and the feed refuses rather than publishing the low price. The others are caught
by `marketplace_seo.price_label_contradicts_variants` — listing 36 advertised
$38.00 against a $2.29 variant, so the row leaves the feed and keeps its
ranking rather than both surfaces going quiet over one stale label. `/35` is
excluded for a reason this lane did not individually pin down; it is recorded as
unpinned rather than guessed at.

**Do not increase feed coverage by lying.** Each of those five exclusions is a
product you could add by weakening one refusal. Three of them would require
asserting a single price for a ranged listing; the others would require
advertising a price the buyer will not be charged.

So the only live things left for you are:

- Do not submit a Premium offer. See `04_premium_price_authority.md`: there is
  no agreed price, the display price does not exist publicly, and the charged
  amount is a Stripe Price object this repo cannot read. The feed does not carry
  it today — checked: no `14.99` and no "PulseSoc Premium" anywhere in it.
- Keep `g:identifier_exists=no`. Brand, GTIN and MPN are absent from all 20,247
  supplier snapshots, so that declaration is the truthful one and there is
  nothing to backfill.
- `image` is a **required** merchant-listing property, and all 36 feed images
  are on `cjdropshipping.com`, none on a PulseSoc domain (see Agent 9). The
  feed inherits that dependency and cannot detect it breaking.
- Refuse variants. See the `ProductGroup` hold above: `item_group_id` has no
  stable source yet.

**Agent 9 (media).** `Product.image` is every URL that
`gallery_items` (`services/marketplace_web.py:1231`) collected whose kind is
`image` (`services/marketplace_web.py:1436`), drawn from three sources in order
— the `media`/`media_assets` rows, then the
`cover_image_url`/`image_url`/`thumbnail_url` column, then `gallery_json`. It
filters on kind, dedups, and caps at `limit`. What it does *not* do is ask
whether a URL is publicly fetchable or whether it expires, and it has no way to:
a signed or short-lived URL is indistinguishable from a permanent one at this
layer.

Measured rather than assumed, across 13 live Product nodes drawn from
`/sitemap-products.xml`: **every image URL is on `cjdropshipping.com`**
(11 `cf.`, 2 `oss-cf.`) and **none is on a PulseSoc domain**. None carries a
query string, so nothing is signature- or expiry-shaped today, and the one I
fetched returns 200 `image/jpeg` with `max-age=31536000`. So the answer to the
question above is currently "fine" — but by a supplier's choice, not by
anything PulseSoc controls.

That is worth your attention because `image` is a **required** property for
merchant listing experiences (see the readiness section). If CJ rotates a path
or drops an asset, the node does not degrade — it fails a required property,
and this layer will keep emitting the dead URL because it has no way to know.

**To be explicit, because an earlier draft of this handoff could be read as
asking for one: this is not a request to migrate the images.** The imagery is
crawlable today — the asset I fetched returns 200 `image/jpeg` with
`max-age=31536000`, nothing is signature- or expiry-shaped, and "third-party
hosted" is not by itself a defect. Media crawlability and lifecycle are your
lane and your call. Agent 5's lane is narrower and is the only thing being
asserted here: **whether structured data truthfully references the canonical
visible image.**

The invariant Agent 5 owns one third of:

> **VISIBLE PDP HERO IMAGE = OG/TWITTER IMAGE WHERE THE CONTRACT REQUIRES IT =
> `Product.image` STRUCTURED-DATA REFERENCE**

Today that holds by construction, because all three read the same
`gallery_items` output, and a video is filtered out rather than offered to
schema as a product image
(`tests/test_marketplace_storefront.py::test_a_video_is_not_offered_to_schema_as_a_product_image`).
It would break the moment a media surface starts choosing its hero
independently — which is a change in your lane that would silently falsify a
claim in mine. That is the whole content of this handoff.

**Agent 11 (drift detection).** You measure; do not build a second schema
engine here. Agent 5's nodes are the reference, and the drift classes worth
watching, in descending order of how badly they fail:

| Drift | Detectable from | Status today |
|---|---|---|
| Visible price ≠ JSON-LD price | rendered HTML vs its own node | **no gate.** Measured true on 41/41 pages — a snapshot, not an invariant |
| JSON-LD price ≠ Merchant feed price | page vs feed | guarded, see below |
| Schema `url`/`mainEntityOfPage` ≠ HTML canonical | page | agrees today |
| Structured image ≠ visible hero | page | agrees by construction (above) |
| A private or deleted product still projecting `Product` | page | 404s before reaching the renderer |
| Unsupported `brand` or `aggregateRating` appearing | node | gated, both renderers, as of this branch |
| Premium price reappearing | node | gated structurally |

The one that needs your attention most is the **two price authorities**, and the
precise shape matters because it is easy to get wrong in both directions. The
page node prices through `marketplace_web.derive_price` (variants first, then
`price_label`); the feed prices through `marketplace_seo.parse_price`
(`price_label` only). Those are genuinely different authorities, and on
2026-09-29 across 123 production listings they disagreed on **85** — 82 with an
empty label displaying a real variant price, 3 differing by up to $35.71.

They nonetheless agree across all 36 live feed rows, and **that agreement is by
design, not by luck**: `marketplace_seo.eligibility` refuses a row whose label
does not parse *and* a row whose label contradicts its variants
(`price_label_contradicts_variants`, `services/marketplace_seo.py:290`). The
feed-eligible set is precisely the subset where the two authorities concur. So
do not report the two-authority split as an open defect — it is a contained one.
Monitor the containment: the 36/36 agreement becomes meaningless if either
refusal is relaxed, and `price_label_contradicts_variants` fails **open** for
callers that do not load variants, which is a deliberate choice documented in
its own docstring.

Checkout, for completeness, is **not** a third authority:
`services/marketplace_price_authority.py` resolves checkout through
`derive_price` for exactly this reason, and refuses rather than guessing when
variants span a range.

**Agent 12 (adversarial).** `05_agent_12_required_mutations.md` is yours:
eighteen mutations, each of which must fail, with the four that currently have
**no gate** named as such. Writing that list is how the live renderer's missing
GTIN/MPN assertion was found.

**Agents 0, 1, 3, 8, 10.** Two things are worth knowing regardless of lane.
First, `seo.schema.serialise_graph` is the only sanctioned way to turn a graph
into page output; adding a twelfth emitter with `json.dumps` reopens a property
these commits just closed, and
`tests/test_structured_data_sinks.py` will turn red when you do. Second, the two
`bot._marketplace_public_*_response` helpers are dead rollback code — if your
lane touches the public marketplace, check whether that rollback is still wanted
before trusting what those functions say the page looks like.

---

## Gates

Full protection suite: **785 checks across 57 suites, passing.** Realtime-audio
change gate: no protected path changed, 11 files inspected.

Structured-data suites, re-run at the branch tip: `test_structured_data_sinks`,
`test_app_schema`, `test_marketplace_storefront`, `test_marketplace_seo`,
`test_merchant_center_feed` — **255 passed, 44 subtests.** Page suites:
`test_marketplace_public_pages`, `test_site_identity`, `test_feature_pages`,
`test_app_promotion`, `test_marketplace_light_parity` — **322 passed, 15
subtests.** Protection gates run individually: `test_route_auth`,
`test_environment_contract`, `test_sitemap_entries_are_indexable` — 42 passed,
45 subtests; `test_every_test_file_is_run_by_ci` — 9 passed, which is the gate
that required declaring the new sink sentinel in `config/ci_test_manifest.json`.

Run them with `/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python -m pytest`.
System `python3` lacks the dependencies and will fake a pass by collecting
nothing.

### Falsifiability, which is the part that matters

A test that cannot fail is documentation with a green tick, and three of the
assertions on this branch guard byte-identical output. So each was proven
against the mutation it exists to catch:

- **The sink sentinel** goes red on a newly added `application/ld+json`
  element — proven by adding and then removing `templates/_sink_probe.html`.
- **The live renderer's identifier assertion** fails when a `gtin13` is
  injected into `marketplace_web.product_jsonld` — proven by patching the
  function and re-running the single test. This is the one that found a real
  gap: before it, that mutation shipped green.
- **The two `test_app_schema` escaping tests** inject a hostile value upstream
  rather than through a request, because no reachable request can carry one into
  those two graphs. They fail on the parent commit.
- **The app-Offer visibility test** asserts both halves — claim printed where
  expected, and `Offer` present **iff** printed — so deleting the visible copy
  also turns it red.
