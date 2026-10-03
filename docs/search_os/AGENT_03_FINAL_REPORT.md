# AGENT 3 — FINAL REPORT

**Product Knowledge Graph + Catalog Semantics**
Branch `search-os/agent-03-product-graph` · commit `54ec072ed` · 2026-10-03

Contracts and fleet status: [AGENT_03_PRODUCT_GRAPH.md](AGENT_03_PRODUCT_GRAPH.md).

---

## 1. What the mission asked, and what it got

The question was not "how do we write better titles". It was **what is this product, and
what can we prove about it?** The answer, measured rather than assumed, is that PulseSoc
knows far less about its own catalogue than its output formats imply, and the gap was
being papered over by convention rather than by code.

Two places already did the right thing. `services/marketplace_seo.py` declined to derive
a brand it could not prove. `services/merchant_center_feed.py` already emitted
`g:identifier_exists="no"`. But both refusals were **documented and undefended** — prose
comments and local conditionals, with no test that would fail if someone replaced the
refusal with a plausible default. That is the failure mode this mission closes.

Delivered:

| Artifact | Lines | Purpose |
|---|---|---|
| `services/catalog_semantics.py` | ~640 | the identity / provenance / confidence layer |
| `tests/catalog/test_catalog_semantics.py` | 63 tests | behavioural pins |
| `scripts/protection/catalog_semantics_mutation_matrix.py` | 20 mutations | proof the pins can fail |
| `docs/search_os/AGENT_03_PRODUCT_GRAPH.md` | — | frozen contracts, fleet notifications |
| `.agent3/*.py` | 6 scripts | the read-only recon that produced every number |

No schema migration. No backfill. No row written. Every database session was
`readonly=True`.

---

## 2. Recon — the catalogue as it actually is

Single supplier (CJ Dropshipping, `provider='cj'`, `fulfillment_mode='DROPSHIP'`), one
seller. Product truth is spread over four tables:

- `marketplace_listings` — 202 rows, the lifecycle and the public-facing text
- `marketplace_product_sources` — 196 rows, the supplier binding
- `marketplace_listing_variants` — 3,797 rows, the purchasable units
- `supplier_snapshots` — the immutable raw payload audit trail, referenced by
  `marketplace_product_sources.source_snapshot_id`

There is no migration framework, so `information_schema` / `pg_class` is the only
trustworthy description of the schema. Documentation and model files both lag it.

---

## 3. Provenance and confidence, as code

`Provenance` distinguishes seven origins: `SUPPLIER_ASSERTED`, `SELLER_ASSERTED`,
`PULSESOC_NORMALIZED`, `SYSTEM_DERIVED`, `UNAVAILABLE_FROM_SOURCE`,
`RECOVERABLE_FROM_SNAPSHOT`, `UNKNOWN`.

The two that carry the mission's whole weight are the last three. `UNKNOWN` means we
never asked. `UNAVAILABLE_FROM_SOURCE` means we asked and the source has nothing, ever.
`RECOVERABLE_FROM_SNAPSHOT` means the data exists in the audit trail and PulseSoc is
discarding it. Collapsing these into "null" is what makes a backfill ticket get filed
for data that does not exist, and makes real data get re-fetched from a supplier that
already sent it.

`Confidence` is an **enum** — `VERIFIED | ASSERTED | DERIVED | NONE` — not a float. A
float invites a threshold, and a threshold invites "0.72 is close enough". There is no
arithmetic on confidence anywhere in the module.

`UNKNOWN` is a falsy sentinel object, deliberately **not** `None`, so that `is None`
checks in downstream code cannot silently swallow it. Consumers use `fact.known`.

---

## 4–7. Identity: product, group, variant, identifiers

**Product key must be namespaced.** The real form is
`cj:sc_366edc85175345eeb4ce86913ed21f1e:1798163608300425216`. 196 distinct, zero
collisions. The temptation is the bare `provider_product_id`, which is unique *today* —
but enforced uniqueness is `(seller, provider, connection, provider_product_id)`, so the
bare id merges two sellers' offers the instant a second seller imports the same CJ
product. `supplier_connection_id` is `text`; do not cast it.

