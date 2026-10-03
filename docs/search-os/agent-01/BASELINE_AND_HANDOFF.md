# Agent 1 — Baseline Publication and Fleet Handoff

**AGENT 1 STATUS: COMPLETE / PUSHED / STANDBY**

Recon is finished. This document publishes the baseline, constrains how it may be
used, and hands each finding to its owning agent. It is the third and final Agent 1
artifact, alongside `SEARCH_TRUTH_REPORT.md` (what the internet showed) and
`FLEET_DEPENDENCY_REPORT.md` (per-agent alerts).

**Ownership.** Agent 1 owns *production search reality and baseline evidence*, measured
2026-10-03 against deployed SHA `5bdf4e431`. Agent 1 is **not** the authority on future
search architecture. Every design decision implied by a finding below belongs to the
owning agent, with Agent 0 arbitrating scope.

**Evidence source.** The pushed branch `search-os/agent-01-forensics` and its committed
artifacts under `.forensics/` and `docs/search-os/agent-01/` are the durable record. No
separate coordination system was invented — none existed, and §0 of the fleet report
notifies Agent 0 of that absence rather than filling it.

---

## 1. The baseline is a SNAPSHOT, not a constant

> **Do not hard-code 148, 44, 41, 36, 31, 12, 4 or 5 anywhere in Search OS.**

These are measurements of one deployed SHA at one timestamp, not business rules. Two
properties of the system guarantee they will be wrong later:

- **Catalogue membership drifts within minutes.** Supplier sync flips stock, which moves
  listings in and out of `public_sql`. Grid and category pages are byte-identical across
  consecutive fetches (the renderer is deterministic), but *membership* is not stable
  over a coffee break, let alone a sprint.
- **The counts are derived, not configured.** 44 is whatever `public_sql ∧
  discovery_visible_sql` returns; 41 is 44 minus whatever fails
  `MIN_DESCRIPTION_CHARS = 40`; 36 is 41 minus whatever
  `price_label_contradicts_variants()` flags; 4-of-12 categories is whatever clears
  `CATEGORY_MIN_INDEXABLE_LISTINGS = 3`. The *gates* are the durable facts. The *counts*
  are outputs.

What to carry forward instead of the numbers:

| Durable | Ephemeral |
|---|---|
| The three narrowing gates and their order | 44 → 41 → 36 |
| `MIN_DESCRIPTION_CHARS = 40` | ids 50, 52, 110 are the thin ones |
| `CATEGORY_MIN_INDEXABLE_LISTINGS = 3`, counted over *indexable* | 4 of 12 departments in the sitemap |
| Feed excludes rows where the two price authorities disagree | the excluded set {112, 15, 89, 35, 36} |
| Sitemap index has six children, two permanently empty | 87/15/4/42/0/0 = 148 |
| Zero orphans is an invariant worth testing | "there were zero orphans on 2026-10-03" |

Any agent that needs a current number must **remeasure**. Agent 11 owns turning these
into continuously measured state (§7 below); Agent 12 owns asserting the *invariants*,
never the integers (§8).

---

## 2. Measurement methodology — and the harness bugs that produced false findings

This section exists because the brief requires it, and because **four of my own
measurement scripts produced confident, plausible, wrong answers during this mission.**
Three were caught; each would have become a Search OS incident.

**Agent 12: treat this as the lesson. TEST THE TEST HARNESS. A broken measurement
script can create a false Search OS incident, and it will not look like a bug — it will
look like a finding.**

### 2.1 The method, exactly

- Anonymous `Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)`;
  `Googlebot-Image/1.0` for image probes. No cookies, no session, no JavaScript
  execution — so every measurement is what a crawler sees, not what a browser renders.
- Read-only `GET` only. Single-threaded, paced 0.4–1.0 s. 148-URL sweep in
  `.forensics/sweep.sh`.
- Production's deployed commit read from `/api/service/health` **before and after** every
  measurement block, so a mid-sweep deploy would have been visible. It stayed
  `5bdf4e431d1fd9164706962d7610d287a1f3092b` throughout.
