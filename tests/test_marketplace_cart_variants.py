"""A cart line that can name which size the buyer chose.

## What was wrong

`marketplace_cart_items` was listing-grained. Its unique key was
`UNIQUE(user_id, listing_id)` and it had no column for a variant, so there was
nowhere to record "size M in Snowflake Blue" -- only "this listing".

That single fact propagated all the way to the storefront. `POST /cart` read a
`listing_id` and a `qty` and nothing else, so a one-tap add on a shirt sold in
four sizes did not fail: it *succeeded*, writing a line naming no size, priced
from the listing's `price_label` rather than from the variant nobody picked.
`marketplace_web.requires_variant_choice` therefore withheld the Add to cart
button from every multi-variant listing -- asking about the *listing* ("does this
have options?") because it had no way to ask about the *buyer* ("have they
answered them?").

The visible symptom was the end of a journey the storefront itself invited
people on. The grid swapped the button for a "Choose options" link to the product
page; the product page rendered the picker, resolved the buyer's selection down
to one variant at one firm price, showed "In stock" -- and offered no way to buy
it. On listing 1 in the production catalogue: pick Snowflake Blue, pick M, get
$95.33 and no button.

Every status census read this as healthy. Both pages answered 200, the picker
worked, `cart_affordance` was returning exactly what it had been asked for.

## What this file pins

Four things, in the order they have to be true:

1. **The schema.** The column exists, the key is three columns wide, and a
   database that predates the column is *migrated* rather than merely tolerated
   -- including the part no other schema helper in this repo does, retiring an
   existing unique constraint.
2. **The add.** A variant id is accepted, validated against the listing, priced
   from the variant's own row, and refused when it is missing but required.
3. **The read.** The line comes back naming what was chosen, does not read
   `price_changed` forever, and reports the states that mean "the thing you chose
   is gone" and "the thing you chose sold out".
4. **The order.** What the buyer chose survives onto `seller_transactions`, which
   is what the seller packing the parcel reads.

## Why a file of its own

The schema migration and the upsert need a real database. `tests/test_storefront_add_to_cart.py`
boots no Flask app and runs in 0.2s, which is why the *contract* assertions live
there and the *effect* assertions live here. This file imports `bot`, so it binds
`DATABASE_URL` at module scope and must run in its own pytest process.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing `bot` connects and runs init_db() at module scope, so the env has to
# be bound before the import rather than in a fixture.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "cart-variant-tests")
# The card rail is an env flag. Without it the checkout section below would be
# asserting the payment pause rather than the metadata it names.
os.environ.setdefault("MARKETPLACE_CARD_PAYMENTS_ENABLED", "1")

import bot  # noqa: E402
from services import db as db_service  # noqa: E402
from services import marketplace_cart_schema as cart_schema  # noqa: E402

# enforce_https 301s anything that does not look like it arrived over TLS.
HTTPS = {"X-Forwarded-Proto": "https"}

CART_API = "/api/pulse/marketplace/cart"
CHECKOUT_API = CART_API + "/checkout"
STUB_SESSION_URL = "https://checkout.stripe.test/c/pay/stub-session"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    bot.webhook_app.config["SECRET_KEY"] = "cart-variant-tests"
    return bot.webhook_app


def _insert_user(cur, handle):
    cur.execute(
        "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
        (handle, f"{handle}@example.com", handle.title()),
    )
    return int(cur.lastrowid)


def _insert_listing(cur, seller_id, *, title="Linen Shirt", price_label="$24.00",
                    quantity=10, product_type="digital"):
    """A live, approved listing.

    `product_type` defaults to `digital` and that default is load-bearing, not
    incidental. `marketplace_listing_lifecycle.STOCKLESS_TYPES` holds `digital`,
    so a digital listing's `quantity` is *ignored* -- which is what keeps the
    checkout section below on the one lane that needs no shipping address. Any
    test that means to say something about the listing's own stock has to pass
    `product_type="physical"` or it is asserting against a column nothing reads.
    """
    cur.execute(
        """INSERT INTO marketplace_listings
           (seller_user_id, title, description, category, price_label, currency,
            quantity, status, approval_status, delivery_type, product_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'approved', 'digital', ?)""",
        (seller_id, title, "A shirt, for testing.", "Education", price_label,
         "USD", quantity, product_type),
    )
    return int(cur.lastrowid)


def _insert_variant(cur, listing_id, seller_id, *, key, options, price_cents=2400,
                    stock_state="IN_STOCK", stock_quantity=None, status="active",
                    sku=""):
    """A row exactly as `services/marketplace_variants.py` writes one."""
    cur.execute(
        """INSERT INTO marketplace_listing_variants
           (listing_id, seller_user_id, variant_key, options_json, sku,
            price_cents, currency, stock_quantity, stock_state, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (listing_id, seller_id, key,
         json.dumps([{"name": name, "value": value} for name, value in options]),
         sku, price_cents, "USD", stock_quantity, stock_state, status),
    )
    return int(cur.lastrowid)


