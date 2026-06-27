"""Role-aware PulseSoc Mission Control dashboard data layer.

This module keeps dashboard access decisions server-side so the template only
renders the sanitized inventory it is allowed to show.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services import dashboard_account_command_center
from services import db as db_service
from services import premium_identity_engine


PUBLIC_ROLES = {"user", "member", "free", "premium", "creator", "seller"}
ADMIN_ROLES = {"owner", "super_admin", "admin", "developer_ops", "developer"}
MODERATOR_ROLES = {"moderator", "pulse_moderator", "support_manager", "support_agent", "content_manager"}


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
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _count(cur: Any, table: str, where: str, params: tuple[Any, ...]) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _admin_for_account(cur: Any, user: dict[str, Any], session_admin: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if session_admin:
        return session_admin
    email = str(user.get("email") or "").strip().lower()
    if not email or not _table_exists(cur, "admin_users"):
        return None
    try:
        cur.execute("SELECT * FROM admin_users WHERE lower(email)=lower(?) AND status='active' LIMIT 1", (email,))
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _user_capabilities(cur: Any, user: dict[str, Any], session_admin: dict[str, Any] | None = None) -> dict[str, Any]:
    user_id = _safe_int(user.get("user_id"), 0)
    admin = _admin_for_account(cur, user, session_admin)
    admin_role = str((admin or {}).get("role") or "").strip().lower()
    is_admin = admin_role in ADMIN_ROLES or admin_role == "owner"
    is_moderator = is_admin or admin_role in MODERATOR_ROLES
    premium = premium_identity_engine.has_active_premium(user)
    creator = bool(
        premium
        or str(user.get("creator_mode") or user.get("account_type") or "").lower() in {"creator", "seller", "business"}
        or _count(cur, "posts", "user_id=?", (user_id,)) > 0
        or _count(cur, "pulse_reels", "user_id=?", (user_id,)) > 0
    )
    seller = bool(
        str(user.get("seller_status") or user.get("marketplace_seller_status") or "").lower() in {"active", "approved"}
        or _count(cur, "marketplace_items", "seller_user_id=?", (user_id,)) > 0
    )
    verified = bool(user.get("email_verified") or user.get("verification_status") == "verified" or premium_identity_engine.identity_mark(user))
    return {
        "user_id": user_id,
        "premium": premium,
        "creator": creator,
        "seller": seller,
        "admin": is_admin,
        "moderator": is_moderator,
        "admin_role": admin_role if (is_admin or is_moderator) else "",
        "verified": verified,
    }


def _widget(
    key: str,
    name: str,
    category: str,
    route: str,
    description: str,
    *,
    api_endpoint: str = "",
    required_role: str = "",
    premium_required: bool = False,
    creator_required: bool = False,
    seller_required: bool = False,
    admin_only: bool = False,
    moderator_only: bool = False,
    free_visible_locked: bool = True,
    sort_order: int = 100,
    accent: str = "cyan",
    status: str = "PRODUCTION_READY",
    tables: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "widget_key": key,
        "display_name": name,
        "category": category,
        "route": route,
        "api_endpoint": api_endpoint,
        "description": description,
        "required_role": required_role,
        "premium_required": premium_required,
        "creator_required": creator_required,
        "seller_required": seller_required,
        "admin_only": admin_only,
        "moderator_only": moderator_only,
        "free_visible_locked": free_visible_locked,
        "sort_order": sort_order,
        "accent": accent,
        "status": status,
        "tables": list(tables),
        "dependencies": list(dependencies),
        "is_active": True,
    }


SECTION_META = {
    "Account Command Center": {"icon": "ID", "accent": "cyan", "label": "Account"},
    "Pulse Network": {"icon": "PN", "accent": "purple", "label": "Network"},
    "Creator Studio": {"icon": "CS", "accent": "blue", "label": "Creator"},
    "Intelligence Center": {"icon": "AI", "accent": "emerald", "label": "Intel"},
    "Economy & Earnings": {"icon": "$", "accent": "gold", "label": "Economy"},
    "Pulse Radio & Media": {"icon": "FM", "accent": "purple", "label": "Media"},
    "Moderation / Safety": {"icon": "SH", "accent": "emerald", "label": "Safety"},
    "Admin / Moderator Only": {"icon": "AD", "accent": "red", "label": "Admin"},
    "Ads & Sponsorships": {"icon": "AD", "accent": "gold", "label": "Ads"},
    "PulseSoc AI": {"icon": "AI", "accent": "purple", "label": "AI"},
    "System Status": {"icon": "SYS", "accent": "cyan", "label": "System"},
}

WIDGET_ICONS = {
    "profile": "ID",
    "verification": "OK",
    "account_health": "98",
    "security": "LK",
    "settings": "GE",
    "advanced_security": "2F",
    "notifications": "AL",
    "messages": "MS",
    "friends": "FR",
    "followers": "NW",
    "groups": "GR",
    "status_activity": "ST",
    "community_activity": "CM",
    "my_posts": "PO",
    "reels": "RL",
    "videos": "VD",
    "statuses": "SS",
    "live_studio": "LV",
    "audience_analytics": "AN",
    "content_performance": "CP",
    "best_posting_time": "BT",
    "creator_score": "SC",
    "creator_tools": "TL",
    "scam_shield": "SH",
    "scam_alerts": "!",
    "pulse_intelligence": "PI",
    "ai_insights": "AI",
    "safety_scan": "100",
    "recommendations": "RC",
    "wallet": "WL",
    "earnings": "ER",
    "marketplace": "MK",
    "seller_tools": "SL",
    "subscriptions": "SB",
    "premium": "PR",
    "creator_revenue": "RV",
    "payouts": "PY",
    "pulse_radio": "FM",
    "music_library": "MU",
    "video_library": "VL",
    "saved_media": "SV",
    "upload_music": "UP",
    "playlists": "PL",
    "reports_submitted": "RP",
    "blocked_users": "BL",
    "appeals": "AP",
    "moderation_status": "MD",
    "content_removals": "RM",
    "identity_protection": "ID",
    "session_intelligence": "SI",
    "device_intelligence": "DV",
    "security_timeline": "TL",
    "threat_detection": "TH",
    "login_analytics": "LA",
    "network_insights": "NI",
    "connection_analytics": "CA",
    "audience_mapping": "AM",
    "relationship_intelligence": "RI",
    "growth_signals": "GS",
    "trend_intelligence": "TR",
    "content_planner": "PL",
    "post_scheduler": "PS",
    "draft_studio": "DR",
    "ai_creator_assistant": "AC",
    "engagement_prediction": "EP",
    "creator_reputation": "CR",
    "viral_opportunity_scanner": "VO",
    "safety_center": "SC",
    "threat_intelligence": "TI",
    "risk_scanner": "RS",
    "reputation_monitoring": "RM",
    "deep_signal_analysis": "DS",
    "ai_research_assistant": "RA",
    "ai_feed_intelligence": "FI",
    "predictive_alerts": "PA",
    "community_heatmaps": "HM",
    "revenue_analytics": "RA",
    "ad_revenue": "AR",
    "affiliate_revenue": "AF",
    "store_analytics": "SA",
    "product_intelligence": "PI",
    "revenue_forecasting": "RF",
    "radio_studio": "RS",
    "creator_music_distribution": "MD",
    "audio_analytics": "AA",
    "media_analytics": "MA",
    "broadcast_tools": "BT",
    "sponsored_audio": "SA",
    "view_sponsored_signals": "VS",
    "ads_manager": "AM",
    "campaign_builder": "CB",
    "sponsored_signal_studio": "SS",
    "ad_analytics": "AN",
    "brand_deals": "BD",
    "creator_sponsorships": "CS",
    "ad_revenue_center": "RC",
    "audience_targeting": "AT",
    "conversion_tracking": "CT",
    "undx": "UX",
    "ai_assistant": "AI",
    "ai_research": "RS",
    "ai_content_generator": "CG",
    "ai_image_tools": "IM",
    "ai_music_tools": "MU",
    "ai_video_tools": "VI",
    "ai_intelligence_center": "IC",
    "feed_status": "FD",
    "messenger_status": "MS",
    "live_status": "LV",
    "radio_status": "FM",
    "marketplace_status": "MK",
    "notifications_status": "NT",
    "ai_status": "AI",
    "scam_shield_status": "SH",
    "ads_status": "AD",
    "creator_studio_status": "CS",
}


WIDGETS: list[dict[str, Any]] = [
    _widget("profile", "Profile", "Account Command Center", "/dashboard/account/profile", "Manage your public identity, bio, avatar, and profile surface.", sort_order=10),
    _widget("verification", "Verification", "Account Command Center", "/dashboard/account/verification", "Check email, identity, and premium verification state.", sort_order=20, accent="emerald"),
    _widget("account_health", "Account Health", "Account Command Center", "/dashboard/account/health", "Review account safety, recovery, and activity protections.", sort_order=30, accent="emerald"),
    _widget("security", "Security", "Account Command Center", "/dashboard/account/security", "Password, sessions, devices, and sensitive action protection.", sort_order=40),
    _widget("settings", "Settings", "Account Command Center", "/dashboard/account/settings", "Profile, notification, privacy, and account settings.", sort_order=50),
    _widget("advanced_security", "Advanced Security", "Account Command Center", "/dashboard/account/security", "Premium device intelligence and risk hardening.", premium_required=True, sort_order=60, accent="purple"),
    _widget("notifications", "Notifications", "Pulse Network", "/dashboard/network/notifications", "Friend requests, reactions, follows, security alerts, and updates.", sort_order=10),
    _widget("messages", "Messages", "Pulse Network", "/dashboard/network/messages", "Open Messenger with unread counts and private chat controls.", sort_order=20, accent="emerald"),
    _widget("friends", "Friends", "Pulse Network", "/dashboard/network/friends", "Review friend requests and your connection graph.", sort_order=30),
    _widget("followers", "Followers / Following", "Pulse Network", "/dashboard/network/followers", "Inspect your public network counts without exposing private data.", sort_order=40),
    _widget("groups", "Groups", "Pulse Network", "/dashboard/network/groups", "Communities and rooms you are allowed to access.", sort_order=50),
    _widget("status_activity", "Status Activity", "Pulse Network", "/pulse/status", "Your status rail, story signals, and viewer activity.", sort_order=60, accent="purple"),
    _widget("community_activity", "Community Activity", "Pulse Network", "/pulse", "Your feed participation, replies, and community signals.", sort_order=70),
    _widget("my_posts", "My Posts", "Creator Studio", "/pulse/profile", "Review and manage your published posts.", sort_order=10),
    _widget("reels", "Reels", "Creator Studio", "/pulse/reels", "Create, review, and manage short-form reels.", sort_order=20),
    _widget("videos", "Videos", "Creator Studio", "/pulse/videos", "Upload and manage long-form video signals.", sort_order=30),
    _widget("statuses", "Statuses", "Creator Studio", "/pulse/status", "Build and inspect immersive status stories.", sort_order=40),
    _widget("live_studio", "Live Studio", "Creator Studio", "/pulse/live", "Go live or schedule a live creator session.", creator_required=True, sort_order=50, accent="red"),
    _widget("audience_analytics", "Audience Analytics", "Creator Studio", "/pulse/creator/analytics", "Premium analytics for reach, retention, and followers.", premium_required=True, creator_required=True, sort_order=60, accent="purple"),
    _widget("content_performance", "Content Performance", "Creator Studio", "/pulse/creator/analytics", "Track posts, reels, videos, comments, and saves.", premium_required=True, creator_required=True, sort_order=70, accent="purple"),
    _widget("best_posting_time", "Best Posting Time", "Creator Studio", "/pulse/creator/analytics", "AI-assisted timing insights for creators.", premium_required=True, creator_required=True, sort_order=80, accent="purple"),
    _widget("creator_score", "Creator Score", "Creator Studio", "/pulse/creator/dashboard", "Creator readiness and audience health.", creator_required=True, sort_order=90, accent="emerald"),
    _widget("creator_tools", "Creator Tools", "Creator Studio", "/pulse/creator/dashboard", "Creator workflows, media, and publishing tools.", creator_required=True, sort_order=100),
    _widget("scam_shield", "Scam Shield", "Intelligence Center", "/scam-shield", "Basic scam education and safety guardrails.", sort_order=10, accent="emerald"),
    _widget("scam_alerts", "Scam Alerts", "Intelligence Center", "/dashboard/scam-alerts", "Public and user-relevant scam warnings.", sort_order=20, accent="red"),
    _widget("pulse_intelligence", "Pulse Intelligence", "Intelligence Center", "/pulse", "Community mood, trends, and safety signals.", sort_order=30),
    _widget("ai_insights", "AI Insights", "Intelligence Center", "/pulse/premium/intelligence", "Premium AI summaries, recommendations, and creator intelligence.", premium_required=True, sort_order=40, accent="purple"),
    _widget("safety_scan", "Safety Scan", "Intelligence Center", "/scam-shield", "Scan links, messages, and activity for warning signs.", sort_order=50, accent="emerald"),
    _widget("recommendations", "Recommendations", "Intelligence Center", "/pulse", "Personalized but privacy-safe discovery recommendations.", premium_required=True, sort_order=60, accent="purple"),
    _widget("wallet", "Wallet", "Economy & Earnings", "/pulse/portfolio", "Manual portfolio and wallet readiness tools.", sort_order=10, accent="gold"),
    _widget("earnings", "Earnings", "Economy & Earnings", "/pulse/creator/analytics", "Owner-only creator earnings once enabled.", creator_required=True, sort_order=20, accent="gold", status="BETA", tables=("creator_dashboard_metrics",), dependencies=("creator",)),
    _widget("marketplace", "Marketplace", "Economy & Earnings", "/pulse/marketplace", "Browse marketplace items under platform safety rules.", sort_order=30),
    _widget("seller_tools", "Seller Tools", "Economy & Earnings", "/pulse/merchant/dashboard", "Seller inventory and marketplace tools.", seller_required=True, sort_order=40, accent="gold"),
    _widget("subscriptions", "Subscriptions", "Economy & Earnings", "/account", "Manage plan, entitlement, and access state.", premium_required=True, sort_order=50),
    _widget("premium", "Premium", "Economy & Earnings", "/pulse/premium", "Unlock premium creator, AI, and security tools.", sort_order=60, accent="gold"),
    _widget("creator_revenue", "Creator Revenue", "Economy & Earnings", "/pulse/creator/analytics", "Creator revenue insights when available.", premium_required=True, creator_required=True, sort_order=70, accent="gold"),
    _widget("payouts", "Payouts", "Economy & Earnings", "/account", "Payout readiness and account requirements.", premium_required=True, seller_required=True, sort_order=80, accent="gold"),
    _widget("pulse_radio", "Pulse Radio", "Pulse Radio & Media", "/pulse/music", "Listen to approved PulseSoc music and radio streams.", sort_order=10, accent="emerald"),
    _widget("music_library", "Music Library", "Pulse Radio & Media", "/pulse/music", "Approved tracks, creator-safe sounds, and uploads.", sort_order=20),
    _widget("video_library", "Video Library", "Pulse Radio & Media", "/pulse/videos", "Your videos and public video discovery.", sort_order=30),
    _widget("saved_media", "Saved Media", "Pulse Radio & Media", "/pulse/saved", "Owner-only saved content and media.", sort_order=40),
    _widget("upload_music", "Upload Music", "Pulse Radio & Media", "/pulse/music", "Partner upload workflow for approved music.", premium_required=True, sort_order=50, accent="purple"),
    _widget("playlists", "Playlists", "Pulse Radio & Media", "/pulse/music", "Build radio-ready playlists.", premium_required=True, sort_order=60, accent="purple"),
    _widget("reports_submitted", "Reports Submitted", "Moderation / Safety", "/support", "Your submitted reports and support requests.", sort_order=10),
    _widget("blocked_users", "Blocked Users", "Moderation / Safety", "/pulse/profile/security", "People you blocked and privacy controls.", sort_order=20),
    _widget("appeals", "Appeals", "Moderation / Safety", "/support", "Submit or review your own moderation appeals.", sort_order=30),
    _widget("moderation_status", "Moderation Status", "Moderation / Safety", "/support", "Your own moderation and report status only.", sort_order=40),
    _widget("content_removals", "Content Removals", "Moderation / Safety", "/support", "Owner-visible content action history when available.", sort_order=50),
    _widget("reports_queue", "Reports Queue", "Admin / Moderator Only", "/admin/private-chat-reports", "Moderation report queue.", moderator_only=True, free_visible_locked=False, sort_order=10, accent="red"),
    _widget("blocked_ips", "Blocked IPs", "Admin / Moderator Only", "/admin/security", "Security actions and blocked IPs.", admin_only=True, free_visible_locked=False, sort_order=20, accent="red"),
    _widget("suspicious_domains", "Suspicious Domains", "Admin / Moderator Only", "/admin/security", "Domain intelligence and scam signals.", moderator_only=True, free_visible_locked=False, sort_order=30, accent="red"),
    _widget("admin_actions", "Admin Actions", "Admin / Moderator Only", "/admin/dashboard", "Authorized admin controls.", admin_only=True, free_visible_locked=False, sort_order=40, accent="red"),
    _widget("audit_logs", "Audit Logs", "Admin / Moderator Only", "/admin/audit", "Audit history for authorized staff.", admin_only=True, free_visible_locked=False, sort_order=50, accent="red"),
    _widget("platform_metrics", "Platform Metrics", "Admin / Moderator Only", "/admin/analytics", "Platform health and analytics.", admin_only=True, free_visible_locked=False, sort_order=60, accent="purple"),
    _widget("infrastructure_health", "Infrastructure Health", "Admin / Moderator Only", "/admin/system", "Railway, database, Redis, and service checks.", admin_only=True, free_visible_locked=False, sort_order=70),
    _widget("push_notification_health", "Push Notification Health", "Admin / Moderator Only", "/admin/notifications", "Push and notification delivery health.", admin_only=True, free_visible_locked=False, sort_order=80),
    _widget("livekit_mux_health", "LiveKit / Mux Health", "Admin / Moderator Only", "/admin/livestreams", "Live media provider diagnostics.", admin_only=True, free_visible_locked=False, sort_order=90),
]

WIDGETS.extend([
    _widget("identity_protection", "Identity Protection", "Account Command Center", "/pulse/profile/security", "Premium identity and impersonation protection.", premium_required=True, sort_order=70, accent="purple", status="BETA", tables=("users", "security_events"), dependencies=("premium", "security")),
    _widget("session_intelligence", "Session Intelligence", "Account Command Center", "/pulse/settings/devices", "Session and device activity analysis.", premium_required=True, sort_order=80, accent="purple", status="PARTIAL", tables=("security_events",), dependencies=("security",)),
    _widget("device_intelligence", "Device Intelligence", "Account Command Center", "/pulse/settings/devices", "Known-device trust and activity review.", premium_required=True, sort_order=90, accent="purple", status="PARTIAL", tables=("security_events",), dependencies=("security",)),
    _widget("security_timeline", "Security Timeline", "Account Command Center", "/pulse/profile/security", "Timeline for account safety events.", premium_required=True, sort_order=100, accent="purple", status="BETA", tables=("security_events", "audit_logs"), dependencies=("security",)),
    _widget("threat_detection", "Threat Detection", "Account Command Center", "/pulse/profile/security", "Premium warning signals for unusual activity.", premium_required=True, sort_order=110, accent="red", status="BETA", tables=("security_events",), dependencies=("security",)),
    _widget("login_analytics", "Login Analytics", "Account Command Center", "/pulse/profile/security", "Login pattern analytics for account owners.", premium_required=True, sort_order=120, accent="purple", status="PARTIAL", tables=("security_events",), dependencies=("security",)),

    _widget("network_insights", "Network Insights", "Pulse Network", "/pulse/premium/intelligence", "Premium view of audience and connection trends.", premium_required=True, sort_order=80, accent="purple", status="BETA", tables=("friendships",), dependencies=("premium",)),
    _widget("connection_analytics", "Connection Analytics", "Pulse Network", "/pulse/premium/intelligence", "Relationship growth and retention signals.", premium_required=True, sort_order=90, accent="purple", status="COMING_SOON", tables=("friendships",), dependencies=("premium",)),
    _widget("audience_mapping", "Audience Mapping", "Pulse Network", "/pulse/creator/analytics", "Creator audience cluster mapping.", premium_required=True, creator_required=True, sort_order=100, accent="purple", status="COMING_SOON", tables=("friendships", "posts"), dependencies=("creator",)),
    _widget("relationship_intelligence", "Relationship Intelligence", "Pulse Network", "/pulse/premium/intelligence", "AI-assisted private relationship insights.", premium_required=True, sort_order=110, accent="purple", status="COMING_SOON", tables=("friendships",), dependencies=("premium", "ai")),
    _widget("growth_signals", "Growth Signals", "Pulse Network", "/pulse/creator/analytics", "Signals that help creators grow without private data leakage.", premium_required=True, creator_required=True, sort_order=120, accent="emerald", status="BETA", tables=("posts", "pulse_reels"), dependencies=("creator",)),

    _widget("trend_intelligence", "Trend Intelligence", "Creator Studio", "/pulse/creator/analytics", "Premium trend signals for creators.", premium_required=True, creator_required=True, sort_order=110, accent="purple", status="BETA", tables=("posts", "pulse_reels"), dependencies=("creator", "premium")),
    _widget("content_planner", "Content Planner", "Creator Studio", "/pulse/creator/dashboard", "Plan future posts, reels, videos, and statuses.", premium_required=True, creator_required=True, sort_order=120, accent="purple", status="COMING_SOON", tables=("dashboard_modules",), dependencies=("creator",)),
    _widget("post_scheduler", "Post Scheduler", "Creator Studio", "/pulse/creator/dashboard", "Schedule future PulseSoc posts.", premium_required=True, creator_required=True, sort_order=130, accent="purple", status="COMING_SOON", tables=("dashboard_modules",), dependencies=("creator",)),
    _widget("draft_studio", "Draft Studio", "Creator Studio", "/pulse/creator/dashboard", "Save and manage creator drafts.", premium_required=True, creator_required=True, sort_order=140, accent="purple", status="COMING_SOON", tables=("dashboard_modules",), dependencies=("creator",)),
    _widget("ai_creator_assistant", "AI Creator Assistant", "Creator Studio", "/pulse/premium/intelligence", "AI support for captions and creator workflows.", premium_required=True, creator_required=True, sort_order=150, accent="purple", status="BETA", tables=("ai_conversations", "ai_messages"), dependencies=("ai", "creator")),
    _widget("engagement_prediction", "Engagement Prediction", "Creator Studio", "/pulse/creator/analytics", "Predictive engagement estimates for creator content.", premium_required=True, creator_required=True, sort_order=160, accent="purple", status="COMING_SOON", tables=("creator_dashboard_metrics",), dependencies=("creator", "ai")),
    _widget("creator_reputation", "Creator Reputation", "Creator Studio", "/pulse/creator/analytics", "Private creator reputation and trust signals.", premium_required=True, creator_required=True, sort_order=170, accent="emerald", status="BETA", tables=("security_events", "posts"), dependencies=("creator", "security")),
    _widget("viral_opportunity_scanner", "Viral Opportunity Scanner", "Creator Studio", "/pulse/creator/analytics", "Find safe high-opportunity content windows.", premium_required=True, creator_required=True, sort_order=180, accent="purple", status="COMING_SOON", tables=("dashboard_recommendations",), dependencies=("creator", "ai")),

    _widget("safety_center", "Safety Center", "Intelligence Center", "/scam-shield", "Unified safety tools for all users.", sort_order=70, accent="emerald", status="BETA", tables=("security_events", "security_reports"), dependencies=("security",)),
    _widget("threat_intelligence", "Threat Intelligence", "Intelligence Center", "/pulse/premium/intelligence", "Premium threat summaries and scam risk intelligence.", premium_required=True, sort_order=80, accent="red", status="BETA", tables=("security_events",), dependencies=("premium", "security")),
    _widget("risk_scanner", "Risk Scanner", "Intelligence Center", "/scam-shield", "Premium scan layer for links and suspicious content.", premium_required=True, sort_order=90, accent="red", status="PARTIAL", tables=("security_events",), dependencies=("security",)),
    _widget("reputation_monitoring", "Reputation Monitoring", "Intelligence Center", "/pulse/premium/intelligence", "Private reputation monitoring for creators and sellers.", premium_required=True, sort_order=100, accent="purple", status="COMING_SOON", tables=("dashboard_recommendations",), dependencies=("premium",)),
    _widget("deep_signal_analysis", "Deep Signal Analysis", "Intelligence Center", "/pulse/premium/intelligence", "Premium deep analysis of public PulseSoc signals.", premium_required=True, sort_order=110, accent="purple", status="COMING_SOON", tables=("ai_analyses",), dependencies=("ai", "premium")),
    _widget("ai_research_assistant", "AI Research Assistant", "Intelligence Center", "/pulse/premium/intelligence", "Research assistant for allowed PulseSoc intelligence workflows.", premium_required=True, sort_order=120, accent="purple", status="BETA", tables=("ai_conversations",), dependencies=("ai",)),
    _widget("ai_feed_intelligence", "AI Feed Intelligence", "Intelligence Center", "/pulse/premium/intelligence", "AI summaries for feed trends and public signals.", premium_required=True, sort_order=130, accent="purple", status="BETA", tables=("ai_analyses",), dependencies=("ai",)),
    _widget("predictive_alerts", "Predictive Alerts", "Intelligence Center", "/pulse/premium/intelligence", "Future alert predictions from safe aggregate data.", premium_required=True, sort_order=140, accent="purple", status="COMING_SOON", tables=("dashboard_recommendations",), dependencies=("ai", "premium")),
    _widget("community_heatmaps", "Community Heatmaps", "Intelligence Center", "/pulse/premium/intelligence", "Aggregate-only community activity maps.", premium_required=True, sort_order=150, accent="purple", status="COMING_SOON", tables=("posts", "pulse_reels"), dependencies=("premium",)),

    _widget("revenue_analytics", "Revenue Analytics", "Economy & Earnings", "/pulse/creator/analytics", "Premium analytics for creator and seller revenue.", premium_required=True, creator_required=True, sort_order=90, accent="gold", status="BETA", tables=("creator_revenue_events",), dependencies=("creator", "premium")),
    _widget("ad_revenue", "Ad Revenue", "Economy & Earnings", "/pulse/premium", "Prepared ad revenue center for eligible creators.", premium_required=True, creator_required=True, sort_order=100, accent="gold", status="COMING_SOON", tables=("ad_revenue",), dependencies=("ads", "premium")),
    _widget("affiliate_revenue", "Affiliate Revenue", "Economy & Earnings", "/pulse/premium", "Prepared affiliate revenue tracking.", premium_required=True, sort_order=110, accent="gold", status="COMING_SOON", tables=("dashboard_modules",), dependencies=("premium",)),
    _widget("store_analytics", "Store Analytics", "Economy & Earnings", "/pulse/merchant/dashboard", "Seller store analytics.", seller_required=True, sort_order=120, accent="gold", status="BETA", tables=("marketplace_listings", "marketplace_orders_placeholder"), dependencies=("marketplace",)),
    _widget("product_intelligence", "Product Intelligence", "Economy & Earnings", "/pulse/merchant/dashboard", "Marketplace product intelligence.", seller_required=True, sort_order=130, accent="gold", status="BETA", tables=("marketplace_listings",), dependencies=("marketplace",)),
    _widget("revenue_forecasting", "Revenue Forecasting", "Economy & Earnings", "/pulse/creator/analytics", "Forecast creator and seller revenue.", premium_required=True, creator_required=True, sort_order=140, accent="gold", status="COMING_SOON", tables=("creator_dashboard_metrics",), dependencies=("premium",)),

    _widget("radio_studio", "Radio Studio", "Pulse Radio & Media", "/pulse/music", "Tools for radio-ready music workflows.", premium_required=True, sort_order=70, accent="purple", status="BETA", tables=("audio_tracks", "pulse_music_events"), dependencies=("music",)),
    _widget("creator_music_distribution", "Creator Music Distribution", "Pulse Radio & Media", "/pulse/music", "Prepared distribution layer for approved creators.", premium_required=True, creator_required=True, sort_order=80, accent="purple", status="COMING_SOON", tables=("audio_tracks",), dependencies=("music", "creator")),
    _widget("audio_analytics", "Audio Analytics", "Pulse Radio & Media", "/pulse/music", "Track plays, saves, and use count.", premium_required=True, sort_order=90, accent="purple", status="BETA", tables=("pulse_music_events",), dependencies=("music",)),
    _widget("media_analytics", "Media Analytics", "Pulse Radio & Media", "/pulse/creator/analytics", "Creator media analytics.", premium_required=True, creator_required=True, sort_order=100, accent="purple", status="BETA", tables=("videos", "pulse_reels"), dependencies=("creator",)),
    _widget("broadcast_tools", "Broadcast Tools", "Pulse Radio & Media", "/pulse/live", "Broadcast and live media tool entry point.", premium_required=True, creator_required=True, sort_order=110, accent="purple", status="BETA", tables=("live_streams",), dependencies=("live",)),
    _widget("sponsored_audio", "Sponsored Audio", "Pulse Radio & Media", "/pulse/music", "Prepared sponsored audio surface.", premium_required=True, sort_order=120, accent="gold", status="COMING_SOON", tables=("sponsorships", "ad_creatives"), dependencies=("ads", "music")),

    _widget("view_sponsored_signals", "View Sponsored Signals", "Ads & Sponsorships", "/pulse", "View public sponsored signals when available.", sort_order=10, accent="gold", status="BETA", tables=("ad_placements",), dependencies=("ads",)),
    _widget("ads_manager", "Ads Manager", "Ads & Sponsorships", "/pulse/premium", "Prepared advertiser dashboard.", premium_required=True, sort_order=20, accent="gold", status="COMING_SOON", tables=("ads", "ad_campaigns"), dependencies=("ads", "premium")),
    _widget("campaign_builder", "Campaign Builder", "Ads & Sponsorships", "/pulse/premium", "Prepared campaign builder.", premium_required=True, sort_order=30, accent="gold", status="COMING_SOON", tables=("ad_campaigns", "ad_creatives"), dependencies=("ads",)),
    _widget("sponsored_signal_studio", "Sponsored Signal Studio", "Ads & Sponsorships", "/pulse/premium", "Prepared sponsored signal creation flow.", premium_required=True, sort_order=40, accent="gold", status="COMING_SOON", tables=("ad_creatives", "ad_placements"), dependencies=("ads",)),
    _widget("ad_analytics", "Ad Analytics", "Ads & Sponsorships", "/pulse/premium", "Prepared analytics for ad performance.", premium_required=True, sort_order=50, accent="gold", status="COMING_SOON", tables=("ad_impressions", "ad_clicks"), dependencies=("ads",)),
    _widget("brand_deals", "Brand Deals", "Ads & Sponsorships", "/pulse/premium", "Prepared brand deal workspace.", premium_required=True, creator_required=True, sort_order=60, accent="gold", status="COMING_SOON", tables=("brand_deals",), dependencies=("ads", "creator")),
    _widget("creator_sponsorships", "Creator Sponsorships", "Ads & Sponsorships", "/pulse/premium", "Prepared sponsorship center for creators.", premium_required=True, creator_required=True, sort_order=70, accent="gold", status="COMING_SOON", tables=("sponsorships",), dependencies=("ads", "creator")),
    _widget("ad_revenue_center", "Ad Revenue Center", "Ads & Sponsorships", "/pulse/premium", "Prepared ad revenue controls.", premium_required=True, creator_required=True, sort_order=80, accent="gold", status="COMING_SOON", tables=("ad_revenue",), dependencies=("ads", "creator")),
    _widget("audience_targeting", "Audience Targeting", "Ads & Sponsorships", "/pulse/premium", "Prepared privacy-safe targeting controls.", premium_required=True, sort_order=90, accent="gold", status="COMING_SOON", tables=("ad_targeting",), dependencies=("ads",)),
    _widget("conversion_tracking", "Conversion Tracking", "Ads & Sponsorships", "/pulse/premium", "Prepared conversion analytics.", premium_required=True, sort_order=100, accent="gold", status="COMING_SOON", tables=("ad_clicks",), dependencies=("ads",)),

    _widget("undx", "UNDX", "PulseSoc AI", "/pulse/premium/undx", "Unknown Destination intelligence layer.", premium_required=True, sort_order=10, accent="purple", status="BETA", tables=("ai_conversations",), dependencies=("premium", "ai")),
    _widget("ai_assistant", "AI Assistant", "PulseSoc AI", "/pulse/assistant", "PulseSoc assistant entry point.", premium_required=True, sort_order=20, accent="purple", status="BETA", tables=("ai_conversations", "ai_messages"), dependencies=("ai",)),
    _widget("ai_research", "AI Research", "PulseSoc AI", "/pulse/premium/intelligence", "Premium AI research workspace.", premium_required=True, sort_order=30, accent="purple", status="BETA", tables=("ai_analyses",), dependencies=("ai",)),
    _widget("ai_content_generator", "AI Content Generator", "PulseSoc AI", "/pulse/premium/intelligence", "AI-assisted creator content generation.", premium_required=True, sort_order=40, accent="purple", status="COMING_SOON", tables=("ai_action_requests",), dependencies=("ai", "creator")),
    _widget("ai_image_tools", "AI Image Tools", "PulseSoc AI", "/pulse/premium/intelligence", "Prepared image tooling.", premium_required=True, sort_order=50, accent="purple", status="COMING_SOON", tables=("ai_action_requests",), dependencies=("ai",)),
    _widget("ai_music_tools", "AI Music Tools", "PulseSoc AI", "/pulse/premium/intelligence", "Prepared music AI tooling.", premium_required=True, sort_order=60, accent="purple", status="COMING_SOON", tables=("ai_action_requests",), dependencies=("ai", "music")),
    _widget("ai_video_tools", "AI Video Tools", "PulseSoc AI", "/pulse/premium/intelligence", "Prepared video AI tooling.", premium_required=True, sort_order=70, accent="purple", status="COMING_SOON", tables=("ai_action_requests",), dependencies=("ai", "video")),
    _widget("ai_intelligence_center", "AI Intelligence Center", "PulseSoc AI", "/pulse/premium/intelligence", "Premium AI intelligence command center.", premium_required=True, sort_order=80, accent="purple", status="BETA", tables=("ai_analyses", "ai_recommendations"), dependencies=("ai", "premium")),

    _widget("feed_status", "Feed", "System Status", "/pulse", "Feed service status.", sort_order=10, accent="emerald", status="ACTIVE", tables=("posts",), dependencies=("feed",)),
    _widget("messenger_status", "Messenger", "System Status", "/pulse/messages", "Messenger service status.", sort_order=20, accent="emerald", status="ACTIVE", tables=("conversations", "private_messages"), dependencies=("messaging",)),
    _widget("live_status", "Live", "System Status", "/pulse/live", "Live system status.", sort_order=30, accent="emerald", status="BETA", tables=("live_streams",), dependencies=("live",)),
    _widget("radio_status", "Radio", "System Status", "/pulse/music", "Pulse Radio status.", sort_order=40, accent="emerald", status="ACTIVE", tables=("audio_tracks",), dependencies=("music",)),
    _widget("marketplace_status", "Marketplace", "System Status", "/pulse/marketplace", "Marketplace status.", sort_order=50, accent="emerald", status="BETA", tables=("marketplace_listings",), dependencies=("marketplace",)),
    _widget("notifications_status", "Notifications", "System Status", "/pulse/notifications", "Notification system status.", sort_order=60, accent="gold", status="PARTIAL", tables=("notifications", "notification_delivery_logs"), dependencies=("notifications",)),
    _widget("ai_status", "AI", "System Status", "/pulse/premium/intelligence", "AI systems status.", sort_order=70, accent="purple", status="BETA", tables=("ai_conversations",), dependencies=("ai",)),
    _widget("scam_shield_status", "Scam Shield", "System Status", "/scam-shield", "Scam Shield status.", sort_order=80, accent="emerald", status="ACTIVE", tables=("security_events",), dependencies=("security",)),
    _widget("ads_status", "Ads", "System Status", "/pulse", "Ads system readiness.", sort_order=90, accent="gold", status="BETA", tables=("ad_placements", "ads"), dependencies=("ads",)),
    _widget("creator_studio_status", "Creator Studio", "System Status", "/pulse/creator/dashboard", "Creator Studio status.", creator_required=True, sort_order=100, accent="emerald", status="BETA", tables=("posts", "pulse_reels", "videos"), dependencies=("creator",)),
])


QUICK_ACTIONS = [
    {"label": "Create Post", "route": "/pulse", "capability": "all"},
    {"label": "Go Live", "route": "/pulse/live", "capability": "creator"},
    {"label": "Upload Video", "route": "/pulse/videos", "capability": "all"},
    {"label": "Add Status", "route": "/pulse/status", "capability": "all"},
    {"label": "Invite Friends", "route": "/pulse/friends", "capability": "all"},
    {"label": "Upgrade to Premium", "route": "/pulse/premium", "capability": "free"},
    {"label": "Open Scam Shield", "route": "/scam-shield", "capability": "all"},
    {"label": "Open Pulse Radio", "route": "/pulse/music", "capability": "all"},
]


def _access_for_widget(widget: dict[str, Any], caps: dict[str, Any]) -> str:
    if widget.get("admin_only") and not caps.get("admin"):
        return "hidden"
    if widget.get("moderator_only") and not caps.get("moderator"):
        return "hidden"
    locked_reasons = []
    if widget.get("premium_required") and not caps.get("premium"):
        locked_reasons.append("Premium required")
    if widget.get("creator_required") and not caps.get("creator"):
        locked_reasons.append("Creator mode required")
    if widget.get("seller_required") and not caps.get("seller"):
        locked_reasons.append("Seller approval required")
    if locked_reasons:
        return "locked" if widget.get("free_visible_locked", True) else "hidden"
    return "active"


def _lock_reason(widget: dict[str, Any], caps: dict[str, Any]) -> str:
    reasons = []
    if widget.get("premium_required") and not caps.get("premium"):
        reasons.append("Upgrade to Premium")
    if widget.get("creator_required") and not caps.get("creator"):
        reasons.append("Enable creator access")
    if widget.get("seller_required") and not caps.get("seller"):
        reasons.append("Seller approval required")
    return " + ".join(reasons) or "Locked"


def build_mission_control_dashboard(conn: Any, user: dict[str, Any], session_admin: dict[str, Any] | None = None) -> dict[str, Any]:
    cur = conn.cursor()
    caps = _user_capabilities(cur, user, session_admin)
    user_id = caps["user_id"]
    account_state = dashboard_account_command_center.build_account_state(conn, user)
    metrics = _metrics(cur, user, caps)
    widgets = []
    for widget in WIDGETS:
        access = _access_for_widget(widget, caps)
        if access == "hidden":
            continue
        item = dict(widget)
        state = dashboard_account_command_center.state_for_widget(account_state, item["widget_key"])
        item["access"] = access
        item["lock_reason"] = _lock_reason(widget, caps) if access == "locked" else ""
        if state and state.get("route"):
            item["route"] = str(state.get("route") or item["route"])[:200]
        item["cta_route"] = "/pulse/premium" if "Premium" in item.get("lock_reason", "") else (item["route"] or "/dashboard")
        item["icon"] = WIDGET_ICONS.get(item["widget_key"], "MC")
        if access == "locked":
            item["status_label"] = "LOCK"
        elif state and state.get("state"):
            item["status_label"] = str(state.get("state") or "").upper()
        else:
            item["status_label"] = str(item.get("status") or "PRODUCTION_READY").replace("_", " ").title()
        if state:
            item["state"] = state
        widgets.append(item)
    categories = []
    for category in dict.fromkeys(item["category"] for item in widgets):
        section_widgets = sorted([item for item in widgets if item["category"] == category], key=lambda item: item["sort_order"])
        meta = SECTION_META.get(category, {"icon": "MC", "accent": "cyan", "label": category})
        categories.append({"name": category, "widgets": section_widgets, **meta})
    quick_actions = _quick_actions(caps)
    return {
        "ok": True,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "user": _user_summary(user, caps),
        "metrics": metrics,
        "categories": categories,
        "quick_actions": quick_actions,
        "counts": {
            "visible_widgets": len(widgets),
            "locked_widgets": len([item for item in widgets if item["access"] == "locked"]),
            "active_widgets": len([item for item in widgets if item["access"] == "active"]),
        },
        "account_command_center": account_state,
    }


def _user_summary(user: dict[str, Any], caps: dict[str, Any]) -> dict[str, Any]:
    display = user.get("display_name") or user.get("full_name") or user.get("username") or "PulseSoc Member"
    return {
        "user_id": caps["user_id"],
        "display_name": str(display)[:80],
        "username": str(user.get("username") or user.get("handle") or "")[:80],
        "avatar_url": str(user.get("avatar_url") or user.get("profile_photo_url") or "")[:300],
        "role_badge": caps.get("admin_role") or ("Creator" if caps.get("creator") else "Member"),
        "plan_badge": "Premium" if caps.get("premium") else "Free",
        "verified": bool(caps.get("verified")),
        "is_admin": bool(caps.get("admin")),
        "is_moderator": bool(caps.get("moderator")),
    }


def _metrics(cur: Any, user: dict[str, Any], caps: dict[str, Any]) -> list[dict[str, Any]]:
    user_id = caps["user_id"]
    followers = _count(cur, "friendships", "friend_id=? AND status='accepted'", (user_id,))
    following = _count(cur, "friendships", "user_id=? AND status='accepted'", (user_id,))
    posts = _count(cur, "posts", "user_id=?", (user_id,))
    reels = _count(cur, "pulse_reels", "user_id=?", (user_id,))
    videos = _count(cur, "videos", "owner_user_id=?", (user_id,))
    comments = _count(cur, "comments", "user_id=?", (user_id,))
    unread_messages = _count(cur, "conversation_participants", "user_id=? AND COALESCE(unread_count,0)>0", (user_id,))
    unread_alerts = _count(cur, "notifications", "user_id=? AND COALESCE(read_at,'')=''", (user_id,))
    pulse_score = min(100, 35 + followers * 2 + posts * 3 + reels * 4 + videos * 4 + comments)
    engagement = posts + reels + videos + comments
    health = 92 if caps.get("verified") else 76
    return [
        {"label": "Pulse Score", "value": str(pulse_score), "detail": "creator and trust activity", "icon": "PS", "accent": "emerald", "trend": "Live"},
        {"label": "Followers", "value": str(followers), "detail": f"{following} following", "icon": "NW", "accent": "purple", "trend": "Network"},
        {"label": "Profile Views", "value": str(_profile_views(cur, user_id)), "detail": "private owner metric", "icon": "EV", "accent": "cyan", "trend": "Owner"},
        {"label": "Engagement", "value": str(engagement), "detail": "posts, reels, videos, comments", "icon": "EG", "accent": "purple", "trend": "Signals"},
        {"label": "Account Health", "value": f"{health}%", "detail": "security and verification", "icon": "SH", "accent": "emerald", "trend": "Secure"},
        {"label": "Unread", "value": str(unread_messages + unread_alerts), "detail": "messages and alerts separated", "icon": "IN", "accent": "gold", "trend": "Split"},
    ]


def _profile_views(cur: Any, user_id: int) -> int:
    for table, column in (("profile_views", "profile_user_id"), ("pulse_profile_views", "profile_user_id")):
        count = _count(cur, table, f"{column}=?", (user_id,))
        if count:
            return count
    return 0


def _quick_actions(caps: dict[str, Any]) -> list[dict[str, str]]:
    actions = []
    for action in QUICK_ACTIONS:
        capability = action["capability"]
        if capability == "creator" and not caps.get("creator"):
            actions.append({"label": action["label"], "route": "/pulse/creator/dashboard", "access": "locked", "reason": "Creator access required"})
            continue
        if capability == "free" and caps.get("premium"):
            continue
        actions.append({"label": action["label"], "route": action["route"], "access": "active", "reason": ""})
    return actions


def registry_rows() -> list[dict[str, Any]]:
    return [dict(item) for item in WIDGETS]
