# Agent 4 — SSR, crawler rendering, web performance

Surface: `https://pulsesoc.com/pulse/marketplace` and everything it links to.
Branch: `search-os/agent-04-rendering-performance`. Worktree: `/Users/hmcherie/Desktop/cpx-rendering`.
Base: `5bdf4e431`, which was `origin/main` and the deployed sha (`/api/service/health`) when this
work started. Every measurement below was taken against production on 2026-10-03, not against a
local server, unless it says otherwise.

## 1. The headline

The brief anticipated an SSR mission. There is nothing to turn on. The public storefront is
already server-rendered, in the strongest sense available: it is a pure Python string builder with
no template engine and no client-side rendering, and a crawler with JavaScript disabled sees the
complete product catalogue. Rendering architecture was not the defect.

The defect was weight. Every product photo on the public storefront was the supplier's
unmodified original — 44 distinct images, 10,773,449 B, mean 257 KB — painted into boxes measured
at 225–607 CSS px. One grid page carried 4.79 MB of image to show 24 cards.

That is fixed, in `services/marketplace_storefront.py`, commit `ffb88a1ca`. Same page, same
cards, same markup contract: **734,358 B catalogue-wide, 93.2% less**.

Agent 1 reached the same conclusion about SSR independently and addressed it to this seat — their
§9 is titled "SSR is already real. Verify before building." Two agents measuring the same surface
by different methods and agreeing is worth more than either measurement alone, so I am naming it
rather than quietly duplicating it.

## 2. What actually renders this page

Two documents live at one URL, chosen by session cookie:

| Reader | Document | Indexable |
| --- | --- | --- |
| No session | `public_document()`, built by `services/marketplace_storefront.py` | yes |
| Signed in | `pulse_social_shell()` | no — `noindex` |

`Vary: Cookie` is set, correctly, so a shared cache cannot hand a signed-in document to an
anonymous reader. This is not cloaking: both documents describe the same products from the same
query, and the anonymous one is the superset for indexing purposes. The split exists because the
signed-in shell is an application, not a page.

### 2.1 A correction to the record

A prior pass through this surface analysed `templates/marketplace_index_public.html` — the Jinja
template with `<article class="card">` and a bare `<img>`. **That template has no callers.** Its
only entry point, `_marketplace_public_index_response` (`bot.py` ~61173), says so in its own
docstring: it is retained for one release as the rollback for the unification in
`pulse_marketplace_page`, and warns against reaching for it as an alternative renderer, because
its cards price through `marketplace_seo.parse_price` while the product page prices through the
variant rows.

Anyone auditing this surface from the git tree alone will find that template, find `mkt-media`
absent from it, and conclude the served DOM and the repository disagree. They do not. The live
renderer is `services/marketplace_storefront.py`. Patching the template would have shipped
nothing. This is the single most expensive wrong turn available on this surface and it is why the
first task in this mission was reconciling raw HTML against the tree rather than reading
templates.

## 3. Rendering matrix

Anonymous, production, 2026-10-03. `prod` counts unique `/pulse/marketplace/<id>` anchors in the
**raw** HTML — no JavaScript executed.

