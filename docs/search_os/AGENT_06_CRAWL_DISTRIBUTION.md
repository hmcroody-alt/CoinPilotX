# AGENT 6 — SITEMAP + CRAWL DISTRIBUTION ENGINE

Branch: `search-os/agent-06-crawl-distribution`
Commit: `39fdd836a` — *Stop advertising two sitemaps that can never list a URL*
Base: `origin/main` @ `5bdf4e431`
Worktree: `/Users/hmcherie/Desktop/cpx-crawldist` (durable, not `/tmp`)
Date: 2026-10-03

---

## 1. Lane

Owned: sitemap architecture, the sitemap index, generation, partitioning, lifecycle,
eligibility *projection*, `lastmod` truth, invalidation, image/video sitemap
distribution mechanics, crawl distribution events, distribution observability.

Not owned, and not touched: canonical policy (Agent 2), product semantics (Agent 3),
SSR/rendering (Agent 4), structured data (Agent 5), Merchant Center (Agent 7),
IndexNow (Agent 8), media eligibility (Agent 9).

The governing constraint I worked under: **a sitemap distributes a decision it does
not make.** Where distribution and policy disagreed, I changed distribution or
escalated — I did not add private eligibility logic to compensate.

---

## 2. Production baseline (observed, not assumed)

From Agent 1's forensics capture (`cpx-searchforensics/.forensics/`, read-only):

| Endpoint | `<loc>` | `<lastmod>` |
|---|---|---|
| `/sitemap-pages.xml` | 87 | 0 |
| `/sitemap-posts.xml` | 15 | 15 |
| `/sitemap-categories.xml` | 4 | 4 |
| `/sitemap-products.xml` | 42 | 41 |
| `/sitemap-live.xml` | **0** | 0 |
| `/sitemap-replays.xml` | **0** | 0 |
| **Total distributed** | **148** | — |

`/sitemap.xml` advertised all six children. `robots.txt:100` advertises exactly one
sitemap, `https://pulsesoc.com/sitemap.xml` — correct; a child should be reachable
through the index, not announced separately.

The two zero-URL children were not transiently empty. Both served the literal
two-line body `<urlset>\n</urlset>` (109 bytes).

---

## 3. P0 defect class found: an advertised partition that cannot ever carry a URL

`/sitemap-live.xml` and `/sitemap-replays.xml` drew every candidate path from the
`/arena` subtree. `services/search_visibility.py` classifies `/arena` as
`noindex,follow` — *"authenticated arena surface behind a redirect"* — because those
surfaces 302 anonymous traffic to `/login`. The eligibility gate therefore dropped
100% of both candidate sets, on every crawl, by design.

So the index made Google six promises and kept four. Each crawl of the index bought
two fetches that returned nothing.

`/sitemap-replays.xml` was additionally running, per crawl, a 200-row
`SELECT replay_token FROM arena_replays` whose result was then discarded by the
eligibility filter — through a connection closed outside any `finally`, so an
exception mid-render leaked it.

### Why this is a distribution defect and not a policy defect

The policy table is right: a surface that redirects anonymous traffic to `/login`
must not be indexable. The defect is that distribution kept advertising a partition
whose entire corpus the policy refuses. The fix therefore belongs on my side —
withdraw the promise — not in `search_visibility`.

### Blast radius: zero

148 distributed URLs before, 148 after. Deleting both routes removes no URL from
crawl distribution, because they distributed none.

---

## 4. Root cause of *survival*: four assertions that looked like six

A protection suite already existed for exactly this failure mode
(`tests/protection/test_sitemap_entries_are_indexable.py`). It did not catch this,
and the reason is structural rather than an oversight:

every assertion in the class iterates the collected entry list —

```python
for child, _loc, path in self.entries:
```

An empty child contributes zero entries, so **it is checked by none of them.** Four
invariants that appeared to cover six children covered four. The two broken children
were the only two invisible to the suite designed to find them.

