#!/usr/bin/env python3
"""Find listings that are "in review" according to nobody, and put them in the queue.

Why this exists
---------------
§40. The review queue now shows every listing that satisfies
``marketplace_listing_lifecycle.awaiting_moderation`` — merchant released, no
decision recorded — and the Approve gate asks the same question, so the queue
and the gate cannot disagree. That fixes the future. It does not move a single
row that was already stranded.

A stranded row is not in a wrong state. It is in a state that no screen
contradicts:

  * the seller's store shows **"In review — not live yet"**, because the listing
    is released and not public. Correct;
  * the Review Center does not list it, because ``awaiting_moderation`` is
    false. Correct;
  * buyer discovery does not return it, because approval never happened.
    Correct.

Three surfaces agree and the product sits there forever. Nobody has a bug to
report except the seller, who has "no orders".

How a row gets there
--------------------
``approval_status`` is blank (an older insert path, or an import that only
populated ``status``), or it holds a word this build does not recognise —
``in_review``, ``submitted``, ``needs_review``. Both read as "in review" to a
human scanning the admin table and as nothing at all to every predicate.

What the repair does and does not do
------------------------------------
It writes exactly one column: ``approval_status='pending_review'``. That puts
the listing in front of a reviewer and decides nothing. Inferring a verdict —
"it was released and looks complete, mark it approved" — would publish an
unreviewed catalogue in one transaction and route around every §34 guard in
``listing_review`` from a maintenance script. The whole point of the states
being unrecognised is that they carry no verdict to recover.

``APPROVED_BUT_UNRELEASED`` (approved on the moderation axis while the merchant
axis still says pending) is reported and never repaired, for the mirror-image
reason: its fix is ``status``, and writing that here would publish a listing
whose merchant never released it.

The imports that were never released
------------------------------------
A second class, found the same way and stranded for the same reason on the other
axis. A supplier import that the publish gate declined was written as
``draft``, and ``draft`` is not a released state, so ``awaiting_moderation`` is
false and no reviewer ever sees it. The merchant did release it — they pressed
"Import & publish" on a store with auto-publish on — and the only exit was them
opening each product and submitting it by hand. Measured in production on
2026-09-27: one seller held 67 of these.

``drafts.autopublish`` now writes ``review_ready`` on a refusal, which fixes
every future import and moves none of these. So this sweep repairs them, writing
``status='review_ready'``: released, still not public, still undecided. It will
not touch a draft it cannot prove was released — the evidence is a
``marketplace_product_sources`` row *and* the store's ``auto_publish``, because
a store that turned auto-publish off asked to hold its imports as drafts and is
entitled to have them left alone.

§41 is also checked here: duplicate ledger rows for one ``(reviewer,
idempotency key)``. The unique index makes new ones impossible, which is exactly
why the old ones need finding — an index added after the fact cleans up nothing.

Usage
-----
    python3 scripts/marketplace_review_zombie_audit.py           # report only
    python3 scripts/marketplace_review_zombie_audit.py --apply   # requeue
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as db_service  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as review  # noqa: E402


def _duplicate_decisions(cur) -> list:
    """§41. Returns [] when the ledger table has not been created yet."""
    try:
        cur.execute(review.duplicate_decision_sql("b"))
    except Exception:
        return []
    return [dict(row) for row in cur.fetchall()]


def _autopublish_stores(cur) -> set:
    """``(business_id, store_id)`` for every store whose imports publish on sight.

    The second half of the ``IMPORTED_BUT_NEVER_RELEASED`` evidence, and the half
    that keeps the sweep honest. A store that turned auto-publish *off* asked to
    look at its imports before anything moved, so its drafts are drafts in the
    ordinary sense and nothing here may touch them. Absent policy row means the
    default, which is on — the same default :mod:`store_policy` applies.

    Returns an empty set when the table does not exist yet, which makes the
    unreleased-import pass a no-op rather than an error on a database that has
    never had a supplier connected.
    """
    try:
        cur.execute("SELECT business_id, store_id, auto_publish "
                    "FROM business_os_store_import_policy")
    except Exception:
        return set()
    stores = set()
    for row in cur.fetchall():
        record = dict(row)
        if int(record.get("auto_publish") or 0):
            stores.add((record.get("business_id"), record.get("store_id")))
    return stores


def _unreleased_imports(cur) -> list:
    """Drafts that an import already released, with the evidence attached.

    Returns [] when the import tables are absent. Each row carries
    ``imported_under_autopublish``, which is what lets
    :func:`review.zombie_reason` classify it — that flag is computed here,
    against the store's policy, and never inferred from the listing.
    """
    autopublish = _autopublish_stores(cur)
    try:
        cur.execute(
            f"""SELECT l.id, l.seller_user_id, l.title, l.status, l.approval_status,
                       l.created_at, s.business_id, s.store_id
                  FROM marketplace_listings l, marketplace_product_sources s
                 WHERE {review.unreleased_import_sql('l', 's')}
                 ORDER BY l.id"""
        )
    except Exception:
        return []
    rows = []
    seen = set()
    for raw in cur.fetchall():
        row = dict(raw)
        listing_id = int(row.get("id") or 0)
        # A listing can carry more than one source row. Sweeping it twice is
        # harmless but would double-count in the report, and the report is the
        # thing an operator reads to decide whether to run --apply.
        if listing_id in seen:
            continue
        seen.add(listing_id)
        row["imported_under_autopublish"] = (
            (row.get("business_id"), row.get("store_id")) in autopublish)
        rows.append(row)
    return rows


def audit(apply_changes: bool = False, limit: int = 0) -> dict:
    conn = db_service.connect()
    cur = conn.cursor()

    # The SQL is a superset — it also matches genuinely decided listings, which
    # is unavoidable without repeating the whole rule in two dialects. Every row
    # it returns is re-checked through `zombie_reason`, so the classification
    # here and the classification the queue uses are the same function.
    cur.execute(
        f"""SELECT id, seller_user_id, title, status, approval_status, created_at
            FROM marketplace_listings l
            WHERE {review.zombie_sql('l')}
            ORDER BY l.id"""
    )
    candidates = [dict(row) for row in cur.fetchall()]
    # Appended rather than queried together: the unreleased-import class needs a
    # join the predicate above cannot carry, and its evidence column is computed
    # in Python against the store's policy.
    candidates.extend(_unreleased_imports(cur))

    requeued: list = []
    reported: list = []
    for row in candidates:
        reason = review.zombie_reason(row)
        if reason is None:
            continue
        record = {
            "listing_id": int(row.get("id") or 0),
            "seller_user_id": int(row.get("seller_user_id") or 0),
            "title": (row.get("title") or "")[:80],
            "status": row.get("status") or "",
            "approval_status": row.get("approval_status") or "",
            "reason": reason,
        }
        repair = review.zombie_repair(row)
        if repair is None:
            reported.append(record)
            continue
        if limit and len(requeued) >= limit:
            record["reason"] = record["reason"] + " (over --limit, not touched)"
            reported.append(record)
            continue
        record["repair"] = {k: v for k, v in repair.items() if k != "reason"}
        requeued.append(record)
        if apply_changes:
            # Whichever column the repair names, and only that one. Built from
            # the dict rather than hardcoded because the two zombie classes are
            # repaired on opposite axes -- `approval_status` for a blank
            # moderation state, `status` for an import that was never released --
            # and a hardcoded column would have silently written the wrong one.
            columns = [k for k in repair if k != "reason"]
            assignments = ", ".join(f"{name}=?" for name in columns)
            cur.execute(
                f"UPDATE marketplace_listings SET {assignments} WHERE id=?",
                tuple(repair[name] for name in columns) + (record["listing_id"],),
            )

    if apply_changes and requeued:
        conn.commit()

    duplicates = _duplicate_decisions(cur)
    conn.close()

    return {
        "candidates_examined": len(candidates),
        "applied": bool(apply_changes),
        # Repairable: one column, no verdict. These become visible to reviewers.
        "requeued": requeued,
        "requeued_count": len(requeued),
        # Not repairable from data. Listed so the number is known rather than
        # quietly excluded, which is how the original defect stayed invisible.
        "needs_human_decision": reported,
        "needs_human_decision_count": len(reported),
        "awaiting_predicate": lifecycle.awaiting_moderation_sql("l"),
        "duplicate_review_batches": duplicates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write approval_status='pending_review' on the repairable rows.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Repair at most N listings (0 = no limit). "
                             "A first --apply on a large catalogue is easier to "
                             "check when it is small.")
    args = parser.parse_args()
    result = audit(apply_changes=args.apply, limit=args.limit)
    print(json.dumps(result, indent=2, default=str))
    # Non-zero when there is work left, so a scheduled run is noticed.
    return 1 if (result["requeued_count"] and not args.apply) else 0


if __name__ == "__main__":
    raise SystemExit(main())
