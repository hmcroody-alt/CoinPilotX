# Agent 1 — Fleet Dependency Report (Deliverable 5)

**Status:** PUBLISHED EARLY, by the brief's cross-agent communication rule.
**Measured:** 2026-10-03, anonymous Googlebot UA, production `pulsesoc.com`.
**Evidence basis:** deployed SHA `5bdf4e431d1fd9164706962d7610d287a1f3092b` is
identical to this worktree's base, so repository reads correspond 1:1 to served
bytes. Every claim below is from a live HTTP response unless marked otherwise.

**AGENT 1 STATUS: COMPLETE / PUSHED / STANDBY.** Agent 1 owns *production search reality
and baseline evidence*; it is **not** the authority on future search architecture.
Agent 0 owns integration — this branch is pushed, not merged, not deployed.

**Read `BASELINE_AND_HANDOFF.md` alongside this file.** It carries the constraints on how
these findings may be used: the baseline is a snapshot and not a Search OS constant (§1),
the measurement methodology and the four harness bugs that produced false findings (§2),
the correctly-stated variant finding (§3), the Merchant price-safety invariant (§5), the
legacy-surface escalation rules (§6), the Agent 11/12 handoffs (§8–§9), the three
UNKNOWNs that stay unknown (§10), and the Agent 2 reconciliation protocol (§11).

---

## 0. TO AGENT 0 — COORDINATION MECHANISM IS ABSENT

**There is no Agent 0 coordination artifact in this repository.** I searched
branches, worktrees (~47 pre-existing), open PRs (5), and the docs tree. No fleet
manifest, no ownership registry, no shared status file.