**Identifiers do not exist and never will.** A full recursive key census over all
**20,247** product snapshots found 20 distinct keys and **zero** identifier-shaped ones.
The only identifier-shaped columns platform-wide are three `sku` columns. Production
`sku_class` distribution: `SUPPLIER_INTERNAL: 3,721`, `MISSING: 76`. **Zero** variants
classify as a GTIN under GS1 mod-10. Brand values across the catalogue: `{UNKNOWN}`.

So brand, GTIN and MPN are `UNAVAILABLE_FROM_SOURCE`. `identifier_exists=no` is the
permanent correct answer, not a stopgap.

A methodological note worth keeping: **do not grep for identifiers by substring.** `ean`
matches `clean`, `jeans` and `Ocean`. The structural key census is the only reliable
method, and it is what turned a suspicion into a count.

A GTIN-shaped value whose check digit fails returns `IdentifierClass.UNVERIFIED` — not
`SUPPLIER_INTERNAL`. Calling it a supplier code asserts something we cannot know. It is
unpublishable in either role.

**Variant identity.** `provider_variant_id`: 3,797 non-empty, 3,797 distinct, 0 reused
across listings. `variant_key`: **2,571 distinct across 3,797 rows**, carries `|`, `=`,
spaces, `/`, parentheses and apostrophes, and embeds the literal `option1=` on every row.
It is not an id. `VariantIdentity.stable_id` is a digest of the supplier's variant id:
`pulsesoc-variant-107-aeac525694e86048`, 3,797 distinct. Not a secret — the input space
is structured and anyone holding a supplier id can confirm it by hashing — but URL-safe,
re-import stable, and label-free.

---

## 8–10. Variant semantics (P0), attributes, taxonomy

**Every option axis in production is positional. Zero carry a semantic name.** Any
"Color" label a user sees was invented by PulseSoc.

Listing 209 is the case that settles the design argument — three axes:

- `option1 = "Picture Color"` — supplier boilerplate, not a colour
- `option2 = "4GB 32GB"` — memory configuration
- `option3 = "AU"` — plug region

Relabelling position 1 as colour asserts a colour named "Picture Color". Dropping
position 3 as noise discards the plug standard — the one axis that can make the product
unusable in the buyer's country. So the projection emits `position` always, and `name`
and `value` only when known, with the name **omitted rather than nulled**. `"name": null`
is precisely what invites a downstream consumer to decide position 1 is obviously the
colour.

Attributes beyond the axes: nothing structured is available. There is no PulseSoc
taxonomy; supplier breadcrumbs are passed through verbatim and stay labelled
`SUPPLIER_ASSERTED`. Relabelling one as a PulseSoc category asserts a classification we
never made.

---

## 11–13. Titles, descriptions, the AI boundary

**There is no title-length threshold in this module, on purpose.** Five listings have a
title under 16 characters: `Big T`, `T2`, `T3`, `T4` — and `Lip Medex`, which is a real
product name. A character count cannot tell the fifth from the other four. A threshold
would have silently suppressed a legitimate product to tidy up four test rows.

Supplier prose arrives already tagged `untrusted_content: "True"` upstream and stays
tagged. No provenance value exists that would let an unreviewed AI suggestion be stored
as fact — that absence is itself pinned by a test
(`test_there_is_no_provenance_meaning_an_unreviewed_ai_suggestion_is_fact`) and by the
`AN_AI_PROVENANCE_IS_ADDED` mutation.

---

## 14. Media ↔ product

**3,779 of 3,797 variant images (99.5%)** were recovered from
`supplier_snapshots.payload_json` by matching the supplier's own option string against
the stored `options_json`. They are real, per-variant, and internally consistent.

PulseSoc has **no variant image column**, so every one of them is discarded at import.
Classified `RECOVERABLE_FROM_SNAPSHOT`: available without a single supplier call. The 18
with no match legitimately have none, which is why the model refuses to substitute
another variant's image rather than falling back.

---

## 15–16. Relationships and duplicates

Nothing to merge. 196 distinct supplier product keys with zero collisions; 3,797 distinct
`provider_variant_id`s with zero cross-listing reuse. Duplicate detection ran and found
no duplicates, so no dedupe work is pending from Agent 3. The six supplier-unbound
listings (ids 8–13) are `seller_deleted` or `review_ready` test rows and should never be
indexed.

---

## 17. Quality and semantic state

196 `READY_WITH_UNKNOWNS` / 6 `NEEDS_VARIANT_DECISION`. The six are exactly the six
unbound rows.

