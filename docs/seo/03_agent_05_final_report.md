# Agent 5 — structured data + search entity engine: final report

Branch `search-os/agent-05-structured-data`, seven commits on `5bdf4e431`.
Written 2026-10-03.

`02_structured_data_state.md` is the findings document and is not restated
here. This is the fleet-facing half: what shipped, what I refused to decide,
what I got wrong, and what each other lane needs from me.

---

## Status

| | |
|---|---|
| Lane | Structured data + search entity engine |
| Branch | `search-os/agent-05-structured-data` |
| Base / production commit | `5bdf4e431` — **identical**, so nothing here is deployed |
| Code commits | 4 (`25e9b2647`, `fdb337296`, `4570a5f3b`, `0b92ae536`) |
| Doc commits | 3 (`e547cb2a1`, `d7d62fec6`, `b478d3690`) |
| Protection suite | 785 checks / 57 suites, passing |
| Realtime-audio gate | no protected path touched |
| New test files | 0 — so `config/ci_test_manifest.json` is unchanged |
| Output changed on any live page | one node deleted, by `fdb337296`, and nothing else |

**Deployment status is the first thing to read.** Production reports
`5bdf4e431` at `/api/service/health`, which is this branch's base. Every
"live today" claim in the findings document is still true of pulsesoc.com,
and every fix is still unmerged.

---

## What shipped

**One behaviour change.** `fdb337296` deletes the `Product` node that told
Google PulseSoc Premium costs $14.99. Three pages carried it; no page showed
a reader any price at all. The node is deleted rather than corrected — see
the refusal below.

**One security property, closed.** Eight of eleven JSON-LD emitters used a
bare `json.dumps` into a `|safe` raw-text `<script>`, where `<` is the only
character that can end the element early. All eleven now escape it. This was
**not** reachable in production; the severity correction is the next section.

**One inventory, closed by enumerating the sink.** Every
`application/ld+json` occurrence in the repo, not every emitter I could think
of — because three outward passes each missed a site and the enumeration is
the only version that terminates.

---

## Two things I published as corrections to myself

Both are in the findings document in full. They are listed here because a
fleet report that only contains an agent's wins is not useful for calibration.

1. **I called the escaping gap live stored XSS. It was not.** Production is
   served by a renderer that already escaped. I inferred otherwise from
   `grep -c u003c` returning 0 on the live page, which is invalid reasoning:
   benign content needs no escapes, so an absence of escapes is not evidence
   of an absent escaper. The fix proved correct by a different route — the
   live Product node carries a `sku` only `marketplace_web` emits.

2. **I called a dead `offers.url` "a Merchant Center disqualification on its
   own". It is not.** That property is *recommended*, not required. I had
   been writing requirement words from memory; once `WebFetch` became
   available I checked them, and this one was wrong.

A third correction is in the findings document rather than here, because it
is about someone else's work and not mine to headline: my Agent 7 handoff
warned against a failure the codebase had already designed against, in a
document sitting in the same directory that I had not read.

---

## What I refused to decide, and why

**What PulseSoc Premium costs.** Three numbers disagree: `999` in the
entitlement catalog, `1900` at the checkout, `$14.99` in the markup (which is
a *different product's* price). A structured-data layer that picks one is
inventing a fact rather than projecting one, so the node is gone and the
question goes to whoever owns pricing. This is the single most important
escalation on the branch, and note that the consumer-facing half does not go
away when the node does: the catalog still says 999 and the charge is still
1900.

**What the canonical URL of anything is.** `offers.url` pointed at
`https://pulsesoc.com/#pricing`, which does not exist. Agent 2 owns canonical
policy; I did not substitute a guess.

**Whether supplier titles should be filtered at the write boundary.** Nothing
strips `<` from a listing title on the way in or out, so the serialiser's
escaping is the only control standing between a supplier-supplied
`</script ` and the page. That makes the hardening load-bearing. It does not
make sanitising the database this layer's job.

I also left `bot.PRO_PRICE_MONTHLY = "$14.99/month"` in place despite proving
it now has zero readers repo-wide. It is dead, and deleting it would be
tidy — but it is a *pricing* constant, and whether `$14.99` is right is the
same question I just escalated.

---

## Handoffs

Full versions are in `02_structured_data_state.md`. Condensed:

| Agent | What you need from me |
|---|---|
| **0, 1, 3, 8, 10–12** | `seo.schema.serialise_graph` is the only sanctioned way to turn a graph into page output — a twelfth emitter with `json.dumps` reopens a property four commits just closed. Also: `bot._marketplace_public_*_response` are dead rollback code, so don't trust them for what the live page looks like. |
| **2 (canonical)** | If a Premium offer URL is ever reinstated I need a canonical from you, not a guess. Nothing is contradicted today: the live Product node's `url` and `mainEntityOfPage` agree with the page's own canonical. |
| **4 (SSR)** | All JSON-LD on this domain is server-rendered into the initial HTML, marketplace included. Nothing depends on hydration. `serialise_graph` is the single choke point if you move the render path — route through it. |
| **6 (sitemaps)** | The three pages that carried the bad `Offer` are all in `/sitemap-pages.xml` and all indexable (200, `index,follow`, self-canonical, not robots-disallowed). After `fdb337296` they still are; they just stop making a price claim. No sitemap change needed. |
| **7 (Merchant Center)** | The feed exists and is already fail-closed — **I was wrong to warn otherwise.** Do not submit a Premium offer (no agreed price, dead URL; the feed does not carry it today). `image` is required and every feed image is on a supplier CDN. |
| **9 (media)** | All 13 sampled live Product nodes and all 36 feed items carry images on `cjdropshipping.com`; none on a PulseSoc domain. Nothing is signed or expiring today. But `image` is a **required** merchant-listing property resting entirely on a third party, and this layer cannot detect it breaking. |

---

## Rich-result readiness, in one line

Eligible on every required property for both product snippets and merchant
listings, verified property-by-property against Google's current docs.
Everything absent — `brand`, `gtin`, `mpn`, `review`, `aggregateRating`,
`shippingDetails`, `hasMerchantReturnPolicy` — is *recommended only*.

That is the useful finding of this lane: **refusing to invent those costs the
site no eligibility at all.** The hard prohibition and the business interest
point the same way, which is not something anyone had checked.

---

## Limits on this report

- **Nothing here is deployed.** Base equals production.
- **Static checks are not device QA.** Every claim is from rendering pages and
  parsing their markup. No Search Console or Merchant Center diagnostics were
  read; I have no account access.
- **Bing and Schema.org's own documentation were not consulted.** Google's
  were, late in the session. Bing's product-markup requirements are checked
  against nothing here.
- **Two tests inject hostile values upstream rather than through a request,**
  because no reachable request can carry one into those two graphs. They fail
  on the parent commit; that is what makes them worth having. A byte-identical
  change is otherwise unfalsifiable.
- **The sampling is partial where it says so**: 13 of 42 product pages, 10 of
  36 feed items. Counts are stated wherever a claim rests on a sample.
