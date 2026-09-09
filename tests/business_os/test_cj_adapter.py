"""Exact CJ transport/normalization contracts using synthetic response fixtures."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from services.business_os.suppliers.cj import AuthBundle, BASE_URL, CJAdapter, RequestsTransport
from services.business_os.suppliers.errors import SupplierError

PID, VID, SHOP, OPEN_ID = "1001", "2001", "3001", "900000000000000001"
API_KEY, ACCESS, REFRESH = "fixture-cj-api-key", "fixture-cj-access-token", "fixture-cj-refresh-token"


class FakeQuota:
    def __init__(self):
        self.calls, self.observations, self.penalties, self.rebindings = [], [], [], []
        self.block_once = None

    def reserve_request(self, account_ref, **options):
        self.calls.append((account_ref, options))
        if self.block_once is not None:
            failure, self.block_once = self.block_once, None
            raise failure
        return len(self.calls)

    def observe(self, account_ref, points, **kwargs):
        self.observations.append((account_ref, points, kwargs))

    def penalize(self, account_ref, delay):
        self.penalties.append((account_ref, delay))
        return 90

    def rebind_account(self, old, new):
        self.rebindings.append((old, new))


class Response:
    def __init__(self, data=None, *, status=200, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = body if body is not None else {"code": 200, "result": True, "data": data}
        self.content = json.dumps(self.body).encode()

    def json(self):
        return self.body


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_adapter(*responses, environment="SANDBOX", token=ACCESS):
    transport, quota, sleeps = Transport(responses), FakeQuota(), []
    adapter = CJAdapter(access_token=token, account_ref="fixture-account", quota=quota,
                        transport=transport, environment=environment, sleep=sleeps.append)
    return adapter, transport, quota, sleeps


def token_data(**changes):
    data = {"accessToken": ACCESS, "refreshToken": REFRESH, "openId": OPEN_ID,
            "accessTokenExpiryDate": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            "refreshTokenExpiryDate": (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()}
    data.update(changes)
    return data


def variant(**changes):
    data = {"pid": PID, "vid": VID, "variantSku": "FIX-SKU", "variantNameEn": "Fixture variant",
            "variantKey": "red-large", "variantSellPrice": "4.25", "variantWeight": "100"}
    data.update(changes)
    return data


def product(**changes):
    data = {"pid": PID, "productNameEn": "Fixture product", "productSku": "FIX-PRODUCT",
            "sellPrice": "4.25", "variants": [variant()], "productProEnSet": ["ORDINARY"]}
    data.update(changes)
    return data


def shipping_payload():
    return {"reqDTOS": [{"srcAreaCode": "CN", "destAreaCode": "US", "weight": 100,
                         "productProp": ["ORDINARY"], "skuList": ["FIX-SKU"],
                         "freightTrialSkuList": [{"sku": "FIX-SKU", "vid": VID, "skuQuantity": 1}]}]}


def order_payload(**changes):
    data = {"orderNumber": "ps-fixture-order", "shippingCountryCode": "US", "shippingCountry": "United States",
            "shippingProvince": "CA", "shippingCity": "Test City", "shippingCustomerName": "Synthetic Customer",
            "shippingAddress": "1 Test Street", "logisticName": "Fixture Channel", "fromCountryCode": "CN",
            "storeName": "Fixture shop", "payType": 3, "orderFlow": 1, "isSandbox": 1,
            "products": [{"vid": VID, "quantity": 1}]}
    data.update(changes)
    return data


def order_data(**changes):
    data = {"orderId": "cj-fixture-order", "orderNum": "ps-fixture-order", "orderStatus": "UNPAID",
            "shopId": SHOP, "isSandbox": 1, "orderAmount": "4.25", "productList": [{"vid": VID, "quantity": 1}]}
    data.update(changes)
    return data


def assert_call(transport, path, method="GET", *, index=0):
    actual_method, url, options = transport.calls[index]
    assert actual_method == method and url == BASE_URL + "/" + path
    assert options["allow_redirects"] is False and options["timeout"] == (5, 20)
    return options


def test_authenticate_and_refresh_exact_payloads_expiry_no_plaintext_repr():
    adapter, transport, quota, _ = make_adapter(Response(token_data()), Response(token_data(openId=None)), token=None)
    bundle = adapter.authenticate(API_KEY)
    assert isinstance(bundle, AuthBundle) and bundle.open_id == OPEN_ID
    assert datetime.fromisoformat(bundle.access_expires_at) > datetime.now(timezone.utc)
    assert all(secret not in repr(bundle) and secret not in repr(adapter) for secret in (API_KEY, ACCESS, REFRESH, OPEN_ID))
    options = assert_call(transport, "authentication/getAccessToken", "POST")
    assert options["json"] == {"apiKey": API_KEY} and "CJ-Access-Token" not in options["headers"]
    refreshed = adapter.refresh_authentication(REFRESH)
    assert refreshed.open_id is None
    assert assert_call(transport, "authentication/refreshAccessToken", "POST", index=1)["json"] == {"refreshToken": REFRESH}
    assert all(call[1]["authentication"] for call in quota.calls)


@pytest.mark.parametrize("change", [{"accessToken": ""}, {"refreshToken": None}, {"openId": True},
    {"openId": 1.5}, {"accessTokenExpiryDate": "2000-01-01T00:00:00Z"},
    {"refreshTokenExpiryDate": "2029-01-01"}, {"openId": None}])
def test_invalid_auth_metadata_fails(change):
    adapter, _, _, _ = make_adapter(Response(token_data(**change)))
    with pytest.raises(SupplierError):
        adapter.authenticate(API_KEY)


def test_provider_token_longer_than_a_label_is_accepted_and_reusable():
    """A real CJ token is far longer than the 200 chars we used to allow.

    That cap was ours, not CJ's -- no CJ document states a token length -- and it
    rejected a key CJ had already authenticated, surfacing to the merchant as a
    502 "supplier unreachable" about a call that had succeeded.

    The refresh leg is asserted in the same test on purpose. Widening only the
    parse would have moved the failure rather than fixed it: the long token would
    be stored, and the next `refresh_authentication` would refuse our own value
    and demand reauth -- at expiry, hours after a connect that looked healthy.
    """
    long_access, long_refresh = "a" * 900, "r" * 1200
    adapter, _, _, _ = make_adapter(Response(token_data(accessToken=long_access, refreshToken=long_refresh)),
                                    Response(token_data(accessToken=long_access, refreshToken=long_refresh, openId=None)))
    bundle = adapter.authenticate(API_KEY)
    assert bundle.access_token == long_access and bundle.refresh_token == long_refresh
    # The token we just stored must be one this adapter will take back.
    assert adapter.refresh_authentication(bundle.refresh_token).access_token == long_access
    assert long_access not in repr(bundle) and long_refresh not in repr(adapter)


@pytest.mark.parametrize("change", [{"accessToken": "a" * 4097}, {"refreshToken": "r" * 4097},
                                    {"accessToken": "   "}, {"refreshToken": 12345}])
def test_provider_token_is_still_bounded_and_typed(change):
    """Widened, not removed: an unbounded provider string must not reach the vault."""
    adapter, _, _, _ = make_adapter(Response(token_data(**change)))
    with pytest.raises(SupplierError):
        adapter.authenticate(API_KEY)


def test_settings_identity_and_active_shop_health():
    adapter, transport, _, _ = make_adapter(Response({"openId": OPEN_ID, "isSandbox": 1, "setting": {"qpsLimit": 2}}),
        Response([{"id": SHOP, "name": "Fixture shop", "type": "API", "status": 1}]))
    adapter.set_credentials(AuthBundle(ACCESS, REFRESH, OPEN_ID, token_data()["accessTokenExpiryDate"], token_data()["refreshTokenExpiryDate"]))
    result = adapter.connection_health(shop_id=SHOP)
    assert result["status"] == "CONNECTED" and result["shop_id"] == SHOP
    assert OPEN_ID not in json.dumps(result)
    assert_call(transport, "setting/get")
    assert_call(transport, "shop/getShops", index=1)


def test_health_without_a_bound_shop_proves_identity_and_asks_nothing_else():
    """No binding to re-prove means no call to spend proving it.

    CJ's ceiling is ten business calls per second per outbound IP, so a periodic
    health check that fetches a shop list it will not read is not merely
    redundant -- it consumes budget every connection shares.
    """
    adapter, transport, _, _ = make_adapter(Response({"openId": OPEN_ID}))
    result = adapter.connection_health(shop_id="")
    assert result["status"] == "CONNECTED" and result["shop_bound"] is False
    assert result["shop_id"] == ""
    assert_call(transport, "setting/get")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("shop,status", [(SHOP, 0), ("4001", 1), (SHOP, None)])
def test_shop_health_rejects_unowned_inactive_unknown(shop, status):
    adapter, _, _, _ = make_adapter(Response({"openId": OPEN_ID}),
        Response([{"id": shop, "name": "Fixture shop", "type": "API", "status": status}]))
    with pytest.raises(SupplierError) as failure:
        adapter.connection_health(shop_id=SHOP)
    assert failure.value.code == "SHOP_BINDING_REQUIRED"


def test_categories_nested_shape_normalizes_without_publishing():
    fixture = [{"categoryFirstName": "Root", "categoryFirstList": [{"categorySecondName": "Middle",
                "categorySecondList": [{"categoryId": "1101", "categoryName": "Leaf"}]}]}]
    adapter, transport, _, _ = make_adapter(Response(fixture))
    assert adapter.get_categories() == [{"category_id": "1101", "name": "Leaf", "parent_name": "Middle", "root_name": "Root"}]
    assert_call(transport, "product/getCategory")


def test_list_v2_bounded_search_captures_points_and_request_cost():
    body = {"code": 200, "result": True, "data": {"content": [{"productList": [{"id": PID, "nameEn": "Search fixture",
            "sku": "FIX", "sellPrice": "2.00-3.00"}]}], "totalRecords": 1, "totalPages": 1},
            "pointsInfo": {"remaining": 49000, "usedToday": 1000, "total": 50000, "openId": OPEN_ID}}
    adapter, transport, quota, _ = make_adapter(Response(body=body))
    result = adapter.search_products({"keyWord": "fixture", "countryCode": "US"}, page=2, size=5)
    assert result["products"][0]["pid"] == PID and result["products"][0]["supplier_price"] is None
    assert result["products"][0]["untrusted_content"]
    assert result["points_info"] == {"remaining": 49000, "usedToday": 1000, "total": 50000}
    assert quota.calls[0][1]["cost"] == 50 and len(quota.observations) == 1
    assert assert_call(transport, "product/listV2")["params"] == {"keyWord": "fixture", "countryCode": "US", "page": 2, "size": 5}


@pytest.mark.parametrize("points", [{"remaining": 0, "usedToday": 0, "total": 0},
                                    {"remaining": 0, "usedToday": 4000, "total": 0}])
def test_a_points_block_with_no_ceiling_is_not_recorded_as_an_empty_budget(points):
    """A `total` of zero is no reading, and must not become a zero balance.

    Recording it would set `remaining` to 0, and `reserve_request` refuses
    every costed call at that value before reaching the network -- so the only
    responses that could revise the figure are the ones we would stop making.
    Leaving it unset keeps costed calls refused (as `QUOTA_UNKNOWN`) while
    leaving the zero-point calls that can still resolve it able to do so.

    The request itself is unaffected: a response we cannot read a budget out of
    is still a response, and its products are returned normally.
    """
    body = {"code": 200, "result": True, "data": {"content": [{"productList": [{"id": PID, "nameEn": "Search fixture",
            "sku": "FIX", "sellPrice": "2.00-3.00"}]}], "totalRecords": 1, "totalPages": 1},
            "pointsInfo": {**points, "openId": OPEN_ID}}
    adapter, transport, quota, _ = make_adapter(Response(body=body))
    result = adapter.search_products({"keyWord": "fixture"})
    assert result["products"][0]["pid"] == PID
    assert result["points_info"] is None
    assert quota.observations == []
    assert quota.calls[0][1]["cost"] == 50
    assert_call(transport, "product/listV2")


@pytest.mark.parametrize("kwargs", [{"size": 101}, {"page": 0}, {"page": True}, {"filters": {"accessToken": ACCESS}},
                                   {"filters": {"keyWord": ["not-string"]}}])
def test_search_rejects_unbounded_or_unsupported_input_before_network(kwargs):
    adapter, transport, _, _ = make_adapter()
    with pytest.raises(SupplierError):
        adapter.search_products(**kwargs)
    assert transport.calls == []


def test_product_and_variants_preserve_exact_identifiers_sku():
    adapter, transport, quota, _ = make_adapter(Response(product()), Response([variant()]), Response(variant()))
    result = adapter.get_product(PID)
    assert result["pid"] == PID and result["variants"][0]["vid"] == VID and result["variants"][0]["sku"] == "FIX-SKU"
    assert result["untrusted_content"] and "canonical_product_id" not in result
    assert adapter.get_variants(PID)[0]["vid"] == VID
    assert adapter.get_variant(VID, pid=PID)["pid"] == PID
    for index, path in enumerate(("product/query", "product/variant/query", "product/variant/queryByVid")):
        assert_call(transport, path, index=index)
        assert quota.calls[index][1]["cost"] == 10


def test_mismatching_provider_product_or_variant_ids_fail_closed():
    cases = [(product(pid="1002", variants=[]), lambda a: a.get_product(PID)),
             ([variant(pid="1002")], lambda a: a.get_variants(PID)),
             (variant(vid="2002"), lambda a: a.get_variant(VID, pid=PID))]
    for response, call in cases:
        adapter, _, _, _ = make_adapter(Response(response))
        with pytest.raises(SupplierError):
            call(adapter)


@pytest.mark.parametrize("total,verified,state", [(5, 1, "IN_STOCK"), (0, 1, "OUT_OF_STOCK"),
    (None, 1, "UNKNOWN"), (5, 2, "UNKNOWN"), (5, None, "UNKNOWN"), (True, 1, "UNKNOWN")])
def test_inventory_never_fabricates_verified_stock(total, verified, state):
    fixture = {"variantInventories": [{"pid": PID, "vid": VID, "inventory": [
        {"countryCode": "US", "areaId": 1, "totalInventory": total, "cjInventory": 2,
         "factoryInventory": 3, "verifiedWarehouse": verified}]}]}
    adapter, transport, quota, _ = make_adapter(Response(fixture))
    result = adapter.get_inventory(PID, VID)
    assert result["variants"][0]["warehouses"][0]["state"] == state
    assert result["state"] == "UNKNOWN"  # no aggregate stock fabrication
    assert quota.calls[0][1]["critical"] and quota.calls[0][1]["cost"] == 10
    assert assert_call(transport, "product/stock/getInventoryByPid")["params"] == {"pid": PID}


def test_inventory_warehouses_are_separate_and_variant_mismatch_rejected():
    fixture = {"variantInventories": [{"pid": PID, "vid": VID, "inventory": [
        {"countryCode": "US", "totalInventory": 3, "verifiedWarehouse": 1},
        {"countryCode": "CN", "totalInventory": 9, "verifiedWarehouse": 2}]}]}
    adapter, _, _, _ = make_adapter(Response(fixture), Response(fixture))
    rows = adapter.get_inventory(PID, VID)["variants"][0]["warehouses"]
    assert [row["total"] for row in rows] == [3, 9]
    assert [row["state"] for row in rows] == ["IN_STOCK", "UNKNOWN"]
    with pytest.raises(SupplierError):
        adapter.get_inventory(PID, "2002")


def test_warehouses_disabled_unknown_is_not_enabled():
    adapter, transport, _, _ = make_adapter(Response([
        {"id": "5001", "countryCode": "US", "areaId": 1, "areaEn": "US", "disabled": False},
        {"id": "5002", "countryCode": "CN", "areaEn": "China"}]))
    result = adapter.get_warehouses()
    assert result[0]["disabled"] is False and result[1]["disabled"] is True
    assert_call(transport, "product/globalWarehouseList")


def test_shipping_provider_total_is_not_component_sum_or_delivery_guarantee():
    fixture = [{"option": {"enName": "Fixture shipping"}, "channelId": "channel1", "optionId": "option1",
        "postage": "7.00", "totalPostageFee": "8.50", "taxesFee": "1.50", "tariff": "3.00",
        "arrivalTime": "5-10 days", "ruleTips": [{"msgEn": "Estimated only"}]}]
    adapter, transport, quota, _ = make_adapter(Response(fixture))
    result = adapter.estimate_shipping(shipping_payload())
    quote = result["quotes"][0]
    assert quote["provider_total"] == "8.50" and quote["base"] == "7.00" and quote["tariff"] == "3.00"
    assert quote["clearance"] is None and quote["guaranteed"] is False
    assert quote["origin"] == "CN" and quote["destination"] == "US"
    assert_call(transport, "logistic/freightCalculateTip", "POST")
    assert quota.calls[0][1]["cost"] == 10 and quota.calls[0][1]["critical"]


def test_shipping_unsupported_route_and_missing_total_are_not_free_shipping():
    adapter, _, _, _ = make_adapter(Response([]), Response([{"option": {"enName": "Unknown"}}]))
    assert adapter.estimate_shipping(shipping_payload())["state"] == "UNSUPPORTED_ROUTE"
    assert adapter.estimate_shipping(shipping_payload())["quotes"][0]["provider_total"] is None


@pytest.mark.parametrize("status,body,code", [(429, None, "RATE_LIMITED"), (401, None, "REAUTH_REQUIRED"),
    (503, None, "PROVIDER_UNAVAILABLE"), (200, {"code": 1600200, "result": False}, "RATE_LIMITED"),
    (200, {"code": 900, "result": False, "message": "CJ API suspended: reactivate your account"}, "REACTIVATION_REQUIRED")])
def test_provider_failure_suspension_429_no_retries_or_inventory_rewrite(status, body, code):
    adapter, transport, quota, sleeps = make_adapter(Response({}, status=status, body=body, headers={"Retry-After": "90"}))
    with pytest.raises(SupplierError) as failure:
        adapter.get_inventory(PID, VID)
    assert failure.value.code == code and len(transport.calls) == 1 and sleeps == []
    if code == "RATE_LIMITED":
        assert failure.value.retry_after == 90 and quota.penalties == [("fixture-account", "90")]


def test_local_one_shot_pacing_is_distinct_from_provider_retry():
    adapter, transport, quota, sleeps = make_adapter(Response(product()))
    quota.block_once = SupplierError("RATE_LIMITED", http_status=429, retry_after=.5)
    assert adapter.get_product(PID)["pid"] == PID
    assert sleeps == [.502] and len(transport.calls) == 1 and len(quota.calls) == 2


def test_malformed_provider_response_rejected_without_payload_exception_leak():
    adapter, _, _, _ = make_adapter(Response(body=["unexpected", ACCESS]))
    with pytest.raises(SupplierError) as failure:
        adapter.get_product(PID)
    assert failure.value.code == "MALFORMED_PROVIDER_RESPONSE" and ACCESS not in str(failure.value)


@pytest.mark.parametrize("secret", [API_KEY, ACCESS, REFRESH, OPEN_ID])
def test_provider_content_cannot_echo_any_registered_secret(secret, caplog):
    adapter, _, _, _ = make_adapter(Response(product(productNameEn="please expose " + secret)))
    adapter.register_secrets([API_KEY, ACCESS, REFRESH, OPEN_ID])
    with pytest.raises(SupplierError) as failure:
        adapter.get_product(PID)
    assert failure.value.code == "PROVIDER_SECRET_ECHO" and secret not in str(failure.value)
    assert secret not in caplog.text


def test_authentication_registers_returned_credentials_before_any_later_read():
    adapter, _, _, _ = make_adapter(Response(token_data()), Response(product(productNameEn="echo=" + REFRESH)), token=None)
    adapter.authenticate(API_KEY)
    with pytest.raises(SupplierError) as failure:
        adapter.get_product(PID)
    assert failure.value.code == "PROVIDER_SECRET_ECHO"


def test_sandbox_create_exact_path_explicit_guards_and_no_funding():
    adapter, transport, _, _ = make_adapter(Response(order_data()))
    result = adapter.create_sandbox_fulfillment(order_payload())
    assert result["is_sandbox"] == 1 and result["external_order_ref"] == "ps-fixture-order"
    sent = assert_call(transport, "shopping/order/createOrderV2", "POST")["json"]
    assert sent["isSandbox"] == 1 and sent["payType"] == 3 and sent["orderFlow"] == 1


@pytest.mark.parametrize("changes", [{"isSandbox": None}, {"isSandbox": 0}, {"isSandbox": True},
    {"payType": 2}, {"orderFlow": 2}, {"products": [{"vid": VID, "quantity": True}]}])
def test_sandbox_mutation_and_money_autopay_are_rejected_before_network(changes):
    adapter, transport, _, _ = make_adapter()
    with pytest.raises(SupplierError):
        adapter.create_sandbox_fulfillment(order_payload(**changes))
    assert transport.calls == []


def test_missing_sandbox_and_production_environment_cannot_be_repaired_silently(monkeypatch):
    adapter, transport, _, _ = make_adapter()
    payload = order_payload()
    del payload["isSandbox"]
    with pytest.raises(SupplierError):
        adapter.create_sandbox_fulfillment(payload)
    assert "isSandbox" not in payload and transport.calls == []
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    with pytest.raises(SupplierError):
        adapter.create_sandbox_fulfillment(order_payload())
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "SANDBOX")
    monkeypatch.setenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "true")
    with pytest.raises(SupplierError):
        adapter.create_sandbox_fulfillment(order_payload())
    assert transport.calls == []


def test_timeout_after_create_is_ambiguous_and_never_retried():
    adapter, transport, _, sleeps = make_adapter(TimeoutError("synthetic secret-free timeout"))
    with pytest.raises(SupplierError) as failure:
        adapter.create_sandbox_fulfillment(order_payload())
    assert failure.value.ambiguous_write and len(transport.calls) == 1 and sleeps == []


def test_create_identity_mismatch_or_missing_reply_cannot_be_claimed_success():
    for data in ({}, order_data(orderNum="other-order")):
        adapter, transport, _, _ = make_adapter(Response(data))
        with pytest.raises(SupplierError) as failure:
            adapter.create_sandbox_fulfillment(order_payload())
        assert failure.value.ambiguous_write and len(transport.calls) == 1


def test_get_order_by_external_reference_and_tracking_readback_identity():
    adapter, transport, _, _ = make_adapter(Response(order_data()), Response(order_data(trackNumber="TRACKFIXTURE")),
        Response([{"trackingNumber": "TRACKFIXTURE", "logisticName": "Fixture Carrier", "trackingStatus": "Moving"}]))
    assert adapter.get_fulfillment(external_order_ref="ps-fixture-order")["order_id"] == "cj-fixture-order"
    assert assert_call(transport, "shopping/order/getOrderDetail")["params"] == {"orderId": "ps-fixture-order"}
    assert adapter.get_tracking("cj-fixture-order")["tracking"][0]["tracking_number"] == "TRACKFIXTURE"
    assert_call(transport, "logistic/trackInfo", index=2)


def test_subscription_list_uses_explicit_bound_shop_and_mutations_remain_gated():
    adapter, transport, _, _ = make_adapter(Response({"content": [{"productId": PID, "sku": "FIX", "status": True}], "totalRecords": 1}))
    assert adapter.get_subscriptions(SHOP)["products"][0]["active"]
    assert assert_call(transport, "webhook/product/subscribe/list")["params"] == {"shopId": SHOP, "pageNum": 1, "pageSize": 20}
    for action in (adapter.configure_webhook, adapter.subscribe_products, adapter.unsubscribe_products,
                   adapter.fund_fulfillment, adapter.add_cart, adapter.add_cart_confirm, adapter.save_generate_parent_order):
        with pytest.raises(SupplierError):
            action({"isSandbox": 1})
    assert len(transport.calls) == 1


def test_balance_read_is_not_authorization_to_pay():
    adapter, transport, _, _ = make_adapter(Response("42.25"))
    assert adapter.get_balance() == {"balance": "42.25", "currency": "USD", "funding_enabled": False}
    assert_call(transport, "shopping/pay/getBalance")


def test_default_requests_transport_cannot_reach_network_without_the_switch(monkeypatch):
    """`CJ_NETWORK_ENABLED` is the last thing between this process and CJ.

    It used to share the job with a provider-approval flag. CJ has since
    confirmed no approval is required, so that flag is gone and this switch now
    carries the whole weight -- which is why the retired name is set here to
    prove it grants nothing on its own.
    """
    monkeypatch.delenv("CJ_NETWORK_ENABLED", raising=False)
    transport = RequestsTransport()
    calls = []
    monkeypatch.setattr(transport.session, "request", lambda *a, **k: calls.append(a))
    for retired_flag_set in (False, True):
        if retired_flag_set:
            monkeypatch.setenv("CJ_HOSTED_CREDENTIALS_APPROVED", "true")
        with pytest.raises(SupplierError):
            transport.request("GET", BASE_URL + "/setting/get")
    assert calls == [] and transport.session.trust_env is False


def test_transport_does_not_allow_arbitrary_endpoint_even_when_enabled(monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    transport = RequestsTransport()
    calls = []
    monkeypatch.setattr(transport.session, "request", lambda *a, **k: calls.append(a))
    for url in ("https://attacker.invalid/collect", BASE_URL + "/shopping/pay/payBalanceV2"):
        with pytest.raises(SupplierError):
            transport.request("GET", url)
    assert calls == []
