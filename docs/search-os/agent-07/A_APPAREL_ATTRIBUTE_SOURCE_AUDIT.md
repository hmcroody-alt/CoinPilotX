# A — Apparel attribute source audit

Measured 2026-10-03 against production Postgres and the live feed at
`https://pulsesoc.com/feeds/merchant-center.xml`. Agent 7 (Google commerce
distribution). Read-only throughout: no catalogue row, no ingestion path and no
feed output was changed by this audit.

The question was narrow. Google requires `color`, `age_group` and `gender` on every
apparel offer, plus `size` on clothing and shoes. The feed emits none of them. Can
those values be recovered from a source that actually asserts them, or would
emitting them mean inventing them?

**Answer: they cannot be recovered. Every candidate source either does not exist
upstream, or exists but asserts something other than what it appears to assert.**

---

## 1. What the supplier actually sends

`supplier_snapshots` holds 69,775 raw payloads — 49,374 `kind=inventory` and 20,401
`kind=product`. These are the untransformed CJ responses, so they are the furthest
upstream this system can see.

A `kind=product` payload has exactly these top-level keys:

```
category, currency, description, images, logistics_properties, pid,
price_range, sku, snapshot_at, supplier_price, title, untrusted_content,
variants, weight
```

and each entry of `variants` has exactly these:

```
currency, image, inventory, options, pid, price, sku, title,
untrusted_content, vid, weight_grams
```

There is no colour field, no size field, no gender field, no age field, no material
field and no pattern field — at either level.

### 1.1 The absence was verified against a positive control

A full-table regex pass over all 69,775 payloads returned **zero** rows for every
one of these JSON keys:

> `color`, `colour`, `colorName`, `colourName`, `productColor`, `size`, `sizeName`,
> `productSize`, `sizeChart`, `sizeInfo`, `gender`, `sex`, `targetGender`,
> `forGender`, `ageGroup`, `age_group`, `age`, `ageRange`, `targetAge`, `material`,
> `materialName`, `fabric`, `composition`, `pattern`, `patternType`, `style`,
> `styleName`, `productType`, `categoryName`, `categoryPath`, `productTypeName`,
> `variantProperty`, `variantProperties`, `propertyName`, `attributeName`,
> `attrName`, `specName`, `optionName`, `variantNameEn`, `variantStandard`, `key`,
> `name`, `propertyValue`, `attributeValue`, `attrValue`, `specValue`,
> `optionValue`, `value`

A uniform zero across 47 keys is far more often a broken measurement than a finding,
and `marketplace_listing_variants.options_json` demonstrably contains the string
`"name"` — so the pass was re-run against keys a separate key-path census had already
proven present:

| key | rows matched |
| --- | --- |
| `"variants"` | 69,785 |
| `"pid"` | 69,783 |
| `"price"` | 20,412 |
| `"sku"` | 20,410 |
| `"options"` | 20,408 |

The control passes, so the zeros are real. The reason the zeros extend even to
`"name"` and `"value"` is the subject of the next section.

### 1.2 `variants[].options` is one opaque string, not a structured list

Across 394,256 variant entries in 20,427 product snapshots, `options` is a `str`
**100% of the time**. It is never a list and never a dict. A representative variant,
verbatim:

```json
{
  "currency": "USD",
  "image": "https://cf.cjdropshipping.com/quick/product/6d620aaf-....jpg",
  "inventory": null,
  "options": "Silver 50CM",
  "pid": "2406090942131614800",
  "price": "1.31",
  "sku": "CJLX205679501AZ",
  "title": "Silver Men's And Women's Sacred Geometric Necklace Silver 50CM",
  "untrusted_content": true,
  "vid": "2406090942131615000",
  "weight_grams": "35.0"
}
```

`"Silver 50CM"` is the entire variant description. There is no field that says the
`Silver` part is a colour, and nothing says `50CM` is a length rather than a size.
That is why no `name`/`value` keys exist upstream: there is no structure to name.

### 1.3 `logistics_properties` is a shipping class, not product semantics

My first census mis-typed this field as a dict and reported it empty. It is a **list
of bare strings**, 1 element in 3,632 of 4,000 sampled products and 2 in the other
368. Observed values are `["COMMON"]`, `["COMMON", "THIN"]`, `["Clothes"]`. These
describe how the parcel ships. `"Clothes"` is a packaging class; it is not a product
type, and it carries no colour, size, gender or age.

---

## 2. Nothing is discarded at import, because nothing arrives

Investigation item 7 asked what the import layer throws away. The answer is: with
respect to apparel semantics, nothing — the names are **minted locally**.

`services/business_os/suppliers/normalize.py`, `_cj_options()`:

