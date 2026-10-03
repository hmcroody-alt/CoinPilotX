# Agent 11 — Search Intelligence, Exposure Ledger, Admin Search OS

Measured 2026-10-03 against production at deployed SHA `5bdf4e431`
(`GET /api/service/health`), which is also this branch's merge base — so every
number below describes the same bytes as the code in this checkout.

This document answers one question: **what can PulseSoc actually prove about its
own search exposure, and where does it only believe things?**

---

## 1. The headline

**There is no inbound provider evidence of any kind.** Not degraded, not stale —
absent. Verified by search across the repository:

| Evidence source | Status | Verified by |
| --- | --- | --- |
| Google Search Console API | **NO CLIENT** | no `searchconsole` / `search_console` / `webmasters` reference in any `.py`, `.json` or `.yml` |
| Merchant Center Content API / `productstatuses` | **NO INGEST** | no `content.googleapis` / `productstatuses` reference |
| Bing Webmaster Tools API | **NO CLIENT** | `bing` appears only as a `BING_SITE_VERIFICATION` meta tag value and as a referrer `LIKE` pattern |
| IndexNow submission | **NOT SUBMITTED** | `/api/indexnow` (`bot.py:32929`) returns the payload and says so in its own docstring: it does not submit it |

The consequence is the single most important fact in this document, and it is a
fact about us, not about Google:

> Every provider-observed lifecycle stage — crawled, provider-observed,
> impressing, clicked, ranked — is `NO_EVIDENCE` for every URL PulseSoc
> publishes. Not `NOT_INDEXED`. We have never asked, so we do not know.

Anything that renders a crawl or index state today is rendering an inference. No
amount of dashboard work changes this; only an API client with credentials does,
and that is a decision with a cost, not a bug to fix.

## 2. Search Observability Reality Map

The lifecycle, and what we can observe at each stage **today**:

| Stage | Can we observe it? | Source | Confidence |
| --- | --- | --- | --- |
| DISCOVERED (row exists) | YES | `marketplace_listings` | OBSERVED |
| ELIGIBLE (policy says indexable) | YES | `services/search_visibility.classify` | DERIVED from policy |
| FEED_ELIGIBLE | YES | `marketplace_seo.eligibility` | DERIVED from policy |
| DISTRIBUTED (in a sitemap we serve) | YES | `GET /sitemap-*.xml` | OBSERVED off the wire |
| SELF_CONSISTENT (page agrees with itself + row) | **YES — new, this branch** | `services/search_truth.py` | OBSERVED off the wire |
| SUBMITTED to a provider | NO | — | `NO_EVIDENCE` |
| CRAWLED | NO | — | `NO_EVIDENCE` |
| PROVIDER-OBSERVED / INDEXED | NO | — | `NO_EVIDENCE` |
| IMPRESSING / CLICKED / RANKED | NO | — | `NO_EVIDENCE` |
| ORGANIC SESSION | **PARTIAL, AND CONTAMINATED** | `analytics_events.referrer` | see §6.2 — cannot exclude paid |
| PRODUCT VIEW → CART → CHECKOUT | YES (not search-attributable) | `analytics_events` | OBSERVED, no join to a search origin |
| PAID ORDER | YES (not search-attributable) | orders tables | OBSERVED |

Read the gap in the middle of that table literally. We can prove what we
*publish*, and we can prove what *happens on site*. We can prove nothing about
the part in between where a search engine is involved. **Therefore no
search-attributed revenue claim is currently supportable**, and any artifact
asserting one is asserting a hypothesis.

## 3. Production baseline, measured today

```
sitemap.xml                  200, 6 child sitemaps
  sitemap-pages.xml          200   87 URLs
  sitemap-posts.xml          200   15 URLs
  sitemap-categories.xml     200    4 URLs
  sitemap-products.xml       200   42 URLs
  sitemap-live.xml           200    0 URLs
  sitemap-replays.xml        200    0 URLs
  TOTAL SUBMITTED            148 URLs
```

Search Truth Sentinel over the product sitemap:

```
origin           https://pulsesoc.com
sitemap products 41
pages compared   41
pages faulted    0

not checked (UNKNOWN, not clean):
  -- every comparison was possible on every page
```

**41 and not 42 is correct and explained:** the 42nd `<loc>` is the storefront
index `/pulse/marketplace`, which has no listing id and no per-listing claims to
compare. It is excluded deliberately, not dropped silently.

Read "0 faults" as exactly what it says: *no contradiction was found among the
surfaces that could be read*, and on these 41 pages every surface could be read.
It is not "verified in Google".

