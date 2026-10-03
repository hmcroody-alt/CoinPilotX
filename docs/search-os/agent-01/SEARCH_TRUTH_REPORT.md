# Agent 1 — Search Truth Report

**Mission:** PULSE SEARCH OS / OMNIPRESENCE — Agent 1, Search Forensics / Live SEO Recon
**Question:** *What does the internet actually see?*
**Measured:** 2026-10-03, production `pulsesoc.com`, anonymous
`Googlebot/2.1` UA, read-only GETs, rate-limited (0.4–1.0 s), single-threaded.
**Primary target:** `https://pulsesoc.com/pulse/marketplace`

**Evidence strength.** Production reports its deployed commit at
`/api/service/health` as `5bdf4e431d1fd9164706962d7610d287a1f3092b`, which is
**identical to this worktree's base**. Repository reads therefore correspond 1:1 to
served bytes — an unusually strong position, and the reason several findings below
can cite both a live response and the exact source line that produced it.

**Scope discipline.** Agent 1 investigates and does not fix. **No source file was
modified.** No implementation is prescribed. Cross-agent alerts are in
`FLEET_DEPENDENCY_REPORT.md`, published ahead of this report per the brief.

---

## 1. Executive summary

PulseSoc's search surface is **much healthier than the fleet's premise assumes, and
much smaller than it needs to be.**

The infrastructure is largely built and correct. A sitemap index with six children
serves 148 URLs, **all 148 return HTTP 200**, **147 are self-canonical**, and **none
is `noindex`**. `robots.txt` is generated from a single policy table rather than
hand-maintained. Product PDPs already carry a valid JSON-LD `@graph`. A Google
Merchant Center feed is **already live** with 36 items. A CI gate already enforces
that every submitted URL is indexable. Both P0s from the previous baseline are closed.

Three things are genuinely wrong, and they are wrong at different layers:

1. **The catalogue is tiny and its variants are invisible.** 44 public products
   narrow to 41 indexable and 36 feed-eligible. 31 of the 44 are multi-variant, with
   counts up to 95 — and **not one variant is reachable by a crawler, represented in
   structured data, or grouped in the feed.** The addressable product space is
   roughly an order of magnitude larger than what search can see.
2. **The site still tells Google it is a crypto company.** JSON-LD
   `Organization.legalName` is `"CoinPlotXAI Inc."` and ~60 of 87 pages in the largest
   child sitemap describe the retired crypto/sports product.
3. **Nothing pushes.** There is no SEO, sitemap, feed or IndexNow worker in the
   Procfile. IndexNow is wired but its endpoint says in its own source that it does
   not submit. Every surface is pull-only against a catalogue that measurably drifts
   within minutes.

The single most important *negative* result: **there is no price misrepresentation
live.** The repo documents four disagreeing Merchant feed rows as of 2026-10-01; as of
today the disagreeing rows are excluded from the feed and prices agree across all
three search-visible surfaces. The stop condition did not fire. But the guard removes
rows rather than reconciling data, and it fails open.

---

## 2. Method, and what would invalidate these numbers

Anonymous `Googlebot/2.1` UA, no cookies, no JavaScript execution. Every artifact is
in `.forensics/` on branch `search-os/agent-01-forensics`:
`probe.sh`, `sweep.sh`, `sweep.tsv` (148 rows), `catalog.py`, `catalog.json` (41 PDPs),
`feed.xml`, `grid.html`, `pdp113.html`, `all_sitemap_urls.txt`.

Two measurement traps I hit and corrected — both would have produced confident false
findings, and both are recorded so no other agent repeats them:

- **Single-quoted attributes.** Canonical/robots tags are emitted with single quotes
  inside Python f-strings. A `rel="canonical"` regex reports mass absence; a prior
  pass recorded in `docs/seo/00_baseline_and_gap_list.md` "reported 189 pages missing
  rel=canonical… That was false." All probes here are quote-agnostic (`['\"]`).
