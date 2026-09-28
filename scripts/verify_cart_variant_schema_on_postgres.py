#!/usr/bin/env python3
"""Prove `marketplace_cart_schema` on real PostgreSQL. Run against a throwaway only.

## Why this exists

`ensure_cart_schema` retires the legacy `UNIQUE(user_id, listing_id)` by two
completely different code paths. SQLite cannot drop a table-level `UNIQUE`, so it
rebuilds the table; PostgreSQL drops the constraint, or the bare index, by the
name it finds in `pg_constraint` / `pg_index`.

`tests/test_marketplace_cart_variants.py` proves the SQLite path thoroughly and
the PostgreSQL path not at all, because the test suite runs on SQLite. **The path
with no coverage is the one production takes.** The failure mode is not subtle
either: if the drop finds no name, the `ALTER TABLE ... ADD` half still succeeds,
so the table gains `variant_id` while keeping the two-column key -- and then every
second add of a different size to the same listing raises a unique violation on a
live cart.

So this is not a nice-to-have. It is the only thing standing between that path
and a production deploy.

## Why a script and not a test

It needs a PostgreSQL server. Adding it to `tests/` would either skip silently in
CI (a green tick that proved nothing, which is worse than no test) or make the
whole suite require a database it does not otherwise need. A script that a human
runs, against a container they started, reports honestly either way.

## Safety

DSN-guarded, on the repo's usual pattern. It creates and drops
`marketplace_cart_items` repeatedly, so it refuses to run against anything that
does not look like a local throwaway: the host must be loopback, and the URL must
not be the production one. There is no flag to override that.

    docker run -d --rm --name cpx-pgverify -e POSTGRES_PASSWORD=verify \
        -e POSTGRES_DB=cpxverify -p 55432:5432 postgres:18
    DATABASE_URL=postgresql://postgres:verify@127.0.0.1:55432/cpxverify \
        python3 scripts/verify_cart_variant_schema_on_postgres.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _guard(url: str) -> None:
    """Refuse anything that is not plainly a local throwaway.

    Checked before `services.db` is imported, because importing it opens the
    connection pool against whatever `DATABASE_URL` says.
    """
    if not url:
        sys.exit("DATABASE_URL is unset. This script needs a throwaway PostgreSQL.")
    if not url.startswith("postgres"):
        sys.exit(f"DATABASE_URL is not PostgreSQL ({url.split(':')[0]}:...). "
                 "The SQLite path is already covered by the test suite.")
    host = (urlparse(url).hostname or "").lower()
    if host not in LOOPBACK:
        sys.exit(f"REFUSED: host {host!r} is not loopback. This script drops and "
                 "recreates marketplace_cart_items -- point it at a container.")


_guard(os.environ.get("DATABASE_URL", ""))

from services import db as db_service  # noqa: E402
from services import marketplace_cart_schema as cart_schema  # noqa: E402

TABLE = cart_schema.CART_TABLE

#: The shape production is actually carrying, reproduced rather than imported --
#: a constant in the schema module would be a copy that moved with the fix and
#: stopped describing the thing being migrated. `psycopg2`'s own spelling, since
#: this runs before any translation layer would help.
LEGACY_DDL_CONSTRAINT = f"""
CREATE TABLE {TABLE} (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    listing_id INTEGER,
    qty INTEGER DEFAULT 1,
    price_snapshot_minor INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    added_at TEXT,
    updated_at TEXT,
    UNIQUE (user_id, listing_id)
)
"""

#: The same key expressed as a bare unique *index* rather than a constraint.
#: Both exist in the wild -- a constraint is what `CREATE TABLE ... UNIQUE`
#: leaves behind, an index is what a hand-rolled `CREATE UNIQUE INDEX` leaves --
#: and `_retire_legacy_unique_postgres` looks for them in that order. Only the
#: constraint branch is reachable if this file tests one shape, so it tests both.
LEGACY_DDL_INDEX = f"""
CREATE TABLE {TABLE} (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    listing_id INTEGER,
    qty INTEGER DEFAULT 1,
    price_snapshot_minor INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    added_at TEXT,
    updated_at TEXT
);
CREATE UNIQUE INDEX marketplace_cart_items_user_listing_legacy
    ON {TABLE} (user_id, listing_id);
