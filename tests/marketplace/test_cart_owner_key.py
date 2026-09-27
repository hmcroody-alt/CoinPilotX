"""A cart belongs to somebody, and "somebody" is a value rather than a NULL.

`marketplace_cart_items` was keyed `UNIQUE (user_id, listing_id)` with a nullable
`user_id`, and the add-to-cart path leans on that constraint by name:

    # A duplicate tap must not duplicate the line: the UNIQUE constraint
    # turns the second add into a quantity update.

Letting `user_id` stay NULL for a guest -- the obvious way to give a guest a cart
-- removes the constraint without removing the line of code that depends on it.
NULL is not equal to NULL inside a unique index, so `ON CONFLICT(user_id,
listing_id)` matches nothing and the second tap inserts a second line.

`test_two_null_owners_do_not_collide_which_is_the_whole_problem` is the first
test in this file because it is the premise everything else rests on, and because
it is the kind of premise that is usually asserted in a comment. If NULLs *did*
collide, `services/commerce_identity.py` would be unnecessary and every test
below would be passing for a reason unrelated to what it claims.

The other thing worth saying up front: this suite runs on SQLite and the bug is
not engine-specific. Both SQLite and PostgreSQL treat NULLs as distinct in a
unique index, which is unusual for this repo -- most constraints here behave
differently on the two engines, and the email-uniqueness suite needed a whole
`ProductionEngineCase` to say anything about production. Here a local test is
genuine evidence, and the first test states that agreement explicitly rather
than leaving it to be inferred from the file running green.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing `bot` connects and runs init_db() at module scope, so the env has to
# be bound before the import rather than in a fixture.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "cart-owner-key-tests")

import bot  # noqa: E402
from services import commerce_identity as identity  # noqa: E402
from services import db as db_service  # noqa: E402
from services import marketplace_cart_routes as cart  # noqa: E402

# enforce_https 301s anything that does not look like it arrived over TLS.
HTTPS = {"X-Forwarded-Proto": "https"}
CART_API = "/api/pulse/marketplace/cart"


# ---------------------------------------------------------------------------
# 1. The premise
# ---------------------------------------------------------------------------

def test_two_null_owners_do_not_collide_which_is_the_whole_problem():
    """A unique index does not constrain rows whose key contains NULL.

    Asserted against a bare SQLite table so there is nothing else in the frame:
    no routes, no `bot`, no fixtures. Two inserts of the same `listing_id` with a
    NULL `user_id` both succeed, and `ON CONFLICT` finds nothing to update.

    PostgreSQL 18.6 behaves identically -- it is the SQL standard's answer, not a
    dialect quirk -- which is what makes this file's verdict meaningful for
    production. PostgreSQL 15 did add `UNIQUE NULLS NOT DISTINCT`, and SQLite has
    no equivalent; reaching for it would put two dialects of one table into a
    repo with no migration framework, the same trap as writing `btrim` for
    `trim`.
    """
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, user_id INTEGER, listing_id INTEGER,"
        " qty INTEGER, UNIQUE(user_id, listing_id))"
    )
    for _ in range(2):
        cur.execute(
            "INSERT INTO t (user_id, listing_id, qty) VALUES (NULL, 5, 1) "
            "ON CONFLICT(user_id, listing_id) DO UPDATE SET qty = t.qty + 1"
        )

    rows = cur.execute("SELECT qty FROM t WHERE listing_id = 5").fetchall()
    assert len(rows) == 2, (
        "NULLs collided, so the constraint would have protected a guest cart and "
        "commerce_identity is unnecessary -- but then every other test in this "
        "file is passing for a reason it does not claim"
    )
    assert [r[0] for r in rows] == [1, 1], "the DO UPDATE never fired, as expected"

    # And the contrast, on the same table shape, with a non-null key.
    cur.execute(
        "CREATE TABLE u (id INTEGER PRIMARY KEY, owner_key TEXT, listing_id INTEGER,"
        " qty INTEGER, UNIQUE(owner_key, listing_id))"
    )
    for _ in range(2):
        cur.execute(
            "INSERT INTO u (owner_key, listing_id, qty) VALUES ('g:abc', 5, 1) "
            "ON CONFLICT(owner_key, listing_id) DO UPDATE SET qty = u.qty + 1"
        )
    assert cur.execute("SELECT qty FROM u WHERE listing_id = 5").fetchall() == [(2,)]
    conn.close()


# ---------------------------------------------------------------------------
# 2. The keys themselves
# ---------------------------------------------------------------------------

def test_an_account_key_names_the_account():
    assert identity.owner_key_for_user(7) == "u:7"
    assert identity.owner_key_for_user("7") == "u:7"
    assert identity.user_id_from_key("u:7") == 7
    assert identity.is_user_key("u:7")
    assert not identity.is_guest_key("u:7")


@pytest.mark.parametrize("bad", [None, 0, -1, "", "abc", "1; DROP TABLE users"])
def test_an_unresolvable_user_id_raises_instead_of_keying_a_shared_cart(bad):
    """The failure mode this prevents is one cart, shared by everyone.

    An f-string would turn `None` into the perfectly valid key `"u:None"`, and a
    `or 0` default would turn every failed lookup into `"u:0"`. Either is a real
    cart row that reads and writes successfully, so it would look like a working
    feature while serving one shopper another's basket. Raising is the only
    answer that cannot be mistaken for success.
    """
    with pytest.raises(ValueError):
        identity.owner_key_for_user(bad)


def test_a_guest_key_is_a_hash_of_the_token_and_not_the_token():
    """The cookie is not in the database, so a dump of the table is not a cookie.

    Recomputed from `hashlib` here rather than compared to a frozen literal: a
    frozen digest would also pass if the implementation started storing the raw
    token and this test hard-coded that token's digest by copying the output.
    """
    token = "H1r4NnTlN8jQ0vvZ6c2xKQmB1YDPz-0LZmXfQ7eH1sA"
    key = identity.owner_key_for_guest_token(token)

    assert key == "g:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token not in key
    assert len(key) == 2 + 64
    assert identity.is_guest_key(key)
    assert not identity.is_user_key(key)
    assert identity.user_id_from_key(key) is None


@pytest.mark.parametrize("bad", [None, "", "short", "x" * 31])
def test_a_short_guest_token_is_refused_rather_than_hashed(bad):
    """A weak token hashes to a perfectly well-formed key, which hides the weakness.

    `sha256("1")` is 64 characters of hex and looks exactly as strong as
    `sha256(<32 random bytes>)`. Nothing downstream could tell them apart, so the
    length has to be checked at the one place that knows it matters.
    """
    with pytest.raises(ValueError):
        identity.owner_key_for_guest_token(bad)


@pytest.mark.parametrize("hostile", [
    "u:1",
    "u:1\x00",
    "../u:1",
    "u:" + "1" * 64,
])
def test_a_guest_can_never_produce_an_account_key(hostile):
    """Even if the session contained an attacker's chosen string.

    It cannot -- the session is server-signed -- but that is one defence, and a
    single defence is a single point of failure. This is the second: the key is
    *constructed* here from a digest, so the output is `g:` followed by 64 hex
    characters whatever the input was. There is no input that makes this function
    return an account's key.
    """
    key = identity.owner_key_for_guest_token(hostile.ljust(32, "z"))
    assert key.startswith("g:")
    assert identity.user_id_from_key(key) is None
    assert not identity.is_user_key(key)


def test_two_different_tokens_never_share_a_cart():
    a = identity.owner_key_for_guest_token("a" * 43)
    b = identity.owner_key_for_guest_token("b" * 43)
    assert a != b
    # Stable, because a per-call salt would give the same guest a new cart on
    # every request -- and each gunicorn worker a different one.
    assert a == identity.owner_key_for_guest_token("a" * 43)


# ---------------------------------------------------------------------------
# 3. The guest handle in the session
# ---------------------------------------------------------------------------

def test_a_read_never_mints_a_guest_identity():
    """Otherwise every crawler and link-preview fetch gets a Set-Cookie.

    A guest acquires an identity when they first put something in a cart, not
    when a bot looks at a product page. `peek` is what the read paths call, and
    the distinction only holds if `peek` leaves the session alone.
    """
    session = {}
    assert identity.peek_guest_token(session) is None
    assert session == {}, "a read wrote to the session"


def test_a_write_mints_once_and_then_reuses():
    session = {}
    first = identity.ensure_guest_token(session)
    assert len(first) >= identity.MIN_GUEST_TOKEN_LENGTH
    assert session[identity.GUEST_SESSION_KEY] == first
    assert identity.ensure_guest_token(session) == first, (
        "a second write minted a new token, which would abandon the first cart"
    )


def test_a_token_too_short_to_trust_is_replaced_not_used():
    """Fails closed into a fresh identity rather than into a weak one.

    The cost of replacing is an abandoned guest cart. The cost of keeping is a
    cart key derived from something guessable.
    """
    session = {identity.GUEST_SESSION_KEY: "tiny"}
    assert identity.peek_guest_token(session) is None
    replaced = identity.ensure_guest_token(session)
    assert replaced != "tiny"
    assert len(replaced) >= identity.MIN_GUEST_TOKEN_LENGTH


def test_forgetting_the_guest_handle_leaves_no_trace():
    session = {}
    identity.ensure_guest_token(session)
    identity.forget_guest_token(session)
    assert identity.GUEST_SESSION_KEY not in session
    # Idempotent: a second merge attempt must not raise.
    identity.forget_guest_token(session)


# ---------------------------------------------------------------------------
# 4. Resolution -- the one code path
# ---------------------------------------------------------------------------

def test_a_signed_in_shopper_resolves_to_their_account():
    key, user_id = identity.resolve_cart_owner(
        {"user_id": 12}, {}, allow_guest=True, minting=True)
    assert key == "u:12"
    assert user_id == 12


def test_an_account_wins_over_a_stale_guest_token_in_the_same_session():
    """The ordering that matters, and the one whose inverse is a real incident.

    A browser that shopped as a guest and then signed in still carries the guest
    handle. If the guest branch were consulted first, a shopper who has just
    logged in would be served the anonymous cart of whoever last used that
    machine -- on a page showing their own name.
    """
    session = {}
    guest_token = identity.ensure_guest_token(session)
    key, user_id = identity.resolve_cart_owner(
        {"user_id": 12}, session, allow_guest=True, minting=True)

    assert key == "u:12"
    assert user_id == 12
    assert key != identity.owner_key_for_guest_token(guest_token)


def test_a_guest_resolves_to_nobody_while_guest_carts_are_off():
    session = {}
    assert identity.resolve_cart_owner(
        None, session, allow_guest=False, minting=True) == (None, None)
    assert session == {}, (
        "a token was minted for a shopper the caller had already refused"
    )


def test_a_guest_read_with_no_prior_token_resolves_to_nobody():
    assert identity.resolve_cart_owner(
        None, {}, allow_guest=True, minting=False) == (None, None)


def test_a_guest_write_resolves_to_a_guest_key():
    session = {}
    key, user_id = identity.resolve_cart_owner(
        None, session, allow_guest=True, minting=True)
    assert identity.is_guest_key(key)
    assert user_id is None
    assert key == identity.owner_key_for_guest_token(session[identity.GUEST_SESSION_KEY])


def test_a_signed_in_shopper_with_a_broken_id_is_refused_not_downgraded():
    """Falling through to the guest branch would lose a real shopper's cart.

    A signed-in user whose id will not resolve is a bug to fix, not a guest to
    serve. Silently moving them into an anonymous cart would present an empty
    basket to somebody who can see they are logged in.
    """
    session = {}
    assert identity.resolve_cart_owner(
        {"user_id": None}, session, allow_guest=True, minting=True) == (None, None)
    assert session == {}, "a guest identity was minted for a signed-in shopper"


# ---------------------------------------------------------------------------
# 5. The schema: column, backfill, index
# ---------------------------------------------------------------------------

def _legacy_cart_table():
    """A database as production has it today: no `owner_key`, rows keyed on user.

    Built by hand rather than by calling `_ensure_schema`, because the point is
    to exercise the upgrade path on a table that predates the column -- which is
    the only shape production will ever present to it.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE marketplace_cart_items ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, listing_id INTEGER,"
        " qty INTEGER DEFAULT 1, price_snapshot_minor INTEGER DEFAULT 0,"
        " currency TEXT DEFAULT 'USD', added_at TEXT, updated_at TEXT,"
        " UNIQUE(user_id, listing_id))"
    )
    return conn, cur


