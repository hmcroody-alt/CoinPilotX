"""``/api/business-os/dropshipping/store-policy`` — the store's import settings over HTTP.

What this file is defending
---------------------------
:mod:`tests.dropshipping.test_dropship_autopublish` proves what the policy *does*:
that an unconfigured store gets the platform margin, that ``auto_publish=False``
lands a priced draft, that ``marketplace_autolist`` is off unless the store turned
it on. All of that is measured by calling ``store_policy.set_policy`` directly with
a connection in hand.

Which means that, until this file existed, there was no way for a merchant to
reach any of it. The policy decided how their products were priced and whether
they went live, and the only writer was a test. A setting nobody can change is a
hardcoded constant wearing a table.

Three things are asserted here that the module-level tests cannot answer.

* **Ownership is proved.** The route takes ``business_id``/``store_id`` from the
  request body, which is exactly the shape that lets one merchant name another's
  store. The refusal has to come from the same authority that guards importing,
  and it has to be a 404 — an authorization failure that distinguishes "not
  yours" from "does not exist" is a store-enumeration oracle.
* **PATCH means patch.** Three independent controls share one row. A write that
  replaced the row wholesale would let the Marketplace toggle overwrite a margin
  the merchant changed thirty seconds earlier with the stale copy the screen was
  holding. Asserted as a sequence, because that is the only way the bug appears.
* **A rejected rule is reported as a rejected rule.** ``pricing.PricingRejected``
  carries no HTTP status, so untranslated it becomes ``supplier_unavailable``
  (503) and a merchant who typed 150% margin is told the supplier is down.

Scope comes from the ``/scope`` route rather than being constructed here. The
seller-backed scope format (``seller:<id>`` in both halves) is
:mod:`merchant_scope`'s business, and a test that hardcoded it would keep passing
if the two disagreed.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="store-policy-route-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from flask import Flask, session  # noqa: E402

from services import business_os_dropshipping_routes as routes  # noqa: E402
from services import db  # noqa: E402
from services.business_os.suppliers import pricing, store_policy  # noqa: E402
from services.business_os.suppliers import schema as connection_schema  # noqa: E402

PREFIX = "/api/business-os/dropshipping"

SELLER = "8101"    # approved seller — the merchant whose policy this is
OTHER = "8102"     # a second approved seller, used only to be refused


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
        for actor, name in ((SELLER, "M&W Store"), (OTHER, "Someone Else")):
            conn.execute(
                "INSERT INTO marketplace_sellers (user_id, display_name, status) "
                "VALUES (?,?,'approved')", (actor, name))
        conn.commit()
    finally:
        conn.close()
    connection_schema.ensure_schema()
    store_policy.ensure_schema()


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "synthetic-session-signing-key"
    app.testing = True

    def user():
        actor = session.get("user_id")
        return {"user_id": actor, "account_status": "active",
                "access_enabled": 1} if actor else None

    monkeypatch.setattr(routes, "_bot", lambda: SimpleNamespace(api_account_user=user))
    # CSRF has its own contract test (tests/protection/test_csrf_contract.py) and
    # its own token plumbing. Stubbed here so these tests measure authorization,
    # not token minting -- and un-stubbed in the one test that is about it.
    monkeypatch.setattr(routes, "_csrf_ok", lambda: True)
    routes.register(app)
    return app.test_client()


def sign_in(client, actor):
    with client.session_transaction() as state:
        state["user_id"] = actor


def scope_of(client, actor):
    """The merchant's own scope, as the client would learn it."""
    sign_in(client, actor)
    body = client.get(PREFIX + "/scope").get_json()
    assert body["status"] == "ok", body
    return {"business_id": body["business_id"], "store_id": body["store_id"]}


def read_policy(client, scope):
    return client.get(PREFIX + "/store-policy", query_string=scope)


