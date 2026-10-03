"""How many production listings does `COALESCE(quantity,0)>0` retire?

Listing 77 is `published`, `approved`, its seller is `approved`, and its public
page answers 404 for one reason: `quantity IS NULL`. The public predicate reads
a null quantity as zero stock and the route turns that into a 404.

Two questions follow, and only the second is interesting.

1. How large is that population? A single row is a data glitch. A hundred is a
   sync failure with an SEO consequence.
2. Is `quantity` null because the product is out of stock, or because nothing
   ever wrote it? Those are different defects with different owners. The
   catalogue is one dropship seller whose rows carry quantities in the
   thousands, so a null is much more likely to be an unwritten column than a
   sold-out shelf -- and the distribution of non-null values is what tells me.

Read-only.
"""

from __future__ import annotations

import os

import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()

RELEASED = "LOWER(COALESCE(l.status,'')) IN ('published','live','active')"
APPROVED = ("LOWER(COALESCE(l.approval_status,''))='approved' "
            "AND LOWER(COALESCE(ms.status,''))='approved'")
JOIN = ("FROM marketplace_listings l "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id")

print("=" * 96)
print("HOW MANY PUBLISHED, APPROVED LISTINGS ARE RETIRED BY THE STOCK CLAUSE?")
print("=" * 96)

cur.execute(f"SELECT count(*) {JOIN} WHERE {RELEASED} AND {APPROVED}")
candidates = cur.fetchone()[0]

cur.execute(
    f"""SELECT
          count(*) FILTER (WHERE l.quantity IS NULL),
          count(*) FILTER (WHERE l.quantity = 0),
          count(*) FILTER (WHERE COALESCE(l.quantity,0) > 0)
        {JOIN} WHERE {RELEASED} AND {APPROVED}""")
qty_null, qty_zero, qty_pos = cur.fetchone()

intangible = ("LOWER(COALESCE(l.product_type,l.listing_type,'')) "
              "IN ('digital','course','service','event','booking')")
cur.execute(
    f"SELECT count(*) {JOIN} WHERE {RELEASED} AND {APPROVED} "
    f"AND NOT ({intangible}) AND COALESCE(l.quantity,0) <= 0")
retired = cur.fetchone()[0]

print(f"  published + approved + seller approved      {candidates}")
print(f"      quantity IS NULL                        {qty_null}")
print(f"      quantity = 0                            {qty_zero}")
print(f"      quantity > 0                            {qty_pos}")
print(f"  RETIRED BY THE STOCK CLAUSE (404 on the wire) {retired}")

cur.execute(
    f"SELECT l.id, l.title, l.updated_at {JOIN} WHERE {RELEASED} AND {APPROVED} "
    f"AND NOT ({intangible}) AND COALESCE(l.quantity,0) <= 0 ORDER BY l.id LIMIT 30")
rows = cur.fetchall()
if rows:
    print("\n  the retired URLs:")
    for listing_id, title, updated in rows:
        print(f"      /pulse/marketplace/{listing_id:<6d} {str(updated)[:19]}  {str(title)[:46]}")

cur.execute(
    f"SELECT min(l.quantity), max(l.quantity), round(avg(l.quantity)) {JOIN} "
    f"WHERE {RELEASED} AND {APPROVED} AND l.quantity > 0")
lo, hi, avg = cur.fetchone()
print(f"\n  non-null stock on the surviving rows: min={lo} max={hi} avg={avg}")
print("  A catalogue stocked in the thousands with a handful of NULLs reads as an")
print("  unwritten column, not a sold-out shelf. Zero would read as sold out.")

conn.close()
