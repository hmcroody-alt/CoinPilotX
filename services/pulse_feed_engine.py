"""Global PulseSoc Feed data and ranking helpers."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import db, embed_service, media_service, premium_identity_engine, pulse_feed_ranking_engine, pulse_id_service, pulse_moderation_engine, pulsesoc_notification_system, user_context
from .discovery_visibility import REQUIRED_USER_COLUMNS, discovery_visible_sql
from .pulse_ai.content_policy import AUTOMATED_ACCOUNT_TYPE, sanitize_automated_text
from .schema_guard import run_once_per_process


REACTIONS = {
    "like",
    "love",
    "fire",
    "funny",
    "wow",
    "rocket",
    "clap",
    "hundred",
    "target",
    "smart",
    "fast_signal",
    "shield",
    "scam_alert",
    "whale",
    "bullish",
    "bearish",
    "elite",
    "brutal",
}
FEEDS = {
    "for_you",
    "following",
    "trending",
    "scam_alerts",
    "arena_highlights",
    "roast_clips",
    "questions",
    "crypto",
    "my_posts",
    "reels",
}
FEED_ALIASES = {
    "home": "for_you",
    "for-you": "for_you",
    "scam-alerts": "scam_alerts",
    "scam": "scam_alerts",
    "arena": "arena_highlights",
    "arena-highlights": "arena_highlights",
    "roast": "roast_clips",
    "roast-clips": "roast_clips",
    "clips": "roast_clips",
    "crypto-feed": "crypto",
    "market": "crypto",
    "markets": "crypto",
    "my-posts": "my_posts",
}
POST_TYPE_ALIASES = {"scam_warning": "scam_report", "question": "poll", "roast": "roast_clip", "roast_battle": "roast_clip"}
POST_TYPES = {"text", "image", "video", "gif", "poll", "replay", "scam_report", "arena_result", "roast_clip", "live"}
MEMBER_000_PUBLIC_PLAYER_ID = "pulsesoc_insight"
MEMBER_000_LEGACY_PUBLIC_PLAYER_ID = "pulsesoc-member-000"
MEMBER_000_DISPLAY_NAME = "PulseSoc Insight"
MEMBER_000_SYSTEM_LABEL = "Official PulseSoc System Account"

#: Repo-relative location of the account's brand media. The dated filename *is*
#: the cache-busting mechanism used by every other asset in ``static/brand`` --
#: a new version ships under a new name, so no browser, CDN edge or on-device
#: image cache can serve the previous artwork for a URL that no longer exists.
#: Superseded files are left in place on purpose: a client still holding a
#: cached payload that names the old asset keeps rendering *something* until it
#: refreshes, rather than falling to the empty circle.
MEMBER_000_AVATAR_PATH = "/static/brand/pulsesoc-insight-avatar-20260825.png"
MEMBER_000_COVER_PATH = "/static/brand/pulsesoc-insight-cover-20260825.png"
#: Every avatar path this module has ever minted, newest-superseded first.
#:
#: Retiring a version means *adding* its path here, never replacing the entry.
#: ``is_member_000_brand_avatar`` consults this list to decide whether a stored
#: ``arena_profiles.avatar_url`` is ours and may be upgraded; a path that falls
#: off the list stops being recognised as ours, and the row holding it is then
#: treated as an operator's deliberate override and kept forever. The account
#: would keep serving retired artwork with nothing in the logs to say why.
MEMBER_000_LEGACY_AVATAR_PATHS = (
    "/static/brand/pulsesoc-insight-avatar-20260823.png",
    "/static/brand/pulsesoc-member-000-avatar.png",
)


def _brand_media_url(path: str) -> str:
    """Absolute URL for a first-party brand asset.

    Every *uploaded* avatar reaches a client as an absolute CDN URL, because it
    is minted from ``R2_PUBLIC_BASE_URL``. This account's avatar was the lone
    exception: it shipped as the site-relative ``/static/brand/...``. A browser
    resolves that against the current origin and looks fine, which is why the
    defect stayed invisible on web -- but React Native's ``Image`` has no origin
    to resolve against, so ``{ uri: "/static/..." }`` simply fails to load and
    the native app drew an empty circle where PulseSoc Insight should be.

    Absolutising here fixes every native surface at once, and does it by making
    the system account's media reference look exactly like every other avatar in
    the product instead of adding a special case to each client normalizer.
    """
    base = (
        os.getenv("PULSE_APP_URL")
        or os.getenv("APP_BASE_URL")
        or os.getenv("BASE_URL")
        or os.getenv("DOMAIN")
        or "https://pulsesoc.com"
    ).strip().rstrip("/")
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return f"{base}/{path.lstrip('/')}"


MEMBER_000_AVATAR_URL = _brand_media_url(MEMBER_000_AVATAR_PATH)
MEMBER_000_COVER_URL = _brand_media_url(MEMBER_000_COVER_PATH)
_MEMBER_000_PROFILE_READY = False


def is_member_000_brand_avatar(url: str) -> bool:
    """True when ``url`` is a PulseSoc-owned avatar for the system account.

    Used to decide whether a stored value may be replaced. The account is
    automated and has no human to upload a picture, but the check stays narrow
    anyway: only paths this module has ever minted are treated as ours, so an
    operator who deliberately points the profile somewhere else keeps it.
    """
    value = str(url or "").strip()
    if not value:
        return True
    known = (MEMBER_000_AVATAR_PATH, *MEMBER_000_LEGACY_AVATAR_PATHS)
    return any(value == path or value.endswith(path) for path in known)


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _row(row):
    return dict(row) if row else None


def _json(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _clean_text(value, limit=4000):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _public_media_url(url):
    return media_service.normalize_url(url)


def _canonical_media_payload(item, resolved, *, index=0, embed=None):
    """Return the one media schema used by all PulseSoc feed renderers."""
    payload = dict(embed or {})
    media_type = (resolved.get("media_type") or item.get("media_type") or payload.get("media_type") or payload.get("type") or "image")
    media_url = resolved.get("media_url") or payload.get("media_url") or ""
    valid_url = resolved.get("valid_url") or payload.get("valid_url") or media_url
    thumb = resolved.get("thumbnail_url") or payload.get("thumbnail_url") or valid_url
    poster = resolved.get("poster_url") or payload.get("poster_url") or thumb
    width = int(float(resolved.get("width") or payload.get("width") or 0) or 0)
    height = int(float(resolved.get("height") or payload.get("height") or 0) or 0)
    ratio = resolved.get("aspect_ratio") or payload.get("aspect_ratio") or 0
    try:
        ratio = round(float(ratio or 0), 4)
    except Exception:
        ratio = 0
    if not ratio and width and height:
        ratio = round(width / height, 4)
    return {
        "id": item.get("id") or payload.get("id"),
        "type": media_type,
        "media_type": media_type,
        "media_url": media_url,
        "valid_url": valid_url,
        "cdn_url": resolved.get("cdn_url") or payload.get("cdn_url") or "",
        "playback_url": resolved.get("playback_url") or payload.get("playback_url") or valid_url,
        "mux_playback_id": resolved.get("mux_playback_id") or payload.get("mux_playback_id") or "",
        "mux_asset_id": resolved.get("mux_asset_id") or payload.get("mux_asset_id") or "",
        "mux_status": resolved.get("mux_status") or payload.get("mux_status") or "",
        "mux_processing": bool(resolved.get("mux_processing") or payload.get("mux_processing")),
        "processing_status": resolved.get("processing_status") or payload.get("processing_status") or "",
        "mux_hls_url": resolved.get("mux_hls_url") or payload.get("mux_hls_url") or "",
        "mux_thumbnail_url": resolved.get("mux_thumbnail_url") or payload.get("mux_thumbnail_url") or "",
        "thumbnail_url": thumb,
        "poster_url": poster,
        "fallback_url": resolved.get("fallback_url") or payload.get("fallback_url") or media_service.FALLBACK_URL,
        "width": width,
        "height": height,
        "aspect_ratio": ratio,
        "mime_type": resolved.get("mime_type") or payload.get("mime_type") or "",
        "playback_mime_type": resolved.get("playback_mime_type") or payload.get("playback_mime_type") or "",
        "embed_type": item.get("embed_type") or payload.get("embed_type") or "upload",
        "source_platform": item.get("source_platform") or payload.get("source_platform") or "coinpilotx",
        "preload_priority": "high" if index == 0 else "lazy",
        "orientation": resolved.get("orientation") or payload.get("orientation") or "unknown",
        "is_available": bool(resolved.get("is_available") if "is_available" in resolved else payload.get("is_available")),
        "storage_provider": resolved.get("storage_provider") or payload.get("storage_provider") or "",
        "storage_key": resolved.get("storage_key") or payload.get("storage_key") or "",
        "fit_mode": "smart",
        "srcset": resolved.get("srcset") or payload.get("srcset") or "",
        "sizes": resolved.get("sizes") or payload.get("sizes") or "(max-width: 760px) 100vw, (max-width: 1400px) 760px, 900px",
        "hydration_state": resolved.get("hydration_state") or payload.get("hydration_state") or ("ready" if valid_url else "missing"),
        "source_url": payload.get("source_url") or "",
    }


def media_for_posts(post_ids):
    """Canonical media for many posts at once, keyed by post id.

    A public name for `_media_for_posts`. The Saved library needs exactly this —
    resolved playback for a batch of posts, in one round trip — and reaching into
    a private helper from `bot.py` would make a rename here a silent breakage
    there. Everything about the payload (Mux playback id, HLS URL, poster,
    storage key) is produced by `_canonical_media_payload`, so a saved item and a
    feed card describe the same media in the same shape.
    """
    return _media_for_posts(post_ids)


def pulse_visibility_decision(post, viewer_user_id=None, include_private=False):
    """Canonical public PulseSoc visibility rule used by feeds, audits, and refresh paths."""
    item = dict(post or {})
    viewer_id = int(viewer_user_id or 0)
    author_id = int(item.get("user_id") or 0)
    moderation_status = str(item.get("moderation_status") or "approved").lower()
    visibility = str(item.get("visibility") or "public").lower()
    status = str(item.get("status") or "published").lower()
    if item.get("deleted_at") or str(item.get("is_deleted") or "").lower() in {"1", "true", "yes"}:
        return False, "deleted"
    if status in {"deleted", "removed", "archived"}:
        return False, f"status:{status}"
    if moderation_status in {"blocked", "rejected", "deleted", "removed"}:
        return False, f"moderation:{moderation_status}"
    if moderation_status != "approved":
        if include_private and viewer_id and author_id == viewer_id:
            return True, "owner_private_review"
        return False, f"moderation:{moderation_status}"
    if visibility in {"public", "reel_only"}:
        return True, "public_approved"
    if include_private and viewer_id and author_id == viewer_id:
        return True, "owner_private"
    if visibility == "followers" and viewer_id:
        return False, "followers_not_expanded"
    return False, f"visibility:{visibility}"


def _public_feed_where(alias="p"):
    prefix = f"{alias}." if alias else ""
    return [
        f"{prefix}deleted_at IS NULL",
        f"COALESCE({prefix}visibility,'public')='public'",
        f"COALESCE({prefix}moderation_status,'approved')='approved'",
        f"COALESCE({prefix}status,'published') NOT IN ('deleted','removed','archived')",
    ]


def _resolve_profile_lookup_user_id(cur, lookup):
    """Resolve every profile key the public profile endpoint accepts.

    Native profile headers expose permanent Pulse IDs (`PLS-*`) while older feed
    profile filtering only understood arena public ids, usernames, or numeric
    user ids. That split let the profile header count posts for a user while the
    Posts tab queried `/api/pulse/feed?profile=PLS-...` and got an empty list.
    Keep the feed path on the same canonical identity contract.
    """
    value = str(lookup or "").strip().lstrip("@")[:160]
    if not value or any(ch.isspace() for ch in value):
        return 0
    try:
        resolved = pulse_id_service.resolve_user_id(cur, value)
        if resolved:
            return int(resolved)
    except Exception:
        pass
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        try:
            cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (int(value),))
            row = _row(cur.fetchone())
            if row:
                return int(row.get("user_id") or 0)
        except Exception:
            pass
    try:
        cur.execute("SELECT user_id FROM arena_profiles WHERE lower(public_player_id)=lower(?) LIMIT 1", (value,))
        row = _row(cur.fetchone())
        if row:
            return int(row.get("user_id") or 0)
    except Exception:
        pass
    try:
        cur.execute("SELECT user_id FROM users WHERE lower(username)=lower(?) LIMIT 1", (value,))
        row = _row(cur.fetchone())
        if row:
            return int(row.get("user_id") or 0)
    except Exception:
        pass
    return 0


def _ensure_member_000_profile():
    """Seed the official system profile used by legacy user_id=0 feed posts."""
    global _MEMBER_000_PROFILE_READY
    if _MEMBER_000_PROFILE_READY:
        return
    try:
        conn = user_context.connect()
        cur = conn.cursor()
        now = _now()
        cur.execute(
            """
            SELECT id, avatar_url
            FROM arena_profiles
            WHERE user_id=0 OR public_player_id IN (?, ?)
            ORDER BY CASE WHEN public_player_id=? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (MEMBER_000_PUBLIC_PLAYER_ID, MEMBER_000_LEGACY_PUBLIC_PLAYER_ID, MEMBER_000_PUBLIC_PLAYER_ID),
        )
        row = _row(cur.fetchone())
        if row:
            # Adopt the current brand avatar when the stored one is empty or is
            # a version this module minted earlier. Without this the profile row
            # written before the artwork changed would outrank the constant
            # forever, because ``_public_author`` reads the row.
            stored_avatar = row.get("avatar_url") or ""
            avatar_url = MEMBER_000_AVATAR_URL if is_member_000_brand_avatar(stored_avatar) else stored_avatar
            cur.execute(
                """
                UPDATE arena_profiles
                SET user_id=0, username=?, public_player_id=?, display_name=?, avatar_url=?, rank=?, updated_at=?
                WHERE id=?
                """,
                (
                    MEMBER_000_PUBLIC_PLAYER_ID,
                    MEMBER_000_PUBLIC_PLAYER_ID,
                    MEMBER_000_DISPLAY_NAME,
                    avatar_url,
                    MEMBER_000_SYSTEM_LABEL,
                    now,
                    row.get("id"),
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO arena_profiles
                    (user_id, username, public_player_id, display_name, avatar_url, rank, created_at, updated_at)
                VALUES
                    (0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    MEMBER_000_PUBLIC_PLAYER_ID,
                    MEMBER_000_PUBLIC_PLAYER_ID,
                    MEMBER_000_DISPLAY_NAME,
                    MEMBER_000_AVATAR_URL,
                    MEMBER_000_SYSTEM_LABEL,
                    now,
                    now,
                ),
            )
        conn.commit()
        conn.close()
        _MEMBER_000_PROFILE_READY = True
    except Exception:
        logging.getLogger(__name__).exception("member_000_profile_seed_failed")


def _page_author(page_id):
    """Attribution for a page-authored post (Page OS). Same shape as a user
    author so every existing consumer keeps working; the extra `page` object
    and `account_type: "PAGE"` are how clients know to render the page badge.
    Any failure falls back to user attribution — a broken page row must never
    hide a post."""
    try:
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, handle, avatar_url, page_type, verification_status "
            "FROM pulse_pages WHERE id=? LIMIT 1",
            (int(page_id),),
        )
        page = _row(cur.fetchone())
        conn.close()
    except Exception:
        return None
    if not page:
        return None
    handle = page.get("handle") or ""
    page_type = str(page.get("page_type") or "PAGE")
    verified = (page.get("verification_status") or "") == "verified"
    label = page_type.replace("_", " ").title()
    return {
        "id": None,
        "user_id": None,
        "public_player_id": handle or None,
        "username": handle or None,
        "handle": handle or None,
        "display_name": str(page.get("name") or handle or "Page")[:80],
        "avatar_url": page.get("avatar_url") or "",
        "profile_url": f"/pulse/pages/@{handle}" if handle else "",
        "rank": label,
        "primary_label": label,
        "badges": [label] + (["Verified"] if verified else []),
        "badge_keys": ["page"] + (["verified"] if verified else []),
        "premium_verified": verified,
        "premium_mark": verified,
        "account_type": "PAGE",
        "automated": False,
        "official_system_account": False,
        "page": {
            "id": int(page.get("id") or 0),
            "name": page.get("name"),
            "handle": handle,
            "page_type": page_type,
            "verified": verified,
        },
    }


