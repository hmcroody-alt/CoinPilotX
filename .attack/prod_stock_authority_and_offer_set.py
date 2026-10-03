"""Is `marketplace_listings.quantity` the real stock authority, and who is offered?

152 of 196 published+approved production listings fail the public predicate's
stock clause (148 `NULL`, 4 zero) and answer 404. Before calling that a defect I
have to rule out the two ways it could be correct:

1. **The column might not be the authority.** This is a dropship catalogue;
   stock could live on supplier variant rows, with `l.quantity` vestigial. If
   the 152 carry variant stock, the defect is that the public predicate reads
   the wrong column -- a different and worse finding than "148 rows never got a
   quantity". If they carry no stock anywhere, the data is simply absent.
2. **The sitemap might be offering them anyway.** If any of the 152 appears in
   a production `<loc>`, invariant 9 is violated *in production* -- I measured
   206/206 against a 16-listing dev copy, which cannot see this. If none is
   offered, the offer set is honest and the finding is about discoverability
   rather than about broken promises.

Both readings come from production, read-only, plus anonymous GETs to confirm
the 404s are real on the wire rather than inferred from SQL. A sample of the
in-stock 44 is the control: if those 404 too, the stock clause is not what
decides and I am reading the wrong predicate.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import psycopg2

BASE = "https://pulsesoc.com"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
RE_ID = re.compile(r"/pulse/marketplace/(\d+)\b")


def get(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; Agent12-readonly-probe)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def locs(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    return [el.text.strip() for el in root.findall(".//s:loc", NS) if el is not None and el.text]


def main():
    conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    released = "LOWER(COALESCE(l.status,'')) IN ('published','live','active')"
    approved = ("LOWER(COALESCE(l.approval_status,''))='approved' "
                "AND LOWER(COALESCE(ms.status,''))='approved'")
    join = ("FROM marketplace_listings l "
            "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id")
    intangible = ("LOWER(COALESCE(l.product_type,l.listing_type,'')) "
                  "IN ('digital','course','service','event','booking')")

    cur.execute(f"SELECT l.id {join} WHERE {released} AND {approved} "
                f"AND NOT ({intangible}) AND COALESCE(l.quantity,0) <= 0 ORDER BY l.id")
    retired = [r[0] for r in cur.fetchall()]
    cur.execute(f"SELECT l.id {join} WHERE {released} AND {approved} "
                f"AND COALESCE(l.quantity,0) > 0 ORDER BY l.id")
    survivors = [r[0] for r in cur.fetchall()]

    print("=" * 96)
    print("IS quantity THE STOCK AUTHORITY, AND WHO DOES PRODUCTION OFFER?")
    print("=" * 96)
    print(f"  retired by the stock clause {len(retired)}   surviving {len(survivors)}")

    # --- 1. does stock live on variant rows instead? -------------------------
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE %s OR table_name LIKE %s",
                ("%variant%", "%inventory%"))
    tables = sorted(r[0] for r in cur.fetchall())
    print(f"\n  variant/inventory tables in production: {tables or 'NONE'}")
    for table in tables:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s", (table,))
        cols = {r[0] for r in cur.fetchall()}
        key = next((c for c in ("listing_id", "product_id", "marketplace_listing_id")
                    if c in cols), None)
        stock = sorted(c for c in cols if "quant" in c or "stock" in c or "inventor" in c)
        cur.execute(f"SELECT count(*) FROM {table}")
        total = cur.fetchone()[0]
        print(f"      {table:34s} rows={total:6d} key={key} stock-like={stock or 'NONE'}")
        if key and stock and retired:
            col = stock[0]
            cur.execute(
                f"SELECT count(DISTINCT {key}) FROM {table} "
                f"WHERE {key} = ANY(%s) AND COALESCE({col},0) > 0", (retired,))
            with_stock = cur.fetchone()[0]
            print(f"          retired listings carrying {col}>0 here: {with_stock}"
                  f" of {len(retired)}")

    # --- 2. what does production actually offer? ----------------------------
    status, body = get(f"{BASE}/sitemap.xml")
    children = [u for u in locs(body)] or []
    offered_ids = set()
    offered_total = 0
    print(f"\n  /sitemap.xml -> HTTP {status}, {len(children)} children")
    for child in children:
        cstatus, cbody = get(child)
        child_locs = locs(cbody)
        offered_total += len(child_locs)
        ids = {int(m.group(1)) for m in (RE_ID.search(u) for u in child_locs) if m}
        offered_ids |= ids
        print(f"      {child.rsplit('/', 1)[-1]:28s} HTTP {cstatus} locs={len(child_locs):4d} "
              f"product ids={len(ids)}")

    print(f"\n  distinct URLs offered by production: {offered_total}")
    print(f"  product ids offered: {len(offered_ids)}")
    offered_but_retired = sorted(offered_ids & set(retired))
    print(f"  OFFERED BUT RETIRED BY THE STOCK CLAUSE: {len(offered_but_retired)}"
          f" {offered_but_retired[:20]}")

    # --- 3. confirm on the wire ---------------------------------------------
    print("\n  wire check (anonymous GET):")
    sample = retired[:6]
    for listing_id in sample:
        code, _ = get(f"{BASE}/pulse/marketplace/{listing_id}")
        print(f"      retired  /pulse/marketplace/{listing_id:<6d} HTTP {code}")
    for listing_id in survivors[:4]:
        code, _ = get(f"{BASE}/pulse/marketplace/{listing_id}")
        print(f"      CONTROL  /pulse/marketplace/{listing_id:<6d} HTTP {code}")

    print()
    print("=" * 96)
    print("  If the controls answer 200 and the retired answer 404, the stock clause is")
    print("  what decides. If `offered but retired` is non-zero, invariant 9 is violated")
    print("  in production and that outranks everything else in this probe.")

    conn.close()


if __name__ == "__main__":
    main()
