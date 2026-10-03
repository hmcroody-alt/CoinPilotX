# B — Merchant identity contract

Measured 2026-10-03 against production Postgres. Agent 7. Read-only.

`g:id` has **not** been changed by this document and must not be until Agent 0
freezes the contract. The purpose here is to establish what the current value
actually commits us to, because the commitment is one-way: once Google has ingested
an id, changing it deletes an offer and creates a new one, discarding that offer's
accumulated history.

Today: the feed emits `g:id = str(marketplace_listings.id)` — e.g. `163` — and the
PDP's JSON-LD emits `sku = "pulsesoc-listing-163"`.

---

## 1. First, dispose of the apparent mismatch

Agent 1 raised the `g:id` ↔ `sku` divergence (finding A1-09). It is real as a fact
and low as a risk, and it is worth being precise about why, because the precision is
what makes the rest of this document tractable.

Google joins a feed item to its landing page by `link`, not by comparing `g:id`
against the page's `sku`. So `163` versus `pulsesoc-listing-163` does not cause a
mismatch error. And the two are not competing identities — they are two spellings of
**one** identity, the listing's primary key. There is one identity authority here,
not two.

That said, the timing argument is the whole point: **the cheapest moment to settle
the spelling is now**, while no Merchant Center account exists and the feed has never
been ingested. After first ingestion, any change to `g:id` is a destructive
migration. So this is not urgent because it is broken; it is urgent because the
window is open and will close.

---

## 2. What `marketplace_listings.id` actually is

```
id  integer  default nextval('marketplace_listings_id_seq'::regclass)
attidentity = ''        -- NOT 'a' (ALWAYS), NOT 'd' (BY DEFAULT)
```

| property | measured | consequence for a permanent Google id |
| --- | --- | --- |
| range | 8 … 209, 202 rows, **0 gaps** | No mid-range row has ever been deleted. The recycle hazard is latent, not realised. |
| sequence state | `last_value = 209`, `is_called = true` | Monotonic in normal operation. |
| identity kind | plain column default, **not** `GENERATED ALWAYS AS IDENTITY` | **Postgres will accept `INSERT … (id, …) VALUES (97, …)`.** An identity column would refuse it. |
| uniqueness | `marketplace_listings_pkey` on `(id)` | Unique at any instant — but uniqueness over *time* is a different guarantee, and it is not made. |

The third row is the one that matters. Id reuse is prevented **by convention, not by
the schema**. In a codebase whose schema is created imperatively in `bot.init_db()`
with no migration framework, where ~200 one-off scripts live in `scripts/`, a restore
or backfill that writes explicit primary keys is an ordinary thing for someone to
write. Nothing would stop it, and nothing would notice.

The absence of ids 1–7 is the only trace of past removal, and it predates the current
range.

---

## 3. The twelve questions, answered from evidence

