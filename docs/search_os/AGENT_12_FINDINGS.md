# AGENT 12 — SEARCH QUALITY / ADVERSARIAL SEO / CI SENTINEL

## Findings log (published as found, not held for the final report)

Status: **OPEN — 4 material findings, 1 fleet blocker**
Branch: `search-os/agent-12-quality-sentinel`
Measured against: `origin/main` @ `5bdf4e431`
Method: Flask test client over `app.url_map`, against a scratch copy of the dev DB
(serving a page writes `visitor_logs`, so probes must not touch the real DB).
Reproduce: `.attack/probe_robots_agreement.py`, `.attack/evidence_findings_1_to_4.py`

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
   every other agent builds on. All four findings below come from that.
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

## Carried forward — not yet investigated

- **Live price disagreement in production.** `price_label_contradicts_variants()`
  currently excludes products **112, 15, 89, 35, 36** from the Merchant feed. The
  guard is failing closed, which is correct behaviour. But the underlying data
  disagreement between a product's price label and its variants is a P0
  search-truth item, and the guard is hiding it rather than resolving it. Owner
  likely Agent 5 / Agent 9; to be confirmed.
- Agent 1's unproven claim that listings **50 / 52 / 110** render `noindex`.
  Asserted without wire evidence, same as A12-01 was. To be measured.
- Canonical/host injection, facet, pagination and redirect attacks — I will
  re-measure these rather than trust Agent 2 §5.6.

## Probe hygiene (Phase 100)

`.attack/probe_robots_agreement.py` currently reports **74 false positives** —
paths with "no directive at all" that are JSON APIs, `sitemap*.xml`, `robots.txt`,
`manifest.json` and `sw.js`. An HTML-content-type filter lands before this becomes
a CI gate. *A gate that cries wolf gets ignored, and then it protects nothing.*
