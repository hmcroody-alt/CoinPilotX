"""``GET /api/business-os/dropshipping/supplier-status`` — the one health answer. §21/§23.

What this file is defending
---------------------------
This route exists so that no screen has to infer supplier health. Inference is
what produced the defect it replaces: a client holding the connections list and
the products list does not hold "is this supplier working", so it derives one,
and a derivation is a second copy of a rule that lives elsewhere. Three screens
derived it three ways.

Replacing three client rules with one server rule is only an improvement if the
server rule is *observed*. A canonical endpoint that cheerfully reports READY
when a lookup failed is worse than the three it replaced, because every screen
now believes it at once. So the properties here are mostly about what the route
refuses to claim:

* A connection that is not CONNECTED never reports as ready, whatever else is
  true of it.
* A connection with no fulfilment shop is not ready either -- it can import and
  cannot fulfil, which is the state most likely to be mistaken for working.
* ``environment`` and ``real_order_submission_enabled`` are whatever ``policy``
  says, not whatever the environment variable says. A badge that disagrees with
  the gate it describes is the §21 defect in its purest form.
* An unrecognised connection status is not good news.
* The route is authenticated, scoped, and leaks no credential.

Why this file runs alone
------------------------
``DATABASE_URL`` is bound to its own temp file at import, before ``services.db``
computes ``IS_POSTGRES``.

    .venv/bin/python3 -m pytest tests/dropshipping/test_supplier_status_route.py
"""

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="supplier-status-route-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from flask import Flask, session  # noqa: E402

from services import business_os_dropshipping_routes as routes  # noqa: E402
from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import fulfillment, policy, status  # noqa: E402
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import (  # noqa: E402
    seed_orders_table, seed_production_listings)

PREFIX = "/api/business-os/dropshipping"

OWNER = "9201"
OTHER = "9202"
BUSINESS, STORE, CONNECTION = "biz-s", "store-s", "conn-s"


