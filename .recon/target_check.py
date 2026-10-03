"""Read-only: hand-check the below-target count instead of trusting it.

589 of 3797 surprised me, so this recomputes the same figure from the raw
quote fields a second way and samples actual rows. A count I cannot reproduce by
hand is a count I should not put in front of the owner.
"""
import os
import sys

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services.business_os.suppliers import catalog_readiness as cr  # noqa: E402

SELLER = "mkt-seller:1"
CONN = "sc_366edc85175345eeb4ce86913ed21f1e"

survey = cr.survey(SELLER, SELLER, 1, CONN)
rule = survey["pricing_rule"]
pct = float(rule["value"])

# Recomputed from the arithmetic in the brief (P = C / (1 - M)), not from
# `apply_rule`, so a bug in either shows up as a disagreement here.
band = {"below_landed": 0, "25_to_target": 0, "at_or_above_target": 0, "other_short": 0}
hand = 0
total = 0
samples = []
conn = None
for entry in survey["listings"]:
    pass

# The rollup's own population, rebuilt: survey does not return `economics`, so
# re-derive per listing through the same public helper it used.
from services import db  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import drafts, store_policy  # noqa: E402

conn = db.connect()
try:
    _, seller_user_id = drafts._scope(conn, SELLER, SELLER, 1, CONN)
    shipping, _ = store_policy.resolve_shipping_allowance(conn, SELLER, SELLER)
    cur = conn.cursor()
    cur.execute("SELECT s.listing_id FROM marketplace_product_sources s "
                "WHERE s.seller_user_id=? AND s.supplier_connection_id=? "
                "AND s.business_id=? AND s.store_id=? ORDER BY s.listing_id",
                (int(seller_user_id), CONN, SELLER, SELLER))
    ids = [int(dict(r)["listing_id"]) for r in cur.fetchall()]
    for lid in ids:
        rows = variants.variants_for(cur, lid)
        for v in drafts.priced_variants(rows, rule, shipping):
            total += 1
            retail = v["retail_cents"]
            landed = v["landed_cost_cents"]
            proposed = v["proposed_retail_cents"]
            if retail is None or landed is None:
                continue
            by_hand = int(round(landed / (1.0 - pct / 100.0)))
            if abs(by_hand - int(proposed)) > 1:
                samples.append(("ARITHMETIC DISAGREEMENT", lid, retail, landed,
                                proposed, by_hand))
            if retail < by_hand:
                hand += 1
            if retail < landed:
                band["below_landed"] += 1
            elif retail < by_hand:
                band["25_to_target"] += 1
            else:
                band["at_or_above_target"] += 1
            if len(samples) < 8 and retail >= by_hand:
                samples.append(("ok", lid, retail, landed, proposed, by_hand))
finally:
    conn.close()

print("rule: %s  (P = C / (1 - %.2f))" % (rule, pct / 100.0))
print("variants seen:", total)
print("below target, recomputed by hand:", hand)
print("below target, from the rollup   :", survey["pricing"]["below_target"])
print("agree:", hand == survey["pricing"]["below_target"])
print("bands:", band)
print("sum of bands:", sum(band.values()))
print()
print("samples (label, lid, retail, landed, apply_rule, by_hand):")
for s in samples[:8]:
    print("  ", s)
