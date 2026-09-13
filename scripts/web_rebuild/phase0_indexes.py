#!/usr/bin/env python3
"""Phase 0 index changes for the web rebuild. Idempotent, online, reversible.

Four changes, all of them cheap, none of them holding a long lock:

  1. UNIQUE index on ``lower(users.username)`` — ``/@username`` is a sequential
     scan today. There is no index and no uniqueness, so two users could hold
     the same name in different case and the web profile route would have no
     defined answer for which one it means.
  2. UNIQUE index on ``lower(users.email)`` — login is a sequential scan for the
     same reason.
  3. UNIQUE index on ``active_sessions.session_hash`` — the table carries only a
     primary key index, so session lookup is a sequential scan and duplicate
     hashes are not prevented.
  4. Drop three redundant indexes: one of the two identical UNIQUE indexes on
     ``pulse_saved_items``, and two exact duplicate pairs on ``pulse_messages``.
     Pure write amplification.

**Why this is a script and not `init_db()`.** ``CREATE INDEX CONCURRENTLY``
cannot run inside a transaction block, and ``init_db()`` runs everything in one.
It is also Postgres-only syntax and ``init_db()`` has to work on SQLite.

**Three things this script does that the obvious version does not:**

*It checks the data before it constrains it.* A UNIQUE index that fails halfway
through a build on a live table is a deploy incident. Each unique index is
preceded by the query that proves it can succeed, and the step is skipped —
loudly — if it cannot.

*It indexes ``lower(nullif(col, ''))`` rather than using a partial index.* This
is the least obvious decision in the file and it was measured, not reasoned.

``users`` holds 6 blank usernames and 3 blank emails — all literal ``''``, no
NULLs. A plain unique index on ``lower(username)`` therefore fails to build: the
six blanks collide with each other. The textbook fix is a partial index,
``... WHERE lower(username) <> ''``, and it does build.

It is also a trap. A partial index can only be used when the planner can *prove*
the query's WHERE clause implies the index predicate. For a literal
(``lower(username) = 'user7'``) it can. For a bound parameter it cannot — and
Postgres switches a prepared statement to a **generic plan on the sixth
execution**. Measured on Postgres 18.6:

    EXECUTE q('user7')   -- 1..5  Index Scan using ux_users_username_lower
    EXECUTE q('user7')   -- 6+    Seq Scan on users

So ``/@username`` would be indexed for five hits per prepared statement and
sequentially scan for every hit after that, forever, with no error and no log
line. The symptom is "the profile page got slow under load" and the cause is
four layers away.

``lower(nullif(username, ''))`` has no predicate to discharge. The six blanks
index as NULL, and NULLs do not collide in a btree unique index, so uniqueness
is enforced on every real value while the blanks coexist. Verified stable across
the generic-plan switch.

**The cost, stated plainly:** queries must use the same expression —
``WHERE lower(nullif(username, '')) = lower(%s)``. That is already true of any
expression index (``lower(username)`` requires writing ``lower(username)``), but
it is one more thing a query author has to match exactly.

*It handles the invalid-index trap.* When ``CREATE INDEX CONCURRENTLY`` fails —
a deploy restart, a lock timeout, a conflicting row — Postgres leaves an
**invalid** index behind under the name you asked for. Every subsequent run of
``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` then sees the name, does nothing,
and reports success. The index never gets built, the planner never uses it, and
nothing ever says so. This script looks for ``pg_index.indisvalid = false``
first and drops the corpse before retrying.

Usage::

    # what it would do, touching nothing (default)
    railway run --service Postgres python3 scripts/web_rebuild/phase0_indexes.py

    # actually do it
    railway run --service Postgres python3 scripts/web_rebuild/phase0_indexes.py --apply

Verify on a throwaway Postgres 18 container before production, never the other
way round::

    DATABASE_URL=postgresql://postgres:x@127.0.0.1:55432/pulsecheck \
        python3 scripts/web_rebuild/phase0_indexes.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
import time

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover
    sys.exit("psycopg2 is required. Use the repo's .venv.")


# --- the plan -------------------------------------------------------------
# Each CREATE carries the query that proves it can succeed. `guard` must return
# zero rows; anything else means the data would reject the constraint and the
# step is skipped rather than attempted.

CREATES = [
    {
        "name": "ux_users_username_lower",
        "table": "users",
        "sql": (
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_users_username_lower "
            "ON users (lower(nullif(username, '')))"
        ),
        "why": "/@username is a sequential scan on every web profile page hit",
        "guard": (
            "SELECT lower(nullif(username, '')) AS value, count(*) AS n FROM users "
            "WHERE nullif(username, '') IS NOT NULL "
            "GROUP BY 1 HAVING count(*) > 1"
        ),
        "guard_failure": "usernames that collide case-insensitively",
        "query_form": "WHERE lower(nullif(username, '')) = lower(%s)",
    },
    {
        "name": "ux_users_email_lower",
        "table": "users",
        "sql": (
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_users_email_lower "
            "ON users (lower(nullif(email, '')))"
        ),
        "why": "login is a sequential scan",
        "guard": (
            "SELECT lower(nullif(email, '')) AS value, count(*) AS n FROM users "
            "WHERE nullif(email, '') IS NOT NULL "
            "GROUP BY 1 HAVING count(*) > 1"
        ),
        "guard_failure": "email addresses that collide case-insensitively",
        "query_form": "WHERE lower(nullif(email, '')) = lower(%s)",
    },
    {
        # Not the nullif form: a session hash is never legitimately blank, and
        # the natural query is `WHERE session_hash = %s`. Making it an
        # expression index would force every call site to wrap the column for
        # no benefit. A second blank hash raising a unique violation is the
        # correct outcome — it means something wrote a session with no token.
        "name": "ux_active_sessions_session_hash",
        "table": "active_sessions",
        "sql": (
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_active_sessions_session_hash "
            "ON active_sessions (session_hash)"
        ),
        "why": "table has only a pkey index; session lookup is a seq scan and "
               "duplicate hashes are not prevented",
        "guard": (
            "SELECT session_hash AS value, count(*) AS n FROM active_sessions "
            "WHERE session_hash IS NOT NULL "
            "GROUP BY 1 HAVING count(*) > 1"
        ),
        "guard_failure": "duplicate session hashes",
        "query_form": "WHERE session_hash = %s",
    },
]

# Drops. Each names the index it is a duplicate OF, and that survivor is checked
# to exist and be valid before the drop runs — dropping both halves of a pair
# because the survivor was already gone is the one irreversible mistake here.
#
# pulse_saved_items: the survivor is the constraint-backed index
# (`..._key`), because dropping that one needs ALTER TABLE DROP CONSTRAINT and
# would take an ACCESS EXCLUSIVE lock. The plain duplicate drops concurrently.
DROPS = [
    {
        "name": "ux_pulse_saved_items_user_content",
        "duplicate_of": "pulse_saved_items_user_id_content_type_content_id_key",
        "why": "identical UNIQUE (user_id, content_type, content_id)",
    },
    {
        "name": "idx_pulse_messages_conversation_created",
        "duplicate_of": "idx_pulse_messages_conversation",
        "why": "identical btree (conversation_id, created_at)",
    },
    {
        "name": "idx_pulse_msg_conv",
        "duplicate_of": "idx_pulse_messages_conversation_id",
        "why": "identical btree (conversation_id, id)",
    },
]


def log(message: str) -> None:
    print(message, flush=True)


def connect():
    url = (
        os.getenv("DATABASE_URL")
        or os.getenv("DATABASE_PUBLIC_URL")
        or ""
    ).strip()
    if not url:
        sys.exit("DATABASE_URL is not set. Run under `railway run --service Postgres`.")
    if url.startswith("sqlite") or url.startswith("file:"):
        sys.exit(
            "This script is Postgres-only. CONCURRENTLY, partial expression "
            "indexes and pg_index are not SQLite features, and SQLite will not "
            "reproduce the behaviour being verified."
        )
    conn = psycopg2.connect(url)
    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction block.
    conn.autocommit = True
    return conn


def index_state(cur, name: str) -> str:
    """One of "missing", "invalid", "valid"."""
    cur.execute(
        "SELECT i.indisvalid FROM pg_class c "
        "JOIN pg_index i ON i.indexrelid = c.oid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = %s AND n.nspname = current_schema()",
        (name,),
    )
    row = cur.fetchone()
    if row is None:
        return "missing"
    return "valid" if row[0] else "invalid"


def plan_survives_generic_plan(cur, step: dict) -> tuple[bool, str]:
    """Prove the index is still usable once Postgres stops inlining the literal.

    This is the check that would have caught the partial-index trap described in
    the module docstring, where the index is used for five executions of a
    prepared statement and then silently abandoned on the sixth.

    ``enable_seqscan = off`` on purpose: the question is *can the planner use
    this index for this query shape*, not *would it choose to* on a 39-row
    table. A cost comparison here would pass trivially and prove nothing.
    """
    form = step.get("query_form")
    if not form:
        return True, "no query form declared"
    where = form.replace("%s", "$1")
    name, table = step["name"], step["table"]
    try:
        cur.execute("DEALLOCATE ALL")
        # Session-level, not SET LOCAL: this connection is in autocommit,
        # so there is no transaction for LOCAL to be local to and it would
        # silently do nothing. Restored below.
        cur.execute("SET enable_seqscan = off")
        cur.execute(f"PREPARE pulse_phase0_check (text) AS SELECT 1 FROM {table} {where}")
        plan = ""
        for _ in range(7):  # the generic plan appears on the 6th
            cur.execute("EXPLAIN EXECUTE pulse_phase0_check ('probe')")
            plan = "\n".join(row[0] for row in cur.fetchall())
        cur.execute("DEALLOCATE ALL")
        cur.execute("SET enable_seqscan = on")
    except Exception as exc:
        try:
            cur.execute("SET enable_seqscan = on")
        except Exception:
            pass
        return True, f"not checked ({str(exc).splitlines()[0][:80]})"
    return (name in plan), plan.splitlines()[0].strip() if plan else ""


def run_create(cur, step: dict, apply: bool) -> str:
    name = step["name"]
    state = index_state(cur, name)

    if state == "valid":
        log(f"  = {name} already present and valid")
        return "skipped"

    if state == "invalid":
        # The trap: IF NOT EXISTS would see this name and do nothing forever.
        log(f"  ! {name} exists but is INVALID — a previous CONCURRENTLY build "
            f"failed. Dropping the corpse before retrying.")
        if apply:
            cur.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        else:
            log(f"    would run: DROP INDEX CONCURRENTLY IF EXISTS {name}")

    cur.execute(step["guard"])
    conflicts = cur.fetchall()
    if conflicts:
        log(f"  x {name} SKIPPED — {len(conflicts)} {step['guard_failure']}. "
            f"Constraining this column would fail mid-build on a live table. "
            f"Resolve the data first. First few: {conflicts[:3]}")
        return "blocked"

    log(f"  + {name}  ({step['why']})")
    if not apply:
        log(f"    would run: {step['sql']}")
        return "planned"

    started = time.time()
    cur.execute(step["sql"])
    elapsed = time.time() - started

    if index_state(cur, name) != "valid":
        log(f"  x {name} built but is not valid. Investigate before retrying.")
        return "failed"
    log(f"    built in {elapsed:.2f}s")

    usable, detail = plan_survives_generic_plan(cur, step)
    if usable:
        log(f"    plan check: usable under a generic plan  [{step['query_form']}]")
    else:
        log(f"  x {name} is NOT used once the plan goes generic — it will help "
            f"the first five executions of a prepared statement and nothing "
            f"after. Plan was: {detail}")
        return "failed"
    return "created"


def run_drop(cur, step: dict, apply: bool) -> str:
    name, survivor = step["name"], step["duplicate_of"]

    if index_state(cur, name) == "missing":
        log(f"  = {name} already gone")
        return "skipped"

    survivor_state = index_state(cur, survivor)
    if survivor_state != "valid":
        # Dropping both halves of a pair is the one irreversible mistake here.
        log(f"  x {name} NOT dropped — its survivor {survivor} is "
            f"{survivor_state}. Dropping this would leave the table with "
            f"neither index.")
        return "blocked"

    log(f"  - {name}  ({step['why']}; {survivor} survives)")
    if not apply:
        log(f"    would run: DROP INDEX CONCURRENTLY IF EXISTS {name}")
        return "planned"
    cur.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    return "dropped"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="execute. Without it the script reports what it would do and "
             "changes nothing.",
    )
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT current_database(), version()")
    database, version = cur.fetchone()
    log(f"database: {database}")
    log(f"server:   {version.split(' on ')[0]}")
    log(f"mode:     {'APPLY' if args.apply else 'dry run (use --apply to execute)'}")

    results: dict[str, int] = {}

    log("\nindexes to create")
    for step in CREATES:
        outcome = run_create(cur, step, args.apply)
        results[outcome] = results.get(outcome, 0) + 1

    log("\nredundant indexes to drop")
    for step in DROPS:
        outcome = run_drop(cur, step, args.apply)
        results[outcome] = results.get(outcome, 0) + 1

    log("\nsummary: " + ", ".join(f"{k}={v}" for k, v in sorted(results.items())))
    conn.close()
    # A blocked step is not a crash, but it must not read as success either.
    return 1 if results.get("blocked") or results.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
