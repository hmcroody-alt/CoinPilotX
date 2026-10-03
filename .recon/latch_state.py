"""Read-only: what actually holds the 152 back, and what would release them.

§12 forbids binding from accidentally meaning publishing, and §16 only allows a
new publication control if no existing column can carry the hold. Both questions
turn on one fact nobody has established yet: *which* publication rule each of
the 152 fails today.

It matters because the rules fail differently under automation. A listing held
back by `released` (its `status` is not public) is held by a column only a human
writes -- we proved no worker, sync tick, repair pass or revision can write
`status='published'`. A listing held back by `in_stock` or `priced` is held by
two columns the supplier pipeline *does* write: `revisions.apply_supplier_read`
fills quantity and price from the supplier read. For those rows the binding IS
the latch -- bind the variant, let the next tick fill the numbers, and the
listing becomes buyer-visible with nobody having chosen to publish it.

So this prints, for every supplier-backed listing, the first publication rule
that fails, cross-tabulated with whether it is one of the unbound 152. The
answer decides whether the new column defaults to "not vetoed" (safe, because
something else is already holding these rows) or whether it has to be written
onto the 152 before any bind is saved.
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

SELLER = "mkt-seller:1"
CONN = "sc_366edc85175345eeb4ce86913ed21f1e"

conn = db.connect()
cur = conn.cursor()

cur.execute(
    "SELECT s.listing_id, s.provider_variant_id, "
    "       l.status, l.approval_status, l.quantity, l.price_label, l.price_minor, "
    "       l.published_at, l.approved_at, "
    "       ms.status AS seller_status, ms.display_name AS seller_store_name "
    "FROM marketplace_product_sources s "
    "JOIN marketplace_listings l ON l.id = s.listing_id "
    "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
    "WHERE s.supplier_connection_id=? AND s.business_id=? AND s.store_id=? "
    "ORDER BY s.listing_id",
    (CONN, SELLER, SELLER))
rows = [dict(r) for r in cur.fetchall()]

evidence = gate.reconciliation_evidence(cur)

# Which rule fails first, by the real predicate table rather than a hand-rolled
# re-reading of the columns. `publication_blocker` walks PUBLICATION_RULES in
# order and names the first one that *proves* unmet, which is exactly the
# "what is holding this row" question -- and it returns "" for a row that is
# purchasable, so an empty key here means a buyer can reach it.
tally = Counter()
status_tally = Counter()
automation_held = []

for r in rows:
    bound = bool(str(r.get("provider_variant_id") or "").strip())
    blocker = lifecycle.publication_blocker(r) or "(none - purchasable)"
    tally[(bound, blocker)] += 1
    status_tally[(bound, str(r.get("status") or ""), str(r.get("approval_status") or ""))] += 1

    # The rows where the supplier pipeline alone could flip the verdict: the
    # merchant's own columns already say "sell this", and the only thing in the
    # way is a number `apply_supplier_read` writes.
    if (not bound
            and lifecycle.normalized(r.get("status")) in lifecycle.PUBLIC_STATUSES
            and blocker in {"in_stock", "priced"}):
        automation_held.append((r["listing_id"], blocker, r.get("quantity"),
                                r.get("price_minor"), r.get("status")))

print("drain latch: %s (running=%s)" % (evidence["state"], evidence["running"]))
print("supplier-backed listings:", len(rows))
print()

print("=== first failing publication rule, by whether a variant is bound ===")
for (bound, blocker), n in sorted(tally.items(), key=lambda kv: -kv[1]):
    print("  %-8s %-22s %s" % ("bound" if bound else "UNBOUND", blocker, n))
print("  total:", sum(tally.values()))
print()

print("=== stored status / approval_status ===")
for (bound, st, ap), n in sorted(status_tally.items(), key=lambda kv: -kv[1]):
    print("  %-8s status=%-16s approval=%-16s %s"
          % ("bound" if bound else "UNBOUND", st or "(empty)", ap or "(empty)", n))
print()

print("=== unbound rows the supplier pipeline alone could make buyable ===")
print("(status already public; only a quantity/price the sync writes is missing)")
for lid, blocker, qty, price, st in automation_held[:30]:
    print("  lid=%-5s blocked_by=%-10s quantity=%-6s price_minor=%-8s status=%s"
          % (lid, blocker, qty, price, st))
if len(automation_held) > 30:
    print("  ... and %d more" % (len(automation_held) - 30))
print("  count:", len(automation_held))

conn.close()
