"""Read-only: what the new checkout gate would do to production, per listing.

§30 wants the exact count that stops being buyable before anything ships. This
runs the *real* `marketplace_supplier_checkout.evaluate` against production --
it issues only SELECTs -- and prints, for every supplier-backed listing, the
facts the three new/changed branches turn on:

  * bound?                 -- source.provider_variant_id set at all
  * bound row found?        -- and does it name a variant this listing has
  * bound variant stock     -- the new, variant-exact stock question
  * sibling stock           -- the old, all-variants question, for the diff
  * retail vs landed        -- the loss check

and then the decision, split by whether a buyer can reach checkout at all. A
listing nobody can buy today cannot be taken off sale by this change, so the
number that matters is the one restricted to purchasable rows.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services import db  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services import marketplace_supplier_checkout as gate  # noqa: E402
from services import marketplace_variants as mv  # noqa: E402
from services import marketplace_supplier_schema as schema  # noqa: E402
from services.business_os.suppliers import pricing  # noqa: E402

SELLER = "mkt-seller:1"
CONN = "sc_366edc85175345eeb4ce86913ed21f1e"
OUT = schema.STOCK_OUT_OF_STOCK


def money(c):
    return "n/a" if c is None else "$%0.2f" % (int(c) / 100.0)


conn = db.connect()
cur = conn.cursor()

# The seller columns are not decoration: lifecycle's rules return None -- "no
# evidence" -- for any column the row was never projected with, so a row missing
# seller_status/seller_store_name would be called purchasable on silence. Read
# the real seller row and let the real predicate judge it.
cur.execute(
    # business_id/store_id are what `_shipping_allowance` keys the store policy
    # on. Omitting them does not change any decision -- `evaluate` loads the
    # source row itself with `SELECT *` -- but it silently understates the
    # landed-cost column printed below, which is the number being reviewed.
    "SELECT s.listing_id, s.provider_variant_id, s.sync_state, s.last_synced_at, "
    "       s.business_id, s.store_id, "
    "       l.status, l.approval_status, l.quantity, l.price_label, l.price_minor, "
    "       l.product_type, l.listing_type, l.title, "
    "       ms.status AS seller_status, ms.display_name AS seller_store_name "
    "FROM marketplace_product_sources s "
    "JOIN marketplace_listings l ON l.id = s.listing_id "
    "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
    "WHERE s.supplier_connection_id=? AND s.business_id=? AND s.store_id=? "
    "ORDER BY s.listing_id",
    (CONN, SELLER, SELLER))
rows = [dict(r) for r in cur.fetchall()]

evidence = gate.reconciliation_evidence(cur)
print("drain latch: %s (running=%s)" % (evidence["state"], evidence["running"]))
print("listings:", len(rows))
print()

tally = Counter()
buyable_refusals = []
changed_stock_reading = []

for r in rows:
    lid = int(r["listing_id"])
    bound_id = str(r.get("provider_variant_id") or "").strip()
    vrows = [v for v in mv.variants_for(cur, lid)
             if str(v.get("status") or "active").strip().lower() == "active"]
    bound_row = next((v for v in vrows
                      if str(v.get("provider_variant_id") or "").strip() == bound_id
                      and bound_id), None)

    # The old question and the new one, side by side.
    all_out = bool(vrows) and all(
        str(v.get("stock_state") or "").strip().upper() == OUT for v in vrows)
    bound_out = (bound_row is not None and
                 str(bound_row.get("stock_state") or "").strip().upper() == OUT)

    purchasable = lifecycle.is_purchasable(r)
    decision = gate.evaluate(cur, listing_id=lid, evidence=evidence)
    key = (decision["decision"], decision["reason"] or "-")
    tally[(purchasable, key[0], key[1])] += 1

    if purchasable and decision["decision"] == gate.DECISION_REFUSE:
        landed = pricing.basis(bound_row.get("cost_cents") if bound_row else None,
                               gate._shipping_allowance(cur, r))[1]
        buyable_refusals.append((
            lid, decision["reason"], len(vrows), bound_id or "-",
            "found" if bound_row else "MISSING",
            money(bound_row.get("price_cents")) if bound_row else "n/a",
            money(landed), (r.get("title") or "")[:34]))

    if bound_out and not all_out:
        changed_stock_reading.append((lid, bound_id, len(vrows), purchasable))

print("=== decision by (purchasable, decision, reason) ===")
for (purch, dec, reason), n in sorted(tally.items(), key=lambda kv: -kv[1]):
    print("  %-14s %-14s %-26s %s" % ("BUYABLE" if purch else "not-buyable",
                                      dec, reason, n))
print("  total:", sum(tally.values()))
print()

print("=== listings a buyer can reach today that the gate would now refuse ===")
print("%-5s %-26s %-4s %-10s %-8s %-9s %-9s %s" %
      ("lid", "reason", "vars", "bound", "row", "retail", "landed", "title"))
for row in buyable_refusals:
    print("%-5s %-26s %-4s %-10s %-8s %-9s %-9s %s" % row)
print("  count:", len(buyable_refusals))
print()

print("=== where variant-exact stock disagrees with the old all-variants read ===")
print("(bound variant out of stock, a sibling still in stock)")
for lid, bid, n, purch in changed_stock_reading:
    print("  lid=%-5s bound=%-10s variants=%-4s buyable=%s" % (lid, bid, n, purch))
print("  count:", len(changed_stock_reading))

conn.close()