| Path | Status | HTML | prod | img | Cache-Control | robots | canonical |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/pulse/marketplace` | 200 | 36,434 | 24 | 24 | `public, max-age=300` | `index,follow,max-image-preview:large` | self |
| `/pulse/marketplace?page=2` | 200 | 28,083 | 20 | 20 | `public, max-age=300` | `noindex,follow` | self |
| `/pulse/marketplace?category=bags-shoes` | 200 | 11,741 | 2 | 2 | `public, max-age=300` | `index,follow,max-image-preview:large` | self |
| `/pulse/marketplace/98` (PDP) | 200 | 21,323 | **0** | 1 | `public, max-age=300` | `index,follow,max-image-preview:large` | self |
| `/pulse/marketplace/85` (PDP) | 200 | 18,878 | 1 | 2 | `public, max-age=300` | `index,follow,max-image-preview:large` | self |
| `/pulse/cart` | 200 | 12,629 | 0 | 0 | `no-store, max-age=0` | `noindex,nofollow` | self |
| `/pulse` | 302 | 223 | 0 | 0 | `no-store, max-age=0` | — | — |

Readings that matter:

- **Content is in the first byte.** No JavaScript is required for any product, price, title, link
  or image on any of these pages. There is no hydration step to get wrong, because there is no
  client-side render.
- **The privacy boundary holds at the route level.** The cart is `no-store, noindex,nofollow`;
  `/pulse` redirects anonymous readers to the login wall. No public cache is offered a document
  containing private data.
- **JavaScript is strictly additive.** `static/js/pulse_marketplace.js` binds an image-error
  fallback and add-to-cart. Progressive enhancement is already correct here, and the one thing
  that could have broken it — my change — was checked against it (§6.3).
- **Both PDP rows are in the table because one PDP is not representative.** 98 renders no
  related-products rail and one image; 85 renders a rail and two. Across all 24 page-1 PDPs the
  split is 16 with no rail and one image, 8 with a rail and two. Sampling a single PDP here
  produces a wrong generalisation in either direction — §8 is where that bit me.

## 4. The image-weight defect

### 4.1 The false premise

`media_box` declined to emit a `srcset`, and documented why:

> product images are served from an R2 bucket behind `cdn.coinpilotx.app` with no image transform
> service in front of it — there is no resize endpoint in the repo and no Cloudflare Images
> binding.

Measured across all 42 sitemap URLs and all four grid pages: **147 image references, 44 distinct
URLs, 100% on `cf.cjdropshipping.com` or `oss-cf.cjdropshipping.com`. Zero on
`cdn.coinpilotx.app`.** Both CJ hosts answer Alibaba Cloud OSS image directives.

The reasoning in that paragraph was sound. Its premise was simply about a different CDN than the
one serving the page. That is the characteristic failure mode of a well-commented codebase: the
comment is load-bearing, nobody re-measures it, and it outlives the thing it described.

### 4.2 What the CDN will and will not do

Probed, not assumed:

| Behaviour | Result |
| --- | --- |
| `?x-oss-process=image/resize,w_400` | works on both hosts |
| `/format,webp`, `/format,avif` | work |
| `format,auto` | **HTTP 400** |
| Unknown operation (`image/zzznotreal,w_400`) | **HTTP 400, not the original** |
| Upscale beyond the original | refused — returns the original size |
| `Vary` | `Origin` only — no `Accept` negotiation |
| `Timing-Allow-Origin` | absent |

Two of these decided the design.

**A bad directive 400s rather than degrading.** Combined with the fact that a failing `srcset`
candidate does **not** fall back to `src` — the browser renders a broken image — this makes a
host allowlist mandatory, not a nicety. A try-everything implementation would have broken every
image on any future origin that does not speak OSS.

**No `Accept` negotiation.** So webp cannot be served through `format,auto` behind one URL; it
needs a `<picture>` `<source type="image/webp">` so the browser does the choosing.

### 4.3 Measured savings

Catalogue-wide, 44 distinct images, at `w_400/format,webp`:

```
original   10,773,449 B   mean 256,510 B
after         734,358 B   mean  17,484 B      -93.2%
```

One grid page (the 24 images a crawler or first-time reader actually lands on):

```
original    5,023,949 B  (4.79 MB)   baseline
w_400       1,075,509 B  (1.03 MB)   -78.6%
w_600 webp    784,328 B  (0.75 MB)   -84.4%
w_400 webp    425,582 B  (0.41 MB)   -91.5%
```

### 4.4 The cost, stated honestly

A `srcset` of 8 candidates per image is not free. Per card the markup grows **+1,053 B raw**; the
24-card page grows **36,434 → 65,066 B raw, +79%**.

Gzip erases most of that, because 24 near-identical candidate lists are exactly what a
dictionary compressor is for. On the wire, where it counts (the server does gzip the GET —
transfer was 6,388 B):

```
HTML gzipped   6,370 -> 7,579 B    +1,209 B  (+19%)
images         5,023,949 -> ~784,328 B at the selected w_600/webp   -4,239,621 B
```

**+1.2 KB of HTML buys −4.2 MB of image.** A ratio of roughly 3,500:1. I am recording the HTML
cost rather than burying it because it is the one real regression in the change, and because if
the candidate ladder is ever widened, this is the number that will stop being negligible.

## 5. What I changed

`services/marketplace_storefront.py` — `media_box()`, the single component every product photo on
the site passes through, plus its four image call sites. `tests/test_marketplace_storefront.py` —
116 → 120 tests.

Nothing else. No CSS, no JavaScript, no routes, no templates, no environment variables, no new
test files, no `?v=` asset token, no `bot.py`.

### 5.1 Design decisions and their reasons

**A host allowlist** (`_RESIZE_HOSTS`), not a try-everything — §4.2. Variants are additionally
refused for any URL that is not https, carries a query string (a second `?` breaks it, and a
signed URL must not be rewritten), has a fragment, names an explicit port, or is not a still
raster (`.jpg`/`.jpeg`/`.png` — a GIF may be animated and would be flattened to frame one; an SVG
has no pixels). Refusal emits byte-identical markup to before. **The degradation path is "no
change", not "a guess."**

**The ladder stops at 800** (`_RESIZE_WIDTHS = (200, 400, 600, 800)`). The supplier originals are
750–800 px and OSS does not upscale, so a 1200 candidate would ship the original under a false
width declaration — worse than not offering it, because the browser would believe it.

**`sizes` is opt-in per call site**, and it is what gates the `srcset`. The box width is a
property of the caller, not of the image; a `w` descriptor without a `sizes` makes the browser
assume `100vw` and over-select. Same reasoning as `cart` being a `card_html` parameter.

**Widths were measured in a browser, not read off the stylesheet.** The grid is
`auto-fill minmax(230px, 1fr)`, so a column is a function of its container, not of a number in
the CSS. Measured: 225–396 px for a grid card, 607 for the PDP hero, 64 for a gallery thumbnail.
The three `sizes` round *up* to the widest measurement in each range — over-stating costs bytes,
under-stating ships a blurry image.

**webp in a `<source>`**, not `format,auto` — §4.2.

**`src` keeps the unmodified original.** Google Images, the structured-data `image` URL (Agent 5's
surface), and every no-variant reader are untouched. This was deliberate: the change had to be
invisible to every consumer except the browser doing layout.

**Commas percent-encoded** (`image%2Fresize%2Cw_400`). OSS accepts either form — verified
byte-identical — and `%2C` means a reader never has to work out whether HTML's `srcset`
comma-splitting rule reads `resize,w_400` as one candidate or two.

### 5.2 One of the four call sites is dormant in production

I wired `sizes` into all four image call sites. Only three fire today. **No production PDP renders
a thumbnail strip** — across all 24 page-1 PDPs, `mkt-gallery-thumb` appears zero times, because
no listing in the public catalogue carries enough images for one. So `THUMB_SIZES` is correct,
tested, and currently unexercised on the live site.

I kept it rather than deleting it because the thumbnail branch of `media_box` is live code reached
by a data condition, not dead code: a listing with multiple photos renders it, and the catalogue
will eventually have one. But it is measured-dormant, not measured-working, and that distinction
belongs in the record rather than in a footnote someone discovers later.

## 6. Verification

### 6.1 Tests

439 pass across the eight marketplace/storefront/SEO suites:

```
test_marketplace_storefront           120 passed
test_marketplace_seo                   46 passed, 20 subtests
test_marketplace_public_pages          90 passed, 13 subtests
test_marketplace_light_parity            9 passed
test_marketplace_pagination_canonical   36 passed
test_marketplace_listing_detail          9 passed,  3 subtests
test_storefront_add_to_cart            111 passed
test_marketplace_price_label_parity     18 passed
```

A whole-`tests/` run aborts in collection (8 pre-existing `AttributeError: module 'bot' has
no attribute ...` errors in unrelated files, 16,920 deselected, nothing of mine selected). Those
errors predate this branch and are not mine to fix; I note them for Agent 12 because they mean
`pytest tests/ -k ...` is not a usable gate on this repo today.

### 6.2 The tests can fail

A 4-mutation harness, each mutation caught by exactly one intended test, then restored green:

| Mutation | Caught by |
| --- | --- |
| Remove the `sizes` gate on the `srcset` | `test_a_width_descriptor_is_never_emitted_without_a_sizes` |
| Widen the host allowlist to everything | `test_no_srcset_is_emitted_against_an_origin_that_cannot_resize` |
| Use literal commas in the directive | `test_the_commas_in_the_directive_are_percent_encoded` |
| Rewrite `src` to a variant | `test_a_resizable_origin_gets_variants_and_keeps_the_original_as_src` |

One note on test integrity. The pre-existing `test_no_srcset...` test would have gone **vacuous**
under a host allowlist: its fixture URL is on host `cdn`, which is not allowlisted, so it would
have passed by accident while asserting nothing about the gate it names. It now says that
explicitly and has a companion driving the same function with a host that *is* allowlisted. A
green test whose fixture sidesteps the mechanism under test is worse than no test.

### 6.3 In a browser, against the live CSS and the live CDN

```
{"label":"AFTER","n":24,"loaded":5,"broken":0,"dpr":2,"cssW":265,
 "selected":{"image%2Fresize%2Cw_600%2Fformat%2Cwebp":19,"ORIGINAL":5},
 "cls":0,"shifts":0,"brokenBoxes":0}
