#!/usr/bin/env python3
"""QA/test account classification for App Review item 4.

DRY-RUN BY DEFAULT: scans the users table for known QA/test-account patterns and
prints a classification table. Nothing is written unless --apply-hide is passed,
and even then the ONLY write performed is `hidden_from_discovery=1` on
pattern-matched accounts. This script NEVER deletes or deactivates anything.

Classifications:
  APP_REVIEW_REQUIRED   Looks like an Apple review / demo account. Always
                        protected: never hidden, never deleted.
  INTERNAL_ONLY         Pattern-matched account that has payment/financial rows.
                        Never delete; hide from discovery only.
  HIDE_FROM_DISCOVERY   Pattern-matched QA account with no financial footprint.
                        Safe to hide from all discovery surfaces.
  DEACTIVATE            Already carries a non-active QA status (e.g.
                        account_status='disabled_qa'); should also be hidden.
  DELETE                Never emitted automatically; reserved for manual triage.

Usage:
  python3 scripts/qa_account_classification.py                # dry run (default)
  python3 scripts/qa_account_classification.py --apply-hide   # set hidden_from_discovery=1
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import db as db_service  # noqa: E402

# Known QA/test-account patterns observed in production data.
QA_LIKE_PATTERNS = (
    "smoke%",            # smoke-test accounts
    "%\\_audit\\_%",     # *_audit_* accounts (escaped underscores)
    "%@example.%",       # example.com/example.org emails
    "phase2tester%",
    "chat_sec_intruder%",
    "qa\\_%",
)

# Accounts matching these must NEVER be hidden or deleted (Apple review demos).
PROTECTED_PATTERNS = ("%appreview%", "%app_review%", "%demo%")

# Tables that indicate a financial footprint (checked only if they exist and
# have a user_id column).
FINANCIAL_TABLE_CANDIDATES = (
    "payments",
    "payment_events",
    "stripe_events",
    "subscriptions",
    "orders",
    "marketplace_orders",
    "wallet_transactions",
    "pulse_wallet_ledger",
    "creator_payouts",
)


def _pattern_clause(column: str, patterns) -> str:
    return " OR ".join(f"LOWER(COALESCE({column},'')) LIKE '{p}' ESCAPE '\\'" for p in patterns)


def _matches(value: str, patterns) -> bool:
    import fnmatch

    value = (value or "").lower()
    for pattern in patterns:
        fn = pattern.replace("\\_", "_").replace("%", "*")
        if fnmatch.fnmatch(value, fn):
            return True
    return False


def _financial_tables(cur):
    usable = []
    for table in FINANCIAL_TABLE_CANDIDATES:
        try:
            columns = db_service.get_table_columns(cur, table)
        except Exception:
            columns = []
        if columns and "user_id" in {str(c).lower() for c in columns}:
            usable.append(table)
    return usable


def _has_financial_rows(cur, tables, user_row) -> bool:
    if str(user_row.get("stripe_customer_id") or "").strip():
        return True
    if str(user_row.get("provider_customer_id") or "").strip():
        return True
    if str(user_row.get("latest_payment_at") or "").strip():
        return True
    user_id = int(user_row.get("user_id") or 0)
    for table in tables:
        try:
            cur.execute(f"SELECT 1 FROM {table} WHERE user_id=? LIMIT 1", (user_id,))
            if cur.fetchone():
                return True
        except Exception:
            continue
    return False


def classify(user_row, has_financial: bool) -> str:
    username = str(user_row.get("username") or "")
    email = str(user_row.get("email") or "")
    display = str(user_row.get("display_name") or "")
    status = str(user_row.get("account_status") or "active").lower()
    for value in (username, email, display):
        if _matches(value, PROTECTED_PATTERNS):
            return "APP_REVIEW_REQUIRED"
    if has_financial:
        return "INTERNAL_ONLY"
    if status == "disabled_qa" or status.startswith("disabled"):
        return "DEACTIVATE"
    return "HIDE_FROM_DISCOVERY"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply-hide", action="store_true", help="Set hidden_from_discovery=1 for pattern-matched, non-protected accounts. Never deletes.")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on scanned rows (0 = all matches).")
    args = parser.parse_args()

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        user_columns = {str(c).lower() for c in db_service.get_table_columns(cur, "users")}
        if "hidden_from_discovery" not in user_columns:
            print("NOTE: users.hidden_from_discovery column missing (run app once to migrate); dry-run classification still works.")

        clause = " OR ".join(
            _pattern_clause(col, QA_LIKE_PATTERNS)
            for col in ("username", "email", "display_name")
            if col.split(".")[-1] in user_columns
        )
        select_cols = [c for c in ("user_id", "username", "email", "display_name", "account_status", "stripe_customer_id", "provider_customer_id", "latest_payment_at", "hidden_from_discovery", "created_at") if c in user_columns]
        sql = f"SELECT {', '.join(select_cols)} FROM users WHERE {clause} ORDER BY user_id"
        if args.limit > 0:
            sql += f" LIMIT {int(args.limit)}"
        cur.execute(sql)
        rows = [dict(row) for row in cur.fetchall()]
        tables = _financial_tables(cur)

        counts: dict[str, int] = {}
        to_hide = []
        header = f"{'user_id':>8}  {'classification':<22}  {'status':<14}  {'hidden':<6}  {'username':<28}  email"
        print(header)
        print("-" * len(header))
        for row in rows:
            has_financial = _has_financial_rows(cur, tables, row)
            label = classify(row, has_financial)
            counts[label] = counts.get(label, 0) + 1
            hidden = int(row.get("hidden_from_discovery") or 0)
            if label in {"HIDE_FROM_DISCOVERY", "INTERNAL_ONLY", "DEACTIVATE"} and not hidden:
                to_hide.append(int(row.get("user_id") or 0))
            print(f"{int(row.get('user_id') or 0):>8}  {label:<22}  {str(row.get('account_status') or 'active'):<14}  {hidden:<6}  {str(row.get('username') or '')[:28]:<28}  {str(row.get('email') or '')[:48]}")

        print()
        print(f"Matched {len(rows)} account(s). Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
        print(f"Financial tables checked: {', '.join(tables) or 'none found'}")

        if not args.apply_hide:
            print(f"\nDRY RUN (default). {len(to_hide)} account(s) would get hidden_from_discovery=1. Re-run with --apply-hide to write.")
            return 0

        if "hidden_from_discovery" not in user_columns:
            print("\nERROR: cannot apply — users.hidden_from_discovery column does not exist yet.")
            return 1
        applied = 0
        for user_id in to_hide:
            cur.execute("UPDATE users SET hidden_from_discovery=1 WHERE user_id=?", (user_id,))
            applied += 1
        conn.commit()
        print(f"\nAPPLIED: hidden_from_discovery=1 set on {applied} account(s). No rows were deleted or deactivated.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
