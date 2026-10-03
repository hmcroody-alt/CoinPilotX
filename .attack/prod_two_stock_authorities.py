"""Which of the two stock columns is written, and which one does the page read?

The page reads `marketplace_listings.quantity`. The supplier sync writes
`marketplace_listing_variants.stock_quantity`. All 152 listings the public
predicate retires carry `stock_quantity > 0` on their variant rows, so the stock
exists and the gate is reading a different column.

What is left to establish is whether the listing-level column is a *roll-up that
failed* or a field that was never part of this path:

- if the 44 survivors' `quantity` lines up with their variants' stock, a roll-up
  exists and ran for 44 rows and not for 152 -- a sync defect with a clear fix
- if the 44 survivors' `quantity` has no relation to their variant stock, the
  listing column is hand-entered and the dropship path simply never sets it --
  a missing write, not a broken one

`stock_synced_at` decides a third question: whether the variant data is fresh.
Stale variant stock would make "the page should trust the variants" a worse
suggestion than it looks.

Read-only.
"""

from __future__ import annotations

import os

import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()

released = "LOWER(COALESCE(l.status,'')) IN ('published','live','active')"
approved = ("LOWER(COALESCE(l.approval_status,''))='approved' "
            "AND LOWER(COALESCE(ms.status,''))='approved'")
join = ("FROM marketplace_listings l "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id")

print("=" * 96)
print("TWO STOCK AUTHORITIES: WHICH ONE IS WRITTEN?")
print("=" * 96)

cur.execute(f"""
    SELECT l.id, l.quantity,
           (SELECT count(*) FROM marketplace_listing_variants v WHERE v.listing_id = l.id),
           (SELECT sum(COALESCE(v.stock_quantity,0)) FROM marketplace_listing_variants v
              WHERE v.listing_id = l.id),
           (SELECT max(v.stock_synced_at) FROM marketplace_listing_variants v
              WHERE v.listing_id = l.id)
    {join}
    WHERE {released} AND {approved} AND COALESCE(l.quantity,0) > 0
    ORDER BY l.id LIMIT 12""")
print("  SURVIVORS (quantity > 0) -- does the listing column match the variants?")
print(f"      {'id':>5s} {'l.quantity':>11s} {'variants':>9s} {'sum(stock)':>11s}  synced")
matches = 0
rows = cur.fetchall()
for listing_id, qty, nvar, vsum, synced in rows:
    flag = "==" if qty == vsum else "!="
    matches += int(qty == vsum)
    print(f"      {listing_id:5d} {str(qty):>11s} {nvar:9d} {str(vsum):>11s} {flag} "
          f"{str(synced)[:19]}")
print(f"      exact match on {matches} of {len(rows)} sampled survivors")

cur.execute(f"""
    SELECT count(*) FROM (
      SELECT l.id, l.quantity,
             (SELECT sum(COALESCE(v.stock_quantity,0)) FROM marketplace_listing_variants v
                WHERE v.listing_id = l.id) AS vsum
      {join}
      WHERE {released} AND {approved} AND COALESCE(l.quantity,0) > 0
    ) t WHERE t.quantity = t.vsum""")
total_match = cur.fetchone()[0]
cur.execute(f"SELECT count(*) {join} WHERE {released} AND {approved} "
            f"AND COALESCE(l.quantity,0) > 0")
total_surv = cur.fetchone()[0]
print(f"      across ALL survivors: {total_match} of {total_surv} have quantity == sum(variant stock)")

intangible = ("LOWER(COALESCE(l.product_type,l.listing_type,'')) "
              "IN ('digital','course','service','event','booking')")
cur.execute(f"""
    SELECT count(*),
           min((SELECT max(v.stock_synced_at) FROM marketplace_listing_variants v
                  WHERE v.listing_id = l.id)),
           max((SELECT max(v.stock_synced_at) FROM marketplace_listing_variants v
                  WHERE v.listing_id = l.id)),
           sum((SELECT sum(COALESCE(v.stock_quantity,0)) FROM marketplace_listing_variants v
                  WHERE v.listing_id = l.id))
    {join}
    WHERE {released} AND {approved} AND NOT ({intangible})
      AND COALESCE(l.quantity,0) <= 0""")
n, synced_lo, synced_hi, stock_total = cur.fetchone()
print(f"\n  RETIRED ({n} rows, all 404 on the wire):")
print(f"      variant stock they collectively hold : {stock_total}")
print(f"      stock_synced_at range                : {str(synced_lo)[:19]} .. {str(synced_hi)[:19]}")

cur.execute("SELECT COALESCE(stock_state,'(null)'), count(*) "
            "FROM marketplace_listing_variants GROUP BY 1 ORDER BY 2 DESC LIMIT 6")
print("      variant stock_state distribution     :")
for state, count in cur.fetchall():
    print(f"          {state:18s} {count}")

print()
print("  A roll-up that matches on the survivors and is NULL on the retired is a sync")
print("  that stopped. No relation on either side would mean the listing column was")
print("  never on this path, and the gate has read a dead field from the start.")

conn.close()