def patch_policy(client, scope, **fields):
    return client.patch(PREFIX + "/store-policy", json={**scope, **fields})


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_a_store_that_has_never_been_configured_reads_the_platform_answer(client):
    """Not an empty object, not a 404. The platform's own defaults, flagged as such.

    A client that had to interpret "no row" would have to know the default margin
    to render the settings screen, which is the second copy of the number §10
    forbids.
    """
    scope = scope_of(client, SELLER)
    response = read_policy(client, scope)
    policy = response.get_json()["policy"]

    assert response.status_code == 200
    assert policy["configured"] is False
    assert policy["pricing_source"] == store_policy.SOURCE_PLATFORM
    assert policy["pricing_rule"] == {
        "type": pricing.TARGET_MARGIN,
        "value": store_policy.PLATFORM_DEFAULT_TARGET_MARGIN}
    # The two behavioural defaults, stated over the wire: imports finish
    # themselves, and finishing does not mean broadcasting (§19/§20).
    assert policy["auto_publish"] is True
    assert policy["marketplace_autolist"] is False


def test_reading_configures_nothing(client):
    """A GET must not write the default it just reported.

    Persisting on read would turn "nobody has expressed a preference" into "this
    store chose 45%", and a later change to the platform default would then skip
    every store that had ever opened the screen.
    """
    scope = scope_of(client, SELLER)
    assert read_policy(client, scope).status_code == 200

    conn = db.connect()
    try:
        rows = conn.execute(f"SELECT COUNT(*) AS n FROM {store_policy.TABLE}").fetchone()["n"]
    finally:
        conn.close()
    assert rows == 0


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def test_a_merchant_can_set_the_margin_their_imports_price_at(client):
    scope = scope_of(client, SELLER)
    response = patch_policy(client, scope,
                            pricing_rule={"type": pricing.TARGET_MARGIN, "value": 60})
    policy = response.get_json()["policy"]

    assert response.status_code == 200
    assert policy["pricing_rule"] == {"type": pricing.TARGET_MARGIN, "value": 60.0}
    assert policy["pricing_source"] == store_policy.SOURCE_STORE
    assert policy["configured"] is True
    # And it is the answer the next read gives, not just the one the write echoed.
    assert read_policy(client, scope).get_json()["policy"]["pricing_rule"]["value"] == 60.0


def test_a_merchant_can_turn_auto_publish_off(client):
    scope = scope_of(client, SELLER)
    assert patch_policy(client, scope, auto_publish=False).status_code == 200
    assert read_policy(client, scope).get_json()["policy"]["auto_publish"] is False


def test_a_merchant_can_opt_into_marketplace_distribution(client):
    """§19/§20. The one-tap import publishes to the store; this is the other switch."""
    scope = scope_of(client, SELLER)
    assert patch_policy(client, scope, marketplace_autolist=True).status_code == 200
    assert read_policy(client, scope).get_json()["policy"]["marketplace_autolist"] is True


def test_changing_one_setting_leaves_the_others_alone(client):
    """The reason this route is PATCH. Asserted as a sequence, because that is the bug.

    A margin set on one screen, then a toggle flipped on another. If the second
    write carried a whole policy object, the margin would revert to whatever the
    toggle's screen last read -- silently, and only for merchants who changed both.
    """
    scope = scope_of(client, SELLER)
    patch_policy(client, scope, pricing_rule={"type": pricing.MULTIPLIER, "value": 3})
    patch_policy(client, scope, marketplace_autolist=True)
    patch_policy(client, scope, auto_publish=False)

    policy = read_policy(client, scope).get_json()["policy"]
    assert policy["pricing_rule"] == {"type": pricing.MULTIPLIER, "value": 3.0}
    assert policy["marketplace_autolist"] is True
    assert policy["auto_publish"] is False


def test_an_empty_patch_is_refused(client):
    """Nothing named means nothing meant. A 200 here would report a policy the
    caller never asked about as though they had just set it."""
    scope = scope_of(client, SELLER)
    assert patch_policy(client, scope).status_code == 400


# ---------------------------------------------------------------------------
# The shipping allowance — §12
# ---------------------------------------------------------------------------
#
# This route is the *only* way a freight figure enters the system. §12's whole
# arithmetic (``tests/dropshipping/test_dropship_landed_cost.py``) is reachable
# by exactly one merchant action, and it is a PATCH here — the import route
# deliberately accepts no cost, and there is no platform default. So a route that
# dropped the field would leave landed-cost pricing implemented and unreachable,
# which is the §31 failure: a feature that exists only in tests.