@pytest.fixture(scope="module")
def world(app):
    """One approved seller, two listings, and a shirt sold in two sizes.

    Module-scoped because `init_db()` and the seller approval are the expensive
    part. Every test that mutates a variant row restores it, and `empty_cart`
    below is what keeps the basket from leaking between tests.
    """
    with app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        seller = _insert_user(cur, "variantseller")
        buyer = _insert_user(cur, "variantbuyer")
        # `approved` on both the seller and the listing: `_line_state` reports
        # `restricted` for anything less, and every assertion below about a
        # usable line would be testing the refusal path instead.
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, status, business_name, display_name) "
            "VALUES (?, 'approved', ?, ?)",
            (seller, "Probe Store", "Probe Store"),
        )
        shirt = _insert_listing(cur, seller, title="Linen Shirt", price_label="$24.00")
        medium = _insert_variant(cur, shirt, seller, key="size-m",
                                 options=[("Size", "M")], price_cents=2400)
        large = _insert_variant(cur, shirt, seller, key="size-l",
                                options=[("Size", "L")], price_cents=2900)
        # A second listing with nothing to choose, which is the negative control
        # for every "needs a choice" assertion below.
        plain = _insert_listing(cur, seller, title="Plain Mug", price_label="$9.00")
        conn.commit()

    client = app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = buyer

    return {
        "client": client, "buyer": buyer, "seller": seller,
        "shirt": shirt, "medium": medium, "large": large, "plain": plain,
    }


def empty_cart(client):
    """Leave the buyer's cart empty, and prove it.

    `world` is module-scoped so the basket carries over. Asserting a quantity
    without this reads as a bug in the quantity rather than as one test seeing
    another's cart.
    """
    for entry in client.get(CART_API, headers=HTTPS).get_json()["lines"]:
        client.delete(f"{CART_API}/{entry['line_id']}", headers=HTTPS)
    assert client.get(CART_API, headers=HTTPS).get_json()["lines"] == []


@pytest.fixture
def cart(world):
    empty_cart(world["client"])
    return world


def add(world, **body):
    return world["client"].post(CART_API, json=body, headers=HTTPS)


def lines_of(world):
    return world["client"].get(CART_API, headers=HTTPS).get_json()["lines"]


