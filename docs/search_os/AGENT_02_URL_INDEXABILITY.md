# Agent 2 — Canonical URL + Indexability Engine

Status record, findings, and downstream contracts for the PULSE SEARCH OS fleet.

Evidence gathered 2026-10-03 against `origin/main` = `5bdf4e431`, which
`/api/service/health` reported as the deployed production commit at the time.
Every production claim below was taken off the wire with a Googlebot user agent,
not inferred from the source.

Production moved to `6e9b64110` while this was being written — one commit ahead,
with `5bdf4e431` as its ancestor and **no change to any of
`search_visibility.py`, `marketplace_seo.py` or `marketplace_storefront.py`**.
The §5 evidence was re-run against `6e9b64110` and still reports 0 faults. Main
takes roughly 60 commits a day from parallel sessions; re-run the verifier (§7)
rather than trusting either SHA.

---

## 1. Fleet status record

```
AGENT           2
ROLE            Canonical URL + Indexability Engine
STATUS          Verification complete. No behavioural change applied.
BRANCH          search-os/agent-02-url-indexability
WORKTREE        /Users/hmcherie/Desktop/cpx-urlindex   (durable, not /tmp)
BASE            origin/main @ 5bdf4e431 (== production)
CURRENT TASK    Published findings + contracts. Awaiting Agent 0 on one defect.
OWNED SURFACES  canonical policy, URL normalisation, indexability directives,
                redirects, 404/410 semantics, facets, query parameters,
                pagination URL rules, removed-content lifecycle, search URLs
UPSTREAM        Agent 0 (integration slot for the ProxyFix defect, §6.1)
                Agent 1 (nothing published as of this writing)
DOWNSTREAM      3, 4, 5, 6, 7, 8, 9, 10, 11, 12 — see §8
BLOCKERS        §6.1 needs an Agent 0 integration slot (app-wide WSGI change).
                §6.2 needs a product-owner decision on the legacy crypto set.
LAST UPDATE     2026-10-03
```

Agent 0 has established no coordination mechanism — there are no
`origin/search-os/*` branches and no shared state file. Per the brief's "do not
create a fragile shared coordination file", this record lives in Agent 2's own
branch under an agent-scoped filename. Nothing here is a file another agent
also writes.

---

## 2. Headline: the engine already exists and is correct in production

The canonical/indexability engine this mission describes is **already built,
already tested, and already live**. It is not partial and it is not stale.

| Component | File | Lines |
|---|---|---|
| Indexability + canonical policy | `services/search_visibility.py` | 731 |
| Listing-level eligibility | `services/marketplace_seo.py` | 842 |
| Storefront canonical + robots | `services/marketplace_storefront.py` | — |
| Listing lifecycle states | `services/marketplace_listing_lifecycle.py` | — |

Existing test coverage, none of it written by this agent:

| Suite | Tests |
|---|---|
| `tests/test_search_visibility.py` | 35 |
| `tests/test_marketplace_pagination_canonical.py` | 30 |
| `tests/test_merchant_center_feed.py` | 41 |
| `tests/protection/test_sitemap_entries_are_indexable.py` | 16 |
| `tests/test_sitemap_integrity.py` | 16 |
| `tests/test_registry_landing_pages.py` | 6 |
| **Total** | **144** |

Plus three mutation harnesses under `scripts/protection/`:
`mutate_marketplace_category_sitemap.py`,
`mutate_marketplace_pagination_canonical.py`,
`mutate_sitemap_and_robots_agreement.py`.

A rebuild would have been destruction disguised as delivery. The correct
deliverable was independent verification, a gap list, and the downstream
contracts — which is what this document is.

---

## 3. URL class matrix (observed, not designed)

Derived by calling `search_visibility.classify()` directly on the deployed
commit. Directives truncated to their first two tokens; the full index
directive is `index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1`.

