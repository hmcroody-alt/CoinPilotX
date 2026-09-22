"""``POST /api/business-os/dropshipping/connections/<id>/sync`` — "Sync now". §9/§16/§21.

What this file is defending
---------------------------
The supplier screen can say "Last sync failed". §21 says every displayed state
must be backed by real work, and the corollary is that every *control* must be
too: a button labelled "Sync now" that does not cause a sync is worse than no
button, because the merchant stops watching a problem they believe they have
already acted on.

So the properties here are about the button being real and being safe:

* Pressing it moves this connection's jobs to the front of the worker's queue —
  asserted against ``available_at`` actually moving, not against the route
  returning 200.
* It enqueues the merchant's *own* products and nothing else. The ids never come
  from the request.
* Another merchant cannot press it for this store's connection.
* It is bounded, and says so when it truncates.
* It queues connection checks even when nothing has been imported, because a
  merchant with no products is still asking about their connection.

Why this file runs alone
------------------------
``DATABASE_URL`` is bound to its own temp file at import, before ``services.db``
computes ``IS_POSTGRES``.

    .venv/bin/python3 -m pytest tests/dropshipping/test_supplier_resync_route.py
"""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="supplier-resync-route-", suffix=".db")
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
from services.business_os.suppliers import resync, worker  # noqa: E402
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

PREFIX = "/api/business-os/dropshipping"

OWNER = "9301"
OTHER = "9302"
BUSINESS, STORE, CONNECTION = "biz-r", "store-r", "conn-r"

#: Far enough in the future that a job left at this time is unambiguously one
#: the request did not move, rather than one a clock happened to overtake.
FAR_FUTURE = 4_000_000_000.0


@pytest.fixture(autouse=True)
def database():
    open(_DB_PATH, "w").close()
    supplier_schema.reset_schema_cache()
    conn = db.connect()
    try:
        cur = conn.cursor()
        seed_production_listings(cur)
        cur.execute("DELETE FROM marketplace_listings")
        supplier_schema.ensure_supplier_schema(cur, force=True)
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
    worker.ensure_schema()
    seed_connection()
    yield
    supplier_schema.reset_schema_cache()