- **`opt_`-prefixed variant params.** A grep for `name="option1"` returns zero on a
  PDP that *does* server-render variant radios, because the control names are
  `opt_option1`. I initially concluded "client-rendered", which was wrong. Testing
  `?option1=…` likewise returns byte-identical HTML and fakes "no variant URLs".

**What would invalidate this report:** a deploy moving production off `5bdf4e431`;
supplier sync changing catalogue membership (it demonstrably does, within minutes);
or PR #152 merging, which changes `/pulse/cart` directives.

---

## 3. The crawl funnel — measured

| Stage | Count | Gate |
|---|---|---|
| Public product pages | **44** | `marketplace_listing_lifecycle.public_sql` ∧ `discovery_visibility.discovery_visible_sql` |
| Reachable by crawling the grid | **44** | 2 pages (24 + 20); **0 orphans** |
| Indexable | **41** | `MIN_DESCRIPTION_CHARS = 40` → ids 50, 52, 110 serve `noindex,follow` |
| In `sitemap-products.xml` | **41** (+ grid) | `marketplace_seo.eligibility().indexable` |
| In Merchant feed | **36** | `.feed_eligible` — strictly narrower |
| **Variants behind those 44** | **≫ 500** | **0 crawlable, 0 in schema, 0 grouped** |

Pagination terminates correctly: `?page=3` and `?page=99` both clamp and
canonicalise to `?page=2`. Page 1 canonicalises to the bare `/pulse/marketplace`
(no `?page=1` duplicate). Real `<a href>` with `rel="next"`.

## 4. Sitemap reality

| Child | URLs | Note |
|---|---|---|
| `sitemap-pages.xml` | 87 | ~60 describe the retired crypto product |
| `sitemap-posts.xml` | 15 | PulseDrop promos duplicating product titles |
| `sitemap-categories.xml` | 4 | of 12 live departments — correct, see §7 |
| `sitemap-products.xml` | 42 | 41 PDPs + the grid |
| `sitemap-live.xml` | **0** | permanently empty |
| `sitemap-replays.xml` | **0** | permanently empty |
| **Unique total** | **148** | zero cross-sitemap duplicates |

**148/148 → HTTP 200. 147/148 self-canonical. 0 `noindex`.** Byte range
4,163–50,375; median 21,151.

Supersedes `docs/seo/00_baseline_and_gap_list.md` (354 URLs, 2026-09-18 @
`469eea07`). **Both of its P0s are closed:** the 16 `/pulse/post/*` HTTP 500s are
gone (all 15 current post URLs are 200/`index,follow`/self-canonical), and no
`noindex` URL — including `/signup` — is in the corpus.

Thinnest pages, all 200 and indexable: seven `/learn/*` pages at 4.1–4.3 kB and
`/sports-edge` at 5.6 kB. All describe the retired product.

## 5. Canonical reality

One defect in the entire submitted corpus: **`/arena-preview`** returns 200,
`index,follow`, and emits **no `rel=canonical`**.

Two directives are in play and they disagree on one path. `search_visibility`
declares `/pulse/cart` as `noindex,follow` — which is why it is absent from the
generated `robots.txt` — while production serves **`noindex,nofollow`**. PR #152
claims this fix and is therefore confirmed unmerged.

A canonical alias exists: `/support` → `/help`.

## 6. Parameter space — contained, not a trap

A correction to the fleet's likely premise. Four parameters are honoured
(`page`, `category`, `q`, `sort`). Measured behaviour:

| Request | Status | Robots | Canonical |
|---|---|---|---|
| `?page=2` | 200 | `index,follow,…` | **self** |
| `?page=3`, `?page=99` | 200 | `index,follow,…` | → `?page=2` (clamped) |
| `?category=womens-clothing` | 200 | `index,follow,…` | **self**, distinct `<title>`/`<h1>` |
| `?category=not-a-real-category` | 200 | **`noindex,follow`** | → bare grid |
| `?sort=price_asc` | 200 | `index,follow,…` | → bare grid |
| `?q=shoes` | 200 | `noindex,follow` | → bare grid |
| `?utm_source=…` | 200 | `index,follow,…` | → bare grid (**byte-identical**) |

