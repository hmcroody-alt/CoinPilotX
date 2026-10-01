"""Provider-neutral dropshipping routes: browse, cart, import, draft, publish.

Deliberately separate from ``business_os_supplier_routes``. That pack is the CJ
*connection* surface — credentials, health, raw gateway reads — and its URLs say
``/suppliers/cj/`` because they are provider-shaped. These are the merchant's
dropshipping URLs, and no path, parameter or response key here names a provider
except as a value. Adding Printful means an adapter in ``normalize`` and a value
in ``provider``; it does not mean a second route pack, and it must not, or the
mobile app grows one screen per supplier.

The trust boundary
------------------
The client sends **ids only**. There is no parameter on any route below through
which a caller could assert a cost, an inventory level, a title from the
supplier, or a variant identity. ``POST /import`` takes ``item_ids`` — rows in
the merchant's own cart — and the server re-fetches everything else from the
provider. Adding a convenience parameter like ``cost_cents`` "to save a round
trip" would hand price-setting to anyone who can craft a request.

Errors carry a code and nothing else, following the supplier pack: no provider
message, no exception text, no request echo.
"""

from __future__ import annotations

import json
import math
import re

from flask import Blueprint, request

from services import db
from services.business_os_commerce_routes import _bot, _csrf_ok, _json
from services.business_os.commerce_gateway import context_from_user
from services.business_os.suppliers import (discovery, drafts, import_cart, importer,
                                            merchant_scope, policy, pricing)
from services.business_os.suppliers.errors import SupplierError


dropshipping_blueprint = Blueprint("business_os_dropshipping", __name__)
PREFIX = "/api/business-os/dropshipping"
MAX_BODY = 131072

#: Response keys that must never appear. Supplier cost is merchant-private but
#: legitimately present on these routes, so it is not listed; credentials are
#: never present and are listed so that a future refactor cannot introduce one
#: silently.
_SECRET_FIELDS = {"apikey", "accesstoken", "refreshtoken", "openid", "ciphertext",
                  "credentialbundle", "authorization", "sign", "secret", "password"}


def _contains_secret_field(value):
    if isinstance(value, dict):
        return any(re.sub(r"[^a-z]", "", str(k).lower()) in _SECRET_FIELDS
                   or _contains_secret_field(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_field(v) for v in value)
    return value is not None and type(value) not in {str, int, float, bool}


def _respond(payload, status=200, retry_after=None):
    if _contains_secret_field(payload):
        payload, status = {"ok": False, "code": "secret_output_blocked"}, 500
    response = _json(payload, status)
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    # Supplier cost and margin are merchant-private; no shared cache may hold them.
    response.headers["Cache-Control"] = "no-store, private"
    if retry_after is not None and math.isfinite(float(retry_after)):
        response.headers["Retry-After"] = str(max(1, math.ceil(float(retry_after))))
    return response


def _error(exc):
    code = str(getattr(exc, "code", "supplier_unavailable"))
    if not re.fullmatch(r"[A-Za-z_]{1,80}", code):
        code = "supplier_unavailable"
    status = getattr(exc, "http_status", 503)
    if type(status) is not int or status < 400 or status > 599:
        status = 503
    # `error_code` is the field the mobile client reads a rejection's code out of.
    # Answering only `code` reached it as no code at all, so every distinction
    # this module makes — the feature being off here, a rejected key, a store
    # awaiting approval — arrived as one generic failure and the merchant was
    # told "Dropshipping didn't load" instead of the actual blocker.
    return _respond(
        {"ok": False, "code": code, "error_code": code}, status, getattr(exc, "retry_after", None))


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


def _required(source, name, limit=190):
    value = source.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SupplierError("invalid_input", http_status=400)
    return value


def _scope(source):
    return _required(source, "business_id", 160), _required(source, "store_id", 160)


def _request_context(write=False):
    policy.require_enabled()
    user = _bot().api_account_user()
    if not user or not user.get("user_id"):
        raise SupplierError("login_required", http_status=401)
    if write and not _csrf_ok():
        raise SupplierError("csrf", http_status=403)
    return user["user_id"], context_from_user(user)


def _provider(source):
    value = source.get("provider", "cj")
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_]{1,32}", value):
        raise SupplierError("invalid_input", http_status=400)
    return value