| Path | Directive | Sitemap | Reason |
|---|---|---|---|
| `/` | `index,follow` | yes | public content |
| `/help` | `index,follow` | yes | public content |
| `/support` | `index,follow` | **no** | canonical alias of `/help` |
| `/pulse/marketplace` | `index,follow` | yes | public product collection |
| `/pulse/marketplace/163` | `index,follow` | yes | public product collection |
| `/pulse/post/123` | `index,follow` | yes | public post permalink |
| `/pulse` | `noindex,follow` | no | authenticated social application |
| `/pulse/orders` | `noindex,follow` | no | authenticated social application |
| `/pulse/search` | `noindex,follow` | no | authenticated social application |
| `/pulse/u/<handle>` | `noindex,follow` | no | authenticated social application |
| `/search` | `noindex,follow` | no | internal search results |
| `/arena/live` | `noindex,follow` | no | authenticated arena surface |
| `/admin/seo` | `noindex,nofollow` | no | administrative surface |
| `/api/pulse/feed` | `noindex,nofollow` | no | JSON API, not a page |
| `/static/*` | `noindex,nofollow` | no | asset path |
| `/.well-known/*` | `noindex,nofollow` | no | protocol metadata |
| `/portfolio` | `noindex,nofollow` | no | financial information |
| `/portfolio-ai` | `index,follow` | yes | public content |
| `/learn/*`, `/quote/*` | `index,follow` | yes | public content (fallthrough) |

Three structural properties of this table are load-bearing and must not be
"tidied":

**3.1 `/pulse` is default-deny, with carve-outs ordered first.** A new
`/pulse/*` route is non-indexable until someone makes it public on purpose. The
commerce paths (`/pulse/marketplace`, `/pulse/post`, `/pulse/help`,
`/pulse/support`) precede the broad rule and are the only exceptions. Adding a
new public `/pulse` surface means adding a rule *above* the deny, not below it.

**3.2 The broad `/pulse` rule is `noindex,follow`, never `nofollow`.**
`robots_disallow_prefixes()` only emits `Disallow:` for `NOINDEX_NOFOLLOW`
paths. Promoting `/pulse` to `nofollow` would emit `Disallow: /pulse/` and
de-index the entire commerce graph in one line. Measured: 145 static `/pulse/*`
routes, 5 anonymous 200s, 138 behind the auth wall.

**3.3 A canonical alias stays `index,follow` and leaves the sitemap.**
`/support` → `/help`. Adding `noindex` to a page that already names a different
canonical is the conflicting-signal pair Google warns about; dropping it from
the sitemap is the correct half of the fix.

### Fallthrough

`classify()` ends at `_d(INDEX_DIRECTIVE, True, "public content")` — unknown
paths are **indexable by default** outside `/pulse`. That is the right default
for a marketing site and the reason §6.2 exists.

---

## 4. Canonical construction — there are TWO authorities

This is the single most important thing for downstream agents to understand,
and it is not documented anywhere else.

**4.1 `search_visibility.canonical_url(path)`** — path-level policy. Absolute,
on the constant `CANONICAL_ORIGIN`, resolves aliases, drops every query
parameter except those allowlisted per-path by name:

```python
_CONTENT_QUERY_PARAMS = {"/pulse/marketplace": ("category",)}
```

Kept params are sorted via `urlencode(sorted(kept))` so there is one spelling.

**4.2 `marketplace_storefront.RenderedPage.canonical_path`** — the storefront's
own, built at `marketplace_storefront.py:1070`. Keeps `category` **and `page`**,
with `page` taken from the *served* page (clamped) rather than the requested
one.

These disagree, deliberately, and the disagreement is a trap:

```
search_visibility.canonical_url("/pulse/marketplace?utm_source=x&page=2")
  -> https://pulsesoc.com/pulse/marketplace          # page dropped

production GET /pulse/marketplace?page=2
  -> <link rel="canonical" href="https://pulsesoc.com/pulse/marketplace?page=2">
```