No faceted explosion exists and none needs to be fixed.

## 7. Category reality

All **12** departments return 200, `index,follow`, self-canonical, non-empty, and are
linked from the grid's nav — so none is orphaned. Only 4 are in the sitemap, which
looks like a defect and is not: `CATEGORY_MIN_INDEXABLE_LISTINGS = 3`, counted over
*indexable* rather than public listings, explains every exclusion.
*(I first measured this as a policy divergence; that was my own lexical-`sort` error.)*

Depth-2 sections are deliberately excluded from the sitemap but stay crawlable via
the department sub-nav. PDP breadcrumbs do link depth-3
(`?category=sports-outdoors/sportswear/pants`).

Per-department public / indexable: womens-clothing 14/11, mens-clothing 9/7,
health-beauty-hair 3/2, home-garden-furniture 3/1, jewelry-watches 5/2, bags-shoes
2/2, phones-accessories 2/1, sports-outdoors 2/1, consumer-electronics 1/0,
automobiles-motorcycles 1/1, home-improvement 1/1, toys-kids-baby 1/0.

**Determinism:** individual pages are byte-identical across three consecutive
fetches. **Catalogue membership is not** — it moved between runs minutes apart
(womens-clothing 15→14, mens-clothing 10→9). Expect `sitemap-categories.xml`
membership to oscillate around the threshold.

## 8. Structured data reality

Present `@type`s: Product, Offer, **AggregateOffer**, Organization, WebSite, WebPage,
AboutPage, ContactPage, MobileApplication, BreadcrumbList, FAQPage, SearchAction,
Question, Answer, ListItem, Service, ItemList.
**Absent: `VideoObject`, `ImageObject`, `ProductGroup`, `hasVariant`.**

The Product graph is **honest**, which is the finding Agent 5 most needs:
- `Offer` → `AggregateOffer` switches correctly on real price spread, with
  `lowPrice`/`highPrice` matching observed variants exactly (listing 112:
  `27.84`–`37.72` vs `$27.84/$29.31/$31.69/$37.72`).
- `sku` is synthetic but honest (`pulsesoc-listing-163`).
- `brand`, `gtin`, `mpn`, `aggregateRating`, `review` are **deliberately omitted**
  with documented reasons (no backing column; no review table).
- **No fabricated value was found anywhere on any surface.**

`Organization.legalName` = **`"CoinPlotXAI Inc."`** — the entity-identity defect.

## 9. Variant reality — the P1

31 of 44 public listings are multi-variant; counts reach 95. Variants are
server-rendered as a `<form method="get">` of radio inputs and **are** URL-addressable
via `?opt_option1=Black&opt_option2=XL` (the server echoes `checked`).

They are nonetheless invisible to search in all three channels:

1. **Uncrawlable** — zero `<a href>` on any PDP contains `opt_option`; a crawler
   never submits a form.
2. **Unmodelled** — no `ProductGroup` / `hasVariant` / `variesBy`.
3. **Ungrouped** — no `g:item_group_id`, no `g:color` / `g:size` in the feed.

## 10. The price-truth result — no misrepresentation live

Two price authorities exist: `price_label` (typed at publish time; what the feed
publishes) and `marketplace_listing_variants.price_cents` (what the PDP renders and
**what checkout charges**). `marketplace_seo.price_label_contradicts_variants()`
exists to detect disagreement and records that **four of the then-35 feed rows
disagreed on 2026-10-01** — listing 36 advertised $38.00 against a $2.29 variant.

Measured today: the 5 sitemap products absent from the feed (**112, 15, 89, 35, 36**)
are **exactly** the disagreeing rows. **0 feed-vs-PDP mismatches** across 36 items;
**0 grid-vs-PDP mismatches** across 20 comparable cards (the grid publishes the
variant-derived low price, inside the PDP range). The stop condition did not fire.

**Caveat that matters more than the result:** the guard *excludes rows*; it does not
*reconcile data*. `price_label` remains wrong on ≥5 rows, and the check **fails open**
— a caller that does not join variants gets `False`.

