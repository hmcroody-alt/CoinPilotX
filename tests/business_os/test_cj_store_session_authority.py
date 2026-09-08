"""Which store the CJ connect route acts for, and what it says when it can't.

The failure this file exists to prevent coming back
---------------------------------------------------
A merchant trading as "M&W Store · Open for orders" tapped **Connect to CJ** and
was told *"You're not signed in to this store anymore."* They were signed in,
their store existed, and it was open for orders. Three separate defects stacked
to produce that sentence:

1. ``_csrf_ok`` treated a request as native-safe only when
   ``g.mobile_access_user_id`` was set. That flag is only set when
   ``bot.account_user_id()`` reaches its bearer branch, and it short-circuits on
   the session cookie. The app sends *both*, so the bearer was never verified,
   the flag was never set, and the app — which has no CSRF token to echo — could
   not satisfy the fallback. Every Business OS read worked; every write was 403.
2. The supplier route pack answered ``{"code": …}`` with no ``error_code``, the
   only field the mobile client reads a code out of, so every rejection reached
   the app as no code at all.
3. With no code, the client classified on status alone: 401/403 → UNAUTHORIZED →
   "you're not signed in".

None of the three is visible from either end on its own, which is why the tests
here run the real Flask gate, the real ownership lookup, and the real error
serialisation together.

The security property that must survive all of it: a bearer is only ever
*evidence*, re-verified server-side. A forged one, or a valid one naming a
different user than the cookie, still fails the gate.

Runs alone: sets ``DATABASE_URL`` per test via the fixture.
"""

from types import SimpleNamespace

from flask import Flask, session
import pytest

from services import db
from services import business_os_commerce_routes as commerce_routes
from services import business_os_supplier_routes as routes
from services.business_os.business import schema as business_schema
from services.business_os.store import schema as store_schema
from services.business_os.suppliers import connections, merchant_scope, schema, vault

BASE = "/api/business-os/suppliers/cj"
CSRF = "fixture-cookie-csrf"

#: The reported merchant: an approved marketplace seller with no Business OS
#: workspace. This is the exact shape production is in — the live database has
#: no ``business_os_business`` row for them at all.
MW = "100"
#: A second approved seller. Store isolation is asserted against this one.
OTHER = "200"
#: Approved once, suspended since.
REVOKED = "300"
#: Approved seller who *also* has a workspace: their canonical scope is the
#: workspace, so a cached seller scope is stale rather than forged.
MOVED = "400"
#: Signed in, but not a seller and not a workspace owner.
UNMAPPED = "500"

#: A stand-in for a real signed access token. The gate never parses it — it
#: hands whatever arrived to bot's verifier and believes only the answer.
GOOD_BEARER = "signed.access.token"


