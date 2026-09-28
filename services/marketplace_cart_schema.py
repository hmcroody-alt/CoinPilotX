"""The canonical owner of the cart table's shape.

Why this module exists
----------------------
``marketplace_cart_items`` shipped **listing-grained**::

    user_id, listing_id, qty, price_snapshot_minor, currency, added_at, updated_at
    UNIQUE(user_id, listing_id)

There was no column able to hold "size M in Snowflake Blue", so a buyer on a
listing that sells four sizes could not put one of them in a cart. The storefront
handled that honestly and at a cost: ``marketplace_web.cart_affordance`` withheld
the add-to-cart control from every listing offering a real choice, because the
route *would* have accepted the add and booked a line naming no variant, priced
from the listing's ``price_label`` rather than from the variant nobody chose.

That refusal was the only place in the lane where the guess could be declined.
This module removes the reason for it.

The constraint is the hard part
-------------------------------
Adding a column is easy; retiring ``UNIQUE(user_id, listing_id)`` is not, and
leaving it in place would defeat the whole change — it is precisely the rule that
forbids a second row for the same listing in a different size. So the ensure has
to *drop* something, which is the one thing this repo's other schema helpers are
careful never to do. It is narrowed to the single legacy constraint, identified
by its column set rather than by a name, and it is replaced in the same call by
the wider unique key it is being traded for. At no point is the table without a
uniqueness rule that stops a double-tap becoming two lines.

The two engines need different mechanics and both are implemented:

* **PostgreSQL** (production) — the constraint has a generated name, so it is
  found by asking ``information_schema`` which unique constraint covers exactly
  ``(user_id, listing_id)`` and dropped by that name. A bare unique *index* with
  the same columns (which is what this module's own earlier index would be on a
  database that ran a previous version) is dropped the same way.
* **SQLite** (tests, local dev) — there is no ``DROP CONSTRAINT`` at all, so the
  table is rebuilt: new table, copy, drop, rename, inside one transaction. This
  is the documented 12-step procedure reduced to what a table of this size needs.
  It runs only when ``sqlite_master`` still shows the two-column UNIQUE.

Why ``variant_id`` is ``0`` and never ``NULL``
----------------------------------------------
``0`` means "this listing has nothing to choose". It is a sentinel, not an id.

It matters because ``NULL`` values are *distinct* from one another in a unique
index on both engines, so a nullable ``variant_id`` would let the same buyer
accumulate unlimited duplicate lines for the same plain listing — the exact bug
the original ``UNIQUE(user_id, listing_id)`` existed to prevent, reintroduced by
the fix meant to preserve it. Every existing row is therefore normalised to ``0``
before the unique index is created, and the column carries a default so a writer
that has not been taught about variants still produces a legal row.

That normalisation is the one write in here. It is a sentinel being written into
a column that has no other possible meaning for a pre-existing row, not a
backfill of data that could have been something else.

What "ensure" guarantees
------------------------
Idempotent, safe to call concurrently from a web process and a worker, safe on
both engines, and it never raises — the failure is returned as data so a caller
in a request path degrades to "cart unavailable" instead of a 500. The result
names which of the four steps ran, so a deployment can be checked rather than
assumed.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

CART_TABLE = "marketplace_cart_items"

#: The sentinel for "no variant". See the module docstring: not NULL, on purpose.
NO_VARIANT = 0

#: The table as it should now be created. ``INTEGER PRIMARY KEY AUTOINCREMENT``
#: is rewritten to ``SERIAL PRIMARY KEY`` by ``services.db._translate_create_table``,
#: which is why the SQLite spelling is safe to send on both engines.
#:
#: A fresh database gets the three-column UNIQUE as a table constraint and never
#: needs the retirement path below. This string is also what the SQLite rebuild
#: builds its replacement table from, so there is exactly one description of the
#: intended shape.
CART_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {CART_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    listing_id INTEGER,
    variant_id INTEGER DEFAULT 0,
    qty INTEGER DEFAULT 1,
    price_snapshot_minor INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    added_at TEXT,
    updated_at TEXT,
    UNIQUE(user_id, listing_id, variant_id)
)
"""

#: Columns added after the table shipped, applied defensively rather than by
#: editing the DDL above — an edit to the CREATE reaches fresh databases only and
#: silently skips production, which already has the table.
CART_COLUMNS = (
    ("variant_id", "INTEGER DEFAULT 0"),
)

#: The uniqueness rule that replaces the legacy one. Created as an index rather
#: than a constraint because ``ALTER TABLE ... ADD CONSTRAINT`` has no
#: ``IF NOT EXISTS`` form on either engine, while ``CREATE UNIQUE INDEX IF NOT
#: EXISTS`` does. PostgreSQL's ``ON CONFLICT`` infers against a unique index just
#: as happily as against a named constraint, so nothing downstream can tell.
CART_UNIQUE_INDEX = "idx_mkt_cart_user_listing_variant"
CART_UNIQUE_INDEX_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {CART_UNIQUE_INDEX} "
    f"ON {CART_TABLE} (user_id, listing_id, variant_id)"
)

