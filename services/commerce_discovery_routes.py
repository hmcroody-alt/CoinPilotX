"""HTTP surface for organic commerce discovery.

Seven endpoints, all under ``/api/pulse/commerce/discovery``, all
signed-in-only. The client contract is
``mobile-native/src/api/commerceDiscovery.ts``.

    POST   /<surface>                 placements for feed | reels | messenger
    GET    /marketplace/modules       the Marketplace shelves
    GET    /taggable-products         the composer's picker (writes nothing)
    POST   /events/impression         rendered, or became visible
    POST   /events/engagement         click → product_view → … → purchase
    POST   /events/feedback           hide / not_interested / see_fewer / …
    POST   /explain/<placement_id>   "Why am I seeing this?"

Six of the seven are the *viewer's* side of discovery. ``/taggable-products`` is
the creator's, and it is here rather than beside the composer in ``bot.py``
because the question it answers is ``eligibility``'s — "will this listing ever be
pushed at anybody" — and moving that judgement into the monolith would make a
second copy of it. It still writes nothing: the write path is
``bot.pulse_attach_products_to_content`` over ``tagging.attach``.

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
from typing import Optional

from flask import Blueprint, jsonify, request

from services import db as db_module
from services.commerce_discovery import (
    config, engine, events, promotion, ranking, schema, suitability,
)
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


def _anchor_listing_id(payload: dict) -> int:
    """The product the viewer is looking at, on the surfaces that have one.

    Unvalidated on purpose beyond being an integer: this id is only ever used to
    *remove* a candidate and to look up a row the caller can already see. There
    is no value a client can send that widens what comes back, which is what
    makes it safe to accept at all.
    """
    try:
        return max(0, int(payload.get("listing_id") or 0))
    except (TypeError, ValueError):
        return 0


def _anchor_context(cur, listing_id: int) -> dict:
    """The anchor product's own taxonomy, read from the database.

    Read server-side rather than taken from the request body even though
    :func:`_context_from_request` would accept the same fields. The difference is
    that this surface labels its results ``similar_to_this_product``, and a claim
    of similarity is only true if both sides of the comparison are ours. A client
    that sent ``category: "watches"`` while displaying a lawnmower would otherwise
    get a row of watches under the word "similar".
    """
    if not listing_id:
        return {}
    try:
        cur.execute(
            "SELECT category, subcategory FROM marketplace_listings WHERE id=? LIMIT 1",
            (int(listing_id),),
        )
        row = cur.fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    # row_values, not tuple(row): a Postgres row is a Mapping, so iterating it
    # yields column *names* and this whole function would match on the literal
    # string "category" in production and nowhere else.
    values = db_module.row_values(row)
    if len(values) < 2:
        return {}
    return {
        "category": str(values[0] or "")[:80],
        "subcategory": str(values[1] or "")[:80],
        "topic": "",
        "tags": [],
    }


#: The columns :func:`suitability.assess_adjacency` reads off a post row.
#:
#: Listed rather than ``SELECT *`` for two reasons. The assessment keys off dict
#: keys, so a star select would make the gate's inputs whatever
#: ``add_columns_if_missing`` last appended to ``pulse_posts`` — a column named
#: ``topic`` or ``tags`` added for some unrelated feature would silently start
#: feeding the sensitivity scan. And a post row is a wide row on the hottest
#: table in the product; this reads eight columns of the one row the viewer is
#: already looking at.
#:
#: Every name here is in ``bot.init_db()``'s ``pulse_posts`` block. ``body`` is
#: the one that matters: it is the full text, which is the entire point of this
#: read.
_POST_COLUMNS = ("post_type", "moderation_status", "risk_score",
                 "title", "body", "ai_summary", "tags_json", "ai_tags_json")

#: The ``pulse_reels`` columns the assessment reads, as ``(key, expression)``.
#:
#: A reel is not a ``pulse_posts`` row — it is a ``pulse_reels`` row *joined* to
#: one — and the half of it that matters here lives on the reel. ``caption`` is
#: the field a viewer reads: ``pulse_reel_payload`` sets
#: ``caption = reel.caption or post.body``, preferring the reel's own words
#: because a reel's post body is routinely empty. Reading only the post row on
#: this surface would therefore be a half-blind check.
#:
#: The two ``COALESCE``es copy ``pulse_reel_payload``'s own convention for the
#: same columns. Measured against production: all 71 reels are ``approved`` with
#: no NULLs, so this is convention-following rather than load-bearing — but
#: ``moderation_status`` is an ``add_columns_if_missing`` addition rather than an
#: original column, and a NULL read as ``""`` would fail the cleared-states check
#: and refuse every reel on a database that predates the backfill.
#:
#: Deliberately absent: ``safety_score``. It is **inverted** relative to
#: ``risk_score`` — 100 is the healthy default — so feeding it in under that name
#: would refuse every good reel and clear every dangerous one. ``post_type``,
#: ``title``, ``body`` and ``risk_score`` are absent because a reel row has none
#: of them; they belong to the post row, which is assessed separately.
_REEL_COLUMNS = (
    ("caption", "caption"),
    ("category", "category"),
    ("ai_tags_json", "ai_tags_json"),
    ("moderation_status", "COALESCE(moderation_status,'approved')"),
)

#: Three outcomes, kept distinct because two of them must not be treated alike.
#: A row that is *gone* is evidence; a row we could not *read* is not.
_ROW_FOUND = "found"
_ROW_ABSENT = "absent"
_ROW_UNREADABLE = "unreadable"


def _content_post_id(payload: dict) -> int:
    """The post the viewer is reading, on the surfaces that have exactly one.

    Same shape and same safety argument as :func:`_anchor_listing_id`, with one
    difference worth stating plainly rather than leaving to be inferred: this id
    is *not* purely narrowing. It is used to decide whether commerce is refused,
    so a client that sent a cheerful post's id while displaying a bereavement
    would pass a check it should have failed.

    That is accepted, because the threat this closes is not a hostile client. A
    hostile client renders whatever carousel it likes without asking us. The
    thing being fixed is our *own* client being honest and lossy — see
    :func:`_content_refusal`. :func:`suitability.assess_context` still runs on
    the wire regardless, which is the check a forged id cannot get past.
    """
    try:
        return max(0, int(payload.get("post_id") or 0))
    except (TypeError, ValueError):
        return 0


def _content_post(cur, post_id: int) -> tuple[str, dict]:
    """``(outcome, row)`` for one ``pulse_posts`` row, by id.

    Deleted posts are not returned: ``deleted_at IS NULL`` is in the query rather
    than checked afterwards, so a tombstoned post is :data:`_ROW_ABSENT` and
    takes the refusal path with everything else the server cannot see.

    Visibility is deliberately *not* filtered. This is not an authorization read
    — nothing from the row is returned to the caller and nothing derived from it
    widens the response; the only two things that can come out of it are "serve
    as before" and "serve nothing". Adding a viewer join would make a followers-
    only post unreadable and therefore refused, which is a commerce blackout on
    private posts achieved by accident.
    """
    if not post_id:
        return _ROW_ABSENT, {}
    try:
        cur.execute(
            "SELECT " + ", ".join(_POST_COLUMNS) +
            " FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1",
            (int(post_id),),
        )
        row = cur.fetchone()
    except Exception:
        # Not `_ROW_ABSENT`. A failed read is not evidence that the post is
        # unsuitable, and conflating the two would turn one bad minute on the
        # database into commerce disappearing from every post page — while the
        # posts that genuinely need suppressing carried on being served for as
        # long as the query worked.
        LOGGER.warning("COMMERCE_DISCOVERY_POST_READ_FAILED post_id=%s",
                       post_id, exc_info=True)
        return _ROW_UNREADABLE, {}
    if not row:
        return _ROW_ABSENT, {}
    # row_values, not tuple(row): iterating a Postgres row yields column *names*,
    # so zipping the raw row would build `{"post_type": "post_type", ...}` and the
    # scan would run over this module's own column list in production only.
    values = db_module.row_values(row)
    if len(values) != len(_POST_COLUMNS):
        return _ROW_UNREADABLE, {}
    return _ROW_FOUND, dict(zip(_POST_COLUMNS, values))


def _content_reel(cur, post_id: int) -> tuple[str, dict]:
    """``(outcome, row)`` for the ``pulse_reels`` row attached to a post.

    Keyed by ``post_id`` and not by the reel's own id, because ``post_id`` is
    what the client can be asked for on both surfaces — the id a reel client
    holds is ``pulse_reels.id``, a different id space from ``pulse_posts.id``, and
    accepting whichever one a caller felt like sending is how a reel id silently
    reads a stranger's post. ``pulse_reels.post_id`` is ``UNIQUE``, so this is a
    single-row lookup on an indexed column, and ``pulse_reel_payload`` already
    treats it as one.

    ``status`` is filtered here rather than assessed, matching the post read: a
    deleted reel is :data:`_ROW_ABSENT`. ``moderation_status`` is *not* filtered,
    because it is the thing being judged — note that ``pulse_reel_payload`` only
    withholds ``blocked``, so a ``needs_review`` reel is served to viewers today
    and would pass unexamined if this query filtered on the same predicate the
    payload does.
    """
    if not post_id:
        return _ROW_ABSENT, {}
    keys = tuple(key for key, _ in _REEL_COLUMNS)
    try:
        cur.execute(
            "SELECT " + ", ".join(expr for _, expr in _REEL_COLUMNS) +
            " FROM pulse_reels WHERE post_id=?"
            " AND COALESCE(status,'active')!='deleted' LIMIT 1",
            (int(post_id),),
        )
        row = cur.fetchone()
    except Exception:
        # Same reasoning as the post read, and it matters more here: this table
        # is younger than the post table and the two reads are separate queries,
        # so a reels-only failure must not black out a surface the post read was
        # perfectly able to judge.
        LOGGER.warning("COMMERCE_DISCOVERY_REEL_READ_FAILED post_id=%s",
                       post_id, exc_info=True)
        return _ROW_UNREADABLE, {}
    if not row:
        return _ROW_ABSENT, {}
    values = db_module.row_values(row)
    if len(values) != len(keys):
        return _ROW_UNREADABLE, {}
    return _ROW_FOUND, dict(zip(keys, values))


def _content_refusal(cur, payload: dict, context: dict, surface: str) -> Optional[dict]:
    """The verdict that refuses this content request, or ``None`` to serve it.

    Called before retrieval, deliberately. A refused request must cost no
    candidate pool, no exposure-ledger write and no impression token, and a check
    placed after scoring would already have paid for all three. See
    :func:`suitability.assess_context` for why this is a refusal rather than a
    score penalty: on the measured grief post, the more the sensitive content
    matched, the *better* it scored, so every floor in the system cleared.

    Two checks, in the order of how much they can be trusted.

    **The wire** (:func:`suitability.assess_context`). Only
    :data:`suitability.SENSITIVE_CONTEXT` refuses, and the narrowness is the
    decision rather than an oversight. ``assess_context`` also answers
    ``NO_SUBJECT``, and acting on *that* would be a different and much larger
    change: :func:`_context_from_request` returns all four keys whenever the body
    carries a ``context`` object at all, so a reel whose context is a bare
    ``{"category": "watches"}`` — which ``reelContext.ts`` returns by design for a
    reel with no caption and no tags — reads as ``NO_SUBJECT`` while being a
    perfectly good ranking signal. Refusing it would switch commerce off for that
    whole population to protect nobody. A client's omission is not evidence about
    the content.

    **The post itself** (:func:`suitability.assess_adjacency`), when the body
    named one. This is the half that was missing, and the gap was not theoretical:

    * ``postContext.ts`` caps every field it sends at ``MAX_FIELD_CHARS = 80``
      and derives ``topic`` from ``post.title`` or, failing that, ``post.body``.
      A bereavement post very commonly opens with a preamble, so the words the
      rule exists to catch are past the cut. Measured on
      ``"Thank you all so much for the kind words these past few days, it has
      meant more to us than I can say. We lost my father on Tuesday morning…"``:
      the full body answers ``SENSITIVE_CONTEXT``, the 80 characters that reach
      the wire answer ``PERMITTED``. The truncation is correct — an unbounded
      free-text field from a client has no business being unbounded — so the fix
      is not a bigger cap, it is reading the row.
    * Every structural gate is invisible to the wire. ``post_type``,
      ``moderation_status`` and ``risk_score`` are not fields a client sends, so
      a ``memorial``, an unmoderated post and a high-risk post all passed.

    :func:`suitability.assess_adjacency` and not :func:`suitability.assess`,
    because ``assess`` adds the ``NO_SUBJECT`` rule and would refuse a
    caption-less post — the same population the wire check deliberately spares.
    This is the same choice ``suitability.annotate`` makes for the feed.

    **The reel**, on ``reels`` only. A reel is a ``pulse_reels`` row joined to a
    ``pulse_posts`` row, and the words the viewer actually reads are on the reel:
    ``pulse_reel_payload`` sets ``caption = reel.caption or post.body``. So on
    that surface the post row alone answers about a field the screen may not even
    be showing, and a bereavement said in a reel caption over an empty post body
    would read as a post with nothing in it. Both rows are assessed and **either**
    may refuse.

    Two rows rather than one merged row, because merging needs a winner for
    ``moderation_status`` and there is no right answer: a reel cleared over a post
    that was not, or the reverse, must both refuse, and whichever way the merge
    resolved would silently discard one of the two states. The cost is that a
    :data:`suitability.CORROBORATED` match spanning a reel's caption and its
    post's body is not seen — accepted, and not really a loss, because the caption
    *replaces* the body in the payload rather than adding to it. The two texts are
    alternatives, so scanning them apart is what matches the screen.

    An **absent** reel row does not refuse, which is the one place this departs
    from the post read. A video post with no ``pulse_reels`` row is legitimate —
    ``pulse_reel_payload`` synthesises one from the post and serves it — so
    treating the absence as evidence would refuse a real, healthy population. The
    post row has already been read and judged by that point, so nothing is
    unexamined; an absent reel adds no text, not an unknown one.

    The precedent for reading server-side what the body already offered is
    :func:`_anchor_context`, which replaces a client-described product taxonomy
    with the listing's own for the same reason in a different shape.

    What this still does not close: the feed. ``useFeedCommerce.ts`` sends no
    context and no post id because the feed's commerce row is a sibling row
    *between* posts rather than an attachment to one, so there is no single post
    to name. That surface is covered instead by ``suitability.annotate``
    stamping every post in the feed payload, and by the client declining to
    insert beside one that says no.
    """
    verdict = suitability.assess_context(context)
    if verdict["code"] == suitability.SENSITIVE_CONTEXT:
        return verdict

    post_id = _content_post_id(payload)
    if not post_id:
        # No id: an older build, or a surface that has no single post. The wire
        # check above is all there is, which is what shipped before this.
        return None

    outcome, row = _content_post(cur, post_id)
    if outcome == _ROW_UNREADABLE:
        return None

    # `_ROW_ABSENT` arrives here as an empty row on purpose rather than as its
    # own branch: `assess_adjacency({})` already answers NO_SUBJECT with "no
    # content was supplied to assess", which is exactly the situation — the
    # client named a post that is deleted, tombstoned, or an id from a table this
    # is not, so the server has no evidence about what is on the screen. The
    # entire reason for accepting the id was to stop relying on the client's
    # description, and falling back to it here would undo that for precisely the
    # requests where it is least trustworthy. The cost of refusing is a missing
    # product row on a screen showing a post our own database says is gone.
    row_verdict = suitability.assess_adjacency(row)
    if not row_verdict["permitted"]:
        return row_verdict

    if surface != "reels":
        return None

    # Reached only after the post row cleared, so this can add a refusal but can
    # never overturn one — which is the whole of "either may refuse" in code.
    reel_outcome, reel_row = _content_reel(cur, post_id)
    if reel_outcome != _ROW_FOUND:
        # Absent *and* unreadable serve, for the two different reasons in the
        # docstring: an absent reel is a legitimate video post, an unreadable one
        # is a database we could not ask. Neither is evidence.
        return None

    reel_verdict = suitability.assess_adjacency(reel_row)
    return None if reel_verdict["permitted"] else reel_verdict


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

    # Normalised once and reused. The check below used to lowercase a copy and
    # leave the original in play, so `/reels` and `/REELS` both passed it and
    # then took different paths through everything downstream — the second one
    # would have been served feed cadence and reported its events under a
    # surface name nothing else writes.
    surface = str(surface or "").strip().lower()
    if surface not in schema.SURFACES:
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

    anchor_id = _anchor_listing_id(payload)

    def handler(cur, conn):
        bot = _bot()
        context = _context_from_request(payload)
        content_post_id = 0
        if surface == "product_detail":
            # The anchor's own taxonomy replaces the client's description of it.
            # An empty read means the listing is gone, and falling back to the
            # body would let the row keep claiming similarity to a product the
            # server can no longer see.
            context = _anchor_context(cur, anchor_id)
        elif surface in suitability.CONTENT_SURFACES:
            # Creator-tagged products are keyed by this id. Resolved here, inside
            # the `CONTENT_SURFACES` branch, and left at 0 everywhere else —
            # deliberately, because the label a tag earns ("the creator attached
            # this to this post") is only truthful where a post is on screen.
            # Messenger and Marketplace have no post, so a `post_id` in their
            # request body describes nothing the viewer can look at, and honouring
            # it would put a creator's product in a private conversation under a
            # claim about content that is not there.
            #
            # Read from the body here but *looked up* in the engine, below the
            # suitability refusal. That ordering is the brief's point and there is
            # a test for it: a creator tag must not be able to force commerce onto
            # a post `suitability.assess` refuses, so the refusal has to `return`
            # before anything resolves a tag.
            content_post_id = _content_post_id(payload)
            # The whole rule, and why it is two checks rather than one, is in
            # `_content_refusal`'s docstring. It runs before retrieval.
            verdict = _content_refusal(cur, payload, context, surface)
            if verdict is not None:
                # `_empty()`, the same answer opted-out and rate-limited get.
                # The client has one rendering path and no error branch, so a
                # refusal is invisible by construction — which is the point: the
                # viewer of a bereavement post should not be told that commerce
                # was considered and declined.
                #
                # Logged without the content or the matched text. The category
                # and evidence tier are enough to audit the rule and to notice
                # if it ever stops firing; the post's words are not ours to put
                # in a log line.
                LOGGER.info(
                    "COMMERCE_DISCOVERY_SUITABILITY_REFUSED "
                    "surface=%s code=%s category=%s evidence=%s",
                    surface, verdict["code"], verdict.get("category"),
                    verdict.get("evidence"),
                )
                return _empty()
        placements = engine.serve(
            cur, user["user_id"], surface, conn=conn,
            context=context,
            session_id=session_id,
            limit=limit,
            promotion_class=promotion.ORGANIC,
            parse_price=bot.parse_price_label_to_cents,
            serialize=bot.pulse_marketplace_listing_payload,
            exclude_listing_ids=(anchor_id,) if anchor_id else (),
            content_post_id=content_post_id,
        )
        return _json({
            "ok": True,
            "placements": placements,
            "surface": surface,
            "visible_percent_threshold": config.VISIBLE_PERCENT_THRESHOLD,
            "visible_dwell_ms": config.VISIBLE_DWELL_MS,
            "cadence": config.cadence(surface),
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
            cur, user["user_id"], "marketplace", conn=conn,
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


# --- composer ---------------------------------------------------------------
#: How many of the creator's own listings the picker is offered. Not a cap on
#: anything the server enforces — `tagging.MAX_TAGGED_PER_CONTENT` is that — only
#: a bound on one response. A seller with 900 listings gets the 60 most recently
#: touched, which is the set a composer is plausibly reaching for.
TAGGABLE_PAGE_MAX = 60


def _taggable_rows(cur, seller_user_id: int, limit: int):
    """The creator's own listings, eligible or not, most recently touched first.

    Deliberately **not** filtered by ``eligibility.candidate_sql()``, which is the
    one thing about this query worth arguing over. Filtering is the obvious
    implementation and it produces the failure this endpoint exists to prevent: a
    creator whose listing has no cover image would simply not see it in the picker,
    would conclude the product was gone or the feature was broken, and would have
    no way to learn that adding a photo is the entire fix. The judgement is
    reported per row instead — see :func:`commerce_discovery_taggable_products`.

    Projection and joins are ``eligibility``'s, unmodified, because
    :func:`eligibility.gate` returns "unknown, therefore pass" for several
    unprojected columns. A hand-trimmed SELECT here would report a listing as
    servable that the serve path then drops, which is worse than no answer.
    """
    from services.commerce_discovery import eligibility

    cur.execute(
        f"SELECT {eligibility.candidate_projection()} "
        "FROM marketplace_listings l "
        "LEFT JOIN users u ON u.user_id=l.seller_user_id "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id "
        "WHERE COALESCE(l.seller_user_id,0)=? "
        # `l.id DESC` is the tiebreak, for `pool._page`'s reason: `updated_at`
        # alone is not a total order on this table and a bulk edit gives a whole
        # page the same timestamp.
        "ORDER BY l.updated_at DESC, l.id DESC "
        "LIMIT ?",
        (int(seller_user_id), int(limit)),
    )
    rows = cur.fetchall() or []
    return [dict(row) for row in rows]


@discovery_blueprint.route(f"{API_PREFIX}/taggable-products", methods=["GET"])
@auth_required
def commerce_discovery_taggable_products():
    """The creator's own listings, each with whether tagging it will do anything.

    The composer's question, and it is two questions that this repository would
    otherwise collapse into one:

    **May I tag this?** Ownership, and on this endpoint the answer is always yes —
    every row is selected by ``seller_user_id = me``, which is exactly
    ``tagging.attach``'s check. There is no ``can_attach`` field because a field
    that is unconditionally ``true`` teaches a client to stop reading it.

    **Will tagging it show anything?** A different question with a different
    answer, asked by ``eligibility.gate`` at *serve* time, on every serve, against
    the live listing. ``tagging.attach`` does not ask it and should not: whether
    this person may point at this product is a permission, and whether the product
    is fit to push at a stranger is a moderation state that changes after the tag
    is written. So the tag succeeds, and the product may still never appear.

    That gap is silent everywhere else. ``pulse_attach_products_to_content`` logs a
    refusal at ``info`` and drops it; an *accepted* tag on an ineligible listing
    logs nothing at all, because nothing refused it. The creator gets a post that
    published cleanly and a product that is never shown, with no symptom and no
    message. This endpoint is the only place that difference is expressible, which
    is why ``blocked_reason`` carries ``eligibility``'s own code rather than a
    boolean: "add a cover photo" and "a moderator flagged this" are the same
    boolean and different instructions.

    ``max_per_content`` is served rather than mirrored. A client constant would be
    a second copy of ``tagging.MAX_TAGGED_PER_CONTENT`` that goes stale in the
    permissive direction — the picker would let a creator select six, and the sixth
    would be refused after the post was already published.

    Failure posture departs from the serve endpoints on purpose. Serve answers
    ``200 {"placements": []}`` for everything because a broken recommendation must
    not break the feed, and an empty carousel is a truthful rendering of "nothing
    to show". Here an empty list is a *claim about the creator's own store* — "you
    have no products" — and a database error must not be allowed to make it. So
    this one returns 500 with a code, and the client shows a retry rather than an
    empty state. (Memory of this codebase: error and empty must never co-render.)
    """
    user, err = _require_user()
    if err:
        return err

    try:
        limit = max(1, min(TAGGABLE_PAGE_MAX, int(request.args.get("limit") or TAGGABLE_PAGE_MAX)))
    except (TypeError, ValueError):
        limit = TAGGABLE_PAGE_MAX

    def handler(cur, conn):
        bot = _bot()
        from services.commerce_discovery import eligibility, tagging

        products = []
        for row in _taggable_rows(cur, user["user_id"], limit):
            blocked = eligibility.gate(row, bot.parse_price_label_to_cents)
            try:
                # `buyer_safe` even here, where the reader *is* the seller. The
                # seller's own dashboard is entitled to more than this, but it is
                # not this endpoint's job to be a second seller dashboard, and
                # `seller_risk_score` is an internal assessment that has no
                # audience — see `engine.PIPELINE_ONLY_FIELDS`.
                card = dict(bot.pulse_marketplace_listing_payload(engine.buyer_safe(row)) or {})
            except Exception:
                LOGGER.warning(
                    "COMMERCE_DISCOVERY_TAGGABLE_SERIALIZE_FAILED listing=%s",
                    row.get("id"), exc_info=True,
                )
                # Skipped, not emitted with an empty card. A row the serializer
                # cannot render is one the picker cannot draw either, and a
                # selectable blank is worse than an absence.
                continue
            products.append({
                "listing_id": int(row.get("id") or 0),
                "product": card,
                "serves": not blocked,
                "blocked_reason": blocked,
            })

        return _json({
            "ok": True,
            "products": products,
            "max_per_content": tagging.MAX_TAGGED_PER_CONTENT,
            "request_limit": bot.PULSE_PRODUCT_TAG_REQUEST_LIMIT,
        })

    try:
        return _with_db(handler)
    except Exception:
        LOGGER.exception("COMMERCE_DISCOVERY_TAGGABLE_FAILED user_id=%s", user.get("user_id"))
        return _error(
            "We could not load your products. Please try again.",
            500, code="TAGGABLE_PRODUCTS_UNAVAILABLE",
        )


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
        return _json(runner(cur, conn, user, payload, placement_id, token))

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
    def runner(cur, conn, user, payload, placement_id, token):
        result = events.record_impression(
            cur, placement_id, token, conn=conn,
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
    def runner(cur, conn, user, payload, placement_id, token):
        # `value_minor` and `currency` are deliberately not read from the payload.
        # A client cannot be allowed to state what an event was worth; the recorder
        # prices it from the buyer's paid order or from the listing itself. A body
        # that still sends them is ignored rather than rejected — the event is real
        # and worth keeping, and an app build already in the store must not lose
        # its funnel events to a field it has no way to stop sending.
        result = events.record_engagement(
            cur, placement_id, token,
            str(payload.get("action") or ""),
            conn=conn,
            buyer_user_id=user["user_id"],
            order_ref=str(payload.get("order_ref") or ""),
            claimed_quantity=payload.get("quantity"),
            parse_price=_bot().parse_price_label_to_cents,
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
    def runner(cur, conn, user, payload, placement_id, token):
        action = str(payload.get("action") or "")
        meta = dict(_request_meta())
        # The category travels with the feedback so "see fewer like this" has
        # something to soften. Bounded here rather than trusted downstream.
        meta["category"] = str(payload.get("category") or "")[:80]
        result = events.record_feedback(cur, placement_id, token, action, conn=conn, request_meta=meta)
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
