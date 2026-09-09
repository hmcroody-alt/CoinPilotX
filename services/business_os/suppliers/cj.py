"""CJ's bounded server-only adapter. Provider content is untrusted snapshots.

Only this module owns CJ HTTP. No arbitrary hosts, redirects, request retries,
credential logging, monetary writes, catalog publication or tenant authority.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests

from .errors import SupplierError
from .quota import DurableCJQuota

BASE_URL = "https://developers.cjdropshipping.com/api2.0/v1"
PATHS = {
    "authentication/getAccessToken", "authentication/refreshAccessToken",
    "setting/get", "shop/getShops", "product/getCategory", "product/listV2",
    "product/query", "product/variant/query", "product/variant/queryByVid",
    "product/stock/getInventoryByPid", "product/globalWarehouseList",
    "logistic/freightCalculateTip", "logistic/trackInfo",
    "shopping/order/createOrderV2", "shopping/order/getOrderDetail",
    "shopping/pay/getBalance", "webhook/product/subscribe/list",
}
ID_PATTERN = re.compile(r"(?:[0-9]{1,40}|[A-Fa-f0-9]{32}|[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12})")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _dict(value):
    if not isinstance(value, dict):
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
    return value


def _list(value, *, maximum=1000):
    if not isinstance(value, list) or len(value) > maximum:
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
    return value


def _text(value, maximum=500, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
    return value


def _secret_text(value, maximum=4096):
    """A provider-issued credential, which is not display text and is not ours to size.

    These went through `_text(..., 200)`, and 200 was an invented number: no CJ
    document states a token length, and a real account's `accessToken` exceeds
    it. So a correct key, which CJ had already authenticated (HTTP 200, `code`
    200, `result` true, a token pair in hand), was thrown away by our own
    validator and surfaced as a 502 -- the merchant was told their supplier was
    unreachable about a call that had in fact succeeded.

    A bound still belongs here, because an unbounded provider string must not
    reach persistence; it just has to be a credential's bound rather than a
    label's. 4096 is the cap the route already applies to the API key a merchant
    submits, so the value derived from that key is held to the same size.

    The three rejections are deliberately separate statements. They are
    indistinguishable in a response -- all three are MALFORMED_PROVIDER_RESPONSE
    -- so giving them their own lines is what lets the diagnostic say which one
    fired instead of costing another live provider call to find out.
    """
    if not isinstance(value, str):
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
    if not value.strip():
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
    if len(value) > maximum:
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
    return value


def _id(value, *, provider=False):
    # CJ documents UUIDs and decimal identifiers. Never accept floats/bools.
    if type(value) is int and provider:
        value = str(value)
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE" if provider else "INVALID_SUPPLIER_IDENTIFIER", http_status=502 if provider else 400)
    return value


def _ref(value, *, maximum=200):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:#-]{1," + str(maximum) + r"}", value):
        raise SupplierError("INVALID_SUPPLIER_REFERENCE", http_status=400)
    return value


def _number(value):
    if type(value) is int and value >= 0:
        return value
    return None


def _money(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("1000000000000"):
            raise InvalidOperation
        return format(amount, "f")
    except InvalidOperation:
        # Product range strings are not selected-variant payable amounts.
        return None


def _expiry(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed <= datetime.now(timezone.utc):
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, ValueError, TypeError, OverflowError):
        raise SupplierError("AUTH_EXPIRED", http_status=401) from None


@dataclass(frozen=True, repr=False)
class AuthBundle:
    access_token: str
    refresh_token: str
    open_id: str | None
    access_expires_at: str
    refresh_expires_at: str

    def __repr__(self):
        return "AuthBundle(<redacted>)"


class RequestsTransport:
    """No ambient proxies/netrc, TLS bypass, redirects or automatic retries."""
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))

    def request(self, method, url, **kwargs):
        from .policy import require_network
        require_network()
        if not url.startswith(BASE_URL + "/") or url[len(BASE_URL) + 1:] not in PATHS:
            raise SupplierError("UNAPPROVED_PROVIDER_ENDPOINT", http_status=400)
        # Bound decompressed response bytes while downloading, not only after
        # requests has buffered a potentially enormous provider response.
        response = self.session.request(method, url, stream=True, **kwargs)
        try:
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > 5_000_000:
                    raise SupplierError("MALFORMED_PROVIDER_RESPONSE", ambiguous_write=method == "POST")
                chunks.append(chunk)
            response._content = b"".join(chunks)
            response._content_consumed = True
            return response
        finally:
            response.close()


class CJAdapter:
    def __init__(self, *, access_token=None, account_ref=None, quota=None,
                 transport=None, environment="SANDBOX", sleep=time.sleep):
        self.access_token = access_token
        self.account_ref = account_ref
        self.quota = quota if quota is not None else DurableCJQuota()
        self.transport = transport if transport is not None else RequestsTransport()
        self.environment = environment
        self.points_info = None
        self.background = False
        self.sleep = sleep
        self._open_id = None
        self._sensitive_values = set()
        self.register_secrets([access_token])

    def __repr__(self):
        return "CJAdapter(<redacted>)"

    def set_credentials(self, bundle, *, account_ref=None):
        if account_ref and self.account_ref and account_ref != self.account_ref:
            self.quota.rebind_account(self.account_ref, account_ref)
        self.access_token = bundle.access_token
        self._open_id = bundle.open_id or self._open_id
        self.account_ref = account_ref or self.account_ref
        self.register_secrets([bundle.access_token, bundle.refresh_token, bundle.open_id])

    def register_secrets(self, values):
        self._sensitive_values.update(str(v) for v in values if v is not None and str(v))

    def _safe_data(self, value):
        """Reject credential echoes even in otherwise allowed provider text fields."""
        if isinstance(value, dict):
            forbidden = {"apikey", "accesstoken", "refreshtoken", "openid", "authorization", "secret"}
            return {k: self._safe_data(v) for k, v in value.items()
                    if re.sub(r"[^a-z]", "", str(k).lower()) not in forbidden}
        if isinstance(value, list):
            return [self._safe_data(v) for v in value]
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            text = str(value)
            if any(text == secret or len(secret) >= 8 and secret in text for secret in self._sensitive_values):
                raise SupplierError("PROVIDER_SECRET_ECHO")
        return value

    def _observe_points(self, body, sequence):
        points = body.get("pointsInfo") if isinstance(body, dict) else None
        if isinstance(points, dict) and all(type(points.get(k)) is int and points[k] >= 0 for k in ("remaining", "usedToday", "total")) and points["remaining"] <= points["total"]:
            self.points_info = {k: points[k] for k in ("remaining", "usedToday", "total")}
            self.quota.observe(self.account_ref, self.points_info, sequence=sequence)

    def _request(self, method, path, *, params=None, payload=None, cost=0,
                 critical=False, authentication=False, write=False, access_token=None):
        critical = critical and not self.background
        if path not in PATHS or method not in {"GET", "POST"}:
            raise SupplierError("UNAPPROVED_PROVIDER_ENDPOINT", http_status=400)
        token = access_token or self.access_token
        if not authentication and (not isinstance(token, str) or not token):
            raise SupplierError("AUTH_EXPIRED", http_status=401)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if not authentication:
            headers["CJ-Access-Token"] = token
        try:
            sequence = self.quota.reserve_request(self.account_ref, cost=cost, critical=critical, authentication=authentication)
        except SupplierError as exc:
            # One bounded wait for LOCAL pacing only. A CJ HTTP 429 never retries.
            if exc.code != "RATE_LIMITED" or not exc.retry_after or exc.retry_after > 1.05:
                raise
            self.sleep(exc.retry_after + .002)
            sequence = self.quota.reserve_request(self.account_ref, cost=cost, critical=critical, authentication=authentication)
        try:
            response = self.transport.request(method, BASE_URL + "/" + path,
                headers=headers, params=params, json=payload,
                timeout=(5, 20), allow_redirects=False)
        except SupplierError:
            raise
        except Exception:
            raise SupplierError("PROVIDER_UNAVAILABLE", ambiguous_write=write) from None
        status = response.status_code
        if status == 429:
            try:
                if len(response.content) <= 5_000_000:
                    self._observe_points(response.json(), sequence)
            except Exception:
                pass  # A malformed 429 body cannot suppress its backoff.
            delay = self.quota.penalize(self.account_ref, response.headers.get("Retry-After"))
            raise SupplierError("RATE_LIMITED", http_status=429, retry_after=delay, ambiguous_write=write)
        if status in (401, 403):
            raise SupplierError("REAUTH_REQUIRED", http_status=401, ambiguous_write=write)
        if not 200 <= status < 300:
            raise SupplierError("PROVIDER_UNAVAILABLE", ambiguous_write=write)
        try:
            # Bound response parsing before allowing snapshots into persistence.
            if len(response.content) > 5_000_000:
                raise ValueError
            body = response.json()
            _dict(body)
        except Exception:
            raise SupplierError("MALFORMED_PROVIDER_RESPONSE", ambiguous_write=write) from None
        self._observe_points(body, sequence)
        code = body.get("code")
        if code in (429, 1600200, 1600201, 16900500):
            delay = self.quota.penalize(self.account_ref, response.headers.get("Retry-After"))
            raise SupplierError("RATE_LIMITED", http_status=429, retry_after=delay, ambiguous_write=write)
        if code in (1600001, 1600002, 1600003, 1600004, 1600005, 1600006, 1600008, 1601000):
            raise SupplierError("REAUTH_REQUIRED", http_status=401, ambiguous_write=write)
        # Inactivity has no stable published code. Only recognize an explicit
        # suspension/reactivation message, never echo it or invent an error code.
        message = str(body.get("message", "")).lower()
        if code != 200 and ("reactivat" in message or "suspend" in message and "api" in message):
            raise SupplierError("REACTIVATION_REQUIRED", http_status=409, ambiguous_write=write)
        if code in (1602000, 1602001, 1603100):
            raise SupplierError("SUPPLIER_NOT_FOUND", http_status=404)
        if code == 1603003:
            raise SupplierError("DUPLICATE_SUPPLIER_ORDER", http_status=409, ambiguous_write=True)
        if code != 200 or (body.get("result") is not True and body.get("success") is not True) or body.get("result") is False or body.get("success") is False:
            raise SupplierError("SUPPLIER_REJECTED", http_status=422, ambiguous_write=write and code in (1600000, 1600301))
        data = body.get("data")
        return data if authentication or path == "setting/get" else self._safe_data(data)

    def _auth(self, path, payload, *, refresh=False):
        data = _dict(self._request("POST", path, payload=payload, authentication=True))
        bundle = AuthBundle(_secret_text(data.get("accessToken")),
            _secret_text(data.get("refreshToken")),
            _id(data["openId"], provider=True) if data.get("openId") is not None else None,
            _expiry(data.get("accessTokenExpiryDate")), _expiry(data.get("refreshTokenExpiryDate")))
        if not refresh and not bundle.open_id:
            raise SupplierError("ACCOUNT_IDENTITY_UNRESOLVED", http_status=401)
        self.access_token = bundle.access_token
        self._open_id = bundle.open_id or self._open_id
        self.register_secrets([bundle.access_token, bundle.refresh_token, bundle.open_id])
        return bundle

    def authenticate(self, api_key):
        if not isinstance(api_key, str) or not 1 <= len(api_key) <= 200 or any(c.isspace() for c in api_key):
            raise SupplierError("INVALID_API_KEY", http_status=400)
        self.register_secrets([api_key])
        return self._auth("authentication/getAccessToken", {"apiKey": api_key})

    def refresh_authentication(self, refresh_token):
        # Bounded by the same cap `_secret_text` accepted it under. A tighter
        # number here would reject our own stored token and turn every refresh
        # into a reauth prompt -- the failure would surface at expiry, long
        # after the connect that looked fine.
        if not isinstance(refresh_token, str) or not 1 <= len(refresh_token) <= 4096:
            raise SupplierError("REAUTH_REQUIRED", http_status=401)
        self.register_secrets([refresh_token])
        return self._auth("authentication/refreshAccessToken", {"refreshToken": refresh_token}, refresh=True)

    def get_settings(self, access_token=None):
        data = _dict(self._request("GET", "setting/get", access_token=access_token))
        account = _id(data.get("openId"), provider=True)
        if self._open_id and account != self._open_id:
            raise SupplierError("ACCOUNT_IDENTITY_MISMATCH", http_status=409)
        if data.get("root") == "NO_PERMISSION":
            raise SupplierError("REAUTH_REQUIRED", http_status=401)
        setting = _dict(data.get("setting", {}))
        # account_id is secret-bearing INTERNAL connection verification data.
        return {"account_id": account, "is_sandbox": data.get("isSandbox") if type(data.get("isSandbox")) in (int, bool) else None,
            "qps_limit": _number(setting.get("qpsLimit")), "quota_limits": [
                {"path": _text(_dict(q).get("quotaUrl"), 200), "limit": _number(q.get("quotaLimit")), "type": _number(q.get("quotaType"))}
                for q in _list(setting.get("quotaLimits", []), maximum=200)]}

    def get_shops(self, access_token=None):
        data = _list(self._request("GET", "shop/getShops", access_token=access_token))
        return [{"shop_id": _id(_dict(s).get("id"), provider=True), "name": _text(s.get("name"), 200),
                 "platform": _text(s.get("type"), 50), "status": _number(s.get("status"))} for s in data]

    def connection_health(self, *, shop_id=None):
        settings = self.get_settings()
        shops = self.get_shops()
        if shop_id is None or not any(s["shop_id"] == shop_id and s["status"] == 1 for s in shops):
            raise SupplierError("SHOP_BINDING_REQUIRED", http_status=409)
        return {"status": "CONNECTED", "verified_at": _now(), "shop_id": shop_id,
                "points_info": self.points_info, "reachable": True}

    def get_categories(self):
        data = _list(self._request("GET", "product/getCategory"), maximum=1000)
        result = []
        for first in data:
            for second in _list(_dict(first).get("categoryFirstList")):
                for leaf in _list(_dict(second).get("categorySecondList")):
                    result.append({"category_id": _id(_dict(leaf).get("categoryId"), provider=True),
                        "name": _text(leaf.get("categoryName"), 200),
                        "parent_name": _text(second.get("categorySecondName"), 200),
                        "root_name": _text(first.get("categoryFirstName"), 200)})
                    if len(result) > 10000:
                        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
        return result

    def _variant(self, data, pid):
        data = _dict(data)
        actual_pid = _id(data.get("pid", pid), provider=True)
        if actual_pid != pid:
            raise SupplierError("VARIANT_PRODUCT_MISMATCH", http_status=409)
        return {"pid": pid, "vid": _id(data.get("vid"), provider=True),
            "sku": _text(data.get("variantSku"), 200), "title": _text(data.get("variantNameEn"), 500),
            "options": _text(data.get("variantKey"), 500), "price": _money(data.get("variantSellPrice")),
            "currency": "USD", "weight_grams": _money(data.get("variantWeight")),
            "untrusted_content": True}

    def _product(self, data, *, detail=False):
        data = _dict(data)
        pid = _id(data.get("pid") if detail else data.get("id"), provider=True)
        return {"pid": pid, "title": _text(data.get("productNameEn") if detail else data.get("nameEn"), 2000),
            "sku": _text(data.get("productSku") if detail else data.get("sku"), 200),
            "supplier_price": _money(data.get("sellPrice")), "currency": "USD",
            "variants": [self._variant(v, pid) for v in _list(data.get("variants", []))],
            "logistics_properties": [_text(v, 100) for v in _list(data.get("productProEnSet", []), maximum=50)],
            "snapshot_at": _now(), "untrusted_content": True}

    def search_products(self, filters=None, *, page=1, size=20):
        if type(page) is not int or not 1 <= page <= 1000 or type(size) is not int or not 1 <= size <= 100:
            raise SupplierError("INVALID_PAGINATION", http_status=400)
        allowed = {"keyWord", "categoryId", "countryCode", "startSellPrice", "endSellPrice", "startWarehouseInventory", "endWarehouseInventory", "verifiedWarehouse", "sort", "orderBy", "supplierId", "productType", "productFlag", "hasCertification", "isSelfPickup", "customization"}
        if filters is not None and (not isinstance(filters, dict) or set(filters) - allowed):
            raise SupplierError("INVALID_CATALOG_FILTER", http_status=400)
        params = dict(filters or {})
        for key, value in params.items():
            if not isinstance(value, (str, int, float)) or isinstance(value, bool) or len(str(value)) > 200:
                raise SupplierError("INVALID_CATALOG_FILTER", http_status=400)
        params.update(page=page, size=size)
        data = _dict(self._request("GET", "product/listV2", params=params, cost=50))
        products = []
        for block in _list(data.get("content"), maximum=100):
            products.extend(self._product(p) for p in _list(_dict(block).get("productList"), maximum=100))
        if len(products) > size:
            raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
        return {"products": products, "page": page, "size": size,
            "total": _number(data.get("totalRecords")), "total_pages": _number(data.get("totalPages")),
            "points_info": self.points_info}

    def get_product(self, pid):
        pid = _id(pid)
        result = self._product(self._request("GET", "product/query", params={"pid": pid}, cost=10), detail=True)
        if result["pid"] != pid:
            raise SupplierError("PRODUCT_IDENTITY_MISMATCH", http_status=409)
        return result

    def get_variants(self, pid):
        pid = _id(pid)
        return [self._variant(v, pid) for v in _list(self._request("GET", "product/variant/query", params={"pid": pid}, cost=10))]

    def get_variant(self, vid, *, pid):
        vid, pid = _id(vid), _id(pid)
        result = self._variant(self._request("GET", "product/variant/queryByVid", params={"vid": vid}, cost=10), pid)
        if result["vid"] != vid:
            raise SupplierError("VARIANT_PRODUCT_MISMATCH", http_status=409)
        return result

    @staticmethod
    def _warehouse_stock(data, *, variant):
        data = _dict(data)
        total = _number(data.get("totalInventory" if variant else "totalInventoryNum"))
        verified = data.get("verifiedWarehouse") if type(data.get("verifiedWarehouse")) is int and data.get("verifiedWarehouse") in (1, 2) else None
        return {"country": _text(data.get("countryCode"), 2), "area_id": _number(data.get("areaId")),
            "total": total, "cj": _number(data.get("cjInventory" if variant else "cjInventoryNum")),
            "factory": _number(data.get("factoryInventory" if variant else "factoryInventoryNum")),
            "verified": verified,
            "state": "OUT_OF_STOCK" if total == 0 else "IN_STOCK" if total is not None and total > 0 and verified == 1 else "UNKNOWN",
            "subwarehouses": [{"stock_id": _text(_dict(s).get("stockId"), 200), "cj": _number(s.get("inventory")), "factory": _number(s.get("factoryInventory"))} for s in _list(data.get("stock") or [])]}

    def get_inventory(self, pid, vid=None):
        pid = _id(pid)
        if vid is not None:
            vid = _id(vid)
        data = _dict(self._request("GET", "product/stock/getInventoryByPid", params={"pid": pid}, cost=10, critical=True))
        rows = []
        for row in _list(data.get("variantInventories", [])):
            row = _dict(row)
            row_vid = _id(row.get("vid"), provider=True)
            if row.get("pid") is not None and _id(row["pid"], provider=True) != pid:
                raise SupplierError("VARIANT_PRODUCT_MISMATCH", http_status=409)
            if vid is None or row_vid == vid:
                rows.append({"vid": row_vid, "pid": pid, "warehouses": [self._warehouse_stock(w, variant=True) for w in _list(row.get("inventory", []))]})
        if vid is not None and not rows:
            raise SupplierError("VARIANT_PRODUCT_MISMATCH", http_status=409)
        return {"pid": pid, "variants": rows, "product_warehouses": [self._warehouse_stock(w, variant=False) for w in _list(data.get("inventories", []))],
            "state": "UNKNOWN", "snapshot_at": _now(), "points_info": self.points_info}

    def get_warehouses(self):
        return [{"warehouse_id": _id(_dict(w).get("id"), provider=True), "area_id": _number(w.get("areaId")),
            "country": _text(w.get("countryCode"), 2), "name": _text(w.get("areaEn"), 200), "disabled": w.get("disabled") is not False}
            for w in _list(self._request("GET", "product/globalWarehouseList"))]

    def estimate_shipping(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"reqDTOS"} or not isinstance(payload["reqDTOS"], list) or len(payload["reqDTOS"]) != 1:
            raise SupplierError("INVALID_SHIPPING_REQUEST", http_status=400)
        row = payload["reqDTOS"][0]
        allowed = {"srcAreaCode", "destAreaCode", "weight", "wrapWeight", "volume", "length", "width", "height", "productProp", "skuList", "freightTrialSkuList", "totalGoodsAmount", "zip", "city", "province", "recipientAddress", "shippingMode", "platforms", "storageIdList"}
        if not isinstance(row, dict) or set(row) - allowed or any(not isinstance(row.get(k), str) or not re.fullmatch("[A-Z]{2}", row[k]) for k in ("srcAreaCode", "destAreaCode")):
            raise SupplierError("INVALID_SHIPPING_REQUEST", http_status=400)
        if type(row.get("weight")) not in (int, float) or not math.isfinite(row["weight"]) or row["weight"] <= 0 or not row.get("productProp") or not row.get("skuList"):
            raise SupplierError("INVALID_SHIPPING_REQUEST", http_status=400)
        sku_rows = row.get("freightTrialSkuList")
        if not isinstance(sku_rows, list) or not 1 <= len(sku_rows) <= 20:
            raise SupplierError("INVALID_SHIPPING_REQUEST", http_status=400)
        for sku in sku_rows:
            if not isinstance(sku, dict) or set(sku) - {"sku", "vid", "skuQuantity", "skuWeight", "skuVolume"} or type(sku.get("skuQuantity")) is not int or not 1 <= sku["skuQuantity"] <= 10000:
                raise SupplierError("INVALID_SHIPPING_REQUEST", http_status=400)
            if "vid" in sku:
                _id(sku["vid"])
        data = _list(self._request("POST", "logistic/freightCalculateTip", payload=payload, cost=10, critical=True), maximum=200)
        quotes = []
        for quote in data:
            quote = _dict(quote)
            option = _dict(quote.get("option") or {})
            quotes.append({"service": _text(option.get("enName"), 200), "channel_id": _text(quote.get("channelId"), 200),
                "option_id": _text(quote.get("optionId"), 200), "origin": row["srcAreaCode"], "destination": row["destAreaCode"],
                "base": _money(quote.get("postage")), "provider_total": _money(quote.get("totalPostageFee")),
                "tax": _money(quote.get("taxesFee")), "tariff": _money(quote.get("tariff")),
                "clearance": _money(quote.get("clearanceOperationFee")), "remote_fee": _money(quote.get("remoteFee")),
                "discount_fee": _money(quote.get("discountFee")), "wrap_postage": _money(quote.get("wrapPostage")),
                "currency": "USD", "weight_grams": row["weight"], "estimated_transit": _text(quote.get("arrivalTime"), 200),
                "restrictions": [_text(_dict(t).get("msgEn"), 1000) for t in _list(quote.get("ruleTips", []), maximum=100)],
                "available": not bool(quote.get("error") or quote.get("errorEn")), "quoted_at": _now(), "guaranteed": False})
        return {"quotes": quotes, "points_info": self.points_info, "state": "QUOTED" if quotes else "UNSUPPORTED_ROUTE"}

    @staticmethod
    def _order(data):
        data = _dict(data)
        order_id = data.get("orderId")
        if type(order_id) is int and order_id >= 0:
            order_id = str(order_id)
        if not isinstance(order_id, str):
            raise SupplierError("MALFORMED_PROVIDER_RESPONSE")
        status = data.get("orderStatus")
        known = {"CREATED", "IN_CART", "UNPAID", "UNSHIPPED", "PENDING", "PROCESSING", "SHIPPED", "DELIVERED", "CLOSED", "CANCELLED", "INVALID"}
        return {"order_id": _ref(order_id), "external_order_ref": _text(data.get("orderNum", data.get("orderNumber")), 200),
            "status": status if status in known else "UNKNOWN", "provider_status": _text(status, 100),
            "tracking_number": _text(data.get("trackNumber"), 200), "shop_id": _text(data.get("shopId"), 200),
            "is_sandbox": data.get("isSandbox") if type(data.get("isSandbox")) is int and data["isSandbox"] in (0, 1) else None,
            "supplier_total": _money(data.get("orderAmount")), "currency": "USD",
            "products": [{"vid": _id(_dict(p).get("vid"), provider=True), "quantity": _number(p.get("quantity"))} for p in _list(data.get("productList", []))]}

    def create_sandbox_fulfillment(self, payload):
        from .policy import require_sandbox
        require_sandbox(payload)
        if self.environment != "SANDBOX":
            raise SupplierError("PRODUCTION_FULFILLMENT_DISABLED", http_status=409)
        if type(payload.get("payType")) is not int or payload["payType"] != 3:
            raise SupplierError("FUNDING_NOT_READY", http_status=409)
        if type(payload.get("orderFlow")) is not int or payload["orderFlow"] != 1:
            raise SupplierError("ORDER_FLOW_NOT_APPROVED", http_status=409)
        allowed = {"orderNumber", "shippingZip", "shippingCountryCode", "shippingCountry", "shippingProvince", "shippingCity", "shippingCounty", "shippingPhone", "shippingCustomerName", "shippingAddress", "shippingAddress2", "houseNumber", "email", "remark", "payType", "isSandbox", "logisticName", "fromCountryCode", "platform", "orderFlow", "products", "storeName", "shopLogisticsType", "storageId"}
        if set(payload) - allowed:
            raise SupplierError("INVALID_FULFILLMENT_REQUEST", http_status=400)
        reference = _ref(payload.get("orderNumber"), maximum=50)
        if not isinstance(payload.get("storeName"), str) or not payload["storeName"].strip() or len(payload["storeName"]) > 50:
            raise SupplierError("SHOP_BINDING_REQUIRED", http_status=409)
        for key in ("shippingCountryCode", "shippingCountry", "shippingProvince", "shippingCity", "shippingCustomerName", "shippingAddress", "logisticName", "fromCountryCode"):
            if not isinstance(payload.get(key), str) or not payload[key].strip() or len(payload[key]) > 500:
                raise SupplierError("INVALID_FULFILLMENT_REQUEST", http_status=400)
        if not isinstance(payload.get("products"), list) or not 1 <= len(payload["products"]) <= 20:
            raise SupplierError("INVALID_FULFILLMENT_REQUEST", http_status=400)
        for product in payload["products"]:
            if not isinstance(product, dict) or set(product) - {"vid", "quantity", "storeLineItemId"} or type(product.get("quantity")) is not int or not 1 <= product["quantity"] <= 10000:
                raise SupplierError("INVALID_FULFILLMENT_REQUEST", http_status=400)
            _id(product.get("vid"))
        raw_result = self._request("POST", "shopping/order/createOrderV2", payload=payload, write=True, critical=True)
        try:
            result = self._order(raw_result)
        except SupplierError:
            raise SupplierError("CREATE_RESULT_UNVERIFIED", ambiguous_write=True) from None
        if result["external_order_ref"] != reference:
            raise SupplierError("ORDER_IDENTITY_MISMATCH", ambiguous_write=True)
        # Missing sandbox echo is UNKNOWN, not a fabricated provider attestation.
        return result

    def get_fulfillment(self, order_id=None, external_order_ref=None):
        if (order_id is None) == (external_order_ref is None):
            raise SupplierError("ORDER_REFERENCE_REQUIRED", http_status=400)
        ref = _ref(order_id or external_order_ref)
        result = self._order(self._request("GET", "shopping/order/getOrderDetail", params={"orderId": ref}, critical=True))
        if external_order_ref is not None and result["external_order_ref"] != external_order_ref or order_id is not None and result["order_id"] != order_id:
            raise SupplierError("ORDER_IDENTITY_MISMATCH", http_status=409)
        return result

    def get_tracking(self, order_id):
        order = self.get_fulfillment(order_id=order_id)
        if not order["tracking_number"]:
            return {"order_id": order_id, "tracking": [], "state": "UNKNOWN"}
        number = _ref(order["tracking_number"])
        data = _list(self._request("GET", "logistic/trackInfo", params={"trackNumber": number}, critical=True), maximum=20)
        normalized = []
        for track in data:
            track = _dict(track)
            if track.get("trackingNumber") != number:
                raise SupplierError("TRACKING_IDENTITY_MISMATCH", http_status=409)
            normalized.append({"tracking_number": number, "carrier": _text(track.get("logisticName"), 200), "provider_status": _text(track.get("trackingStatus"), 200), "origin": _text(track.get("trackingFrom"), 20), "destination": _text(track.get("trackingTo"), 20), "last_mile_carrier": _text(track.get("lastMileCarrier"), 200), "last_mile_number": _text(track.get("lastTrackNumber"), 200)})
        return {"order_id": order_id, "tracking": normalized, "state": "OBSERVED" if normalized else "UNKNOWN"}

    def get_balance(self):
        data = self._request("GET", "shopping/pay/getBalance", critical=True)
        return {"balance": _money(data), "currency": "USD", "funding_enabled": False}

    def get_subscriptions(self, shop_id, *, page=1, size=20):
        shop_id = _id(shop_id)
        if type(page) is not int or not 1 <= page <= 1000 or type(size) is not int or not 1 <= size <= 200:
            raise SupplierError("INVALID_PAGINATION", http_status=400)
        data = _dict(self._request("GET", "webhook/product/subscribe/list", params={"shopId": shop_id, "pageNum": page, "pageSize": size}))
        return {"products": [{"pid": _id(_dict(p).get("productId"), provider=True), "sku": _text(p.get("sku"), 200), "active": p.get("status") is True} for p in _list(data.get("content"), maximum=size)], "page": page, "size": size, "total": _number(data.get("totalRecords"))}

    def configure_webhook(self, *args, **kwargs):
        raise SupplierError("WEBHOOK_CONFIGURATION_APPROVAL_REQUIRED", http_status=409)

    def subscribe_products(self, *args, **kwargs):
        raise SupplierError("SUBSCRIPTION_SEMANTICS_UNVERIFIED", http_status=409)

    unsubscribe_products = subscribe_products

    def fund_fulfillment(self, *args, **kwargs):
        raise SupplierError("FUNDING_NOT_READY", http_status=409)

    add_cart = fund_fulfillment
    add_cart_confirm = fund_fulfillment
    save_generate_parent_order = fund_fulfillment

    def reconcile(self, *, order_id=None, external_order_ref=None):
        return self.get_fulfillment(order_id=order_id, external_order_ref=external_order_ref)

    refreshAuthentication = refresh_authentication
    getSettings = get_settings
    getShops = get_shops