def _public_author(row):
    item = dict(row or {})
    page_id = int(item.get("page_id") or 0)
    if page_id > 0:
        page_author = _page_author(page_id)
        if page_author:
            return page_author
    public_player_id = item.get("public_player_id") or item.get("author_public_player_id") or ""
    user_id = int(item.get("user_id") or 0)
    is_member_000 = public_player_id in {MEMBER_000_PUBLIC_PLAYER_ID, MEMBER_000_LEGACY_PUBLIC_PLAYER_ID} or (
        user_id <= 0
        and not (item.get("user_display_name") or item.get("display_name") or item.get("username") or public_player_id)
    )
    if is_member_000:
        _ensure_member_000_profile()
        public_player_id = MEMBER_000_PUBLIC_PLAYER_ID
        item["username"] = MEMBER_000_PUBLIC_PLAYER_ID
    display = (
        MEMBER_000_DISPLAY_NAME if is_member_000 else
        item.get("user_display_name")
        or item.get("display_name")
        or item.get("username")
        # The anonymous fallback slices the LAST 4 chars of the identifier for
        # the "#NNNN" suffix. When the identifier was the member-000 handle
        # itself ("pulsesoc-member-000"), the slice produced "-000" and every
        # feed card read "PulseSoc Member #-000". Member-000 rows now short-
        # circuit to the canonical display name before the slice.
        or f"PulseSoc Member #{str(public_player_id or item.get('user_id') or '000').lstrip('-')[-4:].lstrip('-')}"
    )
    avatar_url = item.get("user_avatar_url") or item.get("avatar_url") or item.get("arena_avatar_url") or ""
    if is_member_000 and is_member_000_brand_avatar(avatar_url):
        # Previously this only filled in a *blank* avatar, so a profile row
        # holding a superseded brand path kept winning and the account rendered
        # last season's artwork -- or, when the row held the site-relative path,
        # nothing at all on native. The constant is the canonical identity for
        # an account nobody can upload a picture for; anything else an operator
        # set deliberately still passes through untouched.
        avatar_url = MEMBER_000_AVATAR_URL
    badges = ["Member"]
    badge_keys = []
    try:
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.badge_key, b.label
            FROM pulse_user_badges ub
            JOIN pulse_badges b ON b.badge_key=ub.badge_key
            WHERE ub.user_id=? AND COALESCE(b.active,1)=1
            ORDER BY ub.id ASC
            LIMIT 6
            """,
            (user_id,),
        )
        loaded_rows = [dict(row) for row in cur.fetchall()]
        loaded = [str(row.get("label") or "") for row in loaded_rows if str(row.get("label") or "")]
        badge_keys = [str(row.get("badge_key") or "") for row in loaded_rows if str(row.get("badge_key") or "")]
        if loaded:
            badges = loaded
        conn.close()
    except Exception:
        pass
    premium_mark = premium_identity_engine.identity_mark(item, badge_keys)
    badge_key_set = {str(key) for key in badge_keys}
    label_set = {str(label).strip().lower() for label in badges}
    if is_member_000:
        premium_mark = False
        primary_label = MEMBER_000_SYSTEM_LABEL
        badges = ["Automated"]
        badge_keys = ["automated"]
    elif premium_identity_engine.is_owner(item) or {"owner", "founder"} & badge_key_set:
        primary_label = "Founder · PulseSoc"
    elif {"creator", "verified", "partner_creator"} & badge_key_set or "creator" in label_set:
        primary_label = "Verified Creator"
    elif "teacher" in badge_key_set or "teacher" in label_set:
        primary_label = "Teacher"
    elif "marketplace_seller" in badge_key_set or "marketplace seller" in label_set:
        primary_label = "Marketplace Seller"
    elif "livestream_eligible" in badge_key_set or "livestream eligible" in label_set:
        primary_label = "Livestream Eligible"
    elif "trusted_member" in badge_key_set or "trusted member" in label_set:
        primary_label = "Trusted Member"
    else:
        primary_label = "Member"
    return {
        "id": user_id if user_id > 0 else None,
        "user_id": user_id if user_id > 0 else None,
        "public_player_id": public_player_id or None,
        "username": MEMBER_000_PUBLIC_PLAYER_ID if is_member_000 else (item.get("username") or None),
        "handle": MEMBER_000_PUBLIC_PLAYER_ID if is_member_000 else (item.get("username") or None),
        "display_name": display[:80],
        "avatar_url": avatar_url,
        "profile_url": f"/pulse/id/{user_id}" if user_id > 0 else (f"/pulse/@{public_player_id}" if public_player_id else ""),
        "rank": primary_label,
        "primary_label": primary_label,
        "badges": badges,
        "badge_keys": badge_keys,
        "premium_verified": bool(premium_mark),
        "premium_mark": premium_mark,
        "account_type": AUTOMATED_ACCOUNT_TYPE if is_member_000 else "PERSON",
        "automated": bool(is_member_000),
        "official_system_account": bool(is_member_000),
    }


def _notification_actor(cur, user_id):
    try:
        cur.execute(
            """
            SELECT u.user_id, u.display_name AS user_display_name, u.full_name, u.username,
                   ap.public_player_id AS author_public_player_id
            FROM users u
            LEFT JOIN arena_profiles ap ON ap.user_id=u.user_id
            WHERE u.user_id=?
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = _row(cur.fetchone()) or {"user_id": int(user_id)}
        author = _public_author(row)
        return {
            "display_name": author.get("display_name") or f"PulseSoc Member #{int(user_id)}",
            "public_player_id": author.get("public_player_id") or "",
        }
    except Exception:
        return {"display_name": f"PulseSoc Member #{int(user_id)}", "public_player_id": ""}


def _media_for_posts(post_ids):
    if not post_ids:
        return {}
    conn = user_context.connect()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(post_ids))
    try:
        cur.execute(f"SELECT id, media_ids_json FROM pulse_posts WHERE id IN ({placeholders})", [int(x) for x in post_ids])
        media_id_to_post = {}
        for post_row in cur.fetchall():
            post = dict(post_row)
            post_id = int(post.get("id") or 0)
            for media_id in _normalize_media_ids(post.get("media_ids_json")):
                media_id_to_post[int(media_id)] = post_id
        media_ids = sorted(media_id_to_post)
        id_clause = ""
        params = [str(x) for x in post_ids]
        if media_ids:
            id_placeholders = ",".join(["?"] * len(media_ids))
            id_clause = f" OR id IN ({id_placeholders})"
            params.extend(media_ids)
        cur.execute(
            f"""
            SELECT * FROM chat_media_uploads
            WHERE ((context_type IN ('pulse','pulse_post') AND context_id IN ({placeholders})){id_clause})
              AND COALESCE(moderation_status,'approved')!='blocked'
            ORDER BY id ASC
            """,
            params,
        )
        media = {}
        post_id_set = {int(x) for x in post_ids}
        for row in cur.fetchall():
            item = dict(row)
            post_id = media_id_to_post.get(int(item.get("id") or 0), int(item.get("context_id") or 0))
            if post_id not in post_id_set:
                continue
            resolved = media_service.resolve_media(item)
            payload = _canonical_media_payload(item, resolved, index=len(media.get(post_id, [])))
            # Never hand a renderer a media object it cannot draw. The payload
            # shape is always complete, so an attachment whose upload produced no
            # URL still serializes as a full object with blank urls and 0x0
            # dimensions -- which clients counted as "has media" and reserved a
            # full-bleed box for. Omitting it is what makes the post text-only.
            if not (payload.get("valid_url") or payload.get("media_url")):
                logging.info(
                    "pulse_media_invalid_omitted post_id=%s media_id=%s hydration_state=%s",
                    post_id,
                    payload.get("id"),
                    payload.get("hydration_state"),
                )
                continue
            media.setdefault(post_id, []).append(payload)
        return media
    except Exception as exc:
        logging.warning("PulseSoc media hydration skipped: %s", exc)
        return {}
    finally:
        conn.close()