`semantic_state` initially returned **one value for all 202 listings** — a vocabulary
that partitions nothing is a vocabulary that describes nothing, and it only became
visible when the model was driven over real rows. A variantless clause split it. It
deliberately never says a listing is *publishable*: it describes what we know, not what
you may do with it. Eligibility belongs to the surface that owns the decision, and the
`SEMANTIC_STATE_GAINS_AN_ELIGIBILITY_WORD` mutation enforces that boundary.

---

## 18. Search eligibility inputs — the headline finding

| Stage | Count |
|---|---|
| EXISTS | 202 |
| published + approved | 196 |
| **PUBLIC** | **44** |
| MERCHANT ELIGIBLE | 39 |

152 published, approved listings are excluded by `quantity` — 148 `NULL`, 4 `<= 0` —
while all 3,797 of their variants report `IN_STOCK`.

**My first reading of this was wrong, and the way it was wrong is the most useful thing
in this report.** I called it "a one-column data gap on the import path" and briefed the
follow-up as "derive listing stock from variant stock". The cross-tab refutes it:

| supplier-bound | `quantity > 0` | count |
|---|---|---|
| no | no | 152 |
| no | **yes** | **1** (listing 35, a known defect) |
| yes | yes | 43 |
| **yes** | **no** | **0** |

`quantity > 0` ⟺ bound. **Zero** bound listings fail the stock gate, so nothing failed
to populate. `importer._create_draft_listing` leaves the column NULL deliberately — `0`
is a count, a merchant's assertion they have none, which nobody made — and
`drafts.publish` fills it from the bound variant at publish time. These 152 never passed
`drafts.publish`; its `_validate` refuses them with `SUPPLIER_VARIANT_UNBOUND`. They are
`published`+`approved` because the admin bulk-approve path validates nothing.

And the fix I briefed would have been actively harmful. All 152 are unbound, so
`fulfillment.create_intent` raises `product_binding_required` and no supplier order can
ever be placed — while marketplace card payments are LIVE in prod. Deriving the quantity
would have produced 152 chargeable, unfulfillable listings. `_apply_stock` already
refuses the same idea in a comment: it "would offer a buyer stock of a colour they
cannot choose and nobody will ship."

The real blocker is a **missing commercial decision** — which variant ships — and
`importer.py:510` declines to invent it because every variant is in stock and active, so
the tie is genuine. Still not mine to fix, but the ask has changed shape: it is not an
import bug, it is 152 unmade decisions.

Any agent sizing an index, sitemap, feed or crawl budget off "196 products" is off by
4.5×. The number is 44 — 43 plus one defect.

---

## 19–24. Downstream contracts

Published in full in [AGENT_03_PRODUCT_GRAPH.md](AGENT_03_PRODUCT_GRAPH.md) under FROZEN
CONTRACTS and CROSS-AGENT NOTIFICATIONS, and issued immediately rather than held for this
report, per the briefing. Summary of obligations placed on others:

- **Structured data / Merchant / feeds (5, 7, 12):** never promote a SKU to `gtin` or
  `mpn`; `identifier_exists=no` is permanent; namespace `item_group_id` with
  `supplier_product_key`; `supplier_connection_id` is text.
- **Media search (9) + 5, 7:** per-variant images exist and are recoverable; use
  `stable_id`, never `variant_key`.
- **Canonical / indexability / taxonomy (0, 2, 5, 7):** supplier taxonomy is
  pass-through, not ours; all axes are positional, so do not build colour facets on
  position 1.
- **Crawl / discovery (0, 2, 6, 7, 11):** 44, not 196; exclude ids 8–13.
- **Agents 1, 4, 8, 10:** no blocking contract; `public_projection()` is the stable read
  surface.

I define contracts; owners implement. I did not touch checkout, pricing, Stripe, order
creation, supplier order submission, canonical URL policy, sitemap generation, Merchant
submission, SSR, social ranking, the media pipeline, telemetry UI, or CI infrastructure.

---

## 25. Privacy and security

No cost- or margin-shaped key appears in any projection, verified across all 202
listings. Enforced in two independent layers: `VariantIdentity` never captures
`cost_cents` at all, and `public_projection` is an **allowlist**, not a denylist. A
denylist would have needed updating every time the supplier added a field.

