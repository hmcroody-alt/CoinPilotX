# ADR-001: Shared Product Catalog (`business_os_catalog_products`)

**Status:** Proposed (decision doc required by mission plan Phase 2b before any
schema lands). **Date:** 2026-08-06.

## Context

The repo has THREE independent product tables with no shared identifier:
`business_os_mkt_products`, `business_os_store_products`,
`business_os_ent_products`. The mission spec's "search the PulseSoc catalog /
sell this product" flow (barcode scan → match → prefilled listing) has zero
foundation. Meanwhile the add-listing engine (now shipped as
`marketplace/listing_drafts.py`) needs a place to hang a catalog match, and the
legacy pulse marketplace has its own listings that will backfill into the
canonical tables during the strangler migration.

## Decision

One new table, **catalog as identity, not as inventory**:

```
business_os_catalog_products
  catalog_id     TEXT PRIMARY KEY        -- "cat_" + uuid4 hex
  gtin           TEXT UNIQUE             -- normalized GTIN-14 (UPC/EAN/ISBN lifted)
  title          TEXT NOT NULL
  brand          TEXT
  category       TEXT
  attributes     TEXT                    -- JSON, curated facets only
  image_ref      TEXT                    -- canonical R2 ref
  source         TEXT NOT NULL           -- 'seller_submitted' | 'admin' | 'import'
  status         TEXT NOT NULL           -- 'active' | 'merged' | 'suppressed'
  merged_into    TEXT                    -- catalog_id when status='merged'
  created_at / updated_at
```

Per-seller tables then gain ONE nullable column each (hand-rolled idempotent
`ALTER TABLE ... ADD COLUMN`, guarded by a PRAGMA/introspection check):
`catalog_id TEXT REFERENCES business_os_catalog_products`. A seller's row IS
the offer (price, inventory, condition, fulfillment); the catalog row is only
the shared identity. This is the Amazon ASIN/offer split reduced to what the
repo needs.

### Rules

1. **GTIN is the only merge key.** Normalize to GTIN-14 (zero-pad UPC-A/EAN-13,
   strip check-digit-invalid input with a 400). No fuzzy title matching in v1 —
   wrong merges are worse than missed merges.
2. **Catalog rows are append-mostly.** Sellers may create (`seller_submitted`)
   when a scan finds no match; edits to shared identity are admin-only.
   Duplicates discovered later are resolved by `status='merged'` +
   `merged_into` — offers re-point lazily on read, never by mass UPDATE.
3. **No product without an offer is buyable.** Catalog rows never render in
   buyer search on their own; only via at least one active per-seller product.
4. **Existence not leaked:** GTIN lookup is seller-authenticated and returns
   identity fields only — never other sellers' prices/inventory.
5. **Migration posture:** additive column + new table only; the three product
   tables stay authoritative for their own domains. Cross-surface
   consolidation (one product table) is explicitly OUT of scope — it would
   require rewriting orders/offers/returns FKs during an active strangler
   migration.

## Consequences

* Barcode flow becomes: scan → `GET catalog?gtin=` → hit: prefill draft
  identity from catalog + link `catalog_id`; miss: draft as today, submit
  identity as a new `seller_submitted` catalog row at publish.
* "Other sellers of this product" and price-comparison features become a
  simple `catalog_id` join — but are gated behind rule 4's privacy review.
* A curation queue (admin review of `seller_submitted` rows) is accepted debt;
  until built, `seller_submitted` rows serve matches unreviewed.
* Rejected alternative: catalog as a fourth full product table with its own
  offers table (over-engineering: duplicates the existing per-seller product
  machinery); global fuzzy dedupe service (high wrong-merge risk, no owner).
