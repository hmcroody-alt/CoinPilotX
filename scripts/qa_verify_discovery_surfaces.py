#!/usr/bin/env python3
"""Read-only proof that hidden QA accounts are excluded from discovery.

Runs the *real* predicate — imported from services.discovery_visibility, not a
copy of it — against production for each search term a person would actually
type to surface a QA account. If the predicate and the flag disagree, this
catches it.

Also asserts the admin view still sees the hidden accounts, because "hidden"
must mean hidden from discovery, not invisible to the owner.

Strictly SELECT-only.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as db_service  # noqa: E402
from services.discovery_visibility import discovery_visible_sql  # noqa: E402

HIDDEN_IDS = {5, 6, 11, 12, 13, 14, 16, 24, 29, 30, 31, 34}

#: Terms chosen to match the QA accounts that were just hidden. Each one
#: returned at least one QA account before the apply.
SEARCH_TERMS = ["pulseqa", "pulsefinal", "iphone16", "undxqa", "undxreleaseqa",
                "incident", "johndoe", "phase2", "test", "qa"]


def main() -> int:
    conn = db_service.connect()
    out: dict = {}
    try:
        cur = conn.cursor()
        visible = discovery_visible_sql("u")
        out["predicate"] = visible

        # 1. Does any hidden account survive the discovery predicate at all?
        cur.execute(f"SELECT user_id FROM users u WHERE {visible} ORDER BY user_id")
        discoverable = {int(dict(r)["user_id"]) for r in cur.fetchall()}
        out["discoverable_ids"] = sorted(discoverable)
        out["hidden_leaking_into_discovery"] = sorted(HIDDEN_IDS & discoverable)

        # 2. Per-search-term: who would a real user find?
        term_results = {}
        for term in SEARCH_TERMS:
            like = f"%{term.lower()}%"
            cur.execute(
                f"""SELECT user_id, COALESCE(username,'') AS username
                    FROM users u
                    WHERE {visible}
                      AND (LOWER(COALESCE(u.username,'')) LIKE ?
                           OR LOWER(COALESCE(u.display_name,'')) LIKE ?)
                    ORDER BY user_id""",
                (like, like),
            )
            rows = [(int(dict(r)["user_id"]), dict(r)["username"]) for r in cur.fetchall()]
            leaked = [r for r in rows if r[0] in HIDDEN_IDS]
            term_results[term] = {
                "visible_matches": rows,
                "leaked_hidden_accounts": leaked,
            }
        out["search_terms"] = term_results

        # 3. Admin/owner inspection must still reach every hidden account.
        cur.execute(
            "SELECT user_id, COALESCE(username,'') AS username, hidden_from_discovery "
            "FROM users WHERE hidden_from_discovery=1 ORDER BY user_id"
        )
        admin_rows = [dict(r) for r in cur.fetchall()]
        out["admin_visible_hidden_accounts"] = admin_rows
        out["admin_can_see_all_hidden"] = (
            {int(r["user_id"]) for r in admin_rows} == HIDDEN_IDS
        )
    finally:
        conn.close()

    print(json.dumps(out, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
