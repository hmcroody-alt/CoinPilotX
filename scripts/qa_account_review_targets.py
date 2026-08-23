#!/usr/bin/env python3
"""Independent read-only review of the proposed QA hide targets.

Deliberately does NOT import the classifier's logic. The classifier already
said these accounts are safe to hide; re-running its own reasoning would only
confirm that it agrees with itself. This asks a different question — does the
account look *used*? — so that a wrong pattern match has a second chance to be
caught before anything is written.

For each candidate it reports the financial footprint (independently counted)
and the social footprint (posts, followers, messages, logins). A real person
misclassified as QA would show activity here.

Strictly SELECT-only.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as db_service  # noqa: E402

TARGET_IDS = [5, 6, 11, 12, 13, 14, 16, 24, 29, 30, 31, 34]
HELD_IDS = [10, 35]

#: (table, user column). Counted only if both table and column exist.
ACTIVITY_TABLES = [
    ("posts", "user_id"),
    ("pulse_posts", "user_id"),
    ("comments", "user_id"),
    ("followers", "follower_id"),
    ("followers", "following_id"),
    ("messages", "sender_id"),
    ("stripe_events", "user_id"),
    ("subscriptions", "user_id"),
    ("creator_payouts", "user_id"),
]


def main() -> int:
    conn = db_service.connect()
    try:
        cur = conn.cursor()

        def cols(table: str) -> set[str]:
            try:
                return {str(c).lower() for c in db_service.get_table_columns(cur, table)}
            except Exception:
                return set()

        available = []
        for table, col in ACTIVITY_TABLES:
            if col in cols(table):
                available.append((table, col))

        user_cols = cols("users")
        detail_cols = [c for c in ("user_id", "username", "email", "display_name",
                                   "account_status", "created_at", "last_login_at",
                                   "hidden_from_discovery", "stripe_customer_id",
                                   "latest_payment_at", "plan")
                       if c in user_cols]

        report = {"checked_tables": [f"{t}.{c}" for t, c in available], "accounts": []}

        for uid in TARGET_IDS + HELD_IDS:
            cur.execute(
                f"SELECT {', '.join(detail_cols)} FROM users WHERE user_id=?", (uid,)
            )
            row = cur.fetchone()
            if row is None:
                report["accounts"].append({"user_id": uid, "MISSING": True})
                continue
            entry = dict(row)
            email = entry.get("email") or ""
            if "@" in email:
                name, _, domain = email.partition("@")
                entry["email"] = f"{name[:6]}***@{domain}"
            entry["role"] = "HOLD" if uid in HELD_IDS else "TARGET"

            activity = {}
            for table, col in available:
                try:
                    cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {col}=?", (uid,))
                    n = int(list(dict(cur.fetchone()).values())[0])
                    if n:
                        activity[f"{table}.{col}"] = n
                except Exception as exc:
                    activity[f"{table}.{col}"] = f"error:{type(exc).__name__}"
            entry["activity"] = activity
            report["accounts"].append(entry)

        print(json.dumps(report, indent=2, default=str, sort_keys=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