`sitemap-live.xml` and `sitemap-replays.xml` return 200 with zero URLs. An empty
sitemap is a finding and an unreadable one is an unknown; these are empty. See
§6.4 — it belongs to Agent 9, not to me.

## 4. What this branch adds: the Search Truth Sentinel

### 4.1 The gap it closes

A single live product page states its price in **seven** places, each produced by
a different code path:

`<meta name="description">` · `og:description` · `twitter:description` ·
JSON-LD `WebPage.description` prose · JSON-LD `Offer.price` (or
`AggregateOffer.lowPrice`/`highPrice`) · the `data-mkt-variants` bootstrap
attribute · the visible `data-mkt-price` pill

Add the two database authorities and the Merchant feed and there are **ten**
claims about one number. Before this branch, nothing in the repository compared
any of them to any other.

The drift is silent by construction: the page renders, the feed validates, the
unit tests pass. The only readers who notice are a buyer at checkout and a
Merchant Center reviewer filing a misrepresentation finding.

### 4.2 Why it is located at the wire

This is the part that makes it evidence rather than ceremony.

A protection test that imports the policy module and compares its output to
itself proves **the policy agrees with itself**. It cannot prove the *deployed
page* agrees with the policy. Two surfaces derived from the same function
through the same parser cannot verify each other — that is a tautology wearing a
green checkmark.

So the sentinel reads deployed HTML over HTTP with a Googlebot UA and compares
it against policy and against the row. Three consequences, all deliberate:

* **The Merchant feed price is recorded as evidence, never checked.** `feed_row`
  and the page's structured data both read `price_label` through the same
  `parse_price`; comparing them would compare a number to itself.
* **Two fault codes were written and then removed before shipping**, because
  they could never fire independently: `FEED_PRICE_VS_WIRE` (the tautology
  above) and `CURRENCY_MISMATCH` (currency is part of the comparison key, so a
  currency divergence trips `PRICE_WIRE_VS_DB` first). A code that cannot fire
  is worse than no code — it reads as coverage.
* **It runs without a database by default.** `import bot` opens a connection and
  executes `init_db()` at module scope, so a sentinel needing the ORM could not
  safely be pointed at production at all. `--with-db` adds row-level
  comparisons and is for local/CI use only.

### 4.3 UNKNOWN is structural, not a convention

`Verdict.skipped` maps each comparison that could **not** be made to the reason.
The runner prints that tally unconditionally, next to the fault count, never
instead of it:

```
not checked (UNKNOWN, not clean):
  2  PAGE_FETCH
```

This earned its place during development. The first production run reported six
"unknowns" which were one transient fetch failure — correctly surfaced as *we
did not look*, where a lesser design would have reported six faults and sent
someone to debug a healthy page.

### 4.4 Running it

```bash
# wire-only, safe against production, no database
python3 scripts/search_os/search_truth_sentinel.py --origin https://pulsesoc.com

# one listing, full evidence with lineage
python3 scripts/search_os/search_truth_sentinel.py --listing 163 --json

# local/CI only: adds row-level comparisons, imports bot and runs init_db()
python3 scripts/search_os/search_truth_sentinel.py --with-db --origin http://127.0.0.1:5000
```

Read-only: GETs only, writes nothing, sends no PII. `--fail-on` defaults to `P0`.

### 4.5 Fault codes

| Code | Sev | Means |
| --- | --- | --- |
| `PRICE_SURFACES_DISAGREE` | P0 | one page states two different prices in two of its own surfaces |
| `PRICE_PROSE_STATES_TWO_AMOUNTS` | P0 | one sentence states two different prices |
| `PRICE_WIRE_VS_DB` | P0 | the page's price is not the row's price |
| `DB_PRICE_AUTHORITIES_DISAGREE` | P0 | `price_label` is not what the variants charge |
| `CANONICAL_MISMATCH` | P0 | the page declares a canonical that policy does not |
| `ROBOTS_CONTRADICTS_POLICY` | P0 | meta robots contradicts `search_visibility` |
| `PRIVATE_IN_SITEMAP` | P0 | a non-indexable listing is submitted for indexing |
| `PRICE_ABSENT_ON_WIRE` | P1 | the page makes no price claim at all |
| `AVAILABILITY_SURFACES_DISAGREE` | P1 | the page states two stock statuses |
| `AVAILABILITY_WIRE_VS_DB` | P1 | the page's stock status is not the row's |
| `CANONICAL_ABSENT` | P1 | no `link rel=canonical` |
| `IDENTITY_MISMATCH` | P1 | `Product.sku` names a different listing |
| `FEED_ROW_REFUSED` | P1 | `feed_row` reported an internal contradiction |
| `ROBOTS_ABSENT` | P2 | no meta robots; the page inherits an unstated default |
| `SITEMAP_MISSING_INDEXABLE` | P2 | an indexable listing is not submitted |