```python
raw = _first(entry, "variantOptions", "options", default=None)
out = []
if isinstance(raw, (list, tuple)):
    for option in raw:
        ...
        name = clean_text(_first(option, "name", "key", "optionName"), 60)
        value = clean_text(_first(option, "value", "val", "optionValue"), 120)
        if name and value:
            out.append({"name": name, "value": value})
if out:
    return out
key = raw if isinstance(raw, str) else _first(entry, "variantKey", ...)
if isinstance(key, str) and key.strip():
    parts = [clean_text(part, 120) for part in key.split("-")]
    return [{"name": f"option{index + 1}", "value": part}
            for index, part in enumerate(parts) if part]
```

Two things follow, and they pull in opposite directions.

**The good news:** the structured branch already exists and already reads `name` /
`optionName`. If CJ ever starts sending named options, the contract has the slot
for them and no new code is required to accept them. That branch is the correct
place for any future upstream improvement.

**The load-bearing news:** CJ never populates it, so execution always reaches the
fallback, and `f"option{index + 1}"` means **`option1` and `option2` are PulseSoc's
own invention**. They are not a lossy copy of an upstream label. They carry exactly
zero supplier assertion about meaning. Reading them as `color` and `size` is not
recovering a degraded signal; it is authoring a new claim and attributing it to the
supplier.

The hyphen split succeeds often — of 394,256 variant strings, 97.9% split into 2
parts, 2.0% into 1 and 0.1% into 3 — but succeeding at *splitting* is not the same as
knowing *what the parts mean*. 7,781 strings have no hyphen at all and collapse into
a single `option1` holding the whole combination: `'Silver 50CM'`, `'Green Yellow'`,
`'Red And Blue'`, `'100ml set'`, `'W459 Girl Ripple'`.

---

## 3. Position is not a usable proxy, and the live feed proves it

The forbidden heuristic is `option1 = color, option2 = size`. It is tempting because
it is *usually* right. Over 81,202 two-part strings:

| | slot 1 | slot 2 |
| --- | --- | --- |
| recognisable size token | 40 | 64,696 |
| recognisable colour word | 30,452 | 16 |

So the correlation is strong. It is also, on its own, worthless — because the
exceptions are not detectable from the data, and two of them are in the live feed.

### 3.1 The counterexample is a live feed item

Both of these are currently published in the 36-item feed. Both are women's
clothing. Their axes are in **opposite order**:

| id | category | `option1` | `option2` |
| --- | --- | --- | --- |
| 42 | Women's Clothing > Outerwear & Jackets > Basic Jacket | `'Snowflake Blue'` | `'S'` |
| **97** | Women's Clothing > Tops & Sets > Rompers | **`'S'`** | **`'White'`** |

Listing 97's full axis set reads `option1='S' option2='White'`, `option1='S'
option2='Apricot'`, `option1='M' option2='White'` and so on for 20 variants.

Applying `option1 → color` globally publishes, for listing 97, `color = "S"` and
`size = "White"`. Both wrong, on an item Google is already able to fetch. Nothing
separates 97 from 42: same seller, same supplier, same connection, same department,
same two-axis shape, same category depth.

### 3.2 10 of the 36 feed items contradict themselves internally

For these ids, slot 1 does not even hold a consistent *kind* of value across the
listing's own variants:

```
42, 44, 45, 46, 51, 90, 96, 98, 106, 108
```

So the heuristic cannot be rescued by deciding per listing rather than globally.
There is no stable unit to decide over.

### 3.3 Values need per-supplier cleanup even when the axis is right

Listing 98 (`Bags & Shoes/Women's Shoes/Woman Sandals`) spells its sizes
`'Size35'`, `'Size36'` … `'Size42'` — the word is inside the value. Listing 45 uses
bare `'36'`…`'43'`. Listing 111 uses `'90cm'`…`'150cm'`. Three different size
conventions among four shoe-and-children's listings.

---

## 4. The description prose is forbidden, and it could not work anyway

The brief forbids supplier prose as an authoritative structured attribute. That
prohibition needs no defending on policy grounds, but it is worth recording that the
data would defeat the attempt regardless, because two independent failures land on
top of each other.

Labelled prose is abundant — across the product corpus, `color` appears as a label
15,732 times, `size` 13,482, `material` 7,745, `gender` 2,495, `colour` 498,
`age` 468.

**Failure one — the label does not constrain the value.** A real description reads
`Color: 50cm silver<br/> Material: Stainless steel`. The `Color` slot holds a
length. Others: `Color: 'white, red, blue, black gray-black net,'`,
`Size: '135cm*190cm'`, `Material: 'Linen,Wood+Metal'`.

