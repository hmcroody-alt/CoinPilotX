"""Choosing a CJ shop after connecting -- the transition that did not exist.

Connecting without a CJ shop is normal: importing products needs none, and
demanding one made an ordinary CJ account unconnectable. Fulfilling needs one.
Between those two facts there was no door. ``connect_cj`` refuses to change an
existing binding, and "no shop" is a binding for that purpose, so reconnecting
with a shop answered ``connection_binding_conflict``; ``create_intent`` answered
``shop_binding_required`` forever. A merchant could import, publish and sell,
then refuse every one of their own orders with no recorded way out.

These tests are written from the surface that refused. The first one asserts the
whole path -- refusal, bind, order, dispatch -- because that is the claim being
made, and asserting ``bind_shop`` returns a shop id would not have been it.

The second theme is that binding asks the question dispatch asks. A CJ "shop"
may be a Shopify or Woo storefront; only CJ's own API app produces one an API
order can reach, and CJ addresses orders by shop *name*. Those conditions lived
only inside ``dispatch``, so a merchant could bind a shop, be told yes, and find
out at the first order it was never a shop orders could go to. Each refusal
below is therefore measured twice: once where the choice is made, once where it
is spent.
"""
import json
import time

import pytest

from services import db
from services.business_os.suppliers import connections as svc, fulfillment as f
from tests.business_os.test_cj_connections import FakeAdapter, SECRETS, connect, database
from tests.business_os.test_cj_fulfillment import outbox, ready

SHOP = "cj-shop-a"
OTHER = "cj-shop-b"

#: What the fixture's CJ account looks like by default: one active shop created
#: by CJ's API app. `platform` is the field that decides whether an API order
#: can reach it at all.
API_SHOP = {"shop_id": SHOP, "name": "Same name", "platform": "API", "status": 1}


def shop(**overrides):
    return API_SHOP | overrides


def unbind(connection_id):
    """Return the connection to the state a shopless connect leaves it in.

    The same UPDATE ``test_a_connection_with_no_bound_cj_shop_cannot_place_an_order``
    uses. Reached here by writing the column rather than by connecting without a
    shop so the surrounding fixture -- product binding, quote snapshot, canonical
    order -- stays exactly the one the fulfilment suite already proves out.
    """
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_connections SET external_shop_id='' WHERE id=?",
                 (connection_id,))
    conn.commit()
    conn.close()


def force_bind(connection_id, shop_id):
    """Bind without asking, to put dispatch in front of a binding bind refused.

    Only for the dispatch half of a two-surface assertion. A test that used this
    where it means to exercise ``bind_shop`` would be testing nothing.
    """
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_connections SET external_shop_id=? WHERE id=?",
                 (shop_id, connection_id))
    conn.commit()
    conn.close()


def bound(connection_id):
    conn = db.connect()
    try:
        return conn.execute("SELECT external_shop_id FROM business_os_supplier_connections "
                            "WHERE id=?", (connection_id,)).fetchone()["external_shop_id"]
    finally:
        conn.close()


def bind(connection, adapter, shop_id=SHOP, actor="100"):
    return svc.bind_shop(connection["id"], "biz-a", "store-a", actor, shop_id, adapter=adapter)


def dispatch(connection, adapter):
    claimed = f.claim(now=time.time() + .1)
    meta = svc.worker_connection(connection["id"], "biz-a", "store-a")["connection"]
    return f.dispatch(claimed, adapter, meta)


def test_binding_is_the_transition_an_unbound_connection_had_no_way_to_make(ready):
    """The whole path, from the refusal through to a placed sandbox order.

    The reconnect assertion in the middle is the part that makes this a trap
    rather than a missing convenience: the one operation that writes
    ``external_shop_id`` refuses to write it onto a connection that already
    exists, so before ``bind_shop`` there was no sequence of calls at all that
    moved a connected merchant from shopless to fulfilling.
    """
    adapter, connection, request = ready
    unbind(connection["id"])

    with pytest.raises(f.FulfillmentError, match="shop_binding_required"):
        f.create_intent(**request)

    # Reconnecting is not the way out, and never was.
    with pytest.raises(svc.SupplierConnectionError) as reconnect:
        connect(FakeAdapter())
    assert reconnect.value.code == "connection_binding_conflict"
    assert bound(connection["id"]) == ""

    result = bind(connection, adapter)
    assert result["external_shop_id"] == SHOP and result["status"] == "CONNECTED"

    intent = f.create_intent(**request)
    assert outbox(intent["intent_id"])["state"] == "READY"
    assert dispatch(connection, adapter) == "UNKNOWN"  # sent, awaiting read-back
    assert adapter.created and adapter.created[0]["storeName"] == API_SHOP["name"]