Absence and contradiction are deliberately separate codes.
`PRICE_ABSENT_ON_WIRE` means we did not find a price;
`PRICE_WIRE_VS_DB` means we found a wrong one. Collapsing them turns "we looked
in the wrong place" into "production is broken" — which happened once during
development, when a shell-quoting mistake reported no price on a page that
states it seven times.

### 4.6 Fault attribution respects ownership

When `price_label` drifts from the variants, `DB_PRICE_AUTHORITIES_DISAGREE`
fires and `PRICE_WIRE_VS_DB` is **suppressed**. The page is rendering the row it
was given, faithfully. Firing both would send someone to debug a correct
template. This is the no-interference rule expressed in code rather than prose.

### 4.7 Proof the sentinel can fail

A clean baseline across 41 pages is worth exactly as much as the test suite's
ability to go red. A comparison engine fails silently by nature: every check
degrades to "no contradiction found" when the thing it reads stops being read.

`scripts/protection/mutate_search_truth.py` breaks one comparison at a time in a
`TemporaryDirectory` copy — no restore step, so an interrupted run cannot leave
a mutation in real source.

```
baseline  GREEN  (52 passed, 27 subtests passed)
killed 10/10
```

All ten mutants die, including `AGGREGATE_OFFER_IGNORED`,
`STOCK_LABEL_MATCHED_AS_SUBSTRING`, `UNKNOWN_READS_AS_KNOWN` and
`ROW_BLAME_COLLAPSED`. Two things the harness found that the tests had missed:

* **A real blind spot.** The engine originally read only `offers.price`, so the
  three ranged products (listings 15, 89, 112 — 7% of the catalogue) reported
  UNKNOWN. That was the worst possible place to be blind: ranged rows are
  exactly where `marketplace_seo` documents live drift, naming listing 112 in
  its own docstring. Fixed with `MoneyClaim` spans throughout; 41/41 pages are
  now fully comparable.
* **An incidental kill.** `CURRENCY_DROPPED_FROM_COMPARISON` was initially
  "killed" only by a test that *formats* money and asserts nothing about
  comparing it. A real test was added
  (`test_the_same_number_in_a_different_currency_is_a_disagreement`). A mutant
  killed by an unrelated assertion is a survivor with good manners.

The harness reports `HARNESS_ERROR` separately from `KILLED` and aborts on a red
baseline. Its own first run proved why: the sandbox omitted the `seo` package,
the suite could not collect, and a cruder harness would have read that as ten
kills. "Could not run" and "caught it" are different answers.

## 5. Source authority map

Every metric, its authority, and its real failure mode. **Confidence is a
property of the source, not of the renderer.**

| Metric | Authority | Owner | Freshness | Confidence | Failure mode |
| --- | --- | --- | --- | --- | --- |
| path indexability | `search_visibility.classify` | Agent 2 | at read | DERIVED (policy) | policy ≠ deployed page; the sentinel is the only cross-check |
| canonical URL | `marketplace_seo.product_url` | Agent 2 | at read | DERIVED (policy) | two builders disagree on `page`; calling the wrong one hides page 2+ |
| submitted URL set | `GET /sitemap-*.xml` | Agent 6 | at fetch | OBSERVED | an unreadable sitemap must stay UNKNOWN, never `set()` |
| display price | `marketplace_listings.price_label` | Agent 7 | at read | OBSERVED (DB) | drifts from variants independently; feed + structured data consume it |
| charged price | `marketplace_listing_variants.price_cents` | Agent 7 | at read | OBSERVED (DB) | what the page renders and checkout charges |
| wire price (×7) | deployed HTML | Agent 5 / Agent 11 | at fetch | OBSERVED (wire) | each surface drifts alone and silently |
| feed row | `merchant_center_feed.feed_row` | Agent 7 | at read | DERIVED | not independently verifiable in-process — same parser |
| indexed / impressions / clicks | — | — | — | **NO_EVIDENCE** | no provider client exists |
| "organic visits" | `analytics_events.referrer` LIKE | Agent 11 | 24h window | **CONTAMINATED** | cannot exclude paid clicks — §6.2 |
| orders | orders tables | Agent 7 | at read | OBSERVED | no join to any search origin |