@contextlib.contextmanager
def variant_patched(app, variant_id, **columns):
    """Change a variant row for one test and put it back.

    Restores from the values read before the write rather than from the fixture's
    literals, so a test that patches a column the fixture never set still leaves
    the row as it found it.
    """
    assignments = ", ".join(f"{name}=?" for name in columns)
    with app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            f"SELECT {', '.join(columns)} FROM marketplace_listing_variants WHERE id=?",
            (variant_id,),
        )
        # Through `row_values`, not by iterating the row: a DB row yields its
        # *values* on SQLite and its *column names* on PostgreSQL, so `list(row)`
        # would restore the string "price_cents" into the price column there.
        row = cur.fetchone()
        assert row is not None, f"no variant {variant_id} to patch"
        before = list(db_service.row_values(row))
        cur.execute(
            f"UPDATE marketplace_listing_variants SET {assignments} WHERE id=?",
            (*columns.values(), variant_id),
        )
        conn.commit()
    try:
        yield
    finally:
        with app.app_context():
            conn = bot.db()
            cur = conn.cursor()
            cur.execute(
                f"UPDATE marketplace_listing_variants SET {assignments} WHERE id=?",
                (*before, variant_id),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# 1. The schema: a column, a three-column key, and a legacy table migrated
#
# Exercised against a scratch SQLite file rather than the application database,
# because the interesting case is a table that *predates* the column and the app
# database was migrated at import. A raw `sqlite3` cursor is enough:
# `ensure_cart_schema` introspects through `services.db.get_table_columns`, which
# accepts any cursor and picks its dialect from `DATABASE_URL`.
# ---------------------------------------------------------------------------

#: The table exactly as it was before this mission, reproduced rather than
#: referenced. A constant in the schema module would be a copy that moved with
#: the fix and stopped representing what is actually in production.
LEGACY_DDL = """
CREATE TABLE marketplace_cart_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    listing_id INTEGER,
    qty INTEGER DEFAULT 1,
    price_snapshot_minor INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    added_at TEXT,
    updated_at TEXT,
    UNIQUE(user_id, listing_id)
)
"""


@contextlib.contextmanager
def scratch_db(ddl=None):
    handle, path = tempfile.mkstemp(suffix=".db", prefix="cart_schema_")
    os.close(handle)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        if ddl:
            conn.execute(ddl)
            conn.commit()
        yield conn
    finally:
        conn.close()
        os.unlink(path)


def unique_indexes(conn):
    """Every unique index on the cart table, as sets of column names.

    Read from `PRAGMA index_list`/`index_info` rather than from the DDL text,
    because an index created by `CREATE UNIQUE INDEX` does not appear in the
    `CREATE TABLE` string at all -- and the replacement key is an index.
    """
    found = []
    for row in conn.execute("PRAGMA index_list(marketplace_cart_items)"):
        row = dict(row)
        if not int(row["unique"]):
            continue
        columns = {dict(c)["name"] for c in
                   conn.execute(f"PRAGMA index_info({row['name']})")}
        found.append(columns)
    return found


def test_a_fresh_database_gets_the_variant_column_and_the_three_column_key():
    with scratch_db() as conn:
        result = cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()
        assert result["status"] == cart_schema.STATUS_READY, result
        assert result["table_created"] is True
        columns = {dict(r)["name"] for r in
                   conn.execute("PRAGMA table_info(marketplace_cart_items)")}
        assert "variant_id" in columns
        assert {"user_id", "listing_id", "variant_id"} in unique_indexes(conn)


def test_a_fresh_database_has_no_two_column_unique_key_left_anywhere():
    """The negative half. A table carrying both keys would accept the M line and
    then refuse the L line, which is the original bug with an extra index."""
    with scratch_db() as conn:
        cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()
        assert {"user_id", "listing_id"} not in unique_indexes(conn)


def test_a_legacy_table_is_migrated_rather_than_left_alone():
    """The case that made this a schema module instead of two lines in `init_db`.

    There is no migration framework here, so a table that already exists is
    whatever shape it was created in -- and editing the `CREATE TABLE` reaches
    only fresh databases. Production has the legacy shape.
    """
    with scratch_db(LEGACY_DDL) as conn:
        result = cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()
        assert result["status"] == cart_schema.STATUS_READY, result
        assert result["legacy_dropped"], "the two-column key was never retired"
        columns = {dict(r)["name"] for r in
                   conn.execute("PRAGMA table_info(marketplace_cart_items)")}
        assert "variant_id" in columns
        keys = unique_indexes(conn)
        assert {"user_id", "listing_id", "variant_id"} in keys
        assert {"user_id", "listing_id"} not in keys, (
            "the legacy key survived, so a second size is still refused")


def test_migrating_a_legacy_table_keeps_the_rows_and_their_line_ids():
    """A cart is not scratch space -- these are baskets people are holding.

    The row ids matter specifically. `cart_update`, `cart_remove` and the
    checkout's cart clear all address lines by `id`, so a rebuild that renumbered
    them would invalidate every line id a client is currently holding: the buyer's
    next "remove" would 404, or worse, hit somebody else's line.
    """
    with scratch_db(LEGACY_DDL) as conn:
        conn.execute(
            "INSERT INTO marketplace_cart_items "
            "(id, user_id, listing_id, qty, price_snapshot_minor, currency) "
            "VALUES (41, 7, 900, 3, 2400, 'USD')")
        conn.execute(
            "INSERT INTO marketplace_cart_items "
            "(id, user_id, listing_id, qty, price_snapshot_minor, currency) "
            "VALUES (77, 7, 901, 1, 900, 'USD')")
        conn.commit()

        cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()

        rows = {int(dict(r)["id"]): dict(r) for r in conn.execute(
            "SELECT * FROM marketplace_cart_items ORDER BY id")}
        assert sorted(rows) == [41, 77], "line ids were renumbered by the rebuild"
        assert rows[41]["qty"] == 3
        assert rows[41]["listing_id"] == 900
        assert rows[41]["price_snapshot_minor"] == 2400
        assert rows[77]["qty"] == 1


def test_a_migrated_row_gets_zero_and_not_null_for_its_variant():
    """`0`, because NULLs are distinct in a unique index on both engines.

    A nullable `variant_id` would let the same listing be added to the same cart
    without limit -- the exact duplicate-line bug `UNIQUE(user_id, listing_id)`
    existed to prevent, reintroduced by the change meant to preserve it.

    *Which* mechanism delivers the `0` is deliberately not asserted, because two
    do: `ADD COLUMN variant_id INTEGER DEFAULT 0` already fills every existing row
    on both SQLite and PostgreSQL 11+, and the `UPDATE ... WHERE variant_id IS
    NULL` that follows it is a belt for a column that somehow arrived without the
    default. This pins the outcome the cart's uniqueness depends on, which is the
    same either way -- so the `UPDATE` is not mutation-killable, and
    `scripts/protection/cart_variant_mutation_matrix.py` records that in its
    docstring rather than listing a control that would permanently survive.
    """
    with scratch_db(LEGACY_DDL) as conn:
        conn.execute("INSERT INTO marketplace_cart_items (user_id, listing_id, qty) "
                     "VALUES (7, 900, 1)")
        conn.commit()
        cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()
        held = [dict(r)["variant_id"] for r in
                conn.execute("SELECT variant_id FROM marketplace_cart_items")]
        assert held == [cart_schema.NO_VARIANT]
        assert held[0] is not None, "NULL would make the unique index enforce nothing"


def test_the_widened_key_really_refuses_a_duplicate_of_the_same_variant():
    """The replacement key asserted as behaviour, not as a PRAGMA reading.

    An index over the right three columns that was somehow not unique would pass
    every structural check above and silently let `ON CONFLICT` become an insert.
    """
    with scratch_db(LEGACY_DDL) as conn:
        cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()
        conn.execute("INSERT INTO marketplace_cart_items "
                     "(user_id, listing_id, variant_id, qty) VALUES (7, 900, 5, 1)")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO marketplace_cart_items "
                         "(user_id, listing_id, variant_id, qty) VALUES (7, 900, 5, 1)")
        # And the positive half in the same breath: a *different* variant of the
        # same listing is a new line, which is the entire point.
        conn.execute("INSERT INTO marketplace_cart_items "
                     "(user_id, listing_id, variant_id, qty) VALUES (7, 900, 6, 1)")
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM marketplace_cart_items").fetchone()[0] == 2


