# Agent 11 — Search Intelligence, Exposure Ledger, Admin Search OS

First measured 2026-10-03 against production at deployed SHA `5bdf4e431`
(`GET /api/service/health`), which is also this branch's merge base — so the
fixtures below describe the same bytes as the code in this checkout.

**By the final verification pass the same day, production had already moved to
`6e9b64110`** — main takes dozens of commits a day from parallel sessions. The
wire baseline was re-probed against that newer deployment and was unchanged: 41
products, 0 faults, 0 UNKNOWN. That is the single most useful fact in this
document about its own shelf life: every number here is a reading with a
timestamp, the deployment it was read from is already gone, and the re-probe is
one command (§4.4). Do not treat any figure below as a property of the system.

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

**41 is a measurement, not a constant.** It is frozen here as a snapshot —
origin `https://pulsesoc.com`, captured 2026-10-03, first at deployed SHA
`5bdf4e431` and re-confirmed hours later at `6e9b64110` —
and nothing in the engine, the runner or the suite hardcodes it. The count comes
from the live sitemap on every run, so a catalogue of 300 products needs no code
change. Anyone who later writes `41` into an assertion has converted today's
weather into a law; the number to compare against is the sitemap, not this page.

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
baseline  GREEN  (56 passed, 45 subtests passed)
killed 12/12
```

All twelve mutants die, including `AGGREGATE_OFFER_IGNORED`,
`STOCK_LABEL_MATCHED_AS_SUBSTRING`, `UNKNOWN_READS_AS_KNOWN` and
`ROW_BLAME_COLLAPSED`. Four things the harness found that the tests had missed:

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
* **A guard reading prose as behaviour.** The first version of
  `test_the_comparison_engine_cannot_reach_a_connection` scanned raw source for
  `import bot` and failed — on the engine's own docstring, which says it never
  imports `bot`. That is the `route_auth` mistake in this repo exactly: a
  sentence about code counted as code. The scanner now blanks docstrings and
  comments via `ast` + `tokenize`, and deliberately keeps every other string
  literal, because SQL lives in those and excluding them would make the guard
  unfailable.
* **A kill it could not attribute.** The two source-scanning mutants reported
  `KILLED (0 test(s))`: `failing_tests()` read only `FAILED` lines, and a test
  using `subTest` reports `SUBFAILED`. A kill whose killer cannot be named is
  not much better than a survivor, since it is indistinguishable from an
  unrelated failure. The parser now reads both and deduplicates.

The harness reports `HARNESS_ERROR` separately from `KILLED` and aborts on a red
baseline. Two of its own runs proved why. The first omitted the `seo` package,
so the suite could not collect, and a cruder harness would have read that as ten
kills. The run that added the read-only guards omitted
`scripts/search_os/`, so the test that reads the runner raised `FileNotFound`
instead of asserting — the fix was to copy the file, **not** to skip the test
when it is missing, which would have made the guard evaporate in precisely the
environment built to prove it works. "Could not run" and "caught it" are
different answers.

The two newest mutants are deliberately inert at runtime — nothing calls the
function one adds, nothing uses the import the other adds. A mutation with a
runtime effect can be killed by any test that happens to touch the same line,
which only proves the suite noticed *something*. These can be killed by nothing
except the guard being measured, and each is caught by exactly one test.

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
13. **The engine never writes, and cannot reach a connection to write
    through.** No `INSERT`/`UPDATE`/`DELETE`/`UPSERT`/`ON CONFLICT`/`commit(`/
    `executemany` in the engine or the runner; no `import bot`, no
    `from . import db`, no `sqlite3` in the engine. Asserted over source with
    docstrings and comments stripped, and killed by two mutants that are inert
    at runtime so nothing but these guards can catch them.

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
* **Agent 9** — §6.4: two empty media sitemaps, submitted. Also §14.5 row 9.
* **Agent 10** — §14.5 row 10; nothing of yours is compared yet.
* **Agent 12** — §12 for the invariants, §14.9 for the twelve required
  mutations and which six are executable today.

The durable version of all of this, written to survive without me, is §14.

## 14. Durable fleet contracts

Agent 11's implementation lane is closed. This section exists so the contracts
survive without me — it is the part to read if you are picking this up cold.

### 14.1 The sentinel is frozen as an observer

```
      CANONICAL DB / COMMERCE AUTHORITIES
                      |
                      v
                  PROJECTIONS
          +-----------+-----------+
          v           v           v
         PDP       JSON-LD     MERCHANT
          +-----------+-----------+
                      |
                      v
              AGENT 11 COMPARES
                      |
                      v
                 FAULT / PASS
```

It must never become an authority for price, availability, canonical URL,
publication state, variant identity, Merchant eligibility or indexability. It
observes disagreement; it does not resolve disagreement by inventing a third
value.

This is not a stylistic preference. PulseSoc already has two price authorities
that disagree (`price_label` vs `marketplace_listing_variants`, §5). A third
one, owned by the module whose job is to audit the first two, would be the
version of this mistake that is hardest to unwind — and it would be
*self-concealing*, because the faults would stop firing precisely because the
auditor had silenced them. Invariant 13 makes that structural rather than
cultural, and two mutants prove the guard works.

### 14.2 The evidence lifecycle, and what each transition costs

```
ELIGIBLE -> SITEMAPPED -> SUBMITTED -> PROVIDER ACCEPTED -> CRAWLED
  -> INDEXED -> IMPRESSION -> CLICK -> SESSION -> CART -> CHECKOUT -> ORDER
```

Every arrow requires its own evidence. The forbidden promotions, each of which
is a real mistake available today:

| Do not read | as | because |
| --- | --- | --- |
| sitemapped | submitted | we serve the file; nobody fetched it on our instruction |
| IndexNow payload exists | submitted | `/api/indexnow` previews, and says so (§1) |
| provider HTTP 202 | indexed | acceptance is receipt, not inclusion |
| indexed | impression | inclusion is not display |
| impression | click | and a click is not a session |
| Google referrer | organic | cannot exclude paid — §6.2, §14.3 |
| checkout start | paid order | two different tables |
| Merchant feed generated | ingested / approved / shown | §14.5 |
| 200 + robots.txt + sitemap | Bing indexed | §14.6 |
| UNKNOWN | zero | §14.4 |
| "traffic rose after deploy" | causation | no control, n=45 users |

The local Merchant feed can legitimately be measured as GENERATED, ELIGIBLE and
CONSISTENT. None of those three is evidence of INGESTED, APPROVED, SHOWN or
CLICKED.

### 14.3 Attribution contract

Routed to Agent 0, Agent 12, and whoever owns analytics.

`organic_visits` (`bot.py:32015`) classifies by referrer hostname against an
event table that has no `utm_*` and no `gclid` column (`bot.py:125193`); only
`sessions` carries those, and the query never joins it. With Google Ads live at
$10/day, the number is inflated by paid clicks **by construction**. Do not
surface it labelled "Google organic visits" without remediation or an explicit
caveat.

Any eventual model must distinguish, where evidence permits: organic search,
paid search, social organic, social paid, direct, referral, email, unknown. And
must keep `unknown` rather than guessing — a referrer of `google.com` is not
evidence of organic, it is evidence of Google.

### 14.4 `/admin/search` — on hold, and its state contract for later

Still not built, for five reasons: no provider evidence source is connected;
several fleet contracts are still on specialist branches; Agent 0 has not
integrated the truth model; admin auth needs separate attention (§6.3); and a UI
built now would invite fake zeros.

A panel reading `0 impressions / 0 clicks / 0 indexed pages` would be
*materially misleading* when the true state is UNKNOWN. When Agent 0 authorises
it, it must render these states distinctly and never collapse any of them to a
number:

`PASS` · `FAULT` · `UNKNOWN` · `NO_EVIDENCE` · `STALE` · `NOT_CONFIGURED` ·
`DISABLED`

It must also not be built on top of §6.3 — an admin surface authenticated by a
secret in `request.args` should not be extended before that is addressed.

### 14.5 Expansion order for the sentinel

Price is done. Do **not** rush the rest; each one consumes a contract that must
freeze upstream first, and a comparison against a moving definition produces
noise that teaches people to ignore the sentinel.

| # | Comparison | Blocked on |
| --- | --- | --- |
| 1 | **Price** | **done** |
| 2 | Availability | final lifecycle/publication semantics |
| 3 | Canonical URL | Agent 2 |
| 4 | Product / variant identity | Agent 3 |
| 5 | Structured data | Agent 5 |
| 6 | Sitemap membership | Agent 6 |
| 7 | Merchant | Agent 7 |
| 8 | IndexNow / Bing | Agent 8 |
| 9 | Media | Agent 9 |
| 10 | Social × commerce | Agent 10 |

### 14.6 Provider handoffs

* **Agent 8 — IndexNow and Bing.** You own outbound distribution. When it is
  real, I consume: candidate generated, batch created, submission attempted,
  provider response, retry, permanent failure, last successful submission. I
  will never infer any of those from the existence of `/api/indexnow`. Bing
  stays UNKNOWN until a Bing evidence source exists; 200s, robots.txt and
  sitemap membership are not it.
* **Agent 7 — Google and Merchant.** Crawl, index, impression, click and
  Merchant ingestion status stay `NO_EVIDENCE` until a real integration exists.
  Do not build placeholder telemetry; a zero that looks like data is worse than
  a blank that looks like a blank.

### 14.7 Fixture governance

`tests/fixtures/search_truth/` holds two real production pages. Real fixtures
are why the parser works — a hand-written one would have used double quotes for
the single-quoted, `&quot;`-escaped `data-mkt-variants` attribute and the
richest price surface on the page would have gone silently unread. But a real
fixture is a photograph, and photographs age.

| | |
| --- | --- |
| Captured | 2026-10-03, Googlebot UA |
| Production SHA | `5bdf4e431d1fd9164706962d7610d287a1f3092b` — recorded in the suite as `FIXTURE_DEPLOYED_SHA`. **A provenance note, not an enforced gate:** nothing reads it, and nothing can, since the suite runs offline and cannot know what production serves now. Treat it as the date stamp on a photograph. **It was already out of date before this document was committed** — production moved to `6e9b64110` the same afternoon. That is the normal case here, not an incident. |
| Surfaces represented | `pdp_163_point_price.html` — one variant, point price, `Offer.price`; `pdp_15_range_price.html` — 42 variants over 6 prices, `AggregateOffer` |
| Scrubbed | nothing; both are anonymous public PDPs with no personal data |
| Re-probe when | the PDP template changes, a price surface is added or removed, or `marketplace_web.derive_price` changes |

Do not treat these as current production truth indefinitely. The live check is
the runner against the live origin; the fixtures only pin the parser.

### 14.8 Ownership map

| Agent | Owns |
| --- | --- |
| 0 | Integration, activation, admin surface, deployment |
| 2 | Canonical / indexability authority |
| 3 | Product and variant semantics |
| 5 | Structured-data projection |
| 6 | Sitemap and crawl-distribution truth |
| 7 | Google / Merchant projection and eventual provider evidence |
| 8 | IndexNow / Bing distribution |
| 9 | Media search eligibility |
| 10 | Social-commerce relationships |
| 11 | Cross-surface measurement and provider evidence |
| 12 | Adversarial Search OS verification |

### 14.9 Required mutations for Agent 12

Twelve attacks. Six are executable against code that exists today and are named
with the test that already catches them; six are contracts on code that does not
exist yet, and the correct time to write them is the day someone starts building
that code.

**Live today — all pass:**

| # | Attack | Must | Caught by |
| --- | --- | --- | --- |
| 1 | DB 30.50, PDP 30.50, JSON-LD 29.99 | fault | `test_price_surfaces_disagree_when_the_structured_data_drifts` |
| 2 | DB 20–30, `AggregateOffer` 20–30, page prints 20 only | fault | `test_a_visible_pill_showing_only_the_low_end_of_a_span_is_a_fault` — and its mirror, `test_an_aggregate_offer_span_that_drifts_from_the_printed_span` |
| 3 | 19.99 and 29.99 both in authoritative prose | ambiguity must not vanish | `test_a_second_contradictory_price_in_one_sentence_is_a_fault`, `test_a_contradictory_prose_price_is_never_recorded_as_an_absence` |
| 4 | currency differs, number matches | fault | `test_the_same_number_in_a_different_currency_is_a_disagreement` |
| 5 | one surface missing | UNKNOWN, never fabricated agreement | `test_price_absent_on_wire_is_a_different_code_from_a_wrong_price` |
| 6 | HTML fetch fails | UNKNOWN fetch failure, never a product fault | `test_a_page_we_could_not_fetch_produces_no_faults` |
| 12 | sentinel rewrites commerce truth | impossible | `test_the_comparison_engine_contains_no_write`, `test_the_comparison_engine_cannot_reach_a_connection`, `test_the_runner_contains_no_write` |

**Contracts on code that does not exist yet — write the test with the code:**

| # | Attack | Must fail |
| --- | --- | --- |
| 7 | Merchant feed generated ⇒ provider ingestion true | yes |
| 8 | IndexNow payload exists ⇒ submission true | yes |
| 9 | Google referrer ⇒ organic, with no paid/organic evidence | yes |
| 10 | provider telemetry unavailable ⇒ dashboard renders 0 | yes |
| 11 | a new authoritative price surface is added to the PDP and the sentinel ignores it forever | yes |

Number 11 deserves its detection recipe, because it is the one that rots
quietly: compare the surface list in §4.1 against Agent 5's generator output and
the rendered PDP, and fail when the page states a price the engine does not
read. The analogous guard already exists one level down —
`test_every_declared_fault_code_is_covered_by_a_test_above` scrapes the engine
for fault codes so a new code without a test fails the suite. Surface coverage
needs the same treatment once Agent 5's output is a declared contract rather
than a template.

### 14.10 Harness properties to preserve

Whatever the harness grows into, keep: a verified-green baseline before any
result is believed; proof the mutation actually applied (an unmatched anchor is
a harness error, not a kill); an import or collection failure reported as
`COULD_NOT_RUN` and never as a kill; a `TemporaryDirectory` copy with no restore
step; designed survivors counted apart from kills; and every kill attributed to
a named test. Agent 1 reached the same conclusion independently from the other
direction — a measurement harness that writes to real source creates the
production change it was built to detect.

## 15. Definition of done, honestly

Done: observability reality map; provider inventory; source authority map;
ledger contract; the sentinel, tested (56 tests, 45 subtests) and
mutation-proven (12/12); production baseline frozen as a dated snapshot;
incident model; privacy model; rollout/rollback; 13 invariants; fleet contracts
and handoffs.

Not done, and deliberately: `/admin/search` (Phase 38 — one branch of backend
truth is not enough, and §14.4 lists four more reasons); any provider
integration (no credentials, and that is Agent 0's call); any fix to §6.1–§6.5
(not mine to fix); comparisons 2–10 in §14.5 (each blocked on an upstream
contract).

The one-sentence state of the system:

> PulseSoc can now prove that the product pages it submits for indexing tell one
> consistent story about their own prices, availability, canonical identity and
> robots posture — and can prove nothing whatsoever about what any search engine
> did with them.

**Agent 11 — complete / pushed / standby.** Reactivate when Agent 0 integrates
upstream contracts, Agent 7 or Agent 8 provides real provider evidence, Agent 12
breaks an invariant, or production develops a Search Truth disagreement.