@pytest.fixture(autouse=True)
def database():
    open(_DB_PATH, "w").close()
    supplier_schema.reset_schema_cache()
    conn = db.connect()
    try:
        cur = conn.cursor()
        # The catalogue tables, then emptied: the status payload counts rows in
        # `marketplace_listings`, so a missing table and an empty one must not be
        # allowed to look the same here.
        seed_production_listings(cur)
        cur.execute("DELETE FROM marketplace_listings")
        supplier_schema.ensure_supplier_schema(cur, force=True)
        seed_orders_table(cur)
        # Verbatim from `bot.init_db()`, and required rather than incidental:
        # gap 15 made this row the only source of a supplier destination, so an
        # obligation count taken without it is counting a different query from
        # the one production runs.
        cur.execute("""CREATE TABLE IF NOT EXISTS seller_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_user_id INTEGER, seller_user_id INTEGER, seller_type TEXT,
            item_type TEXT, item_id INTEGER, amount_cents INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'USD', platform_fee_cents INTEGER DEFAULT 0,
            seller_net_cents INTEGER DEFAULT 0, status TEXT DEFAULT 'created',
            stripe_checkout_session_id TEXT, stripe_payment_intent_id TEXT,
            metadata_json TEXT, created_at TEXT, updated_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_business (
            business_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_store_storefront (
            storefront_id TEXT PRIMARY KEY, business_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_business_members (
            business_id TEXT NOT NULL, user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'owner',
            status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute("INSERT INTO business_os_business (business_id, owner_user_id) VALUES (?,?)",
                     (BUSINESS, OWNER))
        conn.execute("INSERT INTO business_os_store_storefront (storefront_id, business_id) "
                     "VALUES (?,?)", (STORE, BUSINESS))
        conn.execute("INSERT INTO business_os_business_members (business_id, user_id) "
                     "VALUES (?,?)", (BUSINESS, OWNER))
        conn.commit()
    finally:
        conn.close()
    connection_schema.ensure_schema()
    yield
    supplier_schema.reset_schema_cache()


def seed_connection(*, state="CONNECTED", shop="shop-1"):
    """``shop=""`` is how "no fulfilment shop chosen" is really stored: the
    column is NOT NULL, so absence is the empty string and a test passing
    ``None`` would be asserting against a row the schema cannot hold."""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_supplier_connections")
        conn.execute(
            "INSERT INTO business_os_supplier_connections "
            "(id, merchant_id, business_id, store_id, provider, connection_type, "
            " external_account_id, external_shop_id, status, credential_reference, "
            " access_expires_at, refresh_expires_at, quota_state, created_at, updated_at) "
            "VALUES (?,?,?,?,'CJ','API_KEY',?,?,?,?,?,?,'UNKNOWN',?,?)",
            (CONNECTION, OWNER, BUSINESS, STORE, "acct-1", shop, state, "cred-1",
             "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z",
             "2026-09-07T00:00:00Z", "2026-09-07T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def a_buyer_paid_for_a_dropship_listing(*, cost_cents=800, sku="SKU-1"):
    """One paid sale that owes this connection a supplier purchase.

    Written with SQL rather than by driving the importer, because what is under
    test is the *count* the status payload reports, and the obligation module
    already has a suite that drives the real import path. ``cost_cents=None``
    and ``sku=None`` are how an obligation becomes blocked -- an unknown buying
    price and a missing SKU are both refusals inside ``create_intent`` -- so the
    two states this payload has to tell apart can be seeded here directly.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        # `physical` in both columns: `effective_listing_type` is applied in
        # python after the SQL, and a listing it does not call physical is
        # dropped from the obligation list entirely rather than counted as one.
        cur.execute(
            "INSERT INTO marketplace_listings (seller_user_id, title, status, "
            "product_type, listing_type) VALUES (?,?,?,?,?)",
            (int(OWNER), "A physical thing", "active", "physical", "physical"))
        listing_id = cur.lastrowid
        # Derived from the listing rather than fixed, because the source table
        # is unique on (seller, provider, connection, product) -- two sales of
        # one fixed id is a constraint violation, not a second obligation, and
        # a test counting two would otherwise be unwritable.
        product_id, variant_id = f"PID-{listing_id}", f"VID-{listing_id}"
        cur.execute(
            "INSERT INTO marketplace_product_sources (listing_id, seller_user_id, provider, "
            "provider_product_id, provider_variant_id, fulfillment_mode, "
            "supplier_connection_id, business_id, store_id, supplier_cost_cents, "
            "supplier_cost_currency) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (listing_id, int(OWNER), "CJ", product_id, variant_id, "DROPSHIP",
             CONNECTION, BUSINESS, STORE, cost_cents, "USD"))
        # The bound variant carries the SKU `create_intent` matches on. Absent,
        # the obligation is blocked -- which is one of the two states counted.
        if sku:
            cur.execute(
                "INSERT INTO marketplace_listing_variants (listing_id, seller_user_id, "
                "variant_key, provider_variant_id, sku) VALUES (?,?,?,?,?)",
                (listing_id, int(OWNER), "default", variant_id, sku))
        # The frozen checkout blob, on the transaction rather than the order:
        # the destination lives only here, so a helper writing the order alone
        # would manufacture a `DESTINATION_MISSING` on every sale in this file.
        # Keys spelled the way `validate_details` spells them, not the way a
        # postal form does.
        cur.execute(
            "INSERT INTO seller_transactions (buyer_user_id, seller_user_id, seller_type, "
            "item_type, item_id, amount_cents, currency, status, metadata_json) "
            "VALUES (?,?,'user','marketplace_listing',?,?,?,?,?)",
            (4242, int(OWNER), listing_id, 2000, "USD", "paid", json.dumps({
                "fulfillment": {"kind": "shipping", "details": {
                    "contact_name": "A Buyer", "contact_phone": "+15550100",
                    "address_line1": "1 Fixture Way", "address_city": "Fixture City",
                    "address_region": "CA", "address_postal_code": "94000",
                    "address_country": "US"}}})))
        transaction_id = cur.lastrowid
        cur.execute(
            "INSERT INTO marketplace_orders (seller_transaction_id, buyer_user_id, "
            "seller_user_id, listing_id, quantity, unit_price_cents, amount_cents, "
            "currency, status, paid_at) VALUES (?,?,?,?,1,?,?,'USD','paid',?)",
            (transaction_id, 4242, int(OWNER), listing_id, 2000, 2000,
             "2026-09-12T00:00:00"))
        conn.commit()
        return listing_id
    finally:
        conn.close()


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "synthetic-session-signing-key"
    app.testing = True

    def user():
        actor = session.get("user_id")
        return {"user_id": actor, "account_status": "active", "access_enabled": 1} if actor else None

    monkeypatch.setattr(routes, "_bot", lambda: SimpleNamespace(api_account_user=user))
    routes.register(app)
    return app.test_client()