def test_a_merchant_can_declare_what_their_supplier_charges_to_ship(client):
    scope = scope_of(client, SELLER)
    response = patch_policy(client, scope, shipping_allowance_cents=900)
    policy = response.get_json()["policy"]

    assert response.status_code == 200
    assert policy["shipping_allowance_cents"] == 900
    assert policy["shipping_allowance_source"] == store_policy.SOURCE_STORE
    # And it is the answer the next read gives, not just the one the write echoed.
    assert read_policy(client, scope).get_json()["policy"][
        "shipping_allowance_cents"] == 900


def test_an_unconfigured_store_reports_no_allowance_rather_than_a_free_one(client):
    """``None`` over the wire, not ``0``.

    A client reading ``0`` would render "Shipping: free" for every store that has
    never opened the screen, and the margin beside it would be the item margin
    claiming to be a landed one.
    """
    scope = scope_of(client, SELLER)
    policy = read_policy(client, scope).get_json()["policy"]
    assert policy["shipping_allowance_cents"] is None
    assert policy["shipping_allowance_source"] == store_policy.SOURCE_PLATFORM


def test_a_merchant_can_take_the_declaration_back(client):
    """``None`` already means "leave this alone", so un-declaring needs a third value.

    It has to survive a JSON round trip to get here at all, which is why
    ``CLEAR_ALLOWANCE`` is a string rather than a sentinel object.
    """
    scope = scope_of(client, SELLER)
    patch_policy(client, scope, shipping_allowance_cents=900)
    response = patch_policy(client, scope,
                            shipping_allowance_cents=store_policy.CLEAR_ALLOWANCE)
    assert response.status_code == 200
    assert response.get_json()["policy"]["shipping_allowance_cents"] is None
    assert read_policy(client, scope).get_json()["policy"][
        "shipping_allowance_cents"] is None


def test_declaring_shipping_leaves_the_margin_alone(client):
    scope = scope_of(client, SELLER)
    patch_policy(client, scope, pricing_rule={"type": pricing.MULTIPLIER, "value": 3})
    patch_policy(client, scope, shipping_allowance_cents=900)
    policy = read_policy(client, scope).get_json()["policy"]
    assert policy["pricing_rule"] == {"type": pricing.MULTIPLIER, "value": 3.0}
    assert policy["shipping_allowance_cents"] == 900


def test_true_is_not_nine_dollars_of_freight(client):
    """``isinstance(True, int)`` is ``True``, which is why the route uses ``type(...) is``.

    ``{"shipping_allowance_cents": true}`` is a shape a buggy client sends, and
    accepting it stores a one-cent freight charge — small enough that no margin
    badge ever changes, so nothing would ever surface it.
    """
    scope = scope_of(client, SELLER)
    # `"0"` is in the list deliberately: it is the shape a text input produces,
    # and it is the one wrong value that would otherwise be stored as the
    # perfectly plausible "shipping is free".
    for value in (True, False, "900", "0", 900.5, [], {}):
        response = patch_policy(client, scope, shipping_allowance_cents=value)
        assert response.status_code == 400, (value, response.get_json())
    assert read_policy(client, scope).get_json()["policy"][
        "shipping_allowance_cents"] is None


def test_a_negative_allowance_is_refused_as_an_allowance(client):
    """A 400 naming the field, not a 503 blaming the supplier.

    ``PricingRejected`` carries no HTTP status, so an untranslated one reaches
    ``_error`` as ``supplier_unavailable``. The merchant typed a number; they are
    entitled to be told it was the number. The route pre-checks the type for the
    same reason.
    """
    scope = scope_of(client, SELLER)
    response = patch_policy(client, scope, shipping_allowance_cents=-100)
    body = response.get_json()
    assert response.status_code == 400
    assert body["error_code"] == "invalid_shipping_allowance"


def test_zero_is_accepted_because_it_is_a_real_answer(client):
    """A merchant whose supplier bundles freight into the item price says so this way.

    Refusing zero would leave them no way to distinguish "shipping is included"
    from "we do not know", and those two produce different margins and different
    badges.
    """
    scope = scope_of(client, SELLER)
    assert patch_policy(client, scope, shipping_allowance_cents=0).status_code == 200
    policy = read_policy(client, scope).get_json()["policy"]
    assert policy["shipping_allowance_cents"] == 0
    assert policy["shipping_allowance_source"] == store_policy.SOURCE_STORE


