#!/usr/bin/env python3
"""Read-only snapshot of the account state QA hiding is allowed to affect.

Run before and after ``qa_account_classification.py --apply-hide`` and diff the
two outputs. The point is to prove a negative: that nothing outside
``users.hidden_from_discovery`` moved. It therefore records the things the
mission forbids changing — row counts, account_status, and the financial
tables — not just the flag being written.

Strictly SELECT-only. No DDL, no writes, safe to run against production at any
time.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as db_service  # noqa: E402


def _scalar(cur) -> int:
    """First column of the current row, without depending on its label.

    PostgreSQL names a bare ``COUNT(*)`` column "count" while SQLite names it
    "COUNT(*)", so look the value up positionally instead.
    """
    return int(list(dict(cur.fetchone()).values())[0])


def main() -> int:
    out: dict = {}
    conn = db_service.connect()
    try:
        cur = conn.cursor()

        user_cols = {str(c).lower() for c in db_service.get_table_columns(cur, "users")}
        out["hidden_from_discovery_column_present"] = "hidden_from_discovery" in user_cols

        cur.execute("SELECT COUNT(*) FROM users")
        out["total_users"] = _scalar(cur)

        cur.execute(
            "SELECT COALESCE(account_status,'') AS s, COUNT(*) AS n "
            "FROM users GROUP BY COALESCE(account_status,'') ORDER BY 1"
        )
        out["account_status_counts"] = {
            dict(r)["s"]: int(dict(r)["n"]) for r in cur.fetchall()
        }

        # Financial tables must be byte-identical across the apply.
        out["financial_counts"] = {}
        for table in ("subscriptions", "stripe_events", "creator_payouts"):
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                out["financial_counts"][table] = _scalar(cur)
            except Exception as exc:  # table absent in this deployment
                out["financial_counts"][table] = f"absent ({type(exc).__name__})"

        # Per-user detail for every account, so a change anywhere is visible and
        # not just a change among the ones we expected to touch.
        select = ["user_id", "username", "email", "account_status"]
        if "hidden_from_discovery" in user_cols:
            select.append("hidden_from_discovery")
        for col in ("stripe_customer_id", "latest_payment_at"):
            if col in user_cols:
                select.append(col)
        cur.execute(f"SELECT {', '.join(select)} FROM users ORDER BY user_id")
        rows = []
        for raw in cur.fetchall():
            row = dict(raw)
            email = (row.get("email") or "")
            if "@" in email:
                name, _, domain = email.partition("@")
                row["email"] = f"{name[:3]}***@{domain}"
            rows.append(row)
        out["users"] = rows
    finally:
        conn.close()

    print(json.dumps(out, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