**Contract:** for any `/pulse/marketplace` URL carrying `page`, the storefront's
`canonical_path` is authoritative and `search_visibility.canonical_url` is not.
Any agent that calls `canonical_url` on a paginated marketplace URL will emit a
canonical that contradicts the page's own, collapsing page 2 onto page 1 and
hiding every product that only appears on later pages.

The sitemap is already safe here — `test_a_paginated_path_is_not_sitemap_eligible`
pins it, and `sitemap_eligible()` rejects any query string the canonical would
not keep. The hazard is live for Agents 5, 7, 9 and 10, who do not go through
the sitemap.

**4.3 Robots derivation in the storefront** (`marketplace_storefront.py:1994`):

```python
robots = (page.robots_extra if page.robots_extra
          else (search_visibility.robots_meta(page.canonical_path)
                if page.indexable else search_visibility.NOINDEX_NOFOLLOW))
```

`robots_extra` wins because a renderer that has decided a specific row is a soft
404 knows something a path-shaped policy cannot. The positive directive is
*asked for*, not restated — an earlier version spelled out a shorter directive
from memory and silently dropped `max-snippet:-1` and `max-video-preview:-1`.
**Never restate a directive literal; call `robots_meta()`.**

### 4.4 Case is preserved on purpose

`classify()` lowercases for matching; `canonical_url()` does **not** lowercase
what it emits. That asymmetry looks like a bug and is a requirement:

```
GET /quote/crypto/BTC  -> 200, canonical https://pulsesoc.com/quote/crypto/BTC
GET /quote/crypto/btc  -> 200, canonical https://pulsesoc.com/quote/crypto/BTC
GET /quote/crypto/BtC  -> 200, canonical https://pulsesoc.com/quote/crypto/BTC
```

The quote handler normalises the ticker itself and every case variant converges
on one uppercase canonical. A `canonical_url` that lowercased would emit
`/quote/crypto/btc` as the canonical of a page whose handler says `BTC`, turning
a correctly-consolidated set into a self-contradicting one across every symbol
page. Do not "fix" this.

---

## 5. P0 safety gate — production evidence

All of the following was read off `https://pulsesoc.com` with a Googlebot UA.

**5.1 Sitemap ↔ live agreement: 0 faults.** `scripts/search_os/verify_sitemap_vs_live.py`
(new, this agent — see §7) resolved every `<loc>`:

| Sitemap | Submitted | Faulty |
|---|---|---|
| `sitemap-products.xml` | 42 | 0 |
| `sitemap-categories.xml` | 4 | 0 |
| `sitemap-posts.xml` | 15 | 0 |

**5.2 Exact set equality between sitemap and indexable reality.** The 41
sitemapped product IDs are identical to the 41 live `index,follow` PDPs
(`diff -q` → exact agreement). No URL is recommended-but-not-indexable, and
none is indexable-but-orphaned. The 3 thin listings (IDs 50, 52, 110) serve
`noindex` and appear in no sitemap — `marketplace_seo.MIN_DESCRIPTION_CHARS = 40`
refusing them, exactly as designed.

**5.3 Unknown products 404 cleanly.** 156 of 200 probed IDs answered 404 with
zero redirects. No soft 404, no redirect-to-hub.

**5.4 Pagination is bounded and has no crawl trap.**

| Request | Result |
|---|---|
| `?page=2` | 200, self-canonical `?page=2` |
| `?page=999999` | 200, canonical clamped to `?page=2` (the real last page) |
| `?page=0`, `?page=-1`, `?page=abc` | fall back to the bare hub |

Clamping the canonical to the served page is what prevents soft-404-at-scale.
Indexability still keys on the *requested* page, so no URL that is noindex today
can become indexable through the clamp.

**5.5 Tracking parameters cannot mint a URL.** `utm_*` and `sort` collapse to
the bare hub. `?category=<real>` self-canonicals. `?category=<invented>` →
`noindex,follow` + canonical to the hub, so an invented facet value cannot mint
an indexable empty page.