def seed_connection():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_supplier_connections")
        conn.execute(
            "INSERT INTO business_os_supplier_connections "
            "(id, merchant_id, business_id, store_id, provider, connection_type, "
            " external_account_id, external_shop_id, status, credential_reference, "
            " access_expires_at, refresh_expires_at, quota_state, created_at, updated_at) "
            "VALUES (?,?,?,?,'CJ','API_KEY',?,?,'CONNECTED',?,?,?,'UNKNOWN',?,?)",
            (CONNECTION, OWNER, BUSINESS, STORE, "acct-1", "shop-1", "cred-1",
             "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z",
             "2026-09-07T00:00:00Z", "2026-09-07T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def seed_products(count, *, seller=OWNER, connection_id=CONNECTION, prefix="pid-"):
    """Imported products, as the join that owns them really stores them.

    A source row without a listing row is not a product this store imported, so
    both are written: the resync query is scoped by ``seller_user_id`` and a test
    that skipped the listing would still pass while proving nothing about which
    rows a merchant owns.
    """
    conn = db.connect()
    try:
        for index in range(count):
            listing_id = 5000 + index + (0 if seller == OWNER else 500)
            conn.execute("INSERT INTO marketplace_listings (id, seller_user_id, title, status, "
                         "approval_status) VALUES (?,?,?,'published','approved')",
                         (listing_id, int(seller), f"Item {index}"))
            conn.execute(
                "INSERT INTO marketplace_product_sources "
                "(listing_id, seller_user_id, provider, provider_product_id, "
                " supplier_connection_id, business_id, store_id, sync_state) "
                "VALUES (?,?,'cj',?,?,?,?,'SYNCED')",
                (listing_id, int(seller), f"{prefix}{index:04d}", connection_id, BUSINESS, STORE))
        conn.commit()
    finally:
        conn.close()


def park_jobs():
    """Push every queued job far into the future.

    The assertion this file cares about is that a merchant's request *moved*
    something. Left at their natural cadence the jobs would be due within the
    hour anyway, so a broken implementation that scheduled nothing would still
    find rows that look ready. Parking them makes "moved" observable.
    """
    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_supplier_sync_jobs SET available_at=?", (FAR_FUTURE,))
        conn.commit()
    finally:
        conn.close()


def jobs():
    conn = db.connect()
    try:
        rows = conn.execute("SELECT kind, resource_id, available_at "
                            "FROM business_os_supplier_sync_jobs ORDER BY kind, resource_id").fetchall()
        return [(row["kind"], row["resource_id"], row["available_at"]) for row in rows]
    finally:
        conn.close()


def due_now(now):
    return [(kind, resource) for kind, resource, available in jobs() if available <= now]


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "synthetic-session-signing-key"
    app.testing = True

    def user():
        actor = session.get("user_id")
        return {"user_id": actor, "account_status": "active", "access_enabled": 1} if actor else None

    monkeypatch.setattr(routes, "_bot", lambda: SimpleNamespace(api_account_user=user))
    monkeypatch.setattr(routes, "_csrf_ok", lambda: True)
    routes.register(app)
    return app.test_client()


def sign_in(client, actor):
    with client.session_transaction() as state:
        state["user_id"] = actor


def post(client, *, connection_id=CONNECTION, business_id=BUSINESS, store_id=STORE):
    return client.post(f"{PREFIX}/connections/{connection_id}/sync",
                       json={"business_id": business_id, "store_id": store_id})


# ---------------------------------------------------------------------------
# The button does something
# ---------------------------------------------------------------------------

def test_pressing_sync_queues_the_connection_checks(client):
    """Guards every refusal below: if the route queued nothing at all, the tests
    asserting it refuses to queue would pass against a no-op."""
    sign_in(client, OWNER)
    response = post(client)
    body = response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["queued_connection_checks"] == 3
    queued = {kind for kind, _resource, _at in jobs()}
    assert queued == {"health", "shops", "subscriptions"}


def test_pressing_sync_queues_every_imported_product(client):
    seed_products(3)
    sign_in(client, OWNER)
    body = post(client).get_json()

    assert body["queued_products"] == 3
    assert body["truncated"] is False
    resources = {(kind, resource) for kind, resource, _at in jobs() if resource}
    assert resources == {(kind, f"pid-{index:04d}")
                         for kind in ("product", "inventory") for index in range(3)}


def test_a_parked_job_is_really_pulled_forward(client):
    """The load-bearing assertion.

    A route that inserted rows but never moved an existing one would satisfy
    every count above while leaving the merchant waiting out the same cadence
    they pressed the button to skip.
    """
    seed_products(1)
    sign_in(client, OWNER)
    post(client)
    park_jobs()
    assert due_now(FAR_FUTURE - 1) == []

    body = post(client).get_json()

    assert len(due_now(body["requested_at"])) == 5  # 3 connection + product + inventory


def test_a_merchant_with_nothing_imported_still_gets_their_connection_checked(client):
    """The state a merchant is most likely to press this from: they connected a
    supplier, nothing works, and they have imported nothing yet. Returning "0
    products, nothing to do" would leave the one question they are asking —
    is this connection alive — unanswered."""
    sign_in(client, OWNER)
    body = post(client).get_json()

    assert body["queued_products"] == 0
    assert body["queued_jobs"] == 3
    assert {kind for kind, _resource, _at in jobs()} == {"health", "shops", "subscriptions"}


# ---------------------------------------------------------------------------
# Auth, scope and ownership
# ---------------------------------------------------------------------------

def test_a_signed_out_caller_queues_nothing(client):
    response = post(client)

    assert response.status_code == 401
    assert jobs() == []


def test_another_merchant_cannot_sync_this_stores_connection(client):
    sign_in(client, OTHER)
    response = post(client)

    assert response.status_code in {403, 404}
    assert response.get_json()["ok"] is False
    assert jobs() == []


def test_an_unknown_connection_is_refused_rather_than_scheduled(client):
    """An id that names nothing must not reach ``worker.schedule``. A job row
    keyed on a connection that does not exist is one the worker can never
    complete and will retry forever."""
    sign_in(client, OWNER)
    response = post(client, connection_id="conn-does-not-exist")

    assert response.status_code in {403, 404}
    assert jobs() == []


def test_a_request_without_a_scope_is_rejected_rather_than_defaulted(client):
    sign_in(client, OWNER)
    response = client.post(f"{PREFIX}/connections/{CONNECTION}/sync", json={})

    assert response.status_code == 400
    assert jobs() == []


def test_a_request_without_csrf_is_refused(client, monkeypatch):
    """A state change, so it carries CSRF like every other write on this pack."""
    monkeypatch.setattr(routes, "_csrf_ok", lambda: False)
    sign_in(client, OWNER)
    response = post(client)

    assert response.status_code == 403
    assert jobs() == []


def test_another_sellers_products_are_never_queued(client):
    """The ids come from a query scoped to the caller, not from the request — so
    a second seller's source rows sitting under the same connection id must not
    be swept in. This is the difference between "refresh my catalogue" and
    "refresh whatever is attached to this connection"."""
    seed_products(2)
    seed_products(2, seller=OTHER, prefix="other-")
    sign_in(client, OWNER)
    body = post(client).get_json()

    assert body["queued_products"] == 2
    assert not [resource for _kind, resource, _at in jobs() if resource.startswith("other-")]


# ---------------------------------------------------------------------------
# §24 bounded, and honest about being bounded
# ---------------------------------------------------------------------------

def test_a_large_catalogue_is_capped_and_says_so(client, monkeypatch):
    """A merchant told "syncing" about half their catalogue would go hunting for
    a failure that is really a cap, so the truncation is reported rather than
    hidden behind a full-looking count."""
    monkeypatch.setattr(resync, "MAX_PRODUCTS", 2)
    seed_products(5)
    sign_in(client, OWNER)
    body = post(client).get_json()

    assert body["queued_products"] == 2
    assert body["truncated"] is True
    assert body["max_products"] == 2


def test_a_catalogue_exactly_at_the_cap_is_not_reported_truncated(client, monkeypatch):
    """Off-by-one guard on the ``LIMIT MAX + 1`` probe. Reporting truncation for
    a catalogue that fully synced would send the merchant looking for products
    that are not missing."""
    monkeypatch.setattr(resync, "MAX_PRODUCTS", 3)
    seed_products(3)
    sign_in(client, OWNER)
    body = post(client).get_json()

    assert body["queued_products"] == 3
    assert body["truncated"] is False


def test_pressing_twice_does_not_multiply_the_work(client):
    """``ON CONFLICT`` in ``worker.schedule`` means a second tap re-queues the
    same rows. A merchant who taps twice because nothing visibly happened must
    not double the queue the worker has to drain before it reaches them."""
    seed_products(2)
    sign_in(client, OWNER)
    post(client)
    first = jobs()
    second_body = post(client).get_json()

    assert len(jobs()) == len(first)
    assert second_body["queued_products"] == 2
