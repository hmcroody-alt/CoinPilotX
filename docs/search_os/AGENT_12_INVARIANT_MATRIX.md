# Agent 12 — cross-agent invariant matrix

For **Agent 0** to freeze. Sixteen invariants that span agent boundaries, each
one machine-testable, each one with a named owner and a measured current status.

**Why this document exists.** Every agent can ship a green suite and the
property can still be broken, because the defects that cost real traffic live in
the *disagreements between* agents: the policy table says one thing, the page
renders another, the sitemap offers a third. No single agent's tests can see
that. These fifteen are the seams.

**How to read the status column.** Every entry is something I measured this
round, in this repo, through the Flask test client or against production
read-only — not inferred from source and not recalled. Where I did not measure
it, the row says so. Probes live in `.attack/`; evidence is in
`AGENT_12_FINDINGS.md` under the cited finding ID.

**How to read the coverage column.** This is the column that matters most, and
the three values are not interchangeable:

| value | meaning |
|---|---|
| **GATE** | A test that CI actually executes on every PR. Verified, not assumed — see the note on `realtime-audio.yml` below. |
| **PROBE** | I can measure it on demand. Nothing stops a regression. |
| **NONE** | Nothing measures it, including me, except by hand. |

A **PROBE** row is a row where the invariant is true today and nothing will tell
you the day it stops being true. Those are the rows to fund.

> **Verified CI fact, because the matrix depends on it.** Protection suites are
> discovered by `scripts/protection/run_protection_suite.py`, which **globs**
> `tests/protection/test_*.py` — a new gate is enforced by existing. It is
> invoked from the `backend` job of `.github/workflows/realtime-audio.yml`, which
> carries **no `needs: detect` and no path filter**, so it runs on every PR to
> every branch. I checked this specifically: had the job been gated on
> audio-path changes, every GATE below would really be a PROBE. (CLAUDE.md names
> a `protection.yml` that does not exist. Doc drift, not a defect.)
>
> The four non-protection suites below are in `config/ci_test_manifest.json`'s
> `run` list, which is default-deny: `tests/test_search_visibility.py` (35),
> `tests/test_sitemap_integrity.py` (16), `tests/test_marketplace_seo.py` (46),
> `tests/test_marketplace_pagination_canonical.py` (30) — **127 tests**.

---

## A. Indexability truth — does the page say what the policy decided?

| # | Invariant | Owner | Predicate | Status | Coverage |
|---|---|---|---|---|---|
| 1 | The robots directive a page serves equals `search_visibility.robots_meta(path)` | Agent 2 / 4 | For every `url_map` GET rule rendering HTML: `normalize(served) == normalize(policy)` | **VIOLATED** — A12-01, A12-03, A12-05 | GATE (mine, **unmerged and red by design**) |
| 2 | A page policy declares indexable still emits the preview directives | Agent 4 | `max-image-preview` / `max-snippet` / `max-video-preview` present wherever policy includes them | **VIOLATED** — 14 pages restate the literal and drop them (A12-03); 11 send no directive at all (A12-05) | GATE (same, red) |
| 3 | A page serving `noindex` is never offered to search engines | Agent 2 / 6 | no sitemap `<loc>` resolves to a `noindex` 200 | **HOLDS** — re-measured **against production**: 0 of 148 (plus 206/206 on the dev copy), one carved-out exception (`/pulse/marketplace`, documented in the gate) | **GATE** (landed) |
| 4 | Nothing non-public is declared indexable **or sitemap-eligible** by the policy table | Agent 2 | `classify(p).indexable` ⇒ p is anonymously public, ∀p | **VIOLATED** — A12-02: the fallthrough declares 5 non-public pages both | **NONE** |

**Row 4 is the one to watch.** It is violated in the *policy*, and harmless
today only because no generator enumerates the policy — they all use curated
path lists and DB rows (see row 12). It converts to live breakage the day
Agent 8's IndexNow submitter enumerates eligibility, which its frozen contract
says it will.