## 11. Image reality

Every product image is supplier-hosted on `cf.cjdropshipping.com` /
`oss-cf.cjdropshipping.com`. Tested directly: **200 / `image/jpeg`** to
`Googlebot-Image/1.0`, byte-identical to a browser UA (no hotlink protection), and
both hosts serve `robots.txt` = `Allow: /`. **Crawlable today** — but the entire
Google Images and Shopping image surface sits on a domain PulseSoc does not control.

PDPs ship **1–3 distinct `<img src>`, dominantly 1**, on listings with up to 8
variants and multi-image galleries. `srcset` deliberately absent;
`loading=lazy/eager` set. Feed and PDP reference **different supplier CDN hosts**.

## 12. Social / video reality

`/pulse/reels/<id>` → **302 → `/login`** anonymously; profiles, stores, spaces
likewise. `/pulse/post/<id>` is public (15 in sitemap, all 200 / `index,follow` /
self-canonical, OG tags) but carries **no JSON-LD**, and no `VideoObject` exists
anywhere — so Reels have no video search surface even where public.

The 15 indexable posts are PulseDrop promos whose titles **duplicate product titles
verbatim**: the only indexable social content competes with the PDPs it promotes.

`pulse_social_shell` (`bot.py:50820`) **is** the login wall — it calls
`require_account()`, so relaxing a handler's own guard is a no-op.

## 13. Error and redirect reality

`/pulse/marketplace/{999999,1,abc,163abc}` → 404, zero redirects.
`/pulse/MARKETPLACE/163` → 404 (case-sensitive; no duplicate surface).
**`/pulse/marketplace/` and `/pulse/marketplace/163/` → 404, not 301** — no duplicate
risk, but trailing-slash external links are hard dead ends.
PDPs serve 503 + `Retry-After` on backend failure rather than a soft 200.

## 14. Infrastructure inventory

Reported as state only. **No secret value was read, printed or stored.**

| Component | State |
|---|---|
| Merchant Center feed | **LIVE** — 36 items, `x-robots-tag: noindex,follow`, `max-age=300` |
| `GOOGLE_SITE_VERIFICATION` | PRESENT (meta) |
| GSC property | `sc-domain:pulsesoc.com` |
| `GA_MEASUREMENT_ID` + 8 Ads conversion labels | PRESENT |
| Google Tag Manager | ABSENT |
| `BING_SITE_VERIFICATION` | PRESENT (meta) |
| `BingSiteAuth.xml`, Bing URL Submission API | ABSENT |
| IndexNow key file + `/api/indexnow` | PRESENT — **metadata only**; its source says it does not submit |
| IndexNow submission logic | **ABSENT** |
| **SEO / sitemap / feed / IndexNow worker** | **ABSENT from Procfile** (workers: undx, email, ads, alert, media, supplier) |
| `services/seo_service.py` | ORPHAN — 30 lines, zero callers |

**Zero push-side automation.** Every surface is pull-only.

## 15. Phases not executed — no data, no claims

- **15** authorized read-only GSC inspection — not performed.
- **18** performance baseline (TTFB/LCP/CLS/INP, payload) — not performed. Only raw
  byte sizes were captured (§4).
- **25** competitive research — not performed.

Per the brief: **UNKNOWN IS BETTER THAN INVENTING AN ANSWER.**

---

## SEARCH REALITY MATRIX