## 6. Open defects found

Each is reported to its owner. **I did not fix any of them** — §8.

### 6.1 `/admin/seo` reports intent and presents it as observation — P1, mine

`bot.py:43502`:

```python
schema_types = ["Organization", "SoftwareApplication", "Product", "FAQPage",
                "BreadcrumbList", "WebSite", "Course", "LearningResource"]
```

rendered at `bot.py:43504` as `{len(schema_types)}` under the label **"schema
families active/planned"**. The number 8 is a Python literal. It cannot move when
reality moves: if a schema family stops emitting entirely, the card still says 8.

The sibling counters — "public indexable paths tracked", "private/noindex
patterns protected" — are `len()` over policy lists. They are honest about
policy and silent about deployment. **Nothing on `/admin/seo` reads a deployed
page.** This is the defect the sentinel exists to answer, and it is why Phase 38
is right that `/admin/search` must come after backend truth: a dashboard built
over these inputs would be a prettier version of the same claim.

### 6.2 "Organic search visits" cannot exclude paid clicks — P1, mine

`bot.py:32015`:

```sql
SELECT COUNT(*) FROM analytics_events
 WHERE created_at>=?
   AND (referrer LIKE '%google.%' OR referrer LIKE '%bing.%'
     OR referrer LIKE '%duckduckgo.%' OR referrer LIKE '%search.yahoo.%')
```

rendered as the card **"Organic search visits"**. Three verified problems:

1. **A paid Google Ads click satisfies `referrer LIKE '%google.%'`.** There is a
   live campaign at $10/day. The metric labelled organic provably includes paid
   traffic; the magnitude is UNKNOWN, and it is not zero.
2. **The data cannot distinguish them.** `analytics_events` (`bot.py:125193`) has
   no `utm_*` and no `gclid` column — `gclid` appears **zero** times in `bot.py`.
   The only table carrying paid attribution is `sessions`, which this query never
   joins. The one column that could exclude paid traffic is in the other table.
3. It counts **events**, not sessions, while the adjacent "visitors" cards count
   `DISTINCT session_id` — so the two are not comparable, though they are
   displayed side by side.

This is the mission's "direct traffic ≠ organic" prohibition, live in production.
The honest label is "visits with a search-engine referrer (includes paid)". The
honest fix is capturing `gclid` and joining `sessions` — **Agent 0's call**, as
it touches the ads stack.

### 6.3 `require_admin_password()` accepts the secret in the query string — P1, security

`bot.py:17213`:

```python
supplied = request.args.get("password") or request.headers.get("X-Admin-Password", "")
```

So `GET /admin/analytics?password=<secret>` authenticates. An admin credential in
a URL lands in the Railway edge access log, in browser history, and in the
`Referer` header of any outbound request from that page. The reflection *into the
page* is already hardened (`bot.py:32131` escapes it for both contexts), so this
is specifically about the secret travelling in a URL, not about XSS. Undocumented
— no comment acknowledges the trade-off.

Not search-specific and **not mine to change**; routed to Agent 0. Noted here
because `/admin/analytics` is the surface an Admin Search OS would otherwise
extend, and extending it would inherit this.

### 6.4 Two sitemaps are submitted empty — P2, Agent 9

`sitemap-live.xml` and `sitemap-replays.xml` return 200 with zero URLs. Empty is
a finding, not an unknown. Media/live search exposure is Agent 9's; recorded, not
touched.

### 6.5 `test_merchant_center_feed`'s price check is a containment test — P2, Agent 7

`test_the_feed_price_appears_on_the_landing_page` is
`assertIn(amount, self.page(listing_id))`. A whole-body containment check passes
when the page states the feed's number **and also** a contradictory one, and its
fixture is a synthetic single-variant row that never exercises ranges. The same
shape appears in
`test_the_feed_and_the_page_json_ld_report_the_same_availability`.

This is not a criticism of that suite's purpose — it is why the sentinel's check
is genuinely distinct rather than duplicated work. The sentinel's own test
`test_a_second_contradictory_price_in_one_sentence_is_a_fault` asserts the
containment check still passes on the mutated page before asserting the sentinel
catches it.

## 7. Already closed — do not re-discover

**Stored XSS on `/admin/analytics` is FIXED.** I re-derived it from first
principles and was wrong to think it open: `table()` at `bot.py:32083` escapes
every cell, and its docstring already documents the precise `clean_html` bypass I
had reconstructed.

