# AGENT 12 — SEARCH QUALITY / ADVERSARIAL SEO / CI SENTINEL

## Findings log (published as found, not held for the final report)

Status: **OPEN — 11 findings (1 low, 2 escalated out of Search OS), 8 attacks passed, 1 fleet blocker, 1 gate landed (red), 2 of my own claims corrected**

> **Read A12-11 first.** It is live in production, it is 78% of the product
> catalogue, and it is the only finding here with an immediate revenue cost.
Branch: `search-os/agent-12-quality-sentinel`
Measured against: `origin/main` @ `5bdf4e431`
Method: Flask test client over `app.url_map`, against a scratch copy of the dev DB
(serving a page writes `visitor_logs`, so probes must not touch the real DB).
Reproduce: `.attack/*.py` (probes + mutation harness), and
`tests/protection/test_every_page_agrees_with_the_robots_policy.py`

Note on numbers: probes run against a populated copy of the dev DB; the gate
under pytest runs against an empty fallback DB, because `tests/conftest.py:108`
redirects any in-repo DSN. Counts differ between the two, and A12-06 is why.

I am not the feature team for any of these. Each finding names the owning agent.
I will verify the fix and add permanent regression coverage; I will not patch
another agent's architecture.

---

## A12-00 — FLEET BLOCKER: there is no integrated Search OS to certify

Measured, not assumed:

| Branch | Commits ahead of `origin/main` |
|---|---|
| `search-os/agent-01-forensics` | 1 |
| `search-os/agent-02-url-indexability` | 1 |
| `search-os/agent-03` … `agent-10` | **0** (bare pointers at `origin/main`) |
| Agent 11 | **no branch exists** |
| Agent 0 | **no branch exists** |

Consequence: final certification is **BLOCKED**, and not because a test failed.
8 of 11 upstream surfaces I am required to consume contracts from do not exist
yet. Any "PASS" I returned today would be a pass over empty space.

What is genuinely unblocked, and what I therefore pivoted to:
1. Attacking the **already-shipped production** search engine — the substrate
   every other agent builds on. Every finding below comes from that.
2. The threat model and the cross-agent invariant matrix, which Agent 0 needs to
   freeze and which do not depend on Agents 3–11 existing.
3. The CI gate Agent 2 explicitly assigned me in its §6.3.

**Owner: Agent 0.** Do not read this as "Agent 12 found the fleet lazy" — read it
as "the certification gate cannot be scheduled before the surfaces land."

---

## A12-01 — `/pulse/cart` serves a stricter directive than policy declares

Agent 1 asserted this. It had no captured wire evidence. It is now proven.

```
path            /pulse/cart
status          200
policy          noindex,follow
served          noindex,nofollow
agrees          False
```

Direction is **safe** (the page is stricter than policy), so this is not a leak.
It is still a policy/delivery disagreement, and it is the same defect class as
A12-03 — the page restates a literal instead of calling `robots_meta()`.

**Owner: Agent 2.** Invariant: #7-adjacent (declared policy must equal delivered
directive).

---

## A12-02 — The policy-table fallthrough declares 5 non-public pages indexable **and sitemap-eligible**

`search_visibility.classify()` ends with `return _d(INDEX_DIRECTIVE, True, "public content")`.
Anything outside `/pulse` that nobody enumerated is therefore declared public.

```
/forgot-password     /forgot-username     /offline     /reset-pwa     /scam-shield/scan
```

For all five:

```
policy_directive  index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1
is_indexable      True
sitemap_eligible  True
robots_blocked    None
disallow_match    NONE
served_directive  noindex, nofollow        <-- the page saves itself
```

This is **a live hazard, not cosmetic.** Two reasons:

1. Today's protection is **accidental**. Nothing is indexed because the sitemap
   uses a hand-curated path list, not policy enumeration, and because each page
   happens to hardcode its own `noindex`. Neither is a guarantee.
2. Agent 2 froze **Agent 8's contract as "submit only `sitemap_eligible()` URLs."**
   An IndexNow submitter built correctly to that frozen contract will submit
   password-reset pages, the PWA offline shell, and the PWA reset route to
   Bing/Yandex. The contract is the bug's delivery mechanism.

Note also a spelling inconsistency in the same table: `/stripe-webhook` and
`/stripe/webhook` fall through to `index,follow`, while `/webhook/stripe` and
`/webhooks/stripe` are correctly `noindex,nofollow`.

The architectural question for the owner — and it is **not mine to decide** — is
whether the fallthrough should stay default-index. I only claim: the five paths
above are declared wrong today, and a downstream agent built to spec will act on
that declaration.

**Owner: Agent 2.** Notify: **Agent 6** (sitemaps), **Agent 8** (IndexNow).
Invariant: #4 (nothing non-public is declared indexable).

---

## A12-03 — 14 pages restate the robots literal and silently drop preview directives

Canonical value, single source of truth:

```
INDEX_DIRECTIVE = index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1
```

What is actually served:

| Path | Dropped |
|---|---|
| `/help` `/privacy` `/terms` `/support` `/pulse/help` `/pulse/support` | `max-snippet:-1`, `max-video-preview:-1` |
| `/advertising-policy` `/arena-preview` `/community-rules` `/creator-monetization-policy` `/enterprise` `/privacy-center` `/pro` `/trust-center` | `max-image-preview:large`, `max-snippet:-1`, `max-video-preview:-1` |

Root cause is four hardcoded literals, not fourteen bugs:

- `templates/support.html:9`, `templates/privacy.html:9`, `templates/terms.html:9`
  — `content="index, follow, max-image-preview:large"`
- `templates/seo_page.html:15` — `{{ robots or 'index, follow, max-image-preview:large, max-snippet:-1' }}`
- `bot.py:108648` — `content="index,follow"`, and it also builds its own canonical
  from `request.path`, bypassing `canonical_url()` entirely
- `templates/index.html:14` — hardcodes the full correct string. It agrees **by
  luck**, which is worse than disagreeing, because it will drift silently.

This is precisely the failure Agent 2 said must never recur, and `bot.py:2263`
documents it having already shipped once **at 42-URL scale** (`/markets/<symbol>`
and friends served `index, follow, ...` while policy said `noindex,follow`). That
docstring's own closing line is the fix pattern: *"The declared policy and the
delivered directive now come from one function."*

**Owner: Agent 4** (page-level metadata). Notify: **Agent 2** (policy authority).
Invariant: #7-adjacent.

---

## A12-04 — NEW: two `sitemap_eligible=True` paths point their canonical at a different URL

Not reported by Agent 1 or Agent 2.