def test_binding_records_the_shop_the_order_is_then_checked_against(ready):
    """The bound id is what ``_validate_observed`` compares CJ's answer to.

    ``create_intent`` copies ``external_shop_id`` onto the intent and the
    read-back refuses any provider order that came back on a different shop.
    Asserting the column alone would not have shown that the value travels.
    """
    adapter, connection, request = ready
    unbind(connection["id"])
    bind(connection, adapter)
    intent_id = f.create_intent(**request)["intent_id"]
    conn = db.connect()
    try:
        row = dict(conn.execute("SELECT * FROM business_os_supplier_intents WHERE id=?",
                                (intent_id,)).fetchone())
    finally:
        conn.close()
    assert row["external_shop_id"] == SHOP
    with pytest.raises(f.FulfillmentError, match="provider_shop_mismatch"):
        f._validate_observed(row, {"items": []}, {"order_id": "1", "shop_id": OTHER,
                                                  "external_order_ref": row["external_order_ref"]})


def test_a_shop_this_credential_does_not_list_cannot_be_bound(ready):
    adapter, connection, _ = ready
    unbind(connection["id"])
    with pytest.raises(svc.SupplierConnectionError) as failure:
        bind(connection, adapter, OTHER)
    assert failure.value.code == "shop_not_authorized"
    assert bound(connection["id"]) == ""


def test_a_shop_cj_has_disabled_cannot_be_bound(ready):
    """``status`` is CJ's own verdict; an inactive shop is not a destination."""
    adapter, connection, _ = ready
    adapter.shops = [shop(status=0)]
    unbind(connection["id"])
    with pytest.raises(f.FulfillmentError, match="api_shop_binding_required"):
        bind(connection, adapter)
    assert bound(connection["id"]) == ""


def test_a_storefront_that_cannot_receive_api_orders_cannot_be_bound(ready):
    """The bind-time and dispatch-time answers, both measured, on one account.

    A Shopify storefront is a perfectly real CJ shop. It is not one an order
    placed over the API arrives at. Before this the first half of this test
    passed -- binding said yes -- and the merchant met the second half instead,
    one lost order later.
    """
    adapter, connection, request = ready
    adapter.shops = [shop(platform="Shopify")]
    unbind(connection["id"])

    with pytest.raises(f.FulfillmentError, match="api_shop_binding_required"):
        bind(connection, adapter)
    assert bound(connection["id"]) == ""

    # And the same account, bound anyway, is refused where it would be spent.
    force_bind(connection["id"], SHOP)
    intent = f.create_intent(**request)
    assert dispatch(connection, adapter) == "BLOCKED"
    assert outbox(intent["intent_id"])["last_error"] == "preflight_blocked"
    assert adapter.created == []


def test_two_shops_sharing_a_name_cannot_be_bound(ready):
    """CJ addresses the order by name, so a duplicate name has no destination."""
    adapter, connection, request = ready
    adapter.shops = [API_SHOP, shop(shop_id=OTHER)]
    unbind(connection["id"])

    with pytest.raises(f.FulfillmentError, match="ambiguous_shop_name"):
        bind(connection, adapter)
    assert bound(connection["id"]) == ""

    force_bind(connection["id"], SHOP)
    intent = f.create_intent(**request)
    assert dispatch(connection, adapter) == "BLOCKED"
    assert outbox(intent["intent_id"])["last_error"] == "preflight_blocked"
    assert adapter.created == []


def test_an_existing_binding_is_never_silently_replaced(ready):
    """None-to-one only. Re-stating the same choice is not a change."""
    adapter, connection, _ = ready
    adapter.shops = [API_SHOP, shop(shop_id=OTHER, name="Another name")]
    assert bound(connection["id"]) == SHOP

    assert bind(connection, adapter, SHOP)["external_shop_id"] == SHOP

    with pytest.raises(svc.SupplierConnectionError) as failure:
        bind(connection, adapter, OTHER)
    assert failure.value.code == "connection_binding_conflict"
    assert bound(connection["id"]) == SHOP


def test_the_shop_list_marks_which_shops_can_take_orders(ready):
    """The merchant sees the verdict before choosing, not after selling.

    ``fulfillable`` is ``dispatch_shop``'s answer for that shop, so the list and
    the bind cannot disagree about what is choosable.
    """
    adapter, connection, _ = ready
    adapter.shops = [API_SHOP, shop(shop_id=OTHER, name="Storefront", platform="Shopify"),
                     shop(shop_id="cj-shop-c", name="Closed", status=0)]
    result = svc.connection_shops(connection["id"], "biz-a", "store-a", "100", adapter=adapter)
    assert result["external_shop_id"] == SHOP
    assert {s["shop_id"]: s["fulfillable"] for s in result["shops"]} == {
        SHOP: True, OTHER: False, "cj-shop-c": False}
    assert {s["unfulfillable_reason"] for s in result["shops"] if not s["fulfillable"]} == {
        "api_shop_binding_required"}
    # No credential, token or account id rides along with the shop list.
    rendered = json.dumps(result, default=str)
    assert not any(secret in rendered for secret in SECRETS.values())


