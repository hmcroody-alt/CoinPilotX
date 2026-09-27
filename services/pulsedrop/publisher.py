"""Turning a decision into a post — exactly once, or not at all.

## The claim comes before the post, and that ordering is the whole design

``pulse_posts`` has no idempotency key. ``create_post`` is a bare INSERT: call it
twice with the same arguments and PulseSoc has two identical posts and no way to
tell which one was the mistake. So the uniqueness has to live somewhere else,
before the irreversible step, and ``pulsedrop_publications.idempotency_key`` is
that somewhere. The order is:

    1. INSERT the claim. UNIQUE decides the winner; a loser stops here.
    2. Re-read the listing and re-run eligibility *inside* this transaction.
    3. Create the post.
    4. Write the post id back onto the claim.

A crash between 1 and 3 leaves a claim with a null ``post_id`` and state
``claimed``. That is a leak of one row, and it is the correct thing to leak: the
alternative ordering — post first, record second — leaks a *post*, which is
visible to members and which nothing can later identify as PulseDrop's.

## Why the key is bucketed by UTC date

The key must collide for a retry and must not collide for a legitimate later
publication of the same product. It is not trying to re-express the cooldowns —
:mod:`services.pulsedrop.diversity` owns those, and a key that restated them
would be a second copy of the same policy, drifting. It is the backstop for the
two things cooldowns cannot see: a lease that expired mid-tick while the tick was
still running, and a retry after a partial failure. Both of those resolve within
minutes, so a bucket finer than a day protects nothing the lease did not already,
and a bucket coarser than a day would start silently vetoing real decisions.

## Why the product's images are referenced and not copied

The feed renders ``pulse_posts.media_ids_json``, which points at
``chat_media_uploads``. The product's photographs are already in object storage,
already served by the CDN, already moderated as marketplace media. Re-uploading
them so PulseDrop can have "its own" copy would double the bytes, double the
moderation surface and let a seller replacing a photo leave PulseDrop showing the
old one forever. So :func:`_reference_media` writes a *row* — not a file —
pointing at the URL that already exists, keyed by listing and source media id so
a republication reuses it. One row, no new bytes, one moderation decision.

## Moderation is not bypassed, and could not be

``create_post`` runs ``pulse_moderation_engine.moderate_text`` on every post it
makes, including this one. PulseDrop gets no exemption and is not offered one:
being a system account is an argument for *more* scrutiny, since its posts carry
a verified badge. A blocked result is honoured by soft-deleting the post it
created — ``create_post`` inserts before it judges — so a refusal leaves nothing
behind.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import NamedTuple

from services.pulsedrop import (
    account, audio, config, diversity, editorial, eligibility, schema,
)

log = logging.getLogger(__name__)

#: Publication states. ``claimed`` is in-flight; the other three are terminal.
CLAIMED = "claimed"
PUBLISHED = "published"
ABANDONED = "abandoned"
FAILED = "failed"

#: Context tag on the media rows PulseDrop mints, so they are identifiable as
#: references rather than uploads — and so a future audit of storage can tell
#: that these rows own no bytes.
MEDIA_CONTEXT = "pulsedrop_product"

#: Signals carry up to this many product photographs. Four is the most a feed
#: card can show without becoming a gallery the member has to work through.
SIGNAL_MEDIA_MAX = 4


class Result(NamedTuple):
    ok: bool
    #: Short code. ``published`` on success; otherwise what stopped it.
    reason: str
    publication_id: int = 0
    post_id: int = 0
    reel_id: int = 0


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.utcnow()).isoformat(timespec="seconds")


def idempotency_key(surface: str, listing_id: int, now: datetime | None = None) -> str:
    """The value whose UNIQUE constraint decides whether this publish happens."""
    day = (now or datetime.utcnow()).strftime("%Y-%m-%d")
    return f"{surface}:{int(listing_id or 0)}:{day}"


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


def claim(ranked_item, surface: str, *, now: datetime | None = None) -> int:
    """Reserve the right to publish this product to this surface today.

    Returns the publication row id, or ``0`` when someone else holds the claim.
    Losing is not an error and is not logged as one: it is the mechanism working.

    A key already spent today — published, abandoned or failed — is also a loss.
    That is deliberate for the failure case: a product whose publication failed
    at 09:00 must not be retried every tick until midnight, because whatever
    failed is likely to fail again and the retry storm would be invisible. The
    next day's bucket lets it through.

    Runs on its own connection and commits, for the same reason the lease does —
    a claim inside an uncommitted transaction is not a claim, and two instances
    would both believe they had won until the first one committed.
    """
    schema.ensure_schema()
    from services import db as db_service
    from services.pulsedrop import lease

    listing = ranked_item.listing
    listing_id = int(listing.get("id") or 0)
    if not listing_id:
        return 0
    stamp = _now_text(now)
    key = idempotency_key(surface, listing_id, now)
    # Per *attempt*, not per process. If this were just the process id then a
    # tick that crashed after creating the post but before settling the claim
    # would, on re-entry, read back its own owner on a still-'claimed' row and
    # win a second time — publishing the product twice from one instance, which
    # is the exact outcome the key exists to prevent. A fresh token per call
    # makes "claimed_by is mine" mean "this call inserted this row" and nothing
    # else. The instance id stays as the prefix so ops can still see who holds it.
    mine = f"{lease.owner_id()}:{uuid.uuid4().hex[:8]}"

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pulsedrop_publications
                (idempotency_key, claimed_by, surface, listing_id, seller_user_id, category,
                 editorial_label, rank_score, ranker_version, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (
                key,
                mine,
                surface,
                listing_id,
                int(listing.get("seller_user_id") or 0),
                str(listing.get("category") or "").strip().lower(),
                ranked_item.label.key,
                float(ranked_item.score.total),
                _ranker_version(),
                CLAIMED,
                stamp,
                stamp,
            ),
        )
        conn.commit()
        # Read back rather than trust rowcount: a suppressed insert reports
        # differently on the two drivers, and the question being asked is not
        # "did a row appear" but "is the row that is there mine". A row in
        # 'claimed' state belonging to another owner is a publication in flight
        # on another instance, and must read as a loss — which is precisely the
        # case a rowcount check would get wrong.
        cur.execute(
            "SELECT id, state, claimed_by, post_id FROM pulsedrop_publications WHERE idempotency_key=? LIMIT 1",
            (key,),
        )
        row = dict(cur.fetchone() or {})
        if not row:
            return 0
        if str(row.get("claimed_by") or "") != mine:
            return 0
        if str(row.get("state") or "") != CLAIMED or row.get("post_id"):
            return 0
        return int(row.get("id") or 0)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("pulsedrop_claim_failed surface=%s listing_id=%s", surface, listing_id, exc_info=True)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ranker_version() -> str:
    from services.pulsedrop import ranking

    return ranking.RANKER_VERSION


def _settle(publication_id: int, state: str, *, reason: str = "", post_id: int = 0,
            reel_id: int = 0, render_id: int = 0, now: datetime | None = None) -> None:
    """Move a claim to a terminal state. Never raises.

    ``published_at`` is written only for :data:`PUBLISHED`, because
    :mod:`services.pulsedrop.diversity` reads that column as "when members saw
    this" and a timestamp on an abandoned row would impose cooldowns for a post
    that does not exist.
    """
    from services import db as db_service

    stamp = _now_text(now)
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pulsedrop_publications
            SET state=?, failure_reason=?, post_id=?, reel_id=?, render_id=?,
                published_at=?, updated_at=?
            WHERE id=?
            """,
            (
                state,
                str(reason or "")[:200],
                int(post_id) or None,
                int(reel_id or 0),
                int(render_id or 0),
                stamp if state == PUBLISHED else None,
                stamp,
                int(publication_id),
            ),
        )
        conn.commit()
    except Exception:
        log.warning("pulsedrop_settle_failed publication_id=%s state=%s", publication_id, state, exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


def _reference_media(cur, listing: dict, *, uploader_user_id: int, limit: int,
                     now: datetime | None = None) -> list[int]:
    """``chat_media_uploads`` rows pointing at the product's existing files.

    Idempotent by ``(context_type, context_id)``, where the context id is the
    listing and the URL's position. A republication of the same product finds
    its rows and reuses them; a seller who reordered their photographs gets new
    rows, which is correct, because position is what the post recorded.

    ``moderation_status='approved'`` is not a bypass: the source rows were
    filtered by :func:`eligibility.media_for_listings`, which drops anything
    marketplace moderation rejected. Copying a URL that has already been cleared
    and re-marking it ``pending_review`` would leave PulseDrop's own posts
    invisible behind a queue nobody is working.
    """
    urls = eligibility.image_urls(listing)[: max(0, int(limit))]
    if not urls:
        return []
    listing_id = int(listing.get("id") or 0)
    stamp = _now_text(now)
    media_ids: list[int] = []
    for position, url in enumerate(urls):
        context_id = f"{listing_id}:{position}"
        cur.execute(
            """
            SELECT id FROM chat_media_uploads
            WHERE context_type=? AND context_id=? AND COALESCE(media_url,'')=?
            LIMIT 1
            """,
            (MEDIA_CONTEXT, context_id, url),
        )
        existing = dict(cur.fetchone() or {})
        if existing.get("id"):
            media_ids.append(int(existing["id"]))
            continue
        cur.execute(
            """
            INSERT INTO chat_media_uploads
                (uploader_user_id, context_type, context_id, original_filename, stored_filename,
                 media_url, thumbnail_url, cdn_url, public_url, media_type, mime_type,
                 moderation_status, processing_status, verification_status, is_available,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 'image', 'image/jpeg',
                    'approved', 'ready', 'verified', 1, ?, ?)
            """,
            (
                int(uploader_user_id),
                MEDIA_CONTEXT,
                context_id,
                f"product-{listing_id}-{position}",
                url,
                url,
                url,
                url,
                stamp,
                stamp,
            ),
        )
        cur.execute(
            "SELECT id FROM chat_media_uploads WHERE context_type=? AND context_id=? ORDER BY id DESC LIMIT 1",
            (MEDIA_CONTEXT, context_id),
        )
        created = dict(cur.fetchone() or {})
        if created.get("id"):
            media_ids.append(int(created["id"]))
    return media_ids


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def publish_signal(ranked_item, *, now: datetime | None = None) -> Result:
    """Publish one product as a shoppable Signal.

    The listing is re-read and re-judged between the claim and the post, because
    ranking happened at the top of the tick and a seller can delete, unpublish,
    sell out or be suspended in the seconds since. Publishing a product that
    became unavailable while the curator was thinking is the failure members
    notice first.
    """
    listing_id = int(ranked_item.listing.get("id") or 0)
    publication_id = claim(ranked_item, diversity.SIGNAL, now=now)
    if not publication_id:
        return Result(False, "duplicate_claim")

    user_id = account.account_user_id()
    if not user_id:
        _settle(publication_id, FAILED, reason="account_missing", now=now)
        return Result(False, "account_missing", publication_id)

    fresh, media_ids, blocker = _revalidate_and_stage(
        listing_id, user_id, SIGNAL_MEDIA_MAX, now=now
    )
    if blocker:
        _settle(publication_id, ABANDONED, reason=blocker, now=now)
        return Result(False, blocker, publication_id)

    label = editorial.classify(fresh, now)
    result = _create_post(
        user_id,
        body=editorial.caption(fresh, label),
        post_type="image" if media_ids else "text",
        title=editorial.clean_title(fresh),
        tags=editorial.hashtags(fresh),
        visibility="public",
        media_ids=media_ids,
    )
    if not result.get("ok"):
        reason = _post_failure_reason(result)
        _soft_delete_post(result.get("post_id"))
        _settle(publication_id, FAILED, reason=reason, now=now)
        return Result(False, reason, publication_id)

    post_id = int(result.get("post_id") or 0)
    _settle(publication_id, PUBLISHED, post_id=post_id, now=now)
    log.info(
        "pulsedrop_published surface=signal listing_id=%s post_id=%s label=%s",
        listing_id, post_id, label.key,
    )
    return Result(True, "published", publication_id, post_id)


def publish_reel(ranked_item, render: dict, *, feed_visible: bool = True,
                 now: datetime | None = None) -> Result:
    """Publish one rendered Reel as a shoppable commerce Reel.

    ``render`` is a ``pulsedrop_renders`` row that is already ``ready`` — this
    function never triggers an encode. Rendering is minutes of CPU and runs as a
    retryable job outside the curator's lease; a publisher that blocked on it
    would hold the lease past its expiry and hand a second instance the same tick.

    ``feed_visible`` is False when a Signal for the same product went out in the
    same tick. The Reel is then ``reel_only``: it lives on the Reels surface and
    on PulseDrop's profile, but the main feed shows the product once. Making one
    product occupy two consecutive feed slots is the behaviour the distribution
    engine exists to prevent, and it would be undone here.
    """
    listing_id = int(ranked_item.listing.get("id") or 0)
    video_url = str((render or {}).get("video_url") or "").strip()
    if not video_url:
        return Result(False, "render_not_ready")

    publication_id = claim(ranked_item, diversity.REEL, now=now)
    if not publication_id:
        return Result(False, "duplicate_claim")

    user_id = account.account_user_id()
    if not user_id:
        _settle(publication_id, FAILED, reason="account_missing", now=now)
        return Result(False, "account_missing", publication_id)

    fresh, _media_ids, blocker = _revalidate_and_stage(listing_id, user_id, 0, now=now)
    if blocker:
        _settle(publication_id, ABANDONED, reason=blocker, now=now)
        return Result(False, blocker, publication_id)

    label = editorial.classify(fresh, now)
    caption = editorial.caption(fresh, label)
    result = _create_post(
        user_id,
        body=caption,
        post_type="video",
        title=editorial.clean_title(fresh),
        tags=editorial.hashtags(fresh),
        visibility="public" if feed_visible else "reel_only",
        media_ids=[],
    )
    if not result.get("ok"):
        reason = _post_failure_reason(result)
        _soft_delete_post(result.get("post_id"))
        _settle(publication_id, FAILED, reason=reason, now=now)
        return Result(False, reason, publication_id)

    post_id = int(result.get("post_id") or 0)
    reel_id = _attach_reel(post_id, user_id, fresh, caption, render, now=now)
    if not reel_id:
        # The post exists but is not a Reel, which is a post with no video. Undo
        # it rather than leave a silent blank in the feed.
        _soft_delete_post(post_id)
        _settle(publication_id, FAILED, reason="reel_attach_failed", now=now)
        return Result(False, "reel_attach_failed", publication_id, post_id)

    # Music last, and unguarded by its own failure. The Reel is finished at this
    # point — rendered, posted, claimed — and a bed is a garnish on it. See
    # ``audio``: nothing is encoded into the file, so this writes three rows and
    # the clients overlay the track at playback, which also means a silent Reel
    # can be given music later without re-rendering anything.
    bed = audio.attach(reel_id, listing_id, now=now)

    _settle(
        publication_id,
        PUBLISHED,
        post_id=post_id,
        reel_id=reel_id,
        render_id=int((render or {}).get("id") or 0),
        now=now,
    )
    log.info(
        "pulsedrop_published surface=reel listing_id=%s post_id=%s reel_id=%s label=%s bed=%s",
        listing_id, post_id, reel_id, label.key, bed.get("track_id") or "none",
    )
    return Result(True, "published", publication_id, post_id, reel_id)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _revalidate_and_stage(listing_id: int, user_id: int, media_limit: int,
                          *, now: datetime | None = None) -> tuple[dict, list[int], str]:
    """Re-judge the listing and mint its media rows in one transaction.

    One transaction because the media rows must not survive a revalidation that
    fails: a product that went out of stock between ranking and publication would
    otherwise leave PulseDrop-owned media rows for a post that never happened,
    and the next attempt would find and reuse rows built from stale URLs.
    """
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        fresh, blocker = eligibility.revalidate(
            cur, listing_id, system_user_ids=(user_id,), now=now
        )
        if blocker:
            conn.rollback()
            return {}, [], blocker
        media_ids = _reference_media(
            cur, fresh, uploader_user_id=user_id, limit=media_limit, now=now
        )
        if media_limit and not media_ids:
            # image_urls() was non-empty at ranking time or the listing would not
            # have been a candidate, so an empty result here means the rows could
            # not be written. Publishing a media post with no media is worse than
            # not publishing.
            conn.rollback()
            return {}, [], eligibility.NO_IMAGE
        conn.commit()
        return fresh, media_ids, ""
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        log.warning("pulsedrop_revalidate_failed listing_id=%s", listing_id, exc_info=True)
        return {}, [], "revalidation_error"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _create_post(user_id: int, **kwargs) -> dict:
    """``pulse_feed_engine.create_post``, with its exceptions turned into a result.

    Wrapped because create_post raises on a database failure and returns a dict
    on a policy refusal, and the caller has to settle its claim either way.
    """
    try:
        from services import pulse_feed_engine

        return pulse_feed_engine.create_post(int(user_id), **kwargs) or {}
    except Exception:
        log.exception("pulsedrop_create_post_failed user_id=%s", user_id)
        return {"ok": False, "status": "error", "message": "create_post raised"}


def _post_failure_reason(result: dict) -> str:
    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        return "moderation_blocked"
    if status:
        return f"post_{status}"[:60]
    return "post_rejected"


def _soft_delete_post(post_id) -> None:
    """Retract a post PulseDrop created and then decided against.

    ``create_post`` inserts before it moderates, so a blocked result still leaves
    a row. ``deleted_at`` is what every reader in this codebase checks, so this
    is a retraction and not a hard delete: the row remains for the audit of why
    PulseDrop tried.
    """
    try:
        identifier = int(post_id or 0)
    except (TypeError, ValueError):
        return
    if not identifier:
        return
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE pulse_posts SET deleted_at=?, updated_at=? WHERE id=? AND deleted_at IS NULL",
            (_now_text(), _now_text(), identifier),
        )
        conn.commit()
    except Exception:
        log.warning("pulsedrop_soft_delete_failed post_id=%s", identifier, exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _attach_reel(post_id: int, user_id: int, listing: dict, caption: str,
                 render: dict, *, now: datetime | None = None) -> int:
    """Write the ``pulse_reels`` row for a post that already exists.

    ``ON CONFLICT(post_id)`` mirrors every other writer of this table, so a retry
    updates rather than duplicating. The category is the product's own, lowered
    to the same convention the rest of PulseDrop uses, because the Reels surface
    groups by it.
    """
    from services import db as db_service

    stamp = _now_text(now)
    render = dict(render or {})
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pulse_reels
                (post_id, user_id, category, caption, video_url, poster_url, ai_tags_json,
                 safety_score, educational_value, reel_score, processing_status,
                 transcoding_status, status, duration_seconds, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 95, 50, 60, 'ready', 'ready', 'active', ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                category=excluded.category, caption=excluded.caption,
                video_url=excluded.video_url, poster_url=excluded.poster_url,
                ai_tags_json=excluded.ai_tags_json, duration_seconds=excluded.duration_seconds,
                processing_status=excluded.processing_status,
                transcoding_status=excluded.transcoding_status, updated_at=excluded.updated_at
            """,
            (
                int(post_id),
                int(user_id),
                str(listing.get("category") or "Marketplace").strip() or "Marketplace",
                caption,
                str(render.get("video_url") or ""),
                str(render.get("poster_url") or ""),
                _tags_json(listing),
                float(render.get("duration_seconds") or 0),
                stamp,
                stamp,
            ),
        )
        cur.execute("SELECT id FROM pulse_reels WHERE post_id=? LIMIT 1", (int(post_id),))
        reel_id = int(dict(cur.fetchone() or {}).get("id") or 0)
        conn.commit()
        return reel_id
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        log.exception("pulsedrop_reel_attach_failed post_id=%s", post_id)
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _tags_json(listing: dict) -> str:
    import json

    return json.dumps(editorial.hashtags(listing))


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def reap_stale_claims(*, now: datetime | None = None) -> int:
    """Close out claims whose tick died between the claim and the post.

    A claim with a null ``post_id`` older than one lease is not in flight — the
    instance that made it either finished or is gone, and either way its lease
    has expired. Left alone these rows are harmless but they make
    ``pulsedrop_publications`` unreadable as an answer to "what did PulseDrop
    do", which is the table's entire purpose. Returns how many were closed.
    """
    from datetime import timedelta

    from services import db as db_service

    moment = now or datetime.utcnow()
    cutoff = (moment - timedelta(seconds=config.lease_seconds() * 2)).isoformat(timespec="seconds")
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pulsedrop_publications
            SET state=?, failure_reason='abandoned_incomplete', updated_at=?
            WHERE state=? AND post_id IS NULL AND COALESCE(created_at,'') < ?
            """,
            (FAILED, moment.isoformat(timespec="seconds"), CLAIMED, cutoff),
        )
        closed = int(cur.rowcount or 0)
        conn.commit()
        if closed:
            log.info("pulsedrop_reaped_stale_claims count=%s", closed)
        return closed
    except Exception:
        log.warning("pulsedrop_reap_failed", exc_info=True)
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