```
_CANONICAL_ALIASES = {'/support': '/help'}

/support         served=https://pulsesoc.com/help  policy=https://pulsesoc.com/help          eligible=False  AGREES=True
/help            served=https://pulsesoc.com/help  policy=https://pulsesoc.com/help          eligible=True   AGREES=True
/pulse/support   served=https://pulsesoc.com/help  policy=https://pulsesoc.com/pulse/support eligible=True   AGREES=False
/pulse/help      served=https://pulsesoc.com/help  policy=https://pulsesoc.com/pulse/help    eligible=True   AGREES=False
```

`/support` is correct: it is a declared alias and it is `sitemap_eligible=False`,
so the alias and the eligibility agree.

`/pulse/support` and `/pulse/help` are the defect. They are **declared
sitemap-eligible while rendering a canonical that disowns them.** A sitemap entry
pointing its canonical at some other URL is exactly criterion 4 of the existing
`tests/protection/test_sitemap_entries_are_indexable.py` — but that test only
walks *curated sitemap entries*, so these two paths are invisible to it.

The fix belongs in `_CANONICAL_ALIASES` (one authority), not in the templates —
adding `/pulse/help` and `/pulse/support` there makes `canonical_url()` and
`sitemap_eligible()` agree in one move. Patching the templates would create a
third canonical authority, and there are already two.

**Owner: Agent 2.** Notify: **Agent 6**, **Agent 8**.
Invariant: **#7 (CANONICAL URL MUST AGREE).**

---

## A12-05 — 11 indexable pages send no robots directive at all

Found by enumerating `url_map` rather than by suspicion, so none of these were
on anyone's list.

```
/education   /education/optimism   /education/scam-alerts   /education/toncoin-scenarios
/legal/payments   /legal/refunds   /legal/seller-terms
/predictions/crypto   /quote   /roast-battle-preview   /sports-edge
```

All eleven: `200`, `meta robots = NONE`, `X-Robots-Tag = None`, policy
`index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1`.

Absence is not neutral — a crawler reads a missing directive as `index,follow`.
So indexability is accidentally correct, and all three preview directives are
silently lost. The three `/legal/*` pages are the marketplace's own terms,
refund and seller policies, which is where buyer-trust snippets come from.

**Owner: Agent 4.** Same single fix as A12-03: render `robots_meta(path)`.

---

## A12-06 — `/pulse/marketplace` picks its robots directive from the catalogue, and the sitemap does not know

The storefront's directive is **not a function of its path**:

```
populated catalogue   served  index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1   (27127 bytes)
empty catalogue       served  noindex,follow                                                             ( 8634 bytes)
policy (either way)           index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1
sitemap_eligible              True  <-- in BOTH states
```

The page is **right**. An empty storefront should decline indexing; that is thin
content. `robots_meta()` cannot express "depends on how many rows there are,"
so the page is a third, undeclared directive authority alongside the two
canonical ones.

The defect is the last line. Whenever the catalogue renders empty, we are
submitting a sitemap URL whose page tells Google to go away — precisely the
contradiction `tests/protection/test_sitemap_entries_are_indexable.py` exists
to prevent, and invisible to it because every environment anyone tests in has a
populated catalogue. Per memory, prod's marketplace is a **single seller**, so
an empty render is not a hypothetical state.

How it surfaced, which is worth recording: my gate reported this page as a
mismatch in CI but not locally. `tests/conftest.py:108` redirects any in-repo
`DATABASE_URL` to an empty fallback DB, so CI runs against an empty catalogue.
I had nearly written this off as gate flakiness. **A verdict that depends on row
counts is the Phase 100 failure mode** — it cries wolf, gets muted, and then
protects nothing.

**Owner: Agent 2** (eligibility) with **Agent 6** (sitemaps). Decide one of:
publish the storefront only when non-empty, or stop self-noindexing and accept
the thin page. Invariant: #4 and #7.

---

## Gate landed: `tests/protection/test_every_page_agrees_with_the_robots_policy.py`

Implements Agent 2 §6.3. Walks all 923 parameterless GET rules, judges the 55
that answer 200 with an HTML body. **No path is ever named** — a route leaves
the corpus only by ceasing to be an anonymously-readable HTML document. There is
no allowlist and there must never be one.

| Assertion | Status |
|---|---|
| corpus is not empty (floor 40 of 55) | **PASS** |
| corpus contains noindex-classified pages (anti-vacuity) | **PASS** |
| no page invites indexing the table wants kept out | **PASS** — the leak direction is clean |
| a noindex page sends a directive at all | **PASS** |
| no page declining indexing is offered to search engines | **RED — 6** (A12-02 ×5, A12-06 ×1) |
| pages agreeing on indexability send the same directives | **RED — 28** (A12-03, A12-05) |

Two design decisions worth challenging if anyone disagrees:

1. **`noindex` is normalized to `noindex,follow`** before comparison, because
   `follow` is the crawler default and `/pulse/app` sends the bare form. Preview
   directives are *not* defaulted — dropping `max-snippet:-1` really does change
   Google's behaviour.
2. **Exact path→directive agreement is not asserted** for pages whose
   indexability differs from the table, because A12-06 proves that comparison is
   data-dependent and would be flaky. The stronger, environment-independent
   invariant replaces it: *a page that declines indexing must not be
   sitemap-eligible.* That catches both A12-02 and A12-06 and names a fix rather
   than a discrepancy.

Failure messages name **which side to change**. This matters more than it
sounds: a naive "page must equal policy" gate would have instructed an engineer
to make `/forgot-password` indexable, and they would have been obeying it
correctly.

### Mutation proof (Phase 125)

`.attack/mutate_prove_safety_gate_bites.py` — the four green assertions are
worthless unless they fail when the defect appears, and an assertion in this
exact area has already survived a fix-revert once before.

```
victim /login  table says: noindex,follow
BASELINE  safety gate failures           0   (want 0)
M1        page advertises index,follow    1   (want >0)
M2        page sends no directive at all  1   (want >0)
BASELINE  sitemap-contradiction failures 5   (want >0, the live defect)
M3        sitemap_eligible forced False   0   (want 0 -- proves it reads the real fn)
VERDICT: gate bites
```

The baseline reading 5 rather than 6 is itself the A12-06 evidence: this harness
runs against the populated scratch DB, where the marketplace agrees.

### Why this is not merged to main yet

The two red assertions are red because of A12-02, A12-03, A12-05 and A12-06 —
defects owned by Agents 2, 4 and 6. Golden Rule 1 forbids weakening a test
because the implementation fails it, so **nothing here is allowlisted, skipped,
or xfailed.** The file sits on this branch, fully strict, with its red output
published above, and merges the moment the owners' fixes land. My brief's order
is explicit: publish evidence → owner fixes root architecture → verify → *then*
add permanent regression coverage. Merging it now would instead turn the shared
protection suite red for twelve other agents over defects none of them
introduced.

---

## Attacks that found nothing — recorded as passes

Negative results are results. I am not going to inflate these into findings,
and the next agent should not re-spend the time.

