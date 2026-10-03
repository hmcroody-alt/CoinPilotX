"""Which clause of the public predicate retires listing 77 in production?

77 is `status='published'` in the production table and its public page answers
**404**. 50, 52 and 110 are also `published` and answer 200. So `status` is not
the deciding column, and `marketplace_listing_lifecycle.public_sql()` is a
five-clause conjunction -- status, approval_status, seller status, a non-null
store name, and stock-or-intangible. Any one of them retires the URL.

This evaluates the clauses one at a time rather than the conjunction, because
the conjunction only repeats what HTTP already told me. 50/52/110 are the
control: whatever clause fails for 77 must pass for them, or I am reading a
column that has nothing to do with the 404.

Read-only.
"""

from __future__ import annotations

import os

import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()

IDS = [50, 52, 77, 110]

CLAUSES = (
    ("status released", "LOWER(COALESCE(l.status,'')) IN ('published','live','active')"),
    ("approval approved", "LOWER(COALESCE(l.approval_status,''))='approved'"),
    ("seller approved", "LOWER(COALESCE(ms.status,''))='approved'"),
    ("stock or intangible",
     "(LOWER(COALESCE(l.product_type,l.listing_type,'')) "
     "IN ('digital','course','service','event','booking') "
     "OR COALESCE(l.quantity,0)>0)"),
)

selects = ", ".join(f"({sql}) AS c{i}" for i, (_name, sql) in enumerate(CLAUSES))
cur.execute(
    f"""SELECT l.id, l.status, l.approval_status, ms.status, l.quantity,
               COALESCE(l.product_type, l.listing_type), {selects}
        FROM marketplace_listings l
        LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id
        WHERE l.id = ANY(%s) ORDER BY l.id""",
    (IDS,))

print("=" * 96)
print("WHICH CLAUSE OF public_sql() RETIRES LISTING 77?")
print("=" * 96)
rows = cur.fetchall()
for row in rows:
    listing_id, status, approval, seller_status, qty, ptype = row[:6]
    flags = row[6:]
    failed = [name for (name, _sql), ok in zip(CLAUSES, flags) if not ok]
    print(f"  id={listing_id:4d} status={str(status):10s} approval={str(approval):14s} "
          f"seller={str(seller_status):10s} qty={str(qty):5s} type={str(ptype)}")
    print(f"       failing clauses: {failed or 'none -- all pass'}")

print()
print("  Control: 50, 52 and 110 serve 200, so every clause must pass for them.")
print("  If a clause fails for all four, it is not the reason 77 alone 404s.")

conn.close()
