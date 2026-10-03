"""A cart line whose owner is NULL is not deduplicated, and nothing says so.

## The hazard

`marketplace_cart_items` is variant-grained now, and its protection against a
double-tap becoming two lines is the three-column unique index
`(user_id, listing_id, variant_id)` — `cart_add` upserts against it by name via
`cart_schema.CONFLICT_COLUMNS`, and the comment there says out loud that the
constraint "turns the second add into a quantity update".

`user_id` is declared `INTEGER` with no `NOT NULL`. In SQL a NULL is not equal to
any other NULL, *including inside a unique index*, on both SQLite and PostgreSQL.
So the moment a cart row is written with a NULL owner, that index stops matching
anything for it: the `ON CONFLICT` finds no existing row, the insert succeeds a
second time, and the buyer sees one product listed twice at a total they never
chose. The `MAX_QTY_PER_LINE` clamp goes with it, because the quantity it clamps
lives on the row that was supposed to be found.

Today that is unreachable, which is the only reason it is not a live bug. It used
to be unreachable by accident: every write site in
`services/marketplace_cart_routes.py` bound `int(user["user_id"])`, and
`int(None)` raises. Since the guest cart landed it is unreachable on purpose —
`_cart_owner` is the single source of the owner, it allocates a real guest id
rather than leaving `user_id` NULL, and `cart_add` refuses with a 503 if it
comes back empty. A cart has an owner or it is not written.

## Why pin an unreachable bug

Because the obvious way to give a guest a cart is to let `user_id` stay NULL for
one, and that one-line change silently deletes the constraint rather than
failing. There is no error, no migration, no test that goes red — the dedupe just
stops working for exactly the population that has no account. A guard is cheap
now and the alternative is discovering it from a support ticket about a doubled
order total.

This is deliberately *not* a schema change. `UNIQUE NULLS NOT DISTINCT` exists in
PostgreSQL 15+ but has no SQLite equivalent, and with no migration framework here
one DDL string has to be correct on both engines. The designed fix is a non-null
owner *value* rather than an absence — `owner_key` as `u:<user_id>` for an
account and `g:<sha256 of a guest token>` for a guest, making uniqueness
`(owner_key, listing_id, variant_id)` over ordinary values with no NULL
semantics involved. That work is written and unlanded on
`feat/commerce-identity-hardening` (PR #86); this file is the guard that makes
its absence noisy instead of silent, and the reason the swap is not being done
speculatively is that there is no guest cart yet to need it.

## What this file proves

1. Empirically, against main's own DDL strings, that a NULL owner defeats the
   index while a real owner does not. The asymmetry is the whole finding.
2. That the route still binds a non-null owner, so the hazard stays latent.
   Checked on the syntax tree rather than by grepping, so it reads the argument
   that is actually bound rather than any text that happens to appear nearby.
"""

from __future__ import annotations

import ast
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `marketplace_cart_schema` owns the table's shape and imports nothing but
# `logging` at module scope -- it reaches `services.db` only inside functions.
# So this file costs no `import bot`, which is why it can live here and run in a
# shared pytest process instead of needing one of its own.
from services import marketplace_cart_schema as cart_schema  # noqa: E402

CART_ROUTES = ROOT / "services" / "marketplace_cart_routes.py"

LISTING = 4242
VARIANT = 7

# The table has two live shapes and they are protected by different objects.
#
#   fresh     -- created by `CART_TABLE_DDL`, which declares `UNIQUE(user_id,
#                listing_id, variant_id)` inline as a table constraint. The
#                separate unique index is then redundant here.
#   migrated  -- what PostgreSQL production actually runs. `ensure_cart_schema`
#                found a legacy two-column constraint, and
#                `_retire_legacy_unique_postgres` dropped it; the three-column
#                unique *index* is the only thing standing between a double-tap
#                and two cart lines.
#
# Testing only `fresh` is how this file originally passed a mutation that made
# `CART_UNIQUE_INDEX_DDL` non-unique: the inline table constraint quietly did
# the work, so breaking the index changed nothing. That is precisely backwards
# from production, where the index is the sole protection. Both shapes run.
SHAPES = ("fresh", "migrated")

_INLINE_UNIQUE = re.compile(r",\s*UNIQUE\s*\([^)]*\)", re.IGNORECASE)


