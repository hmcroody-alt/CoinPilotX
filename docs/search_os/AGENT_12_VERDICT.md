# AGENT 12 — VERDICT FOR AGENT 0

## Verdict: **BLOCKED**

Not because a test failed. Because there is nothing integrated to certify, and
because one of the things I attacked instead is broken in production right now.

Two independent reasons, either one sufficient:

1. **A12-00 — the fleet is not assembled.** 8 of the 11 upstream surfaces whose
   frozen contracts I am required to consume have **zero commits** ahead of
   `origin/main`; Agents 0 and 11 have **no branch**. A certification over empty
   space is not a PASS, it is a measurement of nothing.
2. **A12-11 — a production-live defect inside the substrate** every Search OS
   agent builds on: **152 of 196 published, approved, in-stock marketplace
   products answer HTTP 404** on pulsesoc.com. 78% of the catalogue. Certifying
   search quality on top of that would be certifying the discoverability of a
   catalogue that cannot be reached.

Measured against `origin/main` @ `5bdf4e431`. Branch:
`search-os/agent-12-quality-sentinel`, commits `3d0644fda` … `e7e81a327`.

---

## What I certify as holding, with the evidence

These are not assumptions. Each was measured, each has a stated falsifier, and
each detector was shown firing on a live exemplar before any zero-count sweep
was believed.

| Invariant | Reading | Where |
|---|---|---|
| 3 — nothing `noindex` is offered | 0 of 148, **against production** | `.attack/prod_sitemap_promises.py` |
| 9 — every offered URL answers anonymous 200 | 148/148, 0 redirects, 0 canonicals pointing away, **against production** | same |
| 10 — every offered URL is crawlable under our own served `robots.txt` | holds | landed gate |
| 11 — the sitemap index lists exactly the routed children | 6 of 6, expected set derived from `url_map` | landed gate |
| 7 — canonical builders agree | holds **where it matters**; the two disagree on `page` by design and no crawler sees the conflict | `tests/test_marketplace_pagination_canonical.py` (30 manifest-run tests) |
| 8 — canonicals are host-independent | `Host` / `X-Forwarded-Host` injection cannot move the canonical | `.attack/probe_host_injection_and_redirect_targets.py` |
| facet values | 7/7 hostile `?category=` values serve `noindex,follow` and collapse to the bare hub; arbitrary strings have no path into a sitemap (`category_entries` iterates `build_taxonomy()`) | `.attack/probe_facet_value_mitigation.py` |
| pagination | `?page=999` clamps rather than minting a URL; `sitemap_eligible("?page=2")` is False | `.attack/probe_facets_and_pagination.py` |

**Read rows 3 and 9 with the caveat attached.** They hold, and they held while
78% of the catalogue was unreachable, because they certify the offer set in one
direction only — and the offer set is derived from the same broken predicate.
Four GATE-covered offer-truth invariants were green over A12-11 the whole time.
That is the single most important structural fact in this report.

## What is broken, routed to an owner

| # | Finding | Severity | Owner |
|---|---|---|---|
| A12-11 | 152 of 196 published products 404; the gate reads `marketplace_listings.quantity` (NULL on 148) while the sync writes `marketplace_listing_variants.stock_quantity` (3,797 rows, all `IN_STOCK`) | **production-live** | Agent 5 / marketplace lifecycle |
| A12-10 | `/api/pulse/payments/checkout` takes the price from the buyer's request body for `lesson` and `live_class` — measured 50 and 500,000 cents at the buyer's choosing | **critical** | payments (not Search OS) |
| A12-10b | the `is_public` and goods-policy checks are both `if item_type == "marketplace_product"`, so a `draft`/`cancelled` lesson is purchasable | critical | payments |
| A12-01 | `/pulse/cart` serves `noindex,nofollow` where policy declares `noindex,follow` | low (stricter) | Agent 2 |
| A12-03 | 14 pages restate the robots literal and drop the preview directives | medium | Agent 4 |
| A12-05 | 11 indexable pages send no robots directive at all | medium | Agent 4 |
| A12-02 | the `classify()` fallthrough declares 5 non-public pages indexable **and** sitemap-eligible | medium | Agent 2 |
| A12-04 | 2 `sitemap_eligible` paths canonicalise elsewhere (`/pulse/help`, `/pulse/support`) | medium | Agent 2 |
| A12-06 | `/pulse/marketplace` picks its directive from catalogue size; the sitemap does not know | medium | Agent 6 |
| A12-08 | a product leaving Shopping leaves no trace — only the unreachable failure is logged | low | Agent 8 |
| A12-07 | two slash-merge redirects 301 into a 404 | low/latent | Agent 2 |
| A12-09 | `verify_sitemap_vs_live.py` hardcodes 3 of 6 sitemaps and reported 61 entries for the whole property | **gate integrity** | Agent 6 |