**Failure two — and this is the decisive one — the prose describes the PRODUCT, not
the VARIANT.** Of 397 products carrying a `Size:` label, **312 hold multiple values**
and only 85 hold one:

| product | description says | variants |
| --- | --- | --- |
| — | `Size: small, medium, large, XXL` | 30 |
| — | `Size: S,M,L,XL,XXL` | 5 |
| — | `Size: 35,36,37,38,39,40,41,42,43` | 26 |
| — | `Size: No. 5, No. 6, No. 7, No. 8, No. 9, No. 10` | 12 |

A product with 30 variants whose description says `small, medium, large, XXL` tells
you the range the product is sold in. It cannot tell you which of the 30 offers is
the medium. Google's apparel attributes are **per-offer**. So prose is not merely
unreliable here; it is the wrong shape, and no amount of parsing fixes that.

---

## 5. Nothing downstream can store these values either

A schema-wide search for a column named like a gender, age group, colour, size,
material or pattern returns exactly two, and neither is about products:

```
business_os_business.primary_color      -- business branding
pulse_profile_themes.accent_color       -- profile theme
```

So the gap is not "the column is empty". There is no column, in any of the 170-odd
tables, on either the listing or the variant. Nothing was lost; nothing was ever
collected.

The seller override surface does exist: `marketplace_product_sources` has 196 rows
with `overridden_fields_json`. Across all of them, exactly **one** field has ever
been overridden — `price_label`, three times. So the mechanism for a seller
assertion is present and essentially unused. That matters for §8.

---

## 6. How large is the affected population? The honest answer is a range

Between **24 and 27 of the 36** live feed items are governed by Google's apparel
rules. The uncertainty is not sloppiness — it is the finding.

| reading | count | ids |
| --- | --- | --- |
| (a) strict lead-segment apparel | 24 | 17, 21, 26, 37, 40, 42, 44, 45, 46, 47, 48, 51, 85, 86, 90, 92, 96, 97, 98, 102, 104, 105, 106, 108 |
| (b) + children's clothing sub-trees | +1 | 111 |
| (c) + sportswear / swimwear filed under Sports & Outdoors | +2 | 91, 113 |

Reading (a)+(b)+(c) = 27, which decomposes as 23 clothing + 2 shoes (45, 98) +
2 jewelry (21, 37) and matches the figure in `docs/seo/01_merchant_center_feed.md`.
Of those, the 25 clothing-and-shoes items additionally require `size`; the 2 jewelry
items do not.

**There is no CJ → Google taxonomy mapping anywhere in this codebase.** So the size
of the affected population depends on a judgement nobody has made and nothing has
recorded. And it is not even our judgement that governs: Google *auto-assigns*
`google_product_category` when the feed omits it, so Google's reading of these
breadcrumbs decides which offers it holds to the apparel rules.

### 6.1 Worked example of why that mapping is not trivial

Listing 111's category is:

```
'Toys， Kids & Baby > Boys Clothing > Outerwear & Coats'
      ^
      U+FF0C FULLWIDTH COMMA at index 4
```

My own classifier dropped 111 out of the apparel population on the first pass
because `"toys， kids & baby"` is not equal to `"toys, kids & baby"`. CJ's category
strings also mix `>` and `/` as separators, sometimes with no surrounding spaces
(`"Women's Clothing/Outerwear & Jackets/Blazers"`), and spell the same department
two ways (`Men's Clothing` and `Mens Clothing`, `Toys, Kids & Baby` and
`Toys, Kids & Babies`).

Any category-derived attribute would have silently excluded listing 111 — the one
item in the feed where getting `age_group` wrong matters most.

---

## 7. Per-attribute verdict

| Google attribute | required for | authoritative source | verdict |
| --- | --- | --- | --- |
| `color` | all 24–27 apparel items | none | **UNKNOWN.** Axis values resemble colours but position is not stable (listing 97) and 10 feed items are internally inconsistent. |
| `size` | the 25 clothing + shoes | none | **UNKNOWN.** Same positional problem; values additionally use ≥3 conventions (`'38'`, `'Size38'`, `'120cm'`). |
| `gender` | all 24–27 apparel items | breadcrumb only | **UNKNOWN.** The breadcrumb `Men's Clothing` is `SUPPLIER_ASSERTED` as a *category*. Reading a gender off it is `GUESSED_FROM_CATEGORY`, which the brief forbids — and §6.1 shows it would misfire on non-ASCII punctuation. |
| `age_group` | all 24–27 apparel items | none | **UNKNOWN.** Cannot default to `adult`: listing 111 is `Boys Clothing` with sizes `90cm`–`150cm`, i.e. a small child. `adult` would be a false statement about a live feed item. |
| `material` | not required | prose only | **UNKNOWN**, and prose is both forbidden and product-level. |
| `pattern` | not required | none | **UNKNOWN.** |
| `brand` / `gtin` / `mpn` | conditionally | none | Already settled: absent from all supplier snapshots. The feed states this truthfully with `g:identifier_exists = no`. |