Per the brief ("DO NOT INVENT A NEW COORDINATION SYSTEM IF AGENT 0 ALREADY CREATED
ONE… If no mechanism exists, notify Agent 0 rather than independently designing a
fragile shared-file workflow"), **I have not created one.** This file is my own
branch's artifact only. Agent 0 owns the decision.

Registered for the record:
- Branch: `search-os/agent-01-forensics`
- Worktree: `/Users/hmcherie/Desktop/cpx-searchforensics` (durable, not `/tmp`)
- Base: `origin/main` @ `5bdf4e431`
- Contents: Agent 1 artifacts only. No source file modified.

**Adjacent active work Agent 0 should deconflict:** PR #152 `claude/robots-literals`
is open and touches `services/search_visibility.py` — the module Agent 2 (canonical)
and Agent 6 (sitemaps) will both need. See §2 below; it is confirmed unmerged.

---

## 1. NO STOP CONDITION TRIGGERED

I checked every stop condition the brief lists. **None fired.** Stated explicitly so
no agent waits on an escalation that is not coming:

| Stop condition | Verdict | Evidence |
|---|---|---|
| Private user data indexed | NOT TRIGGERED | Social entity routes 302 anonymous → `/login` |
| Private Signals/Reels crawlable | NOT TRIGGERED | `/pulse/reels/<id>` 302 → `/login` |
| Order/customer data exposed | NOT TRIGGERED | No order/customer surface returned 200 anonymously |
| Auth bypass | NOT TRIGGERED | — |
| Production credentials exposed | NOT TRIGGERED | No secret value read, printed or stored |
| **Widespread price disagreement** | **NOT TRIGGERED** | 0 mismatches across 36 feed items, 44 grid cards, 41 PDPs — see §3 |
| Held catalog purchasable via search | NOT TRIGGERED | `public_sql` gates both grid and PDP |
| Exact-variant integrity violation | NOT TRIGGERED | but variants have no search identity — see §4 |

No PII, no cookies, no tokens, no secret values appear in any Agent 1 artifact.

---

## 2. TO AGENT 2 (CANONICAL ARCHITECTURE) — three items

**Reconciliation first.** Agent 1 owns *what the internet actually showed during recon*;
Agent 2 owns *URL/canonical/indexability contract verification*. Those are different
questions and can disagree without either being wrong. Before either of us calls the
other's result a defect, compare timestamp, deployed SHA, URL population, eligibility
definition (`indexable` and `feed_eligible` are two verdicts, the second strictly
narrower), and probe method. Protocol in `BASELINE_AND_HANDOFF.md` §11. Verify the
contract — do not re-run my sweep and overwrite its evidence.

**2a. Canonical health is already good. Do not rebuild it.**
All 148 sitemap URLs: **148/148 HTTP 200**, **147/148 self-canonical**, zero
cross-canonicals, zero `noindex`. The single exception is
`https://pulsesoc.com/arena-preview` — 200, `index,follow`, **no `rel=canonical`
at all**. That is the entire canonical defect surface of the submitted corpus.
→ `FINDING A1-07`, P3, HIGH confidence.

**2b. The policy table and the renderer disagree on `/pulse/cart`.**
`services/search_visibility.py` declares `noindex,follow` (which is why `/pulse/cart`
is absent from the generated `robots.txt`). Production serves **`noindex,nofollow`**.
PR #152 claims to fix this and is therefore confirmed unmerged against prod.
→ `FINDING A1-02`, P2, HIGH confidence. Owner: Agent 2. Blocks: Agent 6.

**2c. Trailing-slash variants 404 instead of redirecting.**
`/pulse/marketplace/` → 404. `/pulse/marketplace/163/` → 404. Zero redirects.
No duplicate-content risk (which is good), but any external link, pasted URL or
citation carrying a trailing slash is a hard dead end rather than a 301.
→ `FINDING A1-03`, P2, HIGH confidence.

**Correction to a likely fleet assumption:** the parameter space is **not** a crawl
trap. Only four params are honoured (`page`, `category`, `q`, `sort`). Measured:
`?page=2` → self-canonical (correct); `?page=3`/`?page=99` → clamp and canonicalise
to `?page=2`; `?sort=`, `?q=`, `?utm_*`, and unknown `?category=` values all
canonicalise to the bare grid, and unknown categories additionally serve
`noindex,follow`. `?utm_source=` returns byte-identical HTML. Nobody needs to build
parameter handling. → `FINDING A1-04`, INFORMATIONAL, HIGH confidence.

---

## 3. TO AGENT 7 (MERCHANT INFRASTRUCTURE) — **DO NOT BUILD A FEED. ONE IS LIVE.**

`/feeds/merchant-center.xml` returns **HTTP 200**, RSS 2.0 with the `g:` namespace,
**36 items**, `x-robots-tag: noindex,follow`, `cache-control: public, max-age=300`.
Built by `services/merchant_center_feed.py`.

**The price-misrepresentation exposure is CLOSED in production as of 2026-10-03.**
This is the single most important thing Agent 7 needs to know, and it is load-bearing:

There are two price authorities — `price_label` (a display string typed at publish
time, which the feed publishes) and `marketplace_listing_variants.price_cents` (which
the PDP renders and **which checkout actually charges**).
`marketplace_seo.price_label_contradicts_variants()` exists only to detect their
disagreement; its docstring records that on **2026-10-01 four of the then-35 feed
rows disagreed** — listing 36 advertised $38.00 against a $2.29 variant.

My measurement today: the 5 sitemap products absent from the feed
(**112, 15, 89, 35, 36**) are **exactly** the price-disagreeing rows. Across all 36
overlapping items: **0 feed-vs-PDP price mismatches** and **0 grid-vs-PDP mismatches**
(20 comparable cards). The 41→36 gap is the safety mechanism working, not a bug.

> **Do not "fix" the feed/sitemap count mismatch by loosening `feed_eligible`.**
> That re-opens a Merchant Center suspension risk. The guard also **fails open** by
> design: a caller that does not join variants gets `False`. Any new feed caller that
> skips the variant join silently re-opens the exposure.

**Real Merchant gaps (all confirmed present/absent from the live feed):**

| Field | State | Consequence |
|---|---|---|
| `g:id`, `title`, `description`, `link`, `g:image_link`, `g:availability`, `g:price`, `g:condition`, `g:identifier_exists` | PRESENT | — |
| `g:item_group_id` | **ABSENT** | All variants invisible to Shopping — see §4 |
| `g:brand` | ABSENT | No backing column |
| `g:gtin` / `g:mpn` | ABSENT | Declared via `g:identifier_exists = no` (honest) |
| `g:google_product_category`, `g:product_type` | ABSENT | Google must guess taxonomy |
| `g:color`, `g:size`, `g:age_group`, `g:gender` | **ABSENT** | **Google requires these for apparel.** Much of this catalogue is apparel (womens-clothing 14, mens-clothing 9) |
| `g:shipping`, `g:sale_price`, `g:mobile_link` | ABSENT | — |

`g:condition` is hardcoded `new`.
→ `FINDING A1-08`, P2, HIGH confidence. Owner: Agent 7.

**ID-space disagreement between two Agent-owned surfaces:** the feed's `g:id` is the
bare listing id (`163`); the PDP's JSON-LD `sku` is `pulsesoc-listing-163`. Nothing
reconciles them. Agents 5 and 7 must agree a single product identity before either
ships. → `FINDING A1-09`, P2, HIGH confidence. Owners: Agent 5 + Agent 7.

**Image host disagreement:** feed `g:image_link` uses `oss-cf.cjdropshipping.com`;
the PDP `<img src>` uses `cf.cjdropshipping.com`. Two different supplier CDN hosts for
the same product. → see §5.

---

## 4. TO AGENT 3 (PRODUCT GRAPH) + AGENT 5 (SCHEMA) + AGENT 7 — VARIANTS HAVE **ZERO** SEARCH IDENTITY

This is the largest structural finding and it spans three agents, so it is filed once.

**31 of 44 public listings are multi-variant.** Variant counts run to 95
(distribution: 10 single-variant, then 4,5,6,7,8,9,10,18,20,26,30,36,40,42,48,56,63,95).

Variants **are** server-rendered and **are** URL-addressable — but via
`?opt_option1=Black&opt_option2=XL`, with an `opt_` prefix on the form control names.
The server echoes `checked` on the matching radios. *(Measurement warning: the
unprefixed `?option1=…&option2=…` form returns byte-identical HTML to the bare PDP —
testing with it fakes "no variant URL support". A grep for `name="option1"` likewise
returns zero and fakes "client-rendered". Both conclusions are wrong.)*

Despite existing, variants are invisible to search in **all three** channels:

1. **Not crawlable.** Zero `<a href>` on any PDP contains `opt_option`. The selector
   is a `<form method="get">` with radio inputs, and a crawler never submits a form.
   So all 8 variants of listing 113 — and all 95 of listing 36 — are unreachable.
2. **Not in structured data.** No `ProductGroup`, no `hasVariant`, no
   `variesBy`. Single-price listings emit `Offer`; multi-price listings emit
   `AggregateOffer` with `lowPrice`/`highPrice`.
3. **Not in the feed.** No `g:item_group_id`, no `g:color`/`g:size`.

→ `FINDING A1-05`, **P1**, HIGH confidence. Owner: Agent 3. Dependents: 5, 7, 2.

**The finding, stated correctly — and the conclusion it does NOT license:**

> **VARIANTS EXIST AND ARE ADDRESSABLE, BUT THEIR SEARCH IDENTITY, GROUPING AND
> DISCOVERY MODEL IS INCOMPLETE.**

That is the entire finding. It does **not** say "therefore every supplier variant should
become an independently indexed URL." Agent 1 has not evaluated thin-content risk,
crawl-budget cost, duplicate handling, or whether a variant is even the right search
entity — minting ~500+ indexable URLs could as easily be a crawl-budget catastrophe as
a coverage win. **That decision belongs to the fleet:** Agent 3 defines the entity model,
Agent 0 approves scope. Agent 5 acts only after Agent 3 freezes identity, and must not
manufacture variant URLs to win rich results.

Full handoff — prevalence distribution, `opt_option` mechanics, large-variant worked
examples (113 / 112 / 36), and the three-way identifier disagreement — in
`BASELINE_AND_HANDOFF.md` §3–§4.

**Credit where due — the honest part.** Agent 5 should know the existing JSON-LD is
already correct and should not be rewritten wholesale:
- `Offer` → `AggregateOffer` switches correctly on real price spread, and the
  `lowPrice`/`highPrice` match the observed variant prices exactly (listing 112:
  `27.84`–`37.72` against variants `$27.84/$29.31/$31.69/$37.72`).
- Product `@graph` carries Organization, WebSite, WebPage, Product, Offer,
  BreadcrumbList.
- `brand`, `gtin`, `mpn`, `aggregateRating`, `review` are **deliberately omitted**
  with documented reasons (no backing column, no review table). **No fabricated
  values were found anywhere.** Do not "complete" the schema by inventing them.

**Entity confusion, for whoever owns brand identity:** JSON-LD
`Organization.legalName` = **`"CoinPlotXAI Inc."`**, and ~60 of the 87
`sitemap-pages.xml` URLs describe the retired crypto/sports product
(`/whale-tracker`, `/sports-edge/*`, `/telegram-crypto-bot`, `/learn/crypto-scams`…).
Google is currently being told this is a crypto company.
→ `FINDING A1-10`, P2, HIGH confidence. Owner: Agent 0 to assign.

**ESCALATE, DO NOT DELETE.** A **legal entity name is not a consumer brand** —
`CoinPlotXAI Inc.` may be the legally correct `legalName` for the company behind a
product branded PulseSoc, and `Organization.legalName` is read by payment processors and
merchant review, not only by search. **Do not automatically remove it.** `name` and
`legalName` are different fields and may legitimately differ. No mass redirect, no mass
`noindex`, no deletion merely because a page is old: some of those ~60 legacy URLs may be
the only pages ranking for anything. Agent 5 determines truthful Organization schema;
Agent 11 gathers traffic/indexation evidence first; **Agent 0 decides.** Constraints in
full: `BASELINE_AND_HANDOFF.md` §6.

---

## 5. TO AGENT 9 (MEDIA PIPELINE) — three items

**5a. Every product image is third-party supplier-hosted.** Hosts:
`cf.cjdropshipping.com` and `oss-cf.cjdropshipping.com`. Crawlability tested
directly: both return **200 / `image/jpeg`** to a `Googlebot-Image/1.0` UA, byte sizes
identical to a browser UA (no hotlink protection), and both hosts serve
`robots.txt` = `User-agent: *` / `Allow: /`.

So images are crawlable **today**. The risk is not blocking — it is that PulseSoc's
entire Google Images and Shopping image surface sits on a domain it does not control,
cannot set cache headers on, and gets no attribution from.
→ `FINDING A1-11`, P2, HIGH confidence.

**5b. PDPs ship exactly one image.** Measured across all 41 indexable PDPs: distinct
`<img src>` count per page is **1 to 3**, and the dominant case is **1** — on listings
with up to 8 variants and multi-image supplier galleries. The gallery is not in the
initial HTML. `srcset` is deliberately absent (documented decision in
`marketplace_storefront.py`); `loading=lazy/eager` is set.
→ `FINDING A1-12`, P2, HIGH confidence.

**5c. `VideoObject` and `ImageObject` are entirely absent** from the site's JSON-LD.
Present `@type`s: Product, Offer, AggregateOffer, Organization, WebSite, WebPage,
AboutPage, ContactPage, MobileApplication, BreadcrumbList, FAQPage, SearchAction,
Question, Answer, ListItem, Service, ItemList. No video markup anywhere — so Reels
have no video search surface even where the page is public.
→ `FINDING A1-13`, P2, HIGH confidence. Owner: Agent 9. Dependent: Agent 10.

---

## 6. TO AGENT 6 (SITEMAPS) — the baseline, and two real defects

**Measured baseline, 2026-10-03** (supersedes `docs/seo/00_baseline_and_gap_list.md`,
which measured **354** URLs on 2026-09-18 @ `469eea07` and is now stale):

| Child sitemap | URLs |
|---|---|
| `sitemap-pages.xml` | 87 |
| `sitemap-posts.xml` | 15 |
| `sitemap-categories.xml` | 4 |
| `sitemap-products.xml` | 42 (41 PDPs + the grid) |
| `sitemap-live.xml` | **0** |
| `sitemap-replays.xml` | **0** |
| **Total unique** | **148** |

Zero cross-sitemap duplicates (`uniq -d` → empty). All 148 return 200.

**Both prior P0s are CLOSED — do not spend time on them:**
- *"A1. 16 sitemap URLs return HTTP 500"* → **CLOSED.** All 15 `/pulse/post/*` URLs
  return 200, `index,follow`, self-canonical (13.5–15.3 kB). Zero 500s in the corpus.
- *"A2. `noindex` URLs are in the sitemap (`/signup`)"* → **CLOSED.** Zero `noindex`
  in the corpus; `/signup` is not present at all.

**6a. Two permanently-empty children are still advertised.**
`sitemap-live.xml` and `sitemap-replays.xml` serve empty `<urlset>` bodies (109 bytes)
yet remain listed in `SITEMAP_CHILDREN` and the sitemap index. Cause: their paths are
hardcoded `/arena/*`, which `search_visibility` now classifies `NOINDEX_FOLLOW`, so
the `sitemap_eligible()` gate filters every candidate. They cannot ever be non-empty
in the current policy. Search Console will report two empty submitted sitemaps forever.
→ `FINDING A1-06`, P2, HIGH confidence.

**6b. `sitemap-pages.xml` is ~60/87 retired crypto product.** See §4 entity
confusion. This is the largest child sitemap and most of it advertises a dead product.
→ `FINDING A1-10`.

**Do NOT file the 4-of-12 category count as a bug — I checked and it is correct.**
Only 4 of the 12 live departments are in `sitemap-categories.xml`, which looks wrong
and is not. `CATEGORY_MIN_INDEXABLE_LISTINGS = 3`, counted over *indexable* listings
rather than public ones, explains every exclusion. All 12 departments are 200,
`index,follow`, self-canonical, non-empty, and crawlable from the grid's category nav,
so none is orphaned. *(I initially measured this as a divergence; that was my own
lexical-`sort` error. The policy is working as documented.)*

**Thin-content measurement, for Agent 6's coverage expectations.** Per-department
public vs indexable (measured live; a listing sits in one department):

| Department | Public | Indexable | In sitemap |
|---|---|---|---|
| womens-clothing | 14 | 11 | YES |
| mens-clothing | 9 | 7 | YES |
| health-beauty-hair | 3 | 2 | YES |
| home-garden-furniture | 3 | 1 | YES |
| jewelry-watches | 5 | 2 | no |
| bags-shoes | 2 | 2 | no |
| phones-accessories | 2 | 1 | no |
| sports-outdoors | 2 | 1 | no |
| consumer-electronics | 1 | 0 | no |
| automobiles-motorcycles | 1 | 1 | no |
| home-improvement | 1 | 1 | no |
| toys-kids-baby | 1 | 0 | no |

Note the drift: these per-department numbers moved between two runs minutes apart
(womens-clothing 15→14, mens-clothing 10→9) while individual pages were
**byte-identical across three consecutive fetches**. The pages are deterministic; the
*catalogue membership* is time-varying as supplier sync flips stock. Two departments
currently in the sitemap have dropped below the threshold since generation — expect
`sitemap-categories.xml` membership to oscillate. → `FINDING A1-14`, P3, MEDIUM
confidence (cause inferred from drift pattern, not from reading the sync worker).

---

## 7. TO AGENT 8 (BING / INDEXNOW) — the wiring exists, the submission does not

| Component | State |
|---|---|
| IndexNow key file route | **PRESENT** (`bot.py:32923-32926`) |
| `/api/indexnow` | **PRESENT but metadata-only** — its own source says "This endpoint does not submit it" |
| IndexNow submission logic | **ABSENT** |
| `BING_SITE_VERIFICATION` meta | PRESENT |
| `BingSiteAuth.xml` | ABSENT |
| Bing URL Submission API | ABSENT |

**There is zero push-side automation of any kind.** The Procfile runs `undx`, `email`,
`ads`, `alert`, `media` and `supplier` workers — **no SEO, sitemap, feed or IndexNow
worker exists**. Every search surface is pull-only: Google and Bing discover changes
only when they choose to re-crawl. For a catalogue whose membership measurably drifts
within minutes (§6), that is the structural gap.
→ `FINDING A1-15`, P2, HIGH confidence. Owner: Agent 8.

Provider inventory (reported as state only — **no secret value was read or stored**):
`GOOGLE_SITE_VERIFICATION` PRESENT (meta); `GA_MEASUREMENT_ID` PRESENT; 8 Google Ads
conversion labels PRESENT; GTM ABSENT. GSC property is `sc-domain:pulsesoc.com`.

---

## 8. TO AGENT 10 (SOCIAL GRAPH) — the social catalogue is behind the login wall

- `/pulse/reels/<id>` → **302 → `/login`** for anonymous Googlebot.
- Profiles, stores and spaces likewise 302.
- `/pulse/post/<id>` **is** public: all 15 in the sitemap return 200, `index,follow`,
  self-canonical, with OG tags — but **no JSON-LD**, and no `VideoObject` exists
  anywhere on the site (§5c).
- The 15 indexable posts are **PulseDrop promos whose titles duplicate product titles
  verbatim** — i.e. the only indexable social content competes with the PDPs it
  promotes. → `FINDING A1-16`, P2, HIGH confidence. Owners: Agent 10 + Agent 2.

**Critical implementation note:** `pulse_social_shell` (`bot.py:50820`) **is itself
the login wall** — it calls `require_account()`. Relaxing an individual handler's own
guard is a **no-op**. Any plan to open a social surface to crawlers must address the
shell, not the handler.

---

## 9. TO AGENT 4 (SSR) — SSR is already real. Verify before building.

Everything search-relevant on the grid, category pages and PDPs is in the **initial
HTML**: title, price, currency, availability, description, seller, category,
breadcrumbs, variant data, pagination anchors. Measured by raw `curl` with no
JavaScript execution. Pagination uses real `<a href>` with `rel="next"`.

There is no evidence of a client-rendered search-critical field on the marketplace
surfaces. Agent 4 should confirm against §4's measurement warning before concluding
anything is client-rendered — that is exactly the trap I fell into and corrected.

---

## 10. TO AGENT 12 (CI PROTECTIONS) — a gate already enforces the invariant

`tests/protection/test_sitemap_entries_are_indexable.py` (~685 lines) already asserts
end-to-end that every sitemap `<loc>` is 200, not `noindex`, self-canonical, and
crawlable under the generated `robots.txt`. It carries a named exemption for
`/pulse/marketplace` (soft-404-when-empty) and a regression guard that
`Disallow: /portfolio` must not block `/portfolio-ai`.

`robots.txt` is **generated** from `search_visibility`, not static — three forms are
emitted per prefix. Any agent hand-editing `robots.txt` is editing a build artifact.

**Measurement trap every agent in this fleet will hit.** Canonical and robots tags are
written with **single quotes** inside Python f-strings. A regex for `rel="canonical"`
therefore reports mass absence. `docs/seo/00_baseline_and_gap_list.md` records a prior
pass that "reported 189 pages missing rel=canonical… That was false. The corrected
counts are 1 and 6." **All Agent 1 probes are quote-agnostic** (`['\"]`). Reuse that
or re-derive the false finding.

**TEST THE TEST HARNESS.** That quote trap is one of **five** measurement bugs this
mission hit; four were mine, and three produced confident, plausible, wrong findings —
including a `comm`-over-lexically-sorted-ids bug that manufactured a **"DIVERGENCE"**
verdict against four departments that does not exist, and a `grep -c` bug that
manufactured a 5-item Merchant feed regression that did not happen. **A broken
measurement script does not look like a bug; it looks like a finding.** All five are
specified with reproductions in `BASELINE_AND_HANDOFF.md` §2, and §8 of that document
names the ten regression/mutation coverage classes Agent 12 owns — including
measurement-harness false positives and fake IndexNow success. Assert **invariants, never
the integers**: a test pinned to `== 148` fails on the next supplier sync and teaches the
fleet to ignore red CI.

---

## 11. WHAT NO AGENT SHOULD REBUILD

Confirmed live and working. Rebuilding any of these is wasted fleet capacity:

| Surface | State |
|---|---|
| Merchant Center feed | LIVE, 36 items, price-guarded |
| Product JSON-LD `@graph` | LIVE, valid, honest, `AggregateOffer`-aware |
| Sitemap index + 6 children | LIVE, 148 URLs, 148/148 × 200 |
| `robots.txt` generation from policy | LIVE |
| Canonical emission + page clamping | LIVE, 147/148 self-canonical |
| Parameter containment | LIVE, 4 params, no trap |
| Breadcrumbs (markup + JSON-LD) | LIVE |
| Thin-content `noindex` gating | LIVE (`MIN_DESCRIPTION_CHARS = 40`) |
| Category department pages | LIVE, 12, all indexable |
| Sitemap-indexability CI gate | LIVE |
| `services/app_links.py`, `app_promotion.py`, `/api/track`, `seo/content.py`, `seo/schema.py`, `seo_engine.py` | incumbents — documented do-not-rebuild |

`services/seo_service.py` is an **orphan** (30 lines, zero callers). Safe to ignore.

---

## 12. OPEN — MEASURED BUT UNRESOLVED

Recorded as UNKNOWN rather than guessed, per the brief.

1. **Catalogue drift cause.** Public membership moves within minutes while pages stay
   byte-identical. Supplier sync is the likely writer, but I did not read the worker
   to confirm. MEDIUM confidence.
2. **Why 35 and 36 are feed-excluded.** Both have a *single* variant price ($14.33,
   $2.29) so price *spread* does not explain it. The code path
   (`price_label` vs variant disagreement) does explain it and listing 36 is a
   documented case — but I did not read their `price_label` values from the database,
   so the per-row cause is inferred, not measured.
3. **Phases not executed:** 15 (authorized GSC read), 18 (performance baseline —
   TTFB/LCP/CLS/INP), 25 (competitive research). No data; no claims made.

---

## 13. SEVERITY ROLL-UP

| ID | P | Confidence | Headline | Owner |
|---|---|---|---|---|
| A1-05 | **P1** | HIGH | Variants have zero search identity in all 3 channels | Agent 3 |
| A1-02 | P2 | HIGH | Policy table vs renderer disagree on `/pulse/cart` | Agent 2 |
| A1-03 | P2 | HIGH | Trailing-slash URLs 404 instead of 301 | Agent 2 |
| A1-06 | P2 | HIGH | Two permanently-empty sitemap children advertised | Agent 6 |
| A1-08 | P2 | HIGH | Merchant feed missing apparel-required + grouping fields | Agent 7 |
| A1-09 | P2 | HIGH | Product ID space disagrees between feed and JSON-LD | Agent 5 + 7 |
| A1-10 | P2 | HIGH | Site identity is still the retired crypto product | Agent 0 |
| A1-11 | P2 | HIGH | All product images third-party supplier-hosted | Agent 9 |
| A1-12 | P2 | HIGH | PDPs ship 1 image despite multi-image galleries | Agent 9 |
| A1-13 | P2 | HIGH | No `VideoObject` / `ImageObject` anywhere | Agent 9 |
| A1-15 | P2 | HIGH | Zero push-side automation; IndexNow wired but inert | Agent 8 |
| A1-16 | P2 | HIGH | Only indexable social content duplicates product titles | Agent 10 |
| A1-07 | P3 | HIGH | `/arena-preview` has no canonical | Agent 2 |
| A1-14 | P3 | MEDIUM | Category sitemap membership will oscillate | Agent 6 |
| A1-04 | INFO | HIGH | Parameter space is contained — no trap to fix | Agent 2 |

**Agent 1 has proposed no implementation and modified no source file.** Per the
brief, findings go to owning agents; Agent 0 decides sequencing.
