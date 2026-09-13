"""Business OS — COMMERCE route pack (thin Flask adapter).

Mount with one line in bot.py, next to the other packs (bot.py ~line 1240):

    _load_route_pack("business_os_commerce",
                     "services.business_os_commerce_routes")

Everything real lives in ``services/business_os/commerce_gateway.py`` (the
framework-agnostic route table + input mapping) and in the eight canonical
controller packs it wires. This file only:

  * resolves the session user (``bot.api_account_user``) or the admin
    (``bot.require_admin_api``) per the route's declared auth tier,
  * hands request parts (path params, query dict, JSON body) to the gateway,
  * jsonifies the controller's ``(status, body)`` tuple.

Every controller goes DARK (404) when its BUSINESS_OS_* flag is off, so
mounting this pack changes nothing until the flags are turned on.
"""

from __future__ import annotations

from flask import (Blueprint, g, jsonify, redirect, render_template, request,
                   session, url_for)

from services import csrf as _shared_csrf
from services.business_os import commerce_gateway as gw


commerce_blueprint = Blueprint("business_os_commerce", __name__)


def _bot():
    import bot
    return bot


def _json(payload, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


#: `_verified_bearer_write_authority()` used to live here. It moved verbatim to
#: `services.csrf.bearer_is_csrf_safe()`, which is the only caller's new home;
#: nothing else in the repo referenced it. Left as a note rather than deleted
#: silently because the behaviour it describes -- the cookie hiding the bearer,
#: so every Business OS write was refused while every read succeeded -- is the
#: reason the exemption exists, and that reasoning is now in the shared module.


def _csrf_ok():
    """Default-deny CSRF gate for cookie-authenticated writes.

    Native app requests carry a signed Authorization bearer and are inherently
    CSRF-safe, so ``allow_bearer`` is on here -- this pack is member-facing and
    the native app has no CSRF token to echo. Web requests echo the session
    token via ``X-CSRF-Token`` or a ``csrf_token`` form field.

    The accept-set and the bearer re-verification now live in
    ``services/csrf.py`` so that this pack, the entitlement endpoints and
    ``bot.verify_csrf`` cannot drift apart again -- they had, on four of nine
    request shapes.
    """
    return _shared_csrf.verify(allow_bearer=True)


def _require_user():
    user = _bot().api_account_user()
    if not user:
        return None, _json({"ok": False, "error": "Login required.",
                            "code": "login_required"}, 401)
    return user, None


def _make_view(route):
    """One closure per ROUTES entry. Defined in a factory so each view binds
    its own route dict (the classic loop-variable pitfall)."""

    def view(**path_params):
        if (route["auth"] != "public"
                and request.method in ("POST", "PATCH", "PUT")
                and not _csrf_ok()):
            return _json({"ok": False, "error": "CSRF check failed.",
                          "code": "csrf"}, 403)
        if route["auth"] == "admin":
            admin, denied = _bot().require_admin_api(
                route.get("admin_perm", "users.view"))
            if denied:
                return denied
            actor, context = admin.get("id"), None
        elif route["auth"] == "user":
            user, denied = _require_user()
            if denied:
                return denied
            actor = user.get("user_id")
            context = gw.context_from_user(user)
        else:  # public
            actor, context = None, None
        body = (request.get_json(silent=True)
                if request.method in ("POST", "PATCH", "PUT") else None)
        status, payload = gw.dispatch(
            route["name"], actor, path=path_params,
            query=request.args.to_dict(), body=body, context=context)
        return _json(payload, status)

    view.__name__ = route["name"]
    return view


@commerce_blueprint.route("/business-os/commerce", methods=["GET"])
def business_os_commerce_page():
    """Seller Commerce Console — the web face of the gateway's 37 endpoints.
    Same auth idiom as the /business-os page: require_account() or a redirect
    to login with a ``next`` pointer back here."""
    bot_module = _bot()
    user = bot_module.require_account()
    if not user:
        return redirect(url_for("login_page", next=request.path))
    return render_template("business_os_commerce.html", user=user,
                           csrf_token=bot_module.get_csrf_token())


for _route in gw.ROUTES:
    commerce_blueprint.add_url_rule(
        gw.API_PREFIX + _route["rule"], endpoint=_route["name"],
        view_func=_make_view(_route), methods=[_route["method"]])


def register(app):
    try:
        gw.ensure_schemas()
    except Exception:
        # Never block boot on schema init; the controllers re-check and the
        # feature flags keep the surface dark until it's ready.
        pass
    app.register_blueprint(commerce_blueprint)