class FakeAdapter:
    """Records whether CJ was reached. Never returns a plausible success."""

    def __init__(self):
        self.calls = []

    def register_secrets(self, values):
        self.calls.append("register_secrets")

    def authenticate(self, api_key):
        self.calls.append("authenticate")
        raise AssertionError("CJ must not be contacted before store authorization")


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "authority.sqlite"))
    monkeypatch.setattr(db, "IS_POSTGRES", False)
    monkeypatch.setenv("BUSINESS_OS_SUPPLIERS_CJ", "on")
    monkeypatch.setenv(vault.KEYRING_ENV, "test:" + "13" * 32)
    monkeypatch.setenv(vault.ACTIVE_KEY_ENV, "test")
    monkeypatch.setenv(vault.INDEX_KEY_ENV, "24" * 32)
    business_schema.ensure_schema()
    store_schema.ensure_schema()
    schema.ensure_schema()
    conn = db.connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_sellers (
        user_id INTEGER PRIMARY KEY, display_name TEXT, business_name TEXT, status TEXT)""")
    for actor, name, status in ((MW, "M&W Store", "approved"),
                                (OTHER, "Other Store", "approved"),
                                (REVOKED, "Closed Store", "suspended"),
                                (MOVED, "Moved Store", "approved")):
        conn.execute("INSERT INTO marketplace_sellers (user_id, display_name, status) VALUES (?,?,?)",
                     (actor, name, status))
    conn.execute("INSERT INTO business_os_business (business_id, owner_user_id, display_name, "
                 "created_at, updated_at) VALUES (?,?,?,?,?)",
                 ("biz-moved", MOVED, "Moved Workspace", "2026-01-01T00:00:00Z", "now"))
    conn.execute("INSERT INTO business_os_store_storefront (storefront_id, business_id, name, "
                 "created_at, updated_at) VALUES (?,?,?,?,?)",
                 ("store-moved", "biz-moved", "Moved Workspace", "now", "now"))
    conn.commit()
    conn.close()


@pytest.fixture
def bearer_users():
    """user id the bearer verifier resolves a token to, keyed by token.

    One token per actor, so a test can sign a request as the merchant it is
    about without the write gate becoming the thing under test.
    """
    return {GOOD_BEARER: MW, "token-" + OTHER: OTHER, "token-" + REVOKED: REVOKED,
            "token-" + MOVED: MOVED, "token-" + UNMAPPED: UNMAPPED}


@pytest.fixture
def app(monkeypatch, bearer_users):
    app = Flask(__name__)
    app.secret_key = "synthetic-session-signing-key"
    app.testing = True

    def api_account_user():
        # ``account_user_id`` is the key bot's own session authority uses. The
        # write gate compares the bearer's user against it, so a fixture that
        # invented a different key would make the mismatch check untestable.
        actor = session.get("account_user_id")
        if not actor:
            return None
        return {"user_id": actor, "account_status": "active", "access_enabled": 1}

    def verify_bearer():
        from flask import request
        header = (request.headers.get("Authorization") or "").strip()
        if not header.lower().startswith("bearer "):
            return None
        return bearer_users.get(header.split(" ", 1)[1].strip())

    monkeypatch.setattr(routes, "_bot",
                        lambda: SimpleNamespace(api_account_user=api_account_user))
    monkeypatch.setattr(commerce_routes, "_bot",
                        lambda: SimpleNamespace(
                            api_account_user=api_account_user,
                            account_user_id_from_mobile_access_token=verify_bearer))
    routes.register(app)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, actor=MW):
    with client.session_transaction() as state:
        state["account_user_id"] = actor
        state["csrf_token"] = CSRF


def native_post(client, path=BASE + "/discover-shops", body=None, bearer=GOOD_BEARER, **changes):
    """Exactly the header set the PulseSoc app sends.

    Session cookie (the test client carries it), a bearer, and — the whole
    point — no ``X-CSRF-Token``, because the native client has no CSRF token to
    send and never had one.
    """
    scope = merchant_scope.seller_scope(changes.pop("scope_owner", MW))
    payload = {"business_id": scope[0], "store_id": scope[1], "api_key": "fixture-api-key"}
    payload.update(changes)
    headers = {"X-PulseSoc-Platform": "ios"}
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    return client.post(path, json=body if body is not None else payload, headers=headers)


def spy(monkeypatch, name="discover_shops", result=None):
    seen = []

    def recorder(*args, **kwargs):
        seen.append(args)
        return result if result is not None else {"shops": [], "requires_explicit_shop_selection": True}

    monkeypatch.setattr(connections, name, recorder)
    return seen


# ---------------------------------------------------------------------------
# 1 — the reported flow, end to end
# ---------------------------------------------------------------------------

def test_store_owner_on_the_native_client_reaches_the_supplier_gateway(client, monkeypatch):
    """The bug. Cookie + bearer, no CSRF token, and the write must be allowed."""
    login(client)
    seen = spy(monkeypatch)
    response = native_post(client)
    assert response.status_code == 200, response.get_json()
    assert seen and seen[0][:3] == ("mkt-seller:" + MW, "mkt-seller:" + MW, MW)


def test_the_owner_is_never_told_they_are_signed_out_of_a_store_they_own(client, monkeypatch):
    """§9: M&W Store exists and is open for orders, so no store-authority refusal."""
    login(client)
    spy(monkeypatch)
    assert native_post(client).status_code == 200


def test_a_cookie_only_web_write_still_needs_a_csrf_token(client, monkeypatch):
    login(client)
    calls = spy(monkeypatch)
    response = native_post(client, bearer=None)
    assert response.status_code == 403 and response.get_json()["code"] == "csrf"
    assert calls == []


def test_a_forged_bearer_does_not_pass_the_write_gate(client, monkeypatch):
    login(client)
    calls = spy(monkeypatch)
    response = native_post(client, bearer="forged-not-verified")
    assert response.status_code == 403 and response.get_json()["code"] == "csrf"
    assert calls == []


def test_a_valid_bearer_for_a_different_user_than_the_cookie_is_refused(client, monkeypatch,
                                                                        bearer_users):
    """A genuinely signed token still cannot vote on someone else's session."""
    bearer_users["other-users-token"] = OTHER
    login(client, MW)
    calls = spy(monkeypatch)
    response = native_post(client, bearer="other-users-token")
    assert response.status_code == 403 and response.get_json()["code"] == "csrf"
    assert calls == []