def _ensure(cur):
    """Call the ensure past its once-per-process cache.

    `run_once_per_process` is the right behaviour in a gunicorn worker and the
    wrong behaviour for a test that builds a new database per case: the second
    case would be served the first case's cached answer against a table that no
    longer exists.
    """
    return identity.ensure_cart_owner_key.__wrapped_ddl__(cur)


def test_the_upgrade_adds_the_column_backfills_it_and_builds_the_index():
    conn, cur = _legacy_cart_table()
    cur.execute("INSERT INTO marketplace_cart_items (user_id, listing_id, qty) VALUES (4, 9, 2)")
    cur.execute("INSERT INTO marketplace_cart_items (user_id, listing_id, qty) VALUES (5, 9, 1)")

    assert _ensure(cur) is True

    assert identity.owner_column_present(cur)
    assert identity.index_exists(cur)
    rows = cur.execute(
        "SELECT user_id, owner_key FROM marketplace_cart_items ORDER BY user_id"
    ).fetchall()
    assert [(r["user_id"], r["owner_key"]) for r in rows] == [(4, "u:4"), (5, "u:5")], (
        "the backfill did not derive the owner key from the user id, so the "
        "pre-existing lines are outside the new index"
    )
    conn.close()


def test_the_upgrade_is_idempotent():
    """It runs on every boot and from a route, so a second pass must be a no-op.

    Not merely "must not raise": a second backfill that re-derived keys would
    overwrite a guest's key with `u:NULL`, and a second `CREATE UNIQUE INDEX`
    would take a ShareLock on PostgreSQL that conflicts with an in-flight INSERT.
    """
    conn, cur = _legacy_cart_table()
    cur.execute("INSERT INTO marketplace_cart_items (user_id, listing_id) VALUES (4, 9)")
    assert _ensure(cur) is True
    before = cur.execute("SELECT owner_key FROM marketplace_cart_items").fetchall()

    assert _ensure(cur) is True
    assert cur.execute("SELECT owner_key FROM marketplace_cart_items").fetchall() == before
    conn.close()