def test_the_ensure_is_idempotent_and_does_not_rebuild_a_table_it_already_fixed():
    """Called on every request through `_ensure_schema`, so a rebuild per call
    would be a table drop per request. The guard is `sqlite_has_legacy_unique`
    matching the two-column form *only*; a looser match would rebuild forever.
    """
    with scratch_db(LEGACY_DDL) as conn:
        first = cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()
        conn.execute("INSERT INTO marketplace_cart_items "
                     "(id, user_id, listing_id, variant_id, qty) VALUES (5, 7, 900, 3, 2)")
        conn.commit()

        second = cart_schema.ensure_cart_schema(conn.cursor(), force=True)
        conn.commit()

        assert first["rebuilt"] is True, "the legacy table should have been rebuilt once"
        assert not second.get("rebuilt"), "the second call rebuilt an already-fixed table"
        assert second["status"] == cart_schema.STATUS_READY
        rows = [dict(r) for r in conn.execute("SELECT * FROM marketplace_cart_items")]
        assert len(rows) == 1 and int(rows[0]["id"]) == 5, rows


def test_the_legacy_detector_reads_the_two_column_form_and_not_the_three():
    """The unit behind the test above, pinned directly because the consequence of
    getting it wrong is invisible in a passing suite: a detector that matched the
    three-column form would drop and recreate the cart table on every boot."""
    assert cart_schema.sqlite_has_legacy_unique(LEGACY_DDL) is True
    assert cart_schema.sqlite_has_legacy_unique(cart_schema.CART_TABLE_DDL) is False
    # Spelling variations of the same legacy key, because a seller's database was
    # created by whichever `CREATE TABLE` text shipped that month.
    for spelling in ('UNIQUE(listing_id, user_id)',
                     'unique ( "user_id" , "listing_id" )',
                     'UNIQUE\n  (user_id,\n   listing_id)'):
        assert cart_schema.sqlite_has_legacy_unique(spelling) is True, spelling
    assert cart_schema.sqlite_has_legacy_unique("") is False
    assert cart_schema.sqlite_has_legacy_unique("UNIQUE(user_id)") is False


# ---------------------------------------------------------------------------
# 2. The add: a choice accepted, validated, and priced from the row it names
# ---------------------------------------------------------------------------

def test_two_sizes_of_one_shirt_are_two_lines(cart):
    """The assertion the whole mission chain terminates in.

    Under `UNIQUE(user_id, listing_id)` the second add was an `ON CONFLICT`
    update: the buyer asked for a Medium and a Large and got two of whichever
    they asked for first.
    """
    assert add(cart, listing_id=cart["shirt"], variant_id=cart["medium"]).status_code == 200
    assert add(cart, listing_id=cart["shirt"], variant_id=cart["large"]).status_code == 200

    lines = lines_of(cart)
    assert len(lines) == 2, lines
    assert {l["variant_id"] for l in lines} == {cart["medium"], cart["large"]}
    assert [l["qty"] for l in lines] == [1, 1], (
        "the second size was merged into the first as a quantity")