**URL-shape duplication: PASS.** 12 variants × 5 indexable pages = 60 probes,
**0 indexable duplicates with a wrong or missing canonical.** Trailing slash,
upper-case, title-case, `/.`, `/../`, `/index.html`, `;jsessionid=` and
`%2F` all 404 rather than minting a second copy. Tracking parameters
(`?utm_source=`, `?fbclid=`) and unknown parameters (`?sortby=`, `?ref=`)
correctly return 200 *with the canonical pointing back at the clean URL* —
which is the right call, since 404ing on a tracking parameter would break
inbound links.

**Host-header canonical injection: PASS.** 8 header combinations × 2 pages,
**0 poisoned canonicals.** `canonical` and `og:url` are built from a hardcoded
base, so `X-Forwarded-Host`, `X-Original-Host`, `X-Host` and `Forwarded` are all
ignored. A spoofed `Host` gets a 301 from the *application*, not just from
Railway's edge — worth knowing, because "the edge would catch it" is a
deployment detail and the edge is not in front of the test client. This is the
highest-severity SEO attack there is and it is properly closed.

**Double-slash reachability: PASS.** 0 host-relative double-slash URLs emitted
across four sitemaps, `robots.txt`, and five rendered pages.

**Facet and pagination explosion: PASS, and this one nearly became a wrong
finding.** `.attack/probe_canonical_page_and_category.py` and
`.attack/probe_facet_value_mitigation.py`.

`_CONTENT_QUERY_PARAMS = {"/pulse/marketplace": ("category",)}` whitelists a
parameter *name* and never inspects the *value*, so `search_visibility` answers
`sitemap_eligible=True` for **every** arbitrary `?category=` string and echoes
each one into its own canonical — `spam-casino-viagra`, a 200-character slug,
`../../etc/passwd`, `<script>alert(1)</script>`. That is an unbounded set of
self-declared-eligible URLs, and I had it written up as a finding before I read
`canonical_url`'s own docstring, which states the gap deliberately and names the
mitigation: *an unknown slug renders `noindex,follow` and canonicalises to the
bare hub, and the callers that submit URLs read the live taxonomy first.*

So the question was never "is the policy layer value-blind" — it is, in
writing. The question is whether those three promises hold. All three do:

| promise | how settled | result |
|---|---|---|
| submitters derive slugs from the live taxonomy | read `marketplace_seo.category_entries` — builds from `build_taxonomy()` over the public catalogue and iterates `taxonomy`, so no arbitrary string has a path in | holds by construction |
| an unknown slug canonicalises to the bare hub | measured, 7 hostile values | 7/7 collapse |
| an unknown slug serves `noindex` | measured **at the wire**, 7 hostile values | 7/7 `noindex,follow` |

With the control live: `?category=home` serves
`index,follow,max-image-preview:large,…` and keeps its own canonical, so the
probe can tell "unknown slugs are suppressed" apart from "every department is
suppressed" — the latter would have voided the whole facet strategy and been the
finding instead.

**Pagination: PASS.** `?page=999` clamps to `?page=2` rather than minting an
unbounded URL space; `?category=<slug>&page=2` composes both facets instead of
dropping either; `sitemap_eligible("?page=2")` is `False`, so a paginated URL is
never offered.

**Coverage, checked rather than assumed:**
`tests/test_marketplace_pagination_canonical.py:273` already pins the
load-bearing promise — `render(category="not-a-real-department").indexable is
False` — and that file is in `config/ci_test_manifest.json`'s `run` list (30
tests). It asserts at the *renderer*; my probe asserts at the *wire*; they
agree, so there is no renderer-to-HTTP gap hiding behind the gate. **No new gate
needed here.** This is the one place where the mitigation for a policy-layer gap
turned out to be both real and guarded.

What this does change is the *reading* of A12-02 and of matrix rows 4 and 12.
The policy layer's over-declaration is unbounded, not five pages. But the bound
on its consequence is tighter than I would have guessed: even if Agent 8's
IndexNow submitter enumerated eligibility and submitted
`?category=spam-casino-viagra`, Google would fetch it, read `noindex`, and drop
it. The cost is wasted crawl budget and quota, not an indexed doorway page. That
tempers A12-02 rather than escalating it, and I would rather say so than leave a
scarier number standing.

**Every submitted URL resolves for an anonymous crawler: PASS.**
`.attack/probe_sitemap_promises.py` walks `/sitemap.xml`, follows its children
rather than trusting a hardcoded list, and fetches all **206** offered URLs with
no session and no cookie — the only client whose opinion matters.

```
sitemaps reachable: 7   distinct URLs offered: 206
status distribution: {200: 206}
UNREADABLE ANONYMOUSLY : 0
SUBMITTED BUT noindex  : 0
CANONICAL POINTS AWAY  : 0
offered URLs that `search_visibility.sitemap_eligible` rejects: 0
```

A three-zero sweep is the exact shape that needs a control, so each detector was
given a live exemplar before I believed it: `/login` and `/pulse/cart` serve
`noindex` (the noindex detector fires), `/pulse/marketplace/110` answers 404 and
`/pulse/profile/2` 302s to `/login?next=…` (the unreadable detector fires).
Every one of those is a real path on this property, and none of them is offered.

Two structural facts worth freezing, both of which this measured rather than
assumed:

- **The offer set is a strict subset of the eligible set.** The generators use
  curated path lists and database rows, not policy enumeration. That is *why*
  A12-02 is harmless today — none of its five hazardous paths
  (`/forgot-password`, `/forgot-username`, `/offline`, `/reset-pwa`,
  `/scam-shield/scan`) is offered by any sitemap — and it is also exactly why
  A12-02 is dangerous tomorrow, because Agent 8's IndexNow submitter is frozen
  to the contract *"submit only `sitemap_eligible()` URLs"* and would enumerate
  the policy instead. The protection is incidental, not architectural.
- **A12-04 is latent at the offer layer.** `/pulse/help` and `/pulse/support`
  declare themselves sitemap-eligible while rendering a canonical that disowns
  them, but neither is offered — and neither is `/help` itself. The defect is
  live at the policy layer and unexpressed at the offer layer.
- **A12-06 is not latent.** `/pulse/marketplace` *is* offered, by
  `/sitemap-products.xml`. So the one page that picks its robots directive from
  the row count is a page we actively submit, which is what makes that finding
  worth more than its row count suggests.

Note the 206 against Agent 2's 61 for the same property. That gap is A12-09.

**Agent 1's listing claims: 2 CONFIRMED, 1 unverifiable, 1 missed — and the
product-page layer is clean.** `.attack/probe_verify_agent1_listing_claims.py`
walks all 16 publishable rows rather than only the three Agent 1 named, and puts
the three authorities side by side.

