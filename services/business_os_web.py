"""Business OS web surface — server route pack for /business-os.

Milestone 3 of the website parity mission. The Business OS subsystem exposes
199 API routes under /api/business-os but until now had zero web page. This
pack serves an authenticated, token-styled dashboard shell whose client-side
JS consumes the existing param-free GET endpoints. No new API surface is
introduced — the page is a pure consumer of the already-registered
business-os API blueprint, so parity comes from presentation, not from
duplicated logic.

Registered from bot.py via ``_load_route_pack("business_os_web",
"services.business_os_web")`` following the same conventions as
``services.presence_routes``: Blueprint + lazy bot import + ``register(app)``.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

business_os_web_bp = Blueprint("business_os_web", __name__)


def _bot():
    """Lazy import to avoid a circular import at module load time."""
    import bot

    return bot


@business_os_web_bp.route("/business-os", methods=["GET"])
def business_os_page():
    """Authenticated Business OS dashboard page.

    Same auth idiom as the other server-rendered pages: ``require_account()``
    returns the user or None; unauthenticated visitors are redirected to the
    login page with a ``next`` pointer back here.
    """
    bot_module = _bot()
    user = bot_module.require_account()
    if not user:
        return redirect(url_for("login_page", next=request.path))
    return render_template("business_os.html", user=user)


def register(app):
    app.register_blueprint(business_os_web_bp)
    return True
