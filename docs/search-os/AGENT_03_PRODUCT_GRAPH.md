# AGENT 3 — PRODUCT KNOWLEDGE GRAPH + CATALOG SEMANTICS

Fleet status, measured catalogue truth, and the frozen contracts other agents depend on.

Measured against production PostgreSQL on **2026-10-03**. Every number below was read
from the live database, not from a prior report. Re-measure before relying on counts;
the *shapes* and *contracts* are stable, the *counts* are not.

---

## FLEET STATUS

```
AGENT:           3 — Product Knowledge Graph + Catalog Semantics
STATUS:          Model + protection layer complete; contracts frozen below
BRANCH:          search-os/agent-03-product-graph
WORKTREE:        .claude/worktrees/agent-03-product-graph
CURRENT TASK:    Publishing contracts; final report
OWNED SURFACES:  services/catalog_semantics.py
                 tests/catalog/test_catalog_semantics.py
                 scripts/protection/catalog_semantics_mutation_matrix.py
UPSTREAM:        Agent 1 (search reality), Agent 2 (canonical/indexability)
DOWNSTREAM:      Agents 0, 2, 5, 6, 7, 9, 10, 11, 12
BLOCKERS:        None. No Agent 0 coordination surface exists anywhere in the repo
                 (no FLEET*, no .fleet/, no docs/search-os/ before this file), so
                 this document is published on my own branch instead.
LAST UPDATE:     2026-10-03
```

No schema was migrated, no row was written, no backfill was run. All database access
was `readonly=True`. I did not touch checkout, pricing, Stripe, order creation, supplier
order submission, canonical URL policy, sitemap generation, Merchant submission, SSR,
social ranking, the media pipeline, telemetry UI, or CI infrastructure.

---

## THE ONE RULE THIS LAYER ENFORCES

> UNKNOWN IS BETTER THAN FALSE. MISSING IS BETTER THAN HALLUCINATED.
> GENERIC IS BETTER THAN FABRICATED.

`marketplace_seo.py` already refused to derive what it could not prove, and
`merchant_center_feed.py` already emitted `g:identifier_exists=no`. Those refusals were
**documented but undefended** — nothing stopped a later edit from filling the gap with a
plausible default. This mission added the layer beneath them plus the tests and the
mutation matrix that make the refusals enforceable. It is deliberately not a rewrite.

---

## MEASURED CATALOGUE TRUTH

### The search funnel — the number is 44, not 196

| Stage | Count | Gate |
|---|---|---|
| EXISTS | 202 | rows in `marketplace_listings` |
| status + approval | 196 | `published` AND `approved` |
| **PUBLIC** | **44** | ...AND `quantity > 0` |
| MERCHANT ELIGIBLE | 39 | ...AND feed-required fields present |

**152 published, approved listings are invisible on a stock technicality alone** — 148
have `quantity IS NULL` and 4 have `quantity <= 0`, while all 3,797 of their variants
report `IN_STOCK` from the supplier. The listing-level `quantity` column is simply never
populated for dropship rows; stock lives at the variant level.

Any agent sizing an index, a sitemap, a feed, or a crawl budget off "196 products" is
sizing it off 4.5× the real exposed catalogue.

### Identifiers: brand, GTIN and MPN are permanently absent

A full recursive key census over all **20,247** product snapshots found **20 distinct
keys and zero identifier-shaped ones**. The only identifier-shaped columns platform-wide
are three `sku` columns.

- `sku_class` distribution across production: `SUPPLIER_INTERNAL: 3721, MISSING: 76`
- **variants claiming a GTIN: 0**
- distinct brand values across the whole catalogue: `{UNKNOWN}`

Brand, GTIN and MPN are `UNAVAILABLE_FROM_SOURCE` — not "not yet fetched". No backfill
recovers them, because the supplier never sent them. `identifier_exists=no` is the
correct and permanent answer.

Do not search for `ean`/`gtin` by substring. It matches `clean`, `jeans` and `Ocean`. A
structural key census is the only reliable method here.

### Supplier product key must be namespaced

Real form:

```
cj:sc_366edc85175345eeb4ce86913ed21f1e:1798163608300425216
```

196 distinct keys, **zero collisions**. Bare `provider_product_id` looks unique *today*,
but enforced uniqueness is `(seller, provider, connection, provider_product_id)`. An
unnamespaced key will silently merge two sellers' offers the moment a second seller
imports the same CJ product.

`supplier_connection_id` is **`text`**, not an integer. Do not cast it.

