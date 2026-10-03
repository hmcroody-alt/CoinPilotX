"""Read-only: measure catalog semantic reality. Identity, identifiers, variants, media."""
import os, json, re, collections, psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()
def q(sql, p=()):
    cur.execute(sql, p); return cur.fetchall()

print("=== 1. LISTING SEGMENTATION: exists vs public vs supplier-backed ===")
for row in q("""SELECT status, approval_status, COUNT(*) FROM marketplace_listings
                GROUP BY 1,2 ORDER BY 3 DESC"""):
    print("  status=%-12s approval=%-16s n=%s" % row)
print("  supplier-backed (has product_sources row):",
      q("SELECT COUNT(DISTINCT listing_id) FROM marketplace_product_sources")[0][0])
print("  listings with >=1 variant:",
      q("SELECT COUNT(DISTINCT listing_id) FROM marketplace_listing_variants")[0][0])
print("  listings with a numeric price_minor:",
      q("SELECT COUNT(*) FROM marketplace_listings WHERE price_minor IS NOT NULL AND price_minor>0")[0][0])
print("  price_label distinct values (top):")
for row in q("""SELECT price_label, COUNT(*) FROM marketplace_listings GROUP BY 1 ORDER BY 2 DESC LIMIT 6"""):
    print("     %-28r n=%s" % row)

print("\n=== 2. PROVIDER / SUPPLIER IDENTITY ===")
for row in q("""SELECT provider, fulfillment_mode, sync_state, COUNT(*)
                FROM marketplace_product_sources GROUP BY 1,2,3 ORDER BY 4 DESC"""):
    print("  provider=%-10s mode=%-12s sync=%-12s n=%s" % row)
print("  distinct provider_product_id:",
      q("SELECT COUNT(DISTINCT provider_product_id) FROM marketplace_product_sources")[0][0],
      "over", q("SELECT COUNT(*) FROM marketplace_product_sources")[0][0], "rows")
print("  provider_product_id reused by >1 listing (DUPLICATE CANDIDATES):")
dups = q("""SELECT provider, provider_product_id, COUNT(*) c,
                   STRING_AGG(listing_id::text, ',' ORDER BY listing_id)
            FROM marketplace_product_sources
            GROUP BY 1,2 HAVING COUNT(*)>1 ORDER BY c DESC LIMIT 15""")
for row in dups: print("     provider=%s ppid=%s n=%s listings=%s" % row)
if not dups: print("     (none)")
print("  sample provider_product_id values:",
      [r[0] for r in q("SELECT DISTINCT provider_product_id FROM marketplace_product_sources LIMIT 5")])

print("\n=== 3. SKU TRUTH (is variant.sku a real SKU? unique? == provider id?) ===")
tot = q("SELECT COUNT(*) FROM marketplace_listing_variants")[0][0]
print("  variants:", tot)
print("  sku non-empty:", q("SELECT COUNT(*) FROM marketplace_listing_variants WHERE COALESCE(sku,'')<>''")[0][0])
print("  distinct sku:", q("SELECT COUNT(DISTINCT sku) FROM marketplace_listing_variants WHERE COALESCE(sku,'')<>''")[0][0])
print("  provider_variant_id non-empty:",
      q("SELECT COUNT(*) FROM marketplace_listing_variants WHERE COALESCE(provider_variant_id,'')<>''")[0][0])
print("  distinct provider_variant_id:",
      q("SELECT COUNT(DISTINCT provider_variant_id) FROM marketplace_listing_variants WHERE COALESCE(provider_variant_id,'')<>''")[0][0])
print("  rows where sku == provider_variant_id (SKU IS the supplier id):",
      q("""SELECT COUNT(*) FROM marketplace_listing_variants
           WHERE COALESCE(sku,'')<>'' AND sku = provider_variant_id""")[0][0])
print("  SKU duplicated across DIFFERENT listings (not globally unique):")
for row in q("""SELECT sku, COUNT(DISTINCT listing_id) c FROM marketplace_listing_variants
                WHERE COALESCE(sku,'')<>'' GROUP BY 1 HAVING COUNT(DISTINCT listing_id)>1
                ORDER BY c DESC LIMIT 8"""):
    print("     sku=%s listings=%s" % row)
print("  sample skus:", [r[0] for r in q("SELECT sku FROM marketplace_listing_variants WHERE COALESCE(sku,'')<>'' LIMIT 6")])
# Would any sku pass a GTIN check? (8/12/13/14 digits + checksum)
def gtin_ok(s):
    s = (s or "").strip()
    if not re.fullmatch(r"\d{8}|\d{12}|\d{13}|\d{14}", s): return False
    ds = [int(c) for c in s][::-1]
    tot = sum(d * (3 if i % 2 else 1) for i, d in enumerate(ds[1:], start=0))
    return (10 - tot % 10) % 10 == ds[0]
skus = [r[0] for r in q("SELECT DISTINCT sku FROM marketplace_listing_variants WHERE COALESCE(sku,'')<>''")]
print("  skus that are a structurally VALID GTIN:", sum(1 for s in skus if gtin_ok(s)), "/", len(skus))

print("\n=== 4. VARIANT OPTION SEMANTICS (positional vs named) ===")
rows = q("SELECT options_json, variant_key FROM marketplace_listing_variants LIMIT 4000")
shapes = collections.Counter(); keys = collections.Counter(); named = 0
for oj, vk in rows:
    try: o = json.loads(oj or "null")
    except Exception: shapes["UNPARSEABLE"] += 1; continue
    if isinstance(o, dict):
        shapes["dict"] += 1
        for k in o: keys[k] += 1
        if any(not re.fullmatch(r"(option|opt|attr)?_?\d+", str(k), re.I) for k in o): named += 1
    elif isinstance(o, list): shapes["list(positional)"] += 1
    else: shapes[type(o).__name__] += 1
