# Agent 7 — Google commerce distribution: fleet state and handoff

Written 2026-10-03. Branch `search-os/agent-07-google-commerce`.

Scope owned: the Merchant feed, Merchant account infrastructure, and the invariant
that what Google is told about a product matches what the page and checkout say.

The substance is in two documents and this file does not repeat them:

- `docs/seo/01_merchant_center_feed.md` — what the feed carries and why, the
  eligibility funnel, current Google policy with dated sources.
- `docs/seo/02_google_commerce_owner_actions.md` — the owner boundary: what only a
  person signed in to a Google account can do.

## 1. State: the feed is live and certified; the account does not exist

`/feeds/merchant-center.xml` → HTTP 200, 36 items, built by
`services/merchant_center_feed.py`. Certified against production 2026-10-03:
36/36 agree with their product page on price, availability, canonical URL and
image; all landing pages 200; all images 200 under a Googlebot UA and all ≥500×500.

Merchant Center account, data source, service account and `GOOGLE_MERCHANT_*`
environment keys are all **ABSENT**. That is the whole remaining gap and it is not
a code gap.

**I changed no executable code.** My commits are documentation and docstrings; the
module's compiled code is byte-identical (verified by AST comparison).

## 2. To Agent 0 — the three things that need a decision

**2a. The verdict is BLOCKED at the owner boundary**, by design, per the brief's
Phase 98. Everything that can be done in a repository is done. The next action is a
human creating a Merchant Center account. Exact consoles, settings and values are
enumerated in `02_google_commerce_owner_actions.md`. No secret is needed in chat.

**2b. Do not connect the account before the order tail is proven.** Production
Stripe is live and has taken real money, but `seller_transactions` has zero rows at
`status='paid'` and `marketplace_orders` is empty — the webhook → paid → order →
fulfilment path has never run end to end. Sending Shopping traffic to a checkout
whose success path is undemonstrated risks taking money without producing an order.

**2c. Apparel attributes are an open data question, not a task.** See §3a. It is the
largest correctness gap in the feed, it affects 27 of 36 live items, and it cannot
be closed by any change to this repository. It needs a decision about where option
semantics come from.

## 3. Adjudicating Agent 1's findings assigned to Agent 7

Agent 1 independently re-measured the feed and corroborated the funnel I had
measured separately: 36 items, the 5 excluded ids are exactly 112/15/89/35/36, zero
feed-vs-PDP price mismatches. Two separate harnesses agreeing on that is worth more
than either alone.

### 3a. A1-08 (apparel + grouping fields) — **ACCEPTED, and escalated**

Agent 1 is right and I had missed it. Correcting one detail of the framing: the
catalogue is not "much" apparel, it is **27 of the 36 live items** (23 clothing,
2 shoes, 2 jewelry), measured 2026-10-03 by supplier breadcrumb.

I accept the finding and **refuse the implied fix**. Adding `g:color` and `g:size`
from the variant axes would publish false data: the axes are positional on all 516
apparel variant rows sampled, position 1 reads `"Gray Flat Feet"` on listing 90 and
`"Picture Color"` on listing 209. `age_group` cannot be defaulted to adult because
id 111 is Boys Clothing. There is no backing column for any of the four anywhere in
production. Reasoning in full in `01_merchant_center_feed.md`.

Re-prioritising: Agent 1 filed this P2. For the apparel share of the catalogue I
read it as the **highest-impact open item in my scope**, above everything except the
owner boundary itself — but it is *not actionable in code*, which is presumably why
it looked like a P2. Both things are true and Agent 0 should hold them together.

### 3b. A1-09 (id space: feed `g:id` vs JSON-LD `sku`) — **ACCEPTED as fact, downgraded as risk, but time-sensitive**

Verified: feed `g:id` is `163`; the PDP's JSON-LD `sku` is `pulsesoc-listing-163`.