def _music_for_posts(post_ids):
    """Hydrate creator-safe music attached to feed posts in one query."""
    if not post_ids:
        return {}
    conn = user_context.connect()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(post_ids))
    try:
        cur.execute(
            f"""
            SELECT CASE WHEN pcm.content_type='reel' THEN r.post_id ELSE pcm.content_id END AS content_id,
                   pcm.audio_track_id, pcm.title, pcm.artist, pcm.source,
                   pcm.license_snapshot_json, pcm.created_at, pcm.audio_start_time, pcm.audio_volume, pcm.original_audio_muted,
                   at.audio_url AS current_audio_url,
                   at.duration_seconds AS current_duration_seconds
            FROM pulse_content_music pcm
            JOIN pulse_audio_tracks at ON CAST(at.id AS TEXT)=CAST(pcm.audio_track_id AS TEXT)
            LEFT JOIN pulse_reels r ON pcm.content_type='reel' AND CAST(r.id AS TEXT)=CAST(pcm.content_id AS TEXT)
            WHERE (
                (pcm.content_type IN ('post','video') AND pcm.content_id IN ({placeholders}))
                OR (pcm.content_type='reel' AND r.post_id IN ({placeholders}))
              )
              AND COALESCE(at.safety_status,'approved')='approved'
              AND COALESCE(at.active,1)=1
              AND COALESCE(at.approved_by_admin,0)=1
              AND COALESCE(at.commercial_use_allowed,0)=1
              AND COALESCE(at.remix_edit_allowed,0)=1
              AND COALESCE(at.removed_at,'')=''
              AND COALESCE(at.audio_url,'')!=''
            ORDER BY CASE WHEN pcm.content_type='video' THEN 0 WHEN pcm.content_type='post' THEN 1 ELSE 2 END, pcm.created_at DESC
            """,
            [int(post_id) for post_id in post_ids] * 2,
        )
        music = {}
        for row in cur.fetchall():
            item = dict(row)
            post_id = int(item.get("content_id") or 0)
            if post_id in music:
                continue
            snapshot = _json(item.get("license_snapshot_json"), {})
            audio_baked_in = bool(snapshot.get("audio_baked_in"))
            audio_url = "" if audio_baked_in else _public_media_url(item.get("current_audio_url") or snapshot.get("audio_url") or snapshot.get("preview_url") or "")
            if not audio_url and not audio_baked_in:
                continue
            music[post_id] = {
                "audio_id": str(item.get("audio_track_id") or snapshot.get("track_id") or ""),
                "track_id": str(item.get("audio_track_id") or snapshot.get("track_id") or ""),
                "title": _clean_text(item.get("title") or snapshot.get("title") or "Approved track", 180),
                "artist": _clean_text(item.get("artist") or snapshot.get("artist") or "PulseSoc Music", 180),
                "attached_audio_url": audio_url,
                "audio_url": audio_url,
                "preview_url": audio_url,
                "duration_seconds": int(float(item.get("current_duration_seconds") or snapshot.get("duration_seconds") or snapshot.get("duration") or 0) or 0),
                "audio_duration": int(float(item.get("current_duration_seconds") or snapshot.get("duration_seconds") or snapshot.get("duration") or 0) or 0),
                "audio_start_time": float(item.get("audio_start_time") or snapshot.get("audio_start_time") or snapshot.get("start_seconds") or 0),
                "audio_volume": max(0.0, min(float(item.get("audio_volume") or snapshot.get("volume") or snapshot.get("audio_volume") or 1), 1.0)),
                "original_audio_muted": bool(int(item.get("original_audio_muted") if item.get("original_audio_muted") is not None else 1)),
                "audio_baked_in": audio_baked_in,
                "source": _clean_text(item.get("source") or snapshot.get("source") or "PulseSoc", 120),
                "is_creator_safe": True,
            }
        return music
    except Exception as exc:
        logging.warning("PulseSoc music hydration skipped: %s", exc)
        return {}
    finally:
        conn.close()


def _reaction_counts(cur, post_ids):
    if not post_ids:
        return {}
    placeholders = ",".join(["?"] * len(post_ids))
    cur.execute(f"SELECT post_id, reaction_type, COUNT(*) AS total FROM pulse_reactions WHERE post_id IN ({placeholders}) GROUP BY post_id, reaction_type", post_ids)
    counts = {}
    for row in cur.fetchall():
        item = dict(row)
        counts.setdefault(int(item["post_id"]), {})[item["reaction_type"]] = int(item["total"] or 0)
    return counts


def _comment_counts(cur, post_ids):
    if not post_ids:
        return {}
    placeholders = ",".join(["?"] * len(post_ids))
    cur.execute(f"SELECT post_id, COUNT(*) AS total FROM pulse_comments WHERE post_id IN ({placeholders}) AND deleted_at IS NULL AND moderation_status!='blocked' GROUP BY post_id", post_ids)
    return {int(row["post_id"]): int(row["total"] or 0) for row in cur.fetchall()}


def _repost_counts(cur, post_ids):
    """
    How many live reposts each post has.

    Deliberately the same predicate `_viewer_post_state` uses for the `reposted`
    flag — `deleted_at IS NULL` on the repost row — so undoing a repost drops the
    flag and the count together. Counting deleted rows here would leave a post
    reading "1 repost" with the button showing not-reposted, which is the state
    the mobile client used to invent locally.
    """
    if not post_ids:
        return {}
    placeholders = ",".join(["?"] * len(post_ids))
    cur.execute(
        f"""
        SELECT repost_of_post_id, COUNT(*) AS total
        FROM pulse_posts
        WHERE repost_of_post_id IN ({placeholders}) AND deleted_at IS NULL
        GROUP BY repost_of_post_id
        """,
        post_ids,
    )
    return {int(row["repost_of_post_id"]): int(row["total"] or 0) for row in cur.fetchall()}


def _view_counts(cur, post_ids):
    if not post_ids:
        return {}
    placeholders = ",".join(["?"] * len(post_ids))
    cur.execute(f"SELECT post_id, COUNT(*) AS total FROM pulse_post_views WHERE post_id IN ({placeholders}) GROUP BY post_id", post_ids)
    return {int(row["post_id"]): int(row["total"] or 0) for row in cur.fetchall()}


def _media_with_attached_music(media, music):
    if not media or not music or not (music.get("attached_audio_url") or music.get("audio_url") or music.get("preview_url")):
        return media or []
    out = []
    for item in media or []:
        enriched = dict(item)
        enriched.update({
            "audio_id": music.get("audio_id") or music.get("track_id") or "",
            "music_id": music.get("track_id") or music.get("audio_id") or "",
            "attached_audio_url": music.get("attached_audio_url") or music.get("audio_url") or music.get("preview_url") or "",
            "audio_title": music.get("title") or "Approved track",
            "audio_artist": music.get("artist") or "PulseSoc Music",
            "audio_duration": music.get("audio_duration") or music.get("duration_seconds") or 0,
            "audio_start_time": music.get("audio_start_time") or 0,
            "audio_volume": music.get("audio_volume") or 1,
            "original_audio_muted": True,
        })
        out.append(enriched)
    return out


def _public_post(
    row,
    media=None,
    reactions=None,
    comments=0,
    viewer_reaction=None,
    viewer_user_id=None,
    views=0,
    music=None,
    viewer_saved=False,
    viewer_reposted=False,
    viewer_follows_author=False,
    reposts=0,
):
    item = dict(row)
    author = _public_author(item)
    repost_original = item.get("_repost_original") or None
    display_media = media or []
    if repost_original and not display_media:
        display_media = repost_original.get("media") or []
    display_body = item.get("body") or ""
    if repost_original and repost_original.get("body") and repost_original.get("body") not in display_body:
        display_body = "\n\n".join(part for part in [display_body, repost_original.get("body")] if part)
    display_title = item.get("title") or (repost_original or {}).get("title") or ""
    if author.get("automated"):
        display_title = sanitize_automated_text(display_title)
        display_body = sanitize_automated_text(display_body)
    display_music = music or (repost_original or {}).get("music") or None
    display_media = _media_with_attached_music(display_media, display_music)
    reaction_counts = reactions or {}
    reaction_total = sum(int(v or 0) for v in reaction_counts.values())
    can_delete = bool(viewer_user_id and int(item.get("user_id") or 0) == int(viewer_user_id or 0))
    live_session_id = int(item.get("live_session_id") or 0)
    live_payload = {}
    if (item.get("post_type") or "") == "live" or live_session_id:
        live_status = str(item.get("live_status") or item.get("status") or "live").lower()
        replay_url = item.get("replay_url") or (item.get("playback_url") if live_status in {"archived", "replay_ready"} else "") or ""
        live_payload = {
            "live_session_id": live_session_id,
            "status": live_status,
            "playback_url": (item.get("playback_url") or "") if live_status not in {"processing", "ended"} else "",
            "preview_url": item.get("preview_url") or "",
            "replay_url": replay_url,
            "viewer_count": int(item.get("live_viewer_count") or 0),
            "live_url": f"/pulse/reels?live={live_session_id}" if live_session_id else f"/pulse/post/{item.get('id')}",
        }
        if live_status in {"archived", "replay_ready"} and replay_url and not display_media:
            display_media = [{
                "id": f"live-replay-{live_session_id}",
                "type": "video",
                "media_type": "video",
                "media_url": replay_url,
                "valid_url": replay_url,
                "playback_url": replay_url,
                "thumbnail_url": item.get("preview_url") or "",
                "poster_url": item.get("preview_url") or "",
                "mime_type": "application/vnd.apple.mpegurl" if ".m3u8" in replay_url else "video/mp4",
                "playback_mime_type": "application/vnd.apple.mpegurl" if ".m3u8" in replay_url else "video/mp4",
                "width": 720,
                "height": 1280,
                "aspect_ratio": 0.5625,
                "orientation": "portrait",
                "processing_status": "ready",
                "is_available": True,
            }]
    return {
        "id": item.get("id"),
        "user_id": int(item.get("user_id") or 0),
        "post_type": item.get("post_type") or "text",
        "content_type": "live" if live_session_id else item.get("post_type") or "text",
        "title": display_title,
        "body": display_body,
        "visibility": item.get("visibility") or "public",
        "moderation_status": item.get("moderation_status") or "approved",
        "ai_summary": item.get("ai_summary") or "",
        "ai_tags": _json(item.get("ai_tags_json"), []),
        "tags": _json(item.get("tags_json"), []),
        "sentiment": item.get("sentiment") or "neutral",
        "risk_score": int(item.get("risk_score") or 0),
        "engagement_score": float(item.get("engagement_score") or 0),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "author": author,
        "author_public_name": author.get("display_name"),
        "author_avatar": author.get("avatar_url"),
        "author_public_player_id": author.get("public_player_id"),
        "media": display_media,
        "music": display_music,
        "repost": {
            "original_post_id": int(item.get("repost_of_post_id") or 0),
            "caption": item.get("body") or "",
            "original": repost_original,
        } if repost_original else None,
        "original_post": repost_original,
        "reaction_counts": reaction_counts,
        "reactions_count": reaction_total,
        "comment_count": comments,
        "comments_count": comments,
        "view_count": int(views or 0),
        "views_count": int(views or 0),
        "viewer_reaction": viewer_reaction,
        "saved": bool(viewer_saved),
        "is_saved": bool(viewer_saved),
        "reposted": bool(viewer_reposted),
        "is_reposted": bool(viewer_reposted),
        # Emitted because the clients render it. The web template already read
        # `p.repost_count || p.reposts_count || 0` and the mobile PostCard shows
        # `post.repost_count ? compactCount(...) : "Repost"`, but nothing here ever
        # sent either key: the count was permanently absent, so a mobile client
        # that incremented it optimistically watched the number vanish on the next
        # refresh. Both spellings, matching the pattern used for comments and views.
        "repost_count": int(reposts or 0),
        "reposts_count": int(reposts or 0),
        "viewer_follows_author": bool(viewer_follows_author),
        "is_following_author": bool(viewer_follows_author),
        "can_delete": can_delete,
        "live": live_payload,
        "permalink": live_payload.get("live_url") or f"/pulse/post/{item.get('id')}",
    }


def savable_post_id(row):
    """The post a Save on this row is *about*.

    A repost is its own `pulse_posts` row wrapping an original, and the wrapper
    is what the feed hands the client. Saving the wrapper stores the wrapper's
    id, so the original's card — same content, different id — kept reading back
    unsaved, and the Saved collection filled up with wrapper rows whose body is
    the resharer's caption rather than the post the user meant to keep.

    Collapsing to the original here means one row in `pulse_post_saves` per
    piece of content no matter which card the user tapped. The write path uses
    the same function, so read and write cannot disagree about identity.
    """
    row = row or {}
    original = int(row.get("repost_of_post_id") or 0)
    return original if original > 0 else int(row.get("id") or 0)


def _viewer_post_state(cur, rows, viewer_user_id=None):
    if not viewer_user_id or not rows:
        return {"saved": set(), "reposted": set(), "following": set()}
    post_ids = sorted({int((row or {}).get("id") or 0) for row in rows or [] if int((row or {}).get("id") or 0) > 0})
    # Saves are keyed on the original, reposts and follows on the wrapper, so
    # the two id sets are deliberately not the same list.
    saved_ids = sorted({savable_post_id(row) for row in rows or [] if savable_post_id(row) > 0})
    author_ids = sorted({int((row or {}).get("user_id") or 0) for row in rows or [] if int((row or {}).get("user_id") or 0) > 0})
    saved = set()
    reposted = set()
    following = set()
    if saved_ids:
        placeholders = ",".join(["?"] * len(saved_ids))
        cur.execute(
            f"SELECT post_id FROM pulse_post_saves WHERE user_id=? AND post_id IN ({placeholders})",
            (int(viewer_user_id), *saved_ids),
        )
        saved = {int(row["post_id"]) for row in cur.fetchall()}
    if post_ids:
        placeholders = ",".join(["?"] * len(post_ids))
        cur.execute(
            f"""
            SELECT repost_of_post_id
            FROM pulse_posts
            WHERE user_id=? AND deleted_at IS NULL AND repost_of_post_id IN ({placeholders})
            """,
            (int(viewer_user_id), *post_ids),
        )
        reposted = {int(row["repost_of_post_id"]) for row in cur.fetchall()}
    if author_ids:
        placeholders = ",".join(["?"] * len(author_ids))
        cur.execute(
            f"SELECT followed_user_id FROM pulse_follows WHERE follower_user_id=? AND followed_user_id IN ({placeholders})",
            (int(viewer_user_id), *author_ids),
        )
        following = {int(row["followed_user_id"]) for row in cur.fetchall()}
    return {"saved": saved, "reposted": reposted, "following": following}