This is the vacuous-pass failure mode, and the file already knew about it in another
place: `_served_disallows` carries `assert patterns, "the served robots.txt has no
Disallow lines to test"` for precisely this reason. The corpus guard existed for
robots.txt and not for the sitemap children.

---

## 5. Fix

Deleted `sitemap_live_xml()` and `sitemap_replays_xml()`. `SITEMAP_CHILDREN` went
from six to four, with the reason recorded in the source so a future agent does not
re-add an arena child without first changing the classification:

> *There is no arena child here. … The eligibility gate therefore dropped all of them
> and both routes rendered an empty `<urlset>` on every crawl. Re-adding an arena
> sitemap means changing that classification first; a sitemap cannot distribute a URL
> the policy table refuses.*

Both paths also came out of the `bot.py:3036` cache-policy tuple, where they were
dead entries.

### The guard that makes the gap non-recurring

```python
def test_no_advertised_child_sitemap_is_structurally_empty(self):
```

It asserts every child named in `SITEMAP_CHILDREN` contributes at least one entry,
and it **fails first**: run against the pre-fix tree it produced exactly two
subfailures, on exactly `/sitemap-live.xml` and `/sitemap-replays.xml`.

The hard part was the exemption. Two children are legitimately empty in a seedless
environment, and an assertion that cannot distinguish that from a real defect is the
cry-wolf gate that gets muted:

- **structural emptiness** — every candidate URL is ineligible. A defect.
- **data-driven emptiness** — no rows exist here. A statement about the environment.

`DATA_DRIVEN_CHILDREN = ("/sitemap-posts.xml", "/sitemap-categories.xml")`, justified
by measurement rather than by reasoning: booting the app on a seedless DB gives
pages=87 (matching production exactly), products=1, posts=0, categories=0. Pages and
products are therefore correctly **not** exempt — each emits at least one static path
regardless of the database, so for them empty is always real.

The exemption carries its own staleness guard: a name in `DATA_DRIVEN_CHILDREN` that
is no longer a child fails the test, because an allowlist entry for something that
does not exist is how an allowlist outlives its reason.

---

## 6. Proof

| Check | Result |
|---|---|
| Sitemap / indexability / merchant / registry / search_visibility | 252 passed |
| `route_auth` + parity gates | 17 passed |
| `mutate_sitemap_and_robots_agreement.py` | **24 killed, 0 survived**, 0 harness failures, of 24 |
| `mutate_marketplace_category_sitemap.py` | **11 killed, 0 survived** |
| Full protection suite | **786 checks across 57 suites passed** (exit 0) |
| `realtime_audio_change_gate.py --base origin/main --head HEAD` | no protected path changed (8 files inspected) |
| Distributed URL count | 148 → 148 |

### The survived mutation, and why it was retargeted rather than deleted

First post-fix harness run: 22 killed, **1 survived** — *"the arena stops being
classified, so its login redirects re-enter the sitemap."* The mutation was not
uncovered; it was **inert**. It had only ever been killed because
`/sitemap-live.xml` hardcoded `/arena/*`, so deleting that route removed its only
observer.

Per the brief — *fix the architecture, do not weaken the test* — I did not delete it.
I applied the mutation by hand, ran candidate killers, and empirically found the one
that catches it (`test_the_arena_subtree_is_not_indexable_but_its_public_siblings_are`,
4 subfailures), reverted, and retargeted `killed_by`. The invariant is still proven,
now by the test that actually owns it.

The new corpus guard was also given its own mutation, so it is provably non-vacuous
rather than merely green.

---

## 7. Deliberate non-actions

These are honest absences, recorded so nobody reads them as oversights:

- **No fabricated `lastmod` for the 87 page URLs.** They are static marketing pages
  with no material-change timestamp in the data. A deploy timestamp would be a lie
  told at scale, and a crawler that learns our `lastmod` is noise discounts it
  everywhere — including on the 60 product/post URLs where it is true.
