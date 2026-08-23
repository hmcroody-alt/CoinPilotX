#!/usr/bin/env python3
"""QA/test account classification for App Review item 4.

DRY-RUN BY DEFAULT. The only write this script can ever perform is
``hidden_from_discovery=1``. It never deletes, never deactivates, and never
touches payment rows.

Why this file is shaped the way it is
-------------------------------------
The first version of this script was calibrated against a local dev database
and was quietly useless against production, in two opposite directions:

1. **It could not emit HIDE_FROM_DISCOVERY at all.** Any row in ``subscriptions``
   counted as a financial footprint, but PulseSoc auto-provisions a trial
   subscription row for every account (36/36 production users had one). So every
   matched account was labelled "has money, don't touch" and the script
   recommended nothing. A protective heuristic that fires on 100% of rows is not
   protection, it is a no-op with a reassuring label.

2. **Its patterns missed most real QA accounts.** ``qa!_%`` requires a literal
   ``qa_`` prefix, so ``pulseqa802505`` slipped through, as did every account
   whose only tell was an ``@coinpilotx.test`` email and an empty username.

The fix is to separate three things the old code conflated:

* **Evidence of money** (:func:`_has_financial_rows`) — must reflect an actual
  payment relationship, not the mere existence of a plan row.
* **Strength of the QA signal** — a machine-provisioned name like
  ``undxqa_20260719222239`` is proof; the substring "test" inside a display name
  is a hint. Hints route to AMBIGUOUS for a human, they do not auto-hide.
* **Write scope** — classification is advisory; ``--only`` decides what may
  actually be written, so a label can never be widened into a write by accident.

Classifications
---------------
  APP_REVIEW_REQUIRED       Apple review / demo account. Never hidden.
  PROTECT_FINANCIAL_HISTORY Real payment relationship. Never hidden, never deleted.
  AMBIGUOUS                 Weak signal only, or an owner-directed manual hold.
                            Requires a human decision; never auto-hidden.
  INTERNAL_ONLY             Explicit internal/staff account. Never auto-hidden.
  DEACTIVATE                Already carries a non-active QA status.
  HIDE_FROM_DISCOVERY       Machine-provisioned QA account, no financial history.
                            The only label ``--apply-hide`` will write by default.

Usage
-----
  python3 scripts/qa_account_classification.py                       # dry run
  python3 scripts/qa_account_classification.py --apply-hide          # writes HIDE_FROM_DISCOVERY only
  python3 scripts/qa_account_classification.py --apply-hide --only HIDE_FROM_DISCOVERY
  python3 scripts/qa_account_classification.py --hold 10,35          # extra manual holds
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import db as db_service  # noqa: E402

# --------------------------------------------------------------------------
# QA identity patterns
#
# STRONG = machine-provisioned identities. A human does not pick the name
# "undxqa_20260719222239"; a test harness does. Matching one of these is
# sufficient to hide.
#
# WEAK = a word that merely suggests testing. Real people are called
# "TestMeNow" and real accounts live at johndoe@gmail.com. Matching only a weak
# pattern routes to AMBIGUOUS for human review — never to an automatic write.
# --------------------------------------------------------------------------
STRONG_NAME_PATTERNS: tuple[str, ...] = (
    "pulseqa%",
    "pulsefinal%",
    "iphone16qa%",
    "iphone16dev%",
    "undxqa%",
    "undxreleaseqa%",
    "incident!_prod!_%",
    "phase2%",
    "smoke%",
    "qa!_%",
    "%!_audit!_%",
    "chat!_sec!_intruder%",
    "synthetic%",
    "e2e!_%",
    "automation!_%",
)

# Reserved / disposable email space. RFC 2606 reserves example.* and .test, so
# an address there can never belong to a real user.
STRONG_EMAIL_PATTERNS: tuple[str, ...] = (
    "%@example.%",
    "%@%.test",
    "%@mailinator.%",
    "%@coinpilotx.%",
)

WEAK_PATTERNS: tuple[str, ...] = (
    "%test%",
    "%tester%",
    "%dummy%",
    "%sample%",
    "%fake%",
    "%staging%",
    "%johndoe%",
)

# Accounts that must never be hidden: Apple review / demo credentials.
PROTECTED_PATTERNS: tuple[str, ...] = ("%appreview%", "%app!_review%", "%demo%")

# Explicit internal/staff accounts. Reported, never auto-hidden.
INTERNAL_PATTERNS: tuple[str, ...] = ("internal!_%", "staff!_%", "ops!_%")

#: Owner-directed manual holds. These user_ids are never written automatically
#: no matter how they classify; they are surfaced for a human decision instead.
#: 35 (TestMeNow) carries a live Stripe subscription despite the name; 10
#: (JOHNDOE) is a test-sounding name on a real consumer mailbox.
DEFAULT_MANUAL_HOLD_IDS: frozenset[int] = frozenset({10, 35})

#: Labels ``--apply-hide`` is permitted to write. Anything outside this set is
#: advisory only. Kept as a constant so widening the write scope is a visible,
#: reviewable edit rather than a side effect of adding a classification.
WRITABLE_LABELS: frozenset[str] = frozenset({"HIDE_FROM_DISCOVERY"})

# --------------------------------------------------------------------------
# Financial evidence
# --------------------------------------------------------------------------

#: ``users`` columns that, when non-empty, prove a payment relationship.
FINANCIAL_USER_COLUMNS: tuple[str, ...] = (
    "stripe_customer_id",
    "provider_customer_id",
    "stripe_subscription_id",
    "latest_payment_at",
)

#: Tables where the existence of a row for the user is itself proof of money.
FINANCIAL_TABLE_CANDIDATES: tuple[str, ...] = (
    "payments",
    "payment_events",
    "stripe_events",
    "orders",
    "marketplace_orders",
    "wallet_transactions",
    "pulse_wallet_ledger",
    "creator_payouts",
)

#: Tables where row existence proves nothing and a predicate is required.
#: ``subscriptions`` is the reason this mechanism exists: every account gets an
#: auto-provisioned ``payment_type='trial' / status='trialing'`` row at signup,
#: so ``EXISTS(SELECT 1 FROM subscriptions WHERE user_id=?)`` is true for
#: literally every user and proves only that they registered.
#: Each entry is (column_required, sql_predicate); predicates whose column is
#: absent from the deployed schema are dropped rather than guessed at.
CONDITIONAL_FINANCIAL_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "subscriptions": (
        ("payment_type", "LOWER(COALESCE(payment_type,'')) NOT IN ('trial','free','none','')"),
        ("stripe_customer_id", "COALESCE(stripe_customer_id,'')<>''"),
        ("stripe_subscription_id", "COALESCE(stripe_subscription_id,'')<>''"),
        ("provider_subscription_id", "COALESCE(provider_subscription_id,'')<>''"),
    ),
}


def _pattern_clause(column: str, patterns) -> tuple[str, list[str]]:
    """Build ``col LIKE ? ESCAPE '!'`` terms plus their bind parameters.

    The patterns are bound, never inlined. Inlining looks harmless because these
    are hard-coded constants rather than user input, but it breaks on
    PostgreSQL: psycopg2 treats the query as a format string, so a pattern
    containing the literal sequence ``%s`` — ``'%sample%'``, ``'%staging%'`` —
    is read as a parameter placeholder and the query dies with
    "IndexError: tuple index out of range". The original pattern list happened
    to contain no ``%s``, so inlining survived by luck until the list grew.
    Binding removes the class of bug rather than escaping around it.

    The LIKE escape character is ``!``, not the conventional backslash, to work
    around a latent bug in ``services/db.py::_translate_sql``. That translator
    rewrites ``?`` placeholders to ``%s`` for PostgreSQL and skips anything
    inside a string literal, but it reads the two characters ``\\'`` as an
    escaped quote. So the literal ``ESCAPE '\\'`` leaves it believing it is
    *inside* an unterminated string, and it silently stops converting
    placeholders until the next ``ESCAPE '\\'`` flips the state back — every
    other ``?`` survives untranslated and psycopg2 fails with "not all arguments
    converted during string formatting". ``!`` needs no escaping, is valid on
    both SQLite and PostgreSQL, and keeps the fix local to this script; the
    translator itself is shared by ~1,538 routes and is out of scope here.
    """
    terms = [f"LOWER(COALESCE({column},'')) LIKE ? ESCAPE '!'" for _ in patterns]
    return " OR ".join(terms), list(patterns)


def _matches(value: str, patterns) -> bool:
    """Python-side mirror of the SQL LIKE used to build the candidate set.

    ``!_`` is a LIKE-escaped literal underscore, and ``%`` is the LIKE wildcard;
    fnmatch spells those ``_`` and ``*``. Order matters: unescape ``!_`` first so
    a literal underscore is not later mistaken for a wildcard.
    """
    value = (value or "").lower()
    for pattern in patterns:
        if fnmatch.fnmatch(value, pattern.replace("!_", "_").replace("%", "*")):
            return True
    return False


def _financial_tables(cur) -> list[str]:
    """Existence-is-proof tables that are actually present and user-scoped."""
    usable = []
    for table in FINANCIAL_TABLE_CANDIDATES:
        try:
            columns = {str(c).lower() for c in db_service.get_table_columns(cur, table)}
        except Exception:
            columns = set()
        if columns and "user_id" in columns:
            usable.append(table)
    return usable


def _conditional_financial_sql(cur) -> dict[str, str]:
    """Build ``WHERE`` predicates for tables where a row alone proves nothing.

    A predicate is only included if its column exists in the deployed schema —
    there is no migration framework here, so prod and ``init_db()`` can
    legitimately disagree. If none of a table's columns are present we drop the
    table entirely rather than fall back to bare existence: falling back is
    exactly the bug this function was written to remove.
    """
    out: dict[str, str] = {}
    for table, predicates in CONDITIONAL_FINANCIAL_TABLES.items():
        try:
            columns = {str(c).lower() for c in db_service.get_table_columns(cur, table)}
        except Exception:
            continue
        if not columns or "user_id" not in columns:
            continue
        usable = [sql for column, sql in predicates if column in columns]
        if usable:
            out[table] = " OR ".join(usable)
    return out


def _financial_reason(cur, tables, conditional, user_row) -> str:
    """Return a human-readable reason for financial protection, or ''.

    Returning the reason rather than a bool keeps the dry-run report auditable:
    a reviewer can see *why* an account was protected and challenge it.
    """
    for column in FINANCIAL_USER_COLUMNS:
        if str(user_row.get(column) or "").strip():
            return f"users.{column}"
    user_id = int(user_row.get("user_id") or 0)
    for table in tables:
        try:
            cur.execute(f"SELECT 1 FROM {table} WHERE user_id=? LIMIT 1", (user_id,))
            if cur.fetchone():
                return f"{table} row"
        except Exception:
            continue
    for table, predicate in conditional.items():
        try:
            cur.execute(
                f"SELECT 1 FROM {table} WHERE user_id=? AND ({predicate}) LIMIT 1",
                (user_id,),
            )
            if cur.fetchone():
                return f"{table} (non-trial)"
        except Exception:
            continue
    return ""


def classify(user_row, financial_reason: str, manual_hold_ids=frozenset()) -> tuple[str, str]:
    """Return ``(classification, reason)`` for one candidate row.

    Order is deliberate: protection outranks hiding, and every "don't touch"
    outcome is decided before any "hide" outcome can be reached.
    """
    username = str(user_row.get("username") or "")
    email = str(user_row.get("email") or "")
    display = str(user_row.get("display_name") or "")
    status = str(user_row.get("account_status") or "active").lower()
    identity = (username, email, display)

    for value in identity:
        if _matches(value, PROTECTED_PATTERNS):
            return "APP_REVIEW_REQUIRED", "matches Apple review/demo pattern"

    if financial_reason:
        return "PROTECT_FINANCIAL_HISTORY", f"financial evidence: {financial_reason}"

    if int(user_row.get("user_id") or 0) in manual_hold_ids:
        return "AMBIGUOUS", "owner-directed manual hold"

    for value in identity:
        if _matches(value, INTERNAL_PATTERNS):
            return "INTERNAL_ONLY", "matches internal/staff pattern"

    if status == "disabled_qa" or status.startswith("disabled") or status == "deleted":
        return "DEACTIVATE", f"account_status={status}"

    if _matches(username, STRONG_NAME_PATTERNS) or _matches(display, STRONG_NAME_PATTERNS):
        return "HIDE_FROM_DISCOVERY", "machine-provisioned QA username"
    if _matches(email, STRONG_EMAIL_PATTERNS):
        return "HIDE_FROM_DISCOVERY", "reserved/disposable test email domain"

    return "AMBIGUOUS", "weak signal only — human review required"


def _mask_email(email: str) -> str:
    email = str(email or "")
    if "@" not in email:
        return email[:6]
    local, _, domain = email.partition("@")
    keep = local[:3]
    return f"{keep}{'*' * max(0, len(local) - 3)}@{domain}"


def _parse_ids(raw: str) -> set[int]:
    out: set[int] = set()
    for chunk in str(raw or "").replace(" ", "").split(","):
        if chunk:
            out.add(int(chunk))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply-hide", action="store_true",
                        help="Write hidden_from_discovery=1. Never deletes or deactivates.")
    parser.add_argument("--only", default="HIDE_FROM_DISCOVERY",
                        help="Comma-separated classifications the write is allowed to touch. "
                             "Only HIDE_FROM_DISCOVERY is permitted.")
    parser.add_argument("--hold", default="",
                        help="Extra user_ids to hold for manual review (comma-separated).")
    parser.add_argument("--no-default-holds", action="store_true",
                        help="Drop the built-in manual holds (ids 10, 35).")
    parser.add_argument("--limit", type=int, default=0, help="Cap scanned rows (0 = all).")
    parser.add_argument("--mask-emails", action="store_true", help="Mask email local parts in output.")
    args = parser.parse_args()

    requested = {c.strip().upper() for c in args.only.split(",") if c.strip()}
    illegal = requested - WRITABLE_LABELS
    if illegal:
        print(f"ERROR: --only may not include {', '.join(sorted(illegal))}. "
              f"Writable labels: {', '.join(sorted(WRITABLE_LABELS))}.")
        return 2

    manual_hold_ids = set() if args.no_default_holds else set(DEFAULT_MANUAL_HOLD_IDS)
    manual_hold_ids |= _parse_ids(args.hold)

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        user_columns = {str(c).lower() for c in db_service.get_table_columns(cur, "users")}
        has_hidden_column = "hidden_from_discovery" in user_columns
        if not has_hidden_column:
            print("NOTE: users.hidden_from_discovery is missing from this database "
                  "(deploy the app once to run init_db); dry-run classification still works.")

        cur.execute("SELECT COUNT(*) AS n FROM users")
        total_users = int(dict(cur.fetchone()).get("n") or 0)

        all_patterns = STRONG_NAME_PATTERNS + STRONG_EMAIL_PATTERNS + WEAK_PATTERNS + INTERNAL_PATTERNS
        match_cols = [c for c in ("username", "display_name", "email") if c in user_columns]
        clauses: list[str] = []
        match_params: list[str] = []
        for column in match_cols:
            fragment, bound = _pattern_clause(column, all_patterns)
            if fragment:
                clauses.append(f"({fragment})")
                match_params.extend(bound)
        clause = " OR ".join(clauses)

        select_cols = [c for c in (
            "user_id", "username", "email", "display_name", "account_status",
            "stripe_customer_id", "provider_customer_id", "stripe_subscription_id",
            "latest_payment_at", "hidden_from_discovery", "created_at",
        ) if c in user_columns]
        sql = f"SELECT {', '.join(select_cols)} FROM users WHERE {clause} ORDER BY user_id"
        if args.limit > 0:
            sql += f" LIMIT {int(args.limit)}"
        cur.execute(sql, tuple(match_params))
        rows = [dict(r) for r in cur.fetchall()]

        tables = _financial_tables(cur)
        conditional = _conditional_financial_sql(cur)

        counts: dict[str, int] = {}
        to_write: list[tuple[int, str]] = []
        header = (f"{'id':>5}  {'classification':<26}  {'fin':<4}  {'hid':<4}  "
                  f"{'username':<28}  {'email':<36}  reason")
        print(header)
        print("-" * len(header))
        for row in rows:
            reason_fin = _financial_reason(cur, tables, conditional, row)
            label, reason = classify(row, reason_fin, manual_hold_ids)
            counts[label] = counts.get(label, 0) + 1
            hidden = int(row.get("hidden_from_discovery") or 0)
            email = _mask_email(row.get("email")) if args.mask_emails else str(row.get("email") or "")
            if label in requested and not hidden:
                to_write.append((int(row.get("user_id") or 0), str(row.get("username") or "")))
            print(f"{int(row.get('user_id') or 0):>5}  {label:<26}  "
                  f"{'YES' if reason_fin else 'no':<4}  {hidden:<4}  "
                  f"{str(row.get('username') or '')[:28]:<28}  {email[:36]:<36}  {reason}")

        print()
        print(f"Total users in database: {total_users}")
        print(f"Matched {len(rows)} account(s).")
        for label in sorted(counts):
            print(f"  {label:<26} {counts[label]}")
        print(f"Existence-is-proof financial tables: {', '.join(tables) or 'none'}")
        print(f"Predicate-guarded financial tables:  {', '.join(sorted(conditional)) or 'none'}")
        print(f"Manual holds in effect: {', '.join(str(i) for i in sorted(manual_hold_ids)) or 'none'}")
        print(f"Write scope (--only): {', '.join(sorted(requested))}")

        if not args.apply_hide:
            print(f"\nDRY RUN (default). {len(to_write)} account(s) would get "
                  f"hidden_from_discovery=1:")
            for user_id, username in to_write:
                print(f"    {user_id:>5}  {username}")
            print("Re-run with --apply-hide to write. Nothing was modified.")
            return 0

        if not has_hidden_column:
            print("\nERROR: cannot apply — users.hidden_from_discovery does not exist. "
                  "Deploy the backend so init_db() adds it, then re-run.")
            return 1

        applied = 0
        for user_id, _username in to_write:
            cur.execute("UPDATE users SET hidden_from_discovery=1 WHERE user_id=?", (user_id,))
            applied += 1
        conn.commit()
        print(f"\nAPPLIED: hidden_from_discovery=1 set on {applied} account(s). "
              f"No rows were deleted, deactivated, or financially modified.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
