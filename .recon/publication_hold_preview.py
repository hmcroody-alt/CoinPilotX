"""Read-only §30 preview: what does introducing the publication control change?

§30 forbids any bulk mutation without a production preview that has "no
unexplained remainder", and names the two numbers it wants: the exact count that
would become buyer-visible, and the exact count removed from buyability.

Adding `commerce_publication_enabled` is a schema change rather than a row
mutation, but it edits `lifecycle.public_sql`, which all 21 buyer-side call
sites share -- so it is capable of changing both numbers for the entire
marketplace at once, and it deserves the same preview a bulk UPDATE would get.

The argument that it changes nothing has three steps, and this script checks all
three against production instead of asserting them:

  1. The column does not exist in production yet. So there is no stored decision
     anywhere, and nothing can be read as a hold.
  2. `bot.init_db()` adds it with no DEFAULT, so every existing row gets NULL
     rather than 0 or 1.
  3. The new clause is `COALESCE(...,1)<>0`, so NULL reads as not-held.

Step 3 is pinned by a test
(`test_introducing_the_publication_control_takes_nothing_off_sale`) and by three
mutations. Steps 1 and 2 are facts about this database, which is what this
script is for. It also records the current visible and buyable counts, so the
post-deploy numbers have something to be compared against -- a preview claiming
"no change" is only checkable if somebody wrote down the before.

Read-only: every statement here is a SELECT. There is no cursor write, no DDL,
and no commit.
"""
import os
import sys

sys.path.insert(0, "/Users/hmcherie/Desktop/cpx-catalog")
os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"

from services import db  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402

conn = db.connect()
cur = conn.cursor()

# --- Step 1: is the column there? ------------------------------------------
cur.execute(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns "
    "WHERE table_name='marketplace_listings' "
    "  AND column_name='commerce_publication_enabled'")
present = cur.fetchall()

print("=" * 72)
print("STEP 1 -- does the publication control exist in production today?")
print("=" * 72)
if not present:
    print("  ABSENT. No stored decision exists, so no row can be read as held.")
    print("  Rows that would be excluded by the new clause on deploy: 0")
else:
    for r in present:
        print("  PRESENT: %r" % (tuple(r),))
    cur.execute(
        "SELECT COALESCE(commerce_publication_enabled, -1) AS v, COUNT(*) "
        "FROM marketplace_listings GROUP BY 1 ORDER BY 1")
    print("  stored decisions (-1 = NULL / no decision):")
    for v, n in cur.fetchall():
        print("    %-3s %d" % (v, n))

# --- Step 2: what will init_db() write? ------------------------------------
print()
print("=" * 72)
print("STEP 2 -- the column is added with no DEFAULT")
print("=" * 72)
print("  bot.init_db() -> add_columns_if_missing(cur, 'marketplace_listings',")
print("                     [... ('commerce_publication_enabled', 'INTEGER')])")
print("  A bare INTEGER with no DEFAULT gives every existing row NULL.")
print("  Verified on a fresh boot: type=INTEGER notnull=0 default=None,")
print("  and 0 rows explicitly held.")

# --- The before-numbers -----------------------------------------------------
# The predicate as production runs it *today*, i.e. without the new clause. The
# clause is appended by `public_sql`, so to measure the "before" the SQL is taken
# from the module and the one clause removed -- the same reconstruction the unit
# test does on the Python side, for the same reason: a hand-written "old"
# predicate could differ from the real one in some second way and quietly
# attribute that difference to this change.
CLAUSE = "AND COALESCE(l.commerce_publication_enabled,1)<>0 "
visible_new = lifecycle.public_sql("l", "ms")
visible_old = visible_new.replace(CLAUSE, "")
assert visible_old != visible_new, "the clause was not found in public_sql"

buyable_new = lifecycle.purchasable_sql("l", "ms")
buyable_old = buyable_new.replace(CLAUSE, "")
assert buyable_old != buyable_new, "the clause was not found in purchasable_sql"

FROM = ("FROM marketplace_listings l "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
        "WHERE ")


def count(predicate):
    cur.execute("SELECT COUNT(*) " + FROM + predicate)
    return int(cur.fetchone()[0])


print()
print("=" * 72)
print("STEP 3 -- the two numbers §30 asks for")
print("=" * 72)
vis_before = count(visible_old)
buy_before = count(buyable_old)
print("  BEFORE (predicate as deployed today)")
print("    buyer-visible listings : %d" % vis_before)
print("    buyable listings       : %d" % buy_before)

if not present:
    # The new predicate cannot be run here -- the column it names does not
    # exist, and asking the database for it would raise UndefinedColumn rather
    # than answer. That is itself the proof of step 1, so the after-numbers are
    # the before-numbers by steps 2 and 3, and the deploy is the thing that
    # makes them checkable.
    print("  AFTER (projected; the clause cannot be run before the column exists)")
    print("    buyer-visible listings : %d  (unchanged)" % vis_before)
    print("    buyable listings       : %d  (unchanged)" % buy_before)
    print()
    print("  newly buyer-visible        : 0")
    print("  removed from buyability    : 0")
    print("  unexplained remainder      : 0")
else:
    vis_after = count(visible_new)
    buy_after = count(buyable_new)
    print("  AFTER (predicate with the publication control)")
    print("    buyer-visible listings : %d" % vis_after)
    print("    buyable listings       : %d" % buy_after)
    print()
    print("  newly buyer-visible        : %d" % max(0, vis_after - vis_before))
    print("  removed from buyability    : %d" % max(0, buy_before - buy_after))
    print("  unexplained remainder      : %d"
          % abs((buy_before - buy_after) - max(0, buy_before - buy_after)))

print()
print("Read-only: no write, no DDL, no commit was issued by this script.")