def test_a_guest_row_survives_a_later_backfill_pass():
    """The backfill must not adopt a guest's line into `u:None`.

    Its WHERE clause is `owner_key IS NULL AND user_id IS NOT NULL`, and both
    halves matter: a guest row has an owner key and no user id, so it is outside
    the clause on either count. Asserted because an edit dropping the first half
    would still pass every other test in this file.
    """
    conn, cur = _legacy_cart_table()
    assert _ensure(cur) is True
    guest_key = identity.owner_key_for_guest_token("g" * 43)
    cur.execute(
        "INSERT INTO marketplace_cart_items (owner_key, user_id, listing_id) VALUES (?, NULL, 9)",
        (guest_key,),
    )

    assert _ensure(cur) is True
    assert cur.execute(
        "SELECT owner_key FROM marketplace_cart_items WHERE listing_id = 9"
    ).fetchone()["owner_key"] == guest_key
    conn.close()


def test_the_index_refuses_to_be_built_over_data_it_would_reject():
    """And it must not *attempt* the build, which is the assertion that matters.

    On PostgreSQL a failed `CREATE UNIQUE INDEX` poisons the caller's open
    transaction, and this runs inside an add-to-cart request. So "it returned
    False" is not the control -- the function returns False whether it declined
    or whether it tried and the exception was caught, and on SQLite those two are
    indistinguishable from outside. The control is that no DDL was issued, plus a
    log line naming the owner responsible, because "the index is missing" is not
    actionable and "this owner holds this listing twice" is.
    """
    conn, cur = _legacy_cart_table()
    # Two rows one owner key apart, reachable only because the legacy constraint
    # is on `user_id` and these have different ones.
    cur.execute("INSERT INTO marketplace_cart_items (user_id, listing_id) VALUES (4, 9)")
    cur.execute("INSERT INTO marketplace_cart_items (user_id, listing_id) VALUES (5, 9)")
    cur.execute("ALTER TABLE marketplace_cart_items ADD COLUMN owner_key TEXT")
    cur.execute("UPDATE marketplace_cart_items SET owner_key = 'u:4'")

    statements = []
    inner = cur

    class Recording:
        def execute(self, sql, *args):
            statements.append(str(sql))
            return inner.execute(sql, *args)

        def fetchone(self):
            return inner.fetchone()

        def fetchall(self):
            return inner.fetchall()

    recording = Recording()
    assert _ensure(recording) is False

    assert statements, "no SQL ran at all, so this proves nothing"
    assert not [s for s in statements if "CREATE" in s.upper()], (
        "the DDL was attempted against data that cannot support it; on PostgreSQL "
        f"that poisons the caller's open transaction: {statements}"
    )
    assert identity.colliding_lines(cur) == [("u:4", 9, 2)]
    assert not identity.index_exists(cur)
    conn.close()