def sign_in(client, actor):
    with client.session_transaction() as state:
        state["user_id"] = actor


def get(client, business_id=BUSINESS, store_id=STORE):
    return client.get(f"{PREFIX}/supplier-status?business_id={business_id}&store_id={store_id}")


# ---------------------------------------------------------------------------
# The route is real
# ---------------------------------------------------------------------------

def test_a_healthy_connection_is_reported_ready(client):
    """Guards every refusal below: if the route answered 'not ready' to
    everything, the tests asserting it refuses would pass against a function
    that can never say yes."""
    seed_connection()
    sign_in(client, OWNER)
    response = get(client)
    body = response.get_json()

    assert response.status_code == 200
    supplier = body["suppliers"][0]
    assert supplier["connection_state"] == "CONNECTED"
    assert supplier["fulfillment_shop_state"] == status.SHOP_BOUND
    assert supplier["needs_attention"] is False
    # Nothing imported yet, so the next step is to import — a suggestion, not
    # an alarm.
    assert supplier["next_action"] == status.IMPORT_PRODUCTS
    assert body["needs_attention"] is False


# ---------------------------------------------------------------------------
# Auth and scope
# ---------------------------------------------------------------------------

def test_a_signed_out_caller_is_refused(client):
    seed_connection()
    response = get(client)

    assert response.status_code == 401
    assert response.get_json()["ok"] is False


def test_another_merchant_cannot_read_this_stores_suppliers(client):
    """A status payload is a disclosure: it carries shop ids and catalogue
    sizes. Asserted with a real second signed-in user rather than inferred
    from the WHERE clause being present."""
    seed_connection()
    sign_in(client, OTHER)
    response = get(client)

    assert response.status_code in {403, 404}
    assert response.get_json()["ok"] is False


def test_a_request_without_a_scope_is_rejected_rather_than_defaulted(client):
    """No implicit "the caller's first store". A missing scope is a client
    bug, and answering it with somebody's data would hide that forever."""
    seed_connection()
    sign_in(client, OWNER)
    response = client.get(f"{PREFIX}/supplier-status")

    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_no_credential_reference_reaches_the_client(client):
    seed_connection()
    sign_in(client, OWNER)
    body = get(client).get_json()

    blob = repr(body)
    assert "cred-1" not in blob
    supplier = body["suppliers"][0]
    # The *presence* of a credential is a legitimate fact for a setup screen;
    # the credential's identity is not.
    assert supplier["credential_present"] is True
    assert "credential_reference" not in supplier


# ---------------------------------------------------------------------------
# §21 the route never reports health it has not observed
# ---------------------------------------------------------------------------

def test_a_disconnected_supplier_is_never_ready(client):
    seed_connection(state="REAUTH_REQUIRED")
    sign_in(client, OWNER)
    supplier = get(client).get_json()["suppliers"][0]

    assert supplier["connection_state"] == "REAUTH_REQUIRED"
    assert supplier["needs_attention"] is True
    assert supplier["next_action"] == status.RECONNECT


def test_a_connection_with_no_fulfilment_shop_is_not_ready(client):
    """The state most easily mistaken for working.

    Credentials are valid, search works, products import — and not one order
    can be fulfilled, because no shop is bound. A screen that only reads
    ``connection_state`` shows this as Connected and green.
    """
    seed_connection(shop="")
    sign_in(client, OWNER)
    supplier = get(client).get_json()["suppliers"][0]

    assert supplier["connection_state"] == "CONNECTED"
    assert supplier["fulfillment_shop_state"] == status.SHOP_NOT_SELECTED
    assert supplier["needs_attention"] is True
    assert supplier["next_action"] == status.CHOOSE_SHOP


