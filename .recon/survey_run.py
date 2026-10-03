import os, sys
sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
from services.business_os.suppliers import catalog_readiness as cr
from collections import Counter

r = cr.survey("mkt-seller:1", "mkt-seller:1", 1, "sc_366edc85175345eeb4ce86913ed21f1e")
print("total:", r["total"], "| shipping:", r["shipping_allowance_cents"], r["shipping_allowance_source"])
print("rule:", r["pricing_rule"], "from", r["pricing_source"])
print("partition:", {s: r["partition"][s] for s in cr.STATES})
print("SUM:", sum(r["partition"].values()))
c = Counter()
for e in r["listings"]:
    for b in (e["blockers"] or ()): c[b] += 1
print("blockers:", dict(c.most_common()))
p = r["pricing"]
print("pricing: variants=%s unpriced=%s below_item=%s below_landed=%s" %
      (p["variants"], p["unpriced"], p["below_item_cost"], p["below_landed_cost"]))
print("below_target: %s of %s" % (p["below_target"], p["target_comparable"]))
print("margin_states:", p["margin_states"])
print("listings with underwater variant:", len(p["worst_by_listing"]))
print("recommended:", sum(1 for e in r["listings"] if e["binding"]["recommended"]))