Measured correction: the over-declared set is **unbounded**, not five pages —
every arbitrary `?category=` value on `/pulse/marketplace` is declared eligible
and indexable, because `_CONTENT_QUERY_PARAMS` whitelists the parameter *name*
and never checks the *value*. But the consequence is bounded tighter than the
count suggests, and I checked rather than assumed: those URLs serve `noindex`
and collapse to the bare hub at the wire, so an IndexNow submission of one costs
crawl budget and quota, not an indexed page. The five non-public paths of A12-02
are the part with no such backstop, and they stay the priority.

## B. Canonical truth — is there exactly one URL per document?

| # | Invariant | Owner | Predicate | Status | Coverage |
|---|---|---|---|---|---|
| 5 | Every indexable 200 declares a canonical | Agent 2 | `rel="canonical"` present on every indexable HTML 200 | **VIOLATED** — `/arena-preview`, 1 of 206 (A12-09b) | **NONE** — the landed gate skips absent canonicals by an explicit `continue` |
| 6 | A canonical is self-referential unless the path is a declared alias | Agent 2 | `canonical(p) == CANONICAL_ORIGIN + p`, except `_CANONICAL_ALIASES` | **VIOLATED** — A12-04: 2 `sitemap_eligible` paths point elsewhere (`/pulse/help`, `/pulse/support`) | PROBE — the landed gate would catch these, but only for *offered* URLs, and these two are not offered |
| 7 | All canonical builders agree for the same request | Agent 2 / 5 | every construction site yields one string per (path, query) | **HOLDS where it matters** — measured: the two builders disagree on `page` *by design* and no crawler sees the conflict (see below) | **GATE** (30 manifest-run tests) |
| 8 | A canonical is host-independent | Agent 2 | `Host:`/`X-Forwarded-Host` injection cannot change the emitted canonical | **HOLDS** — measured | **PROBE** |

**Row 7 was measured this round and the disagreement is benign.** There really
are two builders — `search_visibility.canonical_url(path)` is page-blind and
`marketplace_storefront.render_discovery()` is page-aware — and for `?page=2`
they emit different strings. That is correct: the live page self-canonicalises
to `?page=2` per Google's pagination guidance, while
`sitemap_eligible("?page=2")` is `False`, so page 2 is never *offered*. Two
consumers, two right answers, no conflict reaching a crawler. Measuring this
needed a corpus past `PAGE_SIZE = 24`: my first attempt ran against 16
publishable listings, where page 2 does not exist and `canonical_page` clamps to
1, and would have been published as "they agree." The probe now refuses to
report below two pages.

The same probe's `category` axis is a **PASS** — `?page=999` clamps rather than
minting URLs, known departments keep their own canonical, and every hostile
`?category=` value is suppressed at the wire with `noindex` plus a collapse to
the bare hub. The policy layer is value-blind and says so in its own docstring;
the mitigation is real and is gated at
`tests/test_marketplace_pagination_canonical.py:273`. Full record in the
findings log under the facet/pagination pass.

**Row 5's defect is a comment, not a bug.** The landed gate carries
`continue  # absent canonical is a different (weaker) finding`. It is not
weaker: a *mismatched* canonical folds a page onto one wrong URL (bounded,
one duplicate); an *absent* canonical on an indexable page folds nothing, so
every tracking parameter mints a document (unbounded). Mutation-proved — see the
CORRECTION under A12-09.

**Row 8 is the worst risk/coverage ratio in the matrix.** Canonical host
injection is the highest-severity SEO attack there is: it hands an attacker the
ability to make our pages canonicalise to their domain. It currently **holds**,
and nothing but me is checking. Compounded by the fact that redirects here emit
`http://` behind Railway's TLS edge (no ProxyFix anywhere), so proto handling is
already known to be fragile. **Fund this row first.**

## C. Offer truth — is every URL we submit one we can serve?