**5.6 Canonical/host injection is impossible.**

```
X-Forwarded-Host: evil.example.com  -> 200, canonical still https://pulsesoc.com/...
Host: evil.example.com              -> 404 at the Railway edge
```

The canonical is built from the `CANONICAL_ORIGIN` constant, never from a
request header. This is the correct design and the reason §6.1 is low severity.

**5.7 Internal search cannot mint indexable pages.** `/search?q=…` →
`noindex, follow`. `/pulse/search?q=…` and `/pulse/topic/<tag>` → 302.

**5.8 The retired sitemaps are empty, not stale.** `sitemap-live.xml` and
`sitemap-replays.xml` now contain 0 URLs. The `/arena` rule retired them exactly
as `tests/protection/test_sitemap_entries_are_indexable.py` predicted.

---

## 6. Gaps

### 6.1 Protocol-downgrading redirect chain — the only real defect

Every Werkzeug routing redirect emits an `http://` `Location`, costing an extra
hop through the edge:

```
GET https://pulsesoc.com/pulse//marketplace
  -> 308  location: http://pulsesoc.com/pulse/marketplace
  -> 301  location: https://pulsesoc.com/pulse/marketplace
```

Reproduced on the hub and on PDPs (`/pulse/marketplace//163` → 308 →
`http://pulsesoc.com/pulse/marketplace/163`).

**Root cause.** There is no `ProxyFix` and no `PREFERRED_URL_SCHEME` anywhere in
the app. Behind Railway's TLS edge `wsgi.url_scheme` is `http`, so
`merge_slashes`/`strict_slashes` redirects build their `Location` from `http`.
The app only reads `X-Forwarded-Proto` by hand, at `bot.py:2988` and
`bot.py:3187`.

**Severity: low.** It only fires on malformed URLs, the canonical itself is
never affected (§5.6), and HSTS `max-age=31536000` protects browsers. But it
violates the mission's "no protocol downgrades in a redirect chain".

**Not applied, deliberately.** `ProxyFix` mutates `request.scheme`, `host` and
`remote_addr` for the whole monolith and would conflict with
`services/client_address.py`, which reads the leftmost `X-Forwarded-For` entry
on purpose. If applied it must be `ProxyFix(app.wsgi_app, x_proto=1)` and
nothing else — `x_host=1` would hand canonical-host control to a header and undo
§5.6. **This needs an Agent 0 integration slot, not an Agent 2 commit.**

### 6.2 Thirteen `sitemap-pages.xml` faults — owner decision, not a bug

Twelve legacy pages emit **no robots directive at all** (`/learn/*`, `/quote/*`,
`/predictions/crypto`, `/sports-edge`) and `/arena-preview` has **no
canonical**.

Absence of a directive defaults to `index,follow`, which is what
`classify()` returns for these paths by fallthrough — so the pages are not
*contradicting* policy, they are merely not *deriving* from it. Every one is
inside the legacy crypto set that is pending a product-owner decision. **Not
de-indexed unilaterally.** De-indexing 12 indexed, sitemapped pages on an SEO
agent's own judgement is precisely the "better-looking URL" failure the brief
forbids.

### 6.3 Policy is not universally derived

`/search` hardcodes `"noindex, follow"` — with a space — against the module's
`"noindex,follow"`. Semantically identical to a crawler, but it proves some
pages restate the policy instead of calling `search_visibility.robots_meta()`.
Nothing enforces agreement for paths outside a sitemap;
`tests/test_registry_landing_pages.py` does it for 6 registry pages only.

**Recommended (Agent 12):** a test that walks `app.url_map`, fetches every
`@public_route` GET with the test client, and asserts the rendered robots meta
equals `search_visibility.robots_meta(path)`. Route existence must come from
`url_map`, not a decorator grep — `/contact` is a live 200 with no decorator.

### 6.4 Duplicate slashes are not collapsed — latent, unreachable

