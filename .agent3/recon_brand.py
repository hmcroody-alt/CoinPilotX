"""Read-only: does the supplier EVER send brand/gtin? And what is `untrusted_content`?"""
import os, json, re, collections, psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()

# 1. Server-side scan of EVERY product snapshot for brand/gtin-shaped keys.
print("=== brand/gtin token present anywhere in product snapshot TEXT ===")
for tok in ("brandName", "brand", "gtin", "upc", "ean", "mpn", "barcode", "manufactur", "modelNumber"):
    cur.execute("""SELECT COUNT(*) FROM supplier_snapshots
                   WHERE kind='product' AND payload_json ILIKE %s""", (f"%{tok}%",))
    n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM supplier_snapshots WHERE kind='product'")
    tot = cur.fetchone()[0]
    print(f"  {tok:<12} {n}/{tot} product snapshots contain the substring")

# 2. Full key census over ALL product snapshots (not a 600 sample).
print("\n=== full recursive key census over ALL product snapshots ===")
cur.execute("SELECT payload_json FROM supplier_snapshots WHERE kind='product'")
keys = collections.Counter()
envelope = collections.Counter()
n = 0
while True:
    batch = cur.fetchmany(2000)
    if not batch:
        break
    for (pj,) in batch:
        n += 1
        try:
            o = json.loads(pj or "null")
        except Exception:
            keys["__UNPARSEABLE__"] += 1
            continue
        if isinstance(o, dict):
            envelope[tuple(sorted(o.keys()))] += 1
        stack = [o]
        depth = 0
        while stack and depth < 20000:
            d = stack.pop()
            depth += 1
            if isinstance(d, dict):
                for k, v in d.items():
                    keys[str(k)] += 1
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(d, list):
                stack.extend(x for x in d[:50] if isinstance(x, (dict, list)))
print(f"  scanned {n} product snapshots; {len(keys)} distinct keys")
IDENT = re.compile(r"(gtin|upc|ean|mpn|barcode|brand|manufactur|model)", re.I)
hits = {k: v for k, v in keys.items() if IDENT.search(k)}
print("  IDENTIFIER-SHAPED KEYS:", hits or "NONE")
print("  top-level envelope shapes:")
for shape, c in envelope.most_common(5):
    print(f"     n={c}: {list(shape)}")
print("  all keys (sorted):", sorted(keys)[:80])

# 3. What is untrusted_content?
print("\n=== `untrusted_content` envelope sample ===")
cur.execute("""SELECT payload_json FROM supplier_snapshots
               WHERE kind='product' AND payload_json LIKE '%untrusted_content%' LIMIT 1""")
r = cur.fetchone()
if r:
    o = json.loads(r[0])
    def trim(v, d=0):
        if isinstance(v, dict):
            return {k: trim(x, d+1) for k, x in list(v.items())[:14]} if d < 3 else "{...}"
        if isinstance(v, list):
            return [trim(x, d+1) for x in v[:2]] + (["...%d more" % (len(v)-2)] if len(v) > 2 else [])
        s = str(v)
        return s[:110] + ("..." if len(s) > 110 else "")
    print(json.dumps(trim(o), indent=2)[:2600])
else:
    print("  (none)")
conn.close()