def test_a_line_with_no_owner_at_all_is_reported_and_kept():
    """A cart line is somebody's intent to buy; a boot path may not delete it.

    Production has none of these. If one appeared it would sit *outside* the
    index rather than break it -- NULLs are exempt from uniqueness, which is the
    whole subject of this file -- so it would be silently unconstrained. That is
    exactly why it gets a log line rather than nothing.
    """
    conn, cur = _legacy_cart_table()
    cur.execute("INSERT INTO marketplace_cart_items (user_id, listing_id) VALUES (NULL, 9)")

    assert _ensure(cur) is True  # the orphan does not block the build
    assert identity.unkeyed_rows(cur) == 1
    assert cur.execute("SELECT COUNT(*) FROM marketplace_cart_items").fetchone()[0] == 1
    conn.close()


def test_the_ensure_never_raises_into_a_boot_path():
    """`init_db` is a caller, and there a raise truncates the schema at that line.

    `bot.py:119868` records the occasion that left 49 tables of 586. A missing
    table is the harshest input available, and `False` is the required answer:
    the caller keeps writing `user_id` exactly as it did yesterday.
    """
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    assert _ensure(cur) is False
    conn.close()


def test_the_declared_sql_is_portable_across_both_engines():
    """One DDL string has to be correct on SQLite and PostgreSQL 18.6.

    There is no migration framework here to hold a per-engine variant, so the
    guard is a spelling check on the constants themselves. `btrim`, `MIN(a, b)`,
    `NULLS NOT DISTINCT` and `IF NOT EXISTS` have each already cost this repo a
    production incident or a 500; none of them appear here, and a future edit that
    reaches for one fails this test rather than a buyer's checkout.
    """
    sql = (identity.CREATE_INDEX_SQL + " " + identity.BACKFILL_SQL).lower()
    for banned in ("btrim", "nulls not distinct", "min(", "ilike", "returning"):
        assert banned not in sql, f"{banned!r} is not portable: {sql}"
    # `||` and `CAST` are the portable concat and coercion; asserted positively so
    # a rewrite to a dialect-specific `CONCAT()` or `::text` is caught.
    assert "||" in identity.BACKFILL_SQL
    assert "cast(" in identity.BACKFILL_SQL.lower()