```

19 of 24 images selected `w_600/webp` for a 265 px box at DPR 2 — the correct candidate. Zero
broken images, zero broken boxes, **CLS 0 across zero shifts**.

Two measurement traps worth recording:

- **`resize_window` did not change the viewport.** `innerWidth` stayed 1440 across 1512/900/420
  resizes, so three "different breakpoints" returned identical widths. The `sizes` breakpoints
  were instead measured by setting `.mkt-grid`'s width directly in JS — which is the actual input
  to `auto-fill`.
- **Cross-origin byte measurement returns zeros.** `performance.getEntriesByType('resource')`
  reported `imageBytes: 0` because the CDN sends no `Timing-Allow-Origin`. Bytes were therefore
  measured server-side; the browser was used only for candidate *selection* and CLS.

### 6.4 A hypothesis I had to discard

I predicted an inline `<picture>` would break the `height: 100%` chain that `.mkt-media`'s
aspect-ratio CLS strategy depends on, and planned a CSS edit plus a `?v=` token bump. Measured
after injecting one: `{box:265, img:265, imgW:265, picDisplay:"inline", picH:265}` — unchanged. A
child of an inline `<picture>` resolves percentage height against the nearest **block** ancestor,
which is `.mkt-media` itself.

That removed a CSS file edit and an asset-token bump from the change. Worth stating because the
instinct to "just bump the token to be safe" would have shipped a cache-busting change nothing
needed.

### 6.5 Gates

- `scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` → "No protected
  real-time audio path changed."
- Zero new `os.getenv`/`os.environ`, so the environment-contract gate is unaffected.
- No new test files, so no `config/ci_test_manifest.json` entry is required.
- No new routes, so the route-auth gate is unaffected.
- No `static/` asset changed, so `ASSET_TOKEN = "storefront-20261002b"` stays pinned as the test
  expects.

## 7. What I deliberately did not change

- **The ItemList indexable-only split.** Verified live on the grid: 24 product anchors in the
  HTML, `ItemList` with `numberOfItems=23` and 23 `itemListElement`s. The gap is deliberate — one
  card is not independently indexable — and it is Agent 5's surface, not mine.
- **`?page=2`'s `noindex,follow`** with a self-referential canonical. Agent 2's contract.
- **`.mkt-media`'s aspect-ratio CLS strategy.** Width/height attributes are the textbook fix, but
  supplier aspect ratios here are arbitrary; `aspect-ratio` + `object-fit: contain` is the correct
  call and it already measures CLS 0.
- **AVIF.** Spot-measured at −96% (6,994 B on a sample), better than webp. Deferred because it was
  spot-checked, not verified across all 44 images, and a `<source>` ordering mistake here fails to
  a 400, not to a fallback. It is a clean follow-up with a known method.
- **The variant `<form method="get">`.** Turning those radios into anchors is a two-line render
  change and I could have done it today. I did not: §8 shows it would mint 95 duplicate URLs for
  one listing, and whether a variant is a search entity is Agent 3's call. A rendering change that
  forces a product-model decision is not a rendering change.
- **Agent 2's constraint** that a server-rendered page call `search_visibility.robots_meta(path)`
  rather than writing a directive literal. Not violated — I emitted no directive.

## 8. Escalations — not mine to fix

### P1 — The related-products rail is keyed on the leaf category, so it is empty on two thirds of PDPs

My first measurement of this was wrong and I am recording the correction rather than the
conclusion. I sampled PDP 98, found **zero** outbound product anchors, and wrote it up as "a PDP
is a crawl dead end." Then I found PDP 85 rendering an `<h2>More from this department</h2>` rail
with a real anchor. The rail exists. The question was why it had not rendered.

Measured across all 24 page-1 PDPs:

```
render the "More from this department" heading:   8 of 24
outbound distinct product anchors per PDP:       {0: 16 PDPs, 1: 8 PDPs}
total edges among the 24:                         8
distinct products reachable from any PDP:         8
```

**The rail is keyed on the leaf category.** Leaf categories in this catalogue hold one or two
products, so the rail self-starves even when the parent department is well populated:

```
PDP 85  leaf  womens-clothing/tops-sets/rompers        = {85, 97}        -> rail renders, 1 anchor
        parent womens-clothing/tops-sets               = {26,51,85,86,92,97,104}   7 members
        top    womens-clothing                         = 14 members