def test_the_gate_fails_closed_when_the_bearer_verifier_is_unavailable(client, monkeypatch):
    """An absent resolver must deny. A gate that opens when it cannot check is not a gate."""
    monkeypatch.setattr(commerce_routes, "_bot", lambda: SimpleNamespace())
    login(client)
    calls = spy(monkeypatch)
    assert native_post(client).status_code == 403
    assert calls == []


def test_a_raising_bearer_verifier_denies_rather_than_admits(client, monkeypatch):
    def explode():
        raise RuntimeError("database down")

    monkeypatch.setattr(commerce_routes, "_bot",
                        lambda: SimpleNamespace(account_user_id_from_mobile_access_token=explode))
    login(client)
    calls = spy(monkeypatch)
    assert native_post(client).status_code == 403
    assert calls == []


def test_bot_still_exposes_the_verifier_the_gate_asks_for():
    """The gate resolves the verifier by name, so a rename disarms it silently.

    It would fail closed — which is safe, and would put every merchant back on
    the message this whole change removes.
    """
    import bot

    assert callable(getattr(bot, "account_user_id_from_mobile_access_token", None))


# ---------------------------------------------------------------------------
# 2 — authentication is not authorization is not staleness
# ---------------------------------------------------------------------------

def test_a_signed_out_request_is_login_required_not_a_store_failure(client, monkeypatch):
    calls = spy(monkeypatch)
    response = native_post(client)
    assert response.status_code == 401 and response.get_json()["error_code"] == "login_required"
    assert calls == []


def test_every_rejection_carries_the_field_the_client_reads(client):
    """``error_code``, not just ``code``. Without it the app sees no code at all."""
    login(client)
    body = native_post(client, bearer=None).get_json()
    assert body["error_code"] == "csrf" and body["code"] == "csrf"


def test_a_store_the_caller_does_not_own_is_refused_and_reveals_nothing(client):
    """Real authorization, not a spy: the refusal has to come from the lookup."""
    login(client, MW)
    response = native_post(client, scope_owner=OTHER)
    assert response.status_code == 404
    assert response.get_json()["error_code"] == "store_not_found"

    # A store id nobody owns answers identically, so existence stays unobservable.
    absent = native_post(client, business_id="mkt-seller:999999",
                         store_id="mkt-seller:999999")
    assert absent.status_code == 404
    assert absent.get_json()["error_code"] == "store_not_found"


def test_a_suspended_seller_is_revoked_not_merely_unapproved(client):
    # The bearer names this actor, so the write gate is satisfied and the
    # refusal is the store's — which is the distinction being tested.
    login(client, REVOKED)
    response = native_post(client, scope_owner=REVOKED, bearer="token-" + REVOKED)
    assert response.status_code == 403
    assert response.get_json()["error_code"] == "store_access_revoked"


def test_a_signed_in_caller_with_no_seller_record_is_a_store_failure_not_a_session_one(client):
    login(client, UNMAPPED)
    response = native_post(client, scope_owner=UNMAPPED, bearer="token-" + UNMAPPED)
    assert response.status_code == 404
    assert response.get_json()["error_code"] == "store_not_found"


def test_a_stale_scope_reaches_the_client_as_stale_over_the_wire(client):
    login(client, MOVED)
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_sellers SET status='closed' WHERE user_id=?", (MOVED,))
        conn.commit()
    finally:
        conn.close()
    response = native_post(client, scope_owner=MOVED, bearer="token-" + MOVED)
    assert response.status_code == 409
    assert response.get_json()["error_code"] == "stale_store_context"


# ---------------------------------------------------------------------------
# 3 — the ownership lookup itself
# ---------------------------------------------------------------------------

def authorize(business_id, store_id, actor, write=True):
    conn = db.connect()
    try:
        return connections._authorize(conn, business_id, store_id, actor,
                                      context={"account_status": "active", "access_enabled": True},
                                      write=write)
    finally:
        conn.close()


def refusal(business_id, store_id, actor):
    with pytest.raises(connections.SupplierConnectionError) as caught:
        authorize(business_id, store_id, actor)
    return caught.value


