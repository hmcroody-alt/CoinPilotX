"""The replay ledger, against the database it actually runs on.

Every assertion in `tests/test_federated_replay.py` runs on SQLite, and SQLite
cannot see the two rewrites this module's correctness rests on:

  * `INSERT OR IGNORE` is SQLite syntax. On PostgreSQL `services/db.py` rewrites
    it to `ON CONFLICT DO NOTHING`. If that rewrite misses this statement, the
    conflict raises `UniqueViolation` instead of resolving quietly -- and
    because a failed statement aborts the whole transaction on PostgreSQL, the
    sign-in's own connection would be poisoned for every statement after it.
    That is worse than no defence: a replay attempt would break the *next*
    legitimate sign-in sharing the connection.
  * `INTEGER PRIMARY KEY AUTOINCREMENT` is also SQLite syntax, rewritten to
    `SERIAL PRIMARY KEY`. If that rewrite misses, `ensure_schema` raises during
    `init_db()` and the application does not boot.

Neither can be inferred from a green SQLite run, so both are checked here
against a real server. `rowcount` is checked too, because the whole decision
("was this a replay?") is `rowcount == 0`, and `ON CONFLICT DO NOTHING`
reporting 1 on a conflict would make every replay look like a first use.

Imports `services.db` directly and never `bot`, because importing bot runs
`init_db()` at module scope and would write ~170 tables into whatever database
is bound.

    docker run -d --rm --name cpx-replay-pg -e POSTGRES_PASSWORD=replay \
        -e POSTGRES_DB=replay -p 55439:5432 postgres:18
    DATABASE_URL=postgresql://postgres:replay@127.0.0.1:55439/replay \
        PYTHONPATH=. .venv/bin/python scripts/verify_federated_replay_on_postgres.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
    print("refusing to run: DATABASE_URL must name a PostgreSQL server")
    print("(the entire point is the dialect; SQLite here would prove nothing)")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db  # noqa: E402
from services import federated_replay  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if detail:
        print(f"        {detail}")
    if not condition:
        failures.append(label)


print(f"engine: {db.ENGINE_NAME}")
if not str(db.ENGINE_NAME).startswith("postgres"):
    print("services/db.py did not resolve to the postgres engine")
    sys.exit(2)

print("\nthe AUTOINCREMENT rewrite -- does the DDL survive translation?")
conn = db.connect()
try:
    conn.execute(f"DROP TABLE IF EXISTS {federated_replay.TABLE}")
    conn.commit()
finally:
    conn.close()
try:
    federated_replay.ensure_schema()
    check("ensure_schema runs on PostgreSQL", True)
except Exception as exc:
    check("ensure_schema runs on PostgreSQL", False, f"{exc.__class__.__name__}: {exc}")
    sys.exit(1)

conn = db.connect()
try:
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
        (federated_replay.TABLE,),
    )
    columns = cur.fetchall()
    print("        " + "; ".join(f"{c[0]}:{c[1]}" for c in columns))
    id_column = [c for c in columns if c[0] == "id"]
    check(
        "`id` became a real serial integer, not a literal AUTOINCREMENT",
        bool(id_column) and "int" in str(id_column[0][1]).lower(),
        f"id -> {id_column[0][1] if id_column else 'missing'}, "
        f"default={id_column[0][3] if id_column else 'n/a'}",
    )

    cur.execute(
        "SELECT indexdef FROM pg_indexes WHERE tablename = %s ORDER BY indexname",
        (federated_replay.TABLE,),
    )
    indexes = [row[0] for row in cur.fetchall()]
    for definition in indexes:
        print(f"        {definition}")
    check(
        "the UNIQUE constraint on credential_hash exists server-side",
        any("UNIQUE" in d and "credential_hash" in d for d in indexes),
        "without it the race has no referee",
    )
    check(
        "the retention scan is indexed on expires_at",
        any("expires_at" in d for d in indexes),
    )
finally:
    conn.close()

print("\nthe INSERT OR IGNORE rewrite -- does a conflict resolve or raise?")
token = f"pg-token-{time.time()}"
federated_replay.consume("google", token, expires_at_epoch=time.time() + 600)
check("first presentation accepted", True)

try:
    federated_replay.consume("google", token, expires_at_epoch=time.time() + 600)
    check("second presentation refused", False, "it was ACCEPTED -- no defence on Postgres")
except federated_replay.ReplayError as exc:
    check(
        "second presentation refused as a ReplayError",
        exc.reason == "credential_replayed",
        f"reason={exc.reason}",
    )
except Exception as exc:
    # This is the failure mode worth the whole script: a raised UniqueViolation
    # means the rewrite missed and the caller's transaction is now aborted.
    check(
        "second presentation refused as a ReplayError",
        False,
        f"raised {exc.__class__.__name__} instead -- the rewrite missed and this "
        f"would abort the sign-in's transaction",
    )

print("\nthe connection is still usable after a refused replay")
# The reason `INSERT OR IGNORE` was chosen over catching IntegrityError. A
# poisoned connection is invisible until the next statement.
try:
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {federated_replay.TABLE}")
        count = db.row_values(cur.fetchone())[0]
        check("a query after the refusal still works", True, f"{count} row(s)")
    finally:
        conn.close()
except Exception as exc:
    check("a query after the refusal still works", False, f"{exc.__class__.__name__}: {exc}")

print("\nsame-transaction reuse -- the route's own connection stays healthy")
# The route calls consume() without passing a conn today, but the parameter
# exists, so a refusal on a shared connection must leave it writable.
conn = db.connect()
try:
    shared = f"pg-shared-{time.time()}"
    federated_replay.consume("apple", shared, expires_at_epoch=time.time() + 600, conn=conn)
    refused = False
    try:
        federated_replay.consume("apple", shared, expires_at_epoch=time.time() + 600, conn=conn)
    except federated_replay.ReplayError:
        refused = True
    check("a replay on a shared connection is refused", refused)
    cur = conn.execute(f"SELECT COUNT(*) FROM {federated_replay.TABLE}")
    check(
        "the shared connection can still execute after the refusal",
        True,
        "no InFailedSqlTransaction",
    )
    conn.commit()
except Exception as exc:
    check(
        "the shared connection can still execute after the refusal",
        False,
        f"{exc.__class__.__name__}: {exc}",
    )
finally:
    conn.close()

print("\nconcurrency, on a server that really does run statements at once")
# SQLite serialises writes with a file lock, so its version of this test cannot
# distinguish "the UNIQUE constraint decided" from "the lock decided". Postgres
# has genuine concurrent writers.
raced = f"pg-raced-{time.time()}"
attempts = 10
barrier = threading.Barrier(attempts)
outcomes: list[str] = []
lock = threading.Lock()


def attempt() -> None:
    barrier.wait(timeout=30)
    try:
        federated_replay.consume("google", raced, expires_at_epoch=time.time() + 600)
        result = "accepted"
    except federated_replay.ReplayError:
        result = "refused"
    except Exception as exc:
        result = f"error:{exc.__class__.__name__}"
    with lock:
        outcomes.append(result)


threads = [threading.Thread(target=attempt) for _ in range(attempts)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=60)

check(
    "exactly one of ten simultaneous presentations won",
    outcomes.count("accepted") == 1,
    f"outcomes={sorted(outcomes)}",
)
check(
    "the nine losers were refusals, not errors",
    outcomes.count("refused") == attempts - 1,
    "an error here would be an outage dressed as a defence",
)

conn = db.connect()
try:
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM {federated_replay.TABLE} WHERE credential_hash = %s",
        (federated_replay.digest(raced),),
    )
    check("one row, not ten", db.row_values(cur.fetchone())[0] == 1)
finally:
    conn.close()

print("\nretention, against the server's own clock")
conn = db.connect()
try:
    conn.execute(
        f"INSERT INTO {federated_replay.TABLE} "
        f"(credential_hash, provider, consumed_at, expires_at) VALUES (%s, %s, %s, %s)",
        ("pgstale" + "0" * 57, "google", "2000-01-01 00:00:00", "2000-01-01 01:00:00"),
    )
    conn.commit()
finally:
    conn.close()
removed = federated_replay.purge_expired()
check("the expired row was purged", removed >= 1, f"{removed} row(s) deleted")

conn = db.connect()
try:
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM {federated_replay.TABLE} WHERE credential_hash = %s",
        ("pgstale" + "0" * 57,),
    )
    check("and it is gone", db.row_values(cur.fetchone())[0] == 0)
    cur.execute(
        f"SELECT COUNT(*) FROM {federated_replay.TABLE} WHERE credential_hash = %s",
        (federated_replay.digest(raced),),
    )
    check("while the live row survived the purge", db.row_values(cur.fetchone())[0] == 1)
finally:
    conn.close()

print("\n" + "=" * 68)
if failures:
    print(f"{len(failures)} check(s) failed on PostgreSQL:")
    for label in failures:
        print(f"  - {label}")
    sys.exit(1)
print("the ledger behaves identically on PostgreSQL: both rewrites land, the")
print("conflict resolves without raising, and the race has exactly one winner.")