PDP 98  leaf  bags-shoes/womens-shoes/woman-sandals    = {98}            -> rail absent, 0 anchors
        parent bags-shoes/womens-shoes                 = {45, 98}
        top    bags-shoes                              = {45, 98}
```

So PDP 85 gets 1 sibling where its parent department offers 7, and PDP 98 gets 0 where its parent
offers 1. **This is not catalogue sparsity — `womens-clothing` alone holds 14 products.** It is
the choice of key. The module works; it is asking too specific a question.

The obvious change — key the rail on the parent, falling back up the breadcrumb until it finds
siblings — is one I did **not** make. What counts as "related" is product semantics, and picking
a level is picking a relevance model. Owner: Agent 3, with Agent 10 on graph shape. The renderer
is ready for whatever they define and the `sizes` plumbing is already in place for its images.

### P1 — Variant states are addressable but unreachable, and would be duplicates if linked

Corroborating and extending Agent 1 §4 from the rendering side. Variants are real, server-rendered
and URL-addressable via `?opt_option1=…`, but **zero `<a href>` on any PDP contains
`opt_option`** — the selector is a `<form method="get">` with radio inputs, and a crawler does not
submit forms. Confirmed on 113.

Two things I can add that Agent 1 could not, because they are properties of the render:

**The variant data is already in the initial HTML.** The form carries a `data-mkt-variants`
attribute holding the entire variant table — id, option map, price, stock label — for all 8
variants of 113. The *information* is server-rendered and present. Only the addressable *states*
are unlinked. That is a narrower problem than "not crawlable".

**A variant URL renders a near-duplicate page.** Sampled 113 (8 variants), 112 (4 variants, 4
distinct prices) and 36 (95 variants):

| Listing | Variants | Distinct prices | Image changes per variant? | Variant URL canonical | robots |
| --- | --- | --- | --- | --- | --- |
| 113 | 8 | 1 (`$46.06`) | no — identical `src` | → bare PDP | `index,follow` |
| 112 | 4 | 4 | no — identical `src` | → bare PDP | `index,follow` |
| 36 | 95 | 1 (`$2.29`) | no — identical `src` | → bare PDP | `index,follow` |

**A variant URL never changes the rendered image.** For listing 36 that means 95 candidate URLs
differing only in which radio carries `checked` — same photo, same price, same text. This is
direct evidence for the caution Agent 1 attached to their own finding: minting variant URLs here
is not a coverage win, it is 95 duplicates.

Also worth knowing, and good news: a variant URL **already self-canonicalises to the bare PDP**.
So if one is ever discovered it consolidates correctly today. Nothing is leaking. Agent 2 should
have this; it means the variant question can be decided on merit rather than under pressure.

### P1 — `public, max-age=300` with no validator

Every public storefront page sends `Cache-Control: public, max-age=300` and **no `ETag`, no
`Last-Modified`**. Two consequences:

1. **No conditional revalidation.** Every revalidation is a full 36 KB transfer that could have
   been a 304.
2. **A public→private transition can serve stale for up to 5 minutes.** If a listing is
   unpublished, a shared cache keeps serving it. This is a correctness question, not a performance
   one.

Mitigating, and the reason this is P1 and not P0: `Vary: Cookie` fragments the shared cache on the
full cookie header, so in practice `max-age=300` mostly serves cookieless crawlers. That is also
why it cannot leak private data. But it means the 5-minute window is precisely the window a
crawler sees.

Owner: Agents 0/2 for the policy (how stale may an unpublished listing be?), 12 for the gate. I
did not pick a TTL or add an `ETag` unilaterally because the right answer depends on the
unpublish SLA, which is not mine to set.

### P2 — The entire image path depends on a third-party supplier CDN

All 147 image references are on CJ Dropshipping infrastructure. A CJ outage degrades LCP on every
PDP and every grid page, and there is no origin we control in the path. My change makes the
dependency cheaper (93% fewer bytes) but **deeper** — the page now also depends on CJ's transform
service, not just its storage. The `<img src>` fallback means a transform failure shows the
original rather than nothing, so the failure mode is "slow", not "broken", which is the right way
round. Still worth owning deliberately.

Owner: Agents 0/9 (media processing). The fix is a pull-through cache on an origin we control; it
is a larger change than this mission.

### P3 — `pytest tests/` cannot be collected

8 collection errors in unrelated files abort any whole-suite run (§6.1). Agent 12.

## 9. Handoffs

| Agent | What they need from me |
| --- | --- |
| 0 | §8. The three P1s are policy calls I declined to make alone. |
| 1 | Your §9 ("SSR is already real, verify before building") matches what I measured independently — §1 and §3 corroborate it. Your §4 variant finding is extended from the render side in §8: the data is already in a `data-mkt-variants` attribute, and a variant URL renders a byte-near-identical page that already self-canonicalises. |
| 2 | I emitted no robots/canonical directive. §8's cache-validator question is yours. Two more: the indexable `?category=` facet holding 2 products, and the fact that `?opt_option…` variant URLs are `index,follow` but already canonical to the bare PDP — so the variant question is not urgent. |
| 3 | §8's rail finding is the actionable one: the related-products module is keyed on the **leaf** category, which holds 1–2 products, while the parent department holds up to 14. Choosing the level is a relevance decision, so it is yours. The renderer and its image plumbing are ready. Also: for listing 36, 95 variants share one photo and one price — a variant is not a distinct entity here. |
| 5 | Your `Product.image` is built in the data layer, not by `media_box`, and is unaffected — verified on PDP 98 as a bare `cf.cjdropshipping.com` URL with no directive. If anything downstream ever scrapes the rendered markup instead, read `img[src]`, never the first `srcset` candidate. |
| 9 | The supplier-CDN dependency, and AVIF as a measured follow-up. |
| 11 | Cross-origin resource timing is blind here (no `Timing-Allow-Origin`); byte telemetry must come from the server side. |
| 12 | §6.2's mutation result is the proof the new tests bite. §8's P3 blocks whole-suite runs. |

## 10. Status

`ffb88a1ca` is committed on `search-os/agent-04-rendering-performance` and **not merged, not
deployed**. Production still serves the unresized originals. The change is verified against
production's CSS and CDN, but "verified" is not "live".