def test_the_canonical_store_mapping_is_the_seller_record_not_a_new_business():
    """§4/§11: one mapping, derived. Nothing is created to make the flow work."""
    business_id, store_id = merchant_scope.seller_scope(MW)
    assert authorize(business_id, store_id, MW) == MW
    conn = db.connect()
    try:
        owned = conn.execute("SELECT COUNT(*) AS n FROM business_os_business "
                             "WHERE owner_user_id=?", (MW,)).fetchone()
        stores = conn.execute("SELECT COUNT(*) AS n FROM business_os_store_storefront").fetchone()
    finally:
        conn.close()
    assert owned["n"] == 0, "authorizing a seller must not mint a Business OS business"
    assert stores["n"] == 1, "only the pre-seeded workspace storefront may exist"


def test_two_sellers_resolve_to_two_stores_with_no_leakage():
    """§16: no cross-store reach, and no silent 'first store' for either."""
    mine = merchant_scope.seller_scope(MW)
    theirs = merchant_scope.seller_scope(OTHER)
    assert authorize(*mine, MW) == MW
    assert authorize(*theirs, OTHER) == OTHER
    assert refusal(*theirs, MW).code == "store_not_found"
    assert refusal(*mine, OTHER).code == "store_not_found"


def test_revoked_selling_access_still_denies():
    """§17. The merchant had this store; the refusal names the withdrawal."""
    business_id, store_id = merchant_scope.seller_scope(REVOKED)
    denial = refusal(business_id, store_id, REVOKED)
    assert denial.http_status == 403 and denial.code == "store_access_revoked"


def test_a_stale_cached_scope_is_named_as_stale_not_as_a_signed_out_session():
    """§10: the app cached a seller scope; this merchant's canonical store moved.

    They must not be sent to a sign-in screen to repair a client-side cache.
    """
    business_id, store_id = merchant_scope.seller_scope(MOVED)
    conn = db.connect()
    try:
        # The scope only goes stale once the seller record stops backing it —
        # the workspace is now the canonical answer.
        conn.execute("UPDATE marketplace_sellers SET status='closed' WHERE user_id=?", (MOVED,))
        conn.commit()
    finally:
        conn.close()
    denial = refusal(business_id, store_id, MOVED)
    assert denial.code == "stale_store_context" and denial.http_status == 409


def test_staleness_is_never_reported_for_another_merchants_scope():
    """The stale answer describes the caller's own store, so it cannot leak."""
    theirs = merchant_scope.seller_scope(MW)
    assert refusal(*theirs, MOVED).code == "store_not_found"


def test_a_caller_with_no_store_at_all_is_not_told_they_are_signed_out():
    business_id, store_id = merchant_scope.seller_scope(UNMAPPED)
    denial = refusal(business_id, store_id, UNMAPPED)
    assert denial.code == "store_not_found" and denial.http_status == 404


def test_an_unauthenticated_actor_is_an_authentication_failure():
    business_id, store_id = merchant_scope.seller_scope(MW)
    assert refusal(business_id, store_id, None).code == "unauthorized"


# ---------------------------------------------------------------------------
# 4 — nothing reaches CJ, and nothing leaks, on a store-authority refusal
# ---------------------------------------------------------------------------

def test_no_provider_call_and_no_credential_write_before_store_authorization(caplog):
    """§14: a local refusal must not become a CJ round trip or a stored key."""
    adapter = FakeAdapter()
    theirs = merchant_scope.seller_scope(OTHER)
    with pytest.raises(connections.SupplierConnectionError):
        connections.discover_shops(theirs[0], theirs[1], MW, "fixture-api-key",
                                   context={"account_status": "active", "access_enabled": True},
                                   adapter=adapter)
    assert adapter.calls == []
    conn = db.connect()
    try:
        stored = conn.execute("SELECT COUNT(*) AS n "
                              "FROM business_os_supplier_credential_vault").fetchone()
        rows = conn.execute("SELECT COUNT(*) AS n "
                            "FROM business_os_supplier_connections").fetchone()
    finally:
        conn.close()
    assert stored["n"] == 0 and rows["n"] == 0
    assert "fixture-api-key" not in caplog.text


def test_a_refused_request_never_echoes_the_key_it_was_given(client, caplog):
    """§13. The rejection body and the log are both checked, not just the body."""
    login(client, MW)
    response = native_post(client, scope_owner=OTHER, api_key="secret-cj-key-value")
    assert response.status_code == 404
    assert "secret-cj-key-value" not in response.get_data(as_text=True)
    assert "secret-cj-key-value" not in caplog.text
