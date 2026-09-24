"""HTTP surface for PulseAnalytics. One endpoint, signed-in sellers only.

    GET /api/pulse/analytics/seller/funnel?days=7

There is no ``seller_user_id`` parameter, and adding one would be the whole
vulnerability. The identity comes from the session and is passed to
``read`` as an argument, which refuses anything that is not a positive integer
rather than coercing it. An operator view over another seller's funnel would be
a different endpoint with a different gate; the cheapest way never to ship a
horizontal escalation is not to write the code path that could become one.

Why one window feeds every number
---------------------------------

The route resolves ``days`` once and hands the same window to the impression
read, the engagement read and the order read. ``seller_metrics.compute`` counts
whatever rows it is given, so feeding it every order the seller ever took while
the funnel above it covers seven days would make ``client_server_purchase_gap``
the difference between a week of clicks and a lifetime of sales — a number that
is not wrong about anything in particular, which is worse than being wrong.

Failure posture
---------------

Unlike the discovery serve endpoint, this one is allowed to fail. It backs a
dashboard a seller opened on purpose, not a feed that must render. But it fails
by reporting a measurement as absent, never by reporting it as zero: an
unreadable order table produces ``outcome.source == "unavailable"`` and ``None``
figures, and the client renders an em dash. A 0 would be a claim.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from flask import Blueprint, jsonify, request

from services.business_os.marketplace import seller_metrics
from services.commerce_discovery import subject
from services.pulse_analytics import funnel, read
from services.route_auth import auth_required

LOGGER = logging.getLogger(__name__)

analytics_blueprint = Blueprint("pulse_analytics", __name__)

API_PREFIX = "/api/pulse/analytics"

#: Bounds on the requested window. The floor is a day because an hour of
#: impressions is noise a seller would read as a trend. The ceiling is ninety
#: days because the impression scan is bounded by ``read.MAX_ROWS`` and a
#: request for a year would silently return a truncated window under the label
#: of a whole one.
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 90
DEFAULT_WINDOW_DAYS = 7


def _bot():
    import bot

    return bot


def _json(payload, status: int = 200):
    response = jsonify(payload)
    # One seller's own numbers. A cached copy served to a second seller is the
    # leak this module's whole read layer exists to prevent, and a shared proxy
    # would not know the two requests differ.
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _error(message: str, status: int = 400, *, code: str = ""):
    payload = {"ok": False, "message": message}
    if code:
        # `error_code` is what `pulseApi` reads; `error` is what the older web
        # handlers look for. Both, or the client collapses every failure to a
        # generic message.
        payload["error_code"] = code
        payload["error"] = code
    return _json(payload, status)


def _window_days(raw) -> int:
    """The requested window, clamped. An unreadable value is the default.

    Clamped rather than rejected: ``days=400`` is a client asking for more than
    this endpoint can honestly measure, and answering with ninety days *labelled
    ninety days* tells them so. A 400 here would take a dashboard down over a
    query string.
    """
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, days))


@analytics_blueprint.route(f"{API_PREFIX}/seller/funnel", methods=["GET"])
@auth_required
def api_pulse_analytics_seller_funnel():
    """Exposure through to outcome for the signed-in seller, over one window."""
    bot = _bot()
    try:
        bot.init_db()
        user = bot.api_account_user()
    except Exception:
        LOGGER.exception("PULSE_ANALYTICS_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return _error("Login required.", 401, code="LOGIN_REQUIRED")

    days = _window_days(request.args.get("days"))
    since_seconds = days * 24 * 60 * 60
    surface = str(request.args.get("surface") or "").strip().lower()

    conn = bot.db()
    try:
        try:
            import sqlite3

            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        cur = conn.cursor()
        # Handed over raw. `read` validates it and refuses anything that is not
        # a positive integer; an `int()` here would be a second, laxer gate in
        # front of the strict one — `int(1.5)` is 1, which addresses a different
        # seller without erroring.
        seller_id = user.get("user_id") if hasattr(user, "get") else None
        # Resolved once and reused. Each event read would otherwise re-query the
        # seller's listings, and the two reads must agree on the scope anyway —
        # a listing published between them would appear in one and not the
        # other.
        listing_ids = read.seller_listing_ids(cur, seller_id)
        impression_rows = read.impressions(
            cur, seller_id, listing_ids=listing_ids,
            since_seconds=since_seconds, surface=surface,
        )
        engagement_rows = read.engagements(
            cur, seller_id, listing_ids=listing_ids,
            since_seconds=since_seconds, surface=surface,
        )
        order_rows = read.orders(cur, seller_id, since_seconds=since_seconds)
    except read.SellerScopeError:
        # The session carried something that is not a seller identity. That is
        # a server-side bug, not a client error, and the safe response on an
        # authorisation path is to stop rather than to widen.
        LOGGER.exception("PULSE_ANALYTICS_SELLER_SCOPE_REFUSED")
        return _error("Analytics unavailable.", 500, code="ANALYTICS_UNAVAILABLE")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    metrics = None
    if order_rows is not None:
        # The same clock `read.orders` windowed against. Two clocks a
        # microsecond apart across a midnight boundary put today's takings in
        # yesterday's bucket.
        today = subject.now_utc().date()
        metrics = seller_metrics.compute(
            [],
            order_rows,
            today=today.isoformat(),
            recent_days=[(today - timedelta(days=n)).isoformat() for n in range(days)],
            baseline_day=(today - timedelta(days=days)).isoformat(),
        )

    report = funnel.summarize(
        impression_rows,
        engagement_rows,
        seller_metrics=metrics,
        orders=order_rows or (),
    )
    report["window_days"] = days
    if surface:
        report["surface"] = surface

    # One line per served funnel, carrying the shape of the answer rather than
    # the answer. A degraded report is indistinguishable from a quiet week on
    # the wire and from a healthy one in the logs unless the degradation is
    # counted: `outcome=unavailable` is the field that tells an operator the
    # order table was unreadable, and `listings=0` separates a seller with no
    # listings from a seller whose listings could not be resolved.
    #
    # No `subject_ref`, no listing ids, no money. This is an operational
    # signal, and an operational log that accretes commercial detail becomes a
    # second analytics store that nobody declared.
    LOGGER.info(
        "PULSE_ANALYTICS_FUNNEL_SERVED seller_user_id=%s window_days=%s surface=%s "
        "listings=%s impressions=%s engagements=%s outcome=%s",
        seller_id, days, surface or "-", len(listing_ids), len(impression_rows),
        len(engagement_rows), report["outcome"]["source"],
    )
    return _json({"ok": True, "funnel": report})


def register(app):
    app.register_blueprint(analytics_blueprint)
