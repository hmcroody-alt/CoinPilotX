"""Backend-managed PulseSoc Network Command Center state.

This module intentionally returns aggregate, owner-scoped network data only.
It never exposes private message bodies, raw device registrations, reporter identities,
or provider credentials to the user dashboard or general admin views.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from services import db as db_service


STATE_LABELS = {
    "READY",
    "ACTION REQUIRED",
    "REVIEW",
    "WARNING",
    "SYNCING",
    "OFFLINE",
    "LIMITED",
    "PREMIUM",
    "ADMIN",
    "BETA",
    "COMING SOON",
    "PRODUCTION READY",
}


NETWORK_SUBSYSTEMS = (
    {
        "key": "notifications",
        "label": "Notifications",
        "admin_label": "Notifications Operating System",
        "route": "/admin/network-command-center/notifications",
        "user_route": "/dashboard/network/notifications",
        "action": "Manage Notifications",
        "description": "Queue, unread/read state, delivery status, preferences, retries, and deep-link health.",
        "intelligence": "Prioritizes alerts, separates Messenger from general notifications, and detects delivery fatigue.",
        "automation": "Retries failed deliveries, applies mute rules and quiet hours, and prevents duplicate alerts.",
        "protection": "Deep links, provider responses, and delivery errors are logged without exposing credentials or raw device data.",
        "recovery": "Retry queue, failed-delivery triage, and mark-read recovery keep the alert layer consistent.",
    },
    {
        "key": "messenger",
        "label": "Messages",
        "admin_label": "Messenger Operating System",
        "route": "/admin/network-command-center/messenger",
        "user_route": "/dashboard/network/messages",
        "aliases": ("messages",),
        "action": "Open Messenger",
        "description": "Conversation delivery, unread counts, receipts, media health, reports, and safe diagnostics.",
        "intelligence": "Ranks unread conversations, watches delivery health, and keeps active chats realtime-first.",
        "automation": "Syncs unread counts, delivery receipts, presence, typing, media state, and push decisions.",
        "protection": "Private message bodies stay hidden from dashboard and general admin diagnostics.",
        "recovery": "Conversation recovery and failed delivery retry hooks preserve message continuity where permitted.",
    },
    {
        "key": "friends",
        "label": "Friends",
        "admin_label": "Friends Operating System",
        "route": "/admin/network-command-center/friends",
        "user_route": "/dashboard/network/friends",
        "action": "Manage Friends",
        "description": "Friend requests, accepted edges, cancelled requests, abuse protection, and audit coverage.",
        "intelligence": "Surfaces pending requests, mutual connection context, and abuse-safe relationship signals.",
        "automation": "Keeps request state, accepted edges, ignored requests, and audit events synchronized.",
        "protection": "Blocks, rate limits, and privacy rules prevent unwanted relationship actions.",
        "recovery": "Request timelines and connection history make safe recovery possible after mistakes.",
    },
    {
        "key": "followers",
        "label": "Followers / Following",
        "admin_label": "Audience Operating System",
        "route": "/admin/network-command-center/followers",
        "user_route": "/dashboard/network/followers",
        "action": "View Audience",
        "description": "Follower/following edges, pending requests, blocked relationships, and follow spike detection.",
        "intelligence": "Tracks audience quality, recent followers, growth, and privacy-safe public network state.",
        "automation": "Applies private profile, remove follower, blocked relationship, and spike-detection rules.",
        "protection": "Follower privacy and blocked-user boundaries are enforced server-side.",
        "recovery": "Lost follower and broken edge diagnostics feed the connection recovery layer.",
    },
    {
        "key": "groups",
        "label": "Groups",
        "admin_label": "Communities Operating System",
        "route": "/admin/network-command-center/groups",
        "user_route": "/dashboard/network/groups",
        "action": "Open Communities",
        "description": "Memberships, roles, join requests, group reports, bans, mutes, and moderation health.",
        "intelligence": "Summarizes community health, pending invites, moderator messages, and group activity.",
        "automation": "Routes join requests, member roles, invites, mutes, bans, and reports to the right queues.",
        "protection": "Public/private/invite-only permissions are enforced before access or action.",
        "recovery": "Group audit logs and member controls make community state recoverable.",
    },
    {
        "key": "status_activity",
        "label": "Status Activity",
        "admin_label": "Status Activity Operating System",
        "route": "/admin/network-command-center/status-activity",
        "user_route": "/dashboard/network/status-activity",
        "action": "View Status Activity",
        "description": "Story views, completion, replies, shares, reach, and status recommendations.",
        "intelligence": "Reads status views, reaction analytics, completion rate, private replies, and reach.",
        "automation": "Feeds viewer analytics and recommendations without leaking private status visibility.",
        "protection": "Status privacy and viewer identity boundaries stay server-side.",
        "recovery": "Status timelines help recover insight after expiration where telemetry is allowed.",
    },
    {
        "key": "community_activity",
        "label": "Community Activity",
        "admin_label": "Community Activity Operating System",
        "route": "/admin/network-command-center/community-activity",
        "user_route": "/dashboard/network/community-activity",
        "action": "Explore Community Activity",
        "description": "Recent discussions, popular posts, momentum, trending communities, and moderation signals.",
        "intelligence": "Connects public activity, trust, and discovery signals into safe recommendations.",
        "automation": "Updates community momentum when posts, comments, reports, or groups change.",
        "protection": "Only public or user-permitted community activity is summarized.",
        "recovery": "Moderation signals and activity audit logs explain why recommendations changed.",
    },
    {
        "key": "network_health",
        "label": "Network Health",
        "admin_label": "Network Health Center",
        "route": "/admin/network-command-center/network-health",
        "user_route": "/dashboard/network/network-health",
        "action": "Review Network Health",
        "description": "Connection, relationship, delivery, audience, community, communication, and trust health.",
        "intelligence": "Produces one network health score and the next best action for the user.",
        "automation": "Recomputes health when delivery, relationship, notification, or community events change.",
        "protection": "Health uses aggregate signals only and never exposes private messages or reporter identity.",
        "recovery": "Health recommendations point to recovery modules before problems become user-visible.",
    },
    {
        "key": "delivery_intelligence",
        "label": "Delivery Intelligence",
        "admin_label": "Delivery Command Center",
        "route": "/admin/network-command-center/delivery-intelligence",
        "user_route": "/dashboard/network/delivery-intelligence",
        "action": "Manage Delivery",
        "description": "Push, email, SMS, Telegram, socket, realtime, retry, latency, and regional delivery health.",
        "intelligence": "Explains whether communication is flowing, delayed, retrying, or degraded.",
        "automation": "Coordinates retries, failure reasons, and provider-health events.",
        "protection": "Provider responses are summarized safely without credentials, raw device data, or private URLs.",
        "recovery": "Retry queues and provider diagnostics keep failed communication recoverable.",
    },
    {
        "key": "notification_intelligence",
        "label": "Notification Intelligence",
        "admin_label": "Notification Intelligence Center",
        "route": "/admin/network-command-center/notification-intelligence",
        "user_route": "/dashboard/network/notification-intelligence",
        "action": "Optimize Notifications",
        "description": "Notification fatigue, priority learning, delivery timing, quiet hours, ignored alerts, and high-value alerts.",
        "intelligence": "Learns which alerts are useful without exposing private content.",
        "automation": "Recommends quiet hours, category preferences, and priority routing adjustments.",
        "protection": "Notification learning is privacy-safe and user-scoped.",
        "recovery": "Ignored and failed alert histories make notification tuning explainable.",
    },
    {
        "key": "relationship_intelligence",
        "label": "Relationship Intelligence",
        "admin_label": "Relationship Command Center",
        "route": "/admin/network-command-center/relationship-intelligence",
        "user_route": "/dashboard/network/relationship-intelligence",
        "action": "Analyze Relationships",
        "description": "Strong connections, dormant relationships, frequently contacted people, reconnect suggestions, and trust score.",
        "intelligence": "Maps relationship strength and recommends reconnect actions from safe metadata.",
        "automation": "Updates when friends, follows, messages, blocks, or mutes change.",
        "protection": "No private message bodies are used in dashboard output.",
        "recovery": "Relationship timelines explain changes and support safe reconnection.",
    },
    {
        "key": "connection_analytics",
        "label": "Connection Analytics",
        "admin_label": "Connection Analytics Center",
        "route": "/admin/network-command-center/connection-analytics",
        "user_route": "/dashboard/network/connection-analytics",
        "action": "View Analytics",
        "description": "Connection growth, retention, acceptance rate, friend conversion, follower conversion, and audience funnel.",
        "intelligence": "Turns connection counts into growth and retention signals.",
        "automation": "Refreshes analytics when requests, follows, removals, and blocks happen.",
        "protection": "Analytics are owner-scoped and aggregate-only.",
        "recovery": "Funnel gaps route users to connection recovery or friend tools.",
    },
    {
        "key": "audience_mapping",
        "label": "Audience Mapping",
        "admin_label": "Audience Intelligence Center",
        "route": "/admin/network-command-center/audience-mapping",
        "user_route": "/dashboard/network/audience-mapping",
        "action": "Explore Audience",
        "description": "Interest clusters, creator communities, audience overlap, expansion, and distribution.",
        "intelligence": "Finds safe audience clusters and expansion paths.",
        "automation": "Feeds creator reach and growth signals as audience data changes.",
        "protection": "No private location, exact geolocation, or private user data is exposed.",
        "recovery": "Audience gaps become recommended actions rather than silent failures.",
    },
    {
        "key": "growth_signals",
        "label": "Growth Signals",
        "admin_label": "Growth Intelligence Center",
        "route": "/admin/network-command-center/growth-signals",
        "user_route": "/dashboard/network/growth-signals",
        "action": "View Growth Signals",
        "description": "Growth opportunities, recommended actions, audience momentum, creator momentum, and connection opportunities.",
        "intelligence": "Predicts useful growth actions from public and owner-permitted signals.",
        "automation": "Turns audience, status, community, and creator reach changes into recommendations.",
        "protection": "Recommendations do not reveal private users or hidden content.",
        "recovery": "If growth slows, the system routes to recovery and reconnect actions.",
    },
    {
        "key": "delivery_matrix",
        "label": "Pulse Delivery Matrix",
        "admin_label": "Pulse Delivery Matrix",
        "route": "/admin/network-command-center/delivery-matrix",
        "user_route": "/dashboard/network/delivery-matrix",
        "action": "Review Delivery Matrix",
        "description": "Notifications/sec, messages/sec, push/email/SMS/Telegram success, queue size, retries, failures, and worker health.",
        "intelligence": "Acts as the live delivery brain for communication health.",
        "automation": "Aggregates queues, retries, provider health, and worker state into one matrix.",
        "protection": "Only safe operational metrics are shown.",
        "recovery": "Failures point to retry queues and provider diagnostics.",
    },
    {
        "key": "network_security",
        "label": "Network Security",
        "admin_label": "Network Security Center",
        "route": "/admin/network-command-center/network-security",
        "user_route": "/dashboard/network/network-security",
        "action": "Review Network Security",
        "description": "Spam, scam, abuse, muted users, blocked users, hidden requests, privacy controls, and trust signals.",
        "intelligence": "Connects blocks, mutes, reports, spam, scam, and abuse signals safely.",
        "automation": "Routes high-risk network events to review without auto-banning users.",
        "protection": "Blocks, mutes, bans, and privacy controls are enforced server-side.",
        "recovery": "Hidden requests and blocked relationships stay auditable and reversible when allowed.",
    },
    {
        "key": "community_intelligence",
        "label": "Community Intelligence",
        "admin_label": "Community Intelligence Center",
        "route": "/admin/network-command-center/community-intelligence",
        "user_route": "/dashboard/network/community-intelligence",
        "action": "Review Community Intelligence",
        "description": "Community health, spam level, moderator health, growth, engagement, and suggested improvements.",
        "intelligence": "Explains whether communities are healthy, growing, or requiring moderation.",
        "automation": "Feeds group moderation, recommendations, and activity analytics.",
        "protection": "Moderator-only details stay hidden from normal users.",
        "recovery": "Community health actions route to group tools and moderation queues.",
    },
    {
        "key": "creator_reach",
        "label": "Creator Reach",
        "admin_label": "Creator Reach Center",
        "route": "/admin/network-command-center/creator-reach",
        "user_route": "/dashboard/network/creator-reach",
        "action": "View Creator Reach",
        "description": "Reach, shares, audience spread, virality, engagement, and network expansion.",
        "intelligence": "Connects creator content performance with network growth.",
        "automation": "Updates reach when shares, follows, status views, and community activity change.",
        "protection": "Only owner-safe creator reach data is shown.",
        "recovery": "Low reach produces recommended actions, not fake success states.",
    },
    {
        "key": "connection_recovery",
        "label": "Connection Recovery",
        "admin_label": "Connection Recovery Center",
        "route": "/admin/network-command-center/connection-recovery",
        "user_route": "/dashboard/network/connection-recovery",
        "action": "Recover Connections",
        "description": "Failed requests, broken connections, lost followers, relationship recovery, and recovery recommendations.",
        "intelligence": "Detects dropped relationships and safe recovery opportunities.",
        "automation": "Creates recommendations when requests fail, followers drop, or connections go dormant.",
        "protection": "Recovery honors blocks, privacy, and mute rules before suggesting action.",
        "recovery": "This subsystem is the recovery lane for the entire network layer.",
    },
    {
        "key": "blocks-mutes",
        "label": "Blocks & Mutes",
        "admin_label": "Blocks & Mutes",
        "route": "/admin/network-command-center/blocks-mutes",
        "user_route": "/dashboard/network/network-security",
        "action": "Review Network Security",
        "description": "User blocks, user mutes, conversation mutes, group mutes, and enforcement status.",
        "intelligence": "Explains which protections are currently active.",
        "automation": "Synchronizes blocks and mutes across messages, follows, groups, and notifications.",
        "protection": "Blocked users cannot message, follow, or interact where blocked.",
        "recovery": "Unblock/unmute actions remain auditable and reversible where allowed.",
    },
    {
        "key": "bans",
        "label": "Bans",
        "admin_label": "Bans",
        "route": "/admin/network-command-center/bans",
        "user_route": "/dashboard/network/network-security",
        "action": "Review Restrictions",
        "description": "Temporary bans, permanent bans, group bans, restrictions, and appeal-aware status.",
        "intelligence": "Separates user-visible restrictions from admin-only moderation details.",
        "automation": "Keeps restrictions, bans, appeals, and audit logs synchronized.",
        "protection": "Bans are enforced server-side and never only in UI.",
        "recovery": "Appeal-aware state supports review and restoration where policy permits.",
    },
    {
        "key": "push-delivery",
        "label": "Push Delivery",
        "admin_label": "Push Delivery",
        "route": "/admin/network-command-center/push-delivery",
        "user_route": "/dashboard/network/delivery-intelligence",
        "action": "Manage Delivery",
        "description": "Device registry, platform health, stale devices, provider responses, and retry status.",
        "intelligence": "Summarizes push status without exposing device private data.",
        "automation": "Deactivates invalid devices and routes failed attempts to retry.",
        "protection": "Raw push registrations never render in UI.",
        "recovery": "Stale devices and provider errors point to repair actions.",
    },
    {
        "key": "message-health",
        "label": "Message Health",
        "admin_label": "Message Health",
        "route": "/admin/network-command-center/message-health",
        "user_route": "/dashboard/network/messages",
        "action": "Review Message Health",
        "description": "Realtime delivery, read/delivery receipts, failed messages, attachments, and voice notes.",
        "intelligence": "Measures message latency, delivery receipts, media health, and failed delivery state.",
        "automation": "Keeps realtime, fallback polling, unread counts, and delivery queues aligned.",
        "protection": "Private content remains redacted.",
        "recovery": "Failed delivery state routes to retry and diagnostics.",
    },
    {
        "key": "audit",
        "label": "Network Audit Logs",
        "admin_label": "Network Audit Logs",
        "route": "/admin/network-command-center/audit",
        "user_route": "/dashboard/network/network-health",
        "action": "Review Audit Trail",
        "description": "Network actions, admin actions, notification retries, blocks, mutes, bans, and moderation audit.",
        "intelligence": "Provides explainability for network state changes.",
        "automation": "Records sensitive network actions for review.",
        "protection": "Audit views redact private bodies, reporter identities, and credentials.",
        "recovery": "Audit history supports safe recovery and investigation.",
    },
)

NETWORK_SECTIONS = NETWORK_SUBSYSTEMS
NETWORK_SUBSYSTEM_MAP = {item["key"]: item for item in NETWORK_SUBSYSTEMS}
for _item in NETWORK_SUBSYSTEMS:
    for _alias in _item.get("aliases", ()):
        NETWORK_SUBSYSTEM_MAP[str(_alias)] = _item


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
    return "WARNING" if _safe_int(count) >= warning_threshold else "READY"


def _health_state(score: int) -> str:
    score = _safe_int(score)
    if score >= 92:
        return "PRODUCTION READY"
    if score >= 75:
        return "READY"
    if score >= 55:
        return "ACTION REQUIRED"
    return "WARNING"


def _subsystem_payload(base: dict[str, Any], count: int, state: str, detail: str, recommendations: list[str] | None = None) -> dict[str, Any]:
    safe_state = state if state in STATE_LABELS else "READY"
    return {
        "key": base.get("key"),
        "label": base.get("label"),
        "admin_label": base.get("admin_label") or base.get("label"),
        "state": safe_state,
        "count": _safe_int(count),
        "route": base.get("user_route") or "/dashboard/network",
        "admin_route": base.get("route") or "/admin/network-command-center",
        "action": base.get("action") or "Review Network Health",
        "cta_label": base.get("action") or "Review Network Health",
        "detail": detail,
        "description": base.get("description") or "",
        "intelligence": base.get("intelligence") or "",
        "automation": base.get("automation") or "",
        "protection": base.get("protection") or "",
        "recovery": base.get("recovery") or "",
        "monitors": base.get("description") or "",
        "audited": True,
        "backend_managed": True,
        "recommendations": recommendations or [],
    }


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
    status_views = _sum_counts(
        _count(cur, "pulse_status_views", "owner_user_id=?", (user_id,)),
        _count(cur, "status_views", "owner_user_id=?", (user_id,)),
    )
    status_replies = _sum_counts(
        _count(cur, "pulse_status_replies", "owner_user_id=?", (user_id,)),
        _count(cur, "status_replies", "owner_user_id=?", (user_id,)),
    )
    public_posts = _sum_counts(
        _count(cur, "posts", "user_id=? AND COALESCE(visibility,'public')='public'", (user_id,)),
        _count(cur, "pulse_posts", "user_id=? AND COALESCE(visibility,'public')='public'", (user_id,)),
    )
    public_comments = _sum_counts(
        _count(cur, "comments", "user_id=?", (user_id,)),
        _count(cur, "pulse_comments", "user_id=?", (user_id,)),
    )
    shares = _sum_counts(
        _count(cur, "post_shares", "user_id=?", (user_id,)),
        _count(cur, "pulse_post_shares", "user_id=?", (user_id,)),
    )

    delivery_health = 100 if failed_pushes == 0 else max(25, 100 - min(75, failed_pushes * 5))
    message_health = 100 if active_conversations or unread_messages == 0 else 95
    audience_health = max(60, min(100, 80 + min(12, followers) - min(10, blocked_users)))
    community_health = max(60, min(100, 82 + min(8, group_memberships) - min(10, blocked_users)))
    relationship_score = max(55, min(100, 76 + min(14, friends + pending_incoming) - min(12, muted_conversations)))
    network_trust = max(50, min(100, int((delivery_health + message_health + relationship_score + audience_health + community_health) / 5)))
    risk_alerts = failed_pushes + blocked_users + muted_conversations

    recommendations = []
    if pending_incoming:
        recommendations.append("Review pending friend requests before they go stale.")
    if failed_pushes:
        recommendations.append("Review notification delivery so important alerts keep arriving.")
    if unread_messages:
        recommendations.append("Open Messenger to clear priority unread conversations.")
    if not followers and not friends:
        recommendations.append("Invite trusted people or follow creators to strengthen your network.")
    if not recommendations:
        recommendations.append("Your network is stable. Keep communication settings current.")

    count_map = {
        "notifications": unread_notifications,
        "messenger": unread_messages,
        "friends": pending_incoming,
        "followers": followers,
        "groups": group_memberships,
        "status_activity": status_views + status_replies,
        "community_activity": public_posts + public_comments,
        "network_health": network_trust,
        "delivery_intelligence": failed_pushes,
        "notification_intelligence": notifications_today,
        "relationship_intelligence": friends,
        "connection_analytics": followers + following + friends,
        "audience_mapping": followers,
        "growth_signals": public_posts + shares + followers,
        "delivery_matrix": failed_pushes + unread_messages + unread_notifications,
        "network_security": blocked_users + muted_conversations,
        "community_intelligence": group_memberships,
        "creator_reach": shares + status_views + public_posts,
        "connection_recovery": pending_outgoing + blocked_users,
        "blocks-mutes": blocked_users + muted_conversations,
        "bans": 0,
        "push-delivery": failed_pushes,
        "message-health": unread_messages,
        "audit": risk_alerts,
    }
    detail_map = {
        "notifications": f"{visible_notifications} visible records, {notifications_today} created today.",
        "messenger": f"{active_conversations} conversations. Message bodies stay private.",
        "friends": f"{friends} friends, {pending_outgoing} outgoing requests.",
        "followers": f"{followers} followers, {following} following.",
        "groups": f"{group_memberships} joined, {owned_groups} owned.",
        "status_activity": f"{status_views} views and {status_replies} replies across owner-visible statuses.",
        "community_activity": f"{public_posts} public posts and {public_comments} community replies.",
        "network_health": f"{network_trust}% combined network trust across delivery, audience, relationships, and communities.",
        "delivery_intelligence": f"{failed_pushes} push failures, {push_devices} active device registrations.",
        "notification_intelligence": f"{notifications_today} alerts created today with {unread_notifications} unread.",
        "relationship_intelligence": f"{friends} accepted friends and {muted_conversations} muted conversations.",
        "connection_analytics": f"{followers} followers, {following} following, {friends} friend edges.",
        "audience_mapping": f"{followers} followers available for privacy-safe audience clustering.",
        "growth_signals": f"{public_posts} public posts, {shares} shares, {followers} followers.",
        "delivery_matrix": f"{unread_notifications} unread alerts, {unread_messages} unread conversations, {failed_pushes} delivery failures.",
        "network_security": f"{blocked_users} blocked users and {muted_conversations} muted conversations.",
        "community_intelligence": f"{group_memberships} memberships and {owned_groups} owned communities.",
        "creator_reach": f"{shares} shares, {status_views} status views, {public_posts} public posts.",
        "connection_recovery": f"{pending_outgoing} pending outgoing requests and {blocked_users} blocked edges.",
        "blocks-mutes": f"{blocked_users} blocked users and {muted_conversations} muted conversations.",
        "bans": "No user-visible ban count is exposed in the user dashboard.",
        "push-delivery": f"{failed_pushes} failed push attempts and {push_devices} active device registrations.",
        "message-health": f"{active_conversations} conversations and {unread_messages} unread conversation records.",
        "audit": f"{risk_alerts} owner-visible risk or protection signals.",
    }
    state_map = {
        "notifications": _state_for_count(failed_pushes),
        "messenger": "ACTION REQUIRED" if unread_messages else "READY",
        "friends": "ACTION REQUIRED" if pending_incoming else "READY",
        "followers": "READY",
        "groups": "READY",
        "status_activity": "READY",
        "community_activity": "READY",
        "network_health": _health_state(network_trust),
        "delivery_intelligence": _state_for_count(failed_pushes),
        "notification_intelligence": "READY",
        "relationship_intelligence": "READY",
        "connection_analytics": "READY",
        "audience_mapping": "BETA",
        "growth_signals": "BETA",
        "delivery_matrix": _state_for_count(failed_pushes),
        "network_security": "WARNING" if blocked_users else "READY",
        "community_intelligence": "READY",
        "creator_reach": "READY",
        "connection_recovery": "ACTION REQUIRED" if pending_outgoing else "READY",
        "blocks-mutes": "WARNING" if blocked_users or muted_conversations else "READY",
        "bans": "READY",
        "push-delivery": _state_for_count(failed_pushes),
        "message-health": "ACTION REQUIRED" if unread_messages else "READY",
        "audit": "READY",
    }
    subsystems = {
        item["key"]: _subsystem_payload(
            item,
            count_map.get(item["key"], 0),
            state_map.get(item["key"], "READY"),
            detail_map.get(item["key"], item.get("description") or ""),
            recommendations if item["key"] in {"network_health", "delivery_matrix"} else [],
        )
        for item in NETWORK_SUBSYSTEMS
    }
    cards = [subsystems[key] for key in (
        "notifications",
        "messenger",
        "friends",
        "followers",
        "groups",
        "status_activity",
        "community_activity",
        "network_health",
        "delivery_intelligence",
        "notification_intelligence",
        "relationship_intelligence",
        "connection_analytics",
        "audience_mapping",
        "growth_signals",
        "delivery_matrix",
        "network_security",
        "community_intelligence",
        "creator_reach",
        "connection_recovery",
    )]

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
            "relationship_score": relationship_score,
            "audience_score": audience_health,
            "community_score": community_health,
            "network_trust_score": network_trust,
        },
        "intelligence": {
            "network_health": network_trust,
            "relationship_score": relationship_score,
            "audience_score": audience_health,
            "delivery_score": delivery_health,
            "community_score": community_health,
            "unread_messages": unread_messages,
            "pending_requests": pending_incoming,
            "new_followers": followers,
            "notification_queue": unread_notifications,
            "delivery_health": delivery_health,
            "risk_alerts": risk_alerts,
            "recent_activity": notifications_today + public_comments + status_views,
            "recommended_next_actions": recommendations,
        },
        "cards": cards,
        "subsystems": subsystems,
        "event_bus": {
            "friend_request_updates": pending_incoming + pending_outgoing,
            "delivery_updates": failed_pushes + unread_notifications,
            "relationship_updates": friends + followers + following,
            "community_updates": group_memberships + public_posts + public_comments,
            "security_updates": blocked_users + muted_conversations,
        },
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
            "raw_device_registrations_redacted": True,
            "reporter_identity_hidden": True,
            "device_private_data_hidden": True,
        },
    }


def build_admin_network_state(conn: Any) -> dict[str, Any]:
    """Return aggregate admin state without raw private content or credentials."""
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
    public_posts = _sum_counts(_count(cur, "posts"), _count(cur, "pulse_posts"))
    public_comments = _sum_counts(_count(cur, "comments"), _count(cur, "pulse_comments"))
    status_views = _sum_counts(_count(cur, "pulse_status_views"), _count(cur, "status_views"))
    shares = _sum_counts(_count(cur, "post_shares"), _count(cur, "pulse_post_shares"))
    metrics = {
        "unread_notifications": unread_notifications,
        "queued_notifications": _sum_counts(_count(cur, "notification_delivery_logs", "status='queued'"), _count(cur, "command_center_notification_events", "status='queued'")),
        "failed_pushes": failed_pushes,
        "active_conversations": _sum_counts(_count(cur, "conversation_participants"), _count(cur, "pulse_conversation_participants")),
        "notifications_per_sec": 0,
        "messages_per_sec": 0,
        "average_delivery_time": 0,
        "retry_queue": _sum_counts(_count(cur, "notification_delivery_logs", "status IN ('queued','retrying')"), _count(cur, "command_center_notification_events", "status IN ('queued','retrying')")),
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
    section_counts = {
        "notifications": unread_notifications,
        "messenger": unread_messages,
        "friends": pending_friend_requests,
        "followers": follows,
        "groups": active_groups,
        "status_activity": status_views,
        "community_activity": public_posts + public_comments,
        "network_health": trust_score,
        "delivery_intelligence": failed_pushes,
        "notification_intelligence": unread_notifications + failed_pushes,
        "relationship_intelligence": accepted_friendships,
        "connection_analytics": follows + accepted_friendships,
        "audience_mapping": follows,
        "growth_signals": public_posts + shares,
        "delivery_matrix": failed_pushes + unread_notifications + unread_messages,
        "network_security": blocks + mutes + bans,
        "community_intelligence": active_groups + group_reports,
        "creator_reach": shares + status_views,
        "connection_recovery": pending_friend_requests + blocks,
        "blocks-mutes": blocks + mutes,
        "bans": bans,
        "push-delivery": failed_pushes,
        "message-health": failed_messages,
        "audit": _sum_counts(_count(cur, "admin_audit_logs"), _count(cur, "admin_activity_logs")),
    }
    sections = []
    for section in NETWORK_SECTIONS:
        value = section_counts.get(section["key"], 0)
        warning_keys = {"push-delivery", "message-health", "delivery_intelligence", "delivery_matrix", "network_security", "blocks-mutes", "bans"}
        sections.append({
            **section,
            "count": value,
            "label": section.get("admin_label") or section.get("label"),
            "state": "WARNING" if section["key"] in warning_keys and value else "PRODUCTION READY",
        })
    return {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "metrics": metrics,
        "sections": sections,
        "privacy": {
            "message_body_redacted": True,
            "raw_device_registrations_redacted": True,
            "reporter_identity_hidden": True,
            "device_private_data_hidden": True,
            "admin_sensitive_access_audited": True,
        },
    }


def state_for_widget(network_state: dict[str, Any], widget_key: str) -> dict[str, Any] | None:
    """Return dashboard widget state for Pulse Network widgets."""
    key = str(widget_key or "").strip().lower().replace("-", "_")
    aliases = {
        "messages": "messenger",
        "network_insights": "network_health",
        "push_delivery": "delivery_intelligence",
    }
    key = aliases.get(key, key)
    subsystem = (network_state.get("subsystems") or {}).get(key)
    if not subsystem:
        return None
    return {
        "state": subsystem.get("state") or "READY",
        "route": subsystem.get("route") or "/dashboard/network",
        "cta_label": subsystem.get("cta_label") or subsystem.get("action") or "Review Network Health",
        "detail": subsystem.get("detail") or "",
    }