# ---------------------------------------------------------------------------
# 6. The routes
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def buyer():
    """An approved seller with one live digital listing, and a buyer who is not them."""
    with bot.webhook_app.app_context():
        bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                ("ownerkeybuyer", "ownerkeybuyer@example.com", "x"))
    buyer_id = cur.lastrowid
    cur.execute("INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                ("ownerkeyseller", "ownerkeyseller@example.com", "x"))
    seller_id = cur.lastrowid
    cur.execute("INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                ("ownerkeyother", "ownerkeyother@example.com", "x"))
    other_id = cur.lastrowid
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, status, business_name, display_name) "
        "VALUES (?,?,?,?)", (seller_id, "approved", "Owner Key Store", "Owner Key Store"))
    cur.execute(
        """INSERT INTO marketplace_listings
           (seller_user_id, title, description, category, price_label, currency,
            quantity, status, approval_status, delivery_type)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (seller_id, "Owner Key Widget", "A widget.", "Education",
         "$19.99", "USD", 50, "active", "approved", "digital"))
    listing_id = cur.lastrowid
    conn.commit()
    return buyer_id, other_id, listing_id


def _client(user_id=None):
    client = bot.webhook_app.test_client()
    if user_id is not None:
        with client.session_transaction() as session:
            session["account_user_id"] = user_id
    return client


def _empty(client):
    for entry in client.get(CART_API, headers=HTTPS).get_json()["lines"]:
        client.delete(f"{CART_API}/{entry['line_id']}", headers=HTTPS)
    assert client.get(CART_API, headers=HTTPS).get_json()["lines"] == []


def test_a_double_tap_raises_the_quantity_instead_of_duplicating_the_line(buyer):
    """The guarantee the add path claims in its own comment, asserted end to end.

    This is the behaviour the owner key exists to preserve, so it is asserted
    through the real route rather than against a hand-built table: it is the
    `ON CONFLICT` target, the parameter order and the clamp all agreeing.
    """
    buyer_id, _other, listing_id = buyer
    client = _client(buyer_id)
    _empty(client)

    for _ in range(3):
        added = client.post(CART_API, json={"listing_id": listing_id, "qty": 2}, headers=HTTPS)
        assert added.status_code == 200, added.get_data(as_text=True)

    payload = client.get(CART_API, headers=HTTPS).get_json()
    assert len(payload["lines"]) == 1, f"the line was duplicated: {payload['lines']}"
    assert payload["lines"][0]["qty"] == 6
    json.dumps(payload)


def test_an_account_cart_row_carries_both_the_owner_key_and_the_user_id(buyer):
    """`user_id` keeps being written, and that is deliberate, not residue.

    Two `DELETE`s in `bot.py` clear checked-out lines with `AND user_id=?`, and
    the pre-existing `UNIQUE(user_id, listing_id)` is still on the table. Writing
    both means this change adds a constraint without invalidating anything that
    already reads the old one.
    """
    buyer_id, _other, listing_id = buyer
    client = _client(buyer_id)
    _empty(client)
    client.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS)

    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "SELECT owner_key, user_id FROM marketplace_cart_items WHERE listing_id=? LIMIT 1",
        (listing_id,))
    owner_key, user_id = db_service.row_values(cur.fetchone())[:2]
    conn.close()

    assert owner_key == f"u:{buyer_id}"
    assert int(user_id) == buyer_id


def test_one_shopper_cannot_touch_another_shoppers_line(buyer):
    """The owner key is the authorisation check, not just the lookup key.

    Every write below is `WHERE id=? AND owner_key=?`. Dropping the second half
    would leave a working cart and an IDOR: line ids are sequential integers.
    """
    buyer_id, other_id, listing_id = buyer
    owner = _client(buyer_id)
    _empty(owner)
    owner.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS)
    line_id = owner.get(CART_API, headers=HTTPS).get_json()["lines"][0]["line_id"]

    intruder = _client(other_id)
    assert intruder.get(CART_API, headers=HTTPS).get_json()["lines"] == []
    assert intruder.post(f"{CART_API}/{line_id}", json={"qty": 9},
                         headers=HTTPS).status_code == 404
    assert intruder.delete(f"{CART_API}/{line_id}", headers=HTTPS).status_code == 404
    assert intruder.post(f"{CART_API}/{line_id}/confirm-price", json={},
                         headers=HTTPS).status_code == 404

    still = owner.get(CART_API, headers=HTTPS).get_json()["lines"]
    assert len(still) == 1 and still[0]["qty"] == 1, (
        "an intruder's refused request still changed the owner's line"
    )


def test_a_signed_out_shopper_is_refused_while_guest_carts_are_off(buyer):
    """Today's behaviour, unchanged -- and stated so the flag flip has a baseline.

    `GUEST_CARTS_ENABLED` is False because a cart a guest can fill and cannot
    check out is a dead end. This test is what will have to change when guest
    checkout lands, which is the point of writing it down now.
    """
    _buyer_id, _other, listing_id = buyer
    anonymous = _client()
    assert cart.GUEST_CARTS_ENABLED is False

    for response in (
        anonymous.get(CART_API, headers=HTTPS),
        anonymous.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS),
        anonymous.post(f"{CART_API}/validate", json={}, headers=HTTPS),
        anonymous.post(f"{CART_API}/checkout", json={"seller_user_id": 1}, headers=HTTPS),
    ):
        assert response.status_code == 401, response.get_data(as_text=True)
        assert response.get_json()["error_code"] == "LOGIN_REQUIRED"


def test_a_refused_guest_is_not_given_a_session_cookie(buyer):
    """A 401 must not mint an identity: nothing owns a cart, so nothing is stored.

    Minting on a refused request would set a cookie on every anonymous probe of
    the cart endpoint, and would leave a guest handle in the session of somebody
    who never got a cart.
    """
    _buyer_id, _other, listing_id = buyer
    anonymous = _client()
    anonymous.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS)

    with anonymous.session_transaction() as session:
        assert identity.GUEST_SESSION_KEY not in session


def test_a_guest_cart_through_the_real_route_does_not_duplicate_its_lines(buyer, monkeypatch):
    """The one test that exercises the production shape: an upsert with `user_id` NULL.

    Every other route test here signs in, and for a signed-in shopper
    `ON CONFLICT(owner_key, listing_id)` and `ON CONFLICT(user_id, listing_id)`
    say exactly the same thing -- so the whole suite would pass with the old
    target restored. That is the bug this module exists to fix, and it is
    invisible until a row has a NULL `user_id`.

    So the flag is flipped for the length of this test. Not to claim guest carts
    are finished -- checkout still refuses an owner with no `user_id`, a few lines
    into `cart_checkout` -- but because the *storage* half has to be proven
    against a real NULL before the rest can be built on it. `monkeypatch` puts it
    back, and `test_a_signed_out_shopper_is_refused_while_guest_carts_are_off`
    asserts the shipped default either way.
    """
    _buyer_id, _other, listing_id = buyer
    monkeypatch.setattr(cart, "GUEST_CARTS_ENABLED", True)
    guest = _client()

    for _ in range(3):
        added = guest.post(CART_API, json={"listing_id": listing_id, "qty": 2}, headers=HTTPS)
        assert added.status_code == 200, added.get_data(as_text=True)

    lines = guest.get(CART_API, headers=HTTPS).get_json()["lines"]
    assert len(lines) == 1, f"a NULL user_id duplicated the line: {lines}"
    assert lines[0]["qty"] == 6

    with guest.session_transaction() as session:
        token = session[identity.GUEST_SESSION_KEY]
    expected = identity.owner_key_for_guest_token(token)

    conn = bot.db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT owner_key, user_id FROM marketplace_cart_items "
            "WHERE owner_key=? AND listing_id=?", (expected, listing_id))
        rows = [db_service.row_values(row) for row in cur.fetchall()]
    finally:
        conn.close()

    assert len(rows) == 1, f"the guest holds {len(rows)} rows for one listing"
    assert rows[0][1] is None, (
        "the guest row carries a user_id, so this test is not proving what it claims"
    )


def test_two_guests_on_one_machine_do_not_share_a_cart(buyer, monkeypatch):
    """Separate sessions, separate carts -- and neither can reach an account's.

    The guest handle is the Flask session, so "two guests" is two cookie jars.
    This is the property that makes the owner key safe to read without an auth
    check: a guest key is derived from a server-signed value, so it cannot be
    pointed at `u:1`.
    """
    buyer_id, _other, listing_id = buyer
    monkeypatch.setattr(cart, "GUEST_CARTS_ENABLED", True)

    first, second = _client(), _client()
    first.post(CART_API, json={"listing_id": listing_id, "qty": 1}, headers=HTTPS)
    second.post(CART_API, json={"listing_id": listing_id, "qty": 5}, headers=HTTPS)

    assert first.get(CART_API, headers=HTTPS).get_json()["lines"][0]["qty"] == 1
    assert second.get(CART_API, headers=HTTPS).get_json()["lines"][0]["qty"] == 5

    line_id = first.get(CART_API, headers=HTTPS).get_json()["lines"][0]["line_id"]
    assert second.delete(f"{CART_API}/{line_id}", headers=HTTPS).status_code == 404
    assert first.get(CART_API, headers=HTTPS).get_json()["lines"], "a guest deleted another's line"

    signed_in = _client(buyer_id)
    guest_lines = {entry["line_id"] for entry in first.get(CART_API, headers=HTTPS).get_json()["lines"]}
    account_lines = {entry["line_id"] for entry in signed_in.get(CART_API, headers=HTTPS).get_json()["lines"]}
    assert not (guest_lines & account_lines)


def test_the_live_schema_has_the_column_and_the_index(buyer):
    """`init_db` and the route pack both reach the ensure, so a real boot has it.

    Asserted against the database `bot` actually built rather than a hand-made
    table, because the failure this catches is the ensure never being *called* --
    which every test in section 5 would pass straight through.
    """
    conn = bot.db()
    cur = conn.cursor()
    try:
        assert identity.owner_column_present(cur)
        assert identity.index_exists(cur)
    finally:
        conn.close()