def test_the_same_size_twice_is_one_line_of_two(cart):
    """The half of the old key that had to survive. A double tap must not become
    two lines, or the widened key has traded one bug for the other."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    lines = lines_of(cart)
    assert len(lines) == 1, lines
    assert lines[0]["qty"] == 2


def test_a_listing_with_nothing_to_choose_still_adds_with_no_variant(cart):
    """The common case in this catalogue, and the negative control for every
    refusal below: one price, one thing, no picker, still one tap."""
    assert add(cart, listing_id=cart["plain"]).status_code == 200
    lines = lines_of(cart)
    assert len(lines) == 1
    assert lines[0]["variant_id"] == cart_schema.NO_VARIANT
    assert lines[0]["variant_label"] == ""
    assert lines[0]["variant_options"] == []


def test_an_add_that_names_no_variant_on_a_sized_listing_is_refused(cart):
    """The refusal that makes opening the client-side gate safe.

    A stale cached page, an older app build or a script posts a bare
    `listing_id`. Before this existed the route booked a line naming no size,
    priced from the listing -- which is why the button had to be withheld in the
    renderer, where it could only ever decline.
    """
    answer = add(cart, listing_id=cart["shirt"])
    assert answer.status_code == 400, answer.get_data(as_text=True)
    assert answer.get_json()["error_code"] == "VARIANT_REQUIRED"
    assert lines_of(cart) == [], "the refused add still wrote a line"


def test_a_variant_belonging_to_another_listing_is_refused(cart):
    """Scoped by listing, not just by id. An unscoped lookup would let a
    `variant_id` from a cheap listing attach its price to an expensive one --
    price manipulation through a field the client controls.
    """
    with bot.webhook_app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        other = _insert_listing(cur, cart["seller"], title="Other Thing")
        stranger = _insert_variant(cur, other, cart["seller"], key="x",
                                   options=[("Size", "M")], price_cents=1)
        conn.commit()

    answer = add(cart, listing_id=cart["shirt"], variant_id=stranger)
    assert answer.status_code == 409, answer.get_data(as_text=True)
    assert answer.get_json()["error_code"] == "VARIANT_UNAVAILABLE"
    assert lines_of(cart) == []


def test_a_retired_variant_is_refused_as_a_stale_choice_not_a_dead_listing(app, cart):
    """`VARIANT_UNAVAILABLE`, not `ITEM_UNAVAILABLE`. The item is fine; the choice
    is stale, and the buyer should be told to pick again rather than that the
    product is gone -- which is a different sentence and a different next step."""
    with variant_patched(app, cart["medium"], status="archived"):
        answer = add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    assert answer.status_code == 409, answer.get_data(as_text=True)
    assert answer.get_json()["error_code"] == "VARIANT_UNAVAILABLE"


def test_a_sold_out_variant_is_refused_even_though_the_listing_has_stock(app, cart):
    """The listing says ten in stock; this size says none. Reading only the
    listing is how a buyer gets shown "In stock" and refused at the till."""
    with variant_patched(app, cart["medium"], stock_state="OUT_OF_STOCK"):
        answer = add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    assert answer.status_code == 409, answer.get_data(as_text=True)
    assert answer.get_json()["error_code"] == "OUT_OF_STOCK"


def test_a_variant_whose_stock_is_unknown_is_still_buyable(app, cart):
    """`UNKNOWN` is not `OUT_OF_STOCK`, and the difference is the point of the
    third value: a supplier that has not answered yet must not read as a
    sell-out, or every sync outage empties the shop."""
    with variant_patched(app, cart["medium"], stock_state="UNKNOWN",
                         stock_quantity=None):
        assert add(cart, listing_id=cart["shirt"],
                   variant_id=cart["medium"]).status_code == 200


@pytest.mark.parametrize("junk", ["", "abc", None, {}, [], "3.5", -1, "-2"])
def test_an_unusable_variant_id_is_a_bad_request_and_not_a_500(cart, junk):
    """Coerced outside the transaction, so garbage is a sentence about the request
    rather than a `ValueError` raised inside the handler and served as a 500.

    `""`, `None` and `0` are "no variant named" and reach the ordinary
    needs-a-choice refusal on this listing; the rest are malformed. Both answers
    are 400s that name a variant problem, which is what the client acts on.
    """
    answer = add(cart, listing_id=cart["shirt"], variant_id=junk)
    assert answer.status_code == 400, (junk, answer.get_data(as_text=True))
    assert answer.get_json()["error_code"] == "VARIANT_REQUIRED", junk


def test_the_snapshot_is_the_variants_price_and_not_the_listings(cart):
    """`price_label` is a string a human typed against the listing as a whole. For
    a shirt sold at two prices it is at best one of them, so a line naming the
    Large has to be priced from the Large's own row.
    """
    add(cart, listing_id=cart["shirt"], variant_id=cart["large"])
    line = lines_of(cart)[0]
    assert line["price_snapshot_minor"] == 2900, line
    # And not the listing's $24.00, which is what the old path would have stored.
    assert line["price_snapshot_minor"] != 2400


def test_the_two_sizes_are_held_at_their_own_prices_at_once(cart):
    """The negative control for the test above, which passes just as happily
    against a route that prices every line from the *first* variant it finds."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    add(cart, listing_id=cart["shirt"], variant_id=cart["large"])
    held = {l["variant_id"]: l["price_snapshot_minor"] for l in lines_of(cart)}
    assert held == {cart["medium"]: 2400, cart["large"]: 2900}


