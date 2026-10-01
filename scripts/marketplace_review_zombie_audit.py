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
        requeued.append(record)
        if apply_changes:
            cur.execute(
                "UPDATE marketplace_listings SET approval_status=? WHERE id=?",
                (repair["approval_status"], record["listing_id"]),
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