def test_one_merchant_cannot_declare_anothers_freight(client):
    """Consequential: it reprices a stranger's whole catalogue on the next sync."""
    victim = scope_of(client, SELLER)
    sign_in(client, OTHER)
    assert patch_policy(client, victim, shipping_allowance_cents=900).status_code == 404
    sign_in(client, SELLER)
    assert read_policy(client, victim).get_json()["policy"][
        "shipping_allowance_cents"] is None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_a_rule_the_pricing_engine_rejects_says_so(client):
    """A 400 naming the rule, not a 503 blaming the supplier.

    `pricing.PricingRejected` has no `http_status`, so an untranslated one reaches
    `_error` as `supplier_unavailable`. The merchant typed a number; they are
    entitled to be told it was the number.
    """
    scope = scope_of(client, SELLER)
    response = patch_policy(client, scope,
                            pricing_rule={"type": pricing.TARGET_MARGIN, "value": 100})
    body = response.get_json()

    assert response.status_code == 400
    assert body["code"] == "invalid_pricing_rule"
    # pulseApi reads a rejection's code out of `error_code`; `code` alone arrives
    # as no code at all and collapses to the generic failure copy.
    assert body["error_code"] == "invalid_pricing_rule"


def test_a_rejected_rule_writes_nothing(client):
    scope = scope_of(client, SELLER)
    patch_policy(client, scope, pricing_rule={"type": pricing.TARGET_MARGIN, "value": 60})
    patch_policy(client, scope, pricing_rule={"type": "SOMETHING_INVENTED", "value": 2})

    assert read_policy(client, scope).get_json()["policy"]["pricing_rule"] == {
        "type": pricing.TARGET_MARGIN, "value": 60.0}


def test_a_non_boolean_toggle_is_refused(client):
    """`"false"` is a string, and a truthy one.

    Accepting it would switch Marketplace distribution *on* for a client that
    meant off — which under §19/§20 is the one wrong answer that is visible to
    everyone on PulseSoc rather than just to the merchant.
    """
    scope = scope_of(client, SELLER)
    for value in ("false", 0, 1, "true", "yes"):
        response = patch_policy(client, scope, marketplace_autolist=value)
        assert response.status_code == 400, (value, response.get_json())
    assert read_policy(client, scope).get_json()["policy"]["marketplace_autolist"] is False


def test_one_merchant_cannot_read_anothers_policy(client):
    """The store ids are in the request, so this is the attack the route invites.

    404, not 403: a refusal that says "exists, but not yours" lets a caller
    enumerate other merchants' stores.
    """
    victim = scope_of(client, SELLER)
    sign_in(client, OTHER)
    response = read_policy(client, victim)
    assert response.status_code == 404


def test_one_merchant_cannot_write_anothers_policy(client):
    """The consequential half. This would price a stranger's whole catalogue."""
    victim = scope_of(client, SELLER)
    sign_in(client, OTHER)
    response = patch_policy(client, victim, marketplace_autolist=True)
    assert response.status_code == 404

    conn = db.connect()
    try:
        rows = conn.execute(f"SELECT COUNT(*) AS n FROM {store_policy.TABLE}").fetchone()["n"]
    finally:
        conn.close()
    assert rows == 0, "the refused write reached the table"


def test_a_signed_out_caller_is_refused(client):
    scope = {"business_id": "seller:8101", "store_id": "seller:8101"}
    assert read_policy(client, scope).status_code == 401
    assert patch_policy(client, scope, auto_publish=False).status_code == 401


def test_a_write_without_csrf_is_refused(client, monkeypatch):
    scope = scope_of(client, SELLER)
    monkeypatch.setattr(routes, "_csrf_ok", lambda: False)
    assert patch_policy(client, scope, auto_publish=False).status_code == 403
    # And the read is unaffected: CSRF guards state changes, not lookups.
    assert read_policy(client, scope).status_code == 200


def test_a_server_without_the_supplier_feature_says_so(client, monkeypatch):
    scope = scope_of(client, SELLER)
    monkeypatch.delenv("BUSINESS_OS_SUPPLIERS_CJ")
    response = read_policy(client, scope)
    assert response.status_code == 404
    assert response.get_json()["error_code"] == "disabled"


def test_the_route_leaks_no_credential_shaped_field(client):
    scope = scope_of(client, SELLER)
    body = read_policy(client, scope).get_json()
    assert not {"api_key", "access_token", "refresh_token", "open_id"} & set(body["policy"])
