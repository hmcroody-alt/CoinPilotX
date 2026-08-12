#!/usr/bin/env python3
"""Find Marketplace sellers with no public store name, and repair the repairable.

Why this exists
---------------
Buyer surfaces render ``marketplace_sellers.display_name`` (falling back to
``business_name``) and never the account holder's personal name — see
``services/marketplace_seller_identity``. Publication now requires that identity:
a listing whose seller has neither name is held back rather than sold under a
person's name.

That turns a data defect into a visible outage, which is the point — but it means
the defect has to be findable. This script reports every affected seller and,
with ``--apply``, copies over an authoritative name where one demonstrably
exists elsewhere in the seller's own records.

What counts as authoritative
----------------------------
Only a name the seller themselves entered as their store:

  1. ``marketplace_sellers.business_name`` — the registered business name,
     already a buyer-safe public identity.
  2. ``pulsesoc_seller_stores.store_name`` — written by the seller in the
     dashboard's store settings. Not joined into buyer queries (its table is
     created lazily, so joining it would risk "relation does not exist"), but as
     a one-off migration source it is exactly the right thing.

A personal name is never a source. Neither is an invented one: a seller with no
store name anywhere is reported as ``needs_seller_action`` and their listings
stay off sale until they name their store. Guessing would defeat the change.

Usage
-----
    python3 scripts/marketplace_store_identity_audit.py           # report only
    python3 scripts/marketplace_store_identity_audit.py --apply   # backfill
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as db_service  # noqa: E402
from services import marketplace_seller_identity as seller_identity  # noqa: E402


def _table_exists(cur, table: str) -> bool:
    """Cheap existence probe that works on both SQLite and PostgreSQL."""
    try:
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        cur.fetchall()
        return True
    except Exception:
        return False


def _dashboard_store_names(cur) -> dict[int, str]:
    """user_id -> store name from the dashboard table, when that table exists."""
    if not _table_exists(cur, "pulsesoc_seller_stores"):
        return {}
    try:
        cur.execute("SELECT user_id, store_name FROM pulsesoc_seller_stores")
    except Exception:
        return {}
    names: dict[int, str] = {}
    for row in cur.fetchall():
        row = dict(row)
        name = str(row.get("store_name") or "").strip()
        if name:
            names[int(row.get("user_id") or 0)] = name
    return names


def audit(apply_changes: bool = False) -> dict:
    conn = db_service.connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT s.user_id, s.status, s.display_name, s.business_name,
               (SELECT COUNT(*) FROM marketplace_listings l
                 WHERE l.seller_user_id = s.user_id
                   AND LOWER(COALESCE(l.status,'')) IN ('published','live','active')
               ) AS live_listings
        FROM marketplace_sellers s
        ORDER BY s.user_id
        """
    )
    sellers = [dict(row) for row in cur.fetchall()]
    dashboard_names = _dashboard_store_names(cur)

    repaired: list[dict] = []
    needs_action: list[dict] = []

    for seller in sellers:
        if seller_identity.has_store_identity(seller):
            continue
        user_id = int(seller.get("user_id") or 0)
        # `has_store_identity` already covered business_name, so the only
        # remaining authoritative source is the dashboard store record.
        source_name = dashboard_names.get(user_id, "")
        record = {
            "user_id": user_id,
            "status": seller.get("status") or "",
            "live_listings": int(seller.get("live_listings") or 0),
        }
        if not source_name:
            needs_action.append(record)
            continue
        record["store_name"] = source_name
        record["source"] = "pulsesoc_seller_stores.store_name"
        repaired.append(record)
        if apply_changes:
            cur.execute(
                "UPDATE marketplace_sellers SET display_name=? WHERE user_id=?",
                (source_name, user_id),
            )

    if apply_changes and repaired:
        conn.commit()
    conn.close()

    return {
        "sellers_checked": len(sellers),
        "applied": bool(apply_changes),
        "repairable": repaired,
        # These sellers cannot be fixed from data. Their listings are correctly
        # held back; the fix is the seller naming their store.
        "needs_seller_action": needs_action,
        "listings_currently_withheld": sum(r["live_listings"] for r in needs_action),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the recovered store names back to marketplace_sellers.display_name.",
    )
    args = parser.parse_args()
    print(json.dumps(audit(apply_changes=args.apply), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