### Variant identity: `stable_id`, never `variant_key`

| | value |
|---|---|
| variants | 3,797 |
| `provider_variant_id` non-empty | 3,797 |
| `provider_variant_id` distinct | 3,797 |
| `provider_variant_id` reused across listings | 0 |
| `variant_key` distinct | **2,571 of 3,797** |

`variant_key` is **not a usable public id**. It is listing-scoped rather than unique, it
carries `|`, `=`, spaces, `/`, parentheses and apostrophes (outside the RFC-3986
unreserved set), and it spells out the literal positional label `option1=` on all 3,797
rows. Publishing it produced ids like:

```
pulsesoc-variant-107-option1=sapphire blue|option2=iphone11pro
```

The contract handle is `VariantIdentity.stable_id`, keyed on the supplier's variant id
and hashed:

```
pulsesoc-variant-107-aeac525694e86048
```

3,797 distinct. It is not a secret — the input space is structured, and a party who
already holds a supplier id can confirm it by hashing — but it is URL-safe, stable
across re-import, and label-free.

### Option axes are positional. All of them.

**0 axes in the entire catalogue carry a semantic name.** All 3,797 rows are positional.
PulseSoc invents any label you see in a UI.

Listing 209 is the instructive case — three axes:

- `option1 = "Picture Color"` — supplier boilerplate, not a colour
- `option2 = "4GB 32GB"` — memory configuration
- `option3 = "AU"` — plug region

Relabelling position 1 as "color" asserts a colour literally named "Picture Color".
Dropping position 3 as noise loses the plug standard, which is the one axis that can
make the product unusable in the buyer's country. Position is emitted always; name and
value only when known, and the name is **omitted rather than nulled**, because
`"name": null` is exactly what invites a well-meaning consumer to decide position 1 is
obviously the colour.

### Variant media exists, is good, and is being discarded

**3,779 of 3,797 variant images (99.5%)** were recovered from the immutable
`supplier_snapshots.payload_json` by matching the supplier's own option string against
the stored `options_json`. They are real, per-variant, and consistent.

PulseSoc has **no variant image column**, so all of it is thrown away on import. Images
are classified `RECOVERABLE_FROM_SNAPSHOT` — recoverable without a single supplier call.

### Semantic state

196 `READY_WITH_UNKNOWNS` / 6 `NEEDS_VARIANT_DECISION`. The six variantless rows are
exactly the six supplier-unbound rows (ids 8–13, all `seller_deleted` or `review_ready`
test rows).

There is **no title-length threshold**, on purpose. Five listings have a title under 16
characters: `Big T`, `T2`, `T3`, `T4` — and `Lip Medex`, which is a real product name. A
character count cannot tell the fifth from the other four, so the model does not pretend
it can.

### Privacy

No cost- or margin-shaped key appears in any projection, verified across all 202
listings. Enforced twice over: `VariantIdentity` never captures `cost_cents` at all, and
`public_projection` is an allowlist rather than a denylist. Supplier prose is already
tagged `untrusted_content: "True"` upstream and stays tagged.

---

## FROZEN CONTRACTS

Import from `services.catalog_semantics`. These names and meanings will not change under
you; if a shape must change I will notify every agent listed below first.

```python
from services.catalog_semantics import (
    UNKNOWN,            # falsy sentinel, NOT None
    Confidence,         # VERIFIED | ASSERTED | DERIVED | NONE
    IdentifierClass,    # GTIN | MPN | SUPPLIER_INTERNAL | UNVERIFIED | MISSING
    Provenance,         # where a fact came from
    SemanticState,      # READY_WITH_UNKNOWNS | NEEDS_VARIANT_DECISION | ...
    product_identity,   # (listing, variants, source, variant_images=None) -> ProductIdentity
    public_projection,  # ProductIdentity -> dict, allowlisted, cost-free
    semantic_state,     # ProductIdentity -> SemanticState
)
```

Three contract rules that are not obvious from the signatures:

1. **`UNKNOWN` is falsy but it is not `None`.** `if fact.value is None` will not catch
   it. Use `fact.known`. The distinction exists so "we asked and the source has nothing"
   stays separable from "we never asked".
2. **`Confidence` is an enum, not a float.** A float invites a threshold, and a
   threshold invites "0.72 is close enough".
3. **`semantic_state` never says a listing is publishable.** It describes what we know,
   not what you may do with it. Eligibility stays with the surface that owns the
   decision.

---

## CROSS-AGENT NOTIFICATIONS

### → Agent 0 (coordination)

