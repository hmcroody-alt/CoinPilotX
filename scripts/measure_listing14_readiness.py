"""Read-only: run the REAL publication evaluator against production listing 14.

Why this exists: the architecture map records gap #3 as "cover_image_url is
NULL". That is a symptom read off one column. Mission section 3 says do not fix
symptoms before identifying every blocker, so this asks the evaluator itself --
`services.business_os.suppliers.drafts._validate`, the same function `publish`
calls at line 490 -- for the complete list, using production's own rows as
inputs.

Writes nothing. Every statement here is a SELECT.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras

from services.business_os.suppliers import drafts, pricing
from services import marketplace_variants as variants

LISTING_ID = 14

url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
conn = psycopg2.connect(url)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("SELECT * FROM marketplace_listings WHERE id=%s", (LISTING_ID,))
listing = dict(cur.fetchone())

cur.execute("SELECT * FROM marketplace_product_sources WHERE listing_id=%s", (LISTING_ID,))
src = cur.fetchone()
source = dict(src) if src else None

cur.execute("SELECT * FROM marketplace_listing_variants WHERE listing_id=%s ORDER BY id",
            (LISTING_ID,))
rows = [dict(r) for r in cur.fetchall()]

cur.execute("SELECT id, media_type, is_cover, media_url FROM marketplace_product_media "
            "WHERE product_id=%s ORDER BY id", (LISTING_ID,))
product_media = [dict(r) for r in cur.fetchall()]

conn.close()

# Exactly what `publish` builds before calling `_validate` (drafts.py:483-490).
priced = [{
    "retail_cents": v.get("price_cents"),
    "availability": variants.availability(v),
    "margin_state": pricing.margin_state(v.get("price_cents"), v.get("cost_cents")),
} for v in rows]
media = drafts._media_of(listing)

verdict = drafts._validate(listing, priced, source, media)

print("=" * 72)
print("PRODUCTION LISTING %d -- real evaluator verdict" % LISTING_ID)
print("=" * 72)
print("title            : %r" % listing.get("title"))
print("category         : %r" % listing.get("category"))
print("status           : %r" % listing.get("status"))
print("approval_status  : %r" % listing.get("approval_status"))
print("price_label      : %r" % listing.get("price_label"))
print("quantity         : %r" % listing.get("quantity"))
print("cover_image_url  : %r" % listing.get("cover_image_url"))
print("currency         : %r" % listing.get("currency"))
print()
print("-- the two media sources, side by side --")
print("drafts._media_of (listing_metadata_json.media) : %r" % (media,))
print("marketplace_product_media rows                 : %d" % len(product_media))
for m in product_media:
    print("   %r" % m)
meta_raw = listing.get("listing_metadata_json")
try:
    meta = json.loads(meta_raw or "{}")
except (ValueError, TypeError):
    meta = {"<unparseable>": meta_raw}
print("listing_metadata_json keys                     : %r" % (sorted(meta) if isinstance(meta, dict) else type(meta),))
if isinstance(meta, dict) and "media" in meta:
    print("  metadata media value                         : %r" % (meta.get("media"),))
print()
print("-- variants --")
for v, p in zip(rows, priced):
    print("  id=%s sku=%r price_cents=%r cost_cents=%r stock=%r/%r -> availability=%r margin=%r"
          % (v.get("id"), v.get("sku"), v.get("price_cents"), v.get("cost_cents"),
             v.get("stock_state"), v.get("stock_quantity"), p["availability"], p["margin_state"]))
print()
print("-- source --")
if source is None:
    print("  NONE -- publish() raises not_a_supplier_product before validating")
else:
    for k in ("provider", "external_product_id", "external_sku", "sync_state",
              "fulfillment_mode", "supplier_cost_cents"):
        print("  %-22s: %r" % (k, source.get(k)))
print()
print("=" * 72)
print("VERDICT: publishable=%s" % verdict["publishable"])
for code in verdict["problems"]:
    print("  BLOCKER: %s" % code)
if not verdict["problems"]:
    print("  (no blockers from the publication evaluator)")
print("=" * 72)
