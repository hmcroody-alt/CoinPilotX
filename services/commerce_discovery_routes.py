"""HTTP surface for organic commerce discovery.

Six endpoints, all under ``/api/pulse/commerce/discovery``, all signed-in-only.
The client contract is ``mobile-native/src/api/commerceDiscovery.ts``.

    POST   /<surface>                 placements for feed | reels | messenger
    GET    /marketplace/modules       the Marketplace shelves
    POST   /events/impression         rendered, or became visible
    POST   /events/engagement         click → product_view → … → purchase
    POST   /events/feedback           hide / not_interested / see_fewer / …
    POST   /explain/<placement_id>   "Why am I seeing this?"

Why the serve endpoint is a POST
--------------------------------

It reads, so GET is the obvious shape, and it was a GET first. It is a POST
because the request body carries the *context* — the category and tags of the
post or reel the user is currently looking at — and that is a description of
what someone is reading right now. In a URL it would land in access logs,
proxy caches, and analytics referrers. Memory of this codebase: never put
personal or behavioural data in a query string. The method follows the payload.

Failure posture
---------------

Serve **never** returns an error status. Opted out, rate limited, engine off,
catalogue empty, unexpected exception — all are ``200 {"ok": true,
"placements": []}``. The client has exactly one rendering path and no error
branch, which is what makes "a recommendation failure must never break the
feed" a structural property rather than a promise.

The event endpoints do return errors, because a client that is posting a
malformed or forged placement id needs to be told and there is no user-visible
surface to degrade.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock

from flask import Blueprint, jsonify, request

from services.commerce_discovery import config, engine, events, promotion, ranking, schema
from services.route_auth import auth_required

LOGGER = logging.getLogger(__name__)

discovery_blueprint = Blueprint("pulse_commerce_discovery", __name__)

API_PREFIX = "/api/pulse/commerce/discovery"

#: Marketplace shelves, in the order they are offered. Each is a serve call
#: with a different context, not a different pipeline — one ranker, several
#: questions, so a listing cannot be eligible for a shelf and ineligible for
#: the feed.
MARKETPLACE_MODULES = (
    ("recommended_for_you", ranking.REASON_MATCHES_INTERESTS),
    ("because_you_viewed", ranking.REASON_BECAUSE_YOU_VIEWED),
    ("trending", ranking.REASON_TRENDING),
    ("new_arrivals", ranking.REASON_NEW_ARRIVAL),
    ("from_sellers_you_follow", ranking.REASON_SELLER_FOLLOWED),
    ("new_to_marketplace", ranking.REASON_EXPLORE),
    ("popular", ranking.REASON_POPULAR),
)

#: A shelf with fewer than this many products is dropped. A two-card carousel
#: reads as a loading failure, and the Marketplace already had this convention.
MIN_MODULE_ITEMS = 3


def _bot():
    import bot

    return bot


# --- rate limiting ----------------------------------------------------------
# In-process, per subject, sliding window. Deliberately not Redis-backed: the
# limit is abuse control for a read endpoint that spends a bounded query budget,
# and a limiter that needs a healthy Redis to allow traffic would make an
# optional dependency load-bearing for the feed.
_RATE_LOCK = Lock()
_RATE_BUCKETS: dict[str, deque] = {}
#: Bound on distinct tracked subjects, so a worker cannot accumulate a bucket
#: per user id forever. Evicted oldest-first; the cost of evicting an active
#: subject is that they get a fresh window, which is the harmless direction.
_RATE_MAX_SUBJECTS = 4096


def _rate_limited(ref: str) -> bool:
    limit = config.request_rate_max()
    window = config.request_rate_window_seconds()
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.get(ref)
        if bucket is None:
            if len(_RATE_BUCKETS) >= _RATE_MAX_SUBJECTS:
                _RATE_BUCKETS.pop(next(iter(_RATE_BUCKETS)), None)
            bucket = deque()
            _RATE_BUCKETS[ref] = bucket
        while bucket and (now - bucket[0]) > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        return False


def reset_rate_limiter() -> None:
    """Clear the limiter. Tests only — a per-file bucket leaks across tests."""
    with _RATE_LOCK:
        _RATE_BUCKETS.clear()


# --- plumbing ---------------------------------------------------------------
def _json(payload, status: int = 200):
    response = jsonify(payload)
    # Placements are per-viewer and carry a single-use token. A cached copy
    # served to a second viewer would both mis-attribute the impression and
    # leak one person's recommendations to another.
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


def _empty():
    """The one shape every serve failure returns."""
    return _json({"ok": True, "placements": []})


def _require_user():
    try:
        user = _bot().api_account_user()
    except Exception:
        LOGGER.exception("COMMERCE_DISCOVERY_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return None, _error("Login required.", 401, code="LOGIN_REQUIRED")
    return user, None


def _with_db(handler):
    """Run ``handler(cur, conn)`` on a connection this function owns.

    ``close()`` is in a ``finally``, not on the success path. A close reachable
    only when nothing raised leaks one pooled connection per failure, and this
    pool is 8+8 with a 3s timeout — a few dozen failures is an outage on every
    other feature sharing it.
    """
    bot = _bot()
    conn = bot.db()
    try:
        try:
            import sqlite3

            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        cur = conn.cursor()
        result = handler(cur, conn)
        conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _context_from_request(payload: dict) -> dict:
    """The surrounding-content signal, bounded and stripped of everything else.

    An explicit allowlist rather than passing the body through: the client is
    describing what the user is looking at, and the only fields ranking uses are
    the four below. Accepting the rest would quietly build a channel for a
    client to send arbitrary behavioural data into a server-side store.
    """
    raw = payload.get("context")
    if not isinstance(raw, dict):
        return {}
    tags = raw.get("tags")
    if isinstance(tags, (list, tuple)):
        tags = [str(tag)[:40] for tag in list(tags)[:12]]
    else:
        tags = []
    return {
        "category": str(raw.get("category") or "")[:80],
        "subcategory": str(raw.get("subcategory") or "")[:80],
        "topic": str(raw.get("topic") or "")[:80],
        "tags": tags,
    }


def _request_meta() -> dict:
    """Non-identifying request signals for the event row.

    No IP, no user agent, no user id. The platform and app version are here
    because "is the Reels chip being reported by old builds" is a real
    operational question; nothing else has earned a place.
    """
    return {
        "platform": (request.headers.get("X-Pulse-Platform") or "")[:24],
        "app_version": (request.headers.get("X-Pulse-App-Version") or "")[:24],
    }


# --- serve ------------------------------------------------------------------
@discovery_blueprint.route(f"{API_PREFIX}/<surface>", methods=["POST"])
@auth_required
def commerce_discovery_serve(surface):
    """Placements for one social surface. Never fails visibly."""
    user, err = _require_user()
    if err:
        return err

    if str(surface or "").strip().lower() not in schema.SURFACES:
        return _empty()

    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "")[:64]
    requested = payload.get("limit")
    limit = None
    if requested is not None:
        try:
            limit = max(0, min(10, int(requested)))
        except (TypeError, ValueError):
            limit = None

    from services.commerce_discovery import subject as cd_subject

    if _rate_limited(cd_subject.subject_ref(user["user_id"])):
        # Silently empty, not 429. A client being rate limited is not a
        # situation the *user* should be shown anything about, and a 429 on a
        # feed sub-request is a retry storm waiting to happen.
        return _empty()

    def handler(cur, conn):
        bot = _bot()
        placements = engine.serve(
            cur, user["user_id"], surface,
            context=_context_from_request(payload),
            session_id=session_id,
            limit=limit,
            promotion_class=promotion.ORGANIC,
            parse_price=bot.parse_price_label_to_cents,
            serialize=bot.pulse_marketplace_listing_payload,
        )
        return _json({
            "ok": True,
            "placements": placements,
            "surface": surface,
            "visible_percent_threshold": config.VISIBLE_PERCENT_THRESHOLD,
            "visible_dwell_ms": config.VISIBLE_DWELL_MS,
        })

    try:
        return _with_db(handler)
    except Exception:
        LOGGER.exception("COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED surface=%s", surface)
        return _empty()


@discovery_blueprint.route(f"{API_PREFIX}/marketplace/modules", methods=["GET"])
@auth_required
def commerce_discovery_marketplace_modules():
    """The Marketplace shelves.

    Each shelf is the same ranked pool filtered to one reason code, so a product
    appears under "Trending" only if the ranker genuinely called it trending.
    Building the shelves from separate hand-written queries — which is the
    obvious implementation — is how a product ends up in "New arrivals" and
    "Trending" simultaneously with two different justifications.
    """
    user, err = _require_user()
    if err:
        return err

    def handler(cur, conn):
        bot = _bot()
        pool = engine.serve(
            cur, user["user_id"], "marketplace",
            context=None,
            session_id=str(request.args.get("session_id") or "")[:64],
            limit=config.marketplace_module_limit() * 6,
            promotion_class=promotion.ORGANIC,
            parse_price=bot.parse_price_label_to_cents,
            serialize=bot.pulse_marketplace_listing_payload,
        )
        by_reason: dict[str, list] = {}
        for placement in pool:
            by_reason.setdefault(placement.get("reason") or "", []).append(placement)

        modules = []
        for key, reason in MARKETPLACE_MODULES:
            items = by_reason.get(reason) or []
            if len(items) < MIN_MODULE_ITEMS:
                continue
            modules.append({
                "key": key,
                "reason": reason,
                "title_key": f"commerce:discovery.module.{key}",
                "placements": items,
            })
            if len(modules) >= config.marketplace_module_limit():
                break

        return _json({"ok": True, "modules": modules})

    try:
        return _with_db(handler)
    except Exception:
        LOGGER.exception("COMMERCE_DISCOVERY_MODULES_FAILED")
        return _json({"ok": True, "modules": []})


# --- events -----------------------------------------------------------------
def _event_route(runner):
    """Shared shape for the three event endpoints.

    One wrapper because the three differ only in which recorder they call: the
    auth check, the placement/token extraction, the error vocabulary and the
    unexpected-exception posture are identical, and three copies would be three
    places to forget that a ``DiscoveryEventError`` is a 400 and everything else
    is a 500 that must not leak its message.
    """
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    placement_id = payload.get("placement_id")
    token = payload.get("impression_token") or payload.get("token")

    def handler(cur, conn):
        return _json(runner(cur, user, payload, placement_id, token))

    try:
        return _with_db(handler)
    except events.DiscoveryEventError as exc:
        return _error(exc.message, 400, code=exc.code)
    except promotion.PromotionClassError:
        LOGGER.exception("COMMERCE_DISCOVERY_PROMOTION_CLASS_VIOLATION")
        return _error("This placement cannot be recorded here.", 400, code="INVALID_PLACEMENT")
    except Exception:
        LOGGER.exception("COMMERCE_DISCOVERY_EVENT_FAILED")
        return _error("Could not record that right now.", 500, code="NETWORK_ERROR")


@discovery_blueprint.route(f"{API_PREFIX}/events/impression", methods=["POST"])
@auth_required
def commerce_discovery_impression():
    def runner(cur, user, payload, placement_id, token):
        result = events.record_impression(
            cur, placement_id, token,
            viewer_user_id=user["user_id"],
            visible=bool(payload.get("visible")),
            view_duration_ms=payload.get("view_duration_ms") or 0,
            request_meta=_request_meta(),
        )
        return {"ok": True, "duplicate": bool(result.get("duplicate"))}

    return _event_route(runner)


@discovery_blueprint.route(f"{API_PREFIX}/events/engagement", methods=["POST"])
@auth_required
def commerce_discovery_engagement():
    def runner(cur, user, payload, placement_id, token):
        result = events.record_engagement(
            cur, placement_id, token,
            str(payload.get("action") or ""),
            value_minor=payload.get("value_minor") or 0,
            currency=str(payload.get("currency") or ""),
            order_ref=str(payload.get("order_ref") or ""),
            request_meta=_request_meta(),
        )
        return {"ok": True, "duplicate": bool(result.get("duplicate"))}

    return _event_route(runner)


@discovery_blueprint.route(f"{API_PREFIX}/events/feedback", methods=["POST"])
@auth_required
def commerce_discovery_feedback():
    """Record a negative signal and apply the suppression it implies.

    Returns ``suppressed`` so the client knows the request was honoured and can
    collapse the card without waiting to observe the absence on a later fetch.
    """
    def runner(cur, user, payload, placement_id, token):
        action = str(payload.get("action") or "")
        meta = dict(_request_meta())
        # The category travels with the feedback so "see fewer like this" has
        # something to soften. Bounded here rather than trusted downstream.
        meta["category"] = str(payload.get("category") or "")[:80]
        result = events.record_feedback(cur, placement_id, token, action, request_meta=meta)
        return {
            "ok": True,
            "duplicate": bool(result.get("duplicate")),
            "action": action,
            "suppressed": True,
        }

    return _event_route(runner)


@discovery_blueprint.route(f"{API_PREFIX}/explain/<placement_id>", methods=["POST"])
@auth_required
def commerce_discovery_explain(placement_id):
    """"Why am I seeing this?" — reason code and factor names, never scores.

    POST for the same reason serve is: the placement token is an HMAC
    capability, and a capability in a query string is a credential written to
    every access log and proxy cache between here and the phone. It reads, but
    the method follows the payload.
    """
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("impression_token") or payload.get("token") or "")

    def handler(cur, conn):
        return _json(events.explain(cur, placement_id, token))

    try:
        return _with_db(handler)
    except events.DiscoveryEventError as exc:
        return _error(exc.message, 400, code=exc.code)
    except Exception:
        LOGGER.exception("COMMERCE_DISCOVERY_EXPLAIN_FAILED")
        return _error("Could not load that right now.", 500, code="NETWORK_ERROR")


def register(app) -> None:
    app.register_blueprint(discovery_blueprint)
