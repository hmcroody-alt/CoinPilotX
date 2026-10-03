"""Read-only: the 22 stored decisions, re-read through the new verdict model.

Answers §31 -- "don't overwrite the 22 existing decisions; re-evaluate them
through the new model" -- by reading `marketplace_product_sources.attention_json`
(which is what `drafts.counts()` counts as `attention_products`, and therefore
what the iPhone's "need a decision" tile shows) and putting each flagged row
beside the state `catalog_readiness.survey` independently reached for it.

Writes nothing. The point is to find out whether the new model agrees with the
stored reasons before anything touches them.
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services import db  # noqa: E402
from services.business_os.suppliers import catalog_readiness as cr  # noqa: E402

SELLER = "mkt-seller:1"
CONN = "sc_366edc85175345eeb4ce86913ed21f1e"

conn = db.connect()
cur = conn.cursor()
cur.execute(
    "SELECT s.listing_id, s.attention_json, UPPER(COALESCE(s.sync_state,'')) AS sync_state, "
    "s.last_synced_at, l.title, l.status, l.approval_status, l.price_minor, l.quantity "
    "FROM marketplace_product_sources s "
    "JOIN marketplace_listings l ON l.id = s.listing_id "
    "WHERE s.seller_user_id=? AND s.supplier_connection_id=? "
    "AND s.business_id=? AND s.store_id=? "
    "AND COALESCE(s.attention_json,'') NOT IN ('','[]','null') "
    "ORDER BY s.listing_id ASC",
    (1, CONN, SELLER, SELLER))
flagged = [dict(row) for row in cur.fetchall()]
conn.close()

survey = cr.survey(SELLER, SELLER, 1, CONN)
by_id = {e["listing_id"]: e for e in survey["listings"]}

print("flagged source rows (the tile's 'need a decision'):", len(flagged))
reasons = Counter()
pairs = Counter()
for row in flagged:
    try:
        stored = json.loads(row["attention_json"] or "[]")
    except Exception:
        stored = ["<unparseable>"]
    stored = [str(r) for r in (stored or [])]
    for r in stored:
        reasons[r] += 1
    entry = by_id.get(row["listing_id"]) or {}
    pairs[(tuple(sorted(stored)), entry.get("state"), tuple(sorted(entry.get("blockers") or ())))] += 1

print("stored reasons:", dict(reasons.most_common()))
print()
print("stored reasons  ->  new state / new blockers")
for (stored, state, blockers), n in pairs.most_common():
    print("  %3d  %s -> %s %s" % (n, list(stored), state, list(blockers)))
print()
print("flagged rows the survey did not see:",
      [r["listing_id"] for r in flagged if r["listing_id"] not in by_id])
print("flagged and still purchasable:",
      [r["listing_id"] for r in flagged
       if (by_id.get(r["listing_id"]) or {}).get("purchasable")])
print("sync states among flagged:",
      dict(Counter(r["sync_state"] for r in flagged).most_common()))