| # | Invariant | Owner | Predicate | Status | Coverage |
|---|---|---|---|---|---|
| 9 | Every submitted URL answers 200 to an anonymous crawler | Agent 6 | ∀`<loc>`: 200, no session, no redirect | **HOLDS** — re-measured **against production**: 148/148 200, 0 redirects, 0 canonicals pointing away (dev copy: 206/206) | **GATE** (landed) |
| 10 | Every submitted URL is crawlable under our own `robots.txt` | Agent 6 | no `<loc>` matches a served `Disallow` | **HOLDS** | **GATE** (landed, and it parses the *served* body — a prior version read the helper and survived a mutation) |
| 11 | The sitemap index lists exactly the routed child sitemaps | Agent 6 | `{url_map sitemap-*.xml rules} == {index <loc>}` | **HOLDS** — 6 of 6 | **GATE** (landed; derives the expected set from `url_map`, not from the constant it generates from — deliberately non-circular) |
| 12 | The offer set is a subset of the policy-eligible set | Agent 6 / 8 | ∀ offered p: `sitemap_eligible(p)` | **HOLDS** — 0 of 206 rejected | **NONE** |

**Row 12 holds by accident, not by construction.** Generators use curated lists
and DB rows, so the offer set is a *strict subset* of the eligible set. That is
precisely why row 4 is latent. An IndexNow submitter that enumerates policy
inverts both rows at once, and nothing in CI would notice.

## D. Gate integrity — can the gates be trusted?

| # | Invariant | Owner | Predicate | Status | Coverage |
|---|---|---|---|---|---|
| 13 | A gate must see the whole set it certifies | **Agent 0** | the corpus is *discovered*, never hardcoded; and asserted non-empty | **VIOLATED by one artifact** — A12-09: `verify_sitemap_vs_live.py` hardcodes 3 of 6 sitemaps and reports 61 for a 206-entry property | partial, and **self-enforcing where it is done right** |

This is the meta-invariant, and the fleet is split on it:

- **Right:** the protection runner globs its directory; the sitemap gate derives
  its child list from `url_map`; both my gate and the landed one assert their
  corpus is non-empty *and* contains a known-positive case, so neither can pass
  by having nothing to check.
- **Wrong:** Agent 2's script, with a hardcoded three-name default and no
  discovery step.

Phase 100 belongs here: a gate that cries wolf gets switched off, and a gate
pointed at the wrong corpus is worse — it issues a green check it has not
earned. My own `.attack/probe_robots_agreement.py` currently fails this test
with **74 false positives** (JSON APIs, `sitemap*.xml`, `sw.js`) and must not
become a gate until it filters on content type. Recorded against myself.

## E. Commerce truth — Golden Rule 2's "price truth" and "availability truth"

| # | Invariant | Owner | Predicate | Status | Coverage |
|---|---|---|---|---|---|
| 14 | The price charged equals the price displayed | **payments** (not Search OS) | the amount in the provider Session derives from server state only, never from the request body | **VIOLATED** — A12-10: `lesson` and `live_class` price from `payload["price_label"]`; measured 50 and 500000 cents at the buyer's choosing, against a `course` control fixed at 25000 | **NONE** |
| 15 | An item that cannot be bought is not offered, and a lifecycle gate applies to every item type | **payments** / Agent 5 | the `is_public` / goods-policy checks are not conditioned on one `item_type` | **VIOLATED** — both are `if item_type == "marketplace_product"`, so a `draft` or `cancelled` lesson is purchasable (read from source, not measured) | **NONE** |
| 16 | An item that **can** be bought is reachable and offered | Agent 5 / marketplace lifecycle | ∀ listing where `is_public` holds on the stock authority: the page answers 200 and the URL is offered | **VIOLATED IN PRODUCTION** — A12-11: 152 of 196 published, approved, in-stock products answer 404; the gate reads `marketplace_listings.quantity` (null on 148) while the sync writes `marketplace_listing_variants.stock_quantity` | **NONE** |