- **The exposed catalogue is 44 products, not 196.** Any fleet-level sizing, budget or
  success metric built on 196 is wrong by 4.5×.
- **152 listings are held out of search by `quantity` alone** while their variants are
  all in stock. This is a one-column data gap, not a content problem, and it is the
  single highest-leverage finding in this mission. It is **not mine to fix** — it is a
  listing-lifecycle/import concern. Please assign it.
- No Agent 0 coordination surface exists in the repo. I published here instead. If you
  create one, this file should be linked from it rather than copied.

### → Agents 0, 5, 7, 12 (structured data, Merchant, feeds)

- **Supplier SKU is supplier-internal and must never be promoted.** 3,721 of 3,797 are
  `SUPPLIER_INTERNAL`, 76 are `MISSING`, and **zero** validate as a GTIN under the GS1
  mod-10 check digit. Emitting a SKU as `gtin` or `mpn` publishes a false identifier to
  Google.
- **`identifier_exists=no` is permanent, not provisional.** Brand/GTIN/MPN were never
  sent by the supplier. Do not plan a backfill; there is no source to backfill from.
- **Namespace `item_group_id`.** Use `supplier_product_key` (`cj:<connection>:<product>`),
  not the bare `provider_product_id`. Uniqueness is enforced on
  `(seller, provider, connection, provider_product_id)`, so the bare id merges sellers'
  offers silently once a second seller imports the same product.
- `supplier_connection_id` is `text`.
- A GTIN-shaped value whose check digit fails returns `IdentifierClass.UNVERIFIED`, not
  `SUPPLIER_INTERNAL`. Treat it as unpublishable in either role — we cannot tell which
  it was meant to be.

### → Agents 5, 7, 9 (structured data, feeds, media search)

- **Per-variant images exist for 3,779 of 3,797 variants** and are recoverable from
  `supplier_snapshots` with no supplier call. There is no column holding them today.
  If you want variant-level image search or per-variant feed offers, this is where the
  data is.
- **Use `stable_id` as the variant handle. Never publish `variant_key`.** It is
  listing-scoped (2,571 distinct over 3,797 rows), not URL-safe, and embeds the literal
  string `option1=`. This one is newly measured this session — if you already wired
  `variant_key` into a URL or a feed id, it needs changing.
- An image belongs to the variant it was matched to by option string. Do not fall back
  to "any variant's image" when a match is missing; 18 variants legitimately have none.

### → Agents 0, 2, 5, 7 (canonical, indexability, taxonomy)

- **Supplier taxonomy is passed through verbatim. PulseSoc has no taxonomy of its own.**
  Supplier breadcrumbs are `SUPPLIER_ASSERTED` and must stay labelled as such. Relabelling
  a supplier breadcrumb as a PulseSoc category asserts a classification we never made.
- **All option axes are positional** (0 named, catalogue-wide). Do not build facets that
  assume position 1 is colour.

### → Agents 0, 2, 6, 7, 11 (indexability, crawl, discovery)

- **44 search-exposable products.** See the funnel above. Also note the six
  supplier-unbound rows (ids 8–13) are test/deleted rows and should never be indexed.

### → Agents 2, 11 (duplicates, canonical)

- **Duplicate detection found nothing to merge.** 196 distinct supplier product keys,
  zero collisions; 3,797 distinct `provider_variant_id`s, zero reused across listings.
  No dedupe work is pending from my side.

### → Agents 1, 4, 8, 10

- No blocking contract from me. The projection in `public_projection()` is the stable
  read surface if you need product facts; it is allowlisted and carries no cost or
  margin field.

---

## PROTECTION

- `tests/catalog/test_catalog_semantics.py` — 63 tests, registered in
  `config/ci_test_manifest.json` (CI is default-deny; an unregistered file silently
  never runs).
- `scripts/protection/catalog_semantics_mutation_matrix.py` — 20 mutations, all killed,
  built on the repo's shared `mutation_harness`. The five mandated mutations are
  covered: unknown brand → fake brand, supplier id → GTIN, variant A image → variant B,
  held product → search ready, unknown attribute → fabricated value.

The matrix has already earned its keep once. `STABLE_ID_PUBLISHES_THE_RAW_KEY`
**survived**: the URL-safety test supplied a `provider_variant_id`, so the unsafe
fallback branch was never entered and a module emitting the raw `variant_key` passed.
The fix was to the *test* — parametrized over a present id, an empty string and `None` —
not to the matrix. A guard that cannot fail is worse than no guard.