Worth keeping, because it generalises: `clean_html` (`bot.py:133819`) is
`re.sub(r"<[^>]+>", " ")` — a tag *stripper*, not an escaper. It removes only
syntactically **complete** tags. Verified empirically:

| input | output |
| --- | --- |
| `<img src=x onerror=alert(1)>` | `` (stripped) |
| `<img src=x onerror=alert(1)` | `<img src=x onerror=alert(1)` **survives** |
| `<svg/onload=alert(1)` | `<svg/onload=alert(1)` **survives** |

So the textbook payload dies and an unterminated tag passes through untouched.
`/admin/analytics` is safe because it escapes at render. **Any new render site
that trusts `clean_html` as a sanitiser is not.** This directly constrains
`/admin/search`: §8.

## 8. What I deliberately did not build

**`/admin/search` does not exist yet, on purpose** (Phase 38). Backend truth has
existed for one branch. Building a dashboard over the inputs in §6.1 would
reproduce them with better typography.

When it is built, these are binding:

* **Read-only** (Phase 68). No remediation buttons, no "resubmit", no price edit.
* **No single SEO score** (Phase 106). A composite number hides which of its
  inputs is UNKNOWN, which is the only interesting thing about it.
* **Faults and `skipped` render together, always.** A page showing "0 faults"
  without "not checked" is manufacturing certainty.
* **No provider state rendered as fact.** Until a provider client exists, those
  stages render `NO_EVIDENCE` and link to this document.
* **Escape at render.** See §7. `clean_html` at ingest is not a sanitiser.
* **Proportionality** (Phase 92). 45 users and ~150 URLs. One table scan and an
  HTTP fetch loop. No queue, no warehouse, no new ingest.

Also not built, and not mine: price, inventory, canonical policy, structured-data
generation, sitemap generation, Merchant projection, IndexNow mechanics, media
eligibility, social-graph truth. The sentinel reads all of them and writes none
of them.

## 9. Search Exposure Ledger — a derived projection, not a new table

**The ledger must not be a new table.** Production already has ~111 event/audit
tables, 62 of them empty, with no shared envelope (prior-session census). A new
`search_exposure_ledger` would be the 112th, and nothing would read it.

Everything the ledger needs already exists and is cheap to derive at read time:

| Column | Derived from |
| --- | --- |
| `listing_id` | `marketplace_public_listings()` |
| `eligible` | `marketplace_seo.eligibility` |
| `path_indexable` | `search_visibility.classify` |
| `submitted` | `GET /sitemap-products.xml` |
| `self_consistent` | `search_truth.compare_listing` |
| `faults[]`, `skipped{}` | same verdict |
| `crawled`, `indexed`, `impressions`, `clicks` | `NO_EVIDENCE` — §1 |

The contract:

1. Every field carries its `source` (`POLICY` / `DATABASE` / `WIRE` / `FEED` /
   `PROVIDER`) and its freshness. `Observation(value, source, detail)` already
   does this, and `SOURCE_PROVIDER` is **declared but never produced** — the
   suite asserts no provider observation is ever emitted
   (`test_no_provider_observation_is_ever_produced`). The constant exists so the
   day one appears, it arrives typed rather than conflated with a derivation.
2. A field we could not evaluate is `UNKNOWN` with a reason, never `False`.
3. A stage we never asked about is `NO_EVIDENCE`, distinct from both.
4. Nothing is persisted that is not already persisted. No PII, no IP, no UA.
5. STALE is distinguishable from current: every verdict carries its fetch time,
   and a verdict with no fetch time is UNKNOWN, not clean (Phase 104).

## 10. Privacy and security model

* The sentinel sends **no** PII. One header: a Googlebot UA string. It reads
  public pages anonymously, which is also exactly the crawler's view, so the
  measurement and the privacy posture are the same choice.
* It writes nothing, anywhere. No table, no log line, no file.
* No telemetry field is added. The `ip_hash` inventory is untouched.
* `--with-db` is the only path that imports `bot`, is documented local/CI-only,
  and is never used against production.
* Fixtures are two real production pages, scrubbed of csrf tokens, nonces,
  session identifiers, bearer tokens and keys before committing. Provenance
  (origin, UA, date, deployed SHA) is recorded in the test module so a future
  reader can tell whether they still describe production.

## 11. Rollout and rollback

Rollout is trivial because nothing is wired into a request path:

