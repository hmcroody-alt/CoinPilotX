# Agent 5 — structured data + search entity engine: final report

Branch `search-os/agent-05-structured-data`, on `5bdf4e431`.
Written 2026-10-03.

`02_structured_data_state.md` is the findings document and is not restated
here. This is the fleet-facing half: what shipped, what I refused to decide,
what I got wrong, and what each other lane needs from me.
`04_premium_price_authority.md` is the one escalation that needs an owner, and
`05_agent_12_required_mutations.md` is the adversarial contract.

---

## Status

| | |
|---|---|
| Lane | Structured data + search entity engine |
| Branch | `search-os/agent-05-structured-data` |
| Base commit | `5bdf4e431` — unmerged, so nothing here is deployed |
| Production commit | `5bdf4e431` when measured; `44c019b21` by push time (four commits, none in this lane's code) |
| Code commits | 6 (`25e9b2647`, `fdb337296`, `4570a5f3b`, `0b92ae536`, `32f7f4d5b`, `5f74ddec9`) |
| Protection suite | passing |
| Realtime-audio gate | no protected path touched |
| New test files | 1 (`tests/test_structured_data_sinks.py`), declared in `config/ci_test_manifest.json` |
| Output changed on any live page | one node deleted (`fdb337296`); one `Offer` restricted to pages that print it (`32f7f4d5b`) |

**Deployment status is the first thing to read.** Production reported
`5bdf4e431` at `/api/service/health` — this branch's base — throughout the
measurement window. By push time it had moved to `44c019b21`. The conclusion
does not change, but the *reason* does: nothing here is deployed because the
branch is unmerged, not because production still sits on the base.

Of main's four new commits, only `config/ci_test_manifest.json` overlaps this
lane, additively, from other lanes. Re-checked rather than assumed, because
`44c019b21` is "Stop advertising two sitemaps that can never list a URL" and
the marketplace census is drawn from `/sitemap-products.xml`: that sitemap
still answers with 42 `<loc>` entries, so the census basis is intact. The
sitemap index now lists four children rather than six.

Every "live today" claim in the findings document is still true of
pulsesoc.com, and every fix here is still unmerged.

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
the only version that terminates. Now a CI sentinel
(`tests/test_structured_data_sinks.py`): a newly introduced sink turns it red
until someone classifies it as serialiser-backed or static literal. Two
categories, no exception bucket — the one file that needed a third was
consolidated instead.

**One second escaper, eliminated.** `marketplace_storefront` held an
independently written, **correct** copy of the `<`-escaping. Consolidated onto
`serialise_graph` with byte-identical output, for a reason that is not about
correctness: a security property with two implementations has two chances to be
dropped by a refactor, and only one of them is the copy anybody re-reads.

**One `Offer` restricted to where a reader can see it.** The
`MobileApplication` node claimed `price: "0"` on pages that never printed the
free-download claim. The number is *true* — so this was a contract defect, not
a truthfulness one, and it is fixed by restricting the node rather than by
changing `0` or writing new copy. The flag defaults to **no Offer**, so a new
route that forgets it fails closed.

**One test gap, closed.** Writing Agent 12's mutation list surfaced that the
only assertion against identifier invention lived in `test_marketplace_seo.py`,
against a module that **does not render the live page**. A `gtin13` added to
`marketplace_web.product_jsonld` shipped green. Now asserted on the live
renderer, and verified to fail under that exact mutation.

---

## Four things I published as corrections to myself

All are in the findings document in full. They are listed here because a
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

3. **I said three numbers disagreed about Premium. It is four**, and the
   fourth — the one the live checkout actually charges — is not in this
   repository at all. I had read the `PULSE_PREMIUM_PRICE_CENTS` lane and
   assumed it was *the* checkout; the lane every Premium button posts to builds
   its Stripe session from a Price **id**. Reading one checkout and calling it
   the checkout is the same error class as reading one renderer and calling it
   the renderer, which I also made (see 1).

4. **I twice reported a property as covered when only the non-live module's
   copy was covered.** Correction 1 was that shape; so was the GTIN/MPN gap
   found while writing the mutation list. Two modules, one of them tested, is
   the recurring trap in this subsystem, and the general lesson is that
   "a test exists for this" and "a test fails when this breaks *on the path
   that ships*" are different claims.

A further correction is in the findings document rather than here, because it
is about someone else's work and not mine to headline: my Agent 7 handoff
warned against a failure the codebase had already designed against, in a
document sitting in the same directory that I had not read. The fleet-state
version of it is now permanent — **the Merchant feed already exists.**

---

## What I refused to decide, and why

**What PulseSoc Premium costs.** This is the single most important escalation on
the branch and it is written up in full in `04_premium_price_authority.md`.

An earlier draft of this report said "three numbers disagree". Tracing the
whole chain found **four**, and the authoritative one is not in this
repository:

| Number | Where | Who reads it |
|---|---|---|
| `999` / `9999` | entitlement catalog | nothing that charges money |
| `1900` | `PULSE_PREMIUM_PRICE_CENTS` | one checkout lane, for *every* plan |
| **unknown** | a Stripe Price object via `STRIPE_PRICE_ID` | **the lane the UI actually calls** |
| ~~`1499`~~ | was in the markup | removed; it is `crypto_pro_monthly`'s price |

And the *display* stage is empty: `/pricing` shows no dollar amount and
`/pulse/premium` 302s to login, so no crawler and no logged-out reader has ever
been shown a Premium price.

That reframes the deletion. It was not the conservative option — it was the
**only** option. A structured-data layer cannot project a price the repository
does not contain and no page displays. The invariant is now frozen in-tree:
*unknown or contradictory price truth → no price structured-data claim*,
enforced structurally so any substituted number fails, including one nobody has
thought of yet.

The consumer-facing half does not go away when the node does.

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
| **3 (catalog semantics)** | Adopted as the authority: any future brand, identifier, variant-identity or option-meaning claim in these nodes must consume your semantics, not re-derive them. I deliberately did **not** force-wire `catalog_semantics.py` through the schema builders — that is blast radius for identical output, and Agent 0 owns sequencing. |
| **7 (Merchant Center)** | **THE FEED ALREADY EXISTS**, is live and is fail-closed — I was wrong to warn otherwise. All 36 items agree with their page's node on price, currency and availability (0 mismatches). The 5 exclusions are correct; each one could be "fixed" only by lying. Do not submit a Premium offer. Keep `g:identifier_exists=no`. Refuse variants until `item_group_id` has a stable source. |
| **9 (media)** | **Not a migration request** — the imagery is crawlable and "third-party hosted" is not itself a defect. Your lane owns crawlability and lifecycle. Mine owns one invariant: visible PDP hero = OG/Twitter image where the contract requires = `Product.image`. It holds today by construction because all three read the same `gallery_items` output, and it breaks silently the moment a media surface picks its hero independently. |
| **11 (drift)** | You measure; don't rebuild a schema engine. Biggest item: the page prices through `derive_price` (variants first) and the feed through `parse_price` (label only) — authorities that disagreed on 85 of 123 listings in production. They agree on all 36 feed rows **by design**, because feed eligibility refuses unparseable and contradictory labels. Monitor the containment, not the split. Visible-HTML-vs-node price has **no gate** and is only a 41/41 snapshot. |
| **12 (adversarial)** | `05_agent_12_required_mutations.md`: eighteen mutations, every one must fail, with the four that have **no gate** named as such. |

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

- **Nothing here is deployed.** The branch is unmerged. Production equalled the
  base while the measurements were taken and has since moved past it; see the
  status section.
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
- **The marketplace sweep is now complete, not sampled**: all 42
  product-sitemap URLs and all 36 feed items. Earlier drafts sampled 13 and 10
  respectively and said so. One URL needed a retry after a TLS handshake
  failure — recorded because an unexplained fetch failure in a census is
  indistinguishable from a missing node.
- **`/35`'s feed exclusion is unpinned.** Four of the five exclusions are
  explained (three range-priced, one label-contradicts-variants). The fifth is
  recorded as not individually investigated rather than attributed to a cause
  it might not have.
- **Premium's charged amount is unknowable from here.** It is a Stripe Price
  object. Everything this report says about Premium pricing is about the
  numbers the repository contains, and the most important one is not among
  them.