**1. What is canonical product identity?** The supplier product, keyed by
`marketplace_product_sources.provider_product_id` (`NOT NULL`, CJ's `pid`). This is
the only identity that survives a listing being deleted and re-imported.

**2. What is canonical listing identity?** `marketplace_listings.id`. A PulseSoc
publication decision about a supplier product by a seller. Not the same thing as (1).

**3. What is canonical variant identity?** `marketplace_listing_variants.provider_variant_id`
— CJ's `vid`. Measured: present on **3,797 of 3,797** variants and **3,797 distinct**.
Total and unique. This is a genuine stable variant identity and it already exists.

**4. Does `g:id` identify a listing, a product, an offer or a variant?** Today it
identifies a **listing**, and the offer it describes is the listing's *aggregate* —
one price, one availability, standing in for up to 99 variants. That conflation is
tolerable only while variants are excluded (§5).

**5. Will one listing ever produce multiple Merchant offers?** Yes, if variant
distribution ever happens — one offer per variant. Then `g:id` must become per-variant
and today's listing-level `g:id` becomes `item_group_id`. **This is the strongest
argument for deciding the spelling now**, because that future shape is a renaming of
the current one, and doing it before ingestion costs nothing.

**6. How will `item_group_id` work?** It must be the listing identity, grouping the
listing's variant offers. It must **not** be invented before grouping semantics exist
— and they do not yet, because grouping apparel variants requires knowing which axis
is colour and which is size, which Deliverable A establishes we do not know.
`item_group_id` is therefore blocked behind the same gap, not behind a separate one.

**7. Can listing ids be recycled?** Not by the sequence, and never yet in practice (0
gaps). But the column is a plain default rather than an identity, so an explicit-id
insert is accepted (§2). **Unenforced, and currently relied upon.**

**8. Does a seller re-import create a new listing for the same supplier product?**
No — and this *is* enforced:

```sql
CREATE UNIQUE INDEX idx_mkt_source_conn_ref ON marketplace_product_sources
  (seller_user_id, provider, supplier_connection_id, provider_product_id);
```

Measured: **0** supplier products map to more than one listing, across 196 rows.

But read the key columns. `supplier_connection_id` is part of it. Production has
exactly one connection today — `sc_366edc85175345eeb4ce86913ed21f1e`, 196 rows / 196
listings — so **if the seller ever disconnects and reconnects CJ, the key changes and
the same `provider_product_id` can be imported again as a second listing with a new
id.** To Google that is a duplicate offer for the same product. This is the single
most likely way the identity contract gets violated in practice, and it is latent
only because there has been one connection so far.

**9. Does listing identity survive title, category and price changes?** Yes. `id` is
the primary key and none of those columns participate in it. This is correct and must
be preserved — see mutations 9 and 10 in §7.

**10. What happens on delete-and-recreate?** The new row takes a fresh sequence value.
The old `g:id` disappears from the feed and Google eventually drops that offer; the new
`g:id` arrives as a brand-new product with no history. The recreated listing **must
not** inherit the previous Merchant identity, and nothing in the current design would
let it — correctly. Never observed in production (0 gaps).

**11. What happens when the supplier changes?** `idx_mkt_source_listing` is UNIQUE on
`(listing_id)`, so a listing has at most one supplier source; a supplier change updates
that row in place and the listing id is unaffected. Stable, by construction.

**12. Does the numeric `g:id` expose an implementation identifier we will regret?**
Yes, in two ways, and this is the recommendation in §4.

- It publishes the primary key of an internal table and, with it, the catalogue's
  approximate size and age. `g:id = 163` out of a visible 8…209 tells a competitor how
  many products this marketplace has ever had. The feed is a URL Google fetches, not a
  secret, but it is also not required to be a census.
- It is **ambiguous about what it identifies**, which is the real cost. A bare `163`
  does not say whether it names a listing, a product or a variant. When variant offers
  arrive (§5) there will be two id spaces that both look like small integers —
  `marketplace_listings.id` and `marketplace_listing_variants.id` — and nothing in the
  wire format distinguishes them. `pulsesoc-listing-163` does. That the PDP already
  spells it that way is not a defect; it is the better of the two conventions already
  present in the system.

---

## 4. Recommended contract — for Agent 0 to freeze, not yet implemented

| field | recommended value | why |
| --- | --- | --- |
| `g:id` (listing offer) | `pulsesoc-listing-{listings.id}` | Self-describing; matches the PDP `sku` already published; leaves room for a sibling variant namespace; costs one string change **now** and a destructive migration later. |
| `g:item_group_id` (future) | `pulsesoc-listing-{listings.id}` | The listing is the group. Blocked until apparel semantics exist (§3 q6). |
| `g:id` (future variant offer) | `pulsesoc-variant-{provider_variant_id}` | Total and unique across 3,797 rows (§3 q3). Survives re-import, because it is the supplier's id. |
| `sku` (PDP JSON-LD) | unchanged — `pulsesoc-listing-{id}` | Already correct. Adopting it in the feed converges the two spellings rather than introducing a third. |

Supporting changes that would make the contract real rather than conventional:

1. **Promote `marketplace_listings.id` to `GENERATED BY DEFAULT AS IDENTITY`**, or add
   an explicit guard, so that §2's explicit-id insert is refused by the database rather
   than by hope. This is a schema change, so per the brief it stops at the design.
2. **Drop `supplier_connection_id` from the re-import uniqueness key**, or add a second
   unique index on `(seller_user_id, provider, provider_product_id)`, closing §3 q8's
   reconnect duplicate. Also a schema change; also stops here.
3. **Never derive `g:id` from any mutable column** — not title, not price, not slug,
   not category, not `variant_key`.

**`variant_key` must never be a Merchant id.** Measured: 3,797 variants, **2,571
distinct `variant_key`** — it collides across listings and is unique only as
`(listing_id, variant_key)` via `idx_mkt_variant_listing_key`. It also embeds the
option *values*, so it changes whenever a supplier retitles a colour, and it is not
URL-safe. Three independent disqualifications.

---

## 5. The variant future, and why exclusion is still right

Variant offers must not be emitted merely because 3,797 variants exist. Checked
against what Google needs:

| prerequisite | status |
| --- | --- |
| stable variant identity | **MET** — `provider_variant_id`, 3,797/3,797 distinct |
| per-variant price | **MET** — `price_cents` on 3,797/3,797, across 196 listings |
| per-variant availability | **MET** — `stock_quantity`, `stock_state`, `status` |
| per-variant media | **MISSING** — there is **no per-variant image column anywhere in the schema** |
| explicit variant semantics | **MISSING** — Deliverable A §3 |
| `item_group_id` grouping semantics | **MISSING** — blocked on the above |
| required apparel attributes | **MISSING** — Deliverable A §7 |
| canonical landing behaviour per variant | **UNVERIFIED** — the PDP takes `opt_`-prefixed params; deep-linking a specific variant and having it preselect is not proven |
| exact checkout variant mapping | **UNVERIFIED** — and gated behind CLOSE THE RAIL regardless |

Four of nine are unmet and one more is unverified. The existing fail-closed exclusion
is the correct state. Publishing 3,797 variant offers would also multiply the apparel
problem by roughly a hundred: every one of them would need a `color` and a `size` that
Deliverable A shows we cannot source.

Note the irony worth recording: variant offers would *fix* the price problem.
`price_cents` is per-variant and is what checkout charges, so variant offers would read
the authoritative price instead of the stale `price_label` that currently forces 5
listings out of the feed. That is a genuine future benefit and it is not a reason to
go early.

---

## 6. Payment boundary

Merchant Center activation must not run ahead of CLOSE THE RAIL. Google compares the
feed against the landing page and the landing page's checkout; an offer that cannot
complete a purchase is a misrepresentation risk independent of anything in this
document. Provider checkout → webhook → paid transaction → order → confirmation must
be certified first. No live charge, no production refund, and build 31 is not cut here.

The identity contract is also *upstream* of the rail in a way worth noting: an order
records what was bought. If Merchant identity and order identity disagree about what
`163` means, reconciliation after the first real sale is materially harder than
before it.

---

## 7. Mutations this contract must refuse — for Agent 12

Identity half of the attack set (the apparel half is in Deliverable C). Each **must
fail**.

| # | mutation | must fail because |
| --- | --- | --- |
| 9 | `g:id` changes because the title changed | `id` is the primary key; title is not in it (§3 q9). A title-sensitive id would delete and recreate the offer on every edit. |
| 10 | `g:id` changes because the price changed | Same. Price lives in `price_label` / `price_cents`, neither of which is identity. |
| 11 | A deleted-and-recreated listing inherits the previous Merchant identity | §3 q10. The new row is a new product; inheriting history would attribute one product's performance to another. |
| 12 | `variant_key` used as a permanent Merchant id | §4. 2,571 distinct of 3,797; embeds mutable values; not URL-safe. |
| 13 | `item_group_id` invented before grouping semantics exist | §3 q6. Grouping apparel variants requires knowing the axes, which Deliverable A shows we do not. |
| 14 | No Merchant account exists, yet the system reports an offer accepted by Google | No account exists. Acceptance is a claim only Google can make, and nothing here has ever heard back from a search provider. |
| 15 | A feed was generated, therefore the system reports the product ingested | Generation is local. Ingestion is a remote event. `feed_eligible` is our verdict about our own row, and it is not evidence of anything on Google's side. |

Mutations 14 and 15 are the ones most likely to pass accidentally, because they are
failures of *reporting* rather than of data — the temptation is to treat "we emitted it
correctly" as "it worked".

---

## 8. What Agent 0 must decide

1. **Freeze the `g:id` spelling before first ingestion.** Recommended:
   `pulsesoc-listing-{id}`. The window closes at first ingestion and does not reopen
   cheaply. This is the one decision in this document with a deadline attached.
2. Approve or defer the two schema hardenings in §4 (identity column; re-import
   uniqueness key). Both are designs only, per the brief.
3. Confirm variant offers stay excluded until all nine §5 prerequisites are met.
4. Confirm Merchant activation waits on CLOSE THE RAIL (§6).