"""

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))


def unique_keys(cur) -> set[tuple[str, ...]]:
    """Every unique constraint *and* bare unique index on the table, as column sets.

    Both, because the replacement key is created as an index and the legacy one
    is usually a constraint -- asking `pg_constraint` alone would report the
    migration as having destroyed the uniqueness it actually preserved.
    """
    cur.execute(
        """
        SELECT i.relname,
               (SELECT array_agg(a.attname::text ORDER BY a.attname)
                  FROM unnest(x.indkey) AS k(attnum)
                  JOIN pg_attribute a
                    ON a.attrelid = t.oid AND a.attnum = k.attnum)
          FROM pg_index x
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_class t ON t.oid = x.indrelid
         WHERE t.relname = %s AND x.indisunique AND NOT x.indisprimary
        """,
        (TABLE,),
    )
    # `for _name, cols in ...` would unpack a *Mapping* here -- psycopg2 rows
    # arrive as `CompatRow`, whose `__iter__` yields column NAMES -- so `cols`
    # became the literal string "array_agg" and every column set came back as
    # `('a','r','r','a','y',...)`. That is the trap `row_values` exists for, and
    # it is worth the noise: this script only has value if a red line means the
    # schema is wrong, and that failure mode is red for a reason that is not.
    rows = [db_service.row_values(r) for r in cur.fetchall()]
    return {tuple(cols or ()) for _name, cols in rows}


def fresh(conn, ddl: str | None) -> None:
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
    cur.execute(f"DROP TABLE IF EXISTS {TABLE}__rebuild")
    if ddl:
        for statement in [s for s in ddl.split(";") if s.strip()]:
            cur.execute(statement)
    conn.commit()
    cart_schema.reset_schema_cache()


LEGACY = ("listing_id", "user_id")           # array_agg orders by attname
WIDENED = ("listing_id", "user_id", "variant_id")


def scenario_legacy(conn, label: str, ddl: str) -> None:
    print(f"\n{label}")
    fresh(conn, ddl)
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO {TABLE} (id, user_id, listing_id, qty, price_snapshot_minor) "
        "VALUES (41, 7, 900, 2, 2400), (77, 7, 901, 1, 999)"
    )
    conn.commit()

    before = unique_keys(cur)
    check("the fixture really carries the legacy key", LEGACY in before, str(sorted(before)))

    outcome = cart_schema.ensure_cart_schema(cur, force=True)
    conn.commit()
    check("ensure reports ready", outcome["status"] == cart_schema.STATUS_READY,
          str(outcome.get("error") or outcome["status"]))
    check("it names the key it dropped", bool(outcome["legacy_dropped"]),
          outcome["legacy_dropped"] or "(nothing dropped -- the ALTER half ran alone)")

    after = unique_keys(cur)
    check("the two-column key is gone", LEGACY not in after, str(sorted(after)))
    check("the three-column key is present", WIDENED in after, str(sorted(after)))

    # Rows and their ids, because cart_update, cart_remove and the checkout
    # cart-clear all address lines by id.
    cur.execute(f"SELECT id, variant_id FROM {TABLE} ORDER BY id")
    rows = [(r[0], r[1]) for r in cur.fetchall()]
    check("rows kept their line ids", [r[0] for r in rows] == [41, 77], str(rows))
    check("backfilled to 0, not NULL", all(r[1] == 0 for r in rows), str(rows))

    # The key asserted as behaviour. A PRAGMA-style reading can agree with the
    # catalogue and still not be enforced.
    cur.execute(f"INSERT INTO {TABLE} (user_id, listing_id, variant_id, qty) "
                "VALUES (7, 950, 5, 1)")
    conn.commit()
    try:
        cur.execute(f"INSERT INTO {TABLE} (user_id, listing_id, variant_id, qty) "
                    "VALUES (7, 950, 5, 1)")
        conn.commit()
        check("a duplicate of the same variant is refused", False, "it was accepted")
    except Exception as exc:
        conn.rollback()
        check("a duplicate of the same variant is refused", True, type(exc).__name__)
    try:
        cur.execute(f"INSERT INTO {TABLE} (user_id, listing_id, variant_id, qty) "
                    "VALUES (7, 950, 6, 1)")
        conn.commit()
        check("a different variant of the same listing is accepted", True)
    except Exception as exc:
        conn.rollback()
        check("a different variant of the same listing is accepted", False, repr(exc))

    # ON CONFLICT infers against a unique index as well as a named constraint,
    # and a target with no matching index errors at plan time -- so the upsert
    # the route actually issues is worth proving here rather than inferring.
    try:
        cur.execute(
            f"INSERT INTO {TABLE} (user_id, listing_id, variant_id, qty) "
            "VALUES (7, 950, 6, 1) "
            "ON CONFLICT (user_id, listing_id, variant_id) DO UPDATE SET qty = "
            f"{TABLE}.qty + 1"
        )
        conn.commit()
        cur.execute(f"SELECT qty FROM {TABLE} WHERE user_id=7 AND listing_id=950 "
                    "AND variant_id=6")
        qty = cur.fetchone()[0]
        check("the route's ON CONFLICT target resolves", qty == 2, f"qty={qty}")
    except Exception as exc:
        conn.rollback()
        check("the route's ON CONFLICT target resolves", False, repr(exc))

    # Idempotency. The retirement must not fire on every boot: on SQLite that
    # would be a table drop per process start, and here it would be a needless
    # ACCESS EXCLUSIVE lock on a live cart table.
    second = cart_schema.ensure_cart_schema(cur, force=True)
    conn.commit()
    check("the second ensure drops nothing", not second["legacy_dropped"],
          second["legacy_dropped"] or "(clean)")
    check("and still reports ready", second["status"] == cart_schema.STATUS_READY)
    check("the key survived the second pass", WIDENED in unique_keys(cur))


def scenario_fresh(conn) -> None:
    print("\n3. A database that has never seen the table at all")
    fresh(conn, None)
    cur = conn.cursor()
    outcome = cart_schema.ensure_cart_schema(cur, force=True)
    conn.commit()
    check("ensure reports ready", outcome["status"] == cart_schema.STATUS_READY,
          str(outcome.get("error") or ""))
    check("it created the table", outcome["table_created"])
    columns = db_service.get_table_columns(cur, TABLE)
    check("variant_id is there", "variant_id" in columns, str(sorted(columns)))
    keys = unique_keys(cur)
    check("keyed on three columns", WIDENED in keys, str(sorted(keys)))
    check("and never on two", LEGACY not in keys, str(sorted(keys)))
    # A NULL here would make the unique index enforce nothing -- NULLs are
    # distinct from one another in a unique index on both engines, so one NULL
    # row would reopen the duplicate-line bug the three-column key exists to
    # close. This first ran as `is_nullable == "NO"` and went red: the column is
    # nullable by DDL on *both* engines, and the module never said otherwise.
    # `CART_TABLE_DDL` says `variant_id INTEGER DEFAULT 0`, and its docstring
    # claims the invariant about rows ("``0`` and never ``NULL``"), not a
    # constraint. So the check was wrong, not the schema -- and the honest
    # version is to assert the invariant the cart actually rests on rather than
    # the mechanism I assumed was holding it up.
    #
    # NOT NULL stays deliberately unadded. It would buy defence in depth against
    # a writer that does not exist -- `marketplace_cart_routes` has the only
    # INSERT and it binds `int(payload.get("variant_id") or NO_VARIANT)`, and the
    # two UPDATEs never name the column -- and it would cost a second stateful
    # rebuild trigger on SQLite, where an already-migrated database cannot gain a
    # NOT NULL without another full table rewrite. New migration machinery on a
    # live money-path table is the larger risk of the two.
    cur.execute(
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s "
        "AND column_name = 'variant_id'",
        (TABLE,),
    )
    nullable, default = db_service.row_values(cur.fetchone())
    check("variant_id defaults to 0", str(default or "").startswith("0"),
          f"default={default}")
    check("nullable by DDL, as both engines are", nullable == "YES",
          f"is_nullable={nullable}")
    # The invariant itself: a writer that never heard of variants still lands a
    # row the unique index can see. This is what the default is *for*, and it is
    # the reason the missing constraint is not a live defect.
    cur.execute(
        f"INSERT INTO {TABLE} (user_id, listing_id, qty) VALUES (5, 600, 1)")
    cur.execute(
        f"SELECT variant_id FROM {TABLE} WHERE user_id=5 AND listing_id=600")
    landed = cur.fetchone()[0]
    check("a writer omitting the column lands 0, not NULL", landed == 0,
          f"variant_id={landed!r}")
    # And so the key really does bite for that row, which a NULL would prevent.
    try:
        cur.execute(
            f"INSERT INTO {TABLE} (user_id, listing_id, qty) VALUES (5, 600, 1)")
        conn.commit()
        check("and a second such row is refused", False, "no UniqueViolation")
    except Exception as exc:  # noqa: BLE001 - the type is the assertion
        conn.rollback()
        check("and a second such row is refused",
              exc.__class__.__name__ == "UniqueViolation", exc.__class__.__name__)


def main() -> int:
    print(f"engine: {db_service.ENGINE_NAME}  (IS_POSTGRES={db_service.IS_POSTGRES})")
    if not db_service.IS_POSTGRES:
        sys.exit("services.db did not resolve PostgreSQL. Check DATABASE_URL.")
    conn = db_service.connect()
    try:
        scenario_legacy(conn, "1. Legacy key as a table CONSTRAINT (what CREATE TABLE leaves)",
                        LEGACY_DDL_CONSTRAINT)
        scenario_legacy(conn, "2. Legacy key as a bare unique INDEX (what a hand-rolled one leaves)",
                        LEGACY_DDL_INDEX)
        scenario_fresh(conn)
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        conn.commit()
    finally:
        conn.close()

    failed = [label for label, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAIL:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("PASS: the PostgreSQL retirement path does what the SQLite tests claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
