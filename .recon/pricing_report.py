"""Read-only: the per-product money report the owner chose ("Report, don't change").

Changes no price. Prints, for every listing carrying an underwater variant, the
listing's buyer-facing header price beside the worst variant's cost, freight and
shortfall -- and separately the handful that a buyer can purchase *today*, which
is the only part of this report that is losing money rather than merely able to.

Also reconciles the stored `attention_json` reasons against the new model's
blockers, so §31's "do not overwrite" can be grounded in which reasons the new
model is and is not entitled to restate.
"""
import json
import os
import sys

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services import db  # noqa: E402
from services.business_os.suppliers import catalog_readiness as cr  # noqa: E402

SELLER = "mkt-seller:1"
CONN = "sc_366edc85175345eeb4ce86913ed21f1e"


def money(cents):
    return "n/a" if cents is None else "$%0.2f" % (int(cents) / 100.0)


survey = cr.survey(SELLER, SELLER, 1, CONN)
by_id = {e["listing_id"]: e for e in survey["listings"]}
allowance = survey["shipping_allowance_cents"]

conn = db.connect()
cur = conn.cursor()
cur.execute(
    "SELECT s.listing_id, s.attention_json FROM marketplace_product_sources s "
    "WHERE s.seller_user_id=? AND s.supplier_connection_id=? "
    "AND s.business_id=? AND s.store_id=? "
    "AND COALESCE(s.attention_json,'') NOT IN ('','[]','null')",
    (1, CONN, SELLER, SELLER))
stored = {}
for row in cur.fetchall():
    row = dict(row)
    try:
        stored[row["listing_id"]] = sorted(json.loads(row["attention_json"] or "[]") or [])
    except Exception:
        stored[row["listing_id"]] = ["<unparseable>"]
conn.close()

underwater = sorted(survey["pricing"]["worst_by_listing"], key=lambda w: w["listing_id"])
flagged_below = {lid for lid, rs in stored.items() if "SELLING_BELOW_COST" in rs}
model_negative = {w["listing_id"] for w in underwater}

print("shipping allowance: %s (%s)" % (money(allowance), survey["shipping_allowance_source"]))
print()
print("=== listings with at least one variant priced under landed cost ===")
print("%-5s %-8s %-23s %-9s %-9s %-9s %-10s %s" %
      ("lid", "buyable", "state", "header", "worst", "landed", "shortfall", "variants"))
for w in underwater:
    e = by_id[w["listing_id"]]
    print("%-5s %-8s %-23s %-9s %-9s %-9s %-10s %s" % (
        w["listing_id"],
        "BUYABLE" if e["purchasable"] else "-",
        e["state"],
        e["price"].get("label") or "none",
        money(w.get("retail_cents")),
        money(w.get("landed_cost_cents")),
        money(w.get("shortfall_cents")),
        len(e["binding"]["candidates"])))

print()
print("=== the part that is losing money NOW (a buyer can complete checkout) ===")
for lid in sorted(l for l in model_negative if by_id[l]["purchasable"]):
    e = by_id[lid]
    w = next(x for x in underwater if x["listing_id"] == lid)
    print("  lid=%-4s %-16s header %-8s worst variant %s vs landed %s  shortfall %s  (%s)" % (
        lid, e["state"], e["price"].get("label") or "none",
        money(w.get("retail_cents")), money(w.get("landed_cost_cents")),
        money(w.get("shortfall_cents")), w.get("label")))
    print("         stored: %s | model: %s" % (
        stored.get(lid, []), sorted(e["blockers"] or ())))

print()
print("=== stored-vs-model reconciliation (§31) ===")
print("stored SELLING_BELOW_COST: %d   model NEGATIVE_MARGIN: %d   same set: %s" % (
    len(flagged_below), len(model_negative), flagged_below == model_negative))
print("stored below-cost the model does not restate:", sorted(flagged_below - model_negative))
print("model negative-margin with no stored flag:", sorted(model_negative - flagged_below))
margin_lost_only = sorted(lid for lid, rs in stored.items()
                          if rs == ["MARGIN_LOST"])
print("stored MARGIN_LOST and nothing else:", margin_lost_only)
for lid in margin_lost_only:
    e = by_id[lid]
    print("  lid=%-4s %-24s model blockers: %s" % (lid, e["state"], sorted(e["blockers"] or ())))
