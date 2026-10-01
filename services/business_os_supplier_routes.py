"""Thin, default-dark CJ Supplier Gateway route pack.

Uses the same canonical user/admin and CSRF authorities as Commerce. Credential
request bodies are consumed without Flask's JSON/body cache. No exception detail,
provider response object, body, or credential is logged or passed to analytics.
Under `CJ_SUPPLIER_DIAGNOSTIC_ORIGIN` (default off) `_error` adds our own raise
site as `file:line` -- a constant of this source, never provider or caller data.
"""

from __future__ import annotations

import json
import math
import re

from flask import Blueprint, request, session

from services.business_os_commerce_routes import _bot, _csrf_ok, _json
from services.business_os.commerce_gateway import context_from_user
from services.business_os.suppliers import connections, diagnostics, gateway, policy
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


def _origin(exc):
    """Innermost frame raised inside the supplier package, as `file:line`.

    These modules are barred from every log sink on purpose, and that stays --
    a supplier credential must not be one careless format string away from a log
    aggregator. But the ban left a class of failure undiagnosable:
    `MALFORMED_PROVIDER_RESPONSE` is raised from a dozen separate validators that
    all answer with the same opaque 502, so "CJ returned something we refused"
    arrives with no way to ask *which field* -- and the only alternative was to
    guess and loosen validators one at a time against a live provider call.

    So the coordinate goes back to the operator on the response instead of into
    a log, and only when a deployment asks for it. A basename and a line number
    are compile-time constants of our own source: they cannot carry provider
    content, a credential, or a request body no matter what CJ returns.
    """
    frame, tb = "", getattr(exc, "__traceback__", None)
    while tb is not None:
        path = tb.tb_frame.f_code.co_filename.replace("\\", "/")
        if "/business_os/suppliers/" in path:
            frame = f"{path.rsplit('/', 1)[-1]}:{tb.tb_lineno}"
        tb = tb.tb_next
    # A refusal raised in this module -- csrf, login_required -- has no frame
    # inside the supplier package and would otherwise record as "". Anything
    # that supplies its own coordinate says so explicitly.
    return frame or str(getattr(exc, "origin_hint", "") or "")


def _csrf_gate_bits():
    """Which half of the CSRF gate refused, as five booleans and nothing else.

    A 403 on a write is the failure this integration keeps reproducing and
    cannot diagnose from the outside. The cookie authenticates the request, so
    the access log says `user_id=1` and reads as a signed-in merchant refused
    for no reason -- the distinction that matters is invisible: bearer absent,
    bearer present but not resolvable, or bearer resolving to a different user
    than the cookie. Each has a different fix and two of them are client-side.

    It is worth recording rather than reasoning about because the client cannot
    recover on its own: `pulseApi` refreshes on 401, and this is a 403, so a
    merchant in this state stays in it until something else rotates the token.

    Digits rather than words so the value satisfies `diagnostics.record`'s
    `name:number` validator, which exists to keep free text out of that table.
    Reading order: bearer header present, bearer resolves, cookie present,
    bearer and cookie agree, X-CSRF-Token present. Five booleans about our own
    gate -- no token, no user id, no header value, nothing provider-derived.
    """
    header = (request.headers.get("Authorization") or "").lower()
    resolve = getattr(_bot(), "account_user_id_from_mobile_access_token", None)
    try:
        bearer = resolve() if callable(resolve) else None
    except Exception:
        bearer = None
    cookie = session.get("account_user_id")
    return "csrf_gate:%d%d%d%d%d" % (
        header.startswith("bearer "), bool(bearer), bool(cookie),
        bool(bearer and cookie and str(bearer) == str(cookie)),
        bool(request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken")),
    )


def _error(exc):
    # No str(exc), repr(exc), traceback locals, provider message, or request body.
    code = str(getattr(exc, "code", "supplier_unavailable"))
    if not re.fullmatch(r"[A-Za-z_]{1,80}", code):
        code = "supplier_unavailable"
    status = getattr(exc, "http_status", 503)
    if type(status) is not int or status < 400 or status > 599:
        status = 503
    # `error_code` is the field the mobile client reads a rejection's code out
    # of. Answering only `code` reached it as no code at all, so every
    # distinction below collapsed into the status check and every 401/403 —
    # csrf, login_required, store_not_approved, forbidden — was rendered as
    # "you're not signed in to this store any more".
    payload = {"ok": False, "code": code, "error_code": code}
    if policy.enabled("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN"):
        origin = _origin(exc)
        # `endpoint` and `provider_code` are validated by SupplierError itself
        # and again by `diagnostics.record`; both are None on anything that is
        # not a provider call. A line number alone could not separate the dozen
        # endpoints that share `_request`'s single rejection line.
        endpoint, provider_code = getattr(exc, "endpoint", None), getattr(exc, "provider_code", None)
        payload["origin"] = origin
        if endpoint:
            payload["endpoint"] = endpoint
        if provider_code is not None:
            payload["provider_code"] = provider_code
        # Recorded as well as returned, because the caller that reaches this in
        # practice is a mobile screen that renders a sentence and discards the
        # body -- an operator who turned the flag on would otherwise never see
        # the field they asked for.
        diagnostics.record(code, status, origin, endpoint, provider_code)
    return _respond(payload, status, getattr(exc, "retry_after", None))


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
        error = SupplierError("csrf", http_status=403)
        if policy.enabled("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN"):
            error.origin_hint = _csrf_gate_bits()
        raise error
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
            # Optional: a CJ "shop" is an external storefront authorized inside
            # the merchant's CJ account, and importing products needs none. When
            # sent it is still validated live against that credential, so an
            # absent field widens who can connect, not what a connection may do.
            shop = body.get("external_shop_id")
            result = connections.connect_cj(*args, _required(body, "external_shop_id") if shop is not None else None,
                                            context=context)
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


@supplier_blueprint.route(PREFIX + "/connections/<connection_id>/inactivity", methods=["GET"])
def cj_connection_inactivity(connection_id):
    """CJ's thirty-day no-real-orders clock, as it applies to this connection.

    A separate resource rather than a field on the connection: it costs a join
    over the intent and outbox tables, and `list_connections` would pay that
    per row to render a number nobody is looking at yet. Read-only, so GET.
    """
    try:
        actor, context = _request_context()
        result = connections.inactivity_forecast(connection_id, _required(request.args, "business_id"),
                                                 _required(request.args, "store_id"), actor, context=context)
        return _respond({"ok": True, "inactivity": result})
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
        elif action == "shops":
            # POST, like the other provider reads here, so nothing about the
            # merchant's CJ account lands in an access log's query string.
            result = connections.connection_shops(connection_id, business_id, store_id, actor, context=context)
        elif action == "bind-shop":
            result = connections.bind_shop(connection_id, business_id, store_id, actor,
                                           _required(body, "external_shop_id"), context=context)
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
