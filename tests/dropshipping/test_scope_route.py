"""``GET /api/business-os/dropshipping/scope`` — the route the client trusts.

:mod:`tests.dropshipping.test_merchant_scope` proves the resolution rule. This
file proves the route around it, which is where the reported bug would come
back: the rule can be perfect and still reach the merchant as "you need a
business" if the route reports it wrong.

Three things are asserted that the resolver alone cannot answer.

* The merchant in the bug report — an approved seller with no Business OS
  workspace — gets ``ok`` over HTTP, with the store name buyers see.
* A merchant with no store gets ``missing`` with a *specific* gap, not a generic
  failure, because the client draws a different screen for each one.
* A deployment with the supplier feature switched off says so. It answers
  ``disabled``, and the one thing it must never do is answer as though the
  merchant's own setup were the problem.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="scope-route-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from flask import Flask, session  # noqa: E402

from services import business_os_dropshipping_routes as routes  # noqa: E402
from services import db  # noqa: E402
from services.business_os.suppliers import schema as connection_schema  # noqa: E402

PREFIX = "/api/business-os/dropshipping"

SELLER = "8101"      # approved seller, no Business OS workspace — the bug
PENDING = "8102"     # applied, not approved yet
NOBODY = "8103"      # no store of any kind


@pytest.fixture(autouse=True)
def database():
    open(_DB_PATH, "w").close()
    conn = db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_sellers (
            user_id INTEGER PRIMARY KEY, display_name TEXT, business_name TEXT,
            status TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_business (
            business_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
            display_name TEXT, legal_name TEXT, created_at TEXT,
            status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_store_storefront (
            storefront_id TEXT PRIMARY KEY, business_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active')""")
        conn.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, business_name, status) "
            "VALUES (?,?,?,'approved')", (SELLER, "M&W Store", "M and W Trading Ltd"))
        conn.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status) "
            "VALUES (?,?,'pending')", (PENDING, "Later Store"))
        conn.commit()
    finally:
        conn.close()
    connection_schema.ensure_schema()


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


def scope(client):
    return client.get(PREFIX + "/scope")


def test_the_reported_merchant_is_let_in_with_the_name_buyers_see(client):
    """A seller trading as "M&W Store" and nothing else. This is the whole bug."""
    sign_in(client, SELLER)
    response = scope(client)
    body = response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True and body["status"] == "ok"
    assert body["source"] == "MARKETPLACE_SELLER"
    assert body["store_name"] == "M&W Store"
    # The scope is usable as-is: both halves present, and the same pair the
    # supplier routes will re-verify ownership against.
    assert body["business_id"] and body["store_id"]
    assert "gap" not in body


def test_the_route_creates_nothing_to_answer(client):
    """The forbidden fix, asserted against. No workspace is conjured up."""
    sign_in(client, SELLER)
    assert scope(client).status_code == 200

    conn = db.connect()
    try:
        businesses = conn.execute("SELECT COUNT(*) AS n FROM business_os_business").fetchone()["n"]
        storefronts = conn.execute(
            "SELECT COUNT(*) AS n FROM business_os_store_storefront").fetchone()["n"]
    finally:
        conn.close()
    assert (businesses, storefronts) == (0, 0)


def test_a_merchant_with_no_store_and_one_awaiting_review_are_told_different_things(client):
    """Two absences that need two screens. Collapsing them is the old defect."""
    sign_in(client, NOBODY)
    absent = scope(client).get_json()

    sign_in(client, PENDING)
    waiting = scope(client).get_json()

    assert absent["status"] == "missing" and absent["gap"] == "NO_STORE"
    assert waiting["status"] == "missing" and waiting["gap"] == "STORE_PENDING_REVIEW"
    assert absent["gap"] != waiting["gap"]


def test_a_missing_scope_is_not_an_error_status(client):
    """`missing` is a merchant state, not a failure.

    Answering 4xx here would make the client's error path own the screen, and
    the error path cannot tell a merchant which of the three gaps they have.
    """
    sign_in(client, NOBODY)
    response = scope(client)
    assert response.status_code == 200 and response.get_json()["ok"] is True


def test_a_signed_out_caller_is_refused(client):
    assert scope(client).status_code == 401


def test_a_server_without_the_supplier_feature_says_so(client, monkeypatch):
    """§10. The blocker is this deployment, and it must be reported as itself.

    The client keys its "not enabled on this server" copy off `disabled`. If this
    route ever answered something else here — or worse, answered a scope gap —
    the merchant would be sent to fix a store that is already fine.
    """
    monkeypatch.delenv("BUSINESS_OS_SUPPLIERS_CJ")
    sign_in(client, SELLER)
    response = scope(client)

    assert response.status_code == 404
    assert response.get_json()["code"] == "disabled"


def test_the_route_leaks_no_credential_shaped_field(client):
    sign_in(client, SELLER)
    body = scope(client).get_json()
    assert not {"api_key", "access_token", "refresh_token", "open_id"} & set(body)
