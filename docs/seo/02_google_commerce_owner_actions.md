# Google commerce distribution — what only the account owner can do

Written 2026-10-03. The companion to `01_merchant_center_feed.md`: that document
is about the feed, this one is about the Google-side account work that no commit
can perform. Everything here needs a person signed in to a Google account.

**Nothing in this document should be done by pasting a secret into a chat window.**
Where a credential is involved, the instruction is which console to open and which
field to fill, not which value to send anybody.

## The headline: there is no Merchant Center account yet

The feed is built, served, tested and — as of 2026-10-03 — certified correct
against the live catalogue. It is not connected to anything. That is the whole
remaining gap, and it is not a code gap.

What *does* exist on the Google side:

| thing | status |
|---|---|
| Search Console property `sc-domain:pulsesoc.com` | **PRESENT**, verified 2026-09-18 by DNS TXT |
| Google Cloud project (used for Cloud Translation) | **PRESENT** |
| Google Ads account + conversion labels | **PRESENT**, campaign live |
| `GOOGLE_SITE_VERIFICATION` env slot | **PRESENT** |
| Merchant Center account | **ABSENT** |
| Merchant Center data source / scheduled fetch | **ABSENT** |
| Merchant API service account or credential | **ABSENT** |
| `GOOGLE_MERCHANT_*` environment keys | **ABSENT** |

No deprecated Content API code exists anywhere in the repo, so there is no
migration debt hiding behind the missing account — see the policy section of
`01_merchant_center_feed.md` for why a scheduled fetch is unaffected by the
2026-08-18 Content API sunset.

## Owner actions, in order

1. **Create the Merchant Center account.** Use the Google account that already
   owns the verified Search Console property (`hmcroody@gmail.com`), because step
   2 is dramatically easier if the website claim can inherit that verification.

2. **Claim and verify the website** as `https://pulsesoc.com`. Merchant Center
   should offer the existing Search Console verification as an option. If it
   instead asks for an HTML tag, the `GOOGLE_SITE_VERIFICATION` environment
   variable already exists for exactly this — set it in Railway (it reaches the
   container only on boot, so redeploy) rather than hand-editing a template.

3. **Configure account-level shipping.** Shipping is mandatory for free listings
   in the US and the feed deliberately omits `g:shipping` on every item. This is
   blocker 2 in the companion document and it is the most likely cause of mass
   disapproval *that you can actually fix from a console* — see item 7 for the
   larger one you cannot. Set a real rate, and set it to match what
   checkout actually charges the buyer — buyer-facing shipping is currently free,
   so a non-zero rate here would be its own misrepresentation.
   Console: Merchant Center → Shipping and returns → Shipping services.

4. **Configure tax**, same screen family. US destination.

5. **Add the scheduled fetch.** Merchant Center → Data sources → Add product
   source → scheduled fetch:

   - URL: `https://pulsesoc.com/feeds/merchant-center.xml`
   - Frequency: daily (the feed's `max-age=300` is edge caching, unrelated to
     fetch frequency)
   - No authentication — the route is deliberately public and is declared as such
     in the route-auth baseline.