- Every regex is **quote-agnostic** (`['\"]`).
- Counts are taken by **match**, not by line.
- Set comparisons use `comm` over **numerically** sorted ids, or a Python `set` diff.

### 2.2 Harness bug 1 — double-quoted attribute regexes (inherited, documented)

PulseSoc emits `<link rel='canonical'>` and `<meta name='robots'>` with **single**
quotes, because the HTML is built inside Python f-strings that already use double
quotes. A `rel="canonical"` regex therefore reports mass absence.

This is not hypothetical: `docs/seo/00_baseline_and_gap_list.md` records a prior pass
that "reported 189 pages missing rel=canonical… That was false."

**Rule:** never match an HTML attribute with a fixed quote character in this repo.

### 2.3 Harness bug 2 — the `opt_` prefix (two false conclusions from one guess)

A grep for `name="option1"` on a PDP returns zero. The variant selector *is*
server-rendered; the form control names carry an `opt_` prefix:
`name="opt_option1"`, `name="opt_option2"`. The `data-mkt-option` attribute, the
`data-mkt-variants` JSON payload, and the variant `key` strings all spell it
`option1` — only the control names are prefixed.

Two separate false findings follow from the same guess:

| Probe | Result | False conclusion |
|---|---|---|
| `grep 'name="option1"'` | 0 matches | "variant selector is client-rendered" |
| `GET ?option1=Black&option2=XL` | **byte-identical** to bare PDP (same sha256) | "variants are not URL-addressable" |
| `GET ?opt_option1=Black&opt_option2=XL` | server echoes `checked`, bytes differ | the truth |

Both false conclusions read as *confirmed absences*, not as measurement errors. The
unprefixed parameter form is silently ignored rather than rejected, which is what makes
it dangerous.

**Rule:** read the actual `<input>` tags before asserting anything about variant
rendering or variant URL addressability:
`grep -o -i '<input[^>]*type=.radio[^>]*>' pdp.html`.

### 2.4 Harness bug 3 — lexical sort on numeric ids → a FALSE category divergence

**This is the one the brief specifically asks be preserved so no future agent
reproduces it.**

I compared per-department listing membership against sitemap inclusion using
`comm -12` over `sort -u`. `sort -u` sorts **lexically**: `110` sorts before `15`, which
sorts before `50`. `comm` requires its inputs to be sorted *consistently with its own
comparison*, so a lexical sort silently produces garbage intersections — not an error,
not a warning, just wrong counts.

The result was a confident **"DIVERGENCE"** verdict against four departments: they
appeared to hold enough indexable listings to qualify for the sitemap while being
absent from it. That is exactly the shape of a real P1 sitemap defect, and I was one
step from filing it.

The fix was `sort -un` (numeric) before `comm -12`. With correct sorting, **every single
category exclusion is fully explained by `CATEGORY_MIN_INDEXABLE_LISTINGS = 3` counted
over the *indexable* subset** — not over the public subset, which is the other easy
error here. **No defect existed.**

The corrected measurement, preserved:

- All **12** departments return 200, `index,follow`, self-canonical, non-empty, and are
  linked from the grid. **Zero orphans.**
- Only **4** appear in `sitemap-categories.xml`.
- The 8-way gap is the threshold doing its job. `bot.py`'s own
  `sitemap-categories.xml` docstring records "twelve of them existed in production on
  2026-10-02", so the discrepancy is known to the source, not news.

**→ Agents 6 and 12: do NOT file the 4-of-12 category count as a bug.** It is designed
behaviour. If you want more categories indexed, the lever is the threshold or the
catalogue depth — a fleet decision, not a defect fix.

**Rule:** `comm` over ids demands `sort -un`. Better: do set algebra in Python, where
`set(a) & set(b)` cannot be silently mis-sorted.

### 2.5 Harness bug 4 — `grep -c` counts LINES, not MATCHES

Caught during the final re-probe, and the reason item 5 of my status report says
"nothing changed".