#: The legacy key, as a column set. Matched by its columns and not by a name
#: because PostgreSQL generated the name and SQLite never gave it one.
LEGACY_UNIQUE_COLUMNS = ("user_id", "listing_id")

#: The unique key this module leaves behind, in the order ``ON CONFLICT`` names
#: it. Exported so the cart route's upsert cannot drift from the index it infers
#: against — a mismatch there is a Postgres ``ON CONFLICT`` error on the *first*
#: add, not on conflict, so it fails the whole route rather than an edge case.
CONFLICT_COLUMNS = ("user_id", "listing_id", "variant_id")

STATUS_READY = "ready"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"

_SCHEMA_READY = False


def reset_schema_cache() -> None:
    """Forget the process cache. For tests, and for callers that changed the table."""
    global _SCHEMA_READY
    _SCHEMA_READY = False


def _engine_name() -> str:
    from services import db as db_module

    return getattr(db_module, "ENGINE_NAME", "sqlite")


def _result(status: str, **extra) -> dict:
    payload = {
        "status": status,
        "table_created": False,
        "added": [],
        "normalised": 0,
        "legacy_dropped": "",
        "rebuilt": False,
        "index_created": False,
        "error": None,
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Legacy constraint retirement
# ---------------------------------------------------------------------------

# `?` and not `%s`, even though these only ever run on PostgreSQL: every query in
# this codebase is written in the SQLite spelling and `services.db` rewrites the
# placeholder on the way out. A `%s` here would arrive at psycopg2 as a literal
# and the parameter would never bind.
_POSTGRES_LEGACY_UNIQUE_SQL = """
SELECT tc.constraint_name AS name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name
 AND kcu.table_schema = tc.table_schema
WHERE tc.table_schema = current_schema()
  AND tc.table_name = ?
  AND tc.constraint_type = 'UNIQUE'
GROUP BY tc.constraint_name
HAVING array_agg(kcu.column_name::text ORDER BY kcu.column_name) = ARRAY['listing_id','user_id']::text[]
"""

#: A unique *index* covering the same two columns, which is not reported as a
#: constraint and so would survive the query above. Worth asking separately: an
#: index is as effective a block on the second variant row as a constraint is,
#: and one exists on any database that ran a version of this module that indexed
#: the pair before the column set widened.
_POSTGRES_LEGACY_INDEX_SQL = """
SELECT i.relname AS name
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = current_schema()
  AND t.relname = ?
  AND x.indisunique
  AND NOT EXISTS (
        SELECT 1 FROM pg_constraint c WHERE c.conindid = x.indexrelid
  )
  AND (
        SELECT array_agg(a.attname::text ORDER BY a.attname)
        FROM unnest(x.indkey) AS k(attnum)
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
  ) = ARRAY['listing_id','user_id']::text[]
"""


def _retire_legacy_unique_postgres(cur) -> str:
    """Drop the two-column unique key if PostgreSQL still has one. Returns its name."""
    cur.execute(_POSTGRES_LEGACY_UNIQUE_SQL, (CART_TABLE,))
    row = cur.fetchone()
    name = (row[0] if not isinstance(row, dict) else row.get("name")) if row else ""
    if name:
        # Quoted because the generated name is trusted but not controlled here,
        # and an unquoted identifier would fold case.
        cur.execute(f'ALTER TABLE {CART_TABLE} DROP CONSTRAINT "{name}"')
        return str(name)
    cur.execute(_POSTGRES_LEGACY_INDEX_SQL, (CART_TABLE,))
    row = cur.fetchone()
    name = (row[0] if not isinstance(row, dict) else row.get("name")) if row else ""
    if name and str(name) != CART_UNIQUE_INDEX:
        cur.execute(f'DROP INDEX "{name}"')
        return str(name)
    return ""


def _sqlite_table_sql(cur) -> str:
    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (CART_TABLE,),
    )
    row = cur.fetchone()
    if not row:
        return ""
    value = row[0] if not isinstance(row, dict) else row.get("sql")
    return str(value or "")


def sqlite_has_legacy_unique(table_sql: str) -> bool:
    """Does this ``CREATE TABLE`` text still carry ``UNIQUE(user_id, listing_id)``?

    Read off the stored DDL because SQLite exposes no other way to ask. Matched
    on the two columns in either order and tolerant of whitespace, and
    deliberately *not* matched on a three-column form — a table already carrying
    ``UNIQUE(user_id, listing_id, variant_id)`` must not be rebuilt on every
    boot, which would be a table drop per process start.
    """
    import re

    for match in re.finditer(r"UNIQUE\s*\(([^)]*)\)", table_sql or "", re.IGNORECASE):
        columns = tuple(sorted(
            part.strip().strip('"').strip("`").lower()
            for part in match.group(1).split(",")
            if part.strip()
        ))
        if columns == tuple(sorted(LEGACY_UNIQUE_COLUMNS)):
            return True
    return False


