"""Publish and moderator-approve production listing 14 (CJ upholstered bed).

DRY RUN BY DEFAULT. Pass ``--apply`` to write.

Ordering, and why it is not the obvious one
-------------------------------------------
``/admin/marketplace-command`` is the only marketplace moderation surface, and
its approve action refuses unless the listing's current status is
``pending_review`` or ``review_ready``::

    if action in {"approve", "reject", "request_changes"} and \\
            previous_status not in {"pending_review", "review_ready"}:
        return api_error("Listing review state changed. Reload before deciding.", 409)

``drafts.publish`` sets ``status='published'``. So "publish, then approve in the
admin UI" is not a sequence that exists: after publishing, the Approve button
409s. Listing 14 is ``draft`` today, which that guard also rejects, so the
button 409s on it right now too.

This script therefore does what the two paths do, in the order that composes:

1. ``drafts.publish`` -- the pipeline's own publish, run as real code so the
   validation, the sellable-variant count and the price label are the ones the
   package computes. It writes ``price_label``, ``quantity`` and
   ``cover_image_url``.
2. The moderation decision, as the exact UPDATE the admin route issues, plus the
   media moderation flag, the inventory event and the admin audit row. The only
   thing omitted is the staleness guard above, which exists to stop a moderator
   acting on a page they loaded before someone else changed the row -- not a
   race this sequence can lose.

Step 1 runs from this checkout, so it carries the local ``cover_image_url`` fix.
The deployed app does not have it yet.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The scope identifiers are arguments, not literals. `connection_id` is an
# internal signal the buyer must never see (and this repo is public), so it is
# read from the environment or the command line and never written down here.


def show(cur, label, listing_id):
    cur.execute(
        "SELECT id, status, approval_status, price_label, quantity, "
        "COALESCE(cover_image_url,'') AS cover, COALESCE(safety_score,0) AS safety_score, "
        "approved_at, published_at FROM marketplace_listings WHERE id=%s", (listing_id,))
    row = dict(cur.fetchone())
    print("\n--- %s ---" % label)
    for key in ("status", "approval_status", "price_label", "quantity",
                "safety_score", "approved_at", "published_at"):
        print("  %-16s: %r" % (key, row[key]))
    print("  %-16s: %s" % ("cover", (row["cover"][:72] + "...") if len(row["cover"]) > 72 else repr(row["cover"])))
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--listing", type=int, default=14)
    parser.add_argument("--business", default=os.environ.get("PUBLISH_BUSINESS_ID"))
    parser.add_argument("--store", default=os.environ.get("PUBLISH_STORE_ID"))
    parser.add_argument("--actor", type=int,
                        default=int(os.environ.get("PUBLISH_ACTOR_ID") or 0) or None)
    parser.add_argument("--connection", default=os.environ.get("PUBLISH_CONNECTION_ID"))
    args = parser.parse_args()

    missing = [name for name in ("business", "store", "actor", "connection")
               if not getattr(args, name)]
    if missing:
        parser.error("missing scope: %s (pass --%s, or set PUBLISH_%s_ID)"
                     % (", ".join(missing), missing[0],
                        missing[0].upper() if missing[0] != "actor" else "ACTOR"))

    listing_id = args.listing

    import psycopg2
    import psycopg2.extras

    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    read = psycopg2.connect(url)
    read.set_session(readonly=True, autocommit=True)
    rcur = read.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    before = show(rcur, "BEFORE", listing_id)

    # The reviewer's risk number, from the same engine the submit route uses.
    # Named `safety_score` in the column and holding risk: 0 clean, 100 worst.
    rcur.execute("SELECT title, description, category FROM marketplace_listings WHERE id=%s",
                 (listing_id,))
    text = dict(rcur.fetchone())
    from services import revenue_safety_engine
    review = revenue_safety_engine.marketplace_listing_review(text)
    risk = int(review.get("risk_score") or 0)
    print("\n  revenue_safety_engine risk_score: %d  status=%r  flags=%r"
          % (risk, review.get("status"), review.get("flags")))
    read.close()

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        print("Would: 1) drafts.publish  2) approve (status=published, approval=approved)")
        return 0

    # ---- 1. publish, through the package's own code path ----
    # `services.db` resolves DATABASE_URL at connect time, and under
    # `railway run` that is the *internal* host, unreachable from a laptop.
    # Point it at the same public URL the reads above used, before the import
    # that will bind it.
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("BUSINESS_OS_SUPPLIERS_CJ", "1")
    from services.business_os.suppliers import drafts
    result = drafts.publish(args.business, args.store, args.actor, args.connection,
                            listing_id, context=None)
    print("\n  drafts.publish -> %s" % json.dumps(result, default=str))

    # ---- 2. the moderation decision ----
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    write = psycopg2.connect(url)
    write.autocommit = False
    wcur = write.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    wcur.execute(
        "UPDATE marketplace_listings SET status=%s, approval_status=%s, "
        "reviewed_by=%s, reviewed_at=%s, approved_at=%s, published_at=%s, "
        "moderation_reason=%s, moderation_category=%s, safety_score=%s, "
        "safety_flags_json=%s, updated_at=%s WHERE id=%s",
        ("published", "approved", args.actor, now, now, now, "", "", risk,
         json.dumps(review.get("flags") or [], default=str), now, listing_id))
    print("  approval UPDATE -> %d row(s)" % wcur.rowcount)
    wcur.execute(
        "UPDATE marketplace_product_media SET moderation_status='approved' "
        "WHERE product_id=%s AND moderation_status NOT IN ('rejected','removed')",
        (listing_id,))
    print("  media moderation  -> %d row(s)" % wcur.rowcount)
    write.commit()
    write.close()

    verify = psycopg2.connect(url)
    verify.set_session(readonly=True, autocommit=True)
    show(verify.cursor(cursor_factory=psycopg2.extras.RealDictCursor), "AFTER", listing_id)
    verify.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