The re-probe reported the Merchant feed at **31** items against the **36** in my
committed report — a 5-item drop with no deploy, which is precisely the signature of a
real feed regression. I nearly published it.

```
grep -c '<item>' feed.xml        = 31   # counts LINES containing a match
grep -o '<item>' feed.xml | wc -l = 36   # counts MATCHES
```

Product descriptions contain embedded newlines, so several `<item>` tags share lines. A
Python set diff then confirmed feed membership was **identical**: `earlier: 36 / now: 36
/ DROPPED: [] / ADDED: []`. This also retroactively explains an earlier "31 items"
figure from the same session — same bug, same file.

Because the same bug could have under-counted my **headline** number, I re-counted every
sitemap child by match and cross-checked against the swept corpus:

```
sitemap-pages.xml        regex= 87   grep-c= 87
sitemap-posts.xml        regex= 15   grep-c= 15
sitemap-categories.xml   regex=  4   grep-c=  4
sitemap-products.xml     regex= 42   grep-c= 42
sitemap-live.xml         regex=  0   grep-c=  0
sitemap-replays.xml      regex=  0   grep-c=  0
TOTAL UNIQUE = 148        swept corpus = 148
in sitemap NOT swept: []  swept NOT in sitemap: []
```

`<loc>` tags happen to be one-per-line in the sitemaps, so `grep -c` agreed there. That
agreement is luck, not safety.

**Rule:** count XML/HTML tags with `grep -o … | wc -l` or a parser. Never `grep -c`.

### 2.6 Harness bug 5 — zsh does not word-split (silent single iteration)

`for i in $ids` in zsh iterates **once**, with the entire string as one element. A loop
meant to probe 44 listings probed one and reported a clean result. Fixed by wrapping
loops in `bash -c`. Related: nested quoting inside `bash -c` broke a `tr -d " "`, which
emptied a drift diff and made it read as "no drift".

**Rule:** any loop whose body makes a network call must print a count, and the count
must be asserted against an independently derived expectation.

---

## 3. The variant finding — published to Agents 0, 2, 3, 4, 5, 6, 7, 9, 11, 12

### 3.1 The finding, stated correctly

> **VARIANTS EXIST AND ARE ADDRESSABLE, BUT THEIR SEARCH IDENTITY, GROUPING AND
> DISCOVERY MODEL IS INCOMPLETE.**

That is the whole finding. `FINDING A1-05`, **P1**, HIGH confidence. Owner: Agent 3.

### 3.2 What was measured

- **31 of 44** public listings are multi-variant. Counts run to **95**. Observed
  distribution: 10 single-variant, then 4, 5, 6, 7, 8, 9, 10, 18, 20, 26, 30, 36, 40,
  42, 48, 56, 63, 95.
- Variants **are server-rendered**, inside `<form method="get" action="/pulse/marketplace/{id}">`
  with `<input type="radio">` controls.
- Variants **are URL-addressable**: `?opt_option1=Black&opt_option2=XL` makes the server
  echo `checked` on the matching radios and changes the response bytes.
- Variants are **invisible to search in all three channels**:
  1. **Not crawlable.** Zero `<a href>` anywhere on any PDP contains `opt_option`. A
     crawler never submits a form, so all 8 variants of listing 113 and all 95 of
     listing 36 are unreachable by crawl.
  2. **Not in structured data.** No `ProductGroup`, no `hasVariant`, no `variesBy`.
  3. **Not grouped in the feed.** No `g:item_group_id`, no `g:color`, no `g:size`.
- **Canonical does not reflect the selection.** A variant URL's canonical still points
  at the bare PDP. (Reported otherwise by a sub-agent during recon; that claim is
  **false** and was corrected against saved bytes.)
- Large-variant worked examples, for Agent 3: **listing 113** (8 variants,
  `option1`=colour × `option2`=size, raw HTML saved at `.forensics/pdp113.html`);
  **listing 112** (4 variants, `$27.84`/`$29.31`/`$31.69`/`$37.72`, JSON-LD
  `AggregateOffer` `lowPrice` 27.84 / `highPrice` 37.72 — an exact match);
  **listing 36** (95 variants, and also one of the five feed-excluded price-disagreeing
  rows).

