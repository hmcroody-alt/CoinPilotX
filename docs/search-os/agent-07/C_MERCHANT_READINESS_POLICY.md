# C — Merchant readiness policy

Agent 7, 2026-10-03. **Policy frozen here; gate NOT activated.** No executable
eligibility change ships with this document, and the live feed still carries 36 items.
Activation is Agent 0's decision, for the reason in §6.

---

## 1. The distinction this policy exists to make

> **PULSESOC PUBLIC ≠ GOOGLE COMMERCE READY**

A listing can be a perfectly good public product page — crawlable, indexable,
purchasable — while being unfit to submit as a Shopping offer. Those are different
questions with different consequences for getting them wrong, and the system already
embodies that distinction one level up: `marketplace_seo.eligibility()` returns both
`indexable` and the strictly narrower `feed_eligible`, and `merchant_center_feed.feed_row`
reads only the latter. The funnel today is **44 public / 41 indexable / 36 feed-eligible**.

Merchant readiness is a **third, narrower verdict** on the same axis. It is a
*projection eligibility state*, not a publication state. The invariants:

- Withholding an offer from Google **must not** unpublish the listing from PulseSoc.
- It **must not** alter Marketplace discovery, search, price, stock, or checkout.
- It **must not** cause an attribute to be guessed in order to pass.
- It **must not** be a quality judgement about the product. It is a statement about
  whether *we hold the data Google requires*, and nothing else.

Direction of travel matters: readiness can only ever *subtract* from
`feed_eligible`. A row that fails `feed_eligible` can never become Merchant-ready by
passing a readiness check.

---

## 2. Why the gap cannot be closed by declaring it

Google provides `identifier_exists: no` as a truthful way to say a product genuinely
has no GTIN, brand or MPN. The feed already uses it, which is what makes that absence
legal rather than a rejection.

**There is no equivalent for apparel attributes.** An apparel offer with no `color` is
not "truthfully declared colourless" — it is incomplete. So the three options are:
supply a true value, supply a false value, or withhold the offer. Deliverable A shows
the first is impossible today. The second is forbidden by the brief and is a Merchant
Center misrepresentation risk. The third is this policy.

It is worth recording that Google's own severity wording is ambiguous, because the
policy should not overstate its warrant. Google says both that missing required
attributes "lower your product's data quality. This can reduce your offer's
performance in search results" and, elsewhere, that "You must specify these for your
offers to show." No published guidance addresses merchants who genuinely lack the
data. So the true consequence of shipping incomplete apparel offers is somewhere
between degraded ranking and outright disapproval, and we will not know which until
an account exists. **That uncertainty is an argument for withholding, not for
guessing** — the downside of withholding is bounded and reversible, and the downside
of a misrepresentation finding is not.

---

## 3. The readiness verdict

Readiness is evaluated **per offer**, after `feed_eligible` passes.

```
merchant_ready(listing) :=
      feed_eligible(listing)
  AND (NOT apparel_governed(listing) OR apparel_attributes_satisfied(listing))
```

### 3.1 `apparel_governed`

True when the offer falls under Google's apparel rules. Deliverable A §6 establishes
this is **24–27 of the 36** depending on the taxonomy reading, and that no CJ → Google
mapping exists in this codebase.

Two consequences for the policy:

- The classifier must **fold CJ's homoglyph punctuation before matching** — listing
  111's category contains U+FF0C FULLWIDTH COMMA and was silently dropped from the
  apparel population on the first attempt (Deliverable A §6.1). A classifier that
  misses 111 misses the one item where `age_group` matters most.
- Where the reading is genuinely ambiguous, `apparel_governed` must resolve
  **true** — i.e. fail closed into withholding. Google auto-assigns
  `google_product_category` when the feed omits it, so Google's reading governs, not
  ours; guessing *narrow* here means shipping an incomplete apparel offer, while
  guessing *wide* only withholds a non-apparel offer that would have been fine. The
  asymmetry is clear and the cheap error is the safe one.

This classifier is a **semantics** decision, which makes it Agent 3's to own, not
mine. §7 hands it over.

### 3.2 `apparel_attributes_satisfied`

True only when every attribute Google requires for that offer's bucket is present
with provenance in `{SUPPLIER_ASSERTED, SELLER_ASSERTED, DERIVED_FROM_EXPLICIT_SOURCE}`.

| bucket | required |
| --- | --- |
| Clothing (1604) | `color`, `age_group`, `gender`, `size` |
| Shoes (187) | `color`, `age_group`, `gender`, `size` |
| Apparel & Accessories (166), non-clothing | `color`, `age_group`, `gender` |
| variant offers, if ever emitted | the above **plus** `item_group_id` |

`UNKNOWN` never satisfies. `GUESSED_FROM_POSITION`, `GUESSED_FROM_TITLE`,
`GUESSED_FROM_IMAGE` and `GUESSED_FROM_CATEGORY` are not merely insufficient — they
must not be constructible. A provenance enum that *can* express a guess will
eventually carry one.

Measured today: **zero** of the 24–27 apparel offers would satisfy this, because no
backing column exists anywhere in the schema (Deliverable A §5).

### 3.3 Fail closed, and never silently

Two rules learned from the price guard next door. `marketplace_seo.price_label_contradicts_variants()`
**fails open** — a caller that does not join variants gets `False` and the exposure
silently reopens. Readiness must invert that: an evaluation that cannot obtain the
attributes must return *not ready*, not *ready*.

And a withheld offer must be **observable**. A row that leaves Shopping with no trace
is how a catalogue quietly shrinks; `merchant_center_feed.feed_row` already prefers a
loud per-row failure over a silent skip for exactly this reason.