@run_once_per_process
def _ensure_home_safety_tables(conn):
    """Create the Home safety tables, and commit them.

    The commit is the entire point of this function's shape, so it takes the
    connection rather than a cursor.

    Three of the five callers (`list_feed`, `list_user_posts`, `count_user_posts`)
    only read, so they close without ever committing. PostgreSQL DDL is
    transactional: a `CREATE TABLE IF NOT EXISTS` on a connection that never
    commits is discarded when the connection closes. `@run_once_per_process` has
    meanwhile recorded success, so the retry never comes, and every feed query
    for the rest of that worker's life dies on

        UndefinedTable: relation "pulse_post_hides" does not exist

    from the `NOT EXISTS` clauses below that filter hidden posts and muted
    authors. `/api/pulse/feed` catches that and reports it to the client as an
    empty feed, so the outage presents as "nobody has posted anything" rather
    than as an error. SQLite autocommits DDL, which is why none of this is
    reproducible locally.

    Caching a DDL call is only sound if the DDL is durable — this is what makes
    the guard's promise true. Committing here also releases the CREATE's
    ShareLock immediately instead of holding it until the request commits, which
    is the lock contention the guard was added for in the first place. Every
    caller runs this as the first statement on a freshly opened connection, so
    the commit commits nothing but the DDL itself.
    """
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_post_hides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            post_id INTEGER,
            reason TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, post_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_user_mutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            muted_user_id INTEGER,
            reason TEXT,
            muted_until TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, muted_user_id)
        )
        """
    )
    # The feed's discovery-visibility predicate reads users.hidden_from_discovery.
    # That column is created by bot.init_db(), which every request path runs
    # before reaching here — but this guard exists precisely because "the schema
    # is always there by now" is the assumption that takes the feed down. A
    # missing column is an OperationalError on the single hottest query in the
    # app, which /api/pulse/feed reports to the client as an empty feed, so the
    # outage would present as "nobody has posted anything".
    #
    # The column is checked before it is added rather than added inside a
    # try/except: on PostgreSQL a duplicate-column ALTER aborts the surrounding
    # transaction, so the swallow-the-error version would poison the connection
    # and fail the commit below on every call — the common case, not the rare
    # one. get_table_columns answers portably (PRAGMA on SQLite,
    # information_schema on PostgreSQL).
    try:
        existing = db.get_table_columns(cur, "users")
        for column, ddl in REQUIRED_USER_COLUMNS:
            if column not in existing:
                cur.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
    except Exception:
        # `users` may not exist yet on a bare connection. Not actionable here,
        # and not worth failing the feed over.
        logging.debug("PULSE_FEED_DISCOVERY_COLUMN_GUARD_SKIPPED", exc_info=True)
    conn.commit()


def _repost_originals(cur, rows, viewer_user_id=None):
    original_ids = sorted({
        int((row or {}).get("repost_of_post_id") or 0)
        for row in rows or []
        if int((row or {}).get("repost_of_post_id") or 0) > 0
    })
    if not original_ids:
        return {}
    placeholders = ",".join(["?"] * len(original_ids))
    cur.execute(
        f"""
        SELECT p.*, u.username, u.email, u.full_name, u.display_name AS user_display_name, u.avatar_url AS user_avatar_url,
               u.plan, u.subscription_plan, u.subscription_status, u.is_pro, u.pro_active, u.pro_expires_at, u.subscription_expires_at,
               u.premium_status, u.premium_expires_at, u.lifetime_premium, u.premium_glow_manual_grant, u.premium_mark_override, u.premium_mark_type,
               ap.avatar_url AS arena_avatar_url, ap.public_player_id AS author_public_player_id
        FROM pulse_posts p
        LEFT JOIN users u ON u.user_id=p.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=p.user_id
        WHERE p.id IN ({placeholders}) AND p.deleted_at IS NULL
        """,
        original_ids,
    )
    originals = []
    for row in cur.fetchall():
        item = _row(row)
        visible, _reason = pulse_visibility_decision(item, viewer_user_id=viewer_user_id, include_private=False)
        if visible:
            originals.append(item)
    if not originals:
        return {}
    hydrated_ids = [int(row["id"]) for row in originals]
    reactions = _reaction_counts(cur, hydrated_ids)
    comments = _comment_counts(cur, hydrated_ids)
    reposts = _repost_counts(cur, hydrated_ids)
    views = _view_counts(cur, hydrated_ids)
    viewer_reactions = {}
    if viewer_user_id and hydrated_ids:
        reaction_placeholders = ",".join(["?"] * len(hydrated_ids))
        cur.execute(f"SELECT post_id, reaction_type FROM pulse_reactions WHERE user_id=? AND post_id IN ({reaction_placeholders})", (int(viewer_user_id), *hydrated_ids))
        viewer_reactions = {int(row["post_id"]): row["reaction_type"] for row in cur.fetchall()}
    viewer_state = _viewer_post_state(cur, originals, viewer_user_id)
    media = _media_for_posts(hydrated_ids)
    music = _music_for_posts(hydrated_ids)
    return {
        int(row["id"]): _public_post(
            row,
            media.get(int(row["id"]), []),
            reactions.get(int(row["id"]), {}),
            comments.get(int(row["id"]), 0),
            viewer_reactions.get(int(row["id"])),
            viewer_user_id,
            views.get(int(row["id"]), 0),
            music.get(int(row["id"])),
            savable_post_id(row) in viewer_state["saved"],
            int(row["id"]) in viewer_state["reposted"],
            int(row.get("user_id") or 0) in viewer_state["following"],
            reposts=reposts.get(int(row["id"]), 0),
        )
        for row in originals
    }


def normalize_feed(feed):
    feed = (feed or "for_you").strip().lower()
    feed = FEED_ALIASES.get(feed, feed)
    return feed if feed in FEEDS else "for_you"


def _normalize_media_ids(media_ids):
    if isinstance(media_ids, str):
        try:
            parsed = json.loads(media_ids)
            media_ids = parsed if isinstance(parsed, list) else []
        except Exception:
            media_ids = [x.strip() for x in media_ids.split(",") if x.strip()]
    normalized = []
    for item in media_ids or []:
        try:
            normalized.append(int(item))
        except Exception:
            continue
    return normalized[:8]


def enqueue_job(job_type, target_type, target_id, run_after=None, max_attempts=3):
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        """
        INSERT INTO pulse_jobs
        (job_type, target_type, target_id, status, attempts, max_attempts, run_after, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)
        """,
        (job_type, target_type, int(target_id), int(max_attempts or 3), run_after or now, now, now),
    )
    conn.commit()
    job_id = int(cur.lastrowid)
    conn.close()
    return job_id


def enqueue_post_jobs(post_id, post_type="text", has_media=False):
    jobs = [
        "moderate_post",
        "scan_links",
        "generate_ai_summary",
        "generate_ai_tags",
        "rank_feed",
        "notify_followers",
        "update_trending_topics",
    ]
    if has_media:
        jobs.append("generate_thumbnail")
    if post_type == "video":
        jobs.append("process_video")
    for job_type in jobs:
        enqueue_job(job_type, "post", post_id)


def create_post(user_id, body="", post_type="text", title="", tags=None, visibility="public", media_ids=None, enqueue_background=True, page_id=None):
    post_type = POST_TYPE_ALIASES.get((post_type or "").strip().lower(), (post_type or "text").strip().lower())
    if post_type not in POST_TYPES:
        return {"ok": False, "message": "Post type not supported.", "status": "rejected", "post_type": post_type}
    body = _clean_text(body, 5000)
    title = _clean_text(title, 160)
    # Reels keep a compatibility post for the existing social/reaction model, but
    # reel-only posts must never leak into the regular PulseSoc feed.
    visibility = visibility if visibility in {"public", "followers", "private", "reel_only"} else "public"
    tags = [str(t).strip("# ").lower()[:32] for t in (tags or []) if str(t).strip("# ")]
    media_ids = _normalize_media_ids(media_ids)
    if not body and not title and not tags and not media_ids:
        return {"ok": False, "message": "Write something or attach media before publishing.", "status": "rejected"}
    moderation = pulse_moderation_engine.moderate_text(body or title, post_type)
    all_tags = list(dict.fromkeys((tags + moderation.get("tags", []))[:12]))
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT ap.public_player_id AS public_player_id, u.pulse_id AS pulse_id, u.username AS username
            FROM users u
            LEFT JOIN arena_profiles ap ON ap.user_id=u.user_id
            WHERE u.user_id=?
            LIMIT 1
            """,
            (int(user_id),),
        )
        profile = _row(cur.fetchone()) or {}
    except Exception:
        profile = {}
    post_public_player_id = profile.get("public_player_id") or profile.get("pulse_id") or profile.get("username") or ""
    try:
        cur.execute(
            """
            INSERT INTO pulse_posts
            (user_id, public_player_id, post_type, body, media_ids_json, title, tags_json, visibility,
             moderation_status, ai_summary, ai_tags_json, sentiment, risk_score, engagement_score, page_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                post_public_player_id,
                post_type,
                body,
                json.dumps(media_ids),
                title,
                json.dumps(all_tags),
                visibility,
                moderation.get("status") or "approved",
                moderation.get("ai_summary") or (body or title)[:220],
                json.dumps(all_tags),
                moderation.get("sentiment") or "neutral",
                int(moderation.get("risk_score") or 0),
                0,
                int(page_id) if page_id else None,
                now,
                now,
            ),
        )
        post_id = int(cur.lastrowid)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    # Progress OS: a post is the event that most often completes a Founding
    # Member qualification, since the rule is two posts on two separate days.
    # Runs after the commit and swallows its own failures — the challenge is a
    # program layered on the product, and a bug in it must never cost someone
    # their post. The reconciliation sweep repairs anything dropped here.
    try:
        from services.business_os.progress import bridge as progress_bridge
        progress_bridge.on_post_created(user_id, post_id=post_id, created_at=now)
    except Exception as exc:
        logging.warning("Progress OS post hook failed post_id=%s user_id=%s error=%s", post_id, user_id, exc)
    try:
        media_service.attach_media_to_message(user_id, post_id, media_ids or [], context_type="pulse", context_id=str(post_id))
    except Exception as exc:
        logging.warning("PulseSoc media attachment failed post_id=%s user_id=%s error=%s", post_id, user_id, exc)
    if enqueue_background:
        try:
            enqueue_post_jobs(post_id, post_type=post_type, has_media=bool(media_ids))
        except Exception as exc:
            logging.warning("PulseSoc job enqueue failed post_id=%s user_id=%s error=%s", post_id, user_id, exc)
    next_url = f"/pulse/post/{post_id}"
    try:
        post_payload = get_post(post_id, viewer_user_id=user_id)
    except Exception as exc:
        logging.warning("PulseSoc post hydration failed post_id=%s user_id=%s error=%s", post_id, user_id, exc)
        post_payload = {
            "id": post_id,
            "post_type": post_type,
            "title": title,
            "body": body,
            "visibility": visibility,
            "moderation_status": moderation.get("status") or "approved",
            "ai_summary": moderation.get("ai_summary") or (body or title)[:220],
            "ai_tags": all_tags,
            "tags": all_tags,
            "sentiment": moderation.get("sentiment") or "neutral",
            "risk_score": int(moderation.get("risk_score") or 0),
            "engagement_score": 0,
            "created_at": now,
            "updated_at": now,
            "author": {"display_name": "PulseSoc creator", "public_player_id": None, "avatar_url": "", "rank": "Member", "badges": ["Member"]},
            "media": [],
            "reaction_counts": {},
            "comment_count": 0,
            "viewer_reaction": None,
            "permalink": next_url,
        }
    return {
        "ok": moderation.get("status") != "blocked",
        "post_id": post_id,
        "next_url": next_url,
        "status": moderation.get("status"),
        "message": moderation.get("message") or "PulseSoc post published.",
        "post": post_payload,
    }


def get_post(post_id, viewer_user_id=None, include_private=False):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*, u.username, u.email, u.full_name, u.display_name AS user_display_name, u.avatar_url AS user_avatar_url,
               u.plan, u.subscription_plan, u.subscription_status, u.is_pro, u.pro_active, u.pro_expires_at, u.subscription_expires_at,
               ap.avatar_url AS arena_avatar_url, ap.public_player_id AS author_public_player_id
        FROM pulse_posts p
        LEFT JOIN users u ON u.user_id=p.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=p.user_id
        WHERE p.id=? AND p.deleted_at IS NULL
        LIMIT 1
        """,
        (int(post_id),),
    )
    row = _row(cur.fetchone())
    if not row:
        conn.close()
        return None
    visible, _reason = pulse_visibility_decision(row, viewer_user_id=viewer_user_id, include_private=include_private)
    if not visible and str(row.get("visibility") or "").lower() == "followers" and viewer_user_id:
        cur.execute(
            "SELECT 1 FROM pulse_follows WHERE follower_user_id=? AND followed_user_id=? LIMIT 1",
            (int(viewer_user_id), int(row.get("user_id") or 0)),
        )
        visible = bool(cur.fetchone())
    if visible and viewer_user_id:
        cur.execute(
            """
            SELECT 1 FROM blocked_users
            WHERE (blocker_user_id=? AND blocked_user_id=?) OR (blocker_user_id=? AND blocked_user_id=?)
            LIMIT 1
            """,
            (int(viewer_user_id), int(row.get("user_id") or 0), int(row.get("user_id") or 0), int(viewer_user_id)),
        )
        visible = not bool(cur.fetchone())
    if not visible:
        conn.close()
        return None
    post_ids = [int(post_id)]
    reactions = _reaction_counts(cur, post_ids)
    comments = _comment_counts(cur, post_ids)
    reposts = _repost_counts(cur, post_ids)
    views = _view_counts(cur, post_ids)
    viewer_reaction = None
    if viewer_user_id:
        cur.execute("SELECT reaction_type FROM pulse_reactions WHERE post_id=? AND user_id=? LIMIT 1", (int(post_id), int(viewer_user_id)))
        viewer_reaction = (_row(cur.fetchone()) or {}).get("reaction_type")
    repost_originals = _repost_originals(cur, [row], viewer_user_id=viewer_user_id)
    if int(row.get("repost_of_post_id") or 0):
        row["_repost_original"] = repost_originals.get(int(row.get("repost_of_post_id") or 0))
    viewer_state = _viewer_post_state(cur, [row], viewer_user_id)
    conn.close()
    media = _media_for_posts(post_ids)
    music = _music_for_posts(post_ids)
    return _public_post(
        row,
        media.get(int(post_id), []),
        reactions.get(int(post_id), {}),
        comments.get(int(post_id), 0),
        viewer_reaction,
        viewer_user_id,
        views.get(int(post_id), 0),
        music.get(int(post_id)),
        savable_post_id(row) in viewer_state["saved"],
        int(post_id) in viewer_state["reposted"],
        int(row.get("user_id") or 0) in viewer_state["following"],
        reposts=reposts.get(int(post_id), 0),
    )


