#!/usr/bin/env python3
"""Prove each email-uniqueness control is observed, by deleting it.

``tests/test_account_email_uniqueness.py`` is green. That says it passes, not that
it would notice if the thing it is named after stopped working -- and this
increment has an unusually large blind spot, because the local suite runs on
SQLite and the constraint's whole point is production, which is PostgreSQL 18.6.

Three mutations exist specifically because of that gap, and they are the reason
this file is worth more than the others:

* ``is-unique-violation-is-sqlite-only`` reintroduces ``isinstance(exc,
  sqlite3.IntegrityError)`` as the whole test. That is what every one of the seven
  hand-rolled integrity checks in this repo already says, and it catches *nothing*
  in production: a duplicate arrives as ``psycopg2.errors.UniqueViolation``, which
  shares no ancestor with the sqlite class meaning "uniqueness". A suite that runs
  only on SQLite cannot tell that mutation from the fix. ``ProductionEngineCase``
  exists to kill it; if it survives, the portable predicate is decoration.
* ``is-unique-violation-catches-everything`` is the mirror. Returning True for any
  exception makes every SQLite test pass and turns a foreign-key or not-null
  violation into "that account already exists" -- a wrong, confident answer.
* ``predicate-drops-the-blank-exclusion`` is the one that would have failed in
  production and nowhere else, because only production has the three ``email = ''``
  rows and the one NULL that make a plain unique index refuse to build.

Two more remove capability rather than protection (``index-never-created`` aside):
``race-branch-removed`` and ``precheck-removed``. A duplicate can always be
"prevented" by making signup fail, and a suite asserting only "no duplicates
exist" would score that a win.

What this does not claim: killing a mutant shows the control is observed, not that
it is correct.

Usage:  python3 scripts/protection/account_email_uniqueness_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mutation_harness import run_matrix  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOT = "bot.py"
UNIQ = "services/account_email_uniqueness.py"
DB = "services/db.py"
SUITE = "tests/test_account_email_uniqueness.py"

MUTATIONS = [
    # --- the index expression and predicate --------------------------------
    dict(
        name="predicate-drops-the-blank-exclusion",
        control=(
            "Blank is not a value. Production has three rows with email = '' and one "
            "NULL; NULLs are exempt from uniqueness automatically but '' is not, so "
            "without this the index refuses to build against the real table -- and "
            "only against the real table, which is why no SQLite test would notice."
        ),
        path=UNIQ,
        old='EMAIL_PRESENT_PREDICATE = "email IS NOT NULL AND trim(email) <> \'\'"',
        new='EMAIL_PRESENT_PREDICATE = "email IS NOT NULL"',
        suites=[SUITE],
    ),
    dict(
        name="expression-drops-trim",
        control=(
            "normalize_email is .strip().lower(), so a stored address with stray "
            "whitespace is the same identity. Without trim the constraint enforces a "
            "different notion of identity than every lookup in the codebase, which "
            "permits exactly the duplicates those lookups then disagree about."
        ),
        path=UNIQ,
        old='EMAIL_IDENTITY_EXPRESSION = "lower(trim(email))"',
        new='EMAIL_IDENTITY_EXPRESSION = "lower(email)"',
        suites=[SUITE],
    ),
    dict(
        name="expression-drops-lower",
        control="A@B.com and a@b.com are one account. Case-sensitive uniqueness is not uniqueness.",
        path=UNIQ,
        old='EMAIL_IDENTITY_EXPRESSION = "lower(trim(email))"',
        new='EMAIL_IDENTITY_EXPRESSION = "trim(email)"',
        suites=[SUITE],
    ),
    dict(
        name="btrim-instead-of-trim",
        control=(
            "btrim is PostgreSQL-only. There is no migration framework here, so one "
            "DDL string has to be correct on both engines; btrim makes the local "
            "build fail and production's succeed, or vice versa on a swap."
        ),
        path=UNIQ,
        old='EMAIL_IDENTITY_EXPRESSION = "lower(trim(email))"',
        new='EMAIL_IDENTITY_EXPRESSION = "lower(btrim(email))"',
        suites=[SUITE],
    ),

    # --- the guarded DDL ---------------------------------------------------
    dict(
        name="index-never-created",
        control="The invariant exists at all. Everything else here is about how.",
        path=UNIQ,
        old="        cur.execute(CREATE_INDEX_SQL)",
        new="        pass",
        suites=[SUITE],
    ),
    dict(
        name="catalogue-check-skipped",
        control=(
            "The steady state issues no DDL and so takes no lock. CREATE INDEX IF NOT "
            "EXISTS still takes a ShareLock on PostgreSQL when the index already "
            "exists, and ShareLock conflicts with the RowExclusiveLock an INSERT "
            "needs -- the pattern that once hung half of production."
        ),
        path=UNIQ,
        old="        if index_exists(cur):\n            return True",
        new="        if False:\n            return True",
        suites=[SUITE],
    ),
    dict(
        name="collision-check-removed",
        control=(
            "Refuses to attempt a build the data would fail. A failed CREATE UNIQUE "
            "INDEX poisons the open transaction on PostgreSQL, so the signup that "
            "called it fails for a reason having nothing to do with signup."
        ),
        path=UNIQ,
        old="        collisions = colliding_groups(cur)\n        if collisions:",
        new="        collisions = []\n        if collisions:",
        suites=[SUITE],
    ),
    dict(
        name="failure-raises-into-the-boot-path",
        control=(
            "Never stops a boot. This is called from init_db, where a raise does not "
            "fail loudly -- it truncates the schema at that line. bot.py:119868 "
            "records the time that left 49 tables of 586."
        ),
        path=UNIQ,
        old="    except Exception as exc:",
        new="    except _NeverRaised as exc:",
        suites=[SUITE],
    ),

    # --- the portable duplicate predicate ---------------------------------
    dict(
        name="is-unique-violation-is-sqlite-only",
        control=(
            "THE PRODUCTION BUG, written exactly as the repo's seven existing "
            "hand-rolled sites write it. A duplicate raises UniqueViolation on "
            "PostgreSQL, so this catches nothing in production and the local suite "
            "cannot see the difference. ProductionEngineCase is the only thing "
            "standing between the fix and a comment."
        ),
        path=DB,
        old='    if isinstance(exc, sqlite3.IntegrityError):',
        new='    if True:\n        return isinstance(exc, sqlite3.IntegrityError)\n    if isinstance(exc, sqlite3.IntegrityError):',
        suites=[SUITE],
    ),
    dict(
        name="is-unique-violation-catches-everything",
        control=(
            "Deliberately narrow. A foreign-key or not-null violation is a different "
            "bug; answering 'that account already exists' to one is a wrong answer "
            "given confidently, and every SQLite duplicate test still passes."
        ),
        path=DB,
        old='    if isinstance(exc, sqlite3.IntegrityError):',
        new='    if True:\n        return True\n    if isinstance(exc, sqlite3.IntegrityError):',
        suites=[SUITE],
    ),
    dict(
        name="sqlite-constraint-kind-ignored",
        control=(
            "SQLite folds several constraint kinds into one class, so the class alone "
            "is not the answer -- the message names which one fired."
        ),
        path=DB,
        old='        return "unique" in str(exc).lower()',
        new="        return True",
        suites=[SUITE],
    ),

    # --- the signup path --------------------------------------------------
    dict(
        name="race-branch-removed",
        control=(
            "A lost race is answered as a duplicate, not as 'try again shortly' -- "
            "which for a uniqueness violation is false, fails forever, and tells the "
            "user to keep trying. It also has to be the *same* answer as the "
            "precheck's, or the difference reveals that the address is being "
            "registered right now (Open Commerce section 16)."
        ),
        path=BOT,
        old="        if db_service.is_unique_violation(exc):",
        new="        if False:",
        suites=[SUITE],
    ),
    dict(
        name="race-branch-answers-differently",
        control=(
            "CAPABILITY-shaped, and the subtler half of the above: the branch fires "
            "but says something else. Two distinguishable answers to one fact is the "
            "enumeration oracle by side channel."
        ),
        path=BOT,
        old="            return None, ACCOUNT_ALREADY_EXISTS_MESSAGE\n        logging.exception(\"database transaction rollback during signup",
        new="            return None, \"An account already exists for that email address.\"\n        logging.exception(\"database transaction rollback during signup",
        suites=[SUITE],
    ),
    dict(
        name="precheck-disagrees-with-the-index",
        control=(
            "The precheck asks the index's own expression. An approximation "
            "(lower(email)=lower(?)) lets a stored address with stray whitespace slip "
            "past it and be caught by the index instead, turning an ordinary "
            "duplicate into the race branch."
        ),
        path=BOT,
        old='                f"WHERE {account_email_uniqueness.EMAIL_IDENTITY_EXPRESSION}=? "\n'
            '                f"AND {account_email_uniqueness.EMAIL_PRESENT_PREDICATE} LIMIT 1",',
        new='                "WHERE lower(email)=lower(?) LIMIT 1",',
        suites=[SUITE],
    ),
    dict(
        name="signup-does-not-ensure-the-index",
        control=(
            "The signup path ensures the index too, not only init_db. A worker that "
            "booted before this shipped, or against a database that lost the index, "
            "would otherwise run check-then-insert with nothing underneath it."
        ),
        path=BOT,
        old="        account_email_uniqueness.ensure_email_identity_index(cur)",
        new="        pass",
        suites=[SUITE],
    ),
]

if __name__ == "__main__":
    sys.exit(run_matrix(ROOT, MUTATIONS))