| listing | HTTP | served | in sitemap | `eligibility.indexable` | reason |
|---|---|---|---|---|---|
| 50 | 200 | `noindex,follow` | no | False | description under 40 chars |
| 52 | 200 | `noindex,follow` | no | False | description under 40 chars |
| 77 | 200 | `noindex,follow` | no | False | description under 40 chars |
| 110 | **404** | — | no | — | not publishable (no row) |
| 35, 36 | 200 | `index,follow` | yes | True | price disagreement (feed only) |
| other 11 | 200 | `index,follow` | yes | True | complete |

- **50 and 52: confirmed.** Agent 1 was right, and now there is wire evidence.
- **110: I was wrong, Agent 1 was right.** See the correction below.
- **77 has the identical shape locally and Agent 1 did not name it.** That
  reading does not survive production either — see the correction.
> ### CORRECTION — the table above is a dev-copy reading, and two of its rows are wrong
>
> Settled against production on 2026-10-03, read-only:
> `.attack/prod_settle_listing_110.py` and `.attack/prod_read_listing_directives.py`.
>
> | listing | prod DB | prod wire | my dev-copy claim |
> |---|---|---|---|
> | 50 | `published` | 200 `noindex,follow` | correct |
> | 52 | `published` | 200 `noindex,follow` | correct |
> | 110 | `published` | **200 `noindex,follow`** | **wrong** — I said 404, "no row" |
> | 77 | `published` | **404** | **wrong** — I said 200 `noindex` |
> | 35, 36 | `published` | 200 `index,follow` + preview directives | correct |
>
> **Agent 1's claim about 110 was right in every particular and I called it
> unverifiable.** The dev copy holds 16 publishable rows against production's
> 196; listing 110 is simply not in it. *A claim about a row cannot be refuted by
> a database that does not contain the row*, and I published a refutation that
> had exactly that shape. The control was there to be run and I did not run it:
> 50 and 52 resolved locally, so I treated the corpus as adequate instead of
> asking whether 110's absence was the property's answer or my copy's.
>
> The 77 inversion is not a copy artifact — it is A12-11 below, and it is the
> more serious of the two.
>
> What stands: the three listings Agent 1 named all serve `noindex,follow` in
> production, with a live control (35 and 36 serve `index,follow`), so the
> per-listing split is real and observable from outside. What falls: my "sample,
> not a census" criticism of Agent 1's section. On production evidence Agent 1's
> three were three of three.

- **0 cross-authority disagreements.** Every row's served directive, sitemap
  membership and `eligibility` verdict agree — `in_sitemap == indexable` for all
  16. This is the pattern A12-04 and A12-06 violate at *path* level, and at *row*
  level it is exactly right. Worth saying plainly: the product-page layer is the
  part of this engine that already does what my gate asks for.

Note 35 and 36 serving `index,follow` while excluded from the feed is correct,
not a fault — `Eligibility(indexable=True, feed_eligible=False)` is the designed
answer for a price disagreement, and A12-08 is about it being unlogged, not about
it being wrong.

**Facet and pagination explosion: PASS.** 27 variants against
`/pulse/marketplace`, **0 indexable self-canonical doorways.**
`marketplace_storefront.render_discovery:1031` lists five `noindex,follow`
conditions and all five hold on the wire: a search URL (`?q=`), page ≥ 2, a
category slug no listing carries, a failed catalogue read, and an empty
catalogue. Everything else canonicalises to the hub or to the one real
department URL — `?page=0`, `?page=-1`, `?page=abc`, `?page=2.5`, `?sort=junk`
and `?category=` all point home; `?category=WOMENS-CLOTHING` and
`?category=a&category=b` both point at the real lowercase single-parameter URL.

The important detail is *how* the junk-slug space is closed. It is not closed by
the row-count rule from A12-06 — I assumed it was, and that assumption was
wrong. `known_category` is tested against the taxonomy, and the code says why it
is tested that way rather than against the result count: *"so a real-but-currently
-empty department still stays indexable."* So the two rules are deliberately
decoupled, and **fixing A12-06 cannot open a facet doorway.** Worth stating
explicitly, because that coupling is what I went looking for.

Thin departments *are* indexable and self-canonical — four of the eight hold one
or two products. That is a documented judgement, not an oversight:
`CATEGORY_MIN_INDEXABLE_LISTINGS = 3` withholds them from the sitemap while
leaving them linked and crawlable, and the constant's comment records the
production distribution it was chosen against. Disagree with the threshold if
you like; it is a deliberate, reasoned position and not a defect.

Two corrections to my own probe, recorded because both initially read as clean
passes (Phase 100 — a probe that cannot fail is not evidence):

1. It passed the raw `category` *label* (`Beauty`) instead of the *slug*
   (`health-beauty-hair`). Every real facet came back `noindex`, which looked
   like a closed facet space and was actually a zero-row filter triggering a
   different rule entirely. Caught because the control reported 0 products while
   still declaring itself indexable — an impossible combination.
2. Its self-canonical test used a substring match, which flagged
   `?category=a&category=b → ?category=a` as a doorway. That is the *correct*
   canonical for a duplicate. Full-equality against origin + URL was the fix,
   and it took the count from 1 false positive to 0.

**Price-truth guard: PASS, and it is the best-defended thing I attacked.** Four
attacks, all held:

1. *Can the page publish a number the feed refused?* No. The product page's
   price pill and its `Offer` are both `marketplace_web.PriceView` from
   `derive_price`, so they cannot disagree with each other, and
   `marketplace_seo.public_price` applies the same contradiction refusal the
   feed does — a contradicted row renders with no price pill and a `Product`
   node carrying no `Offer`, rather than printing a number.
2. *Can the discovery cards disagree with the product page?* No. Both price
   through `derive_price`; the module docstring names the grid-vs-API split as
   the defect it was written to end.