def test_an_unrecognised_connection_status_is_not_good_news(client):
    """A status added by the provider, or by a later version of us, must not
    fall through into the ready path."""
    seed_connection(state="SOMETHING_NEW")
    sign_in(client, OWNER)
    supplier = get(client).get_json()["suppliers"][0]

    assert supplier["needs_attention"] is True
    assert supplier["next_action"] == status.RECONNECT


def test_the_connection_problem_outranks_the_shop_problem(client):
    """Dependency order, not severity order.

    A merchant whose credentials expired is not helped by being sent to pick a
    fulfilment shop: the shop list is fetched from the provider they can no
    longer authenticate with, so the errand fails and they have learned nothing.
    """
    seed_connection(state="REAUTH_REQUIRED", shop="")
    sign_in(client, OWNER)
    supplier = get(client).get_json()["suppliers"][0]

    assert supplier["next_action"] == status.RECONNECT


# ---------------------------------------------------------------------------
# §12/§21 the environment is delegated, never re-derived
# ---------------------------------------------------------------------------

def test_the_environment_is_whatever_policy_says_not_whatever_the_env_var_says(client,
                                                                              monkeypatch):
    """The §21 defect in its purest form, asserted against.

    ``policy.fulfillment_environment`` probes the real gate. Re-reading
    ``CJ_ENVIRONMENT_MODE`` here would produce a badge that disagrees with the
    thing it claims to describe — "Live" over a runtime rejecting live orders.
    So the env var is set one way and the policy answer another, and the route
    must follow the policy.
    """
    seed_connection()
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "LIVE")
    monkeypatch.setattr(policy, "fulfillment_environment", lambda: "SANDBOX")
    sign_in(client, OWNER)
    body = get(client).get_json()

    assert body["environment"] == "SANDBOX"
    assert body["suppliers"][0]["environment"] == "SANDBOX"


def test_real_order_submission_is_reported_off_because_the_platform_says_so(client):
    """Not a guess and not a constant: the same function the fulfilment path
    itself consults. If this ever reports True while
    ``live_fulfillment_path_exists`` is False, a merchant has been told their
    orders reach the supplier when they do not."""
    seed_connection()
    sign_in(client, OWNER)
    body = get(client).get_json()

    assert body["real_order_submission_enabled"] is policy.live_fulfillment_path_exists()
    assert body["real_order_submission_enabled"] is False
    assert body["suppliers"][0]["real_order_submission_enabled"] is False


def test_the_store_wide_kill_switch_is_not_read_off_a_connection_row(client, monkeypatch):
    """The switch is platform-level. A client reading it from whichever
    connection sorted first would report it per-supplier, and a second supplier
    could then appear to have different permissions than the first."""
    seed_connection()
    monkeypatch.setattr(policy, "live_fulfillment_path_exists", lambda: True)
    sign_in(client, OWNER)
    body = get(client).get_json()

    assert body["real_order_submission_enabled"] is True


# ---------------------------------------------------------------------------
# §18 multi-supplier readiness
# ---------------------------------------------------------------------------

def test_the_provider_is_a_value_and_never_a_path(client):
    """A second supplier must be a row in this response, not a second endpoint.

    The path is asserted to contain no provider name so that the next adapter
    cannot be added by copying this route.
    """
    seed_connection()
    sign_in(client, OWNER)
    supplier = get(client).get_json()["suppliers"][0]

    assert supplier["provider"] == "cj"
    paths = [str(rule) for rule in client.application.url_map.iter_rules()]
    assert f"{PREFIX}/supplier-status" in paths
    assert not [p for p in paths if "cj" in p.lower().split("/")]


def test_a_store_with_no_connections_is_empty_rather_than_broken(client):
    """No supplier is a real, common state — a merchant who has not connected
    one yet. It must not read as an error, and it must not read as a healthy
    supplier either."""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_supplier_connections")
        conn.commit()
    finally:
        conn.close()
    sign_in(client, OWNER)
    response = get(client)
    body = response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["suppliers"] == []
    assert body["needs_attention"] is False


