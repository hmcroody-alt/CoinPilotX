"""Global Pulse Feed data and ranking helpers."""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta

from . import media_service, premium_identity_engine, pulse_moderation_engine, user_context


REACTIONS = {"fire", "smart", "scam_alert", "whale", "bullish", "bearish", "funny", "elite", "brutal", "fast_signal"}
FEEDS = {
    "for_you",
    "following",
    "trending",
    "scam_alerts",
    "arena_highlights",
    "roast_clips",
    "questions",
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
    "my-posts": "my_posts",
}
POST_TYPE_ALIASES = {"scam_warning": "scam_report", "question": "poll", "roast": "roast_clip", "roast_battle": "roast_clip"}
POST_TYPES = {"text", "image", "video", "gif", "poll", "replay", "scam_report", "arena_result", "roast_clip"}


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


def _public_author(row):
    public_player_id = row.get("public_player_id") or row.get("author_public_player_id") or ""
    user_id = int(row.get("user_id") or 0)
    display = (
        row.get("user_display_name")
        or row.get("display_name")
        or row.get("username")
        or f"Pulse Member #{str(public_player_id or row.get('user_id') or '000')[-4:]}"
    )
    avatar_url = row.get("user_avatar_url") or row.get("avatar_url") or ""
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
    return {
        "public_player_id": public_player_id or None,
        "display_name": display[:80],
        "avatar_url": avatar_url,
        "rank": "Member",
        "badges": badges,
        "badge_keys": badge_keys,
        "premium_verified": bool(premium_mark),
        "premium_mark": premium_mark,
    }


def _media_for_posts(post_ids):
    if not post_ids:
        return {}
    conn = user_context.connect()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(post_ids))
    try:
        cur.execute(
            f"""
            SELECT * FROM chat_media_uploads
            WHERE context_type='pulse' AND context_id IN ({placeholders}) AND COALESCE(moderation_status,'approved')!='blocked'
            ORDER BY id ASC
            """,
            [str(x) for x in post_ids],
        )
        media = {}
        for row in cur.fetchall():
            item = dict(row)
            media.setdefault(int(item.get("context_id") or 0), []).append({
                "id": item.get("id"),
                "media_type": item.get("media_type"),
                "media_url": item.get("media_url"),
                "thumbnail_url": item.get("thumbnail_url") or item.get("media_url"),
                "mime_type": item.get("mime_type"),
                "file_size_bytes": item.get("file_size_bytes"),
            })
        return media
    except Exception as exc:
        logging.warning("Pulse media hydration skipped: %s", exc)
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