---

## 4. What the policy does to the live feed — and why it is not applied

If activated today, with zero apparel attributes in existence:

| | now | after activation |
| --- | --- | --- |
| public | 44 | 44 (**unchanged**) |
| indexable | 41 | 41 (**unchanged**) |
| feed_eligible | 36 | 36 (**unchanged**) |
| **merchant_ready** | — | **9–12** |

The feed would drop from 36 items to roughly 9–12. **The brief forbids doing that in
this mission, and that prohibition is correct**, for a reason worth stating plainly:
the feed is not currently being consumed by anybody. No Merchant Center account
exists. So shrinking it today buys zero reduction in real exposure and costs 24–27
items of working infrastructure — and it would do so on the basis of a severity
judgement (§2) we cannot yet confirm.

The sequencing that follows:

1. **Now** — policy frozen in writing, `merchant_ready` not computed, not wired, feed
   unchanged at 36.
2. **Before an account is created** — Agent 0 decides whether readiness gates the feed
   from first ingestion. This is the cheap moment, because an offer never ingested has
   no history to lose.
3. **If attributes are collected** (Deliverable A §8) — items re-qualify as sellers
   assert, and the feed grows back past 36 on real data.
4. **If an account is created before attributes exist** — activate the gate at that
   point. 9–12 complete offers is a better opening position than 36 offers of which
   24–27 may be disapproved, because disapprovals attach to an account.

---

## 5. Note on the price authority, which readiness must not paper over

Five publishable listings — ids 15, 35, 36, 89, 112 — are already withheld from the
feed because `price_label` contradicts `variants.price_cents`. Listing 36 advertised
$38.00 against 95 variants all priced $2.29.

This policy adds a second withholding reason and **must not be allowed to absorb the
first**. They are different defects with different fixes: 35 and 36 are stale labels a
seller can correct; 15, 89 and 112 are genuinely ranged products where no label edit
helps, and whose structural answer is per-variant offers sharing an `item_group_id`
priced from `price_cents` — which Deliverable B §5 shows is blocked on the same
apparel gap. A single merged "not ready" verdict would hide that distinction and the
sitemap-vs-feed count gap would stop being legible as the safety mechanism it is.

Readiness and the price guard must therefore produce **separate, individually
attributable reasons**, never one boolean.

---

## 6. Agent 0 decisions

1. **Does readiness gate the feed from first ingestion, or does the feed ship as-is
   and accept the disapproval risk?** Recommended: gate from first ingestion, because
   disapprovals attach to an account and a clean start is cheap right now.
2. **Pursue named axes from CJ, or author and own a PulseSoc mapping?** Deliverable A
   §8 designs the latter. This is the only route that produces real `color` and `size`.
3. **Approve the seller decision surface** (Deliverable A §8)? Nothing is built.
4. **Confirm activation waits on CLOSE THE RAIL** (Deliverable B §6).
5. Confirm the **fail-closed-into-withholding** default in §3.1 for ambiguous
   taxonomy, accepting that it withholds some non-apparel offers.

---

## 7. Handoffs

**Agent 3** owns `apparel_governed` (§3.1), the provenance enum (§3.2) and the
punctuation-folding rule. The non-negotiable: the enum must have no spelling for a
guess.

**Agent 5** — JSON-LD must reflect the same canonical semantics. An attribute withheld
from Merchant as `UNKNOWN` must also be absent from the PDP's structured data. A
Merchant-only or PDP-only value breaks the search truth invariant identically.

**Agent 11** — reconcile canonical = PDP = structured data = Merchant, and treat
`UNKNOWN` as a value that must match `UNKNOWN`. A comparison treating absence as
"equal to anything" scores fabrication as consistent. Also reconcile the *reason* a
row is withheld, per §5.

---

## 8. Mutations this policy must refuse — for Agent 12

Apparel half; the identity half is Deliverable B §7. Each **must fail**.

| # | mutation | must fail because |
| --- | --- | --- |
| 1 | `option1` → `color` globally | Listing 97 is live-feed women's clothing with `option1='S'`, `option2='White'`. Publishes `color="S"`. Deliverable A §3.1. |
| 2 | `option2` → `size` globally | Same row, inverted. Publishes `size="White"`. 10 of 36 feed items are also internally inconsistent about slot 1. |
| 3 | all apparel → `age_group='adult'` | Listing 111 is `Boys Clothing`, sizes `90cm`–`150cm`. `adult` is false about a live feed item. |
| 4 | title contains "women" → `gender='female'` without provenance | `GUESSED_FROM_TITLE`. Listing 97's title is "Baggy Pants Backless Women's Outdoor Sleeveless" — the department is not a gender assertion about the wearer, and no provenance class permits the inference. |
| 5 | image looks blue → `color='blue'` | `GUESSED_FROM_IMAGE`. Not constructible. |
| 6 | supplier internal SKU → `GTIN` | A CJ SKU (`CJLX205679501AZ`) is not a GTIN; it fails GTIN shape and check-digit validation. The feed already states the truth with `identifier_exists: no`. |
| 7 | missing apparel attributes → offer treated Merchant-ready | §3. Only assertable once the gate activates; until then it must fail as "gate not active", never as "ready". |
| 8 | Merchant readiness failure → PulseSoc listing unpublished | §1. Readiness may only subtract from the Google projection. A listing withheld from Shopping stays public, indexable and purchasable. |

Mutation 8 is the one this whole document exists to guarantee, and the easiest to
violate by accident — by implementing readiness as a `status` change rather than as a
projection filter.