# ---------------------------------------------------------------------------
# §6 supplier orders — the tile and the screen count the same rows
# ---------------------------------------------------------------------------

def test_a_placeable_sale_is_counted_as_ready_rather_than_merely_waiting(client):
    """The tile's headline number is the one the merchant can act on.

    `awaiting` alone would be a count of work with no verb attached: a merchant
    told "4 waiting" cannot tell whether tapping through gives them four buttons
    or four explanations. So the payload splits the same rows two ways and this
    pins the split rather than the total.
    """
    seed_connection()
    a_buyer_paid_for_a_dropship_listing()
    sign_in(client, OWNER)
    orders = get(client).get_json()["suppliers"][0]["orders"]

    assert orders["awaiting_supplier_order"] == 1
    assert orders["ready_to_place"] == 1
    assert orders["blocked"] == 0
    assert orders["placed"] == 0


@pytest.mark.parametrize("seed,why", [
    ({"cost_cents": None}, "an unknown buying price"),
    ({"sku": None}, "a missing SKU"),
])
def test_a_sale_the_merchant_cannot_place_is_counted_as_blocked(client, seed, why):
    """Blocked is not a smaller kind of ready.

    Both of these refuse inside `create_intent`, so a tile that counted them as
    ready would render a button that cannot work — and the merchant would learn
    that one sale at a time, on the screen behind it. Counting them apart is
    what lets the tile lead with the stuck ones rather than bury them.
    """
    seed_connection()
    a_buyer_paid_for_a_dropship_listing(**seed)
    sign_in(client, OWNER)
    orders = get(client).get_json()["suppliers"][0]["orders"]

    assert orders["awaiting_supplier_order"] == 1, why
    assert orders["ready_to_place"] == 0, why
    assert orders["blocked"] == 1, why


def test_the_count_comes_from_the_list_the_tile_opens(client):
    """One derivation, observed — not two that happen to agree today.

    An obligation is only real if `effective_listing_type` calls the listing
    physical, which is python applied to the rows *after* the SQL. A second
    query written for this payload would therefore over-count, and the first
    symptom would be a tile reporting more sales than the screen it opens. This
    asserts the payload against `list_obligations` itself, so that rewriting the
    count as SQL fails here rather than in a merchant's hands.
    """
    seed_connection()
    a_buyer_paid_for_a_dropship_listing()
    a_buyer_paid_for_a_dropship_listing(sku=None)
    sign_in(client, OWNER)
    orders = get(client).get_json()["suppliers"][0]["orders"]

    listed = fulfillment.list_obligations(
        CONNECTION, BUSINESS, STORE, OWNER, limit=200)["obligations"]
    awaiting = [row for row in listed if not row.get("supplier_order_placed")]
    assert orders["awaiting_supplier_order"] == len(awaiting) == 2
    assert orders["ready_to_place"] == len(
        [row for row in awaiting if row.get("can_place_supplier_order")]) == 1


def test_an_unreadable_fulfilment_read_is_null_and_never_nothing_waiting(client,
                                                                        monkeypatch):
    """§24. Zero is the reassuring answer, so a failed read must not spell it.

    A merchant told nothing is waiting stops looking, and the sales they stopped
    looking for are ones a buyer has already paid for. `null` is a different
    word to the client — "we could not find out" — and the tile renders it as a
    sentence about the screen rather than a count of the store.

    The rest of the payload survives, because a fulfilment table that was
    briefly unreachable says nothing about whether the credential works.
    """
    seed_connection()
    a_buyer_paid_for_a_dropship_listing()

    def unreachable(*args, **kwargs):
        raise RuntimeError("fulfilment tables unreachable")

    monkeypatch.setattr(status.fulfillment, "list_obligations", unreachable)
    sign_in(client, OWNER)
    response = get(client)
    supplier = response.get_json()["suppliers"][0]

    assert response.status_code == 200
    assert "orders" in supplier, "absent is not the same word as null"
    assert supplier["orders"] is None
    assert supplier["connection_state"] == "CONNECTED"
