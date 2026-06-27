"""Backend-managed PulseSoc Network Command Center state.

This module intentionally returns aggregate, owner-scoped network data only.
It never exposes private message bodies, raw push tokens, reporter identities,
or provider secrets to the user dashboard or general admin views.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from services import db as db_service


NETWORK_SECTIONS = (
    {
        "key": "notifications",
        "label": "Notifications Manager",
        "route": "/admin/network-command-center/notifications",
        "description": "Queue, unread/read state, delivery status, preferences, retries, and deep-link health.",
    },
    {
        "key": "messenger",
        "label": "Messenger Health",
        "route": "/admin/network-command-center/messenger",
        "description": "Conversation delivery, unread counts, receipts, media health, reports, and safe diagnostics.",
    },
    {
        "key": "friends",
        "label": "Friends Manager",
        "route": "/admin/network-command-center/friends",
        "description": "Friend requests, accepted edges, cancelled requests, abuse protection, and audit coverage.",
    },
    {
        "key": "followers",
        "label": "Followers Manager",
        "route": "/admin/network-command-center/followers",
        "description": "Follower/following edges, pending requests, blocked relationships, and follow spike detection.",
    },
    {
        "key": "groups",
        "label": "Groups Manager",
        "route": "/admin/network-command-center/groups",
        "description": "Memberships, roles, join requests, group reports, bans, mutes, and moderation health.",
    },
    {
        "key": "blocks-mutes",
        "label": "Blocks & Mutes",
        "route": "/admin/network-command-center/blocks-mutes",
        "description": "User blocks, user mutes, conversation mutes, group mutes, and enforcement status.",
    },
    {
        "key": "bans",
        "label": "Bans",
        "route": "/admin/network-command-center/bans",
        "description": "Temporary bans, permanent bans, group bans, restrictions, and appeal-aware status.",
    },
    {
        "key": "push-delivery",
        "label": "Push Delivery",
        "route": "/admin/network-command-center/push-delivery",
        "description": "Device registry, platform health, stale tokens, provider responses, and retry status.",
    },
    {
        "key": "message-health",
        "label": "Message Health",
        "route": "/admin/network-command-center/message-health",
        "description": "Realtime delivery, read/delivery receipts, failed messages, attachments, and voice notes.",
    },
    {
        "key": "audit",
        "label": "Network Audit Logs",
        "route": "/admin/network-command-center/audit",
        "description": "Network actions, admin actions, notification retries, blocks, mutes, bans, and moderation audit.",
    },
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
                (table,),
            )
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _columns(cur: Any, table: str) -> set[str]:
    if not _table_exists(cur, table):
        return set()
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
                (table,),
            )
            return {str(_row_value(row, "column_name", 0) or "") for row in cur.fetchall()}
        cur.execute(f"PRAGMA table_info({table})")
        return {str(_row_value(row, "name", 1) or "") for row in cur.fetchall()}
    except Exception:
        return set()


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _rows(cur: Any, table: str, columns: str, where: str = "1=1", params: tuple[Any, ...] = (), limit: int = 20) -> list[dict[str, Any]]:
    if not _table_exists(cur, table):
        return []
    try:
        cur.execute(f"SELECT {columns} FROM {table} WHERE {where} LIMIT {max(1, min(int(limit), 100))}", params)
        return [dict(row) for row in cur.fetchall()]
    except Exception:
        return []


def _sum_counts(*values: int) -> int:
    return sum(_safe_int(value) for value in values)


def _state_for_count(count: int, warning_threshold: int = 1) -> str:
    return "WARNING" if _safe_int(count) >= warning_threshold else "ON"


def build_network_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    """Return sanitized owner-scoped dashboard state for one account."""
    cur = conn.cursor()
    user_id = _safe_int(user.get("user_id"), 0)
    now = datetime.utcnow().isoformat(timespec="seconds")
    since_day = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec="seconds")

    unread_notifications = _sum_counts(
        _count(cur, "notifications", "user_id=? AND COALESCE(read_at,'')=''", (user_id,)),
        _count(cur, "pulse_notifications", "user_id=? AND (COALESCE(is_read,0)=0 OR COALESCE(read_at,'')='')", (user_id,)),
        _count(cur, "command_center_notification_events", "recipient_id=? AND COALESCE(read_at,'')=''", (user_id,)),
    )
    visible_notifications = _sum_counts(
        _count(cur, "notifications", "user_id=?", (user_id,)),
        _count(cur, "pulse_notifications", "user_id=?", (user_id,)),
        _count(cur, "command_center_notification_events", "recipient_id=?", (user_id,)),
    )
    unread_messages = _sum_counts(
        _count(cur, "conversation_participants", "user_id=? AND COALESCE(unread_count,0)>0", (user_id,)),
        _count(cur, "pulse_conversation_participants", "user_id=? AND COALESCE(unread_count,0)>0", (user_id,)),
    )
    active_conversations = _sum_counts(
        _count(cur, "conversation_participants", "user_id=?", (user_id,)),
        _count(cur, "pulse_conversation_participants", "user_id=?", (user_id,)),
    )
    pending_incoming = _sum_counts(
        _count(cur, "friend_requests", "recipient_id=? AND status='pending'", (user_id,)),
        _count(cur, "pulse_friend_requests", "recipient_id=? AND status='pending'", (user_id,)),
    )
    pending_outgoing = _sum_counts(
        _count(cur, "friend_requests", "requester_id=? AND status='pending'", (user_id,)),
        _count(cur, "pulse_friend_requests", "requester_id=? AND status='pending'", (user_id,)),
    )
    friends = _sum_counts(
        _count(cur, "friendships", "(user_id=? OR friend_id=?) AND status='accepted'", (user_id, user_id)),
        _count(cur, "pulse_friendships", "(user_id=? OR friend_id=?) AND status='accepted'", (user_id, user_id)),
    )
    followers = _sum_counts(
        _count(cur, "pulse_follows", "followed_user_id=? AND status IN ('active','accepted')", (user_id,)),
        _count(cur, "friendships", "friend_id=? AND status='accepted'", (user_id,)),
    )
    following = _sum_counts(
        _count(cur, "pulse_follows", "follower_user_id=? AND status IN ('active','accepted')", (user_id,)),
        _count(cur, "friendships", "user_id=? AND status='accepted'", (user_id,)),
    )
    group_memberships = _sum_counts(
        _count(cur, "pulse_group_members", "user_id=? AND status IN ('active','member','approved')", (user_id,)),
        _count(cur, "group_members", "user_id=? AND status IN ('active','member','approved')", (user_id,)),
    )
    owned_groups = _sum_counts(
        _count(cur, "pulse_groups", "owner_user_id=?", (user_id,)),
        _count(cur, "groups", "owner_user_id=?", (user_id,)),
    )
    muted_conversations = _sum_counts(
        _count(cur, "conversation_participants", "user_id=? AND COALESCE(is_muted,0)=1", (user_id,)),
        _count(cur, "pulse_conversation_participants", "user_id=? AND COALESCE(is_muted,0)=1", (user_id,)),
    )
    blocked_users = _sum_counts(
        _count(cur, "user_blocks", "blocker_user_id=?", (user_id,)),
        _count(cur, "pulse_user_blocks", "blocker_user_id=?", (user_id,)),
    )
    push_devices = _sum_counts(
        _count(cur, "user_device_tokens", "user_id=? AND COALESCE(active,1)=1", (user_id,)),
        _count(cur, "pulse_push_devices", "user_id=? AND COALESCE(active,1)=1", (user_id,)),
    )
    failed_pushes = _sum_counts(
        _count(cur, "notification_delivery_logs", "user_id=? AND channel='push' AND status IN ('failed','not_configured','error')", (user_id,)),
        _count(cur, "push_delivery_attempts", "user_id=? AND status IN ('failed','not_configured','error')", (user_id,)),
    )
    notifications_today = _sum_counts(
        _count(cur, "notifications", "user_id=? AND created_at>=?", (user_id, since_day)),
        _count(cur, "pulse_notifications", "user_id=? AND created_at>=?", (user_id, since_day)),
    )

    delivery_health = 100 if failed_pushes == 0 else max(25, 100 - min(75, failed_pushes * 5))
    message_health = 100 if active_conversations or unread_messages == 0 else 95
    network_trust = max(50, min(100, int((delivery_health + message_health + 100) / 3)))

    cards = [
        {
            "key": "notifications",
            "label": "Notifications",
            "state": _state_for_count(failed_pushes),
            "count": unread_notifications,
            "route": "/dashboard/network/notifications",
            "detail": f"{visible_notifications} visible records, {notifications_today} created today.",
            "action": "Open Notification Center",
        },
        {
            "key": "messages",
            "label": "Messages",
            "state": "ON",
            "count": unread_messages,
            "route": "/dashboard/network/messages",
            "detail": f"{active_conversations} conversations. Message bodies stay private.",
            "action": "Open Messenger",
        },
        {
            "key": "friends",
            "label": "Friends",
            "state": _state_for_count(pending_incoming),
            "count": pending_incoming,
            "route": "/dashboard/network/friends",
            "detail": f"{friends} friends, {pending_outgoing} outgoing requests.",
            "action": "Manage Friends",
        },
        {
            "key": "followers",
            "label": "Followers / Following",
            "state": "ON",
            "count": followers,
            "route": "/dashboard/network/followers",
            "detail": f"{followers} followers, {following} following.",
            "action": "Review Network",
        },
        {
            "key": "groups",
            "label": "Groups",
            "state": "ON",
            "count": group_memberships,
            "route": "/dashboard/network/groups",
            "detail": f"{group_memberships} joined, {owned_groups} owned.",
            "action": "Open Groups",
        },
    ]

    return {
        "ok": True,
        "generated_at": now,
        "user_id": user_id,
        "metrics": {
            "unread_notifications": unread_notifications,
            "unread_messages": unread_messages,
            "pending_friend_requests": pending_incoming,
            "followers": followers,
            "following": following,
            "groups": group_memberships,
            "muted_conversations": muted_conversations,
            "blocked_users": blocked_users,
            "push_devices": push_devices,
            "failed_pushes": failed_pushes,
            "delivery_health_score": delivery_health,
            "message_health_score": message_health,
            "network_trust_score": network_trust,
        },
        "cards": cards,
        "notifications": {
            "center_route": "/pulse/notifications",
            "settings_route": "/pulse/settings/notifications",
            "unread": unread_notifications,
            "visible": visible_notifications,
            "failed_pushes": failed_pushes,
        },
        "messages": {
            "route": "/pulse/messages",
            "unread": unread_messages,
            "conversations": active_conversations,
            "muted_conversations": muted_conversations,
            "privacy": "Message bodies are not exposed in dashboard diagnostics.",
        },
        "friends": {
            "route": "/pulse/friends",
            "pending_incoming": pending_incoming,
            "pending_outgoing": pending_outgoing,
            "friends": friends,
        },
        "followers": {
            "route": "/pulse/friends",
            "following_route": "/pulse/following",
            "followers": followers,
            "following": following,
        },
        "groups": {
            "route": "/pulse/groups",
            "create_route": "/pulse/groups/create",
            "joined": group_memberships,
            "owned": owned_groups,
        },
        "privacy": {
            "message_body_redacted": True,
            "raw_push_tokens_redacted": True,
            "reporter_identity_hidden": True,
            "device_secrets_hidden": True,
        },
    }


def build_admin_network_state(conn: Any) -> dict[str, Any]:
    """Return aggregate admin state without raw private content or secrets."""
    cur = conn.cursor()
    failed_pushes = _sum_counts(
        _count(cur, "notification_delivery_logs", "channel='push' AND status IN ('failed','not_configured','error')"),
        _count(cur, "push_delivery_attempts", "status IN ('failed','not_configured','error')"),
    )
    active_devices = _sum_counts(
        _count(cur, "user_device_tokens", "COALESCE(active,1)=1"),
        _count(cur, "pulse_push_devices", "COALESCE(active,1)=1"),
    )
    unread_notifications = _sum_counts(
        _count(cur, "notifications", "COALESCE(read_at,'')=''"),
        _count(cur, "pulse_notifications", "COALESCE(is_read,0)=0 OR COALESCE(read_at,'')=''"),
        _count(cur, "command_center_notification_events", "COALESCE(read_at,'')=''"),
    )
    unread_messages = _sum_counts(
        _count(cur, "conversation_participants", "COALESCE(unread_count,0)>0"),
        _count(cur, "pulse_conversation_participants", "COALESCE(unread_count,0)>0"),
    )
    reported_chats = _sum_counts(
        _count(cur, "chat_reports"),
        _count(cur, "pulse_chat_reports"),
    )
    pending_friend_requests = _sum_counts(
        _count(cur, "friend_requests", "status='pending'"),
        _count(cur, "pulse_friend_requests", "status='pending'"),
    )
    accepted_friendships = _sum_counts(
        _count(cur, "friendships", "status='accepted'"),
        _count(cur, "pulse_friendships", "status='accepted'"),
    )
    follows = _count(cur, "pulse_follows", "status IN ('active','accepted')")
    active_groups = _sum_counts(
        _count(cur, "pulse_groups", "COALESCE(status,'active') IN ('active','approved','public')"),
        _count(cur, "groups", "COALESCE(status,'active') IN ('active','approved','public')"),
    )
    group_reports = _sum_counts(
        _count(cur, "group_reports"),
        _count(cur, "pulse_group_reports"),
    )
    blocks = _sum_counts(
        _count(cur, "user_blocks"),
        _count(cur, "pulse_user_blocks"),
    )
    mutes = _sum_counts(
        _count(cur, "user_mutes"),
        _count(cur, "pulse_user_mutes"),
        _count(cur, "conversation_participants", "COALESCE(is_muted,0)=1"),
        _count(cur, "pulse_conversation_participants", "COALESCE(is_muted,0)=1"),
    )
    bans = _sum_counts(
        _count(cur, "account_restrictions", "restriction_type LIKE '%ban%' OR status='banned'"),
        _count(cur, "group_bans"),
        _count(cur, "pulse_group_bans"),
    )
    failed_messages = _sum_counts(
        _count(cur, "message_delivery_attempts", "status IN ('failed','error')"),
        _count(cur, "pulse_message_delivery_attempts", "status IN ('failed','error')"),
    )
    delivery_health = 100 if failed_pushes == 0 else max(15, 100 - min(85, failed_pushes))
    message_health = 100 if failed_messages == 0 else max(20, 100 - min(80, failed_messages * 2))
    trust_score = max(40, min(100, int((delivery_health + message_health + 100 - min(40, group_reports + reported_chats)) / 3)))
    metrics = {
        "unread_notifications": unread_notifications,
        "queued_notifications": _sum_counts(_count(cur, "notification_delivery_logs", "status='queued'"), _count(cur, "command_center_notification_events", "status='queued'")),
        "failed_pushes": failed_pushes,
        "active_conversations": _sum_counts(_count(cur, "conversation_participants"), _count(cur, "pulse_conversation_participants")),
        "failed_messages": failed_messages,
        "reported_chats": reported_chats,
        "pending_friend_requests": pending_friend_requests,
        "followers_gained": follows,
        "active_groups": active_groups,
        "group_reports": group_reports,
        "muted_users": mutes,
        "blocked_users": blocks,
        "banned_users": bans,
        "registered_devices": active_devices,
        "delivery_health_score": delivery_health,
        "message_health_score": message_health,
        "network_trust_score": trust_score,
    }
    sections = []
    for section in NETWORK_SECTIONS:
        value = {
            "notifications": unread_notifications,
            "messenger": unread_messages,
            "friends": pending_friend_requests,
            "followers": follows,
            "groups": active_groups,
            "blocks-mutes": blocks + mutes,
            "bans": bans,
            "push-delivery": failed_pushes,
            "message-health": failed_messages,
            "audit": _sum_counts(_count(cur, "admin_audit_logs"), _count(cur, "admin_activity_logs")),
        }.get(section["key"], 0)
        sections.append({**section, "count": value, "state": "WARNING" if section["key"] in {"push-delivery", "message-health"} and value else "ON"})
    return {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "metrics": metrics,
        "sections": sections,
        "privacy": {
            "message_body_redacted": True,
            "raw_push_tokens_redacted": True,
            "reporter_identity_hidden": True,
            "device_secrets_hidden": True,
            "admin_sensitive_access_audited": True,
        },
    }

