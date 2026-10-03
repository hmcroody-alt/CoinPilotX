"""Read-only: discover the real catalog tables and columns in production."""
import os, psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()

PAT = r'(dropship|supplier|variant|option|product|listing|catalog|sku|cj_|merchant|brand|gtin)'

cur.execute("""
  SELECT c.relname, COALESCE(s.n_live_tup, 0)
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
  WHERE n.nspname='public' AND c.relkind='r' AND c.relname ~ %s
  ORDER BY c.relname
""", (PAT,))
tables = cur.fetchall()
print(f"=== {len(tables)} CANDIDATE CATALOG TABLES (name, approx rows) ===")
for t, n in tables:
    print(f"{t}\t~{n}")

print("\n=== EXACT COUNTS + COLUMNS ===")
for t, _ in tables:
    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
    exact = cur.fetchone()[0]
    cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (t,))
    cols = cur.fetchall()
    print(f"\n--- {t}  ({exact} rows, {len(cols)} cols) ---")
    print("  " + ", ".join(f"{c}:{d}" for c, d in cols))

print("\n=== WHERE DO IDENTIFIER-SHAPED COLUMNS LIVE? ===")
cur.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
  WHERE table_schema='public' AND (
    column_name ~ '(gtin|upc|ean|^mpn$|_mpn|^sku$|_sku|barcode|^brand$|_brand|manufacturer)' )
  ORDER BY table_name, column_name""")
for r in cur.fetchall():
    print("  ", r)
conn.close()