`canonical_url("/pulse//marketplace")` → `https://pulsesoc.com/pulse//marketplace`.
`_normalize()` strips query, fragment and trailing slash but does not collapse
runs of `/`. Unreachable in production: Werkzeug's `merge_slashes` 308s such a
URL before any handler runs, so `canonical_url` is never called with one. Left
unfixed and recorded here so nobody "fixes" it without first checking that
`merge_slashes` is still on — and note that this same redirect is the carrier
for §6.1.

### 6.5 No `410` semantics for removed listings

Permanently removed listings answer 404. `410` exists elsewhere in the codebase
but on no search surface. 404 already retires a URL; `410` only retires it
faster. Not worth changing against a live catalogue.

### 6.6 Trailing-slash variants 404 rather than 301

```
/pulse/marketplace/      -> 404
/pulse/marketplace/163/  -> 404
/support/                -> 404
```

No duplicate content is created — which is the SEO-relevant part — but an
inbound trailing-slash link dies instead of landing. A `strict_slashes=False`
or an explicit 301 would recover that link equity. Low value, touches routing,
flagged rather than applied.

---

## 7. What this agent actually added

`scripts/search_os/verify_sitemap_vs_live.py` — read-only production
wire-conformance checker. It is the one capability CI structurally lacks:
`tests/protection/test_sitemap_entries_are_indexable.py` proves the policy
agrees with itself, which does not prove the *deployed* page agrees with the
policy. The template emits its own robots meta, the route may 302 before the
template runs, and a listing can go held between the sitemap being built and the
page being fetched.

Faults it emits: `TRANSPORT`, `STATUS`, `REDIRECTS_TO`, `NO_ROBOTS_DIRECTIVE`,
`NOINDEX`, `CONTRADICTION:meta=…;header=…`, `NO_CANONICAL`, `CANONICAL_MISMATCH`.

Two details that matter:

- Its canonical regex accepts **single-quoted** attributes. `bot.py` writes the
  post page's canonical inside a single-quoted f-string; a double-quote-only
  pattern reports a missing canonical on a page that has one. That mistake has
  already been made against this exact surface.
- It does **not** follow redirects, so a 302 is reported as a fault rather than
  silently resolved into a 200.

```
python3 scripts/search_os/verify_sitemap_vs_live.py \
  --origin https://pulsesoc.com \
  --sitemaps sitemap-products.xml,sitemap-categories.xml,sitemap-posts.xml
```

Exit code is non-zero on any fault. Writes nothing; issues GETs only.

**Read a lone `TRANSPORT:` fault as a flake until you have retried it.** At the
default `--workers 6` a run against production returned
`TRANSPORT:[Errno 54] Connection reset by peer` for one post; that URL then
answered 200 three times in a row, and a full re-run at `--workers 2` reported 0
faults. The fault class is still worth emitting — a reset is a real result and
silently retrying it would hide a genuinely flaky page — but drop concurrency
before believing it. Every other fault class is deterministic.

---

## 8. Downstream contracts

**Agent 0 — integration.** Two items need you: §6.1 (`ProxyFix(x_proto=1)`,
app-wide WSGI, must not set `x_host`) and §6.2 (product-owner decision on 12
legacy pages). Neither is an Agent 2 commit.

**Agent 1 — forensics.** Nothing was published to consume. §5 is independent
production evidence you can use as a baseline; §6.1 is a real wire-level defect
worth confirming from your side.

**Agent 3 — product semantics.** `marketplace_seo.eligibility()` already owns
listing-level refusal, and `feed_eligible` is strictly narrower than
`indexable`. The module rule is **derive or refuse**: `brand`/`gtin`/`mpn` are
omitted rather than faked. `MIN_DESCRIPTION_CHARS = 40` for listings vs
`MIN_INDEXABLE_BODY_CHARS = 180` for posts is deliberate, not drift.

**Agent 4 — SSR.** A server-rendered page must call
`search_visibility.robots_meta(path)` rather than writing a directive literal
(§4.3). §6.1 is yours to care about: routing-level redirects are emitted before
any handler runs.