def list_feed(viewer_user_id=None, feed="for_you", topic="", profile_public_player_id="", limit=20, offset=0):
    feed = normalize_feed(feed)
    if feed == "my_posts":
        return list_user_posts(viewer_user_id, viewer_user_id=viewer_user_id, limit=limit, offset=offset)
    limit = max(1, min(int(limit or 20), 40))
    offset = max(0, int(offset or 0))
    fetch_limit = limit
    if feed in {"for_you", "following"} and not topic and not profile_public_player_id:
        # Pull a larger recent window before ranking so fresh public posts cannot
        # disappear behind older high-engagement rows on different devices/users.
        fetch_limit = max(limit, min(200, max(120, limit * 5)))
    params = []
    where = _public_feed_where("p")
    if viewer_user_id:
        where = [
            clause.replace(
                "COALESCE(p.visibility,'public')='public'",
                "(COALESCE(p.visibility,'public')='public' OR (p.post_type='live' AND (p.user_id=? OR (p.visibility='followers' AND EXISTS (SELECT 1 FROM pulse_follows pfl WHERE pfl.follower_user_id=? AND pfl.followed_user_id=p.user_id)))))",
            )
            for clause in where
        ]
        params.extend([int(viewer_user_id), int(viewer_user_id)])
        where.append("NOT EXISTS (SELECT 1 FROM blocked_users bu WHERE bu.blocker_user_id=? AND bu.blocked_user_id=p.user_id)")
        params.append(int(viewer_user_id))
        where.append("NOT EXISTS (SELECT 1 FROM blocked_users bu WHERE bu.blocker_user_id=p.user_id AND bu.blocked_user_id=?)")
        params.append(int(viewer_user_id))
        where.append("NOT EXISTS (SELECT 1 FROM pulse_post_hides ph WHERE ph.user_id=? AND ph.post_id=p.id)")
        params.append(int(viewer_user_id))
        # muted_until is a canonical ISO text field in both SQLite and PostgreSQL.
        # Compare it with an ISO parameter so PostgreSQL never attempts the invalid
        # text > timestamptz operation produced by translating datetime('now').
        where.append("NOT EXISTS (SELECT 1 FROM pulse_user_mutes pum WHERE pum.user_id=? AND pum.muted_user_id=p.user_id AND (pum.muted_until IS NULL OR pum.muted_until='' OR pum.muted_until>?))")
        params.extend([int(viewer_user_id), _now()])
    # QA/test and deactivated authors must not surface in any feed. `u` is the
    # users join below, so this costs no extra join. The viewer is always exempt
    # from the predicate: whatever their own account status is, they keep seeing
    # their own posts (this same function backs the profile feed).
    if viewer_user_id:
        where.append(f"(p.user_id=? OR {discovery_visible_sql('u')})")
        params.append(int(viewer_user_id))
    else:
        where.append(discovery_visible_sql("u"))
    if feed == "following" and viewer_user_id:
        where.append("p.user_id IN (SELECT followed_user_id FROM pulse_follows WHERE follower_user_id=?)")
        params.append(int(viewer_user_id))
    elif feed == "scam_alerts":
        where.append("(p.post_type='scam_report' OR p.risk_score>=50 OR p.tags_json LIKE '%scam%')")
    elif feed == "arena_highlights":
        where.append("(p.post_type IN ('replay','arena_result') OR p.tags_json LIKE '%alphaarena%' OR p.tags_json LIKE '%arena%' OR p.body LIKE '%Arena%')")
    elif feed == "roast_clips":
        where.append("(p.post_type='roast_clip' OR p.tags_json LIKE '%roastbattle%' OR p.tags_json LIKE '%roast%' OR p.body LIKE '%Roast Battle%')")
    elif feed == "questions":
        where.append("(p.post_type IN ('poll','question') OR p.tags_json LIKE '%question%' OR p.body LIKE '%?%')")
    elif feed == "crypto":
        crypto_terms = (
            "crypto",
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "solana",
            "sol",
            "token",
            "wallet",
            "whale",
            "defi",
            "nft",
            "market",
            "blockchain",
        )
        crypto_clauses = []
        for term in crypto_terms:
            crypto_clauses.append(
                "(lower(COALESCE(p.tags_json,'')) LIKE ? OR lower(COALESCE(p.body,'')) LIKE ? OR lower(COALESCE(p.title,'')) LIKE ?)"
            )
            token = f"%{term}%"
            params.extend([token, token, token])
        where.append("(" + " OR ".join(crypto_clauses) + ")")
    elif feed == "reels":
        where = [clause.replace("COALESCE(p.visibility,'public')='public'", "COALESCE(p.visibility,'public') IN ('public','reel_only')") for clause in where]
        where.append("(p.post_type IN ('video','replay','roast_clip') OR COALESCE(p.media_ids_json,'[]') NOT IN ('[]',''))")
    if topic:
        where.append("(p.tags_json LIKE ? OR p.body LIKE ?)")
        token = f"%{topic.strip('#').lower()}%"
        params.extend([token, token])
    conn = user_context.connect()
    _ensure_home_safety_tables(conn)
    cur = conn.cursor()
    if profile_public_player_id:
        profile_lookup = str(profile_public_player_id or "").strip().lstrip("@")[:160]
        profile_user_id = _resolve_profile_lookup_user_id(cur, profile_lookup)
        if profile_user_id:
            where.append("p.user_id=?")
            params.append(int(profile_user_id))
        else:
            where.append("(p.public_player_id=? OR ap.public_player_id=? OR lower(u.username)=lower(?))")
            params.extend([profile_lookup[:120], profile_lookup[:120], profile_lookup[:40]])
    if feed == "trending":
        order = "p.engagement_score DESC, p.created_at DESC"
    elif feed in {"for_you", "following"} and not topic and not profile_public_player_id:
        order = "p.created_at DESC, p.id DESC"
    else:
        order = (
            "((CASE WHEN p.user_id IN (SELECT followed_user_id FROM pulse_follows WHERE follower_user_id=?) THEN 18 ELSE 0 END) + "
            "(CASE WHEN p.user_id IN (SELECT friend_user_id FROM pulse_friends WHERE user_id=? AND COALESCE(status,'active')='active') THEN 14 ELSE 0 END) + "
            "p.engagement_score + (CASE WHEN p.risk_score>=70 THEN 8 ELSE 0 END) + "
            "(CASE WHEN p.post_type IN ('scam_report','arena_result','roast_clip') THEN 3 ELSE 0 END)) DESC, p.created_at DESC"
        )
    if feed != "trending" and not (feed in {"for_you", "following"} and not topic and not profile_public_player_id):
        params.extend([int(viewer_user_id or 0), int(viewer_user_id or 0)])
    cur.execute(
        f"""
        SELECT p.*, u.username, u.email, u.full_name, u.display_name AS user_display_name, u.avatar_url AS user_avatar_url,
               u.plan, u.subscription_plan, u.subscription_status, u.is_pro, u.pro_active, u.pro_expires_at, u.subscription_expires_at,
               u.premium_status, u.premium_expires_at, u.lifetime_premium, u.premium_glow_manual_grant, u.premium_mark_override, u.premium_mark_type,
               ap.avatar_url AS arena_avatar_url, ap.public_player_id AS author_public_player_id
        FROM pulse_posts p
        LEFT JOIN users u ON u.user_id=p.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=p.user_id
        WHERE {" AND ".join(where)}
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        (*params, fetch_limit, offset),
    )
    rows = [_row(row) for row in cur.fetchall()]
    post_ids = [int(row["id"]) for row in rows]
    reactions = _reaction_counts(cur, post_ids)
    comments = _comment_counts(cur, post_ids)
    reposts = _repost_counts(cur, post_ids)
    views = _view_counts(cur, post_ids)
    viewer_reactions = {}
    if viewer_user_id and post_ids:
        placeholders = ",".join(["?"] * len(post_ids))
        cur.execute(f"SELECT post_id, reaction_type FROM pulse_reactions WHERE user_id=? AND post_id IN ({placeholders})", (int(viewer_user_id), *post_ids))
        viewer_reactions = {int(row["post_id"]): row["reaction_type"] for row in cur.fetchall()}
    viewer_state = _viewer_post_state(cur, rows, viewer_user_id)
    repost_originals = _repost_originals(cur, rows, viewer_user_id=viewer_user_id)
    for row in rows:
        original_id = int(row.get("repost_of_post_id") or 0)
        if original_id:
            row["_repost_original"] = repost_originals.get(original_id)
    conn.close()
    media = _media_for_posts(post_ids)
    music = _music_for_posts(post_ids)
    posts = [
        _public_post(
            row,
            media.get(int(row["id"]), []),
            reactions.get(int(row["id"]), {}),
            comments.get(int(row["id"]), 0),
            viewer_reactions.get(int(row["id"])),
            viewer_user_id,
            views.get(int(row["id"]), 0),
            music.get(int(row["id"])),
            savable_post_id(row) in viewer_state["saved"],
            int(row["id"]) in viewer_state["reposted"],
            int(row.get("user_id") or 0) in viewer_state["following"],
            reposts=reposts.get(int(row["id"]), 0),
        )
        for row in rows
    ]
    try:
        if feed == "trending" or (feed == "for_you" and (topic or profile_public_player_id)):
            posts = pulse_feed_ranking_engine.rank_posts(posts, {"viewer_user_id": viewer_user_id})
    except Exception:
        logging.exception("PULSE_FEED_RANKING_FAILED feed=%s viewer=%s", feed, viewer_user_id)
    posts = posts[:limit]
    return {"ok": True, "feed": feed, "topic": topic, "posts": posts, "next_offset": offset + len(posts), "has_more": len(rows) == fetch_limit, "intelligence": safe_intelligence_panel(topic)}


def list_user_posts(user_id, viewer_user_id=None, limit=20, offset=0):
    if user_id is None:
        return {"ok": False, "feed": "my_posts", "topic": "", "posts": [], "next_offset": 0, "has_more": False, "intelligence": safe_intelligence_panel("")}
    limit = max(1, min(int(limit or 20), 40))
    offset = max(0, int(offset or 0))
    viewer_is_owner = bool(viewer_user_id and int(viewer_user_id or 0) == int(user_id or 0))
    where = ["p.deleted_at IS NULL", "p.user_id=?"]
    params = [int(user_id)]
    if not viewer_is_owner:
        where = [f"p.user_id=?", *_public_feed_where("p")]
        params = [int(user_id)]
        if viewer_user_id:
            where.append("NOT EXISTS (SELECT 1 FROM blocked_users bu WHERE bu.blocker_user_id=? AND bu.blocked_user_id=p.user_id)")
            params.append(int(viewer_user_id))
            where.append("NOT EXISTS (SELECT 1 FROM pulse_post_hides ph WHERE ph.user_id=? AND ph.post_id=p.id)")
            params.append(int(viewer_user_id))
            where.append("NOT EXISTS (SELECT 1 FROM pulse_user_mutes pum WHERE pum.user_id=? AND pum.muted_user_id=p.user_id AND (pum.muted_until IS NULL OR pum.muted_until='' OR pum.muted_until>?))")
            params.extend([int(viewer_user_id), _now()])
    conn = user_context.connect()
    _ensure_home_safety_tables(conn)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT p.*, u.username, u.email, u.full_name, u.display_name AS user_display_name, u.avatar_url AS user_avatar_url,
               u.plan, u.subscription_plan, u.subscription_status, u.is_pro, u.pro_active, u.pro_expires_at, u.subscription_expires_at,
               ap.avatar_url AS arena_avatar_url, ap.public_player_id AS author_public_player_id
        FROM pulse_posts p
        LEFT JOIN users u ON u.user_id=p.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=p.user_id
        WHERE {" AND ".join(where)}
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    )
    rows = [_row(row) for row in cur.fetchall()]
    post_ids = [int(row["id"]) for row in rows]
    reactions = _reaction_counts(cur, post_ids)
    comments = _comment_counts(cur, post_ids)
    reposts = _repost_counts(cur, post_ids)
    views = _view_counts(cur, post_ids)
    viewer_reactions = {}
    if viewer_user_id and post_ids:
        placeholders = ",".join(["?"] * len(post_ids))
        cur.execute(f"SELECT post_id, reaction_type FROM pulse_reactions WHERE user_id=? AND post_id IN ({placeholders})", (int(viewer_user_id), *post_ids))
        viewer_reactions = {int(row["post_id"]): row["reaction_type"] for row in cur.fetchall()}
    viewer_state = _viewer_post_state(cur, rows, viewer_user_id)
    repost_originals = _repost_originals(cur, rows, viewer_user_id=viewer_user_id)
    for row in rows:
        original_id = int(row.get("repost_of_post_id") or 0)
        if original_id:
            row["_repost_original"] = repost_originals.get(original_id)
    conn.close()
    media = _media_for_posts(post_ids)
    music = _music_for_posts(post_ids)
    posts = [
        _public_post(
            row,
            media.get(int(row["id"]), []),
            reactions.get(int(row["id"]), {}),
            comments.get(int(row["id"]), 0),
            viewer_reactions.get(int(row["id"])),
            viewer_user_id,
            views.get(int(row["id"]), 0),
            music.get(int(row["id"])),
            savable_post_id(row) in viewer_state["saved"],
            int(row["id"]) in viewer_state["reposted"],
            int(row.get("user_id") or 0) in viewer_state["following"],
            reposts=reposts.get(int(row["id"]), 0),
        )
        for row in rows
    ]
    return {"ok": True, "feed": "my_posts", "topic": "", "posts": posts, "next_offset": offset + len(posts), "has_more": len(posts) == limit, "intelligence": safe_intelligence_panel("")}


def count_user_posts(user_id, viewer_user_id=None, media_only=False):
    """Count the same posts ``list_user_posts`` can actually return.

    Profile headers used to count every non-deleted row while the Posts tab used
    public/moderation/status, hide, mute, and block predicates. That made new
    profiles show "1 Posts" and then render "No posts yet." Keep the count and
    listing on one contract.
    """
    if user_id is None:
        return 0
    viewer_is_owner = bool(viewer_user_id and int(viewer_user_id or 0) == int(user_id or 0))
    where = ["p.deleted_at IS NULL", "p.user_id=?"]
    params = [int(user_id)]
    if not viewer_is_owner:
        where = [f"p.user_id=?", *_public_feed_where("p")]
        params = [int(user_id)]
        if viewer_user_id:
            where.append("NOT EXISTS (SELECT 1 FROM blocked_users bu WHERE bu.blocker_user_id=? AND bu.blocked_user_id=p.user_id)")
            params.append(int(viewer_user_id))
            where.append("NOT EXISTS (SELECT 1 FROM pulse_post_hides ph WHERE ph.user_id=? AND ph.post_id=p.id)")
            params.append(int(viewer_user_id))
            where.append("NOT EXISTS (SELECT 1 FROM pulse_user_mutes pum WHERE pum.user_id=? AND pum.muted_user_id=p.user_id AND (pum.muted_until IS NULL OR pum.muted_until='' OR pum.muted_until>?))")
            params.extend([int(viewer_user_id), _now()])
    if media_only:
        where.append("COALESCE(p.media_ids_json,'') NOT IN ('', '[]')")
    conn = user_context.connect()
    _ensure_home_safety_tables(conn)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) AS total FROM pulse_posts p WHERE {' AND '.join(where)}", tuple(params))
    row = cur.fetchone()
    conn.close()
    try:
        return int((dict(row) if row else {}).get("total") or 0)
    except Exception:
        return int(row[0] or 0) if row else 0


def hide_post(user_id, post_id, reason="Hidden from Home"):
    user_id = int(user_id or 0)
    post_id = int(post_id or 0)
    if not user_id or not post_id:
        return {"ok": False, "message": "Valid user and post are required."}, 400
    conn = user_context.connect()
    _ensure_home_safety_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1", (post_id,))
    post = _row(cur.fetchone()) or {}
    if not post:
        conn.close()
        return {"ok": False, "message": "Post not found."}, 404
    if int(post.get("user_id") or 0) == user_id:
        conn.close()
        return {"ok": False, "message": "Your own post cannot be hidden from your Home feed."}, 400
    now = _now()
    cur.execute(
        """
        INSERT INTO pulse_post_hides (user_id, post_id, reason, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, post_id) DO UPDATE SET reason=excluded.reason, updated_at=excluded.updated_at
        """,
        (user_id, post_id, _clean_text(reason or "Hidden from Home", 240), now, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "hidden": True, "post_id": post_id, "message": "Post hidden from Home."}, 200


def get_owned_post_deletion_state(user_id, post_id):
    """Return the owner-scoped deletion state used by routes and UNDX verification."""
    user_id = int(user_id or 0)
    post_id = int(post_id or 0)
    if not user_id or not post_id:
        return None
    conn = user_context.connect()
    try:
        row = conn.execute(
            "SELECT id, user_id, deleted_at FROM pulse_posts WHERE id=? AND user_id=? LIMIT 1",
            (post_id, user_id),
        ).fetchone()
        if not row:
            return None
        record = _row(row) or {}
        return {
            "post_id": post_id,
            "deleted": bool(record.get("deleted_at")),
            "deleted_at": record.get("deleted_at"),
        }
    finally:
        conn.close()


def delete_owned_post(user_id, post_id):
    """Soft-delete exactly one post owned by the authenticated account.

    This is the canonical owner mutation shared by the HTTP route and governed
    UNDX tool.  A post owned by another account is deliberately indistinguishable
    from an unknown post, preventing the mutation path from becoming an oracle.
    """
    user_id = int(user_id or 0)
    post_id = int(post_id or 0)
    if not user_id or not post_id:
        return {"ok": False, "error": "invalid_request", "message": "Valid user and post are required."}
    conn = user_context.connect()
    try:
        row = conn.execute(
            "SELECT id, deleted_at FROM pulse_posts WHERE id=? AND user_id=? LIMIT 1",
            (post_id, user_id),
        ).fetchone()
        record = _row(row) or {}
        if not record:
            return {"ok": False, "error": "not_found", "message": "Post not found."}
        if record.get("deleted_at"):
            return {"ok": True, "post_id": post_id, "deleted": True, "changed": False}
        now = _now()
        conn.execute(
            "UPDATE pulse_posts SET deleted_at=?, updated_at=?, "
            "moderation_status=COALESCE(moderation_status,'approved') "
            "WHERE id=? AND user_id=? AND deleted_at IS NULL",
            (now, now, post_id, user_id),
        )
        conn.commit()
        return {"ok": True, "post_id": post_id, "deleted": True, "changed": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mute_user(user_id, muted_user_id, reason="Muted from Home", muted_until=""):
    user_id = int(user_id or 0)
    muted_user_id = int(muted_user_id or 0)
    if not user_id or not muted_user_id:
        return {"ok": False, "message": "Valid users are required."}, 400
    if user_id == muted_user_id:
        return {"ok": False, "message": "You cannot mute yourself."}, 400
    conn = user_context.connect()
    _ensure_home_safety_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (muted_user_id,))
    if not cur.fetchone():
        conn.close()
        return {"ok": False, "message": "User not found."}, 404
    now = _now()
    cur.execute(
        """
        INSERT INTO pulse_user_mutes (user_id, muted_user_id, reason, muted_until, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, muted_user_id) DO UPDATE SET reason=excluded.reason, muted_until=excluded.muted_until, updated_at=excluded.updated_at
        """,
        (user_id, muted_user_id, _clean_text(reason or "Muted from Home", 240), _clean_text(muted_until or "", 80), now, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "muted": True, "muted_user_id": muted_user_id, "message": "User muted from Home."}, 200


def explain_visibility(post_id, viewer_user_id=None):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_posts WHERE id=? LIMIT 1", (int(post_id or 0),))
    post = _row(cur.fetchone())
    if not post:
        conn.close()
        return {"ok": False, "post_id": int(post_id or 0), "visible": False, "reason": "post_not_found", "media": []}
    visible, reason = pulse_visibility_decision(post, viewer_user_id=viewer_user_id)
    media = _media_for_posts([int(post_id or 0)]).get(int(post_id or 0), [])
    conn.close()
    return {
        "ok": True,
        "post_id": int(post_id or 0),
        "viewer_user_id": int(viewer_user_id or 0),
        "visible": visible,
        "reason": reason,
        "fields": {
            "user_id": post.get("user_id"),
            "visibility": post.get("visibility"),
            "moderation_status": post.get("moderation_status"),
            "status": post.get("status"),
            "deleted_at": post.get("deleted_at"),
            "media_ids_json": post.get("media_ids_json"),
        },
        "media": media,
    }


def _empty_intelligence(topic=""):
    return {
        "trending_topics": [],
        "top_spaces": [
            {"name": "Scam Watch", "slug": "scam-watch", "heat": 0},
            {"name": "Educators", "slug": "educators", "heat": 0},
            {"name": "Alpha Arena", "slug": "alpha-arena", "heat": 0},
            {"name": "Roast Battle", "slug": "roast-battle", "heat": 0},
        ],
        "top_posts": [],
        "active_creators": [],
        "scam_warnings": [],
        "posts_today": 0,
        "open_reports": 0,
        "community_mood": "Warming up",
        "suggested_action": "Create the first PulseSoc for today's crypto conversation.",
        "daily_prompt": daily_prompt(),
        "topic": topic or "",
    }


def safe_intelligence_panel(topic=""):
    try:
        return intelligence_panel(topic)
    except Exception as exc:
        logging.warning("PulseSoc intelligence fallback used: %s", exc)
        return _empty_intelligence(topic)


def intelligence_panel(topic=""):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT tags_json FROM pulse_posts WHERE deleted_at IS NULL AND moderation_status='approved' ORDER BY created_at DESC LIMIT 200")
    counts = {}
    for row in cur.fetchall():
        for tag in _json(row["tags_json"], []):
            counts[tag] = counts.get(tag, 0) + 1
    today_cutoff = datetime.utcnow().date().isoformat()
    cur.execute("SELECT COUNT(*) AS total FROM pulse_posts WHERE created_at>=? AND deleted_at IS NULL", (today_cutoff,))
    posts_today = int((_row(cur.fetchone()) or {}).get("total") or 0)
    cur.execute("SELECT COUNT(*) AS total FROM pulse_reports WHERE status='open'")
    open_reports = int((_row(cur.fetchone()) or {}).get("total") or 0)
    cur.execute(
        """
        SELECT p.id, p.title, p.body, p.post_type, p.engagement_score
        FROM pulse_posts p
        WHERE p.deleted_at IS NULL AND p.visibility='public' AND p.moderation_status='approved'
        ORDER BY COALESCE(p.engagement_score,0) DESC, p.created_at DESC
        LIMIT 5
        """
    )
    top_posts = [
        {
            "id": row["id"],
            "title": row["title"] or (row["body"] or "PulseSoc post")[:80],
            "post_type": row["post_type"] or "text",
            "score": float(row["engagement_score"] or 0),
            "permalink": f"/pulse/post/{row['id']}",
        }
        for row in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT COALESCE(u.display_name, u.username, 'PulseSoc Creator') AS name,
               COUNT(*) AS total
        FROM pulse_posts p
        LEFT JOIN users u ON u.user_id=p.user_id
        WHERE p.deleted_at IS NULL AND p.moderation_status='approved'
        GROUP BY p.user_id, u.display_name, u.username
        ORDER BY total DESC
        LIMIT 5
        """
    )
    active_creators = [{"name": row["name"], "posts": int(row["total"] or 0)} for row in cur.fetchall()]
    cur.execute(
        """
        SELECT id, title, body, risk_score
        FROM pulse_posts
        WHERE deleted_at IS NULL AND moderation_status='approved'
          AND (post_type='scam_report' OR risk_score>=50 OR tags_json LIKE ?)
        ORDER BY created_at DESC
        LIMIT 4
        """,
        ("%scam%",),
    )
    scam_warnings = [
        {"id": row["id"], "title": row["title"] or (row["body"] or "Scam warning")[:80], "risk_score": int(row["risk_score"] or 0), "permalink": f"/pulse/post/{row['id']}"}
        for row in cur.fetchall()
    ]
    conn.close()
    trending = [{"tag": k, "count": v} for k, v in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]]
    top_spaces = [
        {"name": "Scam Watch", "slug": "scam-watch", "heat": counts.get("scamalert", 0) + counts.get("scam", 0)},
        {"name": "Alpha Arena", "slug": "alpha-arena", "heat": counts.get("alphaarena", 0) + counts.get("arena", 0)},
        {"name": "Roast Battle", "slug": "roast-battle", "heat": counts.get("roastbattle", 0) + counts.get("roast", 0)},
        {"name": "Market Psychology", "slug": "market-psychology", "heat": counts.get("marketpsychology", 0)},
    ]
    return {
        "trending_topics": trending,
        "top_spaces": top_spaces,
        "top_posts": top_posts,
        "active_creators": active_creators,
        "scam_warnings": scam_warnings,
        "posts_today": posts_today,
        "open_reports": open_reports,
        "community_mood": "Protective" if any(t["tag"] == "scamalert" for t in trending) else "Curious",
        "suggested_action": "Review new Scam Alerts first." if scam_warnings else "Create the first PulseSoc for today's market conversation.",
        "daily_prompt": daily_prompt(),
    }