3. *Can the feed and the page judge the contradiction against different variant
   sets?* No — and this was the attack I expected to land. All five variant
   loads in the codebase (`bot.py:32585`, `60905`, `61302`, `61730`, and the
   feed's) go through the single `marketplace_storefront_variants` loader, so
   there is no second filter to drift.
4. *Is the guard's fail-open reachable on a live surface?* No.
   `price_label_contradicts_variants` returns `False` when `variants` is absent,
   which is a real fail-open, but the only loader feeding both the sitemap and
   the feed populates the key unconditionally. Both test files
   (`tests/test_marketplace_seo.py`, `tests/test_merchant_center_feed.py`) are
   in `config/ci_test_manifest.json` and both pin the fail-open deliberately.

There is a dead rollback renderer, `bot.py:_marketplace_public_product_response`,
that *would* reintroduce the split — it states a price through `public_price`
and cannot render a range at all. It has no callers and its own docstring warns
against reaching for it. Noted, not filed: an uncalled function is not a defect.
If it is still there next release, delete it.

---

## A12-07 — LOW/latent: two slash-merge redirects 301 into a 404

The slash-merge handler drops the first path segment instead of collapsing the
slashes:

```
//help                301 -> https://pulsesoc.com/              -> 200   (page lost, lands on root)
//terms //privacy //enterprise   301 -> https://pulsesoc.com/   -> 200   (same)
//pulse/marketplace   301 -> https://pulsesoc.com/marketplace   -> 404   DEAD END
//pulse/cart          301 -> https://pulsesoc.com/cart          -> 404   DEAD END
```

`//X/Y` becomes `/Y` and `//X` becomes `/`. The correct target is `/X/Y`.

**Severity is low and I want to be explicit about why:** nothing we emit
contains a double-slash self-path (0 across four sitemaps, `robots.txt` and five
pages), so no crawler reaches these from our own markup. It needs a malformed
external inbound link. Listed because a 301 landing on a 404 destroys whatever
equity the source had, and because `//help → /` is a soft-404 pattern — but it
should not jump any queue.

**Owner: Agent 2.** Invariant: #7.

---

## A12-08 — A product leaving Shopping leaves no trace; only the unreachable failure is logged

I set out to attack the price-truth guard and could not break it (see the pass
record below). What broke instead is the *observability* around it, and it is
one line.

`merchant_center_feed.feed_xml` handles two kinds of omission and logs only one:

```python
try:
    row = feed_row(listing)
except Exception:
    logging.exception("MERCHANT_FEED_ROW_FAILED listing_id=%s; ...")   # loud
    continue
if row is None:
    continue                                                           # silent
```

Measured on the wire — `.attack/probe_feed_exclusion_is_silent.py`, root logger
captured at DEBUG, one row per shape:

| row shape | in feed | log lines | reason |
|---|---|---|---|
| A complete | Y | 0 | `complete` |
| B price label contradicts variants | **N** | **0** | `price_label disagrees with variant prices` |
| C description under 40 chars | **N** | **0** | `description under 40 chars` |
| D availability with no feed spelling (control) | N | **1** | `MERCHANT_FEED_ROW_FAILED` |

The asymmetry is the wrong way round. D is the case the module says it cannot
reason about, and on production data it happens never — `availability` maps
every value the lifecycle can produce, which is why my first attempt at this
control sailed straight into the feed and proved nothing. I had to monkeypatch
`marketplace_seo.availability` to reach that branch at all. B and C are the
cases that happen *now*, to **7 of the ~35 publishable rows** — the five price
contradictions plus the two the module's own docstring counts as having no
description — and they are the silent ones.

So roughly a fifth of the catalogue is in Search and out of Shopping, and
nothing in the platform's telemetry says so. `feed_row`'s docstring states the
hazard in as many words — *"a silent skip is how a product leaves Shopping with
no trace"* — in the comment above the `raise` it added to avoid it, two
branches above the `return None` that does it.

This is why the price disagreement in the carried-forward list was found by a
human reading the feed rather than by an alert. Phase 100 in reverse: a gate
that never fires is not quiet because things are fine.

Not proposing a log line per row — a 500-row feed would emit 500 lines a crawl
and become its own noise. The honest shape is one aggregate per build
(`eligible/total` plus a count by `Eligibility.reason`, which already carries
the string), so the number is trendable and a jump is visible.

**Owner: Agent 6** (Merchant/feed), with Agent 5 for the underlying price data.
Invariant: price truth must be *observable*, not merely enforced.

---

## A12-09 — Agent 2's sitemap verifier is pointed at 3 of 6 sitemaps and reports 61 entries for a 206-entry property

> **Read the CORRECTION at the end of this finding before acting on it.** The
> headline `0 → 13` was measured correctly and framed wrongly; the honest number
> is `0 → 1`, and the coverage I implied was missing turned out to already exist
> and already run on every PR. Left in place with the correction appended.

`scripts/search_os/verify_sitemap_vs_live.py` (Agent 2, `80c057163`) reports
*"all 61 sitemap entries resolve 200, index,follow and self-canonical"* and
prints `TOTAL FAULTY: 0`. I walked the same property and found **206** offered
URLs. Both numbers cannot describe the same set, and the cause is one line:

```python
default="sitemap-products.xml,sitemap-categories.xml,sitemap-posts.xml",
```

Three filenames, hardcoded, with **no discovery step** — the script never fetches
`/sitemap.xml` to ask what the index actually offers.

```
/sitemap.xml advertises 6 child sitemaps:

  /sitemap-pages.xml          87 URLs   <<< NOT CHECKED
  /sitemap-posts.xml         104 URLs   IN scope
  /sitemap-categories.xml      1 URLs   IN scope
  /sitemap-products.xml       14 URLs   IN scope
  /sitemap-live.xml            0 URLs   <<< NOT CHECKED
  /sitemap-replays.xml         0 URLs   <<< NOT CHECKED

offered in total   206
Agent 2 checks     119
never checked       87   (42% of the property)
```

That would be a scoping nit if the excluded sitemaps were clean. They are not,
and the proof deliberately uses **Agent 2's own fault classes, not mine** —
`.attack/probe_agent2_verifier_scope.py` reuses `inspect()`'s checks and their
exact names against the full offer set, so the only variable is scope. Writing
my own checks would merely have proven that two people wrote different checks.

```
faults inside Agent 2's scope  :  0     <-- this is the 0 it reports
faults it never looks at       : 13

  /arena-preview                     NO_CANONICAL
  /learn/arena-ranking-system        NO_ROBOTS_DIRECTIVE
  /learn/crypto-risk-management      NO_ROBOTS_DIRECTIVE
  /learn/crypto-scams                NO_ROBOTS_DIRECTIVE
  /learn/crypto-trading-simulator    NO_ROBOTS_DIRECTIVE
  /learn/how-to-detect-phishing      NO_ROBOTS_DIRECTIVE
  /learn/market-psychology           NO_ROBOTS_DIRECTIVE
  /learn/roast-battle-rules          NO_ROBOTS_DIRECTIVE
  /predictions/crypto                NO_ROBOTS_DIRECTIVE
  /quote                             NO_ROBOTS_DIRECTIVE
  /quote/crypto/BTC                  NO_ROBOTS_DIRECTIVE
  /quote/crypto/ETH                  NO_ROBOTS_DIRECTIVE
  /sports-edge                       NO_ROBOTS_DIRECTIVE
```

**Credit where it is due: the twelve are known.** Agent 2's commit message says
so — *"The twelve legacy crypto pages missing a robots directive are left alone
… de-indexing indexed, sitemapped pages is not a call an SEO change gets to make
by itself."* That is a correct and well-reasoned deferral, and my count landing
on exactly twelve is independent confirmation of it. Three of them
(`/predictions/crypto`, `/quote`, `/sports-edge`) are also A12-05 members, which
upgrades part of A12-05 from latent to **actively submitted**.

So the finding is not "Agent 2 missed defects." It is narrower and worse:

1. **The script reports `TOTAL FAULTY: 0` while thirteen submitted URLs trip its
   own fault classes.** Phase 100: a gate whose default scope excludes the
   defects its author documented is not a gate. The fix is discovery, not a
   longer default — read `/sitemap.xml` and walk whatever it advertises, so a
   sitemap added next quarter is covered the day it ships.
2. **`/sitemap-live.xml` and `/sitemap-replays.xml` are empty today**, so their
   exclusion costs nothing right now. They are also the two sitemaps for the
   content type most likely to grow, and they would be born unchecked.
3. **`/arena-preview` is new.** It is not one of the twelve, nobody has recorded
   it, and it is a different and more actionable fault.

### CORRECTION (same session, before this was acted on) — I overstated this, twice

Published above, then checked. Both corrections cut **against** my own finding
and are recorded in place rather than quietly edited, for the same reason A12-09b
records its predecessor.

**Correction 1 — the coverage I implied was missing already exists, and runs.**
I wrote that Agent 2's script "is the artifact most likely to become the shared
CI gate" and that the twelve "will keep a green check over them." That was
asserted, not measured. In fact `tests/protection/test_sitemap_entries_are_indexable.py`
is **already landed** (`cef8f50f3`, PR #151) and already does end-to-end what
Agent 2's script does — and does it better:

- its corpus is **all six** children via `bot.SITEMAP_CHILDREN`, measured at
  **206 entries**, the same number I found independently;
- `test_the_sitemap_index_lists_every_child_and_each_one_renders` derives the
  expected child list from **`url_map`, not from the constant**, with a docstring
  recording that comparing against `SITEMAP_CHILDREN` was circular and survived a
  mutation. That is exactly the discovery discipline I was about to recommend —
  already implemented, one directory over;
- it asserts 200, not-`noindex`, canonical-agreement and not-robots-blocked;
- `scripts/protection/run_protection_suite.py` **globs** `tests/protection/test_*.py`,
  so it is picked up by existing, and it runs from the `backend` job of
  `.github/workflows/realtime-audio.yml` — which has **no `needs: detect` and no
  path filter**, so it runs on every PR to every branch. I checked this
  specifically because a suite that only runs when audio files change would have
  made the coverage nominal. It isn't.

So the real consequence of A12-09 is **"a redundant artifact publishing a
misleading number" (61 vs 206), not "13 faults reach production unchecked."**
Still worth fixing — two numbers in two places for one property is how a team
learns to trust the wrong one — but materially less severe than I wrote.
*(Aside: CLAUDE.md names `.github/workflows/protection.yml`, which does not
exist. The suite lives in `realtime-audio.yml`. Doc drift, not a defect.)*

**Correction 2 — 12 of my 13 "faults" are not sitemap faults at all.** I let
`0 → 13` stand as the headline. But `NO_ROBOTS_DIRECTIVE` means *absence* of a
robots meta, and absence reads as `index,follow` — which is precisely what a
submitted URL should be. For a sitemap corpus that classification is wrong, and
the landed gate is right not to carry it. My own A12-03/A12-05 case for those
pages is about **dropped preview directives**, which costs rich-result
eligibility — a real but much smaller harm than "submitted and unindexable." The
honest headline is **0 → 1**.

**What survives, and it is the sharper finding:** that one is `/arena-preview`,
and it is uncovered by *both* scripts. The landed gate skips it **deliberately**:

```python
match = RE_CANONICAL.search(resp.get_data(as_text=True))
if not match:
    continue  # absent canonical is a different (weaker) finding
```

Mutation-proved rather than argued — in a throwaway copy, replacing that
`continue` with an `offenders.append` turns the landed gate red with **exactly
one** offender:

```
AssertionError: Lists differ: [] != ['/sitemap-pages.xml: /arena-preview has NO canonical at all']
```

One of 206, found independently by the gate's own regex (which, unlike my first
attempt, handles both attribute orderings). **The `continue` is the whole
defect, and its comment is the thing to disagree with:** an absent canonical is
not weaker than a mismatched one. A mismatched canonical folds a page onto one
wrong URL — bounded, one duplicate. An absent canonical on an indexable page
folds nothing, so every tracking-parameter variant is a separate document
(A12-09b) — unbounded.

**Fix is two lines, and they must land together:** give `/arena-preview` a
self-canonical, *and* delete that `continue` so the next page born without one
cannot pass. Either alone is unstable — the tag without the gate rots on the next
page added, and the gate without the tag reddens CI on main.

### A12-09b — `/arena-preview` is a submitted, indexable page with no canonical, and it mints unbounded duplicates

Not a missing *directive* — a missing *canonical*. The string `canonical` appears
**zero times** in its 14,978-byte body. It is offered by `/sitemap-pages.xml` and
serves `index,follow`. Consequence, measured:

```
/arena-preview                        200  robots='index,follow'  canonical=None
/arena-preview?utm_source=newsletter  200  robots='index,follow'  canonical=None
/arena-preview?fbclid=abc123          200  robots='index,follow'  canonical=None
/arena-preview?ref=partner            200  robots='index,follow'  canonical=None
/arena-preview?gclid=xyz              200  robots='index,follow'  canonical=None

/quote   (no robots directive, A12-05)  canonical=https://pulsesoc.com/quote   <-- correct
/help    (truncated directive, A12-03)  canonical=https://pulsesoc.com/help    <-- correct
```

Every tracking-parameter variant is a distinct indexable document with nothing to
fold it back. That is an unbounded duplicate set on a URL we submit ourselves,
and it is the one page of 206 where this is true — `/quote` and `/help` both
canonicalise home correctly despite carrying defects of their own.

**This also corrects my own URL-shape PASS.** That attack reported 12 variants ×
5 indexable pages = 0 uncanonicalised duplicates, and the result was true of the
five pages it sampled. `/arena-preview` was not among them. The pass was
correctly measured and too narrowly scoped; the honest version is *"tracking
parameters are handled correctly wherever a canonical exists."* Recorded rather
than quietly amended, because a pass with the wrong corpus is the same failure
mode as a gate with the wrong scope — which is the finding above.

**Why nothing caught this:** the landed gate skips absent canonicals by an
explicit `continue  # absent canonical is a different (weaker) finding`, and
Agent 2's script never looks at `/sitemap-pages.xml`. Both are shown, with the
mutation proof, in the CORRECTION under A12-09. Two independent scopes, one
shared blind spot — which is a better argument for the two-line fix than either
finding on its own.

**Owner: Agent 2** (the verifier's scope, and `/arena-preview`'s canonical).
Notify **Agent 6** (sitemaps), **Agent 8** (IndexNow — it would submit all 206).
Invariant: **#7 (canonical must agree)** and **#13 (a gate must see the whole set
it certifies)**.

