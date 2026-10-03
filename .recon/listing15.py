"""Read-only: why doesn't the new gate refuse listing 15?

§2 names six loss-makers (15, 17, 21, 27, 36, 37). The preview refuses five of
them and lets 15 through, so either 15 is not reachable at checkout or the gate
is asking a narrower question than the survey did. §30 forbids an unexplained
remainder, so this resolves which.

It prints, for 15 and for its five siblings, every active variant's retail and
landed cost, marking the bound one -- the only variant `fulfillment.create_intent`
will accept -- so the difference between "this listing contains a loss-making
variant" and "the variant we would actually order loses money" is visible.
"""
import os
import sys

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services import db  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services import marketplace_supplier_checkout as gate  # noqa: E402
from services import marketplace_variants as mv  # noqa: E402
from services.business_os.suppliers import pricing  # noqa: E402

SELLER = "mkt-seller:1"
CONN = "sc_366edc85175345eeb4ce86913ed21f1e"
LIDS = (15, 17, 21, 27, 35, 36, 37)


def money(c):
    return "n/a" if c is None else "$%0.2f" % (int(c) / 100.0)


conn = db.connect()
cur = conn.cursor()
evidence = gate.reconciliation_evidence(cur)

for lid in LIDS:
    cur.execute(
        "SELECT s.listing_id, s.provider_variant_id, s.sync_state, s.business_id, "
        "       s.store_id, s.attention_json, "
        "       l.status, l.approval_status, l.quantity, l.price_label, l.price_minor, "
        "       l.product_type, l.listing_type, l.title, "
        "       ms.status AS seller_status, ms.display_name AS seller_store_name "
        "FROM marketplace_product_sources s "
        "JOIN marketplace_listings l ON l.id = s.listing_id "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
        "WHERE s.listing_id=? AND s.supplier_connection_id=?", (lid, CONN))
    row = cur.fetchone()
    if row is None:
        print("lid=%s  NO SOURCE ROW" % lid)
        continue
    r = dict(row)
    bound_id = str(r.get("provider_variant_id") or "").strip()
    allowance = gate._shipping_allowance(cur, r)
    decision = gate.evaluate(cur, listing_id=lid, evidence=evidence)

    print("=" * 78)
    print("lid=%-4s %s" % (lid, (r.get("title") or "")[:56]))
    print("  purchasable=%s  decision=%s/%s  listing_price=%s  allowance=%s"
          % (lifecycle.is_purchasable(r), decision["decision"],
             decision["reason"] or "-", money(r.get("price_minor")),
             money(allowance)))
    print("  attention=%s" % (r.get("attention_json") or "-"))

    vrows = [v for v in mv.variants_for(cur, lid)
             if str(v.get("status") or "active").strip().lower() == "active"]
    losers = []
    for v in vrows:
        _, landed = pricing.basis(v.get("cost_cents"), allowance)
        retail = v.get("price_cents")
        bad = (retail is not None and landed is not None and int(retail) < int(landed))
        if bad:
            losers.append(v)
        is_bound = (str(v.get("provider_variant_id") or "").strip() == bound_id
                    and bool(bound_id))
        if bad or is_bound:
            print("   %s%s vid=%-38s retail=%-9s landed=%-9s %s"
                  % ("BOUND " if is_bound else "      ",
                     "LOSS" if bad else "ok  ",
                     str(v.get("provider_variant_id") or "-")[:38],
                     money(retail), money(landed),
                     str(v.get("stock_state") or "")[:12]))
    print("  active variants=%s  loss-making variants=%s" % (len(vrows), len(losers)))

conn.close()
