"""Read-only: the EXISTS -> PUBLIC -> SEARCH -> MERCHANT funnel, and what raw snapshots still hold."""
import os, json, re, collections, psycopg2

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()
def one(sql, p=()):
    cur.execute(sql, p); return cur.fetchone()[0]
def all_(sql, p=()):
    cur.execute(sql, p); return cur.fetchall()

JOIN = """FROM marketplace_listings l
          LEFT JOIN users u ON u.user_id=l.seller_user_id
          LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id"""

print("=== SEARCH-ELIGIBILITY FUNNEL (real predicates) ===")
print("  1 EXISTS                         :", one("SELECT COUNT(*) FROM marketplace_listings"))
print("  2 status+approval ok             :", one(f"""SELECT COUNT(*) {JOIN} WHERE
    LOWER(COALESCE(l.status,'')) IN ('published','live','active')
    AND LOWER(COALESCE(l.approval_status,''))='approved'"""))
print("  3 + seller approved              :", one(f"""SELECT COUNT(*) {JOIN} WHERE
    LOWER(COALESCE(l.status,'')) IN ('published','live','active')
    AND LOWER(COALESCE(l.approval_status,''))='approved'
    AND LOWER(COALESCE(ms.status,''))='approved'"""))
print("  4 + quantity>0 OR digital (PUBLIC):", one(f"""SELECT COUNT(*) {JOIN} WHERE
    LOWER(COALESCE(l.status,'')) IN ('published','live','active')
    AND LOWER(COALESCE(l.approval_status,''))='approved'
    AND LOWER(COALESCE(ms.status,''))='approved'
    AND (LOWER(COALESCE(l.product_type,l.listing_type,''))
         IN ('digital','course','service','event','booking') OR COALESCE(l.quantity,0)>0)"""))
print("  5 + discovery-visible seller     :", one(f"""SELECT COUNT(*) {JOIN} WHERE
    LOWER(COALESCE(l.status,'')) IN ('published','live','active')
    AND LOWER(COALESCE(l.approval_status,''))='approved'
    AND LOWER(COALESCE(ms.status,''))='approved'
    AND (LOWER(COALESCE(l.product_type,l.listing_type,''))
         IN ('digital','course','service','event','booking') OR COALESCE(l.quantity,0)>0)
    AND (COALESCE(u.hidden_from_discovery,0)=0
         AND COALESCE(u.account_status,'active') NOT IN ('deleted','suspended','banned','disabled','disabled_qa'))"""))
print("\n  WHY listings drop out:")
print("   published+approved but quantity=0/NULL:", one("""SELECT COUNT(*) FROM marketplace_listings
    WHERE LOWER(COALESCE(status,''))='published' AND LOWER(COALESCE(approval_status,''))='approved'
      AND COALESCE(quantity,0)<=0
      AND LOWER(COALESCE(product_type,listing_type,'')) NOT IN ('digital','course','service','event','booking')"""))
print("   product_type distribution:", all_("SELECT product_type, COUNT(*) FROM marketplace_listings GROUP BY 1 ORDER BY 2 DESC"))
print("   quantity distribution:", all_("""SELECT CASE WHEN quantity IS NULL THEN 'NULL' WHEN quantity<=0 THEN '<=0'
    ELSE '>0' END, COUNT(*) FROM marketplace_listings GROUP BY 1"""))
print("   MERCHANT-eligible extra gates (price + 40-char desc):")
print("     of PUBLIC, with usable price_label:", one(f"""SELECT COUNT(*) {JOIN} WHERE
    LOWER(COALESCE(l.status,'')) IN ('published','live','active')
    AND LOWER(COALESCE(l.approval_status,''))='approved'
    AND LOWER(COALESCE(ms.status,''))='approved' AND COALESCE(l.quantity,0)>0
    AND COALESCE(l.price_label,'') ~ '[0-9]'"""))
print("     of PUBLIC, desc >=40 chars:", one(f"""SELECT COUNT(*) {JOIN} WHERE
    LOWER(COALESCE(l.status,'')) IN ('published','live','active')
    AND LOWER(COALESCE(l.approval_status,''))='approved'
    AND LOWER(COALESCE(ms.status,''))='approved' AND COALESCE(l.quantity,0)>0
    AND LENGTH(COALESCE(l.short_description, l.description,''))>=40"""))

print("\n=== OPTION-SPLIT INTEGRITY (CJ sends 'Blue-10.5g'; split on '-') ===")
rows = all_("SELECT listing_id, options_json FROM marketplace_listing_variants")
nopt = collections.Counter(); suspicious = []
for lid, oj in rows:
    try: o = json.loads(oj or "[]")
    except Exception: nopt["unparseable"] += 1; continue
    nopt[len(o)] += 1
    if len(o) >= 3: suspicious.append((lid, oj))
print("  options-per-variant histogram:", dict(nopt))
print("  variants with >=3 options (likely a hyphenated VALUE mis-split):", len(suspicious))
for lid, oj in suspicious[:6]: print("     listing", lid, oj[:150])
# values that still contain a hyphen => split did not fully separate
hy = [oj for _, oj in rows if '-' in (oj or '')]
print("  variants whose option VALUE still contains '-':", len(hy))
for oj in hy[:5]: print("     ", oj[:140])

print("\n=== WHAT RAW SNAPSHOTS STILL HOLD THAT PULSESOC DROPPED ===")
# latest product snapshot per resource_id
cur.execute("""
  SELECT DISTINCT ON (s.resource_id) s.resource_id, s.payload_json
  FROM supplier_snapshots s WHERE s.kind='product'
  ORDER BY s.resource_id, s.created_at DESC""")
snaps = cur.fetchall()
print("  distinct products with a product snapshot:", len(snaps))
img_per_prod = []; var_img = 0; var_tot = 0; distinct_imgs = []; gal = []
color_to_img = 0; color_img_consistent = 0
for rid, pj in snaps:
    try: o = json.loads(pj or "{}")
    except Exception: continue
    gal.append(len(o.get("images") or []))
    vs = o.get("variants") or []
    imgs = set()
    bycolor = collections.defaultdict(set)
    for v in vs:
        var_tot += 1
        im = v.get("image")
        if im: var_img += 1; imgs.add(im)
        opts = str(v.get("options") or "")
        if im and opts:
            bycolor[opts.split("-")[0].strip().casefold()].add(im)
    distinct_imgs.append(len(imgs))
    if bycolor:
        color_to_img += 1
        if all(len(s) == 1 for s in bycolor.values()): color_img_consistent += 1
print("  supplier GALLERY images per product: min=%s max=%s avg=%.1f" % (min(gal), max(gal), sum(gal)/len(gal)))
print("    >>> PulseSoc persists only 6 gallery_json rows and 1 image each.")
print("  supplier per-VARIANT image present: %d/%d variants" % (var_img, var_tot))
print("  distinct variant images per product: min=%s max=%s avg=%.1f" % (
      min(distinct_imgs), max(distinct_imgs), sum(distinct_imgs)/len(distinct_imgs)))
print("  products where first-option(colour) -> exactly ONE image: %d/%d" % (color_img_consistent, color_to_img))
print("    >>> variant->image mapping is RECOVERABLE from snapshots but has NO column to live in.")
conn.close()