def test_an_account_that_owns_no_storefront_reads_as_no_shops_not_as_an_error(ready):
    """The most likely outcome of opening this screen, and it used to be a 500.

    A CJ account with no external storefront is the normal shape for selling
    through PulseSoc -- PulseSoc *is* the storefront -- and it is the state of
    the live connection today. CJ answers ``shop/getShops`` for such an account
    with a business code its own documentation does not list; the transport
    correctly refuses to interpret it and reports ``SUPPLIER_REJECTED`` (422).

    ``_verify`` already decided this for connecting. Reading the list had never
    been asked, because nothing called it. Left as it was, shipping the merchant
    screen would have shipped "Something went wrong" as the *ordinary* answer:
    422 matches none of the client's status classes, so it renders bare.
    """
    adapter, connection, _ = ready
    adapter.shops_error = svc.SupplierError("SUPPLIER_REJECTED", http_status=422)
    result = svc.connection_shops(connection["id"], "biz-a", "store-a", "100", adapter=adapter)
    assert result["shops"] == []
    # And the merchant's own binding still reads back. An unreadable live list
    # says nothing about what this connection already chose.
    assert result["external_shop_id"] == SHOP


@pytest.mark.parametrize("code,status", [("RATE_LIMITED", 429), ("PROVIDER_UNAVAILABLE", 503),
                                         ("REAUTH_REQUIRED", 401)])
def test_a_list_we_could_not_read_is_not_reported_as_a_list_with_nothing_in_it(ready, code, status):
    """"We could not ask" and "you own none" are different, and the fix is narrow.

    ``_verify`` survives *any* ``SupplierError`` because there the shop list is
    irrelevant -- importing needs none. Here the list is the entire answer, so
    collapsing a throttle, an outage or a dead credential into "no shops" prints
    a false instruction: it sends a merchant to the CJ console to create a
    storefront when the truth is "ask again in a minute" or "your key is being
    refused". Only a rejection -- CJ answered, and the answer was not a list --
    is an empty list.
    """
    adapter, connection, _ = ready
    adapter.shops_error = svc.SupplierError(code, http_status=status)
    with pytest.raises(svc.SupplierError) as failure:
        svc.connection_shops(connection["id"], "biz-a", "store-a", "100", adapter=adapter)
    assert failure.value.code == code


def test_a_shop_list_that_echoes_our_own_secret_is_never_softened_into_no_shops(ready):
    """The leak guard is ours, not CJ's, and survivability does not reach it.

    ``_safe_shops`` raises ``SupplierConnectionError`` for a response that echoes
    a credential back inside a shop name. That is not a provider verdict to be
    interpreted -- it is our refusal to render the response at all -- so it must
    propagate even though the softened path sits right beside it.
    """
    adapter, connection, _ = ready
    adapter.shops = [shop(name="leaked=" + SECRETS["access_token"])]
    with pytest.raises(svc.SupplierConnectionError) as failure:
        svc.connection_shops(connection["id"], "biz-a", "store-a", "100", adapter=adapter)
    assert failure.value.code == "unsafe_provider_response"
    assert SECRETS["access_token"] not in str(failure.value)


def test_binding_still_refuses_a_shop_list_it_cannot_read(ready):
    """The security half, restated where the softening could have leaked into it.

    Reading may survive a rejection because nothing is authorized by looking.
    Choosing may not: an unreadable list that bound anyway would be treating "we
    could not ask" as "you are allowed", which is the tenant check ``bind_shop``
    exists to perform. This is the same rule ``_verify`` follows for a connect
    that names a shop.
    """
    adapter, connection, _ = ready
    unbind(connection["id"])
    adapter.shops_error = svc.SupplierError("SUPPLIER_REJECTED", http_status=422)
    with pytest.raises(svc.SupplierError):
        bind(connection, adapter)
    assert bound(connection["id"]) == ""


def test_a_member_without_write_access_cannot_bind(ready):
    """Binding decides where this store's money goes; viewers do not decide it."""
    adapter, connection, _ = ready
    unbind(connection["id"])
    with pytest.raises(svc.SupplierConnectionError):
        bind(connection, adapter, SHOP, actor="300")
    assert bound(connection["id"]) == ""


def test_a_shop_id_that_is_not_a_usable_string_is_refused(ready):
    adapter, connection, _ = ready
    unbind(connection["id"])
    for candidate in ("", "   ", None, 7, "x" * 257):
        with pytest.raises(svc.SupplierConnectionError) as failure:
            bind(connection, adapter, candidate)
        assert failure.value.code == "shop_required"
    assert bound(connection["id"]) == ""


def test_a_shopless_connection_still_imports_and_the_refusal_is_only_fulfilment(ready):
    """The point of allowing a shopless connect is not undone by any of this.

    If binding had become a precondition for the supplier gateway generally,
    this fix would have re-broken the account shape it exists to support.
    """
    adapter, connection, _ = ready
    unbind(connection["id"])
    result = svc.connection_shops(connection["id"], "biz-a", "store-a", "100", adapter=adapter)
    assert result["external_shop_id"] == ""
    assert [s["shop_id"] for s in result["shops"]] == [SHOP]
    assert svc.get_connection(connection["id"], "biz-a", "store-a", "100")["status"] == "CONNECTED"
