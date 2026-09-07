"""Thin, default-dark CJ Supplier Gateway route pack.

Uses the same canonical user/admin and CSRF authorities as Commerce. Credential
request bodies are consumed without Flask's JSON/body cache. No exception detail,
provider response object, body, or credential is logged or passed to analytics.
"""

from __future__ import annotations

import json
import math
import re

from flask import Blueprint, request

from services.business_os_commerce_routes import _bot, _csrf_ok, _json
from services.business_os.commerce_gateway import context_from_user
from services.business_os.suppliers import connections, gateway, policy
from services.business_os.suppliers.errors import SupplierError


supplier_blueprint = Blueprint("business_os_suppliers", __name__)
PREFIX = "/api/business-os/suppliers/cj"
MAX_BODY = 131072
_SECRET_FIELDS = {"apikey", "accesstoken", "refreshtoken", "openid", "ciphertext", "credentialbundle", "authorization", "sign", "secret"}


def _contains_secret_field(value):
    if isinstance(value, dict):
        return any(re.sub(r"[^a-z]", "", str(k).lower()) in _SECRET_FIELDS
                   or _contains_secret_field(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_field(v) for v in value)
    # Flask's JSON provider implicitly expands dataclasses (including AuthBundle).
    # Only explicit JSON primitives may cross this boundary; no implicit objects.
    return value is not None and type(value) not in {str, int, float, bool}


def _respond(payload, status=200, retry_after=None):
    # Fail rather than silently omit an accidental future credential projection.
    if _contains_secret_field(payload):
        payload, status = {"ok": False, "code": "secret_output_blocked"}, 500
    response = _json(payload, status)
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    if retry_after is not None:
        if math.isfinite(float(retry_after)):
            response.headers["Retry-After"] = str(max(1, math.ceil(float(retry_after))))
    return response


def _error(exc):
    # No str(exc), repr(exc), traceback locals, provider message, or request body.
    code = str(getattr(exc, "code", "supplier_unavailable"))
    if not re.fullmatch(r"[A-Za-z_]{1,80}", code):
        code = "supplier_unavailable"
    status = getattr(exc, "http_status", 503)
    if type(status) is not int or status < 400 or status > 599:
        status = 503
    return _respond({"ok": False, "code": code}, status, getattr(exc, "retry_after", None))


def _body():
    if request.content_length is not None and request.content_length > MAX_BODY:
        raise SupplierError("request_too_large", http_status=413)
    if request.mimetype != "application/json":
        raise SupplierError("json_required", http_status=415)
    raw = request.stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise SupplierError("request_too_large", http_status=413)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise SupplierError("invalid_json", http_status=400) from None
    if not isinstance(result, dict):
        raise SupplierError("object_required", http_status=400)
    return result


def _required(body, name):
    value = body.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > (4096 if name == "api_key" else 160):
        raise SupplierError("invalid_input", http_status=400)
    return value


def _request_context(write=False):
    policy.require_enabled()
    user = _bot().api_account_user()
    if not user or not user.get("user_id"):
        raise SupplierError("login_required", http_status=401)
    if write and not _csrf_ok():
        raise SupplierError("csrf", http_status=403)
    return user["user_id"], context_from_user(user)


@supplier_blueprint.route(PREFIX + "/<action>", methods=["POST"])
def cj_connection_action(action):
    try:
        actor, context = _request_context(write=True)
        if action not in {"connect", "discover-shops"}:
            raise SupplierError("not_found", http_status=404)
        body = _body()
        args = [_required(body, "business_id"), _required(body, "store_id"), actor, _required(body, "api_key")]
        if action == "connect":
            result = connections.connect_cj(*args, _required(body, "external_shop_id"), context=context)
        else:
            result = connections.discover_shops(*args, context=context)
        body.clear()
        return _respond({"ok": True, "data": result}, 201 if action == "connect" else 200)
    except Exception as exc:
        return _error(exc)


@supplier_blueprint.route(PREFIX + "/connections", methods=["GET"])
def cj_connections():
    try:
        actor, context = _request_context()
        business_id, store_id = _required(request.args, "business_id"), _required(request.args, "store_id")
        return _respond({"ok": True, "connections": connections.list_connections(business_id, store_id, actor, context=context)})
    except Exception as exc:
        return _error(exc)


@supplier_blueprint.route(PREFIX + "/connections/<connection_id>", methods=["GET"])
def cj_connection(connection_id):
    try:
        actor, context = _request_context()
        result = connections.get_connection(connection_id, _required(request.args, "business_id"), _required(request.args, "store_id"), actor, context=context)
        return _respond({"ok": True, "connection": result})
    except Exception as exc:
        return _error(exc)


@supplier_blueprint.route(PREFIX + "/connections/<connection_id>/<action>", methods=["POST"])
def cj_scoped_action(connection_id, action):
    try:
        actor, context = _request_context(write=True)
        body = _body()
        business_id, store_id = _required(body, "business_id"), _required(body, "store_id")
        if action == "health":
            result = connections.health_connection(connection_id, business_id, store_id, actor, context=context)
        elif action == "bind-product":
            result = gateway.bind_product(connection_id=connection_id, business_id=business_id, store_id=store_id,
                                          actor_user_id=actor, canonical_product_id=_required(body, "canonical_product_id"),
                                          pid=_required(body, "pid"), vid=_required(body, "vid"), context=context)
        elif action == "import-drafts":
            result = gateway.create_import_draft(_required(body, "snapshot_id"), connection_id, business_id, store_id, actor,
                                                 body.get("merchant_fields", {}), context=context)
        elif action == "fulfillment-intents":
            from services.business_os.suppliers import fulfillment
            policy.require_sandbox(body)
            result = fulfillment.create_intent(connection_id=connection_id, business_id=business_id, store_id=store_id,
                actor_user_id=actor, order_id=_required(body, "order_id"), items=body.get("items"),
                shipping_destination=body.get("shipping_destination"), shipping_quote=body.get("shipping_quote"),
                expected_supplier_cost_cents=body.get("expected_supplier_cost_cents"), isSandbox=body.get("isSandbox"),
                idempotency_key=_required(body, "idempotency_key"), context=context)
        elif action in {"subscribe", "unsubscribe"}:
            adapter = connections.adapter_for(business_id, store_id, actor, connection_id, context=context, write=True)
            result = (adapter.subscribe_products if action == "subscribe" else adapter.unsubscribe_products)(body.get("pids"))
        else:
            raise SupplierError("not_found", http_status=404)
        return _respond({"ok": True, "data": result})
    except Exception as exc:
        return _error(exc)


@supplier_blueprint.route(PREFIX + "/connections/<connection_id>/read/<operation>", methods=["POST"])
def cj_read(connection_id, operation):
    # POST keeps destinations and request filters out of access logs/URLs.
    try:
        actor, context = _request_context(write=True)
        body = _body()
        result = gateway.read(operation, business_id=_required(body, "business_id"), store_id=_required(body, "store_id"),
                              actor_user_id=actor, connection_id=connection_id, params=body.get("params", {}), context=context)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


@supplier_blueprint.route(PREFIX + "/connections/<connection_id>/fulfillments/<intent_id>", methods=["GET"])
def cj_fulfillment(connection_id, intent_id):
    try:
        from services.business_os.suppliers import fulfillment
        actor, context = _request_context()
        result = fulfillment.get_intent(intent_id, connection_id, _required(request.args, "business_id"),
                                         _required(request.args, "store_id"), actor, context=context)
        return _respond({"ok": True, "fulfillment": result})
    except Exception as exc:
        return _error(exc)


@supplier_blueprint.route("/api/provider-webhooks/suppliers/cj/<connection_id>", methods=["POST"])
def cj_webhook(connection_id):
    try:
        from services.business_os.suppliers import webhooks
        policy.require_enabled()
        if request.content_length is not None and request.content_length > MAX_BODY:
            raise SupplierError("request_too_large", http_status=413)
        raw = request.stream.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise SupplierError("request_too_large", http_status=413)
        result = webhooks.receive(connection_id, raw, request.headers.getlist("sign"))
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


@supplier_blueprint.route("/api/admin/business-os/suppliers/cj/health", methods=["GET"])
def cj_admin_health():
    try:
        policy.require_enabled()
        _, denied = _bot().require_admin_api("users.view")
        if denied:
            return denied
        return _respond({"ok": True, "policy": policy.safe_status(), "health": connections.admin_health()})
    except Exception as exc:
        return _error(exc)


def register(app):
    # Schema setup is handled by Business OS bootstrap/on-use, not import-time
    # production mutation. The supplier surface is dark unless explicitly enabled.
    app.register_blueprint(supplier_blueprint)