def _public_post(row, media=None, reactions=None, comments=0, viewer_reaction=None, viewer_user_id=None):
    item = dict(row)
    author = _public_author(item)
    reaction_counts = reactions or {}
    reaction_total = sum(int(v or 0) for v in reaction_counts.values())
    can_delete = bool(viewer_user_id and int(item.get("user_id") or 0) == int(viewer_user_id or 0))
    return {
        "id": item.get("id"),
        "post_type": item.get("post_type") or "text",
        "title": item.get("title") or "",
        "body": item.get("body") or "",
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
        "media": media or [],
        "reaction_counts": reaction_counts,
        "reactions_count": reaction_total,
        "comment_count": comments,
        "comments_count": comments,
        "viewer_reaction": viewer_reaction,
        "can_delete": can_delete,
        "permalink": f"/pulse/post/{item.get('id')}",
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


def create_post(user_id, body="", post_type="text", title="", tags=None, visibility="public", media_ids=None, enqueue_background=True):
    post_type = POST_TYPE_ALIASES.get((post_type or "").strip().lower(), (post_type or "text").strip().lower())
    if post_type not in POST_TYPES:
        return {"ok": False, "message": "Post type not supported.", "status": "rejected", "post_type": post_type}
    body = _clean_text(body, 5000)
    title = _clean_text(title, 160)
    visibility = visibility if visibility in {"public", "followers", "private"} else "public"
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
        cur.execute("SELECT public_player_id FROM arena_profiles WHERE user_id=? LIMIT 1", (int(user_id),))
        profile = _row(cur.fetchone()) or {}
    except Exception:
        profile = {}
    try:
        cur.execute(
            """
            INSERT INTO pulse_posts
            (user_id, public_player_id, post_type, body, media_ids_json, title, tags_json, visibility,
             moderation_status, ai_summary, ai_tags_json, sentiment, risk_score, engagement_score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                profile.get("public_player_id"),
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
    try:
        media_service.attach_media_to_message(user_id, post_id, media_ids or [], context_type="pulse", context_id=str(post_id))
    except Exception as exc:
        logging.warning("Pulse media attachment failed post_id=%s user_id=%s error=%s", post_id, user_id, exc)
    if enqueue_background:
        try:
            enqueue_post_jobs(post_id, post_type=post_type, has_media=bool(media_ids))
        except Exception as exc:
            logging.warning("Pulse job enqueue failed post_id=%s user_id=%s error=%s", post_id, user_id, exc)
    next_url = f"/pulse/post/{post_id}"
    try:
        post_payload = get_post(post_id, viewer_user_id=user_id)
    except Exception as exc:
        logging.warning("Pulse post hydration failed post_id=%s user_id=%s error=%s", post_id, user_id, exc)
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
            "author": {"display_name": "Pulse creator", "public_player_id": None, "avatar_url": "", "rank": "Member", "badges": ["Member"]},
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
        "message": moderation.get("message") or "Pulse post published.",
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
    if not include_private and row.get("visibility") != "public" and int(row.get("user_id") or 0) != int(viewer_user_id or 0):
        conn.close()
        return None
    if row.get("moderation_status") != "approved" and int(row.get("user_id") or 0) != int(viewer_user_id or 0):
        conn.close()
        return None
    post_ids = [int(post_id)]
    reactions = _reaction_counts(cur, post_ids)
    comments = _comment_counts(cur, post_ids)
    viewer_reaction = None
    if viewer_user_id:
        cur.execute("SELECT reaction_type FROM pulse_reactions WHERE post_id=? AND user_id=? LIMIT 1", (int(post_id), int(viewer_user_id)))
        viewer_reaction = (_row(cur.fetchone()) or {}).get("reaction_type")
    conn.close()
    media = _media_for_posts(post_ids)
    return _public_post(row, media.get(int(post_id), []), reactions.get(int(post_id), {}), comments.get(int(post_id), 0), viewer_reaction, viewer_user_id)


def list_feed(viewer_user_id=None, feed="for_you", topic="", profile_public_player_id="", limit=20, offset=0):
    feed = normalize_feed(feed)
    if feed == "my_posts":
        return list_user_posts(viewer_user_id, viewer_user_id=viewer_user_id, limit=limit, offset=offset)
    limit = max(1, min(int(limit or 20), 40))
    offset = max(0, int(offset or 0))
    params = []
    where = ["p.deleted_at IS NULL", "p.visibility='public'"]
    if viewer_user_id:
        where.append("(p.moderation_status='approved' OR p.user_id=?)")
        params.append(int(viewer_user_id))
    else:
        where.append("p.moderation_status='approved'")
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
    elif feed == "reels":
        where.append("(p.post_type IN ('video','replay','roast_clip') OR COALESCE(p.media_ids_json,'[]') NOT IN ('[]',''))")
    if topic:
        where.append("(p.tags_json LIKE ? OR p.body LIKE ?)")
        token = f"%{topic.strip('#').lower()}%"
        params.extend([token, token])
    if profile_public_player_id:
        where.append("p.public_player_id=?")
        params.append(str(profile_public_player_id)[:120])
    order = "p.engagement_score DESC, p.created_at DESC" if feed == "trending" else "(p.engagement_score + (CASE WHEN p.risk_score>=70 THEN 8 ELSE 0 END)) DESC, p.created_at DESC"
    conn = user_context.connect()
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
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    )
    rows = [_row(row) for row in cur.fetchall()]
    post_ids = [int(row["id"]) for row in rows]
    reactions = _reaction_counts(cur, post_ids)
    comments = _comment_counts(cur, post_ids)
    viewer_reactions = {}
    if viewer_user_id and post_ids:
        placeholders = ",".join(["?"] * len(post_ids))
        cur.execute(f"SELECT post_id, reaction_type FROM pulse_reactions WHERE user_id=? AND post_id IN ({placeholders})", (int(viewer_user_id), *post_ids))
        viewer_reactions = {int(row["post_id"]): row["reaction_type"] for row in cur.fetchall()}
    conn.close()
    media = _media_for_posts(post_ids)
    posts = [_public_post(row, media.get(int(row["id"]), []), reactions.get(int(row["id"]), {}), comments.get(int(row["id"]), 0), viewer_reactions.get(int(row["id"])), viewer_user_id) for row in rows]
    return {"ok": True, "feed": feed, "topic": topic, "posts": posts, "next_offset": offset + len(posts), "has_more": len(posts) == limit, "intelligence": safe_intelligence_panel(topic)}


def list_user_posts(user_id, viewer_user_id=None, limit=20, offset=0):
    if not user_id:
        return {"ok": False, "feed": "my_posts", "topic": "", "posts": [], "next_offset": 0, "has_more": False, "intelligence": safe_intelligence_panel("")}
    limit = max(1, min(int(limit or 20), 40))
    offset = max(0, int(offset or 0))
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
        WHERE p.deleted_at IS NULL AND p.user_id=?
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        (int(user_id), limit, offset),
    )
    rows = [_row(row) for row in cur.fetchall()]
    post_ids = [int(row["id"]) for row in rows]
    reactions = _reaction_counts(cur, post_ids)
    comments = _comment_counts(cur, post_ids)
    viewer_reactions = {}
    if viewer_user_id and post_ids:
        placeholders = ",".join(["?"] * len(post_ids))
        cur.execute(f"SELECT post_id, reaction_type FROM pulse_reactions WHERE user_id=? AND post_id IN ({placeholders})", (int(viewer_user_id), *post_ids))
        viewer_reactions = {int(row["post_id"]): row["reaction_type"] for row in cur.fetchall()}
    conn.close()
    media = _media_for_posts(post_ids)
    posts = [_public_post(row, media.get(int(row["id"]), []), reactions.get(int(row["id"]), {}), comments.get(int(row["id"]), 0), viewer_reactions.get(int(row["id"])), viewer_user_id) for row in rows]
    return {"ok": True, "feed": "my_posts", "topic": "", "posts": posts, "next_offset": offset + len(posts), "has_more": len(posts) == limit, "intelligence": safe_intelligence_panel("")}


def _empty_intelligence(topic=""):
    return {
        "trending_topics": [],
        "top_spaces": [
            {"name": "Scam Watch", "slug": "scam-watch", "heat": 0},
            {"name": "Crypto Teachers", "slug": "crypto-teachers", "heat": 0},
            {"name": "Alpha Arena", "slug": "alpha-arena", "heat": 0},
            {"name": "Roast Battle", "slug": "roast-battle", "heat": 0},
        ],
        "top_posts": [],
        "active_creators": [],
        "scam_warnings": [],
        "posts_today": 0,
        "open_reports": 0,
        "community_mood": "Warming up",
        "suggested_action": "Create the first Pulse for today's crypto conversation.",
        "daily_prompt": daily_prompt(),
        "topic": topic or "",
    }


def safe_intelligence_panel(topic=""):
    try:
        return intelligence_panel(topic)
    except Exception as exc:
        logging.warning("Pulse intelligence fallback used: %s", exc)
        return _empty_intelligence(topic)


def intelligence_panel(topic=""):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT tags_json FROM pulse_posts WHERE deleted_at IS NULL AND moderation_status='approved' ORDER BY created_at DESC LIMIT 200")
    counts = {}
    for row in cur.fetchall():
        for tag in _json(row["tags_json"], []):
            counts[tag] = counts.get(tag, 0) + 1
    cur.execute("SELECT COUNT(*) AS total FROM pulse_posts WHERE created_at>=date('now') AND deleted_at IS NULL")
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
            "title": row["title"] or (row["body"] or "Pulse post")[:80],
            "post_type": row["post_type"] or "text",
            "score": float(row["engagement_score"] or 0),
            "permalink": f"/pulse/post/{row['id']}",
        }
        for row in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT COALESCE(u.display_name, u.username, 'Pulse Creator') AS name,
               COUNT(*) AS total
        FROM pulse_posts p
        LEFT JOIN users u ON u.user_id=p.user_id
        WHERE p.deleted_at IS NULL AND p.moderation_status='approved'
        GROUP BY p.user_id
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
          AND (post_type='scam_report' OR risk_score>=50 OR tags_json LIKE '%scam%')
        ORDER BY created_at DESC
        LIMIT 4
        """
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
        "suggested_action": "Review new Scam Alerts first." if scam_warnings else "Create the first Pulse for today's market conversation.",
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


def add_comment(user_id, post_id, body, parent_comment_id=None, media_ids=None):
    post = get_post(post_id, viewer_user_id=user_id, include_private=True)
    if not post:
        return {"ok": False, "message": "Post not found."}, 404
    body = _clean_text(body, 2200)
    moderation = pulse_moderation_engine.moderate_comment(body)
    if moderation.get("status") == "blocked":
        return {"ok": False, "message": "Your comment needs changes before it can be published."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pulse_comments (post_id, user_id, parent_comment_id, body, media_ids_json, moderation_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(post_id), int(user_id), int(parent_comment_id or 0) or None, body, json.dumps(media_ids or []), moderation.get("status"), _now()),
    )
    comment_id = int(cur.lastrowid)
    cur.execute("UPDATE pulse_posts SET engagement_score=COALESCE(engagement_score,0)+2, updated_at=? WHERE id=?", (_now(), int(post_id)))
    conn.commit()
    conn.close()
    media_service.attach_media_to_message(user_id, comment_id, media_ids or [], context_type="pulse_comment", context_id=str(comment_id))
    comments = list_comments(post_id).get("comments", [])
    comment = next((item for item in comments if int(item.get("id") or 0) == comment_id), None)
    return {"ok": True, "comment_id": comment_id, "comment": comment, "comments_count": len(comments), "message": "Comment posted."}, 200


def list_comments(post_id, limit=80):
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
        WHERE c.post_id=? AND c.deleted_at IS NULL AND c.moderation_status!='blocked'
        ORDER BY c.created_at ASC, c.id ASC
        LIMIT ?
        """,
        (int(post_id), max(1, min(int(limit or 80), 120))),
    )
    comments = []
    for row in cur.fetchall():
        item = dict(row)
        comments.append({
            "id": item.get("id"),
            "post_id": item.get("post_id"),
            "parent_comment_id": item.get("parent_comment_id"),
            "body": item.get("body"),
            "created_at": item.get("created_at"),
            "author": _public_author(item),
        })
    conn.close()
    return {"ok": True, "comments": comments}


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
        "parent_comment_id": row.get("parent_comment_id"),
        "body": row.get("body"),
        "created_at": row.get("created_at"),
        "author": _public_author(row),
    }


def react(user_id, post_id, reaction_type):
    reaction_type = (reaction_type or "").strip().lower()
    if reaction_type not in REACTIONS:
        return {"ok": False, "message": "Choose a supported Pulse reaction."}, 400
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM pulse_posts WHERE id=? AND deleted_at IS NULL LIMIT 1", (int(post_id),))
    if not cur.fetchone():
        conn.close()
        return {"ok": False, "message": "Post not found."}, 404
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
    return {"ok": True, "message": "Reaction added.", "reaction_type": reaction_type, "post_id": int(post_id), "reaction_counts": reactions, "reactions_count": sum(int(v or 0) for v in reactions.values())}, 200


def follow(follower_user_id, followed_user_id=None, followed_public_player_id=""):
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
    cur.execute(
        "INSERT OR IGNORE INTO pulse_follows (follower_user_id, followed_user_id, followed_public_player_id, created_at) VALUES (?, ?, ?, ?)",
        (int(follower_user_id), int(followed_user_id), followed_public_player_id or "", _now()),
    )
    conn.commit()
    conn.close()
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
            summary = _clean_text(post.get("body") or post.get("title") or "Pulse community update", 220)
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
