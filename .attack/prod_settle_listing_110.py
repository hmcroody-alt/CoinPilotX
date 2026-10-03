"""Does listing 110 exist in production, and what would it serve?

Agent 1's forensics claim listings 50, 52 and 110 render `noindex`. Against the
dev-DB copy I measured 50 and 52 confirmed, 77 the same shape and unnamed, and
**110 answering 404** -- no row at all. 404 and 200-with-noindex are different
outcomes and I could not tell from a copy whether the row was depublished after
Agent 1 measured or the claim was wrong.

Read-only, and the whole point is the `status` column: a row that exists with a
non-public status explains the discrepancy as a timing difference. No row at all
means the claim was wrong when written.

The control is 50 and 52 in the same query. If production has no row for those
either, I am reading the wrong table or the wrong id space, and 110's absence
proves nothing.
"""

from __future__ import annotations

import os

import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()

IDS = (50, 52, 77, 110, 35, 36)

cur.execute(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name='marketplace_listings' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
wanted = [c for c in ("id", "status", "title", "created_at", "updated_at") if c in cols]

print("=" * 96)
print("DOES LISTING 110 EXIST IN PRODUCTION?")
print("=" * 96)

cur.execute(
    f"SELECT {', '.join(wanted)} FROM marketplace_listings WHERE id = ANY(%s) ORDER BY id",
    (list(IDS),))
rows = {r[0]: r for r in cur.fetchall()}

for listing_id in IDS:
    row = rows.get(listing_id)
    if row is None:
        print(f"  id={listing_id:5d}  NO ROW IN PRODUCTION")
        continue
    data = dict(zip(wanted, row))
    title = str(data.get("title") or "")[:46]
    print(f"  id={listing_id:5d}  status={str(data.get('status')):12s} "
          f"updated={str(data.get('updated_at'))[:19]:19s} {title}")

print()
cur.execute("SELECT COALESCE(status,'(null)'), count(*) FROM marketplace_listings "
            "GROUP BY 1 ORDER BY 2 DESC")
print("  production status distribution:")
for status, n in cur.fetchall():
    print(f"      {status:16s} {n}")

cur.execute("SELECT max(id), count(*) FROM marketplace_listings")
top, total = cur.fetchone()
print(f"\n  max(id)={top}  rows={total}")
print()
print("  Control: 50 and 52 must be present. If they are not, this is the wrong")
print("  table or id space and 110's absence means nothing.")

conn.close()