def _migrated_ddl() -> str:
    """`CART_TABLE_DDL` with its inline UNIQUE stripped, as production has it.

    Derived from the shipped string rather than hand-written, so the migrated
    shape tracks every other column change automatically. The strip is asserted
    because a regex that silently matches nothing would hand back the *fresh*
    shape under the migrated name and re-open the exact blind spot this exists
    to close.
    """
    stripped, count = _INLINE_UNIQUE.subn("", cart_schema.CART_TABLE_DDL, count=1)
    assert count == 1, (
        "expected exactly one inline UNIQUE(...) in CART_TABLE_DDL to strip, "
        f"removed {count}. If the shipped DDL no longer declares its key inline, "
        "this helper is reproducing the fresh shape under the migrated name and "
        "proving nothing -- rewrite it against however the key is declared now."
    )
    assert "UNIQUE" not in stripped.upper(), (
        "CART_TABLE_DDL still declares a UNIQUE constraint after stripping one, "
        "so the migrated shape is still protected by the table rather than by "
        f"the index. Remaining DDL:\n{stripped}"
    )
    return stripped


def _cart_db(shape: str) -> sqlite3.Connection:
    """A cart table built from the module's own DDL, not a copy of it.

    Using `CART_TABLE_DDL` and `CART_UNIQUE_INDEX_DDL` verbatim means this test
    cannot pass against a hand-written table that has drifted from the real one:
    if the shipped shape loses its unique index, or gains `NOT NULL` on the
    owner, that shows up here as a behaviour change rather than being papered
    over by a local fixture.
    """
    assert shape in SHAPES, f"unknown cart shape {shape!r}"
    conn = sqlite3.connect(":memory:")
    conn.execute(cart_schema.CART_TABLE_DDL if shape == "fresh" else _migrated_ddl())
    conn.execute(cart_schema.CART_UNIQUE_INDEX_DDL)
    return conn


def _add(conn: sqlite3.Connection, owner) -> None:
    """One add-to-cart, upserting the way `cart_add` does.

    The conflict target is spelled from `CONFLICT_COLUMNS` for the same reason
    the route spells it that way: so this test infers against whatever index the
    shipped schema actually creates.
    """
    conn.execute(
        f"""
        INSERT INTO marketplace_cart_items
            (user_id, listing_id, variant_id, qty, price_snapshot_minor, currency, added_at, updated_at)
        VALUES (?, ?, ?, 1, 9533, 'USD', '2026-09-30T00:00:00Z', '2026-09-30T00:00:00Z')
        ON CONFLICT({", ".join(cart_schema.CONFLICT_COLUMNS)})
        DO UPDATE SET qty = marketplace_cart_items.qty + excluded.qty
        """,
        (owner, LISTING, VARIANT),
    )


def _lines(conn: sqlite3.Connection) -> list[tuple]:
    return list(conn.execute(
        "SELECT user_id, qty FROM marketplace_cart_items ORDER BY id"
    ))


@pytest.mark.parametrize("shape", SHAPES)
def test_a_real_owner_is_deduplicated_into_a_quantity(shape: str) -> None:
    """The control. Without this the NULL case below proves nothing.

    Runs on both shapes because they are protected by different objects: on
    `fresh` the inline table constraint dedupes, on `migrated` only the unique
    index can. A regression in the index alone is invisible to `fresh`.
    """
    conn = _cart_db(shape)
    _add(conn, 1)
    _add(conn, 1)

    lines = _lines(conn)
    assert lines == [(1, 2)], (
        f"on the {shape!r} cart shape, two adds of the same listing+variant by "
        "the same signed-in buyer should collapse to one line at quantity 2, got "
        f"{lines!r}. If this fails the cart's double-tap protection is broken for "
        "*every* buyer, not just the NULL-owner case the rest of this file is "
        f"about -- check that {cart_schema.CART_UNIQUE_INDEX} still covers "
        f"{cart_schema.CONFLICT_COLUMNS}."
        + (
            " This is the migrated shape, where that index is the only "
            "protection, so a failure here and a pass on 'fresh' means the index "
            "specifically regressed and PostgreSQL production is exposed."
            if shape == "migrated"
            else ""
        )
    )


@pytest.mark.parametrize("shape", SHAPES)
def test_a_null_owner_defeats_the_cart_dedupe(shape: str) -> None:
    """The finding: the same two adds do not collapse when the owner is NULL."""
    conn = _cart_db(shape)
    _add(conn, None)
    _add(conn, None)

    lines = _lines(conn)
    assert lines == [(None, 1), (None, 1)], (
        f"on the {shape!r} cart shape, expected a NULL owner to produce two "
        f"separate cart lines, got {lines!r}. If this now collapses to one line, "
        "the schema gained something that makes NULLs comparable (a NOT NULL "
        "owner, or `UNIQUE NULLS NOT DISTINCT`) -- which would be the fix, and "
        "this test should be replaced by one asserting the new guarantee rather "
        "than deleted."
    )
    assert len(lines) == 2, f"two lines is the bug being pinned ({shape} shape)"


