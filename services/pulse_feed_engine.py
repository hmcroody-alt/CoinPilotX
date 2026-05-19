"""Global Pulse Feed data and ranking helpers."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime

from . import media_service, pulse_moderation_engine, user_context


REACTIONS = {"fire", "smart", "scam_alert", "whale", "bullish", "bearish", "funny", "elite", "brutal", "fast_signal"}
FEEDS = {"for_you", "following", "trending", "scam_alerts", "arena_highlights", "roast_clips", "questions"}


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
    call_sign = row.get("roast_call_sign") or row.get("display_name") or row.get("username") or ""
    display = call_sign or (f"Arena Pilot #{str(public_player_id or row.get('user_id') or '000')[-4:]}")
    return {
        "public_player_id": public_player_id or None,
        "display_name": display[:80],
        "avatar_url": row.get("avatar_url") or "",
        "rank": row.get("rank") or "Rookie",
        "badges": [row.get("rank") or "Rookie"],
    }


def _media_for_posts(post_ids):
    if not post_ids:
        return {}
    conn = user_context.connect()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(post_ids))
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
    conn.close()
    return media


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


def _public_post(row, media=None, reactions=None, comments=0, viewer_reaction=None):
    item = dict(row)
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
        "author": _public_author(item),
        "media": media or [],
        "reaction_counts": reactions or {},
        "comment_count": comments,
        "viewer_reaction": viewer_reaction,
        "permalink": f"/pulse/post/{item.get('id')}",
    }


def create_post(user_id, body="", post_type="text", title="", tags=None, visibility="public", media_ids=None):
    post_type = post_type if post_type in {"text", "image", "video", "gif", "poll", "replay", "scam_report", "arena_result"} else "text"
    body = _clean_text(body, 5000)
    title = _clean_text(title, 160)
    visibility = visibility if visibility in {"public", "followers", "private"} else "public"
    tags = [str(t).strip("# ").lower()[:32] for t in (tags or []) if str(t).strip("# ")]
    moderation = pulse_moderation_engine.moderate_text(body or title, post_type)
    all_tags = list(dict.fromkeys((tags + moderation.get("tags", []))[:12]))
    now = _now()
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT public_player_id FROM arena_profiles WHERE user_id=? LIMIT 1", (int(user_id),))
    profile = _row(cur.fetchone()) or {}
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
            json.dumps([int(x) for x in (media_ids or []) if str(x).isdigit()][:8]),
            title,
            json.dumps(all_tags),
            visibility,
            moderation.get("status"),
            moderation.get("ai_summary"),
            json.dumps(all_tags),
            moderation.get("sentiment"),
            int(moderation.get("risk_score") or 0),
            0,
            now,
            now,
        ),
    )
    post_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    media_service.attach_media_to_message(user_id, post_id, media_ids or [], context_type="pulse", context_id=str(post_id))
    return {"ok": moderation.get("status") != "blocked", "post_id": post_id, "status": moderation.get("status"), "message": moderation.get("message"), "post": get_post(post_id, viewer_user_id=user_id)}


def get_post(post_id, viewer_user_id=None, include_private=False):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*, u.username, u.display_name AS user_display_name, u.roast_call_sign,
               ap.display_name, ap.avatar_url, ap.rank, ap.public_player_id AS author_public_player_id
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
    return _public_post(row, media.get(int(post_id), []), reactions.get(int(post_id), {}), comments.get(int(post_id), 0), viewer_reaction)


def list_feed(viewer_user_id=None, feed="for_you", topic="", profile_public_player_id="", limit=20, offset=0):
    feed = feed if feed in FEEDS else "for_you"
    limit = max(1, min(int(limit or 20), 40))
    offset = max(0, int(offset or 0))
    params = []
    where = ["p.deleted_at IS NULL", "p.visibility='public'", "p.moderation_status='approved'"]
    if feed == "following" and viewer_user_id:
        where.append("p.user_id IN (SELECT followed_user_id FROM pulse_follows WHERE follower_user_id=?)")
        params.append(int(viewer_user_id))
    elif feed == "scam_alerts":
        where.append("(p.post_type='scam_report' OR p.risk_score>=50 OR p.tags_json LIKE '%scam%')")
    elif feed == "arena_highlights":
        where.append("(p.post_type IN ('replay','arena_result') OR p.tags_json LIKE '%alphaarena%')")
    elif feed == "roast_clips":
        where.append("(p.tags_json LIKE '%roastbattle%' OR p.body LIKE '%Roast Battle%')")
    elif feed == "questions":
        where.append("(p.post_type='poll' OR p.body LIKE '%?%')")
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
        SELECT p.*, u.username, u.display_name AS user_display_name, u.roast_call_sign,
               ap.display_name, ap.avatar_url, ap.rank, ap.public_player_id AS author_public_player_id
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
    posts = [_public_post(row, media.get(int(row["id"]), []), reactions.get(int(row["id"]), {}), comments.get(int(row["id"]), 0), viewer_reactions.get(int(row["id"]))) for row in rows]
    return {"ok": True, "feed": feed, "topic": topic, "posts": posts, "next_offset": offset + len(posts), "has_more": len(posts) == limit, "intelligence": intelligence_panel(topic)}


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
    conn.close()
    trending = [{"tag": k, "count": v} for k, v in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]]
    return {
        "trending_topics": trending,
        "posts_today": posts_today,
        "open_reports": open_reports,
        "community_mood": "Protective" if any(t["tag"] == "scamalert" for t in trending) else "Curious",
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
    return {"ok": True, "comment_id": comment_id, "message": "Comment posted."}, 200


def list_comments(post_id, limit=80):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.*, u.username, u.display_name AS user_display_name, u.roast_call_sign,
               ap.display_name, ap.avatar_url, ap.rank, ap.public_player_id AS author_public_player_id
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
    conn.close()
    return {"ok": True, "message": "Reaction added.", "reaction_type": reaction_type}, 200


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
    conn.close()
    counts["intelligence"] = intelligence_panel()
    return counts