### 3.3 What this finding explicitly does NOT say

> **Do NOT conclude "therefore every supplier variant should become an independently
> indexed URL."**

Agent 1 has not evaluated, and does not have standing to evaluate: thin-content risk
across hundreds of near-identical pages, crawl-budget cost, duplicate-content handling,
canonical strategy for a variant, or whether a variant is even the right search entity
here. Turning ~500+ addressable variants into ~500+ indexable URLs could as easily be a
crawl-budget catastrophe as a coverage win. **That decision belongs to the fleet**, with
Agent 3 defining the entity model and Agent 0 approving scope.

### 3.4 Credit where due — do not rewrite what is already right

Agent 5 should know the existing structured data is **already truthful**:

- `Offer` → `AggregateOffer` switches on real price spread, and `lowPrice`/`highPrice`
  match the observed variant prices **exactly**. The system chose to publish a truthful
  range rather than fabricate a single price. That is the correct instinct and it should
  survive any redesign.
- `brand`, `gtin`, `mpn`, `aggregateRating`, `review` are **deliberately omitted** with
  documented reasons (no backing column; no review table). **Zero fabricated values were
  found anywhere on any surface.** Do not "complete" the schema by inventing them.
- The gap is specifically variant *representation* — `ProductGroup` / `hasVariant` /
  `variesBy` — not schema correctness.

**Sequencing: Agent 5 acts only after Agent 3 freezes product identity.** Schema is a
serialisation of an entity model; emitting variant schema before the entity model exists
means encoding a guess into the thing Google caches.

**And: do not tell Agent 5 to manufacture variant URLs to win rich results.** Rich
results are not a reason to create an URL space the fleet has not approved.

---

## 4. Agent 3 handoff — product / variant semantic identity

Agent 3 owns the distinction PulseSoc currently does not draw:
**PRODUCT / PRODUCT GROUP / VARIANT / OFFER / SELLER OFFER / SUPPLIER VARIANT.**

Evidence Agent 3 inherits:

- **Prevalence:** 31/44 multi-variant; counts to 95; §3.2 for the distribution.
- **Variant mechanics:** positional options (`option1`, `option2`, `option3`) with
  `opt_`-prefixed GET control names; `data-mkt-variants` carries a JSON array of
  `{available, id, key, options, price, stock_label}` per variant, where `key` is
  `"option1=black|option2=xl"`.
- **Positional, not semantic.** Every option in production is positional — nothing in the
  data says `option1` *means* colour. PulseSoc invents the display label. An entity model
  that assumes `option1 = color` will be wrong for some listings.
- **Identity disagreement across surfaces**, which is Agent 3's core problem:

| Surface | Identifier for the same listing | Example |
|---|---|---|
| Merchant feed | `g:id` = bare listing id | `163` |
| JSON-LD | `sku` = prefixed | `pulsesoc-listing-163` |
| Variant | `marketplace_listing_variants.id` + a `key` string | `1885`, `option1=black\|option2=xl` |
| Feed grouping | **absent** — no `g:item_group_id` | — |

One product has at least three identifiers across three surfaces and no stable identity
for a variant at all. **Agent 7 must coordinate with Agent 3 before changing item
identity in the feed** — changing `g:id` on a live feed re-identifies items to Google.

---

## 5. Agent 7 handoff — CRITICAL. The feed exclusion is a SAFETY MECHANISM.

> **INVARIANT: IF MERCHANT CANNOT REPRESENT PRICE / VARIANT TRUTH SAFELY, EXCLUDE THE
> ITEM RATHER THAN LIE.**

### 5.1 Do not weaken the exclusion to increase coverage

A Google Merchant Center feed is **already live** at `/feeds/merchant-center.xml` with
**36** items. `sitemap-products.xml` carries **41**. The 5-item gap will look like a bug
to anyone optimising coverage. **It is not a bug. It is the only thing standing between a
stale price label and a Merchant Center misrepresentation suspension.**

