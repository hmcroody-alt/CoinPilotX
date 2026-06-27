"""PulseSoc backend management registry.

This module is the permanent inventory for which PulseSoc features are
manageable from backend/admin surfaces. It is deliberately additive and safe:
the registry can power admin UI, reports, and audits without moving feature
ownership or changing production data paths.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from services import db as db_service


REQUIRED_MODULES = {
    "account": "Account Command Center",
    "network": "Network Command Center",
    "creator": "Creator Command Center",
    "moderation": "Moderation / Safety Command Center",
    "ads": "Ads Command Center",
    "economy": "Economy Command Center",
    "media": "Media Command Center",
    "ai": "AI Command Center",
    "system": "System Command Center",
    "launch": "Launch Readiness Command Center",
    "controls": "Global Controls Command Center",
    "audit": "Audit Command Center",
}

RISK_LEVELS = {"low", "medium", "high", "critical"}
STATUSES = {"active", "partial", "planned", "blocked", "hidden"}


@dataclass(frozen=True)
class BackendFeature:
    feature_key: str
    display_name: str
    category: str
    route: str
    required_role: str
    required_permission: str
    status: str
    owner: str
    backend_service: str
    audit_log_table: str
    risk_level: str
    launch_critical: bool
    manageable_from_backend: bool
    notes: str = ""

    def safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["launch_critical"] = bool(self.launch_critical)
        data["manageable_from_backend"] = bool(self.manageable_from_backend)
        return data


FEATURES: tuple[BackendFeature, ...] = (
    BackendFeature("account.profile", "Profile Manager", "account", "/admin/account-command/profile", "admin", "command_center.view", "active", "Account", "dashboard_account_command_center", "profile_audit_logs", "high", True, True, "Profile updates, avatar/banner controls, privacy state, and rollback audit."),
    BackendFeature("account.verification", "Verification Queue", "account", "/admin/account-command/verification", "admin", "command_center.view", "active", "Trust", "dashboard_account_command_center", "verification_requests", "critical", True, True, "Identity, blue-check, business review, decisions, and appeals."),
    BackendFeature("account.health", "Account Health", "account", "/admin/account-command/account-health", "admin", "command_center.view", "active", "Trust", "dashboard_account_command_center", "account_health_events", "critical", True, True, "Warnings, strikes, restrictions, appeals, and health score."),
    BackendFeature("account.security", "Security Center", "account", "/admin/account-command/security", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "security_login_events", "critical", True, True, "Sessions, devices, suspicious logins, and sensitive action audit."),
    BackendFeature("account.settings", "Settings Manager", "account", "/admin/account-command/settings", "admin", "command_center.view", "active", "Account", "dashboard_account_command_center", "user_settings", "high", True, True, "Server-managed privacy, notification, accessibility, and ads personalization settings."),
    BackendFeature("account.advanced_security", "Advanced Security Manager", "account", "/admin/account-command/advanced-security", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "active_sessions", "critical", True, True, "Risk hardening, trusted devices, sensitive action protection, and recovery state."),
    BackendFeature("account.identity_protection", "Identity Protection", "account", "/admin/account-command/identity-protection", "admin", "trust_safety.manage", "active", "Trust", "dashboard_account_command_center", "account_system_events", "critical", True, True, "Impersonation, username similarity, avatar risk, and badge protection review."),
    BackendFeature("account.appeals", "Appeals", "account", "/admin/account-command/account-health", "admin", "moderation.manage", "partial", "Trust", "dashboard_account_command_center", "account_audit_logs", "high", True, True, "Verification and account-health appeal hooks are present; richer queues can expand here."),
    BackendFeature("account.audit_logs", "Account Audit Logs", "account", "/admin/account-command/audit", "admin", "audit.view", "active", "Security", "dashboard_account_command_center", "account_audit_logs", "critical", True, True, "Sensitive account actions are recorded and admin-reviewable."),
    BackendFeature("account.restrictions", "Restrictions", "account", "/admin/account-command/account-health", "admin", "moderation.manage", "active", "Trust", "dashboard_account_command_center", "account_restrictions", "critical", True, True, "Restriction state is stored and reflected in account health."),
    BackendFeature("account.sessions", "Sessions", "account", "/admin/account-command/session-intelligence", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "active_sessions", "critical", True, True, "Session/device inventory is backend-managed."),
    BackendFeature("account.devices", "Devices", "account", "/admin/account-command/device-intelligence", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "security_devices", "critical", True, True, "Device and push registration management hooks are backend-visible."),
    BackendFeature("account.timeline", "Security Timeline", "account", "/admin/account-command/security-timeline", "admin", "audit.view", "active", "Security", "dashboard_account_command_center", "account_audit_logs", "critical", True, True, "Login, profile, verification, device, and admin event timeline."),
    BackendFeature("account.threat_detection", "Threat Detection", "account", "/admin/account-command/threat-detection", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "account_system_events", "critical", True, True, "Suspicious login, behavior, session, profile, and identity risk review."),
    BackendFeature("account.login_analytics", "Login Analytics", "account", "/admin/account-command/login-analytics", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "security_login_events", "critical", True, True, "Login patterns, failed login counts, device changes, and risk trend review."),
    BackendFeature("network.notifications", "Notifications", "network", "/admin/network-command-center/notifications", "admin", "command_center.view", "active", "Notifications", "notification_orchestrator", "notification_delivery_logs", "high", True, True, "Provider health, delivery logs, queue controls, preferences, retries, and deep-link diagnostics."),
    BackendFeature("network.messages", "Messages", "network", "/admin/network-command-center/messenger", "moderator", "moderation.manage", "active", "Messaging", "chat_realtime_service", "admin_audit_logs", "critical", True, True, "Chat reports, realtime health, delivery receipts, push status, and moderation escalation without private body leakage."),
    BackendFeature("network.groups", "Groups", "network", "/admin/network-command-center/groups", "moderator", "command_center.view", "active", "Community", "community_governance_engine", "admin_tasks", "medium", False, True, "Group memberships, roles, reports, bans, mutes, and group health command surface."),
    BackendFeature("network.status_activity", "Status Activity", "network", "/admin/network-command-center/status-activity", "moderator", "pulse.moderate", "active", "PulseSoc", "pulse_moderation_engine", "moderation_cases", "high", True, True, "Status reports, viewer analytics, completion, replies, shares, and content moderation flow through PulseSoc moderation."),
    BackendFeature("creator.posts", "Posts", "creator", "/admin/creator-command-center/posts", "moderator", "pulse.moderate", "active", "Creator", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Posts are reviewable and manageable through the Creator Command Center and moderation tools."),
    BackendFeature("creator.reels", "Reels", "creator", "/admin/creator-command-center/reels", "moderator", "pulse.moderate", "active", "Creator", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Reels use creator diagnostics, PulseSoc content moderation, and ranking audits."),
    BackendFeature("creator.videos", "Videos", "creator", "/admin/creator-command-center/videos", "moderator", "pulse.moderate", "active", "Creator", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Video review, playback health, and processing state are moderation-visible."),
    BackendFeature("creator.live", "Live Studio", "creator", "/admin/creator-command-center/live-studio", "admin", "system.view", "active", "Live", "dashboard_creator_command_center", "admin_tasks", "critical", True, True, "Live readiness, stream status, provider health, and reports are visible through creator management."),
    BackendFeature("creator.analytics", "Creator Analytics", "creator", "/admin/creator-command-center/analytics", "admin", "analytics.view", "active", "Analytics", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Platform analytics can inspect creator activity safely."),
    BackendFeature("moderation.reports", "Reports Queue", "moderation", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Trust", "pulse_moderation_engine", "moderation_cases", "critical", True, True, "Core report review queue."),
    BackendFeature("moderation.security", "Security Events", "moderation", "/admin/security", "admin", "security.view", "active", "Security", "security_monitoring", "admin_audit_logs", "critical", True, True, "Failed login and security monitoring dashboard."),
    BackendFeature("moderation.scam_shield", "Scam Shield", "moderation", "/admin/scam-shield", "admin", "trust_safety.manage", "active", "Trust", "autonomous_safety_engine", "command_center_security_events", "critical", True, True, "Scam and suspicious activity command surface."),
    BackendFeature("ads.review", "Ads Review Board", "ads", "/admin/pulse-ads-review-board", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_review_logs", "critical", True, True, "Creative review, approval, rejection, and campaign safety."),
    BackendFeature("ads.delivery", "Ad Delivery", "ads", "/admin/pulse-ads-delivery-intelligence", "admin", "analytics.view", "active", "Ads", "pulse_ads_service", "pulse_ad_events", "high", True, True, "Delivery methods, tracking, and placement control."),
    BackendFeature("ads.finance", "Ad Wallets", "ads", "/admin/pulse-ad-finance", "admin", "billing.view", "active", "Ads Finance", "pulse_ad_payments", "pulse_ad_wallet_transactions", "critical", True, True, "Funding, spend ledger, and advertiser finance oversight."),
    BackendFeature("economy.payments", "Payments", "economy", "/admin/payments-command-center", "admin", "billing.view", "active", "Billing", "payment_provider", "admin_audit_logs", "critical", True, True, "Payment command center."),
    BackendFeature("economy.premium", "Premium", "economy", "/admin/transactions", "admin", "billing.view", "active", "Billing", "premium_entitlement_service", "checkout_attempts", "critical", True, True, "Subscription and entitlement review."),
    BackendFeature("economy.marketplace", "Marketplace", "economy", "/admin/departments/monetization", "admin", "monetization.manage", "partial", "Marketplace", "marketplace_engine", "admin_tasks", "high", False, True, "Marketplace controls are routed through monetization command tasks."),
    BackendFeature("media.music", "Music Review", "media", "/admin/pulse-music-review", "moderator", "pulse.moderate", "active", "Media", "music_service", "music_review_logs", "high", True, True, "Uploaded music review and approval queue."),
    BackendFeature("media.uploads", "Uploads", "media", "/admin/pulse-infrastructure", "admin", "system.view", "partial", "Media", "media_storage", "admin_tasks", "critical", True, True, "Storage and upload health are surfaced through infrastructure."),
    BackendFeature("media.radio", "Pulse Radio", "media", "/admin/departments/pulsesoc", "admin", "command_center.view", "active", "Media", "music_service", "admin_tasks", "medium", False, True, "Radio is powered by approved music pool and backend command tasks."),
    BackendFeature("ai.usage", "AI Usage", "ai", "/admin/ai-usage", "admin", "ai.view", "active", "AI", "ai_router", "ai_usage_logs", "high", False, True, "AI usage and provider visibility."),
    BackendFeature("ai.safety", "AI Safety", "ai", "/admin/scam-shield", "admin", "trust_safety.manage", "partial", "AI Safety", "autonomous_safety_engine", "command_center_ai_events", "critical", True, True, "AI safety hooks remain optional and review-gated."),
    BackendFeature("system.health", "System Health", "system", "/admin/system", "admin", "system.view", "active", "Engineering", "production_hardening_engine", "admin_audit_logs", "critical", True, True, "Service status, env safety, and diagnostics."),
    BackendFeature("system.performance", "Performance", "system", "/admin/performance", "admin", "system.view", "active", "Engineering", "performance_monitor", "admin_audit_logs", "critical", True, True, "Latency and platform performance view."),
    BackendFeature("system.audit", "Audit Logs", "system", "/admin/audit-logs", "admin", "audit.view", "active", "Security", "admin_ai_assistant", "admin_audit_logs", "critical", True, True, "Audit trails for sensitive backend operations."),
)


FEATURES = FEATURES + (
    BackendFeature("network.friends", "Friends", "network", "/admin/network-command-center/friends", "moderator", "command_center.view", "active", "Community", "community_governance_engine", "admin_tasks", "medium", False, True, "Friend requests, accepted edges, cancelled requests, abuse protection, and relationship audit coverage."),
    BackendFeature("network.followers", "Followers / Following", "network", "/admin/network-command-center/followers", "moderator", "command_center.view", "active", "Community", "community_governance_engine", "admin_tasks", "medium", False, True, "Follower/following edges, pending requests, blocked relationships, and spike detection."),
    BackendFeature("network.blocks_mutes", "Blocks & Mutes", "network", "/admin/network-command-center/blocks-mutes", "moderator", "moderation.manage", "active", "Community Safety", "community_governance_engine", "admin_audit_logs", "high", True, True, "Block, unblock, mute, unmute, conversation mute, and enforcement diagnostics."),
    BackendFeature("network.bans", "Bans", "network", "/admin/network-command-center/bans", "moderator", "moderation.manage", "active", "Community Safety", "community_governance_engine", "admin_audit_logs", "critical", True, True, "Temporary bans, permanent bans, group bans, restrictions, and appeal-aware status."),
    BackendFeature("network.push_delivery", "Push Delivery", "network", "/admin/network-command-center/push-delivery", "admin", "command_center.view", "active", "Notifications", "notification_orchestrator", "notification_delivery_logs", "critical", True, True, "Push token registry, token health, provider responses, retries, and deep-link diagnostics."),
    BackendFeature("network.message_health", "Message Health", "network", "/admin/network-command-center/message-health", "moderator", "moderation.manage", "active", "Messaging", "chat_realtime_service", "admin_audit_logs", "critical", True, True, "Realtime delivery, read receipts, delivery receipts, failed messages, attachment health, and voice note health."),
    BackendFeature("network.audit_logs", "Network Audit Logs", "network", "/admin/network-command-center/audit", "admin", "audit.view", "active", "Security", "admin_ai_assistant", "admin_audit_logs", "critical", True, True, "Friend, follow, block, mute, ban, group, push, retry, and moderation audit visibility."),
    BackendFeature("network.community_activity", "Community Activity", "network", "/admin/network-command-center/community-activity", "moderator", "pulse.moderate", "active", "Community", "community_governance_engine", "moderation_cases", "high", True, True, "Recent discussions, popular posts, community momentum, trending communities, and moderation signals."),
    BackendFeature("network.network_health", "Network Health", "network", "/admin/network-command-center/network-health", "admin", "command_center.view", "active", "Network", "dashboard_network_command_center", "admin_audit_logs", "critical", True, True, "Connection, relationship, delivery, audience, community, communication, and trust health."),
    BackendFeature("network.delivery_intelligence", "Delivery Intelligence", "network", "/admin/network-command-center/delivery-intelligence", "admin", "command_center.view", "active", "Notifications", "notification_orchestrator", "notification_delivery_logs", "critical", True, True, "Push, email, SMS, Telegram, socket, realtime, retry, latency, and regional delivery health."),
    BackendFeature("network.notification_intelligence", "Notification Intelligence", "network", "/admin/network-command-center/notification-intelligence", "admin", "command_center.view", "active", "Notifications", "notification_orchestrator", "notification_delivery_logs", "high", True, True, "Notification fatigue, priority learning, delivery timing, quiet hours, ignored alerts, and high-value alerts."),
    BackendFeature("network.relationship_intelligence", "Relationship Intelligence", "network", "/admin/network-command-center/relationship-intelligence", "moderator", "command_center.view", "active", "Community", "community_governance_engine", "admin_tasks", "medium", False, True, "Strong connections, dormant relationships, frequently contacted people, reconnect suggestions, and trust score."),
    BackendFeature("network.connection_analytics", "Connection Analytics", "network", "/admin/network-command-center/connection-analytics", "admin", "analytics.view", "active", "Analytics", "dashboard_network_command_center", "admin_audit_logs", "medium", False, True, "Connection growth, retention, acceptance rate, friend conversion, follower conversion, and audience funnel."),
    BackendFeature("network.audience_mapping", "Audience Mapping", "network", "/admin/network-command-center/audience-mapping", "admin", "analytics.view", "active", "Analytics", "dashboard_network_command_center", "admin_audit_logs", "medium", False, True, "Interest clusters, creator communities, audience overlap, audience expansion, and privacy-safe distribution."),
    BackendFeature("network.growth_signals", "Growth Signals", "network", "/admin/network-command-center/growth-signals", "admin", "analytics.view", "active", "Growth", "dashboard_network_command_center", "admin_audit_logs", "medium", False, True, "Growth opportunities, recommended actions, audience momentum, creator momentum, and connection opportunities."),
    BackendFeature("network.delivery_matrix", "Pulse Delivery Matrix", "network", "/admin/network-command-center/delivery-matrix", "admin", "system.view", "active", "Infrastructure", "notification_orchestrator", "notification_delivery_logs", "critical", True, True, "Notifications/sec, messages/sec, provider success, queue size, retries, failures, regional status, and worker health."),
    BackendFeature("network.network_security", "Network Security", "network", "/admin/network-command-center/network-security", "admin", "security.view", "active", "Security", "security_monitoring", "admin_audit_logs", "critical", True, True, "Spam, scam, abuse, muted users, blocked users, hidden requests, privacy controls, and trust signals."),
    BackendFeature("network.community_intelligence", "Community Intelligence", "network", "/admin/network-command-center/community-intelligence", "moderator", "pulse.moderate", "active", "Community", "community_governance_engine", "moderation_cases", "high", True, True, "Community health, spam level, moderator health, growth, engagement, and suggested improvements."),
    BackendFeature("network.creator_reach", "Creator Reach", "network", "/admin/network-command-center/creator-reach", "admin", "analytics.view", "active", "Creator", "dashboard_network_command_center", "admin_audit_logs", "medium", False, True, "Reach, shares, audience spread, virality, engagement, and network expansion."),
    BackendFeature("network.connection_recovery", "Connection Recovery", "network", "/admin/network-command-center/connection-recovery", "moderator", "command_center.view", "active", "Community", "community_governance_engine", "admin_audit_logs", "high", True, True, "Failed requests, broken connections, lost followers, relationship recovery, and recovery recommendations."),
    BackendFeature("network.search", "Search Management", "network", "/admin/command-center/network", "admin", "command_center.view", "partial", "Search", "search_index_service", "admin_tasks", "high", True, True, "Search is registered for backend oversight; deeper index controls remain intentionally staged."),
    BackendFeature("creator.statuses", "Statuses", "creator", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Creator", "pulse_moderation_engine", "moderation_cases", "high", True, True, "Status media, reactions, and report handling are moderation-visible."),
    BackendFeature("creator.media_library", "Video / Reels / Status Media", "creator", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Creator", "media_service", "moderation_cases", "high", True, True, "Creator media inventory is managed through moderation and media storage controls."),
    BackendFeature("creator.monetization", "Creator Monetization", "creator", "/admin/departments/monetization", "admin", "monetization.manage", "partial", "Monetization", "creator_monetization_engine", "admin_tasks", "critical", True, True, "Monetization is visible and gated; iOS paid-digital compliance remains enforced separately."),
    BackendFeature("creator.audience_intelligence", "Audience Intelligence", "creator", "/admin/creator-command-center/audience-intelligence", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Audience growth, retention, conversion, and privacy-safe audience recommendations."),
    BackendFeature("creator.content_performance", "Content Performance", "creator", "/admin/creator-command-center/content-performance", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Cross-format performance, moderation state, saves, shares, comments, and completion signals."),
    BackendFeature("creator.timing_intelligence", "Timing Intelligence", "creator", "/admin/creator-command-center/best-posting-time", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Best posting time, schedule readiness, timing conflicts, and publish guidance."),
    BackendFeature("creator.creator_score", "Creator Score", "creator", "/admin/creator-command-center/creator-score", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Creator readiness, trust, consistency, media health, and moderation health."),
    BackendFeature("creator.creator_tools", "Creator Tools", "creator", "/admin/creator-command-center/creator-tools", "admin", "command_center.view", "active", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Publishing workspace, media tools, caption hooks, and workflow controls."),
    BackendFeature("creator.trend_intelligence", "Trend Intelligence", "creator", "/admin/creator-command-center/trend-intelligence", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Trend alignment, music fit, hashtag opportunity, and creator-safe recommendations."),
    BackendFeature("creator.content_planner", "Content Planner", "creator", "/admin/creator-command-center/content-planner", "admin", "command_center.view", "partial", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Planning console is functional and staged for richer scheduled content persistence."),
    BackendFeature("creator.post_scheduler", "Post Scheduler", "creator", "/admin/creator-command-center/post-scheduler", "admin", "command_center.view", "partial", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Scheduler diagnostics and timing state are visible; automated publish remains staged."),
    BackendFeature("creator.draft_studio", "Draft Studio", "creator", "/admin/creator-command-center/draft-studio", "admin", "command_center.view", "partial", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Draft inventory, recovery, safe deletion, and privacy state are diagnostics-ready."),
    BackendFeature("creator.ai_assistant", "Creator AI", "creator", "/admin/creator-command-center/ai-creator-assistant", "admin", "ai.view", "active", "AI", "dashboard_creator_command_center", "ai_usage_logs", "medium", False, True, "AI creator assistance is optional, gated, and audit-visible when enabled."),
    BackendFeature("creator.engagement_prediction", "Engagement Prediction", "creator", "/admin/creator-command-center/engagement-prediction", "admin", "analytics.view", "partial", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Predictive estimates are diagnostics-ready and clearly marked partial until model-backed."),
    BackendFeature("creator.reputation", "Creator Reputation", "creator", "/admin/creator-command-center/creator-reputation", "admin", "trust_safety.manage", "active", "Trust", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Creator trust, copyright state, warnings, reports, eligibility, and appeals."),
    BackendFeature("creator.viral_opportunities", "Viral Opportunity Scanner", "creator", "/admin/creator-command-center/viral-opportunity-scanner", "admin", "analytics.view", "partial", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Opportunity scanner is staged with safe aggregate signals and no private data exposure."),
    BackendFeature("moderation.content_removals", "Content Removals", "moderation", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Trust", "pulse_moderation_engine", "moderation_cases", "critical", True, True, "Removals, appeals, and status changes must stay reviewable and audited."),
    BackendFeature("ads.advertiser_portal", "Advertiser Portal", "ads", "/advertiser", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_audit_logs", "high", True, True, "Advertiser account, campaign, wallet, and creative workflows are registry-visible."),
    BackendFeature("ads.sponsored_layers", "Sci-Fi Sponsored Layers", "ads", "/admin/pulse-ads-delivery-intelligence", "admin", "analytics.view", "active", "Ads", "pulse_ads_service", "pulse_ad_events", "high", True, True, "UFO, hologram, radio, and sponsored placements are delivery-method tracked."),
    BackendFeature("ads.kill_switch", "Ads Kill Switch", "ads", "/admin/pulse-ads-review-board", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_platform_settings", "critical", True, True, "Ad serving can be disabled globally or by method without exposing internals."),
    BackendFeature("economy.wallets", "Wallet Management", "economy", "/admin/pulse-ad-finance", "admin", "billing.view", "active", "Finance", "pulse_ad_payments", "pulse_ad_wallet_transactions", "critical", True, True, "Ad wallet balances, funding sessions, reserves, and spend ledger are admin-visible."),
    BackendFeature("economy.subscriptions", "Premium / Subscriptions", "economy", "/admin/transactions", "admin", "billing.view", "active", "Billing", "premium_entitlement_service", "payment_audit_logs", "critical", True, True, "Subscriptions are observable while native iOS paid-digital routes stay blocked."),
    BackendFeature("economy.payouts", "Payouts / Refunds", "economy", "/admin/payments-command-center", "admin", "billing.view", "partial", "Finance", "payment_provider", "payment_audit_logs", "critical", True, True, "Payout and refund readiness is visible; risky provider mutations require owner approval."),
    BackendFeature("media.r2_storage", "Media Storage / R2", "media", "/admin/pulse-infrastructure", "admin", "system.view", "partial", "Media", "media_storage", "admin_tasks", "critical", True, True, "Storage health and configured/missing state are shown without exposing bucket credentials."),
    BackendFeature("media.pulse_radio_management", "Pulse Radio Management", "media", "/admin/pulse-music-review", "moderator", "pulse.moderate", "active", "Media", "music_service", "music_review_logs", "high", True, True, "Approved music automatically powers radio and creator-safe sound pools."),
    BackendFeature("media.marketplace_media", "Marketplace Media", "media", "/admin/departments/monetization", "admin", "monetization.manage", "partial", "Marketplace", "marketplace_engine", "admin_tasks", "medium", False, True, "Listing media is visible through marketplace operations."),
    BackendFeature("ai.routing", "AI Model Routing", "ai", "/admin/ai-usage", "admin", "ai.view", "partial", "AI", "ai_router", "ai_usage_logs", "high", False, True, "Provider routing is status-visible; provider credentials and provider credentials stay hidden."),
    BackendFeature("ai.safety_blocks", "AI Safety Blocks", "ai", "/admin/scam-shield", "admin", "trust_safety.manage", "active", "AI Safety", "autonomous_safety_engine", "command_center_ai_events", "critical", True, True, "Safety blocks and scam explanations remain review-gated."),
    BackendFeature("system.railway", "Railway Services", "system", "/admin/system", "admin", "system.view", "partial", "Infrastructure", "railway_runtime", "admin_audit_logs", "critical", True, True, "Deployment/service status is shown as configured/missing and must not expose Railway credentials."),
    BackendFeature("system.database", "PostgreSQL", "system", "/admin/system", "admin", "system.view", "active", "Infrastructure", "database", "admin_audit_logs", "critical", True, True, "Database health and compatibility audits are launch-critical."),
    BackendFeature("system.cache", "Cache / Redis", "system", "/admin/system", "admin", "system.view", "partial", "Infrastructure", "redis_manager", "admin_audit_logs", "critical", True, True, "Cache presence and latency are visible while PostgreSQL remains source of truth."),
    BackendFeature("system.workers", "Background Workers", "system", "/admin/system", "admin", "system.view", "partial", "Infrastructure", "command_center_worker", "admin_audit_logs", "critical", True, True, "Worker readiness, queue health, and fallback mode are backend-visible."),
    BackendFeature("system.scheduled_jobs", "Scheduled Jobs", "system", "/admin/system", "admin", "system.view", "partial", "Infrastructure", "scheduler", "admin_audit_logs", "high", True, True, "Cron and scheduled job coverage is visible; failures must route to admin review."),
    BackendFeature("system.feature_flags", "Feature Flags", "system", "/admin/system", "admin", "system.view", "active", "Engineering", "feature_flag_service", "admin_audit_logs", "critical", True, True, "Feature flags and rollout state are backend-managed."),
    BackendFeature("system.api_key_status", "API Keys / Credentials Status", "system", "/admin/system", "admin", "system.view", "active", "Security", "env_readiness", "admin_audit_logs", "critical", True, True, "Only configured/missing state is shown. Credential values are never rendered."),
    BackendFeature("system.firebase", "Firebase / FCM", "system", "/admin/notifications", "admin", "system.view", "partial", "Notifications", "push_service", "notification_delivery_logs", "critical", True, True, "Push provider readiness is visible without exposing private keys."),
    BackendFeature("system.stripe", "Stripe", "system", "/admin/payments-command-center", "admin", "billing.view", "partial", "Billing", "payment_provider", "payment_audit_logs", "critical", True, True, "Stripe health is visible while product IDs and credentials remain protected."),
    BackendFeature("system.brevo", "Brevo", "system", "/admin/notifications", "admin", "system.view", "partial", "Notifications", "email_provider", "notification_delivery_logs", "high", True, True, "Email provider readiness and failures are visible without exposing API keys."),
    BackendFeature("system.livekit", "LiveKit", "system", "/admin/pulse-infrastructure", "admin", "system.view", "partial", "Live", "live_stream_health_service", "admin_tasks", "critical", True, True, "LiveKit configured/missing status is visible for live streaming operations."),
    BackendFeature("system.mux", "Mux", "system", "/admin/pulse-infrastructure", "admin", "system.view", "partial", "Media", "mux_live_service", "admin_tasks", "critical", True, True, "Mux configured/missing status is visible for video/live processing."),
    BackendFeature("system.expo", "Expo / EAS", "system", "/admin/notifications", "admin", "system.view", "partial", "Mobile", "push_service", "notification_delivery_logs", "high", True, True, "Expo push and mobile build readiness are status-visible."),
    BackendFeature("system.app_store", "App Store Connect", "system", "/admin/system", "admin", "system.view", "partial", "Mobile", "app_store_review_workflow", "admin_tasks", "high", True, True, "App review status is tracked as an operational launch surface."),
    BackendFeature("system.google_play", "Google Play", "system", "/admin/system", "admin", "system.view", "planned", "Mobile", "play_store_workflow", "admin_tasks", "medium", False, True, "Google Play readiness is registered but not launch-critical for iOS submission."),
    BackendFeature("launch.readiness", "Launch Readiness", "launch", "/admin/launch-readiness", "admin", "command_center.view", "active", "Operations", "backend_management_registry", "backend_management_audit_events", "critical", True, True, "Strict launch readiness and backend gap visibility."),
    BackendFeature("launch.blockers", "Launch Blockers", "launch", "/admin/launch-readiness", "admin", "command_center.view", "active", "Operations", "backend_management_registry", "backend_management_audit_events", "critical", True, True, "Unmanaged or partial launch-critical systems are blockers until documented."),
    BackendFeature("launch.qa_evidence", "QA Evidence", "launch", "/admin/launch-readiness", "admin", "command_center.view", "partial", "QA", "qa_audit_scripts", "backend_management_audit_events", "critical", True, True, "QA evidence is tracked by report and audit scripts; browser screenshots remain external artifacts."),
    BackendFeature("controls.global_kill_switches", "Global Kill Switches", "controls", "/admin/system", "admin", "system.view", "partial", "Operations", "feature_flag_service", "admin_audit_logs", "critical", True, True, "High-risk global controls are visible and require owner-level approval before mutation."),
    BackendFeature("controls.ads_kill_switch", "Ads Kill Switch", "controls", "/admin/pulse-ads-review-board", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_platform_settings", "critical", True, True, "Ads can be disabled safely without touching unrelated systems."),
    BackendFeature("controls.notifications_pause", "Notification Delivery Pause", "controls", "/admin/notifications", "admin", "system.view", "partial", "Notifications", "notification_orchestrator", "notification_delivery_logs", "critical", True, True, "Provider pausing is visible; destructive changes require approval."),
    BackendFeature("audit.admin_actions", "Admin Actions", "audit", "/admin/audit-logs", "admin", "audit.view", "active", "Security", "admin_ai_assistant", "admin_audit_logs", "critical", True, True, "Admin actions are searchable and role-gated."),
    BackendFeature("audit.payment_actions", "Payment Audit", "audit", "/admin/payments-command-center", "admin", "billing.view", "active", "Finance", "payment_provider", "payment_audit_logs", "critical", True, True, "Money actions must remain idempotent and auditable."),
    BackendFeature("audit.ad_actions", "Ads Audit", "audit", "/admin/pulse-ads-review-board", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_audit_logs", "critical", True, True, "Ad moderation, delivery, wallet, and tracking actions are audit-backed."),
)


MODULE_OPERATING_BLUEPRINTS: dict[str, dict[str, Any]] = {
    "account": {
        "surface": "/admin/account-command",
        "operators": "Owner, admin, security, trust roles",
        "visible_state": "users, verification, profile updates, account health, sessions, restrictions",
        "actions": ["review", "revert", "restrict", "force logout", "audit"],
        "failure_behavior": "Sensitive actions fail closed and require audit log creation.",
    },
    "network": {
        "surface": "/admin/network-command-center",
        "operators": "Admin, moderator, support",
        "visible_state": "notifications, messages, friends, followers, groups, blocks, mutes, bans, push delivery, message health",
        "actions": ["inspect", "triage", "retry", "mute", "block", "escalate", "audit"],
        "failure_behavior": "Messaging falls back to polling; notifications log precise skip/failure reasons.",
    },
    "creator": {
        "surface": "/admin/pulse-moderation, /admin/pulse-analytics",
        "operators": "Moderator, creator ops, admin",
        "visible_state": "posts, reels, videos, statuses, live, creator analytics",
        "actions": ["review", "remove", "restore", "feature", "escalate"],
        "failure_behavior": "Unclear moderation decisions stay queued; content is not destroyed without audit.",
    },
    "moderation": {
        "surface": "/admin/pulse-moderation, /admin/security, /admin/scam-shield",
        "operators": "Trust and safety, moderators, owner",
        "visible_state": "reports, scam events, suspicious domains, account risk, removals",
        "actions": ["approve", "reject", "block", "mark safe", "investigate"],
        "failure_behavior": "Detection flags do not auto-ban; human review remains required.",
    },
    "ads": {
        "surface": "/admin/pulse-ads-review-board, /admin/pulse-ads-delivery-intelligence",
        "operators": "Ads ops, finance, owner",
        "visible_state": "creative review, campaigns, wallets, delivery methods, frequency caps",
        "actions": ["approve", "reject", "pause", "kill switch", "audit spend"],
        "failure_behavior": "Unapproved ads cannot serve; kill switch disables delivery safely.",
    },
    "economy": {
        "surface": "/admin/payments-command-center, /admin/pulse-ad-finance",
        "operators": "Finance admins and owner",
        "visible_state": "payments, wallets, subscriptions, refunds, payouts, marketplace money",
        "actions": ["inspect", "reconcile", "refund prepare", "pause", "audit"],
        "failure_behavior": "Money actions are idempotent and must not create negative balances.",
    },
    "media": {
        "surface": "/admin/pulse-music-review, /admin/pulse-infrastructure",
        "operators": "Media ops, moderator, admin",
        "visible_state": "uploads, approved music, Pulse Radio, R2/Mux health",
        "actions": ["approve", "reject", "quarantine", "repair", "audit"],
        "failure_behavior": "Unsafe media stays unavailable until reviewed; raw storage paths are hidden.",
    },
    "ai": {
        "surface": "/admin/ai-usage, /admin/scam-shield",
        "operators": "AI ops, security admins",
        "visible_state": "usage, failures, safety blocks, routing readiness",
        "actions": ["inspect", "disable", "explain risk", "audit"],
        "failure_behavior": "AI is optional and must fail unavailable without blocking core messaging/feed.",
    },
    "system": {
        "surface": "/admin/system, /admin/performance",
        "operators": "Engineering, owner",
        "visible_state": "Railway, database, cache, workers, provider readiness, app stores",
        "actions": ["health check", "diagnose", "restart externally", "disable feature", "audit"],
        "failure_behavior": "Secrets are never displayed; operational resource renames require approval.",
    },
    "launch": {
        "surface": "/admin/launch-readiness",
        "operators": "Owner, launch lead",
        "visible_state": "registered features, unmanaged gaps, QA coverage, blockers",
        "actions": ["review blockers", "open module", "run audit", "document risk"],
        "failure_behavior": "Launch readiness stays watch/blocked when launch-critical coverage is incomplete.",
    },
    "controls": {
        "surface": "/admin/system plus module-specific control rooms",
        "operators": "Owner-level admins",
        "visible_state": "kill switches, provider pauses, feature flags, risky operations",
        "actions": ["disable", "pause", "require approval", "audit"],
        "failure_behavior": "Risky changes require approval and must be audited.",
    },
    "audit": {
        "surface": "/admin/audit-logs",
        "operators": "Owner, audit admins",
        "visible_state": "admin, payment, ads, account, moderation, security actions",
        "actions": ["search", "export-ready review", "investigate", "escalate"],
        "failure_behavior": "Missing audit coverage is a launch readiness blocker.",
    },
}


EXTERNAL_SERVICE_CHECKS: tuple[dict[str, Any], ...] = (
    {"key": "railway", "label": "Railway", "module": "system", "env": ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_DEPLOYMENT_ID")},
    {"key": "postgres", "label": "PostgreSQL", "module": "system", "env": ("DATABASE_URL",)},
    {"key": "redis", "label": "Redis", "module": "system", "env": ("REDIS_URL",)},
    {"key": "cloudflare_r2", "label": "Cloudflare R2", "module": "media", "env": ("R2_BUCKET_NAME", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")},
    {"key": "stripe", "label": "Stripe", "module": "economy", "env": ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")},
    {"key": "brevo", "label": "Brevo", "module": "network", "env": ("BREVO_API_KEY",)},
    {"key": "firebase", "label": "Firebase / FCM", "module": "system", "env": ("FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY")},
    {"key": "apns", "label": "Apple APNs", "module": "system", "env": ("APNS_BUNDLE_ID", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_PRIVATE_KEY")},
    {"key": "expo", "label": "Expo / EAS", "module": "system", "env": ("EXPO_ACCESS_TOKEN",)},
    {"key": "livekit", "label": "LiveKit", "module": "system", "env": ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_URL")},
    {"key": "mux", "label": "Mux", "module": "system", "env": ("MUX_TOKEN_ID", "MUX_TOKEN_SECRET")},
    {"key": "app_store", "label": "App Store Connect", "module": "launch", "env": ("APP_STORE_CONNECT_KEY_ID", "APP_STORE_CONNECT_ISSUER_ID")},
    {"key": "google_play", "label": "Google Play", "module": "launch", "env": ("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON",)},
)


def all_features() -> list[dict[str, Any]]:
    return [feature.safe_dict() for feature in FEATURES]


def feature_by_key(feature_key: str) -> dict[str, Any] | None:
    for feature in FEATURES:
        if feature.feature_key == feature_key:
            return feature.safe_dict()
    return None


def _role_allows(admin: dict[str, Any] | None, feature: BackendFeature) -> bool:
    if not admin:
        return False
    role = str(admin.get("role") or "").lower()
    required = str(feature.required_role or "admin").lower()
    if role in {"owner", "super_admin"}:
        return True
    if required == "moderator" and role in {"admin", "pulse_moderator", "senior_moderator", "trust_safety_agent"}:
        return True
    if required == "admin" and role == "admin":
        return True
    return role == required


def visible_features(admin: dict[str, Any] | None, permission_checker=None) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for feature in FEATURES:
        if feature.status == "hidden":
            continue
        if not _role_allows(admin, feature):
            continue
        if permission_checker and not permission_checker(admin, feature.required_permission):
            continue
        visible.append(feature.safe_dict())
    return visible


def category_summary(features: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = features if features is not None else all_features()
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in REQUIRED_MODULES}
    for item in rows:
        grouped.setdefault(str(item.get("category")), []).append(item)
    summary: list[dict[str, Any]] = []
    for key, title in REQUIRED_MODULES.items():
        items = grouped.get(key, [])
        manageable = sum(1 for item in items if item.get("manageable_from_backend"))
        active = sum(1 for item in items if item.get("status") == "active")
        critical = sum(1 for item in items if item.get("launch_critical"))
        gaps = [item for item in items if item.get("status") in {"partial", "planned", "blocked"} or not item.get("manageable_from_backend")]
        score = 100 if not items else round(((active + manageable) / (len(items) * 2)) * 100)
        summary.append({
            "category": key,
            "title": title,
            "total": len(items),
            "active": active,
            "manageable": manageable,
            "launch_critical": critical,
            "gaps": len(gaps),
            "readiness_score": score,
            "risk_level": _module_risk(items),
        })
    return summary


def module_blueprint(category: str) -> dict[str, Any]:
    category = str(category or "").strip().lower()
    return MODULE_OPERATING_BLUEPRINTS.get(category, {
        "surface": "/admin/command-center",
        "operators": "Role-gated admins",
        "visible_state": "Registered feature status and audit coverage",
        "actions": ["inspect", "audit", "escalate"],
        "failure_behavior": "Unknown modules fail closed and remain hidden from unauthorized roles.",
    })


def service_readiness_from_env(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    import os

    source = env if env is not None else os.environ
    rows: list[dict[str, Any]] = []
    for check in EXTERNAL_SERVICE_CHECKS:
        env_names = tuple(check.get("env") or ())
        configured = [name for name in env_names if bool(source.get(name))]
        missing = [name for name in env_names if not source.get(name)]
        if not env_names:
            state = "not_tracked"
        elif len(configured) == len(env_names):
            state = "configured"
        elif configured:
            state = "partial"
        else:
            state = "missing"
        rows.append({
            "key": check["key"],
            "label": check["label"],
            "module": check["module"],
            "state": state,
            "configured_count": len(configured),
            "required_count": len(env_names),
            "missing_env_names": missing,
        })
    return rows


def operating_system_snapshot(features: list[dict[str, Any]] | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    rows = features if features is not None else all_features()
    modules = category_summary(rows)
    services = service_readiness_from_env(env)
    unmanaged = [item for item in rows if not item.get("manageable_from_backend")]
    partial = [item for item in rows if item.get("status") == "partial"]
    blocked = [item for item in rows if item.get("status") in {"blocked", "planned"}]
    audit_missing = [item for item in rows if not str(item.get("audit_log_table") or "").strip()]
    routes_missing = [item for item in rows if not str(item.get("route") or "").strip()]
    critical = [item for item in rows if item.get("launch_critical")]
    configured_services = [item for item in services if item.get("state") == "configured"]
    service_gaps = [item for item in services if item.get("state") in {"missing", "partial"}]
    module_cards: list[dict[str, Any]] = []
    for module in modules:
        blueprint = module_blueprint(str(module.get("category") or ""))
        state = "ONLINE" if module.get("risk_level") == "low" and not module.get("gaps") else "WATCH" if module.get("risk_level") in {"medium", "high"} else "CRITICAL"
        module_cards.append({
            **module,
            "state": state,
            "surface": blueprint["surface"],
            "operators": blueprint["operators"],
            "visible_state": blueprint["visible_state"],
            "actions": blueprint["actions"],
            "failure_behavior": blueprint["failure_behavior"],
        })
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "total_features": len(rows),
        "registered_modules": len([item for item in modules if item.get("total")]),
        "managed_features": len([item for item in rows if item.get("manageable_from_backend")]),
        "unmanaged_features": len(unmanaged),
        "partial_features": len(partial),
        "blocked_features": len(blocked),
        "critical_features": len(critical),
        "audit_missing": len(audit_missing),
        "route_missing": len(routes_missing),
        "external_services": services,
        "external_services_configured": len(configured_services),
        "external_service_gaps": len(service_gaps),
        "modules": module_cards,
        "risk_summary": {
            "critical": len([item for item in rows if item.get("risk_level") == "critical"]),
            "high": len([item for item in rows if item.get("risk_level") == "high"]),
            "medium": len([item for item in rows if item.get("risk_level") == "medium"]),
            "low": len([item for item in rows if item.get("risk_level") == "low"]),
        },
    }


def _module_risk(items: list[dict[str, Any]]) -> str:
    if any(item.get("risk_level") == "critical" and item.get("status") in {"partial", "blocked", "planned"} for item in items):
        return "critical"
    if any(item.get("risk_level") in {"critical", "high"} and item.get("status") != "active" for item in items):
        return "high"
    if any(item.get("status") != "active" for item in items):
        return "medium"
    return "low"


def launch_readiness() -> dict[str, Any]:
    features = all_features()
    critical = [item for item in features if item.get("launch_critical")]
    blocked = [item for item in critical if item.get("status") in {"blocked", "planned"} or not item.get("manageable_from_backend")]
    partial = [item for item in critical if item.get("status") == "partial"]
    active = [item for item in critical if item.get("status") == "active" and item.get("manageable_from_backend")]
    score = 100 if not critical else round((len(active) / len(critical)) * 100)
    os_snapshot = operating_system_snapshot(features)
    gaps = gap_audit()
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "score": score,
        "status": "ready" if not blocked and score >= 90 else "watch" if not blocked else "blocked",
        "critical_total": len(critical),
        "critical_active": len(active),
        "critical_partial": len(partial),
        "critical_blocked": len(blocked),
        "modules": category_summary(features),
        "remaining_gaps": gaps["gaps"],
        "total_features_discovered": os_snapshot["total_features"],
        "registered_modules": os_snapshot["registered_modules"],
        "managed_features": os_snapshot["managed_features"],
        "unmanaged_features": os_snapshot["unmanaged_features"],
        "audit_missing": os_snapshot["audit_missing"],
        "external_service_gaps": os_snapshot["external_service_gaps"],
        "strict_gap_count": gaps["missing_count"],
    }


def gap_audit() -> dict[str, Any]:
    features = all_features()
    keys = {item["feature_key"] for item in features}
    gaps: list[dict[str, Any]] = []
    expected = {
        "account.profile", "account.verification", "account.health", "account.security", "account.settings",
        "account.appeals", "account.audit_logs", "account.restrictions", "account.sessions", "account.devices",
        "network.notifications", "network.messages", "creator.posts", "creator.reels", "creator.videos",
        "moderation.reports", "moderation.security", "ads.review", "ads.delivery", "economy.payments",
        "media.music", "ai.usage", "system.health", "system.audit",
    }
    for key in sorted(expected - keys):
        gaps.append({"feature_key": key, "severity": "critical", "reason": "required feature missing from registry"})
    for item in features:
        reason = ""
        severity = "low"
        if not item.get("route"):
            reason = "missing backend route"
            severity = "critical" if item.get("launch_critical") else "high"
        elif not item.get("manageable_from_backend"):
            reason = "not manageable from backend"
            severity = "critical" if item.get("launch_critical") else "medium"
        elif item.get("status") in {"blocked", "planned"}:
            reason = f"status is {item.get('status')}"
            severity = "critical" if item.get("launch_critical") else "medium"
        elif item.get("status") == "partial":
            reason = "partial backend management surface"
            severity = "high" if item.get("launch_critical") else "medium"
        if reason:
            gaps.append({"feature_key": item["feature_key"], "severity": severity, "reason": reason, "route": item.get("route"), "category": item.get("category")})
        if not item.get("audit_log_table"):
            gaps.append({"feature_key": item["feature_key"], "severity": "critical" if item.get("launch_critical") else "high", "reason": "missing audit target", "route": item.get("route"), "category": item.get("category")})
    for service in service_readiness_from_env():
        if service.get("state") == "missing":
            gaps.append({"feature_key": f"external.{service['key']}", "severity": "high", "reason": f"{service['label']} env status missing", "route": "/admin/system", "category": service.get("module")})
        elif service.get("state") == "partial":
            gaps.append({"feature_key": f"external.{service['key']}", "severity": "medium", "reason": f"{service['label']} env status partial", "route": "/admin/system", "category": service.get("module")})
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "total_features": len(features),
        "gaps": gaps,
        "missing_count": len(gaps),
    }


def audit_standard() -> dict[str, Any]:
    return {
        "required_for_new_features": [
            "feature registry entry",
            "backend/admin route or intentional hidden status",
            "server-side role and permission gate",
            "audit log table or audit event target",
            "launch critical flag",
            "risk level",
            "owner/service mapping",
            "QA/audit script coverage",
        ],
        "do_not_launch_without": [
            "auth required",
            "owner/admin scoping",
            "no credential value exposure",
            "clear rollback or moderation action where applicable",
            "mobile and desktop admin usability",
        ],
    }


def ensure_schema(conn: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS backend_feature_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_key TEXT UNIQUE,
            display_name TEXT,
            category TEXT,
            route TEXT,
            required_role TEXT,
            status TEXT,
            owner TEXT,
            backend_service TEXT,
            audit_log_table TEXT,
            risk_level TEXT,
            launch_critical INTEGER DEFAULT 0,
            manageable_from_backend INTEGER DEFAULT 1,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS backend_management_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_admin_id INTEGER,
            action TEXT,
            feature_key TEXT,
            details_json TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()


def sync_registry(conn: Any) -> None:
    ensure_schema(conn)
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = conn.cursor()
    for feature in FEATURES:
        values = (
            feature.feature_key,
            feature.display_name,
            feature.category,
            feature.route,
            feature.required_role,
            feature.status,
            feature.owner,
            feature.backend_service,
            feature.audit_log_table,
            feature.risk_level,
            1 if feature.launch_critical else 0,
            1 if feature.manageable_from_backend else 0,
            now,
        )
        if db_service.IS_POSTGRES:
            cur.execute(
                """
                INSERT INTO backend_feature_registry
                (feature_key, display_name, category, route, required_role, status, owner, backend_service, audit_log_table, risk_level, launch_critical, manageable_from_backend, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (feature_key) DO UPDATE SET
                    display_name=EXCLUDED.display_name,
                    category=EXCLUDED.category,
                    route=EXCLUDED.route,
                    required_role=EXCLUDED.required_role,
                    status=EXCLUDED.status,
                    owner=EXCLUDED.owner,
                    backend_service=EXCLUDED.backend_service,
                    audit_log_table=EXCLUDED.audit_log_table,
                    risk_level=EXCLUDED.risk_level,
                    launch_critical=EXCLUDED.launch_critical,
                    manageable_from_backend=EXCLUDED.manageable_from_backend,
                    updated_at=EXCLUDED.updated_at
                """,
                values,
            )
        else:
            cur.execute(
                """
                INSERT OR REPLACE INTO backend_feature_registry
                (feature_key, display_name, category, route, required_role, status, owner, backend_service, audit_log_table, risk_level, launch_critical, manageable_from_backend, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
    conn.commit()
