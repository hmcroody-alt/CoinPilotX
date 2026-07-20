"""UNDX V5 governed discovery over canonical PulseSOC content.

This is a bounded adapter over existing PulseSOC tables and visibility rules;
it is not a second search or content system.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from services.pulse_feed_engine import pulse_visibility_decision


TOPIC_EXPANSIONS = {
    "love": ("love", "romance", "relationship", "dating", "heartbreak", "marriage"),
    "money": ("money", "finance", "wealth", "budget", "income", "invest", "financial freedom", "passive income"),
    "crypto": ("crypto", "bitcoin", "ethereum", "blockchain", "defi", "wallet", "altcoin"),
}


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse_search_request(message: str) -> dict[str, Any] | None:
    text = _clean(message, 2000).lower()
    if not any(term in text for term in ("find ", "search ", "show me", "only show", "trending")):
        return None
    content_type = "all"
    resource_type = "crypto_alert" if "alert" in text and any(term in text for term in ("crypto", "coin", "bitcoin", "ethereum")) else "content"
    if "reel" in text:
        content_type = "reel"
    elif "video" in text:
        content_type = "video"
    elif "post" in text:
        content_type = "post"
    topic = next((name for name in TOPIC_EXPANSIONS if name in text), "")
    if not topic:
        match = re.search(r"(?:about|for)\s+([a-z0-9][a-z0-9 _-]{1,80})", text)
        topic = _clean(match.group(1), 80) if match else _clean(re.sub(r"\b(find|search|show me|pulsesoc|posts?|reels?|videos?|content|trending)\b", " ", text), 80)
    days = 7 if "this week" in text else 30 if "this month" in text else 1 if "today" in text else 0
    return {
        "topic": topic,
        "terms": list(TOPIC_EXPANSIONS.get(topic, (topic,)))[:10],
        "content_type": content_type,
        "resource_type": resource_type,
        "owner_only": any(term in text for term in ("i created", "i made", "my alert", "my crypto")),
        "days": days,
        "saved_only": "saved" in text,
        "following_only": "people i follow" in text or "creators i follow" in text,
        "sort": "trending" if "trending" in text else "newest" if "newest" in text else "relevance",
        "limit": 10,
    }


def search_owned_crypto_alerts(cur, user_id: int, query: str, filters: dict[str, Any]) -> dict[str, Any]:
    """Read canonical alert definitions with an unconditional account boundary."""
    clauses = ["user_id=?", "deleted_at IS NULL", "COALESCE(status,'active')!='deleted'"]
    params: list[Any] = [int(user_id)]
    if int(filters.get("days") or 0) > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(filters["days"]))
        clauses.append("created_at>=?")
        params.append(cutoff.isoformat(timespec="seconds"))
    cur.execute(
        f"""SELECT id, alert_type, symbol, target, condition, threshold_value,
                   target_value, status, active, created_at, last_triggered_at, trigger_count
            FROM alert_rules WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC LIMIT ?""",
        (*params, min(20, max(1, int(filters.get("limit") or 10)))),
    )
    results = []
    for row in cur.fetchall():
        item = dict(row)
        symbol = _clean(item.get("symbol") or item.get("target") or "Crypto", 40)
        condition = _clean(item.get("condition") or item.get("alert_type") or "alert", 60)
        threshold = item.get("threshold_value") if item.get("threshold_value") is not None else item.get("target_value")
        alert_id = int(item["id"])
        results.append({
            "canonical_content_id": alert_id,
            "content_type": "crypto_alert",
            "creator_id": int(user_id),
            "preview_text": f"{symbol} {condition} {threshold if threshold is not None else ''}".strip(),
            "thumbnail_or_media_reference": "",
            "created_at": item.get("created_at") or "",
            "engagement_summary": {"trigger_count": int(item.get("trigger_count") or 0)},
            "deep_link": f"/pulse/crypto/alerts/{alert_id}",
            "visibility_reason": "account_owner_only",
            "relevance_reason": "Matches your crypto alerts",
            "status": item.get("status") or ("active" if item.get("active") else "paused"),
            "last_triggered_at": item.get("last_triggered_at") or "",
        })
    return {"ok": True, "results": results, "filters": filters, "privacy_filter_status": "account_owner_enforced"}


def search_authorized_resources(cur, user_id: int, query: str, filters: dict[str, Any]) -> dict[str, Any]:
    if filters.get("resource_type") == "crypto_alert":
        return search_owned_crypto_alerts(cur, user_id, query, filters)
    return search_visible_content(cur, user_id, query, filters)


def search_visible_content(cur, user_id: int, query: str, filters: dict[str, Any]) -> dict[str, Any]:
    """Search canonical rows, then reapply canonical visibility before hydration."""
    terms = [_clean(term, 80) for term in filters.get("terms") or [] if _clean(term, 80)] or [_clean(query, 80)]
    clauses = ["p.deleted_at IS NULL", "COALESCE(p.status,'published') NOT IN ('deleted','removed','archived')"]
    params: list[Any] = []
    lexical = []
    for term in terms[:10]:
        lexical.append("LOWER(COALESCE(p.title,'') || ' ' || COALESCE(p.body,'') || ' ' || COALESCE(p.tags_json,'') || ' ' || COALESCE(p.ai_tags_json,'')) LIKE ?")
        params.append(f"%{term.lower()}%")
    clauses.append("(" + " OR ".join(lexical) + ")")
    content_type = filters.get("content_type")
    if content_type == "reel":
        clauses.append("LOWER(COALESCE(p.post_type,'')) IN ('reel','short_video')")
    elif content_type == "video":
        clauses.append("LOWER(COALESCE(p.post_type,'')) IN ('video','long_video')")
    elif content_type == "post":
        clauses.append("LOWER(COALESCE(p.post_type,'text')) NOT IN ('reel','short_video','video','long_video')")
    if int(filters.get("days") or 0) > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(filters["days"]))
        clauses.append("p.created_at>=?")
        params.append(cutoff.isoformat(timespec="seconds"))
    join = ""
    if filters.get("saved_only"):
        join += " JOIN pulse_post_saves ps ON ps.post_id=p.id AND ps.user_id=?"
        params.insert(0, int(user_id))
    if filters.get("following_only"):
        join += " JOIN pulse_follows pf ON pf.followed_user_id=p.user_id AND pf.follower_user_id=?"
        params.insert(0, int(user_id))
    order = "p.engagement_score DESC, p.created_at DESC" if filters.get("sort") == "trending" else "p.created_at DESC"
    sql = f"""SELECT p.id, p.user_id, p.post_type, p.title, p.body, p.tags_json,
                     p.visibility, p.moderation_status, p.status, p.engagement_score,
                     p.created_at, p.deleted_at, p.preview_url, p.playback_url
              FROM pulse_posts p {join}
              WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT 60"""
    cur.execute(sql, tuple(params))
    visible = []
    for row in cur.fetchall():
        item = dict(row)
        allowed, reason = pulse_visibility_decision(item, viewer_user_id=int(user_id), include_private=True)
        if not allowed:
            continue
        post_id = int(item["id"])
        kind = str(item.get("post_type") or "post").lower()
        canonical_type = "reel" if kind in {"reel", "short_video"} else "video" if kind in {"video", "long_video"} else "post"
        visible.append({
            "canonical_content_id": post_id,
            "content_type": canonical_type,
            "creator_id": int(item.get("user_id") or 0),
            "preview_text": _clean(item.get("title") or item.get("body"), 280),
            "thumbnail_or_media_reference": item.get("preview_url") or item.get("playback_url") or "",
            "created_at": item.get("created_at") or "",
            "engagement_summary": {"score": float(item.get("engagement_score") or 0)},
            "deep_link": f"/pulse/post/{post_id}",
            "visibility_reason": reason,
            "relevance_reason": f"Matches {filters.get('topic') or query}",
        })
        if len(visible) >= min(20, max(1, int(filters.get("limit") or 10))):
            break
    return {"ok": True, "results": visible, "filters": filters, "privacy_filter_status": "canonical_visibility_reapplied"}


def persist_search_session(cur, user_id: int, conversation_id: int, query: str, filters: dict[str, Any], results: list[dict[str, Any]]) -> str:
    session_id = "undx_search_" + secrets.token_hex(10)
    timestamp = datetime.now(timezone.utc)
    expires_at = timestamp + timedelta(minutes=30)
    cur.execute(
        """INSERT INTO pulse_ai_search_sessions
        (search_session_id, user_id, conversation_id, original_query, normalized_query,
         filters_json, result_ids_json, created_at, expires_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, int(user_id), int(conversation_id), _clean(query, 2000), _clean(query, 2000).lower(),
         json.dumps(filters, sort_keys=True), json.dumps([item["canonical_content_id"] for item in results]),
         timestamp.isoformat(timespec="seconds"), expires_at.isoformat(timespec="seconds"), timestamp.isoformat(timespec="seconds")),
    )
    return session_id


def result_components(search_result: dict[str, Any], search_session_id: str) -> list[dict[str, Any]]:
    return [{"component": "search_result_card", "search_session_id": search_session_id, **item} for item in search_result.get("results") or []]
