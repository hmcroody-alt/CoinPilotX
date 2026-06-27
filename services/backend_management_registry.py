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
    BackendFeature("account.profile", "Profile Manager", "account", "/admin/account-command", "admin", "command_center.view", "active", "Account", "dashboard_account_command_center", "profile_audit_logs", "high", True, True, "Profile updates, avatar/banner controls, privacy state, and rollback audit."),
    BackendFeature("account.verification", "Verification Queue", "account", "/admin/account-command", "admin", "command_center.view", "active", "Trust", "dashboard_account_command_center", "verification_requests", "critical", True, True, "Identity, blue-check, business review, decisions, and appeals."),
    BackendFeature("account.health", "Account Health", "account", "/admin/account-command", "admin", "command_center.view", "active", "Trust", "dashboard_account_command_center", "account_health_events", "critical", True, True, "Warnings, strikes, restrictions, appeals, and health score."),
    BackendFeature("account.security", "Security Center", "account", "/admin/account-command", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "security_login_events", "critical", True, True, "Sessions, devices, suspicious logins, and sensitive action audit."),
    BackendFeature("account.settings", "Settings Manager", "account", "/admin/account-command", "admin", "command_center.view", "active", "Account", "dashboard_account_command_center", "user_settings", "high", True, True, "Server-managed privacy, notification, accessibility, and ads personalization settings."),
    BackendFeature("account.appeals", "Appeals", "account", "/admin/account-command", "admin", "moderation.manage", "partial", "Trust", "dashboard_account_command_center", "account_audit_logs", "high", True, True, "Verification and account-health appeal hooks are present; richer queues can expand here."),
    BackendFeature("account.audit_logs", "Account Audit Logs", "account", "/admin/account-command", "admin", "audit.view", "active", "Security", "dashboard_account_command_center", "account_audit_logs", "critical", True, True, "Sensitive account actions are recorded and admin-reviewable."),
    BackendFeature("account.restrictions", "Restrictions", "account", "/admin/account-command", "admin", "moderation.manage", "active", "Trust", "dashboard_account_command_center", "account_restrictions", "critical", True, True, "Restriction state is stored and reflected in account health."),
    BackendFeature("account.sessions", "Sessions", "account", "/admin/account-command", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "active_sessions", "critical", True, True, "Session/device inventory is backend-managed."),
    BackendFeature("account.devices", "Devices", "account", "/admin/account-command", "admin", "security.view", "active", "Security", "dashboard_account_command_center", "security_devices", "critical", True, True, "Device and push registration management hooks are backend-visible."),
    BackendFeature("network.notifications", "Notifications", "network", "/admin/notifications", "admin", "command_center.view", "active", "Notifications", "notification_orchestrator", "notification_delivery_logs", "high", True, True, "Provider health, delivery logs, and queue controls."),
    BackendFeature("network.messages", "Messages", "network", "/admin/private-chat-reports", "moderator", "moderation.manage", "active", "Messaging", "chat_realtime_service", "admin_audit_logs", "critical", True, True, "Chat reports, realtime health, and moderation escalation."),
    BackendFeature("network.groups", "Groups", "network", "/admin/departments/social", "moderator", "command_center.view", "partial", "Community", "community_governance_engine", "admin_tasks", "medium", False, True, "Group management routes remain distributed; command tasks centralize oversight."),
    BackendFeature("network.status_activity", "Status Activity", "network", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "PulseSoc", "pulse_moderation_engine", "moderation_cases", "high", True, True, "Status reports and content moderation flow through PulseSoc moderation."),
    BackendFeature("creator.posts", "Posts", "creator", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Creator", "pulse_moderation_engine", "moderation_cases", "high", True, True, "Posts are reviewable and removable through moderation tools."),
    BackendFeature("creator.reels", "Reels", "creator", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Creator", "reel_ranking_engine", "moderation_cases", "high", True, True, "Reels use PulseSoc content moderation and ranking audits."),
    BackendFeature("creator.videos", "Videos", "creator", "/admin/pulse-moderation", "moderator", "pulse.moderate", "active", "Creator", "media_service", "moderation_cases", "high", True, True, "Video review and content reports are moderation-visible."),
    BackendFeature("creator.live", "Live Studio", "creator", "/admin/pulse-infrastructure", "admin", "system.view", "partial", "Live", "live_stream_health_service", "admin_tasks", "critical", True, True, "Live health is visible; deeper creator-level controls remain staged."),
    BackendFeature("creator.analytics", "Creator Analytics", "creator", "/admin/pulse-analytics", "admin", "analytics.view", "active", "Analytics", "analytics_intelligence_engine", "admin_audit_logs", "medium", False, True, "Platform analytics can inspect creator activity safely."),
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
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "score": score,
        "status": "ready" if not blocked and score >= 90 else "watch" if not blocked else "blocked",
        "critical_total": len(critical),
        "critical_active": len(active),
        "critical_partial": len(partial),
        "critical_blocked": len(blocked),
        "modules": category_summary(features),
        "remaining_gaps": gap_audit()["gaps"],
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
            "no secret exposure",
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