# ---------------------------------------------------------------------------
# 3. The read: what was chosen, and what it means when it changes
# ---------------------------------------------------------------------------

def test_the_line_names_the_chosen_combination_the_way_the_seller_wrote_it(cart):
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    line = lines_of(cart)[0]
    assert line["variant_id"] == cart["medium"]
    assert line["variant_key"] == "size-m"
    assert line["variant_options"] == [{"name": "Size", "value": "M"}]
    assert line["variant_label"] == "Size: M"


def test_a_variant_line_is_not_permanently_price_changed(cart):
    """The bug two pricing expressions would have produced, pinned as the state.

    If the snapshot came from the variant and the "price now" from the listing,
    every variant line would read `price_changed` on the very next read -- the
    cart demanding confirmation of a change that never happened, forever, with
    checkout blocked behind it. One function (`_line_price_minor`) produces both,
    which is what makes this `available`.
    """
    add(cart, listing_id=cart["shirt"], variant_id=cart["large"])
    line = lines_of(cart)[0]
    assert line["state"] == "available", line
    assert line["price_now_minor"] == line["price_snapshot_minor"] == 2900


def test_a_repriced_variant_does_report_price_changed(cart, app):
    """The anti-vacuity pair for the test above. A `_line_state` that had lost the
    price comparison entirely would pass that test and this is what catches it."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["large"])
    with variant_patched(app, cart["large"], price_cents=3100):
        line = lines_of(cart)[0]
        assert line["state"] == "price_changed", line
        assert line["price_now_minor"] == 3100
        assert line["price_snapshot_minor"] == 2900


def test_retiring_the_chosen_variant_makes_the_line_removed(cart, app):
    """The listing survives, so this is not `restricted` (which is about who may
    sell) and not `sold` (which is about stock). The specific thing the buyer
    chose has ceased to exist, and `removed` is the state that says so."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    with variant_patched(app, cart["medium"], status="archived"):
        line = lines_of(cart)[0]
        assert line["state"] == "removed", line
        # The listing is still there, which is what makes `removed` about the
        # variant rather than about the product.
        assert line["title"] == "Linen Shirt"


def test_a_variant_that_sells_out_makes_the_line_sold(cart, app):
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    with variant_patched(app, cart["medium"], stock_state="OUT_OF_STOCK"):
        assert lines_of(cart)[0]["state"] == "sold"


def test_the_listings_own_stock_still_decides_when_the_variant_has_plenty(cart):
    """Additive, never a replacement. Checkout reserves against and decrements
    the *listing* quantity, so a variant with stock must not be buyable out of a
    listing without any -- which is what dropping the listing test would allow.

    On a `physical` listing specifically, because the rest of this file uses
    `digital` ones and `digital` is in `STOCKLESS_TYPES`: a digital listing's
    `quantity` is never read, so setting it to zero there proves nothing. This
    test found that out the hard way -- it reported `low_stock` rather than `sold`
    on the shared fixture, which was the fixture being stockless and not the
    state machine being wrong.
    """
    with bot.webhook_app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        listing = _insert_listing(cur, cart["seller"], title="Boxed Shirt",
                                  quantity=0, product_type="physical")
        sized = _insert_variant(cur, listing, cart["seller"], key="phys-m",
                                options=[("Size", "M")], stock_state="IN_STOCK",
                                stock_quantity=50)
        conn.commit()

    # The listing has none and the variant has fifty. The add is refused on the
    # listing's emptiness before the variant is ever consulted.
    answer = add(cart, listing_id=listing, variant_id=sized)
    assert answer.status_code == 409, answer.get_data(as_text=True)
    assert answer.get_json()["error_code"] == "OUT_OF_STOCK"

    # And a line already held reads `sold` for the same reason. Written directly
    # because the route above correctly refuses to create it.
    with bot.webhook_app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_cart_items "
            "(user_id, listing_id, variant_id, qty, price_snapshot_minor, currency, added_at) "
            "VALUES (?, ?, ?, 1, 2400, 'USD', '2026-01-01T00:00:00')",
            (cart["buyer"], listing, sized),
        )
        conn.commit()
    line = next(l for l in lines_of(cart) if l["listing_id"] == listing)
    assert line["state"] == "sold", line