6. **Expect, and do not "fix", warnings for missing `brand` and `gtin`.** These
   are warnings rather than errors and they are the intended state: verified
   2026-10-03 that no brand/GTIN/MPN/UPC/EAN column exists in any production
   table, and that the upstream CJ Dropshipping payload has no such key either
   (only CJ's internal `sku`). `g:identifier_exists: no` is the truthful answer,
   not a placeholder. The reasoning, including why Google's "use your store name
   as the brand" guidance does **not** apply to resold goods, is in
   `01_merchant_center_feed.md`.

7. **Expect apparel problems on about three quarters of the catalogue, and know
   that nobody can clear them from a console.** For US free listings Google
   requires `color`, `age_group` and `gender` on every `Apparel & Accessories`
   item, plus `size` for clothing and shoes. **27 of the 36 live items are
   apparel** and the feed sends none of the four. Unlike the missing identifiers,
   there is no truthful way to declare these absent — Google provides
   `identifier_exists: no` for identifiers and nothing equivalent here.

   This is the one item on this list that is a genuine open question rather than a
   task. It cannot be fixed by typing into Merchant Center, and it should not be
   fixed by reading the variant axes, because those axes are positional: position 1
   on listing 90 is `"Gray Flat Feet"` and on listing 209 it is `"Picture Color"`.
   Publishing those as colours is exactly the misrepresentation the rest of this
   document is trying to avoid. The full argument is in `01_merchant_center_feed.md`.

   What the owner actually has to decide: whether to pursue named option axes from
   CJ, or to author and own a PulseSoc mapping. Until one of those happens, assume
   the apparel share of the feed underperforms or does not serve.

## Do not turn this on before the order tail is proven

This is a product decision and it sits above the feed.

Production Stripe is live and has taken real money (29 succeeded charges,
$420.22, including one real marketplace charge on 2026-08-23). But
`seller_transactions` has zero rows at `status='paid'` and `marketplace_orders`
is empty, so the webhook → paid → order → fulfilment tail has never run end to
end. Sending Shopping traffic to a checkout whose success path is undemonstrated
risks taking money without producing an order, which is worse than not being
listed. Prove it with one real low-value purchase first.

The other standing business exposure, unchanged: every published listing belongs
to a single seller with supplier-scraped titles and quantities in the thousands.
That is a misrepresentation-policy question about what the catalogue *is*, and no
feed change addresses it.

## Dated risk: the image floor moves on 2027-01-31

Google raises the minimum product image to 500×500 for all categories and
marketing methods on **2027-01-31**. All 36 live items clear it today (smallest is
exactly 500×500, id 105), so there is no action now — but:

- Nothing in this codebase enforces the floor. There is no dimension check in
  `services/merchant_center_feed.py`, none at upload, and none in the eligibility
  verdict.
- The images are not ours. All 36 are supplier-hosted, split across two CJ CDN
  hosts — 31 on `cf.cjdropshipping.com`, 5 on `oss-cf.cjdropshipping.com`. A
  supplier that re-encodes thumbnails smaller moves the catalogue into
  disapproval with no local signal at all. (Two hosts is not a defect: every
  item's feed image is the same URL its own product page renders.)

Worth a calendar entry and, if anyone wants a code task out of this document,
worth a width/height check in the eligibility gate before then.

## The largest distribution gain available, and it is a code task

Not an owner action, but it belongs next to them because it changes how much of
the catalogue Google can see.

Five publishable products are currently withheld from Shopping because one
`g:price` cannot be truthful about a product sold at several prices (details and
ids in `01_merchant_center_feed.md`). Three of those five are *correctly* ranged —
no data fix helps them. The answer Google provides is to submit each variant as
its own item sharing an `item_group_id`, priced from
`marketplace_listing_variants.price_cents`, which is already the authority the
product page and checkout use.

That is a real feature, not a tweak: `g:id` moves from listing-scoped to
variant-scoped, and the feed grows from tens of items to hundreds. It is also
where the catalogue's shape points — ranged, multi-option supplier products are
the normal case here, not the exception. Worth scoping properly before anyone
concludes the feed is "done".

## Recommended but deliberately not implemented

`g:product_type` is a recommended attribute and PulseSoc has real data for it:
`marketplace_listings.category` exists, is non-empty for live rows, is present in
the CJ payload, and is already published in the PDP's `Product` JSON-LD as
`category`. Adding it would invent nothing.

It was left out on purpose. The feed's governing rule is that every field is
derived from `services/marketplace_seo.py` and never re-derived locally, because a
second reader of the same column is how the feed ends up disagreeing with the
page. Honouring that means extracting a shared accessor in `marketplace_seo` and
pointing both the JSON-LD builder and the feed at it — a change to a module the
product page also depends on. That is the right change, but it is a wider blast
radius than a recommended-only attribute justifies while the account does not even
exist. Do it as its own piece of work, not as a footnote to turning the feed on.
