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

Today that is unreachable, which is the only reason it is not a live bug: every
write site in `services/marketplace_cart_routes.py` binds `int(user["user_id"])`,
and `int(None)` raises. A cart belongs to an account or it does not exist.

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
import sqlite3
import sys
from pathlib import Path

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


def _cart_db() -> sqlite3.Connection:
    """A cart table built from the module's own DDL, not a copy of it.

    Using `CART_TABLE_DDL` and `CART_UNIQUE_INDEX_DDL` verbatim means this test
    cannot pass against a hand-written table that has drifted from the real one:
    if the shipped shape loses its unique index, or gains `NOT NULL` on the
    owner, that shows up here as a behaviour change rather than being papered
    over by a local fixture.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(cart_schema.CART_TABLE_DDL)
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


def test_a_real_owner_is_deduplicated_into_a_quantity() -> None:
    """The control. Without this the NULL case below proves nothing."""
    conn = _cart_db()
    _add(conn, 1)
    _add(conn, 1)

    lines = _lines(conn)
    assert lines == [(1, 2)], (
        "two adds of the same listing+variant by the same signed-in buyer should "
        f"collapse to one line at quantity 2, got {lines!r}. If this fails the "
        "cart's double-tap protection is broken for *every* buyer, not just the "
        "NULL-owner case the rest of this file is about -- check that "
        f"{cart_schema.CART_UNIQUE_INDEX} still covers "
        f"{cart_schema.CONFLICT_COLUMNS}."
    )


def test_a_null_owner_defeats_the_cart_dedupe() -> None:
    """The finding: the same two adds do not collapse when the owner is NULL."""
    conn = _cart_db()
    _add(conn, None)
    _add(conn, None)

    lines = _lines(conn)
    assert lines == [(None, 1), (None, 1)], (
        "expected a NULL owner to produce two separate cart lines, got "
        f"{lines!r}. If this now collapses to one line, the schema gained "
        "something that makes NULLs comparable (a NOT NULL owner, or "
        "`UNIQUE NULLS NOT DISTINCT`) -- which would be the fix, and this test "
        "should be replaced by one asserting the new guarantee rather than "
        "deleted."
    )
    assert len(lines) == 2, "two lines is the bug being pinned"


def _cart_insert_owner_argument() -> ast.expr:
    """The expression bound to `user_id` by the cart's INSERT, from the AST.

    Read off the syntax tree because the thing that matters is the *argument
    that is bound*, and a text search would equally match the column list, the
    `ON CONFLICT` target, the surrounding comments, or any of the dozen other
    `int(user["user_id"])` occurrences in this module that belong to reads and
    deletes rather than to the insert.
    """
    tree = ast.parse(CART_ROUTES.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
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
            "the cart INSERT's parameters are no longer a literal tuple, so the "
            "owner argument cannot be read positionally. Re-point this check at "
            "however they are passed now."
        )
        return params.elts[0]
    raise AssertionError(
        "found no `INSERT INTO marketplace_cart_items` call with bound "
        f"parameters in {CART_ROUTES.name}. The upsert moved; this guard is "
        "looking in the wrong place and is currently protecting nothing."
    )


def test_the_cart_insert_still_binds_a_non_null_owner() -> None:
    """The hazard stays latent only while the owner cannot be None.

    `int(...)` is what makes that true: `int(None)` raises `TypeError`, so a
    missing owner fails the request loudly instead of writing a row the unique
    index will ignore. If this assertion fails, read
    `test_a_null_owner_defeats_the_cart_dedupe` above before deciding it is the
    assertion that is wrong.
    """
    owner = _cart_insert_owner_argument()

    assert isinstance(owner, ast.Call), (
        "the cart INSERT binds "
        f"`{ast.unparse(owner)}` as the row's owner, which is no longer a call. "
        "A bare subscript or `.get()` can be None, and a NULL owner silently "
        "defeats the unique index -- see this file's other test."
    )
    assert isinstance(owner.func, ast.Name) and owner.func.id == "int", (
        "the cart INSERT binds "
        f"`{ast.unparse(owner)}` as the row's owner rather than an `int(...)`. "
        "Anything that can evaluate to None reintroduces the duplicate-line bug "
        "for that population. If this is the guest-cart work landing, the owner "
        "should be a non-null key (`owner_key`) and the unique index should move "
        "with it -- not a nullable `user_id`."
    )
