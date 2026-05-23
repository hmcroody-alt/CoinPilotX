"""Central registry for Pulse Premium capabilities.

The premium page can sound ambitious, but the product should only expose
features that are active, safely scaffolded, or clearly marked as future work.
This module is the single source of truth for that promise.
"""

from __future__ import annotations

import os
from copy import deepcopy


ACTIVE = "active"
SCAFFOLDED = "scaffolded"
FUTURE = "future"
DISABLED = "disabled"


CAPABILITIES = {
    "premium_identity": {
        "label": "Premium Identity",
        "status": ACTIVE,
        "required_tables": ["pulse_premium_profiles", "pulse_profile_themes", "pulse_identity_effects"],
        "required_routes": ["/api/pulse/premium/activate", "/api/pulse/premium/profile-theme", "/api/pulse/premium/identity-effects"],
        "required_services": ["premium_identity_engine", "premium_visibility_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Active for founder/admin grants; checkout activation is safely gated.",
    },
    "creator_ai": {
        "label": "Creator AI",
        "status": ACTIVE,
        "required_tables": [],
        "required_routes": ["/api/pulse/creator-ai/hook", "/api/pulse/creator-ai/caption", "/api/pulse/creator-ai/virality", "/api/pulse/creator-ai/live-title"],
        "required_services": ["premium_capability_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Active as deterministic creator assistance with safety-first wording.",
    },
    "advanced_analytics": {
        "label": "Advanced Analytics",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_creator_analytics", "pulse_reel_retention_events", "pulse_creator_audience_segments", "pulse_content_sentiment", "pulse_creator_energy_snapshots"],
        "required_routes": ["/pulse/creator/analytics"],
        "required_services": ["premium_capability_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Preview/scaffolded until enough real engagement data exists.",
    },
    "premium_studio": {
        "label": "Premium Studio",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_identity_effects"],
        "required_routes": ["/pulse/camera", "/pulse/reels"],
        "required_services": ["camera_filter_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Studio presets are available as a catalog and connect to camera/reels surfaces.",
    },
    "discovery_boosts": {
        "label": "Discovery Context",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_creator_energy_snapshots"],
        "required_routes": ["/pulse/creator/dashboard"],
        "required_services": ["reel_ranking_engine", "pulse_feed_ranking_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Context signals only; Premium never overrides safety or quality ranking.",
    },
    "livestream_prestige": {
        "label": "Livestream Prestige",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_live_streams", "pulse_live_sessions"],
        "required_routes": ["/pulse/live", "/pulse/live/studio"],
        "required_services": ["live_stream_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Premium overlays and branded rooms are scaffolded on the live session system.",
    },
    "creator_acceleration": {
        "label": "Creator Acceleration",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_creator_energy_snapshots", "pulse_creator_analytics"],
        "required_routes": ["/pulse/creator/dashboard"],
        "required_services": ["social_energy_engine", "retention_analytics"],
        "admin_visibility": True,
        "user_facing_availability": "Uses creator consistency, education, and trust context without pay-to-win ranking.",
    },
    "audience_intelligence": {
        "label": "Audience Intelligence",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_creator_audience_segments"],
        "required_routes": ["/pulse/creator/analytics"],
        "required_services": ["retention_analytics"],
        "admin_visibility": True,
        "user_facing_availability": "Analytics preview until large audience segments are available.",
    },
    "retention_prediction": {
        "label": "Retention Prediction",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_reel_retention_events"],
        "required_routes": ["/api/pulse/creator-ai/virality"],
        "required_services": ["retention_analytics"],
        "admin_visibility": True,
        "user_facing_availability": "Uses transparent heuristics until measured retention curves mature.",
    },
    "profile_aura": {
        "label": "Profile Aura",
        "status": ACTIVE,
        "required_tables": ["pulse_premium_profiles", "pulse_identity_effects"],
        "required_routes": ["/api/pulse/premium/identity-effects"],
        "required_services": ["premium_visibility_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Active for premium/founder identity rendering.",
    },
    "elite_themes": {
        "label": "Elite Themes",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_profile_themes"],
        "required_routes": ["/api/pulse/premium/profile-theme"],
        "required_services": ["premium_visibility_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Theme selection is scaffolded and protected by entitlement checks.",
    },
    "premium_filters": {
        "label": "Premium Filters",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_identity_effects"],
        "required_routes": ["/pulse/camera", "/pulse/reels"],
        "required_services": ["camera_filter_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Filter catalog is available for camera/reel integration.",
    },
    "creator_luts": {
        "label": "Creator LUTs",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_identity_effects"],
        "required_routes": ["/pulse/camera"],
        "required_services": ["camera_filter_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Visual presets are cataloged as studio effects.",
    },
    "replay_intelligence": {
        "label": "Replay Intelligence",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_creator_analytics", "pulse_reel_retention_events"],
        "required_routes": ["/pulse/creator/analytics"],
        "required_services": ["retention_analytics"],
        "admin_visibility": True,
        "user_facing_availability": "Analytics preview until replay data volume is sufficient.",
    },
    "trust_visibility": {
        "label": "Trust Visibility",
        "status": ACTIVE,
        "required_tables": ["user_trust_profiles"],
        "required_routes": ["/pulse/profile"],
        "required_services": ["user_trust_engine", "premium_visibility_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Active as trust-safe profile and creator context.",
    },
    "creator_energy": {
        "label": "Creator Energy",
        "status": SCAFFOLDED,
        "required_tables": ["pulse_creator_energy_snapshots"],
        "required_routes": ["/pulse/creator/dashboard"],
        "required_services": ["social_energy_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Preview signal; improves as real engagement data grows.",
    },
    "cohosting_future": {
        "label": "Co-hosting",
        "status": FUTURE,
        "required_tables": ["pulse_live_sessions"],
        "required_routes": ["/pulse/live"],
        "required_services": ["live_stream_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Coming soon.",
    },
    "elite_rooms_future": {
        "label": "Elite Rooms",
        "status": FUTURE,
        "required_tables": ["pulse_conversations"],
        "required_routes": ["/pulse/messages"],
        "required_services": ["premium_visibility_engine"],
        "admin_visibility": True,
        "user_facing_availability": "Coming soon.",
    },
}


def capability_registry():
    registry = deepcopy(CAPABILITIES)
    if os.getenv("PULSE_PREMIUM_DISABLED", "").lower() in {"1", "true", "yes"}:
        for item in registry.values():
            item["status"] = DISABLED
            item["user_facing_availability"] = "Temporarily disabled by platform configuration."
    return registry


def capability_by_key(key):
    return capability_registry().get(str(key or "").strip())


def premium_feature_flags():
    return {
        "premium_identity_enabled": True,
        "premium_analytics_enabled": True,
        "premium_messenger_enabled": True,
        "premium_live_enabled": True,
        "premium_studio_enabled": True,
        "premium_teacher_enabled": True,
        "premium_marketplace_enabled": True,
    }


def capability_summary():
    registry = capability_registry()
    totals = {ACTIVE: 0, SCAFFOLDED: 0, FUTURE: 0, DISABLED: 0}
    for item in registry.values():
        totals[item.get("status", SCAFFOLDED)] = totals.get(item.get("status", SCAFFOLDED), 0) + 1
    return {"total": len(registry), "status_counts": totals, "capabilities": registry}


def capability_health(table_exists=None, route_exists=None, service_exists=None):
    health = {}
    for key, item in capability_registry().items():
        missing_tables = [t for t in item.get("required_tables", []) if table_exists and not table_exists(t)]
        missing_routes = [r for r in item.get("required_routes", []) if route_exists and not route_exists(r)]
        missing_services = [s for s in item.get("required_services", []) if service_exists and not service_exists(s)]
        health[key] = {
            **item,
            "ok": not missing_tables and not missing_routes and not missing_services,
            "missing_tables": missing_tables,
            "missing_routes": missing_routes,
            "missing_services": missing_services,
        }
    return health