**Row 16 is new, is the inverse of row 15, and is the only row in this matrix
that is violated in production right now.** Every other violation here is a
policy-layer or coverage defect; this one is 152 dead product URLs on
pulsesoc.com as of 2026-10-03. It earns its place next to row 15 because the two
are the same question asked in opposite directions, and because the reason
nothing caught it is structural: **rows 3, 9, 10 and 11 all certify the offer
set forward** — every URL we submit is good — and the offer set is generated by
the same broken predicate, so it is honestly small rather than wrong. Four
GATE-covered invariants pass cleanly over a catalogue that is 78% unreachable.
The inverse direction, *is every sellable product offered*, is row 16 and has
never had an owner.

Rows 14 and 15 are the only two whose violation costs **money** rather than
crawl budget, and they are the two with no coverage at all. Both are latent:
production has **zero** rows in `pulse_lessons`, `pulse_live_classes` and
`pulse_courses`, and `seller_transactions` holds `marketplace_product` rows
only. The defect is in the route, which ships today; the data is absent today.
That ordering is the argument for fixing it now — one line per branch while the
tables are empty, versus a refund reconciliation later.

They are in a *search-quality* matrix because Golden Rule 2 names price truth
explicitly: a Merchant feed is a price claim made to Google, and a checkout that
honours the buyer's own number makes every such claim unfalsifiable.

---

## Summary for Agent 0

| | count |
|---|---|
| invariants that **hold** | 6 (3, 7, 8, 9, 10, 11) |
| invariants **violated** | 10 (1, 2, 4, 5, 6, 13, 14, 15, **16** — and 12 latent-by-accident) |
| violated **in production, today** | 1 (**16** — A12-11) |
| not re-measured this round | 0 |
| covered by a **GATE CI runs** | 5 (3, 7, 9, 10, 11) |
| **PROBE** only — true today, unguarded | 3 (6, 8, 12) |
| **NONE** — nothing measures it | 6 (4, 5, 12, 14, 15, **16**) |

**The shape of the risk is not "many violations."** It is that the six rows with
no coverage at all include both money rows (14, 15), the unbounded-duplicate row
(5), the row that arms the other two (4, 12), and the one row that is already
broken in production (16). Meanwhile the four strongest gates all guard **offer
truth** (rows 3, 9–11) — the thing that was already broken once in production
and got fixed properly. Coverage followed the last incident, which is the normal
and wrong way for coverage to be allocated.

Row 16 is the sharpest illustration of that, and it is why I would put it first
rather than last. Those four offer-truth gates were all green over production
while 78% of the catalogue was unreachable, because they certify the offer set
in one direction only and the offer set is *derived from the same broken
predicate*. A gate cannot catch a defect that is upstream of the set it
enumerates. That is the same lesson as row 13, arriving from the other side: it
is not enough for a gate to see the whole set it certifies — something has to
check that the set is the right set.

**Four things to freeze, in order:**

1. **Row 16** — A12-11. The only production-live violation here, 152 dead
   product URLs, and the only one with a revenue number attached. Decide the
   stock authority, then gate the inverse direction. It jumped the queue on
   evidence, not on severity theatre.
2. **Row 8** — host-independent canonicals. Holds today, highest severity,
   PROBE-only. A gate here is cheap and the downside is catastrophic.
3. **Rows 14 and 15** — price and availability truth. One line per branch now;
   a reconciliation later.
4. **Row 5 + row 13** — delete the `continue`, add one canonical. They must land
   together: the tag without the gate rots on the next page added, the gate
   without the tag reddens main.

**One correction to my own numbers, since the matrix depends on them.** Every
"206" in the first version of this document was a dev-copy reading. Production
offers **148** URLs across the same six children, and its catalogue is 196
published listings against the copy's 16. Rows 3 and 9 have been re-measured
against production and hold there; rows 1, 2, 4, 5, 6 remain dev-copy readings
of *policy* code, which is environment-independent, so they stand. Row 16 exists
because I finally looked at production rows, which is where it was visible all
along.
