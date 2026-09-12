"""Read-only: what does production listing 14 actually sell, and how many?

The companion to `measure_listing14_readiness.py`, asking the two questions that
script could not, because the concepts did not exist when it was written:

* which supplier variant is an order for this listing placed for
  (`marketplace_product_sources.provider_variant_id`), and
* how many *units* is the shelf offering against how many the supplier holds.

Both are asked through the real functions -- `drafts._sold_variant`,
`drafts._sellable_units`, `drafts._validate`, `lifecycle.inventory_available` --
built from exactly the inputs `publish` builds, so the answer is the evaluator's
and not this script's.

Writes nothing. Every statement here is a SELECT, on a read-only session.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras

from services import marketplace_listing_lifecycle as lifecycle
from services import marketplace_variants as variants
from services.business_os.suppliers import drafts, pricing

LISTING_ID = int(os.environ.get("LISTING_ID") or 14)

url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
conn = psycopg2.connect(url)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("SELECT * FROM marketplace_listings WHERE id=%s", (LISTING_ID,))
listing = dict(cur.fetchone())
cur.execute("SELECT * FROM marketplace_product_sources WHERE listing_id=%s", (LISTING_ID,))
row = cur.fetchone()
source = dict(row) if row else None
cur.execute("SELECT * FROM marketplace_listing_variants WHERE listing_id=%s ORDER BY id",
            (LISTING_ID,))
rows = [dict(r) for r in cur.fetchall()]
conn.close()

# Exactly what `publish` builds before `_validate` -- including the two fields it
# did not carry until the binding existed.
priced = [{
    "provider_variant_id": v.get("provider_variant_id"),
    "stock_quantity": v.get("stock_quantity"),
    "retail_cents": v.get("price_cents"),
    "availability": variants.availability(v),
    "margin_state": pricing.margin_state(v.get("price_cents"), v.get("cost_cents")),
} for v in rows]

sold = drafts._sold_variant(priced, source)
verdict = drafts._validate(listing, priced, source, drafts._media_of(listing))

print("=" * 72)
print("PRODUCTION LISTING %d -- what it sells, and how much of it" % LISTING_ID)
print("=" * 72)
print("title           : %r" % listing.get("title"))
print("status          : %r  approval: %r" % (listing.get("status"),
                                              listing.get("approval_status")))
print("price_label     : %r" % listing.get("price_label"))
print("shelf quantity  : %r   <- units a buyer may take" % listing.get("quantity"))
print()
print("-- supplier binding --")
print("fulfillment_mode    : %r" % (source or {}).get("fulfillment_mode"))
print("provider_product_id : %r" % (source or {}).get("provider_product_id"))
print("provider_variant_id : %r   <- NULL means create_intent refuses outright"
      % (source or {}).get("provider_variant_id"))
print()
print("-- variants this listing carries --")
for v, p in zip(rows, priced):
    print("  id=%-5s vid=%-24r %-11s stock=%-6r price=%r"
          % (v.get("id"), v.get("provider_variant_id"), p["availability"],
             v.get("stock_quantity"), v.get("price_cents")))
print()
print("sold variant    : %r" % (sold and sold["provider_variant_id"]))
print("units it offers : %r" % (drafts._sellable_units(sold) if sold else None))
print()
print("-- what the buyer surface answers today --")
for want in (1, 2, 40, 132, 133):
    print("  inventory_available(listing, %-4d) -> %s"
          % (want, lifecycle.inventory_available(listing, want)))
print()
print("-- the real evaluator, on production's own rows --")
print("  %r" % (verdict,))