Note the asymmetry in that last row. Google provides `identifier_exists: no` as a
sanctioned way to say "this product genuinely has no GTIN". **There is no equivalent
escape hatch for apparel attributes.** An apparel offer with no `color` is not
"truthfully declared colourless"; it is incomplete. That is why the gap cannot be
closed by declaring it, and why §C withholds instead.

---

## 8. If the data must exist, it has to be collected — design only

Per the brief, this is the branch we are on, and it stops at the design. Nothing
below is built, and Agent 0 decides whether any of it proceeds.

The natural shape, given where the concepts actually live:

- **Product-level, seller-asserted:** `gender`, `age_group`. These are properties of
  the product, not of the variant, and a seller listing a boys' jacket knows both.
- **Variant-level, seller-asserted:** `color`, `size` — asserted by *mapping the
  existing positional axes*, not by replacing them. The seller is shown listing 97's
  real axes and states "axis 1 is size, axis 2 is colour" once, for that listing.
  That single statement is `SELLER_ASSERTED` and resolves all 20 of its variants.
- Both carry provenance explicitly. Absence stays `UNKNOWN` and is never defaulted.
- **Only prompt where the rules apply.** 9–12 of the 36 feed items are not apparel.
  Asking a phone-case seller for a gender is how a decision surface gets ignored,
  and an ignored surface produces junk rather than silence.

Two existing facts make this cheaper than it looks. `marketplace_product_sources.overridden_fields_json`
already exists as a per-listing seller-assertion store (§5), and
`services/catalog_semantics.py` on Agent 3's branch already defines `Provenance`,
`Confidence`, `UNKNOWN`, `Fact` and `OptionAxis`, including
`_POSITIONAL_LABEL = re.compile(r"\A(?:option|opt|attr|attribute)[\s_-]*\d+\Z")` —
which already recognises `option1` as positional rather than semantic.

**Agent 7 must not normalize any of this.** Per the brief, Agent 3 owns meaning,
provenance, confidence and canonical representation. This document is the source
evidence handed to them; §9 is the handoff.

---

## 9. Handoffs

**To Agent 3 — semantics and provenance.** The evidence above is yours to adjudicate.
The specific decisions only you can freeze: (a) that a positional axis label carries
no semantic assertion and must resolve to `UNKNOWN`, not to a guess; (b) whether a
supplier breadcrumb asserting `Men's Clothing` may ever license a `gender` fact, and
under what provenance — my reading is that it cannot, because the supplier asserted a
category and not a gender, which makes any derivation `GUESSED_FROM_CATEGORY`; (c) the
canonical representation for a seller axis-mapping assertion, if Agent 0 approves §8;
(d) the punctuation-folding rule for CJ category strings, given §6.1. Adopting
`catalog_semantics` inside `marketplace_seo` and `merchant_center_feed` is mine, and
deliberately not yet.

**To Agent 5 — JSON-LD.** The PDP's structured data must consume the same canonical
semantics as the feed. An apparel attribute that is `UNKNOWN` must be absent from
JSON-LD too, not filled in with a plausible value for the page's benefit. A
Merchant-only fiction, or a PDP-only fiction, breaks the search truth invariant in
the same way.

**To Agent 11 — reconciliation.** Eventually: canonical catalogue attribute = PDP
visible = structured data = Merchant feed. The non-obvious requirement is that
**`UNKNOWN` must reconcile as `UNKNOWN` on all four surfaces.** A comparison that
treats "absent" as "equal to anything" would score a fabricated value as consistent.

**To Agent 0 — decisions.** (1) Pursue named option axes from CJ upstream, or accept
that PulseSoc must author and own the mapping? (2) If the latter, approve the §8
decision surface? (3) When does the §C readiness gate activate, given it will shrink
the live feed?

---

## 10. Reproducing this

All probes were read-only, run via
`railway run --service Postgres .venv/bin/python <script>` against
`DATABASE_PUBLIC_URL`, with `conn.set_session(readonly=True, autocommit=True)`.
The live feed was read with an unauthenticated `GET` of
`https://pulsesoc.com/feeds/merchant-center.xml` and parsed with ElementTree.

Two measurement errors were made and corrected during this audit, both recorded here
because the corrected numbers are the ones above: `logistics_properties` was first
mis-typed as a dict and reported empty (§1.3), and a substring test for `"accessor"`
matched `Phones & Accessories` and inflated the apparel count before the
lead-segment classifier replaced it (§6). A third was avoided by the positive control
in §1.1.
