"""One email address, one account -- enforced by the database rather than hoped for.

WHAT WAS ACTUALLY WRONG
-----------------------
``users`` had no uniqueness on ``email`` at all. The only guard was a
check-then-insert in the signup path (``bot.py``: ``SELECT user_id FROM users
WHERE lower(email)=lower(?)`` immediately followed by ``INSERT``), which is a
textbook time-of-check-to-time-of-use race: two concurrent signups for the same
address both pass the SELECT, both insert, and the address now names two
accounts.

That matters more than a tidiness argument, because the whole Open Commerce
design treats the email address as the anchor that ties a guest order to a person
(§14). Two accounts sharing an anchor makes "which account does this order belong
to" unanswerable, and no amount of care in the claim flow can recover it after
the fact.

WHY A PARTIAL INDEX ON AN EXPRESSION, AND NOT ``UNIQUE (email)``
----------------------------------------------------------------
Three separate reasons, each of which would independently break the plain form:

1. **Case and whitespace.** ``normalize_email`` is ``.strip().lower()``, so the
   application already treats ``A@B.com`` and ``a@b.com`` as one identity. A
   constraint on the raw column would enforce a *different* notion of identity
   than every lookup in the codebase, which is worse than none: it would permit
   exactly the duplicates the lookups then disagree about. Hence
   ``lower(trim(email))``.

2. **Blank is not a value.** Production has four rows with no usable address --
   three with ``email = ''`` and one NULL (the ``pulsedrop`` system account).
   NULLs are exempt from uniqueness automatically, but ``''`` is not: a plain
   unique index would see three identical empty strings and refuse to build. The
   predicate excludes both, so "no address recorded" stays a legal state for any
   number of rows -- the same shape, and for the same reason, as
   ``ux_users_pulse_id``.

3. **It has to apply today.** Verified against production before writing this:
   42 users, 38 real addresses, and **zero** collisions -- exact or
   case/whitespace-insensitive -- with every stored address already
   ``lower(trim())``-clean. So there is no backfill and no reconciliation
   decision to make. That is a property of being early, not a property of the
   data, and it gets harder every day the platform grows.

``lower(trim(...))`` is deliberately the spelling: ``btrim`` is PostgreSQL-only
and ``trim`` means the same thing on both engines, so the one DDL string is
correct locally and in production. Verified on SQLite 3.53 and PostgreSQL 18.6.

WHY THE DDL IS GUARDED
----------------------
``CREATE UNIQUE INDEX IF NOT EXISTS`` is not free on PostgreSQL -- it takes a
ShareLock on ``users`` even when the index already exists, and ShareLock
conflicts with the RowExclusiveLock any INSERT needs. ``services/schema_guard.py``
exists because that exact pattern once hung half of production, and its docstring
tells the story. So this runs once per worker process, and checks the catalogue
before issuing DDL at all, which keeps the steady state entirely lock-free.

At the current 42 rows a blocking build is instantaneous. It would not be at a
million, where this wants ``CREATE UNIQUE INDEX CONCURRENTLY`` -- which cannot
run inside a transaction and so cannot live in this function. Recorded rather
than pre-solved: the cheap moment to hold this lock is now.
"""
from __future__ import annotations

import logging

from . import db as db_service
from .schema_guard import run_once_per_process

#: The identity the application actually uses, expressed as SQL. Kept as one
#: constant so the index and any query that wants to agree with it cannot drift.
EMAIL_IDENTITY_EXPRESSION = "lower(trim(email))"

#: Rows this constraint has an opinion about. Everything else -- NULL and blank --
#: is outside it, deliberately and unboundedly.
EMAIL_PRESENT_PREDICATE = "email IS NOT NULL AND trim(email) <> ''"

INDEX_NAME = "ux_users_email_identity"

CREATE_INDEX_SQL = (
    f"CREATE UNIQUE INDEX {INDEX_NAME} ON users ({EMAIL_IDENTITY_EXPRESSION}) "
    f"WHERE {EMAIL_PRESENT_PREDICATE}"
)


def index_exists(cur) -> bool:
    """Whether the index is already present, asked of each engine's own catalogue.

    Checked rather than relying on ``IF NOT EXISTS`` so the common case -- every
    boot after the first -- issues no DDL and therefore takes no lock. See the
    module docstring.
    """
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = 'users' AND indexname = ? LIMIT 1",
            (INDEX_NAME,),
        )
    else:
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ? LIMIT 1",
            (INDEX_NAME,),
        )
    return cur.fetchone() is not None


def colliding_groups(cur) -> list[tuple[str, int]]:
    """Addresses that already name more than one account, newest-first by count.

    Empty in production today. Reported rather than resolved, because merging two
    accounts is a product decision about somebody's data and not something a boot
    path may make on its own.
    """
    cur.execute(
        f"SELECT {EMAIL_IDENTITY_EXPRESSION} AS identity, COUNT(*) AS n FROM users "
        f"WHERE {EMAIL_PRESENT_PREDICATE} "
        "GROUP BY 1 HAVING COUNT(*) > 1 ORDER BY 2 DESC"
    )
    return [
        (str(db_service.row_values(row)[0]), int(db_service.row_values(row)[1]))
        for row in cur.fetchall()
    ]


@run_once_per_process
def ensure_email_identity_index(cur) -> bool:
    """Create the index if it is absent and can be built. Returns whether it exists.

    Refuses to try when the data would make the build fail, and says which
    addresses are responsible. A failed ``CREATE UNIQUE INDEX`` inside a request's
    open transaction poisons that transaction on PostgreSQL, so the signup it was
    called from would fail for a reason having nothing to do with signup. Checking
    first turns that into a log line and a ``False`` -- which
    ``run_once_per_process`` deliberately does not cache, so a later boot retries
    once the duplicates are resolved.
    """
    try:
        if index_exists(cur):
            return True
        collisions = colliding_groups(cur)
        if collisions:
            logging.error(
                "EMAIL_IDENTITY_INDEX_BLOCKED groups=%s worst=%s "
                "-- %s not created; duplicate accounts must be reconciled first",
                len(collisions), collisions[0][1], INDEX_NAME,
            )
            return False
        cur.execute(CREATE_INDEX_SQL)
        logging.warning("EMAIL_IDENTITY_INDEX_CREATED name=%s engine=%s",
                        INDEX_NAME, db_service.ENGINE_NAME)
        return True
    except Exception as exc:
        # Never let this stop a boot. The constraint is an improvement on having
        # no constraint; failing to add it leaves the application exactly as
        # correct as it was yesterday, whereas raising here would take down a
        # signup path that works.
        logging.warning("EMAIL_IDENTITY_INDEX_FAILED name=%s error=%s", INDEX_NAME, exc)
        return False