def daily_prompt():
    prompts = [
        "What crypto scam did you almost fall for?",
        "What did the market teach you today?",
        "Drop your BTC prediction with one reason.",
        "Share one wallet safety tip.",
        "Who had the best Arena moment today?",
    ]
    day = datetime.utcnow().timetuple().tm_yday
    return prompts[day % len(prompts)]


def add_comment(user_id, post_id, body, parent_comment_id=None, media_ids=None, notify_owner=True):
    post = get_post(post_id, viewer_user_id=user_id, include_private=True)
    if not post:
        return {"ok": False, "message": "Post not found."}, 404
    body = _clean_text(body, 2200)
    moderation = pulse_moderation_engine.moderate_comment(body)
    if moderation.get("status") == "blocked":
        return {"ok": False, "message": "Your comment needs changes before it can be published."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1", (int(post_id),))
    owner_row = _row(cur.fetchone()) or {}
    post_owner_id = int(owner_row.get("user_id") or 0)
    parent_owner_id = 0
    if parent_comment_id:
        cur.execute("SELECT user_id FROM pulse_comments WHERE id=? AND deleted_at IS NULL LIMIT 1", (int(parent_comment_id),))
        parent_row = _row(cur.fetchone()) or {}
        parent_owner_id = int(parent_row.get("user_id") or 0)
    actor = _notification_actor(cur, user_id)
    cur.execute(
        "INSERT INTO pulse_comments (post_id, user_id, parent_comment_id, body, media_ids_json, moderation_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(post_id), int(user_id), int(parent_comment_id or 0) or None, body, json.dumps(media_ids or []), moderation.get("status"), _now()),
    )
    comment_id = int(cur.lastrowid)
    cur.execute("UPDATE pulse_posts SET engagement_score=COALESCE(engagement_score,0)+2, updated_at=? WHERE id=?", (_now(), int(post_id)))
    conn.commit()
    conn.close()
    media_service.attach_media_to_message(user_id, comment_id, media_ids or [], context_type="pulse_comment", context_id=str(comment_id))
    if notify_owner:
        notified = set()
        for recipient_id in [post_owner_id, parent_owner_id]:
            if not recipient_id or int(recipient_id) == int(user_id) or recipient_id in notified:
                continue
            notified.add(recipient_id)
            try:
                pulsesoc_notification_system.notify_post_comment(
                    recipient_user_id=recipient_id,
                    actor_user_id=int(user_id),
                    post_id=int(post_id),
                    comment_id=comment_id,
                    body=body,
                    parent_comment_id=int(parent_comment_id or 0) or None,
                    actor_name=actor.get("display_name") or "",
                    metadata={"media_count": len(media_ids or [])},
                )
            except Exception as exc:
                logging.warning(
                    "PULSE_FEED_COMMENT_NOTIFICATION_FAILED post_id=%s comment_id=%s recipient_user_id=%s error=%s",
                    post_id,
                    comment_id,
                    recipient_id,
                    exc,
                )
    comments = list_comments(post_id).get("comments", [])
    comment = next((item for item in comments if int(item.get("id") or 0) == comment_id), None)
    return {"ok": True, "comment_id": comment_id, "comment": comment, "comments_count": len(comments), "message": "Comment posted."}, 200


COMMENT_PAGE_LIMIT_MAX = 120
COMMENT_PAGE_LIMIT_DEFAULT = 80


def list_comments(post_id, limit=80, offset=0, viewer_user_id=None):
    """Return one page of a post's comments, oldest first.

    Pagination is offset-based rather than cursor-based to match the existing
    ORDER BY (created_at ASC, id ASC), which is stable for already-published
    comments: a new comment always sorts after the current page, so paging
    forward cannot skip or repeat a row. `total` is the unpaginated count, so a
    client knows whether another page exists without fetching it — returning
    only the rows makes "has more" unanswerable except by guessing from the page
    being full, which is wrong exactly when the total is a multiple of limit.

    `viewer_user_id` populates can_edit/can_delete. Without it the client has to
    infer ownership by comparing ids itself, which is how a delete control ends
    up rendered for a comment the server will refuse to delete.
    """
    safe_limit = max(1, min(int(limit or COMMENT_PAGE_LIMIT_DEFAULT), COMMENT_PAGE_LIMIT_MAX))
    safe_offset = max(0, int(offset or 0))
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) AS total
        FROM pulse_comments c
        WHERE c.post_id=? AND c.deleted_at IS NULL AND c.moderation_status!='blocked'
        """,
        (int(post_id),),
    )
    total = int(dict(cur.fetchone() or {}).get("total") or 0)
    cur.execute(
        """
        SELECT c.*, u.username, u.email, u.full_name, u.display_name AS user_display_name, u.avatar_url AS user_avatar_url,
               u.plan, u.subscription_plan, u.subscription_status, u.is_pro, u.pro_active, u.pro_expires_at, u.subscription_expires_at,
               ap.avatar_url AS arena_avatar_url, ap.public_player_id AS author_public_player_id
        FROM pulse_comments c
        LEFT JOIN users u ON u.user_id=c.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=c.user_id
        WHERE c.post_id=? AND c.deleted_at IS NULL AND c.moderation_status!='blocked'
        ORDER BY c.created_at ASC, c.id ASC
        LIMIT ? OFFSET ?
        """,
        (int(post_id), safe_limit, safe_offset),
    )
    comments = []
    viewer_id = int(viewer_user_id) if viewer_user_id else 0
    for row in cur.fetchall():
        item = dict(row)
        author_id = int(item.get("user_id") or 0)
        owned = bool(viewer_id) and author_id == viewer_id
        comments.append({
            "id": item.get("id"),
            "post_id": item.get("post_id"),
            "user_id": item.get("user_id"),
            "parent_comment_id": item.get("parent_comment_id"),
            "body": item.get("body"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "edited_at": item.get("edited_at"),
            "can_edit": owned,
            "can_delete": owned,
            "author": _public_author(item),
        })
    conn.close()
    return {
        "ok": True,
        "comments": comments,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "has_more": safe_offset + len(comments) < total,
    }


def get_comment(comment_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.*, u.username, u.email, u.full_name, u.display_name AS user_display_name, u.avatar_url AS user_avatar_url,
               u.plan, u.subscription_plan, u.subscription_status, u.is_pro, u.pro_active, u.pro_expires_at, u.subscription_expires_at,
               ap.avatar_url AS arena_avatar_url, ap.public_player_id AS author_public_player_id
        FROM pulse_comments c
        LEFT JOIN users u ON u.user_id=c.user_id
        LEFT JOIN arena_profiles ap ON ap.user_id=c.user_id
        WHERE c.id=? AND c.deleted_at IS NULL AND c.moderation_status!='blocked'
        LIMIT 1
        """,
        (int(comment_id),),
    )
    row = _row(cur.fetchone())
    conn.close()
    if not row:
        return None
    return {
        "id": row.get("id"),
        "post_id": row.get("post_id"),
        "user_id": row.get("user_id"),
        "parent_comment_id": row.get("parent_comment_id"),
        "body": row.get("body"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "edited_at": row.get("edited_at"),
        "author": _public_author(row),
    }


def react(user_id, post_id, reaction_type, notify_owner=True):
    reaction_type = (reaction_type or "").strip().lower()
    if reaction_type not in REACTIONS:
        return {"ok": False, "message": "Choose a supported PulseSoc reaction."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1", (int(post_id),))
    post_row = _row(cur.fetchone())
    if not post_row:
        conn.close()
        return {"ok": False, "message": "Post not found."}, 404
    post_owner_id = int(post_row.get("user_id") or 0)
    actor = _notification_actor(cur, user_id)
    cur.execute("SELECT reaction_type FROM pulse_reactions WHERE post_id=? AND user_id=? LIMIT 1", (int(post_id), int(user_id)))
    existing = _row(cur.fetchone())
    if existing and existing.get("reaction_type") == reaction_type:
        cur.execute("DELETE FROM pulse_reactions WHERE post_id=? AND user_id=?", (int(post_id), int(user_id)))
        cur.execute("UPDATE pulse_posts SET engagement_score=MAX(COALESCE(engagement_score,0)-1,0), updated_at=? WHERE id=?", (_now(), int(post_id)))
        conn.commit()
        reactions = _reaction_counts(cur, [int(post_id)]).get(int(post_id), {})
        conn.close()
        return {"ok": True, "message": "Reaction removed.", "reaction_type": reaction_type, "post_id": int(post_id), "reaction_counts": reactions, "reactions_count": sum(int(v or 0) for v in reactions.values()), "removed": True}, 200
    cur.execute(
        """
        INSERT INTO pulse_reactions (post_id, user_id, reaction_type, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(post_id, user_id) DO UPDATE SET reaction_type=excluded.reaction_type, created_at=excluded.created_at
        """,
        (int(post_id), int(user_id), reaction_type, _now()),
    )
    cur.execute("UPDATE pulse_posts SET engagement_score=COALESCE(engagement_score,0)+1, updated_at=? WHERE id=?", (_now(), int(post_id)))
    conn.commit()
    reactions = _reaction_counts(cur, [int(post_id)]).get(int(post_id), {})
    conn.close()
    if notify_owner and post_owner_id and int(post_owner_id) != int(user_id):
        try:
            pulsesoc_notification_system.notify_post_like(
                recipient_user_id=post_owner_id,
                actor_user_id=int(user_id),
                post_id=int(post_id),
                reaction_type=reaction_type,
                actor_name=actor.get("display_name") or "",
            )
        except Exception as exc:
            logging.warning(
                "PULSE_FEED_REACTION_NOTIFICATION_FAILED post_id=%s recipient_user_id=%s reaction=%s error=%s",
                post_id,
                post_owner_id,
                reaction_type,
                exc,
            )
    return {"ok": True, "message": "Reaction added.", "reaction_type": reaction_type, "post_id": int(post_id), "reaction_counts": reactions, "reactions_count": sum(int(v or 0) for v in reactions.values())}, 200


def repost(
    user_id,
    post_id,
    note="",
    undo=False,
    default_title="",
    default_body="",
    reposter_public_player_id="",
    original_public_player_id="",
):
    """
    Repost, un-repost, and the idempotence that makes both safe.

    The route this replaces unconditionally INSERTed a 'repost' row, so it had
    three defects that the clients could only work around by lying to the user:

    1. NO UNDO. There was no delete path at all, so the mobile clients rendered a
       one-way button and left a comment explaining that a toggle would "claim an
       un-repost the server never performed". The user could repost by accident and
       had no way back. `undo=True` soft-deletes, matching `deleted_at IS NULL` —
       the predicate `_viewer_post_state` and `_repost_counts` both already use —
       so the flag and the count drop together.
    2. NO DEDUPE. Two taps meant two repost rows on the same original, and the
       second was invisible to the tapper. Reposting when a live repost already
       exists is now a no-op that reports the existing row.
    3. NO STATE IN THE RESPONSE. It returned `{ok, post_id, next_url}` — no
       `reposted`, no count — so a client had no way to reconcile with the server
       and had to guess. Both are returned now.

    Undo clears EVERY live repost row the viewer holds for this original, not just
    the newest one, because the create-only route left duplicates behind and a
    single-row undo would leave the button stuck on "Reposted" with no way to
    clear it.
    """
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1", (int(post_id),))
    original = _row(cur.fetchone())
    if not original:
        conn.close()
        return {"ok": False, "message": "Post not found."}, 404
    cur.execute(
        "SELECT id FROM pulse_posts WHERE user_id=? AND repost_of_post_id=? AND deleted_at IS NULL ORDER BY id",
        (int(user_id), int(post_id)),
    )
    existing_ids = [int(row["id"]) for row in cur.fetchall()]
    now = _now()

    if undo:
        if not existing_ids:
            # Not an error: the client's optimistic state and the server agree on
            # the outcome, which is all the caller needs. Reporting 404 here would
            # make a double-tapped undo look like a failure.
            counts = _repost_counts(cur, [int(post_id)])
            conn.close()
            return {
                "ok": True,
                "message": "Repost already removed.",
                "post_id": int(post_id),
                "reposted": False,
                "is_reposted": False,
                "repost_count": int(counts.get(int(post_id), 0)),
                "removed": True,
            }, 200
        placeholders = ",".join(["?"] * len(existing_ids))
        cur.execute(
            f"UPDATE pulse_posts SET deleted_at=?, updated_at=? WHERE id IN ({placeholders})",
            (now, now, *existing_ids),
        )
        conn.commit()
        counts = _repost_counts(cur, [int(post_id)])
        conn.close()
        return {
            "ok": True,
            "message": "Repost removed.",
            "post_id": int(post_id),
            "removed_post_ids": existing_ids,
            "reposted": False,
            "is_reposted": False,
            "repost_count": int(counts.get(int(post_id), 0)),
            "removed": True,
        }, 200

    if existing_ids:
        counts = _repost_counts(cur, [int(post_id)])
        conn.close()
        return {
            "ok": True,
            "message": "Already reposted.",
            "post_id": existing_ids[0],
            "original_post_id": int(post_id),
            "reposted": True,
            "is_reposted": True,
            "repost_count": int(counts.get(int(post_id), 0)),
            "next_url": f"/pulse/post/{existing_ids[0]}",
        }, 200

    # Both identities are accepted as arguments because bot.py resolves them
    # through pulse_identity_for_user, which falls back through arena_profiles,
    # the users row and a derived handle. Recomputing them here from
    # arena_profiles alone would silently downgrade attribution for anyone whose
    # profile row is missing, so the caller's answer wins when it has one.
    reposter_public_id = str(reposter_public_player_id or "").lstrip("@")
    if not reposter_public_id:
        try:
            cur.execute("SELECT public_player_id FROM arena_profiles WHERE user_id=? LIMIT 1", (int(user_id),))
            reposter_public_id = str((_row(cur.fetchone()) or {}).get("public_player_id") or "").lstrip("@")
        except Exception:
            reposter_public_id = ""
    original_public_id = str(original_public_player_id or original.get("public_player_id") or "").lstrip("@")
    # `default_body` exists so the reel route can keep saying "Reposted a Reel"
    # while sharing this implementation. Wording is the only thing that differed
    # between the two routes, and it is not worth a second copy of the dedupe and
    # soft-delete logic to preserve.
    body = _clean_text(note, 1200) or _clean_text(default_body, 1200) or (
        f"Reposted a PulseSoc from @{original_public_id}" if original_public_id else "Reposted a PulseSoc"
    )
    try:
        cur.execute(
            """
            INSERT INTO pulse_posts
            (user_id, public_player_id, post_type, body, title, tags_json, visibility,
             moderation_status, repost_of_post_id, created_at, updated_at)
            VALUES (?, ?, 'repost', ?, ?, ?, 'public', 'approved', ?, ?, ?)
            """,
            (
                int(user_id),
                reposter_public_id or None,
                body,
                original.get("title") or default_title or "",
                original.get("tags_json") or "[]",
                int(post_id),
                now,
                now,
            ),
        )
        repost_id = int(cur.lastrowid)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        logging.exception("PULSE_FEED_REPOST_FAILED user_id=%s post_id=%s error=%s", user_id, post_id, exc)
        return {"ok": False, "message": "Repost could not be completed."}, 500
    counts = _repost_counts(cur, [int(post_id)])
    conn.close()
    return {
        "ok": True,
        "message": "Reposted to PulseSoc.",
        "post_id": repost_id,
        "original_post_id": int(post_id),
        "reposted": True,
        "is_reposted": True,
        "repost_count": int(counts.get(int(post_id), 0)),
        "next_url": f"/pulse/post/{repost_id}",
    }, 200


def follow(follower_user_id, followed_user_id=None, followed_public_player_id="", notify_owner=True):
    if not followed_user_id and followed_public_player_id:
        conn = user_context.connect()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM arena_profiles WHERE public_player_id=? LIMIT 1", (followed_public_player_id,))
        row = _row(cur.fetchone())
        conn.close()
        followed_user_id = int((row or {}).get("user_id") or 0)
    if not followed_user_id or int(followed_user_id) == int(follower_user_id):
        return {"ok": False, "message": "Choose another creator to follow."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    actor = _notification_actor(cur, follower_user_id)
    cur.execute(
        "INSERT OR IGNORE INTO pulse_follows (follower_user_id, followed_user_id, followed_public_player_id, created_at) VALUES (?, ?, ?, ?)",
        (int(follower_user_id), int(followed_user_id), followed_public_player_id or "", _now()),
    )
    inserted = getattr(cur, "rowcount", 0) > 0
    conn.commit()
    conn.close()
    if notify_owner and inserted:
        try:
            pulsesoc_notification_system.notify_follow(
                recipient_user_id=int(followed_user_id),
                actor_user_id=int(follower_user_id),
                actor_name=actor.get("display_name") or "",
                actor_profile_id=actor.get("public_player_id") or "",
            )
        except Exception as exc:
            logging.warning(
                "PULSE_FEED_FOLLOW_NOTIFICATION_FAILED follower_user_id=%s followed_user_id=%s error=%s",
                follower_user_id,
                followed_user_id,
                exc,
            )
    return {"ok": True, "message": "Creator followed."}, 200


def report(user_id, target_type, target_id, reason):
    target_type = target_type if target_type in {"post", "comment", "media", "user"} else "post"
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pulse_reports (reporter_user_id, target_type, target_id, reason, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)",
        (int(user_id), target_type, int(target_id), _clean_text(reason, 500), _now()),
    )
    if target_type == "post":
        cur.execute("UPDATE pulse_posts SET moderation_status='needs_review' WHERE id=? AND moderation_status='approved'", (int(target_id),))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Report sent to moderation."}


def record_view(post_id, user_id=None, visitor_id="", dwell_ms=None):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pulse_post_views (post_id, user_id, visitor_id, viewed_at, dwell_ms) VALUES (?, ?, ?, ?, ?)",
        (int(post_id), int(user_id or 0) or None, visitor_id or "", _now(), int(dwell_ms or 0) or None),
    )
    cur.execute("UPDATE pulse_posts SET engagement_score=COALESCE(engagement_score,0)+0.1 WHERE id=?", (int(post_id),))
    conn.commit()
    conn.close()
    return {"ok": True}


def admin_analytics():
    conn = user_context.connect()
    cur = conn.cursor()
    today = datetime.utcnow().date().isoformat()
    counts = {}
    for key, table in [("posts_today", "pulse_posts"), ("comments_today", "pulse_comments"), ("reactions_today", "pulse_reactions"), ("reports_open", "pulse_reports")]:
        if key == "reports_open":
            cur.execute("SELECT COUNT(*) AS total FROM pulse_reports WHERE status='open'")
        else:
            cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE created_at>=?", (today,))
        counts[key] = int((_row(cur.fetchone()) or {}).get("total") or 0)
    cur.execute("SELECT moderation_status, COUNT(*) AS total FROM pulse_posts GROUP BY moderation_status")
    counts["moderation"] = [dict(row) for row in cur.fetchall()]
    try:
        cur.execute("SELECT status, COUNT(*) AS total FROM pulse_jobs GROUP BY status")
        counts["jobs"] = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT status, error_reason, created_at FROM pulse_post_attempts ORDER BY id DESC LIMIT 12")
        counts["post_attempts"] = [dict(row) for row in cur.fetchall()]
    except Exception:
        counts["jobs"] = []
        counts["post_attempts"] = []
    conn.close()
    counts["intelligence"] = safe_intelligence_panel()
    return counts


def _complete_job(cur, job_id, status="done", error_message=""):
    now = _now()
    cur.execute(
        "UPDATE pulse_jobs SET status=?, error_message=?, updated_at=?, completed_at=? WHERE id=?",
        (status, str(error_message or "")[:1000], now, now if status == "done" else None, int(job_id)),
    )


def _process_job(cur, job):
    job_type = job.get("job_type")
    target_id = int(job.get("target_id") or 0)
    if not target_id:
        _complete_job(cur, job["id"], "failed", "Missing target id")
        return
    if job_type == "moderate_post":
        cur.execute("SELECT id, body, title, post_type FROM pulse_posts WHERE id=? LIMIT 1", (target_id,))
        post = _row(cur.fetchone())
        if not post:
            _complete_job(cur, job["id"], "failed", "Post not found")
            return
        moderation = pulse_moderation_engine.moderate_text((post.get("body") or post.get("title") or ""), post.get("post_type") or "text")
        cur.execute(
            "UPDATE pulse_posts SET moderation_status=?, sentiment=?, risk_score=?, updated_at=? WHERE id=? AND moderation_status!='blocked'",
            (moderation.get("status") or "approved", moderation.get("sentiment") or "neutral", int(moderation.get("risk_score") or 0), _now(), target_id),
        )
    elif job_type == "scan_links":
        cur.execute("SELECT body FROM pulse_posts WHERE id=? LIMIT 1", (target_id,))
        post = _row(cur.fetchone()) or {}
        suspicious = 1 if re.search(r"https?://|www\\.|airdrop|seed phrase|private key|claim", post.get("body") or "", re.I) else 0
        if suspicious:
            cur.execute("UPDATE pulse_posts SET risk_score=MAX(COALESCE(risk_score,0), 45), updated_at=? WHERE id=?", (_now(), target_id))
    elif job_type in {"generate_ai_summary", "generate_ai_tags"}:
        cur.execute("SELECT body, title, tags_json FROM pulse_posts WHERE id=? LIMIT 1", (target_id,))
        post = _row(cur.fetchone()) or {}
        if job_type == "generate_ai_summary":
            summary = _clean_text(post.get("body") or post.get("title") or "PulseSoc community update", 220)
            cur.execute("UPDATE pulse_posts SET ai_summary=?, updated_at=? WHERE id=?", (summary, _now(), target_id))
        else:
            tags = _json(post.get("tags_json"), [])
            if not tags and post.get("body"):
                tags = [token.strip("#").lower() for token in re.findall(r"#([A-Za-z0-9_]{2,32})", post.get("body"))][:8]
            cur.execute("UPDATE pulse_posts SET ai_tags_json=?, updated_at=? WHERE id=?", (json.dumps(tags), _now(), target_id))
    elif job_type == "rank_feed":
        cur.execute(
            """
            UPDATE pulse_posts
            SET engagement_score=COALESCE(engagement_score,0)
                + (SELECT COUNT(*) FROM pulse_reactions WHERE post_id=?)
                + ((SELECT COUNT(*) FROM pulse_comments WHERE post_id=? AND deleted_at IS NULL) * 2),
                updated_at=?
            WHERE id=?
            """,
            (target_id, target_id, _now(), target_id),
        )
    elif job_type in {"generate_thumbnail", "process_video"}:
        cur.execute("UPDATE chat_media_uploads SET moderation_status=COALESCE(moderation_status,'approved') WHERE context_type='pulse' AND context_id=?", (str(target_id),))
    elif job_type == "generate_insight_image":
        from .pulse_ai.automated_image_pipeline import process_job as process_insight_image_job

        process_insight_image_job(cur, job)
    elif job_type in {"notify_followers", "update_trending_topics"}:
        pass
    _complete_job(cur, job["id"], "done")


def process_pending_jobs(batch_size=10):
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    cur.execute(
        """
        SELECT * FROM pulse_jobs
        WHERE status='pending' AND (run_after IS NULL OR run_after<=?)
        ORDER BY id ASC
        LIMIT ?
        """,
        (now, max(1, min(int(batch_size or 10), 50))),
    )
    jobs = [dict(row) for row in cur.fetchall()]
    processed = 0
    failed = 0
    for job in jobs:
        try:
            cur.execute("UPDATE pulse_jobs SET status='processing', attempts=COALESCE(attempts,0)+1, updated_at=? WHERE id=? AND status='pending'", (_now(), job["id"]))
            _process_job(cur, job)
            processed += 1
        except Exception as exc:
            failed += 1
            attempts = int(job.get("attempts") or 0) + 1
            max_attempts = int(job.get("max_attempts") or 3)
            status = "failed" if attempts >= max_attempts else "pending"
            run_after = (datetime.utcnow() + timedelta(seconds=min(900, 30 * attempts))).isoformat(timespec="seconds")
            cur.execute(
                "UPDATE pulse_jobs SET status=?, attempts=?, error_message=?, run_after=?, updated_at=? WHERE id=?",
                (status, attempts, str(exc)[:1000], run_after, _now(), job["id"]),
            )
    conn.commit()
    conn.close()
    return {"ok": True, "processed": processed, "failed": failed, "remaining": max(0, len(jobs) - processed)}
