"""Read-only: is provider_variant_id a better identity than variant_key, and
where does a title stop being a title?"""
import collections
import os
import re

import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()


def q(sql, p=()):
    cur.execute(sql, p)
    return cur.fetchall()


print("=== IS provider_variant_id A USABLE IDENTITY? ===")
tot = q("SELECT COUNT(*) FROM marketplace_listing_variants")[0][0]
nonempty = q(
    "SELECT COUNT(*) FROM marketplace_listing_variants WHERE COALESCE(provider_variant_id,'')<>''"
)[0][0]
distinct = q(
    "SELECT COUNT(DISTINCT provider_variant_id) FROM marketplace_listing_variants"
    " WHERE COALESCE(provider_variant_id,'')<>''"
)[0][0]
print("  variants: %d  provider_variant_id non-empty: %d  distinct: %d" % (tot, nonempty, distinct))
print("  reused across listings:", q("""
    SELECT COUNT(*) FROM (SELECT provider_variant_id FROM marketplace_listing_variants
      WHERE COALESCE(provider_variant_id,'')<>''
      GROUP BY 1 HAVING COUNT(DISTINCT listing_id)>1) t""")[0][0])
print("  sample:", [r[0] for r in q(
    "SELECT provider_variant_id FROM marketplace_listing_variants"
    " WHERE COALESCE(provider_variant_id,'')<>'' LIMIT 4")])

print("\n=== IS variant_key URL-SAFE? ===")
keys = [r[0] for r in q("SELECT variant_key FROM marketplace_listing_variants")]
unsafe = collections.Counter()
for k in keys:
    for ch in set(str(k or "")):
        if not re.match(r"[A-Za-z0-9._~-]", ch):
            unsafe[ch] += 1
print("  distinct keys:", len(set(keys)), "of", len(keys))
print("  characters outside the RFC-3986 unreserved set:", dict(unsafe))
print("  keys containing a positional label 'option%d=':",
      sum(1 for k in keys if re.search(r"option\d+=", str(k or ""))), "of", len(keys))
print("  longest key:", max(len(str(k or "")) for k in keys), "chars")
print("  sample:", list(dict.fromkeys(keys))[:3])

print("\n=== WHERE DOES A TITLE STOP BEING A TITLE? ===")
rows = q("SELECT id, status, approval_status, title FROM marketplace_listings")
buckets = collections.Counter()
for lid, st, ap, t in rows:
    n = len(str(t or ""))
    bucket = "0" if n == 0 else "1-5" if n <= 5 else "6-15" if n <= 15 else "16-30" if n <= 30 else "31+"
    buckets[bucket] += 1
print("  title length buckets (all 202):", dict(buckets))
print("  titles under 16 chars, with their lifecycle:")
for lid, st, ap, t in rows:
    if len(str(t or "")) < 16:
        print("     id=%-4s status=%-14s approval=%-14s title=%r" % (lid, st, ap, t))

print("\n=== WHICH LISTINGS ARE NOT SUPPLIER-BOUND? ===")
for r in q("""SELECT l.id, l.status, l.approval_status, LENGTH(COALESCE(l.title,'')),
                     (SELECT COUNT(*) FROM marketplace_listing_variants v WHERE v.listing_id=l.id)
              FROM marketplace_listings l
              LEFT JOIN marketplace_product_sources s ON s.listing_id=l.id
              WHERE s.listing_id IS NULL ORDER BY l.id"""):
    print("     id=%-4s status=%-14s approval=%-14s title_len=%-3s variants=%s" % r)
conn.close()