print("  options_json shapes:", dict(shapes))
print("  dict keys seen (top):", keys.most_common(12))
print("  rows with at least one SEMANTIC (non-positional) key:", named)
print("  sample options_json:", [r[0] for r in rows[:5]])
print("  sample variant_key:", [r[1] for r in rows[:5]])

print("\n=== 5. PER-VARIANT PRICE / COST / STOCK / IMAGE ===")
print("  variants with price_cents>0:", q("SELECT COUNT(*) FROM marketplace_listing_variants WHERE COALESCE(price_cents,0)>0")[0][0])
print("  variants with cost_cents>0 (PRIVATE supplier cost):", q("SELECT COUNT(*) FROM marketplace_listing_variants WHERE COALESCE(cost_cents,0)>0")[0][0])
print("  listings whose variants differ in price:",
      q("""SELECT COUNT(*) FROM (SELECT listing_id FROM marketplace_listing_variants
           GROUP BY 1 HAVING COUNT(DISTINCT price_cents)>1) t""")[0][0])
print("  stock_state distribution:", q("SELECT stock_state, COUNT(*) FROM marketplace_listing_variants GROUP BY 1 ORDER BY 2 DESC"))
print("  variant status distribution:", q("SELECT status, COUNT(*) FROM marketplace_listing_variants GROUP BY 1 ORDER BY 2 DESC"))
print("  >>> NOTE: marketplace_listing_variants has NO image column. Variant->image mapping cannot exist here.")

print("\n=== 6. MEDIA / IMAGE RELATIONSHIPS ===")
print("  marketplace_product_media rows:", q("SELECT COUNT(*) FROM marketplace_product_media")[0][0])
print("  listings with cover_image_url:", q("SELECT COUNT(*) FROM marketplace_listings WHERE COALESCE(cover_image_url,'')<>''")[0][0])
print("  listings with gallery_json:", q("SELECT COUNT(*) FROM marketplace_listings WHERE COALESCE(gallery_json,'')NOT IN ('','[]','null')")[0][0])
g = q("SELECT id, gallery_json FROM marketplace_listings WHERE COALESCE(gallery_json,'') NOT IN ('','[]','null') LIMIT 400")
cnt = []
for lid, gj in g:
    try:
        v = json.loads(gj)
        if isinstance(v, list): cnt.append(len(v))
    except Exception: pass
print("  gallery sizes: n=%d min=%s max=%s avg=%.1f" % (len(cnt), min(cnt) if cnt else 0, max(cnt) if cnt else 0, (sum(cnt)/len(cnt)) if cnt else 0))

print("\n=== 7. CATEGORY / TITLE / DESCRIPTION QUALITY ===")
print("  distinct category:", q("SELECT COUNT(DISTINCT category) FROM marketplace_listings")[0][0])
for row in q("SELECT category, COUNT(*) FROM marketplace_listings GROUP BY 1 ORDER BY 2 DESC LIMIT 12"):
    print("     %-34r n=%s" % row)
print("  subcategory non-empty:", q("SELECT COUNT(*) FROM marketplace_listings WHERE COALESCE(subcategory,'')<>''")[0][0])
print("  title length: ", q("""SELECT MIN(LENGTH(title)), ROUND(AVG(LENGTH(title))), MAX(LENGTH(title)),
                                      COUNT(*) FILTER (WHERE LENGTH(title)>70),
                                      COUNT(*) FILTER (WHERE title = UPPER(title) AND title ~ '[A-Z]{4,}')
                               FROM marketplace_listings WHERE title IS NOT NULL""")[0],
      " (min, avg, max, >70chars, ALLCAPS)")
print("  duplicate titles:", q("""SELECT COUNT(*) FROM (SELECT title FROM marketplace_listings
                                  GROUP BY 1 HAVING COUNT(*)>1) t""")[0][0])
print("  description empty:", q("SELECT COUNT(*) FROM marketplace_listings WHERE COALESCE(description,'')=''")[0][0])
print("  description contains HTML:", q("SELECT COUNT(*) FROM marketplace_listings WHERE description ~ '<[a-zA-Z/]'")[0][0])
print("  sample supplier-backed titles:")
for (t,) in q("""SELECT l.title FROM marketplace_listings l
                 JOIN marketplace_product_sources s ON s.listing_id=l.id LIMIT 8"""):
    print("     %r" % (t,))

print("\n=== 8. RAW SUPPLIER PAYLOAD: does the SUPPLIER give us brand/gtin? ===")
print("  supplier_snapshots by kind:", q("SELECT kind, COUNT(*) FROM supplier_snapshots GROUP BY 1 ORDER BY 2 DESC"))
seen = collections.Counter(); ident = collections.Counter()
IDENT = re.compile(r"(gtin|upc|ean|mpn|barcode|brand|manufactur|model)", re.I)
for (pj,) in q("SELECT payload_json FROM supplier_snapshots WHERE kind IS NOT NULL LIMIT 600"):
    try: o = json.loads(pj or "null")
    except Exception: continue
    def walk(d, pre=""):
        if isinstance(d, dict):
            for k, v in d.items():
                seen[k] += 1
                if IDENT.search(str(k)): ident[k] += 1
                if isinstance(v, (dict, list)) and pre.count(".") < 2: walk(v, pre + "." + str(k))
        elif isinstance(d, list):
            for v in d[:3]: walk(v, pre)
    walk(o)
print("  most common supplier payload keys:", seen.most_common(30))
print("  >>> IDENTIFIER-SHAPED keys in raw supplier payload:", ident.most_common(20) or "NONE")
conn.close()