- **No `<lastmod>` on the sitemap index entries.** Google documents index-level
  `lastmod` as a large-site benefit. At 148 URLs across 4 partitions it buys nothing
  and adds a second timestamp to keep honest.
- **No repartitioning.** 148 URLs is three orders of magnitude inside the 50,000-URL
  partition limit. Splitting for appearance is the mistake the brief warns about.
- **No image/video sitemap extensions.** Media eligibility is Agent 9's; I will wire
  distribution once eligibility is declared.
- **No second indexability engine.** Every child still filters through
  `search_visibility`.

---

## 8. Escalations

### → Agent 2 (with Agent 0): 59 of 87 page URLs are the retired crypto/sports product

Classifying the non-`/pulse` corpus:

```
87  /sitemap-pages.xml total
59  legacy crypto / sports-betting landing pages   (68%)
28  current product or neutral
```

The 59 include `/whale-tracker`, `/trending-crypto`, `/telegram-crypto-bot`,
`/btc-price-prediction`, `/crypto-scam-scanner`, `/ai-crypto-assistant`,
`/sports-betting-intelligence`, the 8 `/sports-edge/*` pages, `/quote/*`, and the 7
`/intel/*` articles. **40% of everything we ask Google to crawl is a product we no
longer sell.**

Meanwhile the 61 `/pulse` URLs are posts, categories and products only. Feed, reels,
profiles, stores and spaces contribute **zero** URLs, because those routes 302
anonymous traffic to `/login`.

This is Agent 2's call (page retirement and eligibility), not mine. I am reporting the
distribution consequence: the crawl budget is spent mostly on the old product, and the
new product is largely absent from distribution because it is behind a login wall.
I have not unilaterally changed it — dropping 59 URLs from a sitemap is a
de-indexing decision with revenue consequences and it needs an owner.

### → Agent 2 / Agent 8: the A12-02 and A12-04 paths are **not** in distribution today

Agent 12 flagged seven paths declared `sitemap_eligible=True` that should not be
(`/forgot-password`, `/forgot-username`, `/offline`, `/reset-pwa`,
`/scam-shield/scan`, `/pulse/help`, `/pulse/support`). I checked all seven against
the live 148-URL corpus: **none is distributed.**

The mechanism matters, because Agent 12 correctly called today's protection
accidental and I can now name it precisely: `seo/content.py:1114 all_public_paths()`
is a **hand-curated literal list**, not a `url_map` enumeration, and the eligibility
gate is applied as a *filter on top of it*. Policy declaring a path indexable can
therefore never by itself inject it into a sitemap.

Two consequences:

1. **The hazard is live for Agent 8, not for me.** An IndexNow submitter built to the
   frozen contract "submit `sitemap_eligible()` URLs" consumes the declaration
   directly, with no curated list in front of it. It will submit password-reset pages.
2. **If anyone "improves" crawl coverage by deriving the pages sitemap from
   `url_map`, all five A12-02 paths enter distribution in one commit.** They would
   not land silently — `test_no_sitemapped_url_serves_noindex` catches them, since
   each page saves itself with a hardcoded `noindex`. I am deliberately not adding a
   further "the corpus must stay curated" guard: that defends against a refactor that
   has not happened, and the existing test already covers the outcome.

### → Agent 12 / Agent 2: A12-06 is a pre-existing *arbitrated* decision, and the rationale rejects option A

Agent 12 reported `/pulse/marketplace` as a new defect — `sitemap_eligible=True` in
both states while the page serves `noindex,follow` on an empty catalogue — and asked
the owners to choose between (A) publish the storefront only when non-empty, or
(B) stop self-noindexing and accept the thin page.

That decision already exists in my lane, named and documented, at
`tests/protection/test_sitemap_entries_are_indexable.py:272`:

```python
NOINDEX_ALLOWED = ("/pulse/marketplace",)
```