# ---------------------------------------------------------------------------
# Layer 0 — which store am I acting for
# ---------------------------------------------------------------------------

@dropshipping_blueprint.route(PREFIX + "/scope", methods=["GET"])
def merchant_scope_route():
    """The store this merchant's dropshipping work belongs to.

    One server-side answer instead of the client stitching a business list to a
    storefront lookup and inferring a gap from two empty responses. That
    inference is what told a merchant trading as "M&W Store · Open for orders"
    to go and create a business first: their store is a marketplace seller
    record, and the client was only ever asking Business OS.

    Read-only and never creates anything — a merchant without a store gets a
    reason, not a silently provisioned second identity.
    """
    try:
        actor, _context = _request_context()
        conn = db.connect()
        try:
            result = merchant_scope.resolve(conn, actor)
        finally:
            conn.close()
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


# ---------------------------------------------------------------------------
# Layer 2 — find products
# ---------------------------------------------------------------------------

@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/search", methods=["POST"])
def search_products(connection_id):
    # POST, not GET: search filters are merchant business intelligence and have
    # no business sitting in an access log or a browser history entry.
    try:
        actor, context = _request_context(write=True)
        body = _body()
        business_id, store_id = _scope(body)
        result = discovery.search(business_id, store_id, actor, connection_id,
                                  filters=body.get("filters"), page=body.get("page", 1),
                                  size=body.get("size", 20), provider=_provider(body),
                                  context=context)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/products/<path:external_product_id>", methods=["GET"])
def supplier_product(connection_id, external_product_id):
    try:
        actor, context = _request_context()
        business_id, store_id = _scope(request.args)
        result = discovery.detail(business_id, store_id, actor, connection_id,
                                  external_product_id, provider=_provider(request.args),
                                  context=context)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


# ---------------------------------------------------------------------------
# Layer 3 — import cart
# ---------------------------------------------------------------------------

@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/cart", methods=["GET"])
def get_cart(connection_id):
    try:
        actor, context = _request_context()
        business_id, store_id = _scope(request.args)
        return _respond({"ok": True, **import_cart.get_cart(business_id, store_id, actor,
                                                            connection_id, context=context)})
    except Exception as exc:
        return _error(exc)


@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/cart/items", methods=["POST"])
def add_cart_item(connection_id):
    try:
        actor, context = _request_context(write=True)
        body = _body()
        business_id, store_id = _scope(body)
        result = import_cart.add_item(business_id, store_id, actor, connection_id,
                                      external_product_id=_required(body, "external_product_id"),
                                      selected_variant_ids=body.get("selected_variant_ids"),
                                      product=body.get("product"), provider=_provider(body),
                                      context=context)
        return _respond({"ok": True, "item": result}, 201)
    except Exception as exc:
        return _error(exc)


@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/cart/items/<item_id>", methods=["PATCH"])
def update_cart_item(connection_id, item_id):
    try:
        actor, context = _request_context(write=True)
        body = _body()
        business_id, store_id = _scope(body)
        result = import_cart.update_item(business_id, store_id, actor, connection_id, item_id,
                                         selected_variant_ids=body.get("selected_variant_ids"),
                                         context=context)
        return _respond({"ok": True, "item": result})
    except Exception as exc:
        return _error(exc)


@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/cart/items/<item_id>", methods=["DELETE"])
def remove_cart_item(connection_id, item_id):
    try:
        actor, context = _request_context(write=True)
        business_id, store_id = _scope(request.args)
        result = import_cart.remove_item(business_id, store_id, actor, connection_id,
                                         item_id, context=context)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


