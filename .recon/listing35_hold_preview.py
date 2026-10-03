"""Read-only §30 preview for the one mutation §29 separately authorises: hold 35.

§1 asks for listing 35 to be taken out of buyer purchase availability
immediately, using the safest *existing* canonical mechanism, preserving the
listing, its supplier provenance, pricing, variants, seller ownership, approval
history and diagnostic evidence. §29 authorises this one row while the 152 stay
untouched. §30 wants the exact counts before any mutation runs.

What this script is for, and what it establishes
------------------------------------------------
Three questions, asked of production rather than argued:

  1. Is the loss already stopped? The checkout gate shipped in f4daceb34 and
     refuses a known-unshippable supplier purchase server-side. If 35 is already
     refused there, the hold is the durable fix rather than the emergency one,
     and that changes what "immediately" has to mean.
  2. What would the hold actually change? The two §30 numbers, computed by
     running the real predicate against the real row with the control flipped --
     not by reasoning about it.
  3. Can it be applied at all yet? `commerce_publication_enabled` does not exist
     in production until this branch deploys and `init_db()` adds it. A hold
     written before that raises `UndefinedColumn`. Reported rather than
     discovered, because "apply the hold" is not an action that can be taken
     today and a report claiming otherwise would be false.

Read-only: every statement is a SELECT. No write, no DDL, no commit. The
projection in step 2 is computed in Python against a copy of the row.
"""
import os
import sys

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services import db  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services import marketplace_supplier_checkout as gate  # noqa: E402

TARGET = 35

conn = db.connect()
cur = conn.cursor()