with the recorded reason that **option A was considered and rejected**: keying the
hub's inclusion on a row count cannot distinguish an empty catalogue from a failed
query, so it would drop the commerce entry point from distribution whenever the
database hiccups. I concur, and from the distribution side the trade is lopsided:
option A risks self-de-indexing the storefront hub on a transient failure; the status
quo costs one crawl hit out of 148 on a page that declines indexing. **Recommend B
or status quo.**

Two corrections to A12-06 as filed: the carve-out means this is not invisible to the
test, and it is not an undeclared third authority — it is a declared exception with
one name in one place.

**Coupling to my new guard, for whoever closes this:** `/sitemap-products.xml` is
absent from `DATA_DRIVEN_CHILDREN` *because* `marketplace_public_entries` submits the
hub unconditionally, which is the same decision as the carve-out. If Agent 2 ever
takes option A, both change together — the carve-out goes away, and
`/sitemap-products.xml` must join `DATA_DRIVEN_CHILDREN`, or my guard will fail in
CI and describe data-driven emptiness as a structural defect. Fix it there; do not
soften the assertion.

### → Agent 0: `/sitemap-marketplace.xml` is declared but does not exist

`config/route_auth_baseline.json` carries a `sitemap_marketplace_xml` rule. No such
route exists anywhere in the repo, and no such endpoint answers in production. It is
baseline residue. I left it alone rather than regenerate — see §9.

### → Agent 2: the `?category=` query URLs are correct; do not "fix" them

The 4 category URLs in distribution carry a `?category=` query string. That is
deliberate and legitimate: each renders its own `h1`, `title` and meta through
`marketplace_storefront.render_discovery`, and each is self-canonical. A future pass
that strips query strings from sitemaps as hygiene would silently delete the entire
category layer from distribution.

---

## 9. Baselines touched, and the one I refused to regenerate

Deleting two routes invalidated four generated artefacts:

- `config/route_auth_baseline.json` — **hand-edited**, two entries plus `totals`
  (unknown 171→169, no-known-gate 160→158, rules 2121→2119). I ran the generator
  first and it produced +702/−62, absorbing hundreds of unrelated routes because the
  committed baseline is stale against `origin/main`'s own `bot.py`. That churn would
  have buried a two-line change and silently rebaselined other agents' lanes, so I
  reverted it. Deletion is safe to hand-edit: `test_no_route_loses_its_gate` records
  that *"deleting a route is not a security regression,"* and
  `test_no_route_family_vanished_wholesale` has a budget of 43.
- `scripts/parity/url_map_snapshot.json` — two path lines removed.
- `docs/parity/PULSESOC_WEB_PRODUCT_INVENTORY.md` — regenerated; diff is exactly
  −2 rules (2083→2081 booted, 1823→1821 static).
- `tests/test_sitemap_integrity.py` — `SITEMAP_ROUTES` tuple.

Agent 12's two mutation harnesses needed anchor updates, since three anchors pointed
into deleted code. `config/ci_test_manifest.json` needs no change: `tests/protection/`
is run by `protection.yml` and is deliberately in neither manifest list.

---

## 10. Invariants now held by the suite

1. Every child named in the index renders, and the index names every child.
2. No advertised child is structurally empty. *(new)*
3. No sitemapped URL is blocked by our own `robots.txt`.
4. Every sitemapped URL answers 200.
5. No sitemapped URL serves `noindex`, except one name carved out with its reason.
6. Every sitemapped URL declares itself canonical.
7. The exemption list for #2 cannot outlive the children it names. *(new)*

Each is backed by a mutation that kills it; 24 of 24 killed.

---

## 11. Handoff to Agent 0

Ready to merge. One commit, 8 files, +128/−95, no `origin/main` conflict at
`5bdf4e431`. Nothing in the P0 isolation set was touched: no Stripe, no Apple
capability, no Catalog Safety Gate, no held products, no platform fee, no shipping
rule. The audio gate inspected 8 files and found no protected path.

Open items are all other agents' decisions, listed in §8. The one I would sequence
first is the 59 legacy URLs — it is the largest single distortion in the crawl
distribution, and it is pure policy, so it needs no code from me.