There are **two price authorities**:

| Authority | What it is | Who reads it |
|---|---|---|
| `price_label` | a display string a human typed at publish time | **the feed** |
| `marketplace_listing_variants.price_cents` | the real variant price | the PDP, **and checkout** |

`marketplace_seo.price_label_contradicts_variants()` exists solely to detect
disagreement. Its own docstring records that on **2026-10-01, four of the then-35 feed
rows disagreed** — listing 36 advertised `$38.00` against a `$2.29` variant; listing 112
advertised `$29.31` against a `$27.84`–`$37.72` range. *Page dearer than feed* is
textbook misrepresentation.

### 5.2 The 2026-10-03 result — exposure CLOSED, data debt OPEN

Measured across all 36 feed items and 20 grid cards:

- The 5 sitemap products absent from the feed are **exactly** the price-disagreeing rows:
  **{112, 15, 89, 35, 36}**.
- **0** feed-vs-PDP price mismatches.
- **0** grid-vs-PDP price mismatches (the grid publishes a variant-derived low price).
- JSON-LD switches to `AggregateOffer` with ranges matching the real variant spread, so
  structured data does not misrepresent either.
- **The stop condition did not fire.** No live price misrepresentation.

**But the guard removes rows from the feed; it does not reconcile the data.**
`price_label` is still wrong on those rows. And the guard **fails open by design** — its
docstring says so: "Callers that do not load variants get `False`… a deliberate
fail-open."

So, for Agent 7:

- **Do not "fix" the feed/sitemap count mismatch by loosening `feed_eligible`.** The gap
  is the safety mechanism.
- **Never make the feed read `price_label` without the contradiction check.**
- **Any new feed caller that does not join variants silently re-opens the exposure.**
- The real fix is upstream: reconcile `price_label` against variant truth, or stop
  treating a typed string as a price authority. That is data work, not feed work.
- **Do not reopen this as a live misrepresentation finding without new evidence.** It was
  measured closed on 2026-10-03. Agents 7, 11 and 12 should monitor it **continuously** —
  it is a drift risk, not a settled question.

### 5.3 Merchant field inventory (measured, for coverage work that does not touch safety)

| Present | Absent |
|---|---|
| `g:id`, `title`, `description`, `link`, `g:image_link`, `g:availability`, `g:price`, `g:condition` (hardcoded `new`), `g:identifier_exists` (`no`) | `g:item_group_id`, `g:brand`, `g:gtin`/`g:mpn`, `g:google_product_category`, `g:product_type`, `g:color`/`g:size`/`g:age_group`/`g:gender` (**required for apparel**), `g:shipping`, `g:sale_price`, `g:mobile_link` |

Also: image hosts disagree across surfaces (`oss-cf.cjdropshipping.com` vs
`cf.cjdropshipping.com`), and every product image is third-party supplier-hosted —
crawlable today (`200 image/jpeg` to `Googlebot-Image/1.0`, no hotlink protection, both
hosts `Allow: /`) but on a domain PulseSoc does not control. That is Agent 9's.

---

## 6. The retired crypto surface — ESCALATE, DO NOT DELETE

Published to Agents 0, 1, 2, 5, 6, 11, 12.

**Measured:** JSON-LD `Organization.legalName` is **`"CoinPlotXAI Inc."`**, and ~60 of
the 87 URLs in `sitemap-pages.xml` describe the retired crypto/sports product
(`/whale-tracker`, `/sports-edge/*`, `/telegram-crypto-bot`, `/learn/crypto-scams`, …).
Google is currently being told this is a crypto company.
→ `FINDING A1-10`, P2, HIGH confidence. **Owner: Agent 0 to assign.**

**Constraints on anyone who touches it:**