Prompt-injection surface: supplier text is untrusted and stays tagged. No supplier string
is ever promoted into a position where it reads as a PulseSoc assertion — the
`SUPPLIER_PROSE_RELABELLED_AS_OURS` mutation pins that.

---

## 26. Protection: the tests, and the proof the tests can fail

`tests/catalog/test_catalog_semantics.py` — 63 tests, registered in
`config/ci_test_manifest.json`. That registration is load-bearing: CI here is
**default-deny**, so an unregistered test file silently never runs.

`scripts/protection/catalog_semantics_mutation_matrix.py` — 20 mutations, all killed,
built on the repo's shared `mutation_harness` (red baseline aborts; a missing anchor is
reported as DRIFTED rather than passing; an ambiguous anchor is a survivor; restoration
is hash-verified twice).

All five mandated mutations are covered and killed: unknown brand → fake brand, supplier
id → GTIN, variant A image → variant B, held product → search ready, unknown attribute →
fabricated value.

The matrix has already earned its keep. `STABLE_ID_PUBLISHES_THE_RAW_KEY` **survived** on
first run: the URL-safety test supplied a `provider_variant_id`, so the unsafe fallback
branch was never entered and a module publishing the raw `variant_key` passed clean. The
fix went to the **test** — parametrized over a present id, an empty string and `None` —
not to the matrix. A guard that cannot fail is worse than no guard, because it also
stops anyone else from looking.

One control is deliberately **not** mutated, and the matrix docstring says so: the
two-layer cost privacy, because no single-line edit can leak a value that is never in
scope. Claiming a mutation there would be theatre.

---

## 27. What measurement changed

Three of this mission's decisions were wrong until the model was run against production,
and all three looked fine in fixtures:

1. `stable_id` emitted `pulsesoc-variant-107-option1=sapphire blue|option2=iphone11pro`.
2. `semantic_state` returned a single value for all 202 rows.
3. `IdentifierClass.UNVERIFIED` was dead code — a bad check digit fell through to
   `SUPPLIER_INTERNAL`, asserting a supplier code we had no basis to assert.

A fourth was a test bug of exactly the kind this codebase punishes: `"CJ" not in
json.dumps(...)` failed against the legitimate SKU `CJYD235792608HS`. Keys and values now
get walked and compared as whole tokens, because a substring search over a serialised
blob also trips on a price of `$9.40` containing `940`.

The lesson is the briefing's own: a model that only ever sees fixtures is a model that
agrees with its author.

---

## 28. Migration, events, rollout, rollback

Nothing to migrate. The module is additive and pure — it reads rows that already exist
and returns value objects. It has no DDL, no writes, no background job, no env var, and
no import of `services.db`. Rollback is deleting the file; nothing depends on it yet
because the contracts were published in the same change that defined them.

The deliberate consequence: **adoption is opt-in per surface.** No existing caller
changed behaviour in this commit. That keeps the blast radius at zero while twelve other
agents are working in the same tree, at a cost of the layer not yet being enforced on
`marketplace_seo` or `merchant_center_feed`. Wiring those two up is the natural next
change and belongs to whoever owns them.

Observability: not added. Instrumenting a pure function that nothing calls yet would be
instrumenting nothing. When a surface adopts the projection, that surface's telemetry
owner should count `semantic_state` outcomes — that is the signal worth watching, because
a drift from 196/6 means the import changed.

---

## 29. Definition of done — honest status

Done and verified: catalogue re-measured rather than inherited; identifier truth settled
against all 20,247 snapshots; product/group/variant identity defined and proven unique in
production; provenance and confidence expressed as code; variant axes modelled without
inventing labels; media provenance established; duplicates checked (none); search funnel
quantified; privacy enforced twice and verified catalogue-wide; contracts frozen and
published; 63 tests green; 20 mutations killed; CI registration done; prod-reachability
gate green; branch pushed.

**Not done, and not mine:** the 152 unmade variant-binding decisions behind the
`quantity` NULLs (a commercial call, not a code fix — see §18); populating a
variant image column; adopting the layer inside `marketplace_seo` and
`merchant_center_feed`; any PulseSoc taxonomy. Each is named above with an owner or an
ask to Agent 0.

**A standing caveat.** Every count here is a measurement of 2026-10-03, and this repo
takes on the order of 60 commits a day from parallel sessions. The *shapes* and
*contracts* are stable. The *numbers* are perishable — re-measure before you act on one.