It is not a search-truth incident. Google joins a feed item to its landing page by
`link`, not by comparing `g:id` to `sku`; `g:id` is a feed-internal identifier and
`sku` is a schema.org merchant SKU. They occupy different namespaces and are not
required to match. Canonical/`link` parity is 36/36 clean. So I do not agree this
must be resolved "before either ships".

The part that *is* urgent is timing, and it cuts the other way from the priority.
`g:id` must stay stable for the life of the account — changing it later orphans
whatever Shopping history has accrued against the old id. **The cheapest moment to
reconcile the two id spaces is right now, while no Merchant account exists and the
feed has never been ingested.** After step 5 of the owner actions, this gets
expensive. If Agent 5 wants one identity, decide before the account is created.

### 3c. Image host disagreement — **REJECTED, with evidence**

Agent 1 reports the feed using `oss-cf.cjdropshipping.com` while the PDP uses
`cf.cjdropshipping.com`. There is no such disagreement at the item level. The
catalogue has two upstream CJ hosts — 31 items on `cf`, 5 on `oss-cf` (ids 51, 70,
85, 102, 163) — and each item's feed image is the same URL its own page renders.
Checked directly on listing 163: feed and page both serve the identical `oss-cf`
URL, and the exact feed URL appears in the page HTML.

The likely cause is comparing the *set* of hosts across the feed against the *set*
across pages, which disagrees while no individual item does. Flagging it because it
is the same class of harness error Agent 1 usefully warned the fleet about.

My own committed documentation had the mirror-image error — it said all 36 images
were on `cf.cjdropshipping.com` — and both documents are now corrected.

## 4. To Agent 3 — your layer is the right answer, and I am deliberately not adopting it yet

`services/catalog_semantics.py` is the authority the feed will eventually need, and
your decision to emit `position` always and `name` only when known is exactly right
for my purposes: it refuses the inference that would have produced a false `g:color`.

You list "adopting the layer inside `marketplace_seo` and `merchant_center_feed`"
as not yours. Accepted as mine. Not doing it in this mission, for the same reason I
declined to add the recommended-only `g:product_type`: the feed's governing rule is
that every field derives from `services/marketplace_seo.py` and is never re-derived
locally, so adoption means extracting a shared accessor in a module the product page
also depends on. That is one deliberate piece of work — adoption, `g:product_type`
and per-variant items should land together or not at all — and its blast radius is
not justified while no Merchant account exists.

## 5. To Agent 5 — one decision needed before the account is created

Product identity. See §3b: `g:id` = `163`, `sku` = `pulsesoc-listing-163`. Harmless
today, permanent once ingested. You own schema; I own the feed; the window closes at
owner action 5.

## 6. What no agent should rebuild

The Merchant feed. It is live, mature, test-covered, and price-guarded by
`marketplace_seo.price_label_contradicts_variants()`. Agent 1 says the same thing
independently. Specifically: **do not "fix" the 41→36 sitemap/feed count gap by
loosening `feed_eligible`** — those 5 items are withheld because their advertised
price contradicts what checkout charges. The gap is the safety mechanism working.

## 7. The largest distribution gain available, unchanged

Five publishable products are withheld because one `g:price` cannot be truthful
about a product sold at several prices. Three of the five are *correctly* ranged, so
no data fix reaches them. The answer is per-variant items sharing an
`item_group_id`, priced from `marketplace_listing_variants.price_cents` — the
authority the PDP and checkout already use. That also happens to be what Google
requires for apparel variants (§3a), so it and the apparel question are one piece of
work, not two.

## 8. Sources

Google documentation consulted 2026-10-03:

- Product data specification — `https://support.google.com/merchants/answer/7052112`
- Missing value: size / color / gender / age group —
  `https://support.google.com/merchants/answer/9762223`

Policy findings recorded in `01_merchant_center_feed.md`: the Content API sunset
does not affect a scheduled fetch; the 500×500 image floor is enforced 2027-01-31.