def test_a_variant_running_low_warns_rather_than_blocks(cart, app):
    """`low_stock` sits below `price_changed` on purpose: a state that blocks
    checkout is reported ahead of one that only warns, so a line that is both
    re-priced and running low is reported as the thing the buyer must act on."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"], qty=3)
    with variant_patched(app, cart["medium"], stock_quantity=2):
        assert lines_of(cart)[0]["state"] == "low_stock"
    with variant_patched(app, cart["medium"], stock_quantity=2, price_cents=3300):
        assert lines_of(cart)[0]["state"] == "price_changed", (
            "a blocking state must outrank the warning")


def test_a_low_variant_line_still_counts_toward_the_badge(cart, app):
    """`low_stock` is purchasable after one confirmation, so the badge counts it.
    A badge that dropped it would understate a cart the buyer can still pay for.
    """
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"], qty=3)
    with variant_patched(app, cart["medium"], stock_quantity=2):
        payload = cart["client"].get(CART_API, headers=HTTPS).get_json()
        assert payload["lines"][0]["state"] == "low_stock"
        assert payload["badge_count"] == 3


def test_a_removed_variant_line_is_not_counted(cart, app):
    """The other side of the badge's promise: a line the buyer cannot buy must
    not be counted, or the number is an overstatement they discover at checkout.
    """
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    with variant_patched(app, cart["medium"], status="archived"):
        payload = cart["client"].get(CART_API, headers=HTTPS).get_json()
        assert payload["lines"][0]["state"] == "removed"
        assert payload["badge_count"] == 0


def test_the_two_size_lines_are_addressable_separately(cart):
    """Two lines are only useful if the buyer can act on one of them. `DELETE` by
    line id must take the Large and leave the Medium -- which is the reason the
    SQLite rebuild preserves row ids."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    add(cart, listing_id=cart["shirt"], variant_id=cart["large"])
    lines = lines_of(cart)
    large = next(l for l in lines if l["variant_id"] == cart["large"])
    assert cart["client"].delete(f"{CART_API}/{large['line_id']}",
                                 headers=HTTPS).status_code == 200
    left = lines_of(cart)
    assert [l["variant_id"] for l in left] == [cart["medium"]]


def test_a_multi_option_variant_reads_as_one_label(cart):
    """`"Color: Snowflake Blue · Size: M"` -- what the seller packing the parcel
    reads, in the seller's own words and in the seller's own order."""
    with bot.webhook_app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        listing = _insert_listing(cur, cart["seller"], title="Two Axis Shirt")
        combo = _insert_variant(cur, listing, cart["seller"], key="blue-m",
                                options=[("Color", "Snowflake Blue"), ("Size", "M")])
        other = _insert_variant(cur, listing, cart["seller"], key="blue-l",
                                options=[("Color", "Snowflake Blue"), ("Size", "L")])
        conn.commit()
    add(cart, listing_id=listing, variant_id=combo)
    line = lines_of(cart)[0]
    assert line["variant_label"] == "Color: Snowflake Blue · Size: M"
    assert line["variant_options"] == [
        {"name": "Color", "value": "Snowflake Blue"},
        {"name": "Size", "value": "M"},
    ]


# ---------------------------------------------------------------------------
# 4. The order: the choice survives onto the transaction
#
# The point of the whole chain. A cart that records the size and an order that
# does not is a seller reading "Linen Shirt x1" off a packing slip and guessing.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def card_rail_open():
    """No Stripe in a test process, and none needed.

    `marketplace_card_capability.evaluate` answers STRIPE_UNAVAILABLE without a
    secret key and the lane returns 503 *before* it writes a transaction -- so
    without this, the assertions below would be assertions about a missing API
    key. Deliberately narrow: the per-seller verdict is a real guard with its own
    tests, and `session` is a dict this file wrote. No key, no account, no card,
    no charge.
    """
    from services import marketplace_card_capability

    created = []

    class _Sessions:
        @staticmethod
        def create(**kwargs):
            created.append(kwargs)
            return {"id": "cs_test_stub", "url": STUB_SESSION_URL}

    class _Checkout:
        Session = _Sessions

    real_evaluate = marketplace_card_capability.evaluate
    real_key = bot.STRIPE_SECRET_KEY
    real_checkout = bot.stripe.checkout
    marketplace_card_capability.evaluate = lambda *a, **k: {
        "card_payments_available": True, "reason_code": "", "message": "", "badge": ""}
    bot.STRIPE_SECRET_KEY = "sk_test_stub_not_a_real_key"
    bot.stripe.checkout = _Checkout
    try:
        yield created
    finally:
        marketplace_card_capability.evaluate = real_evaluate
        bot.STRIPE_SECRET_KEY = real_key
        bot.stripe.checkout = real_checkout