Full detail, reproduction output and falsifiers: `AGENT_12_FINDINGS.md`.
Seam-by-seam coverage: `AGENT_12_INVARIANT_MATRIX.md` (16 invariants).

## Freeze order

Unchanged from the matrix, repeated here so the handoff is self-contained:
**row 16 (A12-11)** → **row 8 (host-independent canonicals: holds, highest
severity, unguarded)** → **rows 14/15 (price and availability truth)** →
**rows 5 + 13 together** (the canonical tag and the gate that enforces it must
land in the same change, or one of them rots).

## What I deliberately did not do

- **No regression gate for A12-11.** The assertion depends on which column
  becomes authoritative. A gate written against `marketplace_listings.quantity`
  would pin the defect in place and go green doing it. Golden Rule 1 cuts this
  way too: do not write a test that ratifies the implementation. The gate is
  owed, and it is owed *after* the authority is decided.
- **The robots gate is landed but held out of `config/ci_test_manifest.json`.**
  `tests/protection/test_every_page_agrees_with_the_robots_policy.py` is fully
  strict and currently red — it reports A12-01, A12-03 and A12-05. I published
  the red output rather than relaxing the assertion or merging a gate that
  reddens `main` for other people's work. Merge it when Agents 2/4/6 land their
  fixes. Phase 100: a gate that cries wolf gets ignored, and a gate that was
  loosened to go green was never a gate.
- **No production write, no charge, no refund.** Every `prod_*.py` probe runs
  `conn.set_session(readonly=True)`; the A12-10 price proof **monkeypatches
  `stripe.checkout.Session.create`** and asserts on the `unit_amount` captured on
  its way out — no Stripe API call is made at all, in any mode. Platform fee
  untouched at 0%.
- **I did not guess `l.quantity`'s intended semantics** from 44 samples, and I
  did not propose the one-line fix that follows from the guess.

## Defects in my own work, reported against myself

- **I wrongly refuted Agent 1 on listing 110** and published the refutation. I
  measured against the dev copy, which holds 16 publishable listings against
  production's 196 — listing 110 is simply not in it. Agent 1 was right in every
  particular. A claim about a row cannot be refuted by a database that does not
  contain the row, and the control (fetch the id from production) was available
  and I did not run it. Corrected in `AGENT_12_FINDINGS.md`.
- **Every "206" in my earlier reports is a dev-copy number.** Production offers
  **148**. Corrected in both documents.
- **My "sample, not a census" criticism of Agent 1 also falls** — listing 77 is a
  404 in production, not a 200-with-noindex, so Agent 1's three were three of
  three. Chasing my own contradiction between 77 and 110 is what produced A12-11.
- **`.attack/probe_robots_agreement.py` emits 74 false positives** — it needs an
  HTML content-type filter. Recorded against myself in matrix row 13 and not
  fixed.
- **`.attack/scratch.db` is seeded** (30 listings, ids 900001–900030, category
  `home`), which raises the local publishable count 16→46. It is gitignored. Any
  count taken from it is not a production count.

## Reproduction

```
# policy-layer probes (dev copy; serving a page writes visitor_logs, so never the real DB)
cd /Users/hmcherie/Desktop/cpx-agent12
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python .attack/<probe>.py

# production readings — must run from the main checkout; the worktree has no Railway link
cd /Users/hmcherie/Desktop/CoinPilotX
railway run --service Postgres /Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python \
  /Users/hmcherie/Desktop/cpx-agent12/.attack/prod_stock_authority_and_offer_set.py
```

`.attack/mutate_prove_safety_gate_bites.py` is the Phase 125 harness: it shows
the landed gate failing under a deliberate mutation, so "green" means the gate
can discriminate rather than that it ran.

---

**PROTECT THE SYSTEM.** The honest summary is that the strongest gates in this
property all guard the thing that broke once before and got fixed properly,
while both money rows, the duplicate row and the one production-live row have no
coverage at all. Coverage followed the last incident. That is normal, and it is
how A12-11 stayed invisible in plain sight for as long as it has.