**Agent 5 — structured data.** `og:url` and `rel=canonical` must be the same
string; `tests/test_marketplace_pagination_canonical.py::test_open_graph_url_never_disagrees_with_the_canonical`
pins it for the marketplace. On paginated marketplace URLs use the storefront's
`canonical_path`, **not** `search_visibility.canonical_url` (§4.2).

**Agent 6 — sitemaps.** `sitemap_eligible(path, record=None)` is the single
gate; it rejects any query string the canonical would not keep. Sitemap
eligibility is *derived* from the directive, never hardcoded — hardcoding it
`False` once silently emptied the product, category and post sitemaps.
`sitemap-live.xml` and `sitemap-replays.xml` are now legitimately empty (§5.8).

**Agent 7 — Merchant feeds.** `PRODUCT_PATH = "/pulse/marketplace/{listing_id}"`
is one path so canonical, sitemap, feed link and shared URL all agree. Do not
introduce a feed-only URL shape. `price_label_contradicts_variants()` is your
safety gate — on 2026-10-01 it caught 4 of 35 feed rows advertising a price
checkout would not charge.

**Agent 8 — Bing/IndexNow.** Submit only `sitemap_eligible()` URLs. Submitting a
`noindex` URL is a self-contradiction and burns quota; §5.2 is the proof the two
sets are currently identical.

**Agent 9 — media.** `/static/` and `/.well-known/` are `noindex,nofollow` but
explicitly **crawlable** via `_CRAWLABLE_DESPITE_NOINDEX` — a `Disallow` would
prevent the `noindex` from ever being read. Image URLs in structured data must
resolve on the canonical origin.

**Agent 10 — social graph.** `/pulse/u/*`, reels and stores are
`noindex,follow` behind `pulse_social_shell` (`bot.py:50820`). Making any of
them public means adding a carve-out **above** the broad `/pulse` rule (§3.1),
coordinated through Agent 0 — not relaxing the handler.

**Agent 11 — Search Admin.** `/admin/*` is `noindex,nofollow` and
`Disallow`-ed. Reason codes are prose strings on `Decision.reason`, not an enum;
if you need stable codes for a UI, add them beside the prose rather than
replacing it, since the prose is what the 35 existing tests assert.

**Agent 12 — adversarial tests.** Highest-value missing test is §6.3 (walk
`url_map`, assert rendered robots == `robots_meta(path)`). Three traps:
`robots_blocked()` deliberately does not reuse `classify()` so a disagreement
stays detectable — do not refactor them together; single-quoted HTML attributes
defeat double-quote-only regexes (§7); any new test file must be registered in
`config/ci_test_manifest.json`, which is default-deny.

---

## 9. Definition of Done

Satisfied by **pre-existing** code on `origin/main`, verified in production by
this agent: URL taxonomy, product URL identity, canonical registry,
normalisation, tracking-parameter collapse, pagination rules, facet handling,
internal-search exclusion, category/product indexability, out-of-stock
handling, 404 semantics, variant handling, Universal Link coexistence, checkout
exclusion, Merchant/sitemap/structured-data/social agreement, robots meta and
`X-Robots-Tag`, robots.txt generation, crawl-budget protection, canonical
injection resistance, and the test matrix (144 tests + 3 mutation harnesses).

Added by this agent: production wire-conformance verification (§7), the
two-canonical-authority contract (§4.2), the case-preservation rationale (§4.4),
and this document.

**Genuinely open:** §6.1 (needs Agent 0), §6.2 (needs a product owner), §6.3
(Agent 12), §6.5 and §6.6 (judged not worth the risk). §6.4 is closed as
unreachable.

Agent 2 is **not** done because "canonical tags exist" — it is done because the
sitemap and the indexable set were proven byte-for-byte identical on the wire,
and because the four things that are still wrong are named, attributed to an
owner, and deliberately not fixed by the wrong hands.