def transaction_metadata(tx_ids):
    with bot.webhook_app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        out = []
        for tx in tx_ids:
            cur.execute("SELECT metadata_json FROM seller_transactions WHERE id=?", (tx,))
            row = dict(cur.fetchone() or {})
            out.append(json.loads(row.get("metadata_json") or "{}"))
        return out


def test_an_order_for_two_sizes_records_which_size_each_line_was(cart):
    """Both forms, because they answer different questions: the seller packing
    the parcel reads `variant_label`, and anything reconciling stock or
    re-ordering from a supplier needs the `variant_id`/`variant_key` that address
    the row.
    """
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    add(cart, listing_id=cart["shirt"], variant_id=cart["large"])

    with card_rail_open():
        answer = cart["client"].post(CHECKOUT_API, json={
            "seller_user_id": cart["seller"],
            "fulfillment_details": {},
            "idempotency_key": "cart-variant-order-1",
        }, headers=HTTPS)
    assert answer.status_code == 200, answer.get_data(as_text=True)
    tx_ids = answer.get_json()["transaction_ids"]
    assert len(tx_ids) == 2, tx_ids

    recorded = transaction_metadata(tx_ids)
    labels = {m.get("variant_label") for m in recorded}
    assert labels == {"Size: M", "Size: L"}, recorded
    assert {m.get("variant_id") for m in recorded} == {cart["medium"], cart["large"]}
    assert {m.get("variant_key") for m in recorded} == {"size-m", "size-l"}
    for entry in recorded:
        assert entry["variant_options"], entry


def test_an_order_for_a_plain_listing_carries_no_variant_fields_at_all(cart):
    """Omitted entirely rather than written as empty strings, so a variant-less
    order carries no field suggesting a choice was made and lost. A seller
    reading `variant_label: ""` cannot tell "no options" from "we lost it"."""
    add(cart, listing_id=cart["plain"])

    with card_rail_open():
        answer = cart["client"].post(CHECKOUT_API, json={
            "seller_user_id": cart["seller"],
            "fulfillment_details": {},
            "idempotency_key": "cart-variant-order-2",
        }, headers=HTTPS)
    assert answer.status_code == 200, answer.get_data(as_text=True)

    recorded = transaction_metadata(answer.get_json()["transaction_ids"])
    assert len(recorded) == 1
    for banned in ("variant_id", "variant_key", "variant_label", "variant_options"):
        assert banned not in recorded[0], (banned, recorded[0])
    # And the order is otherwise complete, or the test above passes vacuously
    # against a metadata builder that wrote nothing at all.
    assert recorded[0]["title"] == "Plain Mug"


def test_the_amount_charged_is_the_variants_price(cart):
    """The consequence that reaches the card statement. Two sizes at two prices
    must bill as the sum of those two prices, not twice the listing's."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    add(cart, listing_id=cart["shirt"], variant_id=cart["large"])

    with card_rail_open() as created:
        answer = cart["client"].post(CHECKOUT_API, json={
            "seller_user_id": cart["seller"],
            "fulfillment_details": {},
            "idempotency_key": "cart-variant-order-3",
        }, headers=HTTPS)
    assert answer.status_code == 200, answer.get_data(as_text=True)
    assert len(created) == 1
    amounts = sorted(item["price_data"]["unit_amount"]
                     for item in created[0]["line_items"])
    assert amounts == [2400, 2900], created[0]["line_items"]


def test_the_stripe_metadata_names_a_variant_per_line_positionally(cart):
    """`variant_ids` is a comma-joined list with `0` sentinels, so it stays the
    same length as its sibling lists and the *n*th entry is the *n*th line. A
    list that dropped the variant-less entries would silently re-align every
    field after it."""
    add(cart, listing_id=cart["shirt"], variant_id=cart["medium"])
    add(cart, listing_id=cart["plain"])

    with card_rail_open() as created:
        answer = cart["client"].post(CHECKOUT_API, json={
            "seller_user_id": cart["seller"],
            "fulfillment_details": {},
            "idempotency_key": "cart-variant-order-4",
        }, headers=HTTPS)
    assert answer.status_code == 200, answer.get_data(as_text=True)

    metadata = created[0]["metadata"]
    variant_ids = metadata["variant_ids"].split(",")
    listing_ids = metadata["listing_ids"].split(",")
    assert len(variant_ids) == len(listing_ids) == 2, metadata
    paired = dict(zip(listing_ids, variant_ids))
    assert paired[str(cart["shirt"])] == str(cart["medium"])
    assert paired[str(cart["plain"])] == str(cart_schema.NO_VARIANT)