# ---------------------------------------------------------------------------
# Layers 4-6 — import
# ---------------------------------------------------------------------------

@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/import", methods=["POST"])
def import_selected(connection_id):
    try:
        actor, context = _request_context(write=True)
        body = _body()
        business_id, store_id = _scope(body)
        result = importer.import_selected(business_id, store_id, actor, connection_id,
                                          item_ids=body.get("item_ids"),
                                          pricing_rule=body.get("pricing_rule"),
                                          context=context)
        # 200, not 207: per-item outcomes are the payload's job. A transport-level
        # multi-status pushes callers into treating "some failed" as "call failed".
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


# ---------------------------------------------------------------------------
# Layers 7-9 — draft review, merchant edits, publish
# ---------------------------------------------------------------------------

@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/imported", methods=["GET"])
def list_imported(connection_id):
    try:
        actor, context = _request_context()
        business_id, store_id = _scope(request.args)
        status = request.args.get("status")
        if status is not None and not re.fullmatch(r"[a-z_]{1,32}", str(status)):
            raise SupplierError("invalid_input", http_status=400)
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            raise SupplierError("invalid_input", http_status=400) from None
        result = drafts.list_drafts(business_id, store_id, actor, connection_id,
                                    context=context, status=status, limit=limit)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/imported/<listing_id>", methods=["GET"])
def get_imported(connection_id, listing_id):
    try:
        actor, context = _request_context()
        business_id, store_id = _scope(request.args)
        result = drafts.get_draft(business_id, store_id, actor, connection_id,
                                  listing_id, context=context)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/imported/<listing_id>", methods=["PATCH"])
def update_imported(connection_id, listing_id):
    try:
        actor, context = _request_context(write=True)
        body = _body()
        business_id, store_id = _scope(body)
        result = drafts.update_draft(business_id, store_id, actor, connection_id, listing_id,
                                     fields=body.get("fields"), context=context)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


@dropshipping_blueprint.route(PREFIX + "/connections/<connection_id>/imported/<listing_id>/<action>", methods=["POST"])
def imported_action(connection_id, listing_id, action):
    try:
        actor, context = _request_context(write=True)
        body = _body()
        business_id, store_id = _scope(body)
        if action == "publish":
            result = drafts.publish(business_id, store_id, actor, connection_id,
                                    listing_id, context=context)
        elif action == "validate":
            result = drafts.validate(business_id, store_id, actor, connection_id,
                                     listing_id, context=context)
        else:
            raise SupplierError("not_found", http_status=404)
        return _respond({"ok": True, **result})
    except Exception as exc:
        return _error(exc)


# ---------------------------------------------------------------------------
# Pricing preview — no persistence, no provider call
# ---------------------------------------------------------------------------

@dropshipping_blueprint.route(PREFIX + "/pricing/preview", methods=["POST"])
def pricing_preview():
    """Show what a pricing rule would do, without applying it to anything.

    Pure arithmetic over numbers the merchant already has on screen, so it takes
    no connection and touches no provider — a merchant dragging a margin slider
    must not generate supplier traffic.
    """
    try:
        _request_context(write=True)
        body = _body()
        costs = body.get("cost_cents")
        if not isinstance(costs, (list, tuple)) or len(costs) > 200:
            raise SupplierError("invalid_input", http_status=400)
        try:
            rule = pricing.normalize_rule(body.get("pricing_rule"))
        except pricing.PricingRejected:
            raise SupplierError("invalid_pricing_rule", http_status=400) from None
        quotes = [pricing.quote(rule, c if isinstance(c, int) and not isinstance(c, bool) else None)
                  for c in costs]
        return _respond({"ok": True, "rule": rule, "quotes": quotes})
    except Exception as exc:
        return _error(exc)


def register(app):
    # Schema is created on use, not at import. Registering this pack must not
    # mutate a production database.
    app.register_blueprint(dropshipping_blueprint)