def _cart_insert_owner_argument() -> tuple[ast.expr, ast.FunctionDef]:
    """The expression bound to `user_id` by the cart's INSERT, and its handler.

    Read off the syntax tree because the thing that matters is the *argument
    that is bound*, and a text search would equally match the column list, the
    `ON CONFLICT` target, the surrounding comments, or any of the dozen other
    `int(user["user_id"])` occurrences in this module that belong to reads and
    deletes rather than to the insert.

    The enclosing handler comes back with it because what keeps the owner
    non-NULL is no longer visible in the argument alone -- it is a guard
    earlier in the same function.
    """
    tree = ast.parse(CART_ROUTES.read_text(encoding="utf-8"))
    functions = [
        fn for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for function in functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            sql = node.args[0]
            # The statement is an f-string, so the literal prefix is a constant
            # segment inside a JoinedStr rather than a plain string node.
            segments = (
                [part.value for part in sql.values if isinstance(part, ast.Constant)]
                if isinstance(sql, ast.JoinedStr)
                else [sql.value] if isinstance(sql, ast.Constant) else []
            )
            text = " ".join(str(seg) for seg in segments)
            if "INSERT INTO marketplace_cart_items" not in text:
                continue
            params = node.args[1]
            assert isinstance(params, ast.Tuple) and params.elts, (
                "the cart INSERT's parameters are no longer a literal tuple, so "
                "the owner argument cannot be read positionally. Re-point this "
                "check at however they are passed now."
            )
            return params.elts[0], function
    raise AssertionError(
        "found no `INSERT INTO marketplace_cart_items` call with bound "
        f"parameters in {CART_ROUTES.name}. The upsert moved; this guard is "
        "looking in the wrong place and is currently protecting nothing."
    )


def test_the_cart_insert_still_binds_a_non_null_owner() -> None:
    """The hazard stays latent only while the owner cannot be None.

    What makes that true has changed once already, so this pins the property
    and not the spelling. It used to be `int(...)` at the bind site, where
    `int(None)` raises. The guest cart replaced that with a named owner from
    `_cart_owner` plus an explicit refusal above the INSERT -- a cleaner answer,
    because it 503s instead of surfacing a `TypeError` as a 500, and because it
    gives a guest a real allocated id rather than letting `user_id` stay NULL.

    Either shape is acceptable here. What is not acceptable is binding something
    nullable with nothing between it and the INSERT. If this assertion fails,
    read `test_a_null_owner_defeats_the_cart_dedupe` above before deciding it is
    the assertion that is wrong.
    """
    owner, handler = _cart_insert_owner_argument()

    if isinstance(owner, ast.Call):
        assert isinstance(owner.func, ast.Name) and owner.func.id == "int", (
            "the cart INSERT binds "
            f"`{ast.unparse(owner)}` as the row's owner rather than an "
            "`int(...)`. Anything that can evaluate to None reintroduces the "
            "duplicate-line bug for that population."
        )
        return

    assert isinstance(owner, ast.Name), (
        f"the cart INSERT binds `{ast.unparse(owner)}` as the row's owner. That "
        "is neither an `int(...)` nor a local this test can trace a guard to, so "
        "nothing here can show it is non-NULL -- and a NULL owner silently "
        "defeats the unique index. See this file's other test."
    )

    # The owner is a local. It is only safe if the handler refuses when it is
    # empty, so find `if not <owner>: ... return ...` ahead of the INSERT.
    guarded = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == owner.id
        and any(isinstance(stmt, ast.Return) for stmt in ast.walk(node))
        for node in ast.walk(handler)
    )
    assert guarded, (
        f"the cart INSERT binds the local `{owner.id}`, but `{handler.name}` "
        f"never refuses on a falsy `{owner.id}`. Without that guard an owner "
        "that comes back empty is written as NULL, and a NULL owner silently "
        "defeats the three-column unique index -- the insert stops conflicting, "
        "the double-tap becomes two lines, and the MAX_QTY_PER_LINE clamp goes "
        "with it. Restore the refusal, or bind a value that cannot be empty."
    )
