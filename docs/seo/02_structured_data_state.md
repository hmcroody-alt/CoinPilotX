# Structured data: state, findings, and open escalations

Agent 5 (structured data + search entity engine), 2026-10-03.
Branch `search-os/agent-05-structured-data`, four commits on top of `5bdf4e431`.

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

### Escalation — owner of Premium pricing

Three numbers disagree and this layer cannot choose between them. What is
needed is one answer to "what does PulseSoc Premium cost", written once where
both the checkout and any future `Offer` can read it. Until that exists, the
correct structured data is no `Offer` at all, which is what the branch ships.

Note the consumer-facing half of this is not a schema problem and does not go
away when the node does: the catalog says 999 and the charge is 1900.

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
  `marketplace_storefront`, which already escaped (`services/marketplace_storefront.py:2029`).
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

Eleven places put a string inside an `application/ld+json` element. Every one is
now one of three things:

| Count | Kind | Where |
|---|---|---|
| 8 | `seo.schema.serialise_graph` | the 6 graph builders, `marketplace_seo`'s 2 |
| 2 | `serialise_graph(..., indent=2)` | `bot.organization_ld`, `bot.mobile_app_ld` |
| 1 | `marketplace_storefront`'s own escaper | `services/marketplace_storefront.py:2029` |

Plus one hand-written literal with no interpolation at all (`bot.py:34045`, the
crypto-predictions page) — safe by construction, but see the entity note below.

All eleven were verified byte-identical before and after, by rendering each
affected page on both revisions and diffing the extracted blocks. Of the four
commits, only `fdb337296` changes any output.

---

## Live today: what production actually publishes

Measured against `5bdf4e431`.

`/pulse/marketplace` — one block, `@graph` of Organization, WebSite,
CollectionPage, ItemList (`numberOfItems` 23), BreadcrumbList.

`/pulse/marketplace/163` — one block, `@graph` of Organization, WebSite,
WebPage, Product, BreadcrumbList. The Product node is sound, and worth saying so
explicitly since most of this document is defects:

- No `brand`, no `review`, no `aggregateRating`, no `gtin`/`mpn`. The column and
  the table do not exist, and the builder's docstring says that is why.
- `availability` is only set when stock is actually known
  (`services/marketplace_web.py:1445`, `if in_stock is not None`) — it fails closed to
  omission rather than to `InStock`.
- `offers.price` `"30.50"` matches the `$30.50` visible on the page, and is the
  only dollar amount on it.

That is the standard the rest of the domain's structured data should be held to.

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

## Open, not addressed on this branch

- **`bot.py:34045`** hand-writes a `WebPage` node with no `@id`, bypassing
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

**Agent 7 (Merchant Center).** Do not submit a Premium offer. There is no agreed
price (999 / 1900 / $14.99) and the offer URL does not resolve.

Marketplace listings are a separate matter and are genuinely submittable, not
just plausibly so: the live node carries every required merchant-listing
property, verified property-by-property in the readiness section above. Two
cautions, both of which are ways a feed could undo that:

- `availability` is omitted whenever stock is unknown, by design. It is only a
  *recommended* property, so omitting it costs nothing — but a feed that
  defaults the omission to `in stock` would reintroduce exactly the claim this
  layer refused to make.
- `image` is **required**, and every live image URL is on a third-party
  supplier CDN rather than a PulseSoc domain (see the Agent 9 handoff). A feed
  inherits that dependency.

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
Whether product imagery should be mirrored to PulseSoc-controlled storage is
your call, not this layer's; I am telling you the dependency exists and that
structured data is one of the things that breaks when it does.

**Agents 0, 1, 3, 8, 10–12.** Two things are worth knowing regardless of lane.
First, `seo.schema.serialise_graph` is the only sanctioned way to turn a graph
into page output; adding a twelfth emitter with `json.dumps` reopens a property
four commits just closed. Second, the two
`bot._marketplace_public_*_response` helpers are dead rollback code — if your
lane touches the public marketplace, check whether that rollback is still wanted
before trusting what those functions say the page looks like.

---

## Gates

Full protection suite: 785 checks across 57 suites, passing. Realtime-audio
change gate: no protected path touched. No new test files, so the CI manifest is
unchanged. Suites re-run green: `test_app_schema` (29), `test_marketplace_seo`
(50), `test_marketplace_public_pages` (93), `test_site_identity` (7),
`test_about_page` (15), `test_feature_pages` (119), `test_commerce_policy_pages`
(41), `test_legal_documents_describe_the_real_product` (52),
`protection/test_route_auth` (12), `protection/test_environment_contract` (14),
`protection/test_sitemap_entries_are_indexable` (16).

The two tests added to `test_app_schema.py` inject a hostile value upstream
rather than through a request, because no request can carry one into those two
graphs. They fail on the parent commit; that is what makes them worth having.
