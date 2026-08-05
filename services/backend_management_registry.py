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
    "intelligence": "Intelligence Command Center",
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
    BackendFeature("network.calls", "Calls", "network", "/admin/calls", "admin", "system.view", "active", "Communications", "pulsesoc_communications_engine", "communication_call_events", "critical", True, True, "Voice/video call records, participants, LiveKit readiness, notifications, push delivery, timelines, quality reports, failures, and admin force-end diagnostics."),
    BackendFeature("network.groups", "Groups", "network", "/admin/network-command-center/groups", "moderator", "command_center.view", "active", "Community", "community_governance_engine", "admin_tasks", "medium", False, True, "Group memberships, roles, reports, bans, mutes, and group health command surface."),
    BackendFeature("network.status_activity", "Status Activity", "network", "/admin/network-command-center/status-activity", "moderator", "pulse.moderate", "active", "PulseSoc", "pulse_moderation_engine", "moderation_cases", "high", True, True, "Status reports, viewer analytics, completion, replies, shares, and content moderation flow through PulseSoc moderation."),
    BackendFeature("creator.posts", "Posts", "creator", "/admin/creator-command-center/posts", "moderator", "pulse.moderate", "active", "Creator", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Posts are reviewable and manageable through the Creator Command Center and moderation tools."),
    BackendFeature("creator.reels", "Reels", "creator", "/admin/creator-command-center/reels", "moderator", "pulse.moderate", "active", "Creator", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Reels use creator diagnostics, PulseSoc content moderation, and ranking audits."),
    BackendFeature("creator.videos", "Videos", "creator", "/admin/creator-command-center/videos", "moderator", "pulse.moderate", "active", "Creator", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Video review, playback health, and processing state are moderation-visible."),
    BackendFeature("creator.live", "Live Studio", "creator", "/admin/creator-command-center/live-studio", "admin", "system.view", "active", "Live", "dashboard_creator_command_center", "admin_tasks", "critical", True, True, "Live readiness, stream status, provider health, and reports are visible through creator management."),
    BackendFeature("creator.analytics", "Creator Analytics", "creator", "/admin/creator-command-center/analytics", "admin", "analytics.view", "active", "Analytics", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Platform analytics can inspect creator activity safely."),
    BackendFeature("moderation.reports", "Reports Queue", "moderation", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Trust", "pulse_moderation_engine", "moderation_cases", "critical", True, True, "Core report review queue."),
    BackendFeature("moderation.security", "Security Events", "moderation", "/admin/security", "admin", "security.view", "active", "Security", "security_monitoring", "admin_audit_logs", "critical", True, True, "Failed login and security monitoring dashboard."),
    BackendFeature("moderation.scam_shield", "Scam Shield", "moderation", "/admin/scam-shield", "admin", "trust_safety.manage", "active", "Trust", "autonomous_safety_engine", "security_events", "critical", True, True, "Scam and suspicious activity command surface."),
    BackendFeature("ads.review", "Ads Review Board", "ads", "/admin/pulse-ads-review-board", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_review_board", "critical", True, True, "Creative review, approval, rejection, and campaign safety."),
    BackendFeature("ads.delivery", "Ad Delivery", "ads", "/admin/ads-command-center/delivery-engine", "admin", "analytics.view", "active", "Ads", "pulse_ads_service", "pulse_ad_events", "high", True, True, "Delivery methods, tracking, and placement control."),
    BackendFeature("ads.finance", "Ad Wallets", "ads", "/admin/financial-audit", "admin", "billing.view", "active", "Ads Finance", "pulse_ad_payments", "pulse_ad_wallet_transactions", "critical", True, True, "Funding, spend ledger, and advertiser finance oversight."),
    BackendFeature("economy.payments", "Payments", "economy", "/admin/payments-command-center", "admin", "billing.view", "active", "Billing", "payment_provider", "admin_audit_logs", "critical", True, True, "Payment command center."),
    BackendFeature("economy.premium", "Premium", "economy", "/admin/transactions", "admin", "billing.view", "active", "Billing", "premium_entitlement_service", "checkout_attempts", "critical", True, True, "Subscription and entitlement review."),
    BackendFeature("economy.marketplace", "Marketplace", "economy", "/admin/monetization", "admin", "monetization.manage", "partial", "Marketplace", "marketplace_engine", "admin_tasks", "high", False, True, "Marketplace controls are routed through monetization command tasks."),
    BackendFeature("media.music", "Music Review", "media", "/admin/pulse-music-review", "moderator", "pulse.moderate", "active", "Media", "music_service", "pulse_music_events", "high", True, True, "Uploaded music review and approval queue."),
    BackendFeature("media.uploads", "Uploads", "media", "/admin/pulse-infrastructure", "admin", "system.view", "partial", "Media", "media_storage", "admin_tasks", "critical", True, True, "Storage and upload health are surfaced through infrastructure."),
    BackendFeature("media.radio", "Pulse Radio", "media", "/admin/departments/pulsesoc", "admin", "command_center.view", "active", "Media", "music_service", "admin_tasks", "medium", False, True, "Radio is powered by approved music pool and backend command tasks."),
    BackendFeature("ai.usage", "AI Usage", "ai", "/admin/ai-usage", "admin", "ai.view", "active", "AI", "ai_router", "pulse_ai_provider_events", "high", False, True, "AI usage and provider visibility."),
    BackendFeature("ai.safety", "AI Safety", "ai", "/admin/scam-shield", "admin", "trust_safety.manage", "partial", "AI Safety", "autonomous_safety_engine", "pulse_ai_provider_events", "critical", True, True, "AI safety hooks remain optional and review-gated."),
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
    BackendFeature("creator.monetization", "Creator Monetization", "creator", "/admin/monetization", "admin", "monetization.manage", "partial", "Monetization", "creator_monetization_engine", "admin_tasks", "critical", True, True, "Monetization is visible and gated; iOS paid-digital compliance remains enforced separately."),
    BackendFeature("creator.audience_intelligence", "Audience Intelligence", "creator", "/admin/creator-command-center/audience-intelligence", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Audience growth, retention, conversion, and privacy-safe audience recommendations."),
    BackendFeature("creator.content_performance", "Content Performance", "creator", "/admin/creator-command-center/content-performance", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Cross-format performance, moderation state, saves, shares, comments, and completion signals."),
    BackendFeature("creator.timing_intelligence", "Timing Intelligence", "creator", "/admin/creator-command-center/best-posting-time", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Best posting time, schedule readiness, timing conflicts, and publish guidance."),
    BackendFeature("creator.creator_score", "Creator Score", "creator", "/admin/creator-command-center/creator-score", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Creator readiness, trust, consistency, media health, and moderation health."),
    BackendFeature("creator.creator_tools", "Creator Tools", "creator", "/admin/creator-command-center/creator-tools", "admin", "command_center.view", "active", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Publishing workspace, media tools, caption hooks, and workflow controls."),
    BackendFeature("creator.trend_intelligence", "Trend Intelligence", "creator", "/admin/creator-command-center/trend-intelligence", "admin", "analytics.view", "active", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Trend alignment, music fit, hashtag opportunity, and creator-safe recommendations."),
    BackendFeature("creator.content_planner", "Content Planner", "creator", "/admin/creator-command-center/content-planner", "admin", "command_center.view", "partial", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Planning console is functional and staged for richer scheduled content persistence."),
    BackendFeature("creator.post_scheduler", "Post Scheduler", "creator", "/admin/creator-command-center/post-scheduler", "admin", "command_center.view", "partial", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Scheduler diagnostics and timing state are visible; automated publish remains staged."),
    BackendFeature("creator.draft_studio", "Draft Studio", "creator", "/admin/creator-command-center/draft-studio", "admin", "command_center.view", "partial", "Creator", "dashboard_creator_command_center", "admin_tasks", "medium", False, True, "Draft inventory, recovery, safe deletion, and privacy state are diagnostics-ready."),
    BackendFeature("creator.ai_assistant", "Creator AI", "creator", "/admin/creator-command-center/ai-creator-assistant", "admin", "ai.view", "active", "AI", "dashboard_creator_command_center", "pulse_ai_provider_events", "medium", False, True, "AI creator assistance is optional, gated, and audit-visible when enabled."),
    BackendFeature("creator.engagement_prediction", "Engagement Prediction", "creator", "/admin/creator-command-center/engagement-prediction", "admin", "analytics.view", "partial", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Predictive estimates are diagnostics-ready and clearly marked partial until model-backed."),
    BackendFeature("creator.reputation", "Creator Reputation", "creator", "/admin/creator-command-center/creator-reputation", "admin", "trust_safety.manage", "active", "Trust", "dashboard_creator_command_center", "moderation_cases", "high", True, True, "Creator trust, copyright state, warnings, reports, eligibility, and appeals."),
    BackendFeature("creator.viral_opportunities", "Viral Opportunity Scanner", "creator", "/admin/creator-command-center/viral-opportunity-scanner", "admin", "analytics.view", "partial", "Creator", "dashboard_creator_command_center", "admin_audit_logs", "medium", False, True, "Opportunity scanner is staged with safe aggregate signals and no private data exposure."),
    BackendFeature("moderation.content_removals", "Content Removals", "moderation", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Trust", "pulse_moderation_engine", "moderation_cases", "critical", True, True, "Removals, appeals, and status changes must stay reviewable and audited."),
    BackendFeature("ads.advertiser_portal", "Advertiser Portal", "ads", "/advertiser", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_audit_logs", "high", True, True, "Advertiser account, campaign, wallet, and creative workflows are registry-visible."),
    BackendFeature("ads.sponsored_layers", "Sci-Fi Sponsored Layers", "ads", "/admin/ads-command-center/delivery-engine", "admin", "analytics.view", "active", "Ads", "pulse_ads_service", "pulse_ad_events", "high", True, True, "UFO, hologram, radio, and sponsored placements are delivery-method tracked."),
    BackendFeature("ads.kill_switch", "Ads Kill Switch", "ads", "/admin/pulse-ads-review-board", "admin", "command_center.view", "active", "Ads", "pulse_ads_service", "pulse_ad_platform_settings", "critical", True, True, "Ad serving can be disabled globally or by method without exposing internals."),
    BackendFeature("economy.wallets", "Wallet Management", "economy", "/admin/economy-command-center/wallets", "admin", "billing.view", "active", "Finance", "pulse_ad_payments", "pulse_ad_wallet_transactions", "critical", True, True, "Ad wallet balances, funding sessions, reserves, and spend ledger are admin-visible."),
    BackendFeature("economy.subscriptions", "Premium / Subscriptions", "economy", "/admin/transactions", "admin", "billing.view", "active", "Billing", "premium_entitlement_service", "payment_audit_logs", "critical", True, True, "Subscriptions are observable while native iOS paid-digital routes stay blocked."),
    BackendFeature("economy.payouts", "Payouts / Refunds", "economy", "/admin/payments-command-center", "admin", "billing.view", "partial", "Finance", "payment_provider", "payment_audit_logs", "critical", True, True, "Payout and refund readiness is visible; risky provider mutations require owner approval."),
    BackendFeature("media.r2_storage", "Media Storage / R2", "media", "/admin/pulse-infrastructure", "admin", "system.view", "partial", "Media", "media_storage", "admin_tasks", "critical", True, True, "Storage health and configured/missing state are shown without exposing bucket credentials."),
    BackendFeature("media.pulse_radio_management", "Pulse Radio Management", "media", "/admin/pulse-music-review", "moderator", "pulse.moderate", "active", "Media", "music_service", "pulse_music_events", "high", True, True, "Approved music automatically powers radio and creator-safe sound pools."),
    BackendFeature("media.marketplace_media", "Marketplace Media", "media", "/admin/monetization", "admin", "monetization.manage", "partial", "Marketplace", "marketplace_engine", "admin_tasks", "medium", False, True, "Listing media is visible through marketplace operations."),
    BackendFeature("ai.routing", "AI Model Routing", "ai", "/admin/ai-usage", "admin", "ai.view", "partial", "AI", "ai_router", "pulse_ai_provider_events", "high", False, True, "Provider routing is status-visible; provider credentials and provider credentials stay hidden."),
    BackendFeature("ai.safety_blocks", "AI Safety Blocks", "ai", "/admin/scam-shield", "admin", "trust_safety.manage", "active", "AI Safety", "autonomous_safety_engine", "pulse_ai_provider_events", "critical", True, True, "Safety blocks and scam explanations remain review-gated."),
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


FEATURES = FEATURES + (
    BackendFeature("intelligence.scam_shield", "Scam Intelligence", "intelligence", "/admin/intelligence-command-center/scam-intelligence", "admin", "trust_safety.manage", "active", "Trust", "dashboard_intelligence_command_center", "security_events", "critical", True, True, "Scam patterns, fake account/giveaway/crypto/job/marketplace/link signals, community reports, and safety recommendations."),
    BackendFeature("intelligence.alerts", "Alert Management", "intelligence", "/admin/intelligence-command-center/alert-management", "admin", "command_center.view", "active", "Notifications", "dashboard_intelligence_command_center", "notification_delivery_logs", "high", True, True, "Active/local/global/trending scam alerts, priority, dismissals, notification queue integration, and alert audit."),
    BackendFeature("intelligence.pulse_brain", "Pulse Brain", "intelligence", "/admin/intelligence-command-center/pulse-brain", "admin", "analytics.view", "active", "Intelligence", "dashboard_intelligence_command_center", "admin_audit_logs", "high", True, True, "Community mood, platform health, topics, creators, safety signals, summaries, and daily briefing state."),
    BackendFeature("intelligence.ai_advisor", "AI Advisor", "intelligence", "/admin/intelligence-command-center/ai-advisor", "admin", "ai.view", "partial", "AI", "dashboard_intelligence_command_center", "pulse_ai_provider_events", "high", True, True, "Daily recommendations, suggested actions, explanations, and provider-disabled safe behavior."),
    BackendFeature("intelligence.safety_scanner", "Safety Scanner", "intelligence", "/admin/intelligence-command-center/safety-scanner", "admin", "security.view", "active", "Security", "dashboard_intelligence_command_center", "security_events", "critical", True, True, "Message, link, file, device, session, suspicious activity, recovery action, and threat integration scans."),
    BackendFeature("intelligence.recommendations", "Recommendation Engine", "intelligence", "/admin/intelligence-command-center/recommendation-engine", "admin", "analytics.view", "partial", "Recommendations", "dashboard_intelligence_command_center", "dashboard_recommendations", "medium", True, True, "Privacy-safe people, groups, content, marketplace, music, creator suggestions, and ranking signals."),
    BackendFeature("intelligence.security_operations", "Security Operations", "intelligence", "/admin/intelligence-command-center/security-operations", "admin", "security.view", "active", "Security", "dashboard_intelligence_command_center", "admin_audit_logs", "critical", True, True, "Overall safety score, checklist, device/login/privacy health, recovery status, and security timeline."),
    BackendFeature("intelligence.threats", "Threat Intelligence", "intelligence", "/admin/intelligence-command-center/threat-intelligence", "admin", "security.view", "active", "Security", "dashboard_intelligence_command_center", "security_events", "critical", True, True, "Current threats, timelines, suspicious accounts, blocked threats, emerging risks, severity, and resolution history."),
    BackendFeature("intelligence.risk", "Risk Assessment", "intelligence", "/admin/intelligence-command-center/risk-assessment", "admin", "security.view", "partial", "Risk", "dashboard_intelligence_command_center", "security_events", "critical", True, True, "Account, device, network, financial, reputation, marketplace, confidence, and timeline risk."),
    BackendFeature("intelligence.trust", "Trust Intelligence", "intelligence", "/admin/intelligence-command-center/trust-intelligence", "admin", "trust_safety.manage", "partial", "Trust", "dashboard_intelligence_command_center", "moderation_cases", "high", True, True, "Reputation, trust, reports, copyright, violations, appeals, improvement plan, and trust timeline."),
    BackendFeature("intelligence.signals", "Signal Intelligence", "intelligence", "/admin/intelligence-command-center/signal-intelligence", "admin", "analytics.view", "partial", "Signals", "dashboard_intelligence_command_center", "admin_audit_logs", "medium", True, True, "Feed, community, trend, creator, engagement, safety, and recommendation signal processing."),
    BackendFeature("intelligence.research", "Research Engine", "intelligence", "/admin/intelligence-command-center/research-engine", "admin", "ai.view", "partial", "AI", "dashboard_intelligence_command_center", "pulse_ai_provider_events", "medium", True, True, "Topic research, source summaries, saved research, citations, export readiness, and usage limits."),
    BackendFeature("intelligence.feed", "Feed Intelligence", "intelligence", "/admin/intelligence-command-center/feed-intelligence", "admin", "analytics.view", "partial", "Feed", "dashboard_intelligence_command_center", "admin_audit_logs", "medium", True, True, "Feed summaries, hidden trends, recommended reading, creator opportunities, and personalized briefing."),
    BackendFeature("intelligence.predictions", "Prediction Engine", "intelligence", "/admin/intelligence-command-center/prediction-engine", "admin", "analytics.view", "partial", "Predictions", "dashboard_intelligence_command_center", "dashboard_recommendations", "medium", True, True, "Future risks, opportunities, creator predictions, trend forecasts, confidence levels, and history."),
    BackendFeature("intelligence.heatmaps", "Heatmap Engine", "intelligence", "/admin/intelligence-command-center/heatmap-engine", "admin", "analytics.view", "partial", "Heatmaps", "dashboard_intelligence_command_center", "admin_audit_logs", "medium", True, True, "Aggregate-only global/community/topic/engagement/safety/discovery heatmaps."),
    BackendFeature("intelligence.audit", "Intelligence Audit Logs", "intelligence", "/admin/intelligence-command-center/audit", "admin", "audit.view", "active", "Security", "admin_ai_assistant", "admin_audit_logs", "critical", True, True, "Sensitive intelligence actions, recommendations, scans, alerts, and admin changes remain audit-visible."),
)


FEATURES = FEATURES + (
    BackendFeature("economy.wallet_os", "Wallets", "economy", "/admin/economy-command-center/wallets", "admin", "billing.view", "active", "Finance", "dashboard_economy_command_center", "payment_audit_logs", "critical", True, True, "Wallet balances, holds, reserves, credits, refunds, permissions, fraud protection, and timeline review."),
    BackendFeature("economy.transactions", "Transactions", "economy", "/admin/economy-command-center/transactions", "admin", "billing.view", "active", "Finance", "dashboard_economy_command_center", "payment_audit_logs", "critical", True, True, "Transaction search, reconciliation, payment failures, duplicate protection, and money audit visibility."),
    BackendFeature("economy.orders", "Orders", "economy", "/admin/economy-command-center/orders", "admin", "billing.view", "partial", "Marketplace", "dashboard_economy_command_center", "admin_audit_logs", "high", True, True, "Marketplace order state, pending orders, fulfillment, refunds, disputes, and fraud handoff."),
    BackendFeature("economy.sellers", "Sellers", "economy", "/admin/economy-command-center/sellers", "admin", "monetization.manage", "active", "Marketplace", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Seller onboarding, KYC readiness, tax profile, payout setup, seller trust, violations, and appeals."),
    BackendFeature("economy.products", "Products", "economy", "/admin/economy-command-center/products", "admin", "monetization.manage", "partial", "Marketplace", "dashboard_economy_command_center", "admin_audit_logs", "high", True, True, "Product inventory, pricing, demand, policy flags, metadata, and review state."),
    BackendFeature("economy.subscriptions_os", "Subscriptions", "economy", "/admin/economy-command-center/subscriptions", "admin", "billing.view", "active", "Billing", "dashboard_economy_command_center", "checkout_attempts", "critical", True, True, "Subscription state, benefits, invoices, renewals, cancellations, entitlements, and App Store-safe boundaries."),
    BackendFeature("economy.premium_os", "Premium", "economy", "/admin/economy-command-center/premium", "admin", "billing.view", "active", "Billing", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Premium benefits, entitlement history, recommendations, iOS compliance state, and upgrade advisory controls."),
    BackendFeature("economy.payouts_os", "Payouts", "economy", "/admin/economy-command-center/payouts", "admin", "billing.view", "partial", "Finance", "dashboard_economy_command_center", "payment_audit_logs", "critical", True, True, "Payout readiness, bank verification state without raw bank data, payout limits, failed payout retries, and approval workflow."),
    BackendFeature("economy.revenue", "Revenue", "economy", "/admin/economy-command-center/revenue", "admin", "analytics.view", "active", "Finance Analytics", "dashboard_economy_command_center", "admin_audit_logs", "high", True, True, "Creator, marketplace, subscription, advertising, affiliate, projections, and revenue trend analytics."),
    BackendFeature("economy.affiliate", "Affiliate", "economy", "/admin/economy-command-center/affiliate", "admin", "monetization.manage", "partial", "Growth", "dashboard_economy_command_center", "admin_audit_logs", "medium", False, True, "Referrals, commissions, conversions, pending/completed payouts, and campaign performance readiness."),
    BackendFeature("economy.marketplace_os", "Marketplace", "economy", "/admin/economy-command-center/marketplace", "admin", "monetization.manage", "active", "Marketplace", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Products, orders, inventory, shipping, digital products, refunds, disputes, reviews, reputation, and fraud detection."),
    BackendFeature("economy.taxes", "Taxes", "economy", "/admin/economy-command-center/taxes", "admin", "billing.view", "partial", "Finance Compliance", "dashboard_economy_command_center", "payment_audit_logs", "critical", True, True, "Tax status, forms readiness, withholding readiness, and owner-scoped display boundaries."),
    BackendFeature("economy.fraud", "Fraud", "economy", "/admin/economy-command-center/fraud", "admin", "security.view", "active", "Risk", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Fraud scoring, payment risk, suspicious transactions, chargeback signals, AML readiness, and admin review handoff."),
    BackendFeature("economy.refunds", "Refunds", "economy", "/admin/economy-command-center/refunds", "admin", "billing.view", "active", "Finance", "dashboard_economy_command_center", "payment_audit_logs", "critical", True, True, "Refund queue, refund credits, duplicate protection, status, audit, and rollback readiness."),
    BackendFeature("economy.chargebacks", "Chargebacks", "economy", "/admin/economy-command-center/chargebacks", "admin", "billing.view", "partial", "Finance Risk", "dashboard_economy_command_center", "payment_audit_logs", "critical", True, True, "Chargeback state, seller trust impact, revenue correction, evidence readiness, and fraud escalation."),
    BackendFeature("economy.payment_providers", "Payment Providers", "economy", "/admin/economy-command-center/payment-providers", "admin", "billing.view", "active", "Finance Infrastructure", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Provider health without secrets, Stripe/IAP/Play Billing readiness, failures, and compliance status."),
    BackendFeature("economy.stripe", "Stripe", "economy", "/admin/economy-command-center/stripe", "admin", "billing.view", "active", "Finance Infrastructure", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Stripe readiness, checkout/webhook status, idempotency state, and iOS-safe redaction boundaries."),
    BackendFeature("economy.apple_iap", "Apple IAP", "economy", "/admin/economy-command-center/apple-iap", "admin", "billing.view", "partial", "Finance Compliance", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Apple IAP readiness and compliance controls; native iOS paid digital access stays blocked until approved."),
    BackendFeature("economy.google_play_billing", "Google Play Billing", "economy", "/admin/economy-command-center/google-play-billing", "admin", "billing.view", "partial", "Finance Compliance", "dashboard_economy_command_center", "admin_audit_logs", "critical", True, True, "Google Play Billing readiness, platform compliance, and safe web/native separation."),
    BackendFeature("economy.audit_logs", "Economy Audit Logs", "economy", "/admin/economy-command-center/audit", "admin", "audit.view", "active", "Finance Security", "dashboard_economy_command_center", "payment_audit_logs", "critical", True, True, "Money, seller, payout, refund, chargeback, fraud, provider, and admin action audit visibility."),
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
    "intelligence": {
        "surface": "/admin/intelligence-command-center",
        "operators": "Owner, admin, AI ops, trust and safety",
        "visible_state": "scam shield, alerts, Pulse Brain, AI Advisor, safety scan, recommendations, threat, risk, trust, signals, research, feed, predictions, heatmaps",
        "actions": ["analyze", "explain", "triage", "recommend", "review", "audit"],
        "failure_behavior": "AI and prediction features fail closed to safe unavailable states; safety and scam signals remain review-gated.",
    },
    "moderation": {
        "surface": "/admin/pulse-moderation, /admin/security, /admin/scam-shield",
        "operators": "Trust and safety, moderators, owner",
        "visible_state": "reports, scam events, suspicious domains, account risk, removals",
        "actions": ["approve", "reject", "block", "mark safe", "investigate"],
        "failure_behavior": "Detection flags do not auto-ban; human review remains required.",
    },
    "ads": {
        "surface": "/admin/pulse-ads-review-board, /admin/ads-command-center/delivery-engine",
        "operators": "Ads ops, finance, owner",
        "visible_state": "creative review, campaigns, wallets, delivery methods, frequency caps",
        "actions": ["approve", "reject", "pause", "kill switch", "audit spend"],
        "failure_behavior": "Unapproved ads cannot serve; kill switch disables delivery safely.",
    },
    "economy": {
        "surface": "/admin/economy-command-center",
        "operators": "Finance admins and owner",
        "visible_state": "wallets, transactions, orders, sellers, products, subscriptions, premium, payouts, revenue, affiliate, marketplace, taxes, fraud, refunds, chargebacks, providers, audit logs",
        "actions": ["inspect", "reconcile", "reserve", "release reserve", "refund prepare", "fraud review", "provider check", "audit"],
        "failure_behavior": "Money actions are idempotent, fail closed, avoid negative balances, and never expose raw provider identifiers or payment secrets.",
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


# Each `env` entry is either a variable name or a tuple of interchangeable names,
# any one of which satisfies the requirement. The alias form exists because the
# runtime accepts aliases: services/media_storage.py resolves the bucket as
# `R2_BUCKET or S3_BUCKET` and the credentials as `R2_* or AWS_*`. A readiness row
# that names only one spelling reports a working deployment as unconfigured.
#
# `scope` separates what the running web service reads from what only a release
# pipeline reads. Both belong on this page - an operator needs to know a build
# credential is missing - but a build credential absent from the web service's
# environment is not a runtime degradation, and previously it was shown as one.
#
# Every "runtime" name below is verified by tests/protection/test_environment_contract.py
# to be a variable production code actually reads. `R2_BUCKET_NAME` was listed here
# for the life of this file and is read by nothing; the Cloudflare R2 row could
# therefore never reach "configured".
EXTERNAL_SERVICE_CHECKS: tuple[dict[str, Any], ...] = (
    {"key": "railway", "label": "Railway", "module": "system", "scope": "runtime", "env": ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_DEPLOYMENT_ID")},
    {"key": "postgres", "label": "PostgreSQL", "module": "system", "scope": "runtime", "env": ("DATABASE_URL",)},
    {"key": "redis", "label": "Redis", "module": "system", "scope": "runtime", "env": ("REDIS_URL",)},
    {"key": "cloudflare_r2", "label": "Cloudflare R2", "module": "media", "scope": "runtime", "env": (
        ("R2_BUCKET", "S3_BUCKET"),
        ("R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
        ("R2_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
        ("R2_ENDPOINT_URL", "R2_ENDPOINT", "R2_ACCOUNT_ID", "S3_ENDPOINT_URL"),
        "R2_PUBLIC_BASE_URL",
    )},
    {"key": "stripe", "label": "Stripe", "module": "economy", "scope": "runtime", "env": ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")},
    {"key": "brevo", "label": "Brevo", "module": "network", "scope": "runtime", "env": ("BREVO_API_KEY",)},
    {"key": "firebase", "label": "Firebase / FCM", "module": "system", "scope": "runtime", "env": ("FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY")},
    {"key": "apns", "label": "Apple APNs", "module": "system", "scope": "runtime", "env": ("APNS_BUNDLE_ID", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_PRIVATE_KEY")},
    {"key": "livekit", "label": "LiveKit", "module": "system", "scope": "runtime", "env": ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_URL")},
    {"key": "mux", "label": "Mux", "module": "system", "scope": "runtime", "env": ("MUX_TOKEN_ID", "MUX_TOKEN_SECRET")},
    # Build and release credentials. Read by EAS and the store upload steps, never
    # by this web service, so they are expected to be absent from the Railway
    # runtime environment and must not be counted as a runtime provider outage.
    {"key": "expo", "label": "Expo / EAS", "module": "system", "scope": "build", "env": ("EXPO_ACCESS_TOKEN",)},
    {"key": "app_store", "label": "App Store Connect", "module": "launch", "scope": "build", "env": ("APP_STORE_CONNECT_KEY_ID", "APP_STORE_CONNECT_ISSUER_ID")},
    {"key": "google_play", "label": "Google Play", "module": "launch", "scope": "build", "env": ("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON",)},
)


def all_features() -> list[dict[str, Any]]:
    return [feature.safe_dict() for feature in FEATURES]


# --- Runtime verification --------------------------------------------------
#
# Every field on a BackendFeature is a hand-written literal. `status="active"`
# is a claim a developer typed, not a fact anyone measured. Left alone, the
# Launch Readiness card can only ever report "Blocked: 0", because no code path
# in this module has ever been able to produce a blocked feature - a subsystem
# can 404 in production, or log to a table that was never created, and the
# registry will keep reporting it green.
#
# These helpers close that gap. They compare the registry's declarations
# against two runtime facts supplied by the caller: which URL rules the Flask
# app actually registered, and which tables the database actually has. A
# feature that fails verification is downgraded to "blocked" regardless of what
# its literal says.

def _rule_matcher(registered_rules):
    """Return a predicate that tells whether a declared route is reachable.

    Flask rules carry converters (`/admin/calls/<path:call_id>`), so an exact
    string comparison under-reports. Declared routes are matched literally
    first, then against parameterised rules.
    """
    import re as _re

    literals = {str(rule) for rule in (registered_rules or [])}
    patterns = []
    for rule in literals:
        if "<" not in rule:
            continue
        try:
            patterns.append(_re.compile("^" + _re.sub(r"<[^>]+>", "[^/]+", rule) + "$"))
        except _re.error:
            continue

    def is_registered(route: str) -> bool:
        route = str(route or "").strip()
        if not route:
            return False
        if route in literals:
            return True
        return any(pattern.match(route) for pattern in patterns)

    return is_registered


def verify_features(
    registered_rules=None,
    existing_tables=None,
    features: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Annotate features with measured route and audit-table reachability.

    `registered_rules` - iterable of Flask url_map rule strings. `existing_tables`
    - iterable of table names present in the live schema. Either may be None,
    in which case that dimension is reported as "unverified" rather than being
    silently assumed to pass. Unverified is not the same as verified; callers
    that render readiness must say which one they have.
    """
    rows = features if features is not None else all_features()
    is_registered = _rule_matcher(registered_rules) if registered_rules is not None else None
    tables = {str(name).lower() for name in existing_tables} if existing_tables is not None else None

    verified: list[dict[str, Any]] = []
    for item in rows:
        row = dict(item)
        route = str(row.get("route") or "")
        audit_table = str(row.get("audit_log_table") or "")

        if is_registered is None:
            row["route_registered"] = None
        else:
            row["route_registered"] = bool(route) and is_registered(route)

        if tables is None:
            row["audit_table_exists"] = None
        else:
            row["audit_table_exists"] = bool(audit_table) and audit_table.lower() in tables

        failures = []
        if row["route_registered"] is False:
            failures.append("route not registered")
        if row["audit_table_exists"] is False:
            failures.append("audit table missing")
        row["verification_failures"] = failures
        # A feature whose surface does not exist is not "active", whatever the
        # literal says. Audit-table gaps are recorded but do not by themselves
        # blank a working surface - they are reported as gaps.
        row["effective_status"] = "blocked" if row["route_registered"] is False else row.get("status")
        verified.append(row)
    return verified


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


def _effective_status(item: dict[str, Any]) -> str:
    """Prefer a verified status over the hand-written literal when one exists."""
    return str(item.get("effective_status") or item.get("status") or "")


def category_summary(features: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = features if features is not None else all_features()
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in REQUIRED_MODULES}
    for item in rows:
        grouped.setdefault(str(item.get("category")), []).append(item)
    summary: list[dict[str, Any]] = []
    for key, title in REQUIRED_MODULES.items():
        items = grouped.get(key, [])
        manageable = sum(1 for item in items if item.get("manageable_from_backend"))
        active = sum(1 for item in items if _effective_status(item) == "active")
        critical = sum(1 for item in items if item.get("launch_critical"))
        gaps = [item for item in items if _effective_status(item) in {"partial", "planned", "blocked"} or not item.get("manageable_from_backend")]
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

    def satisfied(requirement: Any) -> bool:
        names = (requirement,) if isinstance(requirement, str) else tuple(requirement)
        return any(str(source.get(name) or "").strip() for name in names)

    def label(requirement: Any) -> str:
        return requirement if isinstance(requirement, str) else " or ".join(requirement)

    rows: list[dict[str, Any]] = []
    for check in EXTERNAL_SERVICE_CHECKS:
        requirements = tuple(check.get("env") or ())
        met = [req for req in requirements if satisfied(req)]
        missing = [label(req) for req in requirements if not satisfied(req)]
        if not requirements:
            state = "not_tracked"
        elif not missing:
            state = "configured"
        elif met:
            state = "partial"
        else:
            state = "missing"
        rows.append({
            "key": check["key"],
            "label": check["label"],
            "module": check["module"],
            # "runtime" credentials are read by this web service; "build" credentials
            # are read only by the release pipeline. A caller that treats every gap
            # as a production outage will page someone about a missing Expo token.
            "scope": check.get("scope", "runtime"),
            "state": state,
            "configured_count": len(met),
            "required_count": len(requirements),
            "missing_env_names": missing,
        })
    return rows


def operating_system_snapshot(features: list[dict[str, Any]] | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    rows = features if features is not None else all_features()
    modules = category_summary(rows)
    services = service_readiness_from_env(env)
    unmanaged = [item for item in rows if not item.get("manageable_from_backend")]
    partial = [item for item in rows if _effective_status(item) == "partial"]
    blocked = [item for item in rows if _effective_status(item) in {"blocked", "planned"}]
    audit_missing = [item for item in rows if not str(item.get("audit_log_table") or "").strip()]
    routes_missing = [item for item in rows if not str(item.get("route") or "").strip()]
    critical = [item for item in rows if item.get("launch_critical")]
    configured_services = [item for item in services if item.get("state") == "configured"]
    # Only runtime credentials count toward the operational gap headline. The Expo,
    # App Store Connect and Google Play tokens are read by the release pipeline and
    # are expected to be absent from the web service's environment; counting them
    # here reported three permanent "external service gaps" that no change to the
    # running system could ever clear, which trains operators to ignore the number.
    service_gaps = [
        item for item in services
        if item.get("state") in {"missing", "partial"} and item.get("scope") != "build"
    ]
    build_credential_gaps = [
        item for item in services
        if item.get("state") in {"missing", "partial"} and item.get("scope") == "build"
    ]
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
        "build_credential_gaps": len(build_credential_gaps),
        "modules": module_cards,
        "risk_summary": {
            "critical": len([item for item in rows if item.get("risk_level") == "critical"]),
            "high": len([item for item in rows if item.get("risk_level") == "high"]),
            "medium": len([item for item in rows if item.get("risk_level") == "medium"]),
            "low": len([item for item in rows if item.get("risk_level") == "low"]),
        },
    }


def _module_risk(items: list[dict[str, Any]]) -> str:
    if any(item.get("risk_level") == "critical" and _effective_status(item) in {"partial", "blocked", "planned"} for item in items):
        return "critical"
    if any(item.get("risk_level") in {"critical", "high"} and _effective_status(item) != "active" for item in items):
        return "high"
    if any(_effective_status(item) != "active" for item in items):
        return "medium"
    return "low"


def launch_readiness(registered_rules=None, existing_tables=None) -> dict[str, Any]:
    """Launch readiness measured against runtime facts where they are available.

    Pass the live Flask url_map rules and the live table list to get a verified
    answer. Called with neither, the result is explicitly marked unverified, so
    no caller can mistake a registry transcript for a system check.
    """
    features = verify_features(registered_rules, existing_tables)
    critical = [item for item in features if item.get("launch_critical")]
    blocked = [item for item in critical if item.get("effective_status") in {"blocked", "planned"} or not item.get("manageable_from_backend")]
    partial = [item for item in critical if item.get("effective_status") == "partial"]
    active = [item for item in critical if item.get("effective_status") == "active" and item.get("manageable_from_backend")]
    unreachable = [item for item in features if item.get("route_registered") is False]
    audit_gaps = [item for item in features if item.get("audit_table_exists") is False]
    score = 100 if not critical else round((len(active) / len(critical)) * 100)
    os_snapshot = operating_system_snapshot(features)
    gaps = gap_audit()
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "score": score,
        "status": "ready" if not blocked and score >= 90 else "watch" if not blocked else "blocked",
        "verified": registered_rules is not None,
        "verification_note": (
            "Route reachability checked against the live URL map."
            if registered_rules is not None else
            "UNVERIFIED: statuses are registry declarations, not measured system state."
        ),
        "critical_total": len(critical),
        "critical_active": len(active),
        "critical_partial": len(partial),
        "critical_blocked": len(blocked),
        "unreachable_route_count": len(unreachable),
        "unreachable_routes": [
            {"feature_key": item["feature_key"], "route": item.get("route"), "launch_critical": item.get("launch_critical")}
            for item in unreachable
        ],
        "audit_table_missing_count": len(audit_gaps),
        "audit_table_missing": [
            {"feature_key": item["feature_key"], "audit_log_table": item.get("audit_log_table")}
            for item in audit_gaps
        ],
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