- **Do NOT automatically remove the legal entity name.** A **legal entity name is not a
  consumer brand.** `CoinPlotXAI Inc.` may well be the correct, legally required
  `legalName` for the operating company behind a product branded PulseSoc. Removing it to
  tidy up the brand story could make the Organization schema *false* — and
  `Organization.legalName` is read by payment processors and merchant review, not just by
  search.
- **Agent 5** determines what a *truthful* Organization schema looks like: `legalName` and
  `name` are different fields and may legitimately differ. The fix, if any, is probably
  "set `name` correctly", not "delete `legalName`".
- **Agent 11** gathers the evidence before anything is decided: traffic, impressions, and
  indexation state for the ~60 legacy URLs. Some may be the only pages ranking for
  anything.
- **Agent 0 decides.** This is a brand/legal/lifecycle question, not an SEO cleanup task.
- **No mass redirect. No mass `noindex`. No deletion merely because a page is old.**
  Retiring 60 indexed URLs without evidence is the single fastest way to lose whatever
  organic presence exists.

---

## 7. "Nothing pushes" — published to Agents 0, 6, 7, 8, 11, 12

**Measured:** there is **zero push-side search automation** in production.

- No SEO, sitemap, feed or IndexNow worker in the `Procfile`. The workers that exist are
  `undx`, `email`, `ads`, `alert`, `media`, `supplier`.
- IndexNow has a **key file and a metadata endpoint**, both present and serving — and
  **no submission logic**. The endpoint says in its own source that it does not submit.
- `BingSiteAuth.xml` is **ABSENT**; GSC / GA4 / Bing verification meta tags are
  **PRESENT**; GTM is **ABSENT**.
- `services/seo_service.py` is an **orphan** — no call sites.
- Every surface is therefore **pull-only**, against a catalogue that measurably drifts
  within minutes.
→ `FINDING A1-13`, P2.

**Agent 1 does not implement these workers.** Agent 8 owns Bing/IndexNow submission;
Agent 11 owns monitoring; Agent 0 owns sequencing. The specific trap for Agent 12: an
IndexNow integration can return success from a key-file check while submitting nothing —
**a fake IndexNow success is a named regression class** (§8).

---

## 8. Agent 12 handoff — permanent regression and mutation coverage

A CI gate already exists enforcing that every submitted URL is indexable; do not rebuild
it. What is missing is coverage for the **disagreements** this mission found. Ten named
classes, each of which must fail when the invariant is violated and must be proven to
fail (mutate the source, watch the test go red, revert):

1. **Variant search-identity disagreement** — a variant that is addressable but absent
   from schema and feed grouping.
2. **Merchant price-authority disagreement** — `price_label` vs
   `variants.price_cents`. Assert the *exclusion behaviour*, not the excluded ids.
   Include a test that a feed caller which does not join variants is rejected, because
   the production guard fails **open**.
3. **Sitemap / indexability disagreement** — a URL in a sitemap that serves `noindex`,
   and the inverse.
4. **Canonical disagreement** — cross-canonical, missing canonical, and a canonical that
   contradicts the serving URL's own directives.
5. **Parameter crawl explosion** — pagination that does not clamp, or a parameter that
   mints an indexable URL. The space is contained **today**.
6. **Fake IndexNow success** — a submission path that reports success without submitting.
7. **Legacy surface lifecycle mistakes** — a mass `noindex`/redirect/delete landing on
   the legacy crypto URLs without the evidence gate from §6.
8. **Measurement-harness false positives** — §2 is the specification. Assert the harness:
   quote-agnostic attribute matching, `opt_`-prefix awareness, numeric sort before
   `comm`, match-counting not line-counting, and loop iteration counts.
9. **Private / held exposure** — anything behind `/pulse`'s private-by-exception default
   leaking into a sitemap, feed, or `index,follow` response.
10. **Structured-data / product-truth disagreement** — JSON-LD price vs PDP price vs feed
    price vs what checkout charges; and any *fabricated* field (`brand`, `gtin`, `mpn`,
    `aggregateRating`, `review` are currently omitted on purpose — a test should notice if
    one starts appearing with invented content).