---

## A12-10 — `/api/pulse/payments/checkout` takes the price from the buyer's own request body for `lesson` and `live_class`

**Severity: HIGH as written, latent today (0 production rows). Not a Search OS
defect — escalated to Agent 0 for routing to the payments owner.**

Found while grounding invariant #14 ("the price charged equals the price
displayed") for the cross-agent matrix. The route accepts four `item_type`
values and prices them from three different places (`bot.py:104190`–`104231`):

| `item_type` | priced from |
|---|---|
| `marketplace_product` | `marketplace_price_authority.resolve_for_listing(...)` — correct |
| `course` | `parse_price_label_to_cents(item["price_label"])` — **the DB row** |
| `lesson` | `parse_price_label_to_cents(payload["price_label"])` — **the request** |
| `live_class` | `parse_price_label_to_cents(payload["price_label"])` — **the request** |

`payload` is `request.get_json(silent=True)` (`bot.py:104081`). The last two load
their row from the database one line earlier and then never consult it for
price.

**This is not two price authorities drifting apart.** For these two item types
there is no server-side price to drift *from* — the tables have no price column
at all. Confirmed against the **production** schema, not just `init_db()`:

```
pulse_lessons          rows=     0  price-like columns=NONE
pulse_live_classes     rows=     0  price-like columns=NONE
pulse_courses          rows=     0  price-like columns=['price_label']
```