| Surface | CRAWLABLE | INDEXABLE | SSR | CANONICAL | SCHEMA | SITEMAP | IMAGE READY | VIDEO READY | PRIVACY SAFE | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| Marketplace grid | YES | YES | YES | YES (self) | PARTIAL | YES | PARTIAL | NO | YES | `ItemList`; `?page=1` → bare canonical |
| Category (department) | YES | YES | YES | YES (self) | PARTIAL | **PARTIAL** (4/12) | PARTIAL | NO | YES | threshold = 3 indexable; correct by policy |
| Subcategory (depth 2–3) | YES | YES | YES | YES (self) | PARTIAL | **NO** | PARTIAL | NO | YES | deliberate; crawlable via sub-nav |
| PDP | YES | YES (41/44) | YES | YES (self) | **YES** | YES | **PARTIAL** | NO | YES | 3 thin → `noindex,follow` |
| **Variant** | **NO** | **NO** | YES | → parent | **NO** | **NO** | NO | NO | YES | **P1** — URL-addressable, zero crawl path |
| Seller storefront | **NO** | **NO** | UNKNOWN | UNKNOWN | UNKNOWN | NO | UNKNOWN | NO | YES | 302 → `/login` |
| Signal / post | YES | YES (15) | YES | YES (self) | **NO** | YES | PARTIAL | NO | YES | OG only; duplicates product titles |
| Reel | **NO** | **NO** | UNKNOWN | UNKNOWN | **NO** | NO | NO | **NO** | YES | 302 → `/login`; no `VideoObject` |
| PulseDrop | YES | YES | YES | YES (self) | NO | YES | PARTIAL | NO | YES | = the 15 posts |
| Collection | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NO | UNKNOWN | NO | UNKNOWN | no distinct surface found |

"PRIVACY SAFE = YES" means no private data was observed on a crawlable surface. No
stop condition fired.

## SEARCH TRUTH MATRIX

Agreement of each field across surfaces. `—` = surface does not carry the field.

| Field | DATABASE | PDP HTML | HYDRATED UI | JSON-LD | SITEMAP | MERCHANT | PULSEDROP |
|---|---|---|---|---|---|---|---|
| PRODUCT ID | `id` | in URL | same | **`pulsesoc-listing-163`** | in URL | **`163`** | in link |
| URL | — | self-canonical | same | matches | matches | matches | matches |
| TITLE | `title` | verbatim + brand | same | verbatim | — | verbatim | **duplicates PDP title** |
| PRICE | **2 authorities** | variant-derived | same | `Offer` / `AggregateOffer` | — | `price_label`, guarded | — |
| CURRENCY | `currency` | USD | same | `USD` | — | `… USD` | — |
| AVAILABILITY | stock ∧ type | `In stock` | same | `schema.org/InStock` | — | `in_stock` | — |
| **VARIANT** | `option1/2/3` | **radios, uncrawlable** | same | **ABSENT** | **ABSENT** | **ABSENT** | — |
| SELLER | `seller_store_name` | shown | same | `Organization: "M&W Store"` | — | — | — |
| PUBLICATION STATE | `status` + `approval_status` | 404 / 200 / `noindex` | same | present iff indexable | iff `.indexable` | iff `.feed_eligible` | — |

**Two disagreements, both real:**
1. **PRODUCT ID** — Merchant says `163`, JSON-LD says `pulsesoc-listing-163`.
   Nothing reconciles them.
2. **PRICE** — two database authorities. Not currently visible on any search surface,
   because the feed guard excludes every row where they disagree. The *surfaces* agree;
   the *data* does not.

**VARIANT is absent from four of seven columns** — the P1 restated as a truth gap.

---

## Deliverable index

| # | Deliverable | Where |
|---|---|---|
| 1 | Search Reality Map | this file — matrices + §3–§14 |
| 2 | Current Crawl Graph | §3, §6, §7 (grid → 2 pages → 44 PDPs; 0 orphans; PDPs are dead ends) |
| 3 | Search Infrastructure Inventory | §14 |
| 4 | Search Truth Report | this file |
| 5 | Fleet Dependency Report | `FLEET_DEPENDENCY_REPORT.md` |
| 6 | Before Baseline | §3, §4, §7, §10 + `.forensics/sweep.tsv`, `catalog.json`, `feed.xml` |

**Baseline, one line, for re-measurement after the fleet ships:**
> 2026-10-03 @ `5bdf4e431` — 148 sitemap URLs (148×200, 147 self-canonical, 0
> `noindex`); 44 public / 41 indexable / 36 feed-eligible products; 12 departments
> (4 in sitemap); 0 orphans; 0 price disagreements across surfaces; 0 crawlable
> variants; 0 `VideoObject`; 0 push-side automation.
