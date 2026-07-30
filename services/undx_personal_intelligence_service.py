"""Authorized, source-grounded reads for UNDX's personal intelligence layer.

This module intentionally owns no storage.  It composes existing PulseSoc domain
services and tables into small read models whose individual facts carry
provenance, time, ownership scope, confidence, and a canonical native route.
A read that cannot run produces an empty section *and* names itself in the
caller's ``degraded_sources``, never an invented event and never a silent zero.

That distinction is the whole point of the layer.  A summary that answers "no
messages today" because the query failed is not a missing answer, it is a wrong
one delivered with full confidence -- the exact failure mode this layer exists
to prevent.  So a failed read is recorded, logged, and reported alongside the
result rather than smoothed into an empty list.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from services import db as db_service


SELF_SCOPE = "self_account_only"

logger = logging.getLogger(__name__)

#: Names of reads that failed inside the current :func:`_collecting` block.
#: A context variable rather than a module global because two requests may build
#: summaries concurrently and must not inherit each other's failures.
_DEGRADED: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar(
    "undx_personal_intelligence_degraded", default=None
)


@contextlib.contextmanager
def collecting():
    """Collect the names of reads that failed while building one read model.

    Reentrant on purpose. The gateway wraps every personal read in this block, and
    some read models open one of their own; if the inner block installed a fresh
    set it would swallow its own failures on the way out and the outer caller would
    see a clean run. Nesting therefore shares the outermost set, so a failure is
    reported to every layer that asked to hear about one.
    """
    existing = _DEGRADED.get()
    if existing is not None:
        yield existing
        return
    token = _DEGRADED.set(set())
    try:
        yield _DEGRADED.get()
    finally:
        _DEGRADED.reset(token)


#: Retained for the read models that opened their own block before the gateway
#: began wrapping every personal read.
_collecting = collecting


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value)
    except Exception:
        return {}


def _read(sql: str, params: Iterable[Any] = (), *, source: str = "") -> list[dict[str, Any]]:
    """Run one actor-scoped read, recording rather than hiding a failure.

    ``source`` names the read for ``degraded_sources``.  It is a parameter and
    not something parsed back out of ``sql`` because the table a query reads is
    not always the thing a caller needs told about: the message read below joins
    two tables, and "which one broke" matters less to a caller than "the message
    section of your summary is incomplete".
    """
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        return [_row(item) for item in (cur.fetchall() or [])]
    except Exception:
        degraded = _DEGRADED.get()
        label = source or "unknown"
        if degraded is not None:
            degraded.add(label)
        logger.warning("undx_personal_intelligence_read_failed source=%s", label, exc_info=True)
        return []
    finally:
        conn.close()


def _fact(
    source: str,
    source_id: Any,
    timestamp: Any,
    native_route: str,
    *,
    kind: str,
    title: str,
    detail: str = "",
    data: dict[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "title": str(title or ""),
        "detail": str(detail or ""),
        "source": source,
        "source_id": str(source_id or ""),
        "timestamp": str(timestamp or ""),
        "authorization_scope": SELF_SCOPE,
        "native_route": native_route,
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "data": dict(data or {}),
    }


def notifications_inbox(user_id: int, *, limit: int = 25, unread_only: bool = False) -> list[dict[str, Any]]:
    from services import pulsesoc_notification_system as notifications

    payload = notifications.list_notifications(
        int(user_id), limit=max(1, min(int(limit), 100)), unread_only=bool(unread_only),
    )
    facts = []
    for item in payload.get("notifications") or []:
        notification_id = item.get("notification_id") or item.get("id")
        facts.append(_fact(
            "notifications", notification_id, item.get("created_at"),
            item.get("deep_link") or item.get("action_url") or "/pulse/notifications",
            kind="notification",
            title=item.get("title") or item.get("type") or "Notification",
            detail=item.get("body") or item.get("message") or "",
            data={
                "notification_id": notification_id,
                "category": item.get("category"),
                "read": bool(item.get("read") or item.get("is_read")),
                "source_type": item.get("source_type") or item.get("entity_type"),
                "source_id": item.get("source_id") or item.get("entity_id"),
            },
        ))
    return facts


def notification_explain(user_id: int, notification_id: int) -> dict[str, Any] | None:
    from services import pulsesoc_notification_system as notifications

    item = notifications.get_notification(int(user_id), int(notification_id))
    if not item:
        return None
    source_type = str(item.get("source_type") or item.get("entity_type") or item.get("category") or "event")
    source_id = item.get("source_id") or item.get("entity_id")
    explanation = f"This notification was created from the authorized PulseSoc {source_type} event"
    if source_id:
        explanation += f" {source_id}"
    explanation += "."
    return _fact(
        "notifications", item.get("notification_id") or item.get("id"), item.get("created_at"),
        item.get("deep_link") or item.get("action_url") or "/pulse/notifications",
        kind="notification_explanation", title=item.get("title") or "Notification",
        detail=explanation,
        data={"source_type": source_type, "source_id": source_id},
    )


def notification_group_summary(user_id: int, *, limit: int = 100) -> dict[str, Any]:
    items = notifications_inbox(user_id, limit=limit)
    categories = Counter(str(item["data"].get("category") or "other") for item in items)
    unread = sum(not bool(item["data"].get("read")) for item in items)
    return {
        "count": len(items),
        "unread_count": unread,
        "groups": dict(sorted(categories.items())),
        "sources": [item["source_id"] for item in items],
        "generated_at": _now(),
        "authorization_scope": SELF_SCOPE,
        "native_route": "/pulse/notifications",
    }


def activity_daily_summary(user_id: int, *, days: int = 1) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 31)))).isoformat()
    with _collecting() as degraded:
        return _activity_daily_summary(int(user_id), since, degraded)


def _activity_daily_summary(user_id: int, since: str, degraded: set[str]) -> dict[str, Any]:
    facts = notifications_inbox(user_id, limit=50)

    # Membership lives in ``pulse_conversation_participants`` (bot.py:38775) and
    # the author column is ``sender_user_id`` (bot.py:38807).  Both names are
    # load-bearing: SQLite raises on an unknown table or column, and a raised
    # read here would once have been swallowed into an empty list, so the
    # summary would have reported "no messages" with full confidence.
    for row in _read(
        """SELECT id, sender_user_id, body, created_at FROM pulse_messages
           WHERE sender_user_id!=? AND created_at>=? AND conversation_id IN
             (SELECT conversation_id FROM pulse_conversation_participants WHERE user_id=?)
           ORDER BY created_at DESC LIMIT 25""",
        (user_id, since, user_id),
        source="messages_received",
    ):
        facts.append(_fact(
            "pulse_messages", row.get("id"), row.get("created_at"), "/pulse/messages",
            kind="message_received", title="Message received",
            detail=row.get("body") or "", data={"sender_id": row.get("sender_user_id")},
        ))

    # ``pulse_posts`` and ``pulse_statuses`` both carry ``deleted_at``; a row the
    # user has already deleted is not something that "happened today", so the
    # soft-delete filter is part of correctness rather than tidiness.
    for table, kind, route, soft_delete in (
        ("pulse_posts", "post_created", "/pulse", True),
        ("pulse_reels", "reel_activity", "/pulse/reels", False),
        ("pulse_statuses", "status_activity", "/pulse/status", True),
    ):
        deleted_clause = " AND deleted_at IS NULL" if soft_delete else ""
        for row in _read(
            f"SELECT * FROM {table} WHERE user_id=? AND created_at>=?{deleted_clause}"
            " ORDER BY created_at DESC LIMIT 20",
            (user_id, since),
            source=table,
        ):
            source_id = row.get("id") or row.get("post_id") or row.get("reel_id") or row.get("status_id")
            facts.append(_fact(
                table, source_id, row.get("created_at"), route, kind=kind,
                title=row.get("title") or row.get("caption") or row.get("body") or kind.replace("_", " ").title(),
                data={key: row.get(key) for key in ("share_count", "completion_rate", "replay_count") if key in row},
            ))

    for row in _read(
        """SELECT follower_user_id, created_at FROM pulse_follows
           WHERE followed_user_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 25""",
        (user_id, since),
        source="pulse_follows",
    ):
        facts.append(_fact(
            "pulse_follows", row.get("follower_user_id"), row.get("created_at"), "/pulse/profile",
            kind="new_follower", title="New follower",
            data={"follower_user_id": row.get("follower_user_id")},
        ))

    for row in _read(
        """SELECT id, symbol, condition, threshold_value AS threshold, status, updated_at, created_at
           FROM alert_rules WHERE user_id=? AND COALESCE(updated_at,created_at)>=?
           ORDER BY COALESCE(updated_at,created_at) DESC LIMIT 25""",
        (user_id, since),
        source="alert_rules",
    ):
        facts.append(_fact(
            "alert_rules", row.get("id"), row.get("updated_at") or row.get("created_at"),
            f"/pulse/alerts/{row.get('id')}", kind="crypto_alert",
            title=f"{row.get('symbol') or 'Crypto'} alert",
            detail=str(row.get("status") or ""),
            data={key: row.get(key) for key in ("symbol", "condition", "threshold", "status")},
        ))

    facts.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    counts = Counter(item["kind"] for item in facts)
    # ``complete`` is what a narrator must consult before saying "nothing
    # happened".  With a source missing, the honest answer is "nothing I could
    # see", and confidence drops to say so numerically as well as in prose.
    complete = not degraded
    return {
        "period_start": since,
        "period_end": _now(),
        "count": len(facts),
        "counts": dict(sorted(counts.items())),
        "items": facts,
        "authorization_scope": SELF_SCOPE,
        "confidence": 1.0 if complete else 0.5,
        "complete": complete,
        "degraded_sources": sorted(degraded),
        "native_route": "/pulse/activity/all",
    }


def _search_rows(sql: str, params: tuple[Any, ...], source: str, kind: str, route: str,
                 title_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    facts = []
    for row in _read(sql, params, source=source):
        source_id = row.get("id") or row.get("user_id") or row.get("post_id")
        title = next((row.get(key) for key in title_fields if row.get(key)), kind.replace("_", " ").title())
        facts.append(_fact(
            source, source_id, row.get("created_at") or row.get("updated_at"), route,
            kind=kind, title=title, data=row,
        ))
    return facts


def search_people(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    term = f"%{str(query or '').strip()}%"
    return _search_rows(
        """SELECT user_id, username, display_name, bio, updated_at FROM users
           WHERE deleted_at IS NULL AND account_status='active'
             AND (profile_visibility='public' OR user_id=?)
             AND (username LIKE ? OR display_name LIKE ? OR bio LIKE ?)
           ORDER BY user_id DESC LIMIT ?""",
        (int(user_id), term, term, term, max(1, min(int(limit), 40))),
        "users", "profile", "/pulse/search", ("display_name", "username"),
    )


def search_content(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    term = f"%{str(query or '').strip()}%"
    return _search_rows(
        """SELECT id, user_id, title, body, created_at FROM pulse_posts
           WHERE deleted_at IS NULL AND status='published'
             AND (visibility='public' OR user_id=?)
             AND (title LIKE ? OR body LIKE ?)
           ORDER BY created_at DESC LIMIT ?""",
        (int(user_id), term, term, max(1, min(int(limit), 40))),
        "pulse_posts", "post", "/pulse/search", ("title", "body"),
    )


def search_messages(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    term = f"%{str(query or '').strip()}%"
    return _search_rows(
        """SELECT id, conversation_id, sender_user_id, body, created_at FROM pulse_messages
           WHERE conversation_id IN
             (SELECT conversation_id FROM pulse_conversation_participants WHERE user_id=?)
             AND body LIKE ? ORDER BY created_at DESC LIMIT ?""",
        (int(user_id), term, max(1, min(int(limit), 40))),
        "pulse_messages", "message", "/pulse/messages", ("body",),
    )


def search_activity(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    lowered = str(query or "").lower()
    return [
        item for item in activity_daily_summary(user_id, days=31)["items"]
        if lowered in (item.get("title", "") + " " + item.get("detail", "")).lower()
    ][:max(1, min(int(limit), 40))]


def search_global(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    per_source = max(2, min(10, int(limit)))
    results = (
        search_people(user_id, query, limit=per_source)
        + search_content(user_id, query, limit=per_source)
        + search_messages(user_id, query, limit=per_source)
        + search_activity(user_id, query, limit=per_source)
    )
    results.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return results[:max(1, min(int(limit), 40))]


def settings_inspect(user_id: int) -> dict[str, Any]:
    from services.pulse_settings_routes import load_preferences

    conn = db_service.connect()
    try:
        preferences, revision, updated_at = load_preferences(conn.cursor(), int(user_id))
    finally:
        conn.close()
    return {
        "preferences": preferences,
        "revision": revision,
        "timestamp": str(updated_at or ""),
        "source": "user_settings",
        "authorization_scope": SELF_SCOPE,
        "confidence": 1.0,
        "native_route": "/pulse/settings",
    }


def settings_explain(user_id: int, *, section: str = "all") -> dict[str, Any]:
    snapshot = settings_inspect(user_id)
    prefs = snapshot["preferences"]
    selected = prefs if section == "all" else {section: prefs.get(section, {})}
    return {**snapshot, "section": section, "explanation": selected}


def settings_recommend(user_id: int) -> dict[str, Any]:
    snapshot = settings_inspect(user_id)
    prefs = snapshot["preferences"]
    recommendations = []
    privacy = prefs.get("privacy") or {}
    if privacy.get("accountVisibility") == "public":
        recommendations.append({
            "setting": "privacy.accountVisibility",
            "current": "public",
            "recommendation": "Review whether public visibility still matches your preference.",
            "mutates": False,
        })
    return {**snapshot, "recommendations": recommendations, "mutates": False}


def security_sessions(user_id: int) -> list[dict[str, Any]]:
    rows = _read(
        """SELECT id, device_label, platform, country, last_seen_at, created_at
           FROM mobile_security_sessions
           WHERE user_id=? AND status='active' AND COALESCE(revoked_at,'')=''
           ORDER BY COALESCE(last_seen_at,created_at) DESC LIMIT 50""",
        (int(user_id),),
    )
    return [
        _fact("mobile_security_sessions", row.get("id"), row.get("last_seen_at") or row.get("created_at"),
              "/pulse/settings/devices", kind="security_session",
              title=row.get("device_label") or row.get("platform") or "Device session",
              data={key: row.get(key) for key in ("id", "device_label", "platform", "country")})
        for row in rows
    ]


def security_activity_summary(user_id: int) -> dict[str, Any]:
    sessions = security_sessions(user_id)
    events = _read(
        # ``security_events`` (bot.py:105459) has no severity column.  It records
        # ``status`` and ``ip_address``; grading an event's seriousness is a
        # judgement the table does not store, and inventing one here would put a
        # fabricated risk label in front of a user reading about their account.
        """SELECT id, event_type, status, ip_address, created_at FROM security_events
           WHERE user_id=? ORDER BY created_at DESC LIMIT 50""", (int(user_id),),
        source="security_events",
    )
    facts = sessions + [
        _fact("security_events", row.get("id"), row.get("created_at"), "/pulse/account-health",
              kind="security_event", title=row.get("event_type") or "Security event",
              detail=str(row.get("status") or ""),
              data={"status": row.get("status"), "ip_address": row.get("ip_address")})
        for row in events
    ]
    return {"count": len(facts), "items": facts, "authorization_scope": SELF_SCOPE,
            "generated_at": _now(), "native_route": "/pulse/account-health"}


def security_devices(user_id: int) -> list[dict[str, Any]]:
    """Device view of the same redacted, owner-scoped session source."""
    return security_sessions(user_id)


def premium_status(user_id: int) -> dict[str, Any]:
    from services.premium_entitlement_service import get_user_entitlements, is_premium_user

    entitlements = get_user_entitlements(int(user_id))
    return {
        "premium": bool(is_premium_user(int(user_id))),
        "entitlements": entitlements,
        "source": "premium_entitlement_service",
        "timestamp": _now(),
        "authorization_scope": SELF_SCOPE,
        "confidence": 1.0,
        "native_route": "/pulse/premium",
    }


def premium_entitlements(user_id: int) -> dict[str, Any]:
    state = premium_status(user_id)
    return {**state, "premium": bool(state["premium"])}


def marketplace_search(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    term = f"%{str(query or '').strip()}%"
    return _search_rows(
        # Listings carry a free-text ``price_label`` (bot.py:99994, defaulting to
        # "Request access"), not a numeric amount and currency.  Reading the
        # label keeps the price exactly as the seller wrote it rather than
        # implying a machine-comparable figure the table never held.
        """SELECT id, seller_user_id, title, description, category, price_label, updated_at
           FROM marketplace_listings WHERE status='active'
             AND (title LIKE ? OR description LIKE ?) ORDER BY updated_at DESC LIMIT ?""",
        (term, term, max(1, min(int(limit), 40))),
        "marketplace_listings", "marketplace_listing", "/pulse/marketplace", ("title",),
    )


def marketplace_listing_summary(user_id: int, listing_id: int) -> dict[str, Any] | None:
    rows = _read(
        """SELECT id, seller_user_id, title, description, category, price_label, status, updated_at
           FROM marketplace_listings WHERE id=? AND status='active' LIMIT 1""", (int(listing_id),),
        source="marketplace_listings",
    )
    if not rows:
        return None
    row = rows[0]
    return _fact("marketplace_listings", row["id"], row.get("updated_at"),
                 f"/pulse/marketplace/{int(listing_id)}", kind="marketplace_listing",
                 title=row.get("title") or "Listing", data=row)


def marketplace_order_status(user_id: int, order_id: int) -> dict[str, Any] | None:
    # Orders live in ``business_os_mkt_orders`` (services/business_os/marketplace/
    # schema.py:127).  Its key is ``order_id`` and both party columns are TEXT,
    # so the parameters are stringified -- binding an int against a TEXT column
    # matches nothing in SQLite, which would have read as "no such order" and
    # been indistinguishable from a genuine miss.
    #
    # Both party columns are constrained inside the query rather than compared
    # after the fetch, so a stranger's order id returns no row at all instead of
    # revealing that the order exists.
    rows = _read(
        """SELECT * FROM business_os_mkt_orders WHERE order_id=?
           AND (buyer_user_id=? OR seller_user_id=?) LIMIT 1""",
        (str(order_id), str(user_id), str(user_id)),
        source="business_os_mkt_orders",
    )
    if not rows:
        return None
    row = rows[0]
    return _fact("business_os_mkt_orders", row.get("order_id"),
                 row.get("updated_at") or row.get("created_at"),
                 f"/pulse/orders/{order_id}", kind="marketplace_order",
                 title=f"Order {row.get('order_id')}", detail=str(row.get("status") or ""), data=row)


def ads_performance_summary(user_id: int, *, days: int = 7) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 90)))).isoformat()
    rows = _read(
        # There is no ``business_os_ad_metrics`` rollup table.  Delivery
        # telemetry is event-sourced in ``business_os_ad_impression_events`` and
        # ``business_os_ad_click_events`` (advertising/schema.py:384, :421), each
        # stamped ``event_at``, so the window is applied to the events.
        #
        # Spend is deliberately NOT summed from those events.  Billing has its
        # own accumulator (schema.py:538) holding money the platform actually
        # recognised; recomputing a spend figure from impressions here would
        # invent a number that disagrees with the advertiser's invoice.  The
        # accumulator is lifetime-to-date, not windowed, and is labelled as such
        # so a narrator cannot present it as spend for the period.
        """SELECT c.campaign_id, c.name, c.status, c.updated_at,
                  (SELECT COUNT(*) FROM business_os_ad_impression_events i
                     WHERE i.campaign_id=c.campaign_id AND i.event_at>=?) AS impressions,
                  (SELECT COUNT(*) FROM business_os_ad_click_events k
                     WHERE k.campaign_id=c.campaign_id AND k.event_at>=?) AS clicks,
                  (SELECT COALESCE(SUM(s.billed_cents),0) FROM business_os_ad_spend_accumulator s
                     WHERE s.campaign_id=c.campaign_id) AS billed_cents_to_date
           FROM business_os_ad_campaigns c
           WHERE c.advertiser_user_id=?
           ORDER BY c.updated_at DESC LIMIT 50""",
        (since, since, str(user_id)),
        source="business_os_ad_campaigns",
    )
    return {
        "period_start": since, "period_end": _now(), "campaigns": rows,
        "count": len(rows), "source": "business_os_ad_campaigns",
        "spend_basis": "billed_cents_to_date is lifetime-to-date, not the period above",
        "authorization_scope": SELF_SCOPE, "confidence": 1.0,
        "native_route": "/pulse/intelligence/advertising",
    }


def live_search(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    term = f"%{str(query or '').strip()}%"
    return _search_rows(
        # ``pulse_live_sessions`` (bot.py:100891) has ``user_id``, not
        # ``host_user_id``, and carries no description -- so the search matches
        # on title and category, the two text fields that exist.  ``audience``
        # gates who may see the session at all.
        """SELECT id, user_id, title, category, status, audience, started_at, created_at
           FROM pulse_live_sessions WHERE status IN ('live','starting','ended')
             AND (audience='public' OR user_id=?)
             AND (title LIKE ? OR category LIKE ?) ORDER BY created_at DESC LIMIT ?""",
        (int(user_id), term, term, max(1, min(int(limit), 40))), "pulse_live_sessions",
        "live_session", "/pulse/live", ("title",),
    )


def live_summary(user_id: int, live_id: int) -> dict[str, Any] | None:
    rows = _read(
        """SELECT * FROM pulse_live_sessions WHERE id=?
           AND (user_id=? OR audience='public') LIMIT 1""",
        (int(live_id), int(user_id)),
        source="pulse_live_sessions",
    )
    if not rows:
        return None
    row = rows[0]
    return _fact("pulse_live_sessions", live_id, row.get("created_at"),
                 f"/pulse/live/{int(live_id)}", kind="live_session",
                 title=row.get("title") or "Live", detail=row.get("status") or "", data=row)


def live_performance(user_id: int, live_id: int) -> dict[str, Any] | None:
    rows = _read(
        # Of the metrics this once claimed to return -- peak_viewers,
        # total_views, reaction_count, duration_seconds -- the table holds none.
        # It holds a running ``viewer_count`` and the two timestamps.  Reporting
        # a smaller, true set is the point: a performance summary is exactly the
        # place where an invented number would be believed and acted on.
        """SELECT id, user_id, title, category, status, viewer_count,
                  started_at, ended_at, created_at
           FROM pulse_live_sessions WHERE id=? AND user_id=? LIMIT 1""",
        (int(live_id), int(user_id)),
        source="pulse_live_sessions",
    )
    if not rows:
        return None
    row = rows[0]
    return _fact("pulse_live_sessions", live_id, row.get("ended_at") or row.get("created_at"),
                 f"/pulse/live/{int(live_id)}", kind="live_performance",
                 title=row.get("title") or "Live performance",
                 detail=str(row.get("status") or ""),
                 data=dict(row, metrics_available=["viewer_count", "started_at", "ended_at"]))


def learning_search(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    term = f"%{str(query or '').strip()}%"
    return _search_rows(
        """SELECT id, title, description, updated_at FROM pulse_courses
           WHERE status='published' AND (title LIKE ? OR description LIKE ?)
           ORDER BY updated_at DESC LIMIT ?""",
        (term, term, max(1, min(int(limit), 40))), "pulse_courses",
        "course", "/pulse/courses", ("title",),
    )


def learning_progress(user_id: int) -> list[dict[str, Any]]:
    return _search_rows(
        # ``pulse_course_progress`` does not exist.  Learning progress is tracked
        # per lesson in ``education_progress`` (bot.py:102970), which records a
        # status and a score rather than a percentage -- so this returns what the
        # product measures instead of a completion figure nothing computes.
        """SELECT id, path, lesson_slug, status, score, updated_at FROM education_progress
           WHERE user_id=? ORDER BY updated_at DESC LIMIT 50""",
        (int(user_id),), "education_progress", "learning_progress",
        "/pulse/courses", ("lesson_slug", "path"),
    )


def memory_activity_inspect(user_id: int) -> dict[str, Any]:
    summary = activity_daily_summary(user_id, days=31)
    return {
        "facts": summary["items"],
        "count": summary["count"],
        "storage": "source_retrieval_only",
        "sensitive_memory_written": False,
        "deletion_support": "source-owned",
        "authorization_scope": SELF_SCOPE,
        "generated_at": _now(),
        "native_route": "/pulse/undx/actions",
    }


def groups_list(user_id: int, *, limit: int = 40) -> list[dict[str, Any]]:
    rows = _read(
        """SELECT g.id, g.slug, g.name, g.description, g.category, g.group_type,
                  g.member_count, g.trust_level, g.featured, g.updated_at, g.created_at,
                  CASE WHEN m.user_id IS NULL THEN 0 ELSE 1 END AS joined,
                  COALESCE(m.role,'') AS viewer_role
           FROM pulse_groups g
           LEFT JOIN pulse_group_members m ON m.group_id=g.id AND m.user_id=?
           WHERE COALESCE(g.status,'active')='active' AND COALESCE(g.deleted_at,'')=''
             AND (COALESCE(g.group_type,'public')='public' OR m.user_id IS NOT NULL
                  OR g.owner_user_id=?)
           ORDER BY joined DESC, COALESCE(g.featured,0) DESC,
                    COALESCE(g.member_count,0) DESC, g.id DESC LIMIT ?""",
        (int(user_id), int(user_id), max(1, min(int(limit), 80))),
        source="pulse_groups",
    )
    return [
        _fact(
            "pulse_groups", row.get("id"), row.get("updated_at") or row.get("created_at"),
            f"/pulse/groups/{row.get('slug')}", kind="group",
            title=row.get("name") or "PulseSoc group", detail=row.get("description") or "",
            data={key: row.get(key) for key in (
                "slug", "category", "group_type", "member_count", "trust_level",
                "featured", "joined", "viewer_role",
            )},
        )
        for row in rows
    ]


def groups_search(user_id: int, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    lowered = str(query or "").strip().lower()
    return [
        item for item in groups_list(user_id, limit=80)
        if lowered in " ".join([
            item.get("title", ""), item.get("detail", ""),
            str(item.get("data", {}).get("category") or ""),
        ]).lower()
    ][:max(1, min(int(limit), 40))]


def events_upcoming(user_id: int, *, limit: int = 25) -> list[dict[str, Any]]:
    del user_id
    rows = _read(
        """SELECT event_id, business_id, title, description, venue, starts_at,
                  ends_at, capacity, currency, status, updated_at, created_at
           FROM business_os_events WHERE status='published'
           ORDER BY CASE WHEN COALESCE(starts_at,'')='' THEN 1 ELSE 0 END,
                    starts_at ASC, created_at DESC LIMIT ?""",
        (max(1, min(int(limit), 50)),),
        source="business_os_events",
    )
    return [
        _fact(
            "business_os_events", row.get("event_id"),
            row.get("starts_at") or row.get("created_at"),
            f"/pulse/events/{row.get('event_id')}", kind="event",
            title=row.get("title") or "PulseSoc event",
            detail=row.get("description") or row.get("venue") or "",
            data={key: row.get(key) for key in (
                "business_id", "venue", "starts_at", "ends_at", "capacity", "currency", "status",
            )},
        )
        for row in rows
    ]


def music_search(user_id: int, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
    del user_id
    from services.music_service import search_tracks

    return [
        _fact(
            "music_service", track.get("id"), _now(),
            f"/pulse/music?track={track.get('id')}", kind="music_track",
            title=track.get("title") or "PulseSoc sound",
            detail=f"{track.get('artist') or 'PulseSoc Studio'} · {track.get('genre') or track.get('mood') or 'approved'}",
            data={key: track.get(key) for key in (
                "id", "artist", "duration_seconds", "license_type", "source",
                "commercial_use_allowed", "remix_edit_allowed", "attribution_required",
                "mood", "genre", "bpm", "is_creator_safe",
            )},
        )
        for track in search_tracks(query=str(query or ""), limit=max(1, min(int(limit), 40)))
    ]


def account_health_summary(user_id: int) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    sources = (
        ("account_health_events", "account_health", "event_type", "severity", "expires_at", "updated_at"),
        ("account_strikes", "account_strike", "policy_category", "severity", "expires_at", "updated_at"),
        ("account_warnings", "account_warning", "policy_category", "status", "NULL AS expires_at", "updated_at"),
        ("account_restrictions", "account_restriction", "restriction_type", "status", "expires_at", "updated_at"),
        ("account_system_events", "account_system", "event_type", "severity", "NULL AS expires_at", "created_at AS updated_at"),
    )
    for table, kind, title_column, detail_column, expiry_column, updated_column in sources:
        for row in _read(
            f"""SELECT id, {title_column} AS title, {detail_column} AS detail,
                       status, public_summary, created_at, {updated_column}, {expiry_column}
                FROM {table} WHERE user_id=?
                  AND COALESCE(status,'open') NOT IN ('resolved','expired','dismissed')
                ORDER BY COALESCE(updated_at,created_at) DESC LIMIT 25""",
            (int(user_id),), source=table,
        ):
            facts.append(_fact(
                table, row.get("id"), row.get("updated_at") or row.get("created_at"),
                "/pulse/account-health", kind=kind, title=row.get("title") or kind.replace("_", " ").title(),
                detail=row.get("public_summary") or row.get("detail") or row.get("status") or "",
                data={key: row.get(key) for key in ("status", "expires_at")},
            ))
    facts.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return {
        "count": len(facts), "items": facts, "authorization_scope": SELF_SCOPE,
        "generated_at": _now(), "native_route": "/pulse/account-health",
    }


def verification_status(user_id: int) -> list[dict[str, Any]]:
    rows = _read(
        """SELECT id, verification_type, status, reviewed_at, created_at
           FROM verification_requests WHERE user_id=?
           ORDER BY COALESCE(reviewed_at,created_at) DESC LIMIT 25""",
        (int(user_id),), source="verification_requests",
    )
    return [
        _fact(
            "verification_requests", row.get("id"),
            row.get("reviewed_at") or row.get("created_at"),
            "/pulse/verification", kind="verification_request",
            title=f"{row.get('verification_type') or 'Account'} verification",
            detail=str(row.get("status") or "pending"),
            data={key: row.get(key) for key in (
                "verification_type", "status", "reviewed_at",
            )},
        )
        for row in rows
    ]


def support_tickets_list(user_id: int, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = _read(
        """SELECT id, issue_type, subject, status, priority, created_at, updated_at
           FROM support_tickets WHERE user_id=?
           ORDER BY COALESCE(updated_at,created_at) DESC LIMIT ?""",
        (int(user_id), max(1, min(int(limit), 50))), source="support_tickets",
    )
    return [
        _fact(
            "support_tickets", row.get("id"), row.get("updated_at") or row.get("created_at"),
            "/pulse/support", kind="support_ticket", title=row.get("subject") or "Support request",
            detail=f"{row.get('status') or 'open'} · {row.get('priority') or 'normal'}",
            data={key: row.get(key) for key in ("issue_type", "status", "priority")},
        )
        for row in rows
    ]


def creator_analytics_summary(user_id: int, *, days: int = 30) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 365)))).isoformat()
    posts = _read(
        """SELECT COUNT(*) AS content_count,
                  COALESCE(AVG(engagement_score),0) AS average_engagement_score
           FROM pulse_posts WHERE user_id=? AND deleted_at IS NULL AND created_at>=?""",
        (int(user_id), since), source="pulse_posts",
    )
    reels = _read(
        """SELECT COUNT(*) AS reel_count, COALESCE(AVG(completion_rate),0) AS average_completion_rate,
                  COALESCE(SUM(replay_count),0) AS replays, COALESCE(SUM(share_count),0) AS shares
           FROM pulse_reels WHERE user_id=? AND status='active' AND created_at>=?""",
        (int(user_id), since), source="pulse_reels",
    )
    statuses = _read(
        """SELECT COUNT(*) AS status_count,
                  (SELECT COUNT(*) FROM pulse_status_views v
                   JOIN pulse_statuses owned ON owned.id=v.status_id
                   WHERE owned.user_id=? AND owned.created_at>=?) AS status_views
           FROM pulse_statuses WHERE user_id=? AND deleted_at IS NULL AND created_at>=?""",
        (int(user_id), since, int(user_id), since), source="pulse_statuses",
    )
    snapshot = {
        **(posts[0] if posts else {}),
        **(reels[0] if reels else {}),
        **(statuses[0] if statuses else {}),
    }
    fact = _fact(
        "creator_content_graph", int(user_id), _now(), "/pulse/creator-studio",
        kind="creator_analytics", title="Creator performance",
        detail=f"{int(snapshot.get('content_count') or 0)} posts · {int(snapshot.get('reel_count') or 0)} Reels · {int(snapshot.get('status_count') or 0)} statuses",
        data=snapshot,
    )
    return {
        "period_start": since, "period_end": _now(), "items": [fact],
        "metrics": snapshot, "authorization_scope": SELF_SCOPE,
        "native_route": "/pulse/creator-studio",
    }


def localization_preferences(user_id: int) -> dict[str, Any]:
    from services.content_translation import get_preference
    from services.pulse_region_preferences import get_preferences

    translation = get_preference(int(user_id))
    region = get_preferences(int(user_id))
    facts = [
        _fact(
            "content_translation", int(user_id), translation.get("updated_at") or _now(),
            "/pulse/settings/language-region", kind="translation_preference",
            title="Translation preferences",
            detail=f"{translation.get('source_language') or 'auto'} → {translation.get('target_language') or 'default'}",
            data=translation,
        ),
        _fact(
            "pulse_region_preferences", int(user_id), region.get("updated_at") or _now(),
            "/pulse/settings/language-region", kind="region_preference",
            title="Language and region",
            detail=" · ".join(str(region.get(key) or "") for key in ("locale", "time_zone", "currency") if region.get(key)),
            data=region,
        ),
    ]
    return {
        "translation": translation,
        "region": region,
        "items": facts,
        "source": "content_translation+pulse_region_preferences",
        "timestamp": _now(), "authorization_scope": SELF_SCOPE,
        "confidence": 1.0, "native_route": "/pulse/settings/language-region",
    }


def presence_privacy_status(user_id: int) -> dict[str, Any]:
    rows = _read(
        """SELECT hide_last_seen, invisible_mode, updated_at
           FROM presence_privacy_settings WHERE user_id=? LIMIT 1""",
        (int(user_id),), source="presence_privacy_settings",
    )
    comm = _read(
        """SELECT presence_privacy, updated_at FROM comm_v2_user_settings
           WHERE user_id=? LIMIT 1""",
        (int(user_id),), source="comm_v2_user_settings",
    )
    state = {
        "hide_last_seen": bool((rows[0] if rows else {}).get("hide_last_seen")),
        "invisible_mode": bool((rows[0] if rows else {}).get("invisible_mode")),
        "presence_privacy": str((comm[0] if comm else {}).get("presence_privacy") or "everyone"),
        "source": "presence_service", "timestamp": str(
            (rows[0] if rows else {}).get("updated_at") or (comm[0] if comm else {}).get("updated_at") or ""
        ),
        "authorization_scope": SELF_SCOPE, "confidence": 1.0,
        "native_route": "/pulse/settings/privacy",
    }
    state["items"] = [_fact(
        "presence_service", int(user_id), state["timestamp"], state["native_route"],
        kind="presence_privacy", title="Presence privacy",
        detail=(
            "Invisible mode"
            if state["invisible_mode"]
            else f"Visible to {state['presence_privacy']}; "
                 f"last seen {'hidden' if state['hide_last_seen'] else 'visible'}"
        ),
        data={key: state[key] for key in ("hide_last_seen", "invisible_mode", "presence_privacy")},
    )]
    return state


__all__ = [
    "activity_daily_summary", "notifications_inbox", "notification_explain",
    "notification_group_summary", "search_global", "search_people", "search_content",
    "search_messages", "search_activity", "settings_inspect", "settings_explain",
    "settings_recommend", "security_sessions", "security_devices",
    "security_activity_summary", "premium_status", "premium_entitlements",
    "marketplace_search", "marketplace_listing_summary", "marketplace_order_status",
    "ads_performance_summary", "live_search", "live_summary", "live_performance",
    "learning_search", "learning_progress", "memory_activity_inspect",
    "groups_list", "groups_search", "events_upcoming", "music_search",
    "account_health_summary", "verification_status", "support_tickets_list",
    "creator_analytics_summary", "localization_preferences",
    "presence_privacy_status",
]