**Assert invariants, never the integers.** A test pinned to `== 148` fails on the next
supplier sync and teaches the fleet to ignore red CI.

---

## 9. Agent 11 handoff — convert the baseline into measured ongoing state

Agent 11's job is **not** to store 148/44/41/36 as expected values. It is to measure the
same things continuously and alert on **divergence between surfaces**, which is what
every real finding in this mission turned out to be.

Signals worth continuous measurement:

- Funnel at each gate (public → indexable → feed-eligible) as a **series**, with the
  narrowing *ratios* as the watched quantity.
- Count of rows where the two price authorities disagree — expected **non-zero**, and
  falling is good. Alert on it appearing **in the feed**, not on it existing.
- Orphan count (expected 0).
- Sitemap URLs that do not return 200 / are not self-canonical / are `noindex`
  (expected 0 each).
- Variant count addressable vs variant count represented in schema and feed.
- Deployed SHA, so any baseline can be attributed to a deploy.
- GSC-side reality: impressions, coverage, excluded reasons — **currently UNKNOWN to
  Agent 1** (§10).

---

## 10. The three UNKNOWNs stay UNKNOWN

Agent 1 did not measure these and will not guess. **UNKNOWN IS BETTER THAN INVENTING AN
ANSWER.**

| Unknown | Why | Owner |
|---|---|---|
| Google Search Console / Bing Webmaster reality — what is *actually* indexed, impressions, coverage exclusions | requires authenticated property access; verification meta is present but Agent 1 has no console access | Agents 7, 11 |
| Real-world performance / Core Web Vitals / crawl budget consumption | requires field data and log analysis, not single GETs | Agent 4 |
| Competitive search landscape | out of Agent 1's scope entirely | Agent 0 |

No finding in any Agent 1 artifact depends on these. Anyone who needs them must measure
them.

---

## 11. Reconciliation protocol with Agent 2

Agent 1 and Agent 2 measure overlapping surfaces and **must not overwrite each other's
evidence.**

| | Owns |
|---|---|
| **Agent 1** | *What the internet actually showed during recon* — a timestamped, SHA-attributed observation of production |
| **Agent 2** | *URL / canonical / indexability contract verification* — whether the system's rules are correct and correctly implemented |

These answer different questions and **can legitimately disagree without either being
wrong.** Before either agent declares the other's result a defect, compare all five:

1. **Timestamp** — catalogue membership drifts within minutes.
2. **Deployed SHA** — Agent 1's corpus is `5bdf4e431`. A different SHA is a different
   system.
3. **URL population** — 148 sitemap URLs is not the same population as "all routes", not
   the same as "all public URLs", not the same as "all indexable URLs".
4. **Eligibility definition** — `indexable` and `feed_eligible` are **two different
   verdicts** from the same `Eligibility` dataclass, and the second is strictly narrower.
   Comparing one against the other manufactures a disagreement.
5. **Probe method** — anonymous Googlebot UA without JS is not the same observation as an
   authenticated browser fetch, and `/pulse` is private-by-exception.

Known overlap points, already filed to Agent 2 in §2 of the fleet report: canonical
health is good (147/148 self-canonical, one `NO_CANONICAL` at `/arena-preview`);
`/pulse/cart` shows a policy-table-vs-renderer disagreement; trailing-slash variants
404; the parameter space is contained. Agent 2 should **verify the contract** rather than
re-running my sweep.

---

## 12. Scope close-out

Agent 1 does **not** implement any finding in this document and does **not** become
Agent 3, 5, 7, 8, 11 or 12. No source file was modified in this mission; the only
changes on this branch are evidence artifacts and these three reports.

**Agent 0 owns integration.** This branch is pushed, not merged, not deployed.

**Agent 1 remains available on standby for:** re-probing production on request;
validating another agent's assumption against the recon corpus; before/after comparison
once the fleet ships; certifying a change against the baseline; and investigating any
discrepancy Agent 11 or Agent 12 surfaces against these measurements.

**AGENT 1 STATUS: COMPLETE / PUSHED / STANDBY**