Measured, not read. `.attack/probe_checkout_price_is_client_supplied.py` drives
the live route with a seeded class, lesson and course and captures the number on
its way to Stripe:

```
live_class   buyer sends price_label=$0.50      -> HTTP 200; Stripe asked for unit_amount=50
live_class   buyer sends price_label=$5000.00   -> HTTP 200; Stripe asked for unit_amount=500000
lesson       buyer sends price_label=$0.50      -> HTTP 200; Stripe asked for unit_amount=50
lesson       buyer sends price_label=$5000.00   -> HTTP 200; Stripe asked for unit_amount=500000
course       buyer sends price_label=$0.50      -> HTTP 200; Stripe asked for unit_amount=25000
course       buyer sends price_label=$5000.00   -> HTTP 200; Stripe asked for unit_amount=25000
```

**The `course` row is the control, and it is the reason this reading is
trustworthy.** It takes the identical payload field and ignores it, charging its
true DB price (`$250.00`) both times. If a changed payload had moved *both*, the
probe would be reading a field that happens to be echoed rather than a price
authority. It moves exactly the two branches the source says it should.

Cross-checked against a second, independent reading: `seller_transactions` is
INSERTed with `amount_cents` *before* the Stripe guard, and it recorded the same
50 / 500000 / 25000. The ledger is cleared in `seed()` so the rows provably
belong to the current run — the first version of this probe did not do that and
reported four convincing amounts left over from a previous 503 run.

**Exploitable window: `[50, 99_999_999]` — $0.50 to $999,999.99, buyer's
choice.** `MAX_PRICE_LABEL_CENTS` (`bot.py:5430`) clamps the top and
`DEFAULT_MINIMUM_CHARGE_MINOR = 50` is the Stripe floor. Note the clamp protects
against *over*-charge only; the direction an attacker wants is down, and 50
cents passed with HTTP 200. There is no downstream recompute — the only
subsequent checks are `amount_cents <= 0` and `below_minimum_charge_error`, and
the value reaches both the ledger insert and the Session's `unit_amount`
(`bot.py:104643`). A comment in the route confirms the reach in as many words:
*"the Session above is created for every item_type this route accepts"*.

### What is honestly NOT proven

- **Nothing has been charged this way.** Production `seller_transactions` holds
  `marketplace_product` rows only — 47 across 5 statuses, no `lesson`,
  `live_class` or `course` row has ever existed.
- **There is nothing to buy.** All three teaching tables are empty in
  production; the teaching product has never launched a row.
- A buyer must be authenticated (`api_error("Login required.", 401)`), and
  `ios_native_app_request()` refuses every non-`marketplace_product` type — so
  the native app cannot reach it. Web/API only.
- No first-party client POSTs these item types, so a buyer would be
  hand-crafting the request.

So: **latent, and it arms itself on the first row.** The defect is in the route,
which ships today — not in the data, which is absent today. That ordering is
what makes it worth publishing now: the fix is one line per branch while the
tables are empty, and a refund reconciliation afterwards.

### Two stubs, and why neither manufactures the result

The probe patches three things, all restored in `finally`, with no source edits:

1. `api_account_user` — the buyer. Stubbed because the hole is *behind* the login
   wall: it is reachable by **any** logged-in buyer, and stubbing isolates the
   price question from the access question.
2. `approved_teacher_for_user` — worth stating precisely, because it looks like a
   gate and is not. It is called as
   `approved_teacher_for_user(cur, seller_user_id)` (`bot.py:104274`): a property
   of the **seller**, not a barrier the buyer passes. Stubbing it asserts "this
   class belongs to an approved teacher," which is true of every genuinely listed
   class. It does not create reachability.
3. `STRIPE_SECRET_KEY` — a configuration constant, not a price authority. Without
   it the route returns 503 before building the Session, so the amount could not
   be observed at all. `Session.create` is already replaced with a capture, so no
   key exists, no network call is made, and no money can move.

**Nothing here touched production, Stripe or money.** The DB is
`.attack/scratch.db`; the production query above was `readonly=True`.

### Secondary gap found in the same read

The lifecycle check is marketplace-only:
`if item_type == "marketplace_product" and not marketplace_listing_lifecycle.is_public(item)`
(`bot.py:104236`). So for `lesson` and `live_class` a row in **any** status —
`draft`, `cancelled` — is purchasable. The same is true of the goods-policy
check. My probe seeded `scheduled`/`published`, so this is read from source, not
measured; flagging it so the owner fixes the branch rather than the line.

**Owner: payments surface, not a Search OS agent.** Escalated to **Agent 0**.
Invariant: **#14 (the price charged equals the price displayed)** — the one
invariant in the matrix whose violation costs money rather than crawl budget.
Golden Rule 2 names price truth explicitly, which is why a payments finding
belongs in a search-quality report at all.

---

## A12-11 — 152 of 196 published products answer 404 in production, because the public gate reads a stock column the supplier sync does not write

**This is live right now, it is 78% of the catalogue, and it is the largest
discoverability defect on the property.** Not latent, not a crawl-budget
nuance: 152 approved, published, genuinely in-stock products have no reachable
URL.

Found by accident. Production says listing 77 is `published`; its page answers
**404**. 50, 52 and 110 are also `published` and answer 200. So `status` is not
what decides, and `marketplace_listing_lifecycle.public_sql()` is a five-clause
conjunction where any one clause retires the URL. Evaluating them one at a time
(`.attack/prod_why_does_77_404.py`) gives a single answer:

