"""HTTP surface for Market Pulse — the live crypto command center.

The phone never talks to CoinGecko. It talks to these endpoints, which read the
shared market foundation (``services.market_pulse``) that the dashboard board
and Pulse Briefings already poll. One provider call per window serves every
user, so user count does not scale provider spend.

Two kinds of data meet here and are kept strictly apart:

  * The market list, global strip, trending and charts are **shared** — the same
    bytes for every caller, cached, and safe to serve from one fetch.
  * "Watching", the favorite star and the alert badge are **per account**, read
    with an explicit ``user_id`` through ``dashboard_crypto_command_center``.

They are merged only inside a single authenticated request. Nothing user-scoped
is ever written into the shared cache, which is what stops User A's watchlist
appearing on User B's screen when both hit the same cached board.

Writes live in the existing crypto API: watchlist rows go through
``/api/dashboard/crypto/watchlists/...`` and alerts through the canonical alert
engine. There is no second watchlist store and no second alert engine here.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services import market_pulse

LOGGER = logging.getLogger(__name__)

market_pulse_blueprint = Blueprint("pulse_market_pulse", __name__)

API_PREFIX = "/api/pulse/market"

MAX_LIMIT = 80
DEFAULT_LIMIT = 50


def _bot():
    import bot

    return bot


def _current_user():
    try:
        return _bot().api_account_user()
    except Exception:  # noqa: BLE001 - an auth lookup failure is "not signed in"
        return None


def _require_user():
    user = _current_user()
    if not user:
        return None, (jsonify({"ok": False, "message": "Login required."}), 401)
    return user, None


def _json(payload, status=200):
    response = jsonify(payload)
    # Prices carry their own freshness metadata; an HTTP cache layered on top
    # would put a second, invisible age on the same numbers.
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _limit(default: int = DEFAULT_LIMIT) -> int:
    try:
        value = int(request.args.get("limit") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_LIMIT))


def _overlay(user):
    """The caller's own watching / favorite / alert symbols, or empty on failure.

    A personalisation read that fails must not take the market screen down with
    it: the shared list is still perfectly true without the badges.
    """
    empty = {"watching": [], "favorites": [], "alertCounts": {}, "available": False}
    if not user:
        return empty
    bot = _bot()
    conn = None
    try:
        from services import dashboard_crypto_command_center as crypto_center

        conn = bot.db()
        overlay = crypto_center.personal_overlay(conn, int(user["user_id"]))
        overlay["available"] = True
        return overlay
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("MARKET_PULSE_OVERLAY_UNAVAILABLE user=%s error=%s", (user or {}).get("user_id"), exc)
        return empty
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _apply_overlay(assets, overlay):
    watching = set(overlay.get("watching") or [])
    favorites = set(overlay.get("favorites") or [])
    counts = overlay.get("alertCounts") or {}
    for asset in assets:
        symbol = asset.get("symbol") or ""
        asset["watching"] = symbol in watching
        asset["favorite"] = symbol in favorites
        asset["alertCount"] = int(counts.get(symbol, 0) or 0)
    return assets


@market_pulse_blueprint.route(f"{API_PREFIX}/snapshot", methods=["GET"])
def market_pulse_snapshot():
    """Everything the screen needs on open: strip, list, freshness, overlay."""
    user, error = _require_user()
    if error:
        return error
    category = (request.args.get("category") or "all").strip().lower()
    try:
        payload = market_pulse.snapshot(category, _limit())
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("MARKET_PULSE_SNAPSHOT_FAILED error=%s", exc)
        return _json({"ok": False, "message": "Market data is temporarily unavailable."}, 503)
    overlay = _overlay(user)
    assets = payload.get("assets") or []
    if category == "watchlist":
        # The watchlist chip filters the shared list by the caller's own symbols
        # rather than fetching anything extra. An asset the user watches that is
        # outside the top 50 is simply absent here; the watchlist screen itself
        # remains the complete view.
        watching = set(overlay.get("watching") or [])
        assets = [a for a in assets if a.get("symbol") in watching]
        payload["assets"] = assets
    _apply_overlay(assets, overlay)
    payload["ok"] = True
    payload["personalized"] = bool(overlay.get("available"))
    return _json(payload)


@market_pulse_blueprint.route(f"{API_PREFIX}/global", methods=["GET"])
def market_pulse_global():
    """The global strip alone, for a cheap foreground refresh."""
    _, error = _require_user()
    if error:
        return error
    return _json({"ok": True, "global": market_pulse.global_metrics()})


@market_pulse_blueprint.route(f"{API_PREFIX}/trending", methods=["GET"])
def market_pulse_trending():
    """CoinGecko's real trending list — not a sort of the price board."""
    user, error = _require_user()
    if error:
        return error
    payload = market_pulse.trending()
    _apply_overlay(payload.get("assets") or [], _overlay(user))
    payload["ok"] = True
    return _json(payload)


@market_pulse_blueprint.route(f"{API_PREFIX}/search", methods=["GET"])
def market_pulse_search():
    """Search the same board that supplies prices, so every hit is openable."""
    user, error = _require_user()
    if error:
        return error
    payload = market_pulse.search(request.args.get("q") or "", _limit(25))
    _apply_overlay(payload.get("assets") or [], _overlay(user))
    payload["ok"] = True
    return _json(payload)


@market_pulse_blueprint.route(f"{API_PREFIX}/assets/<symbol>/history", methods=["GET"])
def market_pulse_history(symbol: str):
    """Real price history for one asset and range.

    Ranges accept both the stored keys (1M, 3M) and the labels the chart shows
    (30D, 90D); ``market_data`` owns the aliasing. There is no synthesised
    series behind any of them — an unanswerable range returns no points.
    """
    _, error = _require_user()
    if error:
        return error
    range_key = (request.args.get("range") or "24H").strip().upper()
    payload = market_pulse.asset_history(symbol, range_key)
    payload["ok"] = bool(payload.get("points"))
    return _json(payload)


def register(app) -> None:
    app.register_blueprint(market_pulse_blueprint)