* **Added:** `services/search_truth.py`, `scripts/search_os/search_truth_sentinel.py`,
  `scripts/protection/mutate_search_truth.py`, `tests/test_search_truth.py`,
  two fixtures, one line in `config/ci_test_manifest.json`.
* **No new route** — `config/route_auth_baseline.json` untouched.
* **No new `os.getenv`** — the env-contract gate is untouched.
* **No schema change.** No migration to roll back.
* **No deploy.** Nothing in this branch executes in production.

Rollback is `git revert`. The only non-additive change is the manifest line, and
removing the test file without it fails the CI manifest gate — revert both.

Gates run green: `tests/protection/test_every_test_file_is_run_by_ci.py` and
`tests/protection/test_environment_contract.py` (23 passed).

## 12. Invariants handed to Agent 12

Try to break these. Each is asserted by a named test, and each has a mutant that
dies when it is broken.

1. A page stating two different prices in two of its own surfaces is **always**
   P0. Includes two amounts in one sentence.
2. `0 faults` is **never** emitted without the `skipped` tally beside it.
3. An unfetchable page produces **zero faults** and a non-empty `skipped`.
4. A missing `Product.sku` is UNKNOWN, never `IDENTITY_MISMATCH`.
5. No `Observation` is ever emitted with `source == "PROVIDER"`.
6. The feed price is **evidence**, never a check — `FEED_PRICE_VS_WIRE` must not
   exist in the engine source (asserted literally).
7. `compare_page` never imports `bot` — asserted via `sys.modules`.
8. A drifting `price_label` is blamed on the row, and `PRICE_WIRE_VS_DB` is
   suppressed.
9. A range and a point price are both comparable; neither degrades to UNKNOWN.
10. Mixed-currency options are **not** collapsed into an invented span.
11. `"Currently unavailable"` is never read as available.
12. Every fault code declared in the engine is covered by a test — asserted by
    scraping `Fault("CODE"` out of the engine source, so adding a code without a
    test fails the suite.

**The specific attack I expect to work:** point the sentinel at an origin that
serves a login wall or an error page with HTTP 200. Every surface reads absent,
every comparison lands in `skipped`, and the run reports 0 faults. The `skipped`
tally is the only thing that distinguishes that from a healthy catalogue — which
is precisely why it prints unconditionally. If you can get a clean verdict whose
`skipped` is empty on a page that is not a real PDP, that is a genuine kill.

## 13. Handoffs

* **Agent 0** — §6.2 (organic/paid conflation; needs an ads-stack decision) and
  §6.3 (admin secret in URL). Both outside my authority. Also: the fleet has no
  provider credentials, so §1 is a budget/access decision only you can make.
* **Agent 2** — the sentinel is the first wire-level check of your policy.
  `ROBOTS_CONTRADICTS_POLICY` and `CANONICAL_MISMATCH` are yours when they fire.
  I consume `classify`/`canonical_url` and re-derive nothing. Zero faults today.
* **Agent 3** — `IDENTITY_MISMATCH` compares `Product.sku` to
  `pulsesoc-listing-{id}`. If you change the identity scheme, that template moves
  with you; it is one constant, `SKU_TEMPLATE`.
* **Agent 5** — the seven-surface map in §4.1 is your generator's full output
  surface. `PRICE_SURFACES_DISAGREE` and `PRICE_PROSE_STATES_TWO_AMOUNTS` fire
  against your layer.
* **Agent 6** — I read your sitemaps and keep "unreadable" (UNKNOWN) strictly
  apart from "empty" (a finding). §6.4 is in your neighbourhood.
* **Agent 7** — §6.5 (your feed test's containment gap) and the two DB price
  authorities. `DB_PRICE_AUTHORITIES_DISAGREE` is yours alone; the page is
  innocent when it fires.
* **Agent 9** — §6.4: two empty media sitemaps, submitted.
* **Agent 12** — §12.

## 14. Definition of done, honestly

Done: observability reality map; provider inventory; source authority map;
ledger contract; the sentinel, tested (52 tests) and mutation-proven (10/10);
production baseline; incident model; privacy model; rollout/rollback;
invariants; handoffs.

Not done, and deliberately: `/admin/search` (Phase 38 — one branch of backend
truth is not enough); any provider integration (no credentials, and that is
Agent 0's call); any fix to §6.1–§6.5 (not mine to fix).

The one-sentence state of the system:

> PulseSoc can now prove that the 41 product pages it submits for indexing tell
> one consistent story about their own prices, availability, canonical identity
> and robots posture — and can prove nothing whatsoever about what any search
> engine did with them.