```
  id=  50 status=published approval=approved seller=approved qty=14126 type=physical
       failing clauses: none -- all pass
  id=  77 status=published approval=approved seller=approved qty=None  type=physical
       failing clauses: ['stock or intangible']
  id= 110 status=published approval=approved seller=approved qty=14880 type=physical
       failing clauses: none -- all pass
```

`COALESCE(l.quantity,0) > 0`. Listing 77's `quantity` is `NULL`, the predicate
reads null as zero stock, and the route turns zero stock into a 404.

### The population (`.attack/prod_stock_null_population.py`)

```
  published + approved + seller approved        196
      quantity IS NULL                          148
      quantity = 0                                4
      quantity > 0                               44
  RETIRED BY THE STOCK CLAUSE (404 on the wire) 152
```

### It is not out of stock. There are two stock columns and the gate reads the wrong one

This is the part that makes it a defect rather than a shelf state. Stock lives
in two places, and `.attack/prod_stock_authority_and_offer_set.py` asks which:

```
  marketplace_listing_variants   rows=3797  key=listing_id  stock-like=['stock_quantity', 'stock_state', 'stock_synced_at']
      retired listings carrying stock_quantity>0 here: 152 of 152
```

**All 152 of 152.** They collectively hold **43,380,177 units**, every one of
the 3,797 variant rows reads `stock_state = IN_STOCK`, and `stock_synced_at`
across the retired set spans a 90-minute window ending minutes before I
measured. The supplier sync is running, current, and writing real stock — into
`marketplace_listing_variants.stock_quantity`. The public gate reads
`marketplace_listings.quantity`, which for 148 of 196 rows nobody writes at all.

And the listing-level column is not a stale roll-up of the variant data — I
checked, because "the roll-up broke" and "the column was never on this path"
have different owners and different fixes
(`.attack/prod_two_stock_authorities.py`):

```
  across ALL survivors: 11 of 44 have quantity == sum(variant stock)
         35          55        42      523654 !=
         36          55        95     1191446 !=
```

So on 33 of the 44 rows that *do* carry a listing-level quantity, the number
bears no arithmetic relation to the variants beneath it. I am not going to
guess its intended semantics from two samples. What is measured is enough:
**the column the public predicate depends on is not maintained by the system
that owns stock**, and where it has a value that value disagrees with the
authority three times out of four.

### What it costs, stated honestly

- **152 product pages return 404 to every buyer and every crawler.** A buyer
  following a link from anywhere — a share, an email, an older index entry —
  gets a dead page for a product with 43 million units behind it.
- **The catalogue Google can see is 41 products, not 196.** The sitemap offers
  41 product URLs, and that is *correct behaviour for a broken input*: the
  generator applies the same predicate, so it honestly declines to submit a URL
  that would 404. Invariant 9 is not violated. The offer set is truthful about a
  catalogue that is 78% unreachable.
- **404 is also the wrong answer for the 4 rows that genuinely read zero.**
  Google's documented guidance for a temporarily out-of-stock product is a 200
  with `availability: OutOfStock`, not a 404 — a 404 retires the URL and
  discards whatever equity it had, so the page restarts from nothing when stock
  returns. That is a secondary point and I am keeping it secondary: with 148 of
  the 152 holding live stock, "we 404 out-of-stock products" is not the finding,
  it is the smaller half of it.

### Why nothing caught it

Every layer is individually consistent, which is the shape this whole report
keeps running into. The predicate does what it says. The route does what the
predicate says. The sitemap generator applies the same predicate and so never
offers a URL it cannot serve — the gate for invariant 9 passes **because** the
defect is upstream of the offer set. A test that asks "is every offered URL
reachable" gets a clean 148/148. No test asks the inverse: *is every publishable
product offered?* That question has no owner and no coverage, and it is the only
one that would have seen this.

The dev copy cannot see it either. It holds 16 publishable rows, all with
`quantity` populated, so locally the clause never fires. This is the same
corpus gap that made me wrongly refute Agent 1 on listing 110, two findings
apart — and it is the second time this round that a conclusion drawn from that
database was wrong in production.

### Owner and fix

**Owner: marketplace listing lifecycle / the supplier sync path (Agent 5's
area, not Search OS.)** Escalating rather than filing it inward, same as A12-10.

The fix is a decision, not a patch, and it should be made by whoever owns stock
semantics:

1. make `marketplace_listings.quantity` a maintained roll-up of the variant
   stock, written by the same sync that writes the variants; or
2. have `public_sql()` read stock through the variant table when variants
   exist, which is where the authority demonstrably is; and
3. for a row that really has zero stock, serve 200 with an out-of-stock
   availability signal rather than 404.

(1) and (2) are alternatives; (3) is independent of both and smaller.

**Regression coverage this needs, and it is the missing invariant rather than a
new one:** *every listing the lifecycle calls public must answer 200, and every
publishable product must be offered.* The suite has the forward direction and
not the inverse. I have not written this gate — it belongs with the owner who
decides between (1) and (2), because the assertion differs depending on which
column becomes authoritative, and a gate written against the wrong one would
pin the defect in place. Golden Rule 1 cuts the other way here too: the right
move is not to write a test that passes today.

Invariant: new row **16** in the matrix — the inverse of row 15. Row 15 says an
item that cannot be bought must not be offered. Nothing said that an item that
*can* be bought must be reachable.

---

## Carried forward — not yet investigated

- **Live price disagreement in production** — products **112, 15, 89, 35, 36**.
  Investigated this round: the guard itself is sound (see the pass record), and
  the observability gap around it is now A12-08. What is still open is the
  underlying data: *why* does a seller's `price_label` disagree with the
  supplier-synced `price_cents`, and which of the two is wrong per row. That is
  a data question for Agent 5, not a code question.
- Agent 1's claim about listing **110** — answers 404 here, not 200-with-noindex.
  Needs a production reading to settle; 50, 52 and 77 are now measured.

## Probe hygiene (Phase 100)

**`.attack/scratch.db` no longer holds the catalogue the earlier probes saw.**
To get past `PAGE_SIZE = 24` I seeded 30 listings (ids `900001`–`900030`, cloned
from listing 77, category `home`), so publishable went **16 → 46** and the live
taxonomy gained a `home` department. Anything re-run against this DB and
compared to a number published earlier in this log is comparing two different
corpora — the Agent 1 listing table above is the 16-row reading. Recorded here
rather than reverted, because the >1-page corpus is what makes the pagination
measurement possible at all.

`.attack/probe_robots_agreement.py` currently reports **74 false positives** —
paths with "no directive at all" that are JSON APIs, `sitemap*.xml`, `robots.txt`,
`manifest.json` and `sw.js`. An HTML-content-type filter lands before this becomes
a CI gate. *A gate that cries wolf gets ignored, and then it protects nothing.*
