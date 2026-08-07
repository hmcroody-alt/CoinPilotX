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
    return render_template(
        "business_os.html",
        user=user,
        csrf_token=bot_module.get_csrf_token(),
    )


def _bootstrap_business_os_schema_if_needed():
    """First-touch schema bootstrap for the Business OS namespace.

    Production Postgres has no migration runner; each Business OS subsystem
    ships an idempotent ``ensure_schema()`` that historically nothing invoked
    at startup, so enabling the ``BUSINESS_OS_*`` flags on a fresh database
    500'd with ``UndefinedTable``. This hook runs the one-shot bootstrap the
    first time any Business OS page or API route is requested. It is
    process-idempotent (module-level once latch), flag-aware (no-ops while
    every flag is off), and never raises.
    """
    path = request.path or ""
    if not (path.startswith("/api/business-os") or path.startswith("/business-os")):
        return None
    try:
        from services.business_os import schema_bootstrap

        schema_bootstrap.ensure_all_once()
    except Exception:
        # Never let bootstrap problems mask the request's own error handling.
        import logging

        logging.exception("BUSINESS_OS_SCHEMA_BOOTSTRAP_HOOK_FAILED (non-fatal)")
    return None


def register(app):
    app.register_blueprint(business_os_web_bp)
    app.before_request(_bootstrap_business_os_schema_if_needed)
    return True