def bar(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# --- Step 0: the row, and the provenance §1 requires preserved --------------
cur.execute(
    "SELECT l.*, ms.status AS seller_status, ms.display_name AS seller_name, "
    "       s.provider, s.provider_product_id, s.provider_variant_id, "
    "       s.supplier_cost_cents, s.fulfillment_mode, s.sync_state "
    "FROM marketplace_listings l "
    "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
    "LEFT JOIN marketplace_product_sources s ON s.listing_id = l.id "
    "WHERE l.id = %s LIMIT 1", (TARGET,))
raw = cur.fetchone()
if raw is None:
    print("Listing %d is not present in this database. Nothing to preview."
          % TARGET)
    raise SystemExit(1)
row = dict(raw)

bar("STEP 0 -- listing %d as production holds it today" % TARGET)
for key in ("id", "title", "status", "approval_status", "price_label",
            "price_minor", "quantity", "currency", "seller_user_id",
            "seller_status", "provider", "provider_product_id",
            "provider_variant_id", "supplier_cost_cents", "fulfillment_mode",
            "sync_state"):
    print("  %-24s %r" % (key, row.get(key)))
print()
print("  is_public        : %r" % lifecycle.is_public(row))
print("  is_purchasable   : %r" % lifecycle.is_purchasable(row))
print("  first unmet rule : %r" % (lifecycle.publication_blocker(row) or
                                   "(none -- purchasable)"))
print()
print("  This is the UNSHIPPABLE_LIVE row: published, approved, priced, in")
print("  stock, and with provider_variant_id NULL, so nothing can be ordered")
print("  for it. A buyer can pay and no supplier order can be placed.")

# --- Step 1: is the loss already stopped at checkout? -----------------------
bar("STEP 1 -- does the deployed checkout gate already refuse it?")
evidence = gate.reconciliation_evidence(cur)
verdict = gate.evaluate(cur, listing_id=TARGET, evidence=evidence,
                        price_minor=row.get("price_minor"))
print("  gate.reconciliation_evidence(cur) -> %r"
      % ({k: evidence.get(k) for k in ("running", "state")},))
print("  gate.evaluate(listing_id=%d) -> %r" % (TARGET, verdict))
print("  gate.refusal_code(...) -> %r" % (gate.refusal_code(verdict),))
refused = verdict.get("decision") == gate.DECISION_REFUSE
print()
if refused:
    print("  REFUSED server-side. The money leak is already closed by")
    print("  f4daceb34, which is deployed. So the hold below is the durable")
    print("  fix -- it takes the product off the shelf rather than letting a")
    print("  buyer reach a refusal -- and it is not the thing standing between")
    print("  this catalogue and a bad charge.")
else:
    print("  *** NOT REFUSED. The gate is not covering this row, which makes")
    print("  *** the hold urgent rather than tidy. Investigate before anything")
    print("  *** else in this mission proceeds.")

# --- Step 2: what the hold would change ------------------------------------
bar("STEP 2 -- the two numbers §30 asks for, for this one row")

# The before-counts have to be measured with the predicate *as production runs it
# today*, which is the one without the publication clause -- asking the database
# for `purchasable_sql` verbatim raises UndefinedColumn, which is step 3's finding
# arriving early and as a crash. So the clause is removed from the real SQL rather
# than an "old" predicate being hand-written: a hand-written one could differ in
# some second way and quietly attribute that difference to this change. Same
# reconstruction, and same reason, as publication_hold_preview.py.
CLAUSE = "AND COALESCE(l.commerce_publication_enabled,1)<>0 "


def count(predicate):
    sql = predicate.replace(CLAUSE, "")
    assert sql != predicate, "the publication clause was not found in the predicate"
    cur.execute(
        "SELECT COUNT(*) AS n FROM marketplace_listings l "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
        "WHERE " + sql)
    return int(dict(cur.fetchone())["n"])


buyable_before = count(lifecycle.purchasable_sql("l", "ms"))
visible_before = count(lifecycle.public_sql("l", "ms"))

# The projection: the same row, one integer different, run through the same
# predicate. Done in Python rather than in SQL because the column does not exist
# here yet -- see step 3 -- and because `is_purchasable` is the predicate the
# row-for-row agreement test pins against `purchasable_sql`.
held = dict(row)
held["commerce_publication_enabled"] = lifecycle.PUBLICATION_HELD
was_buyable = lifecycle.is_purchasable(row)
was_visible = lifecycle.is_public(row)
now_buyable = lifecycle.is_purchasable(held)
now_visible = lifecycle.is_public(held)

print("  BEFORE (whole catalogue, predicate as deployed)")
print("    buyer-visible listings : %d" % visible_before)
print("    buyable listings       : %d" % buyable_before)
print()
print("  listing %d: is_public %r -> %r, is_purchasable %r -> %r"
      % (TARGET, was_visible, now_visible, was_buyable, now_buyable))
print("  blocker after hold     : %r" % lifecycle.publication_blocker(held))
print()
removed = 1 if (was_buyable and not now_buyable) else 0
added = 1 if (now_visible and not was_visible) else 0
print("  AFTER (projected)")
print("    buyer-visible listings : %d" % (visible_before - (1 if was_visible
                                                             and not now_visible
                                                             else 0)))
print("    buyable listings       : %d" % (buyable_before - removed))
print()
print("  newly buyer-visible        : %d" % added)
print("  removed from buyability    : %d" % removed)
print("  unexplained remainder      : %d"
      % (0 if removed == (1 if was_buyable else 0) else 1))
print()
print("  §1 also requires the hold to cost the listing nothing. One integer is")
print("  written: status, approval_status, price_label, price_minor, quantity,")
print("  provenance and variants are all untouched. That is asserted as a")
print("  whole-row diff by test_a_hold_changes_exactly_one_column, and the")
print("  round trip is asserted by its neighbour -- the property status=paused")
print("  lacks, since /resume returns the row to review and the approval that")
print("  follows NULLs published_at.")

# --- Step 3: can it be applied today? --------------------------------------
bar("STEP 3 -- can the hold be applied to production right now?")
cur.execute(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'marketplace_listings' "
    "  AND column_name = 'commerce_publication_enabled'")
exists = cur.fetchone() is not None
print("  commerce_publication_enabled in production: %s"
      % ("PRESENT" if exists else "ABSENT"))
print()
if exists:
    print("  The hold can be applied. The call is:")
    print("    publication.hold(business_id, store_id, actor_user_id,")
    print("                     connection_id, %d," % TARGET)
    print("                     reason='unshippable: no supplier variant bound')")
    print("  It runs through drafts._scope, so it requires the real merchant")
    print("  identity and writes an audit row naming the actor -- which is the")
    print("  point: there is no system actor for this family.")
else:
    print("  NO. The column does not exist in this database, so any hold")
    print("  raises UndefinedColumn. bot.init_db() adds it at boot, which")
    print("  means this mutation is gated on the branch deploying -- not on")
    print("  more code, and not on a decision.")
    print()
    print("  Until then the row is covered by the deployed checkout gate")
    print("  (step 1), which is why the two were built in that order and")
    print("  shipped separately under §29.")

print()
print("Read-only: no write, no DDL, no commit was issued by this script.")