def _rebuild_sqlite_table(cur) -> bool:
    """Rebuild the table without the legacy constraint, preserving every row.

    SQLite cannot drop a table constraint, so the shape is changed the only way
    it can be: build the intended table under a temporary name, copy the rows
    into it, drop the original, rename. Row ids are copied explicitly because
    ``cart_update``, ``cart_remove`` and the checkout's cart clear all address
    lines by ``id``, and a rebuild that renumbered them would invalidate every
    line id a client is currently holding.
    """
    from services import db as db_module

    columns = db_module.get_table_columns(cur, CART_TABLE)
    scratch = f"{CART_TABLE}__rebuild"
    cur.execute(f"DROP TABLE IF EXISTS {scratch}")
    cur.execute(CART_TABLE_DDL.replace(CART_TABLE, scratch, 1))
    # Only the columns both tables have, so a database carrying an extra column
    # somebody added out of band is copied rather than refused — and a database
    # missing one this module expects does not produce a column-count mismatch.
    shared = [
        name for name in
        ("id", "user_id", "listing_id", "variant_id", "qty",
         "price_snapshot_minor", "currency", "added_at", "updated_at")
        if name in columns
    ]
    joined = ", ".join(shared)
    # `GROUP BY` collapses any row set that the wider key would now permit but
    # the *narrower* one already forbade — there cannot be one, which is the
    # point: copying with the legacy key's own invariant intact means the insert
    # cannot fail on the new unique index and leave the table dropped.
    cur.execute(f"INSERT INTO {scratch} ({joined}) SELECT {joined} FROM {CART_TABLE}")
    cur.execute(f"DROP TABLE {CART_TABLE}")
    cur.execute(f"ALTER TABLE {scratch} RENAME TO {CART_TABLE}")
    return True


# ---------------------------------------------------------------------------
# Ensure
# ---------------------------------------------------------------------------

def ensure_cart_schema(cur, *, force: bool = False) -> dict:
    """Bring ``marketplace_cart_items`` to its variant-grained shape.

    Four steps, in an order chosen so the table is never briefly unprotected and
    never briefly un-addable:

    1. Create the table if absent — a fresh database is finished after this.
    2. Add ``variant_id`` if absent, and normalise ``NULL`` to ``0``. Before the
       unique index, because NULLs are distinct in one and the index would then
       enforce nothing for legacy rows.
    3. Retire the legacy two-column unique key. After the column exists, so the
       replacement can be created immediately rather than in a later call.
    4. Create the three-column unique index.

    Returns a structured result and never raises. ``status`` is ``ready`` when
    the table ends up variant-grained, ``missing`` when it does not (a role
    without ``ALTER``, most likely), and ``error`` when the ensure itself failed.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return _result(STATUS_READY)

    from services import db as db_module

    outcome = _result(STATUS_READY)
    try:
        existed = True
        try:
            columns = db_module.get_table_columns(cur, CART_TABLE)
        except Exception:
            columns = set()
        if not columns:
            existed = False
        cur.execute(CART_TABLE_DDL)
        if not existed:
            outcome["table_created"] = True
            cur.execute(CART_UNIQUE_INDEX_DDL)
            outcome["index_created"] = True
            _SCHEMA_READY = True
            return outcome

        columns = db_module.get_table_columns(cur, CART_TABLE)
        for name, spec in CART_COLUMNS:
            if name in columns:
                continue
            try:
                cur.execute(f"ALTER TABLE {CART_TABLE} ADD COLUMN {name} {spec}")
                outcome["added"].append(name)
            except Exception as exc:  # pragma: no cover - permissions/locks
                LOGGER.warning("cart schema: could not add %s: %s", name, exc)

        columns = db_module.get_table_columns(cur, CART_TABLE)
        if "variant_id" not in columns:
            # Without the column there is nothing to widen the key to, and the
            # legacy constraint must stay — dropping it here would leave the
            # table with *no* protection against a duplicate line.
            outcome["status"] = STATUS_MISSING
            return outcome

        cur.execute(
            f"UPDATE {CART_TABLE} SET variant_id=? WHERE variant_id IS NULL",
            (NO_VARIANT,),
        )
        outcome["normalised"] = int(getattr(cur, "rowcount", 0) or 0)

        if _engine_name() == "postgresql":
            outcome["legacy_dropped"] = _retire_legacy_unique_postgres(cur)
        elif sqlite_has_legacy_unique(_sqlite_table_sql(cur)):
            outcome["rebuilt"] = _rebuild_sqlite_table(cur)
            outcome["legacy_dropped"] = "UNIQUE(user_id, listing_id)"

        cur.execute(CART_UNIQUE_INDEX_DDL)
        outcome["index_created"] = True
        _SCHEMA_READY = True
        return outcome
    except Exception as exc:
        LOGGER.warning("cart schema ensure failed: %s", exc)
        outcome["status"] = STATUS_ERROR
        outcome["error"] = str(exc)
        return outcome
