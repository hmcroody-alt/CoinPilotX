"""How many production rows can the client-priced checkout branches reach?

Read-only. A12-10 is proven in a local DB; this decides whether it is latent or
live. Counts by status too, because the checkout route applies its
`marketplace_listing_lifecycle.is_public` gate only to `marketplace_product` --
so for these two item types a draft or cancelled row is purchasable as well.
"""

from __future__ import annotations

import os

import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()

for table in ("pulse_lessons", "pulse_live_classes", "pulse_courses"):
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=%s", (table,))
    if not cur.fetchone()[0]:
        print(f"{table:22s} TABLE DOES NOT EXIST IN PROD")
        continue
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s "
        "AND (column_name LIKE %s OR column_name LIKE %s OR column_name LIKE %s)",
        (table, "%price%", "%amount%", "%cost%"))
    priceish = [r[0] for r in cur.fetchall()]
    cur.execute(f"SELECT count(*) FROM {table}")
    total = cur.fetchone()[0]
    cur.execute(f"SELECT COALESCE(status,'(null)'), count(*) FROM {table} GROUP BY 1 ORDER BY 2 DESC")
    by_status = cur.fetchall()
    print(f"{table:22s} rows={total:6d}  price-like columns={priceish or 'NONE'}")
    for status, n in by_status:
        print(f"{'':24s} status={status:16s} {n}")

# Has anything actually been charged through these branches?
cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='seller_transactions'")
if cur.fetchone()[0]:
    cur.execute("SELECT item_type, COALESCE(status,'(null)'), count(*), min(amount_cents), "
                "max(amount_cents) FROM seller_transactions GROUP BY 1,2 ORDER BY 1,2")
    print("\nseller_transactions by item_type/status:")
    for row in cur.fetchall():
        print(f"    {row[0] or '(null)':22s} {row[1]:18s} n={row[2]:5d} "
              f"amount_cents min={row[3]} max={row[4]}")
else:
    print("\nseller_transactions: TABLE DOES NOT EXIST IN PROD")

conn.close()
