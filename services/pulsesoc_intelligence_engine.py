"""PulseSoc Galaxy Intelligence Engine foundation.

This service turns raw platform/source signals into confidence-scored
Intelligence Pulses. It is intentionally queue-ready: user routes read cached
state only, while collectors/workers call ``ingest_signal`` out of band.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Any

from services import db as db_service
from services import pulsesoc_notification_system


INTERNAL_CODENAME = "LogiNexus Intelligence Engine"
PUBLIC_CENTER_NAME = "Pulse Signals"
ADMIN_CENTER_NAME = "Galaxy Intelligence Center"
PULSESOC_APP_STORE_URL = os.getenv(
    "PULSESOC_APP_STORE_URL",
    "https://apps.apple.com/us/app/pulsesoc/id6777591572",
).strip()
ALLOWED_ACTION_DOMAINS = {"apps.apple.com", "pulsesoc.com"}
CONFIDENCE_LABELS = [
    (88, "Very High"),
    (74, "High"),
    (52, "Medium"),
    (0, "Low"),
]
STREAM_KEYS = {
    "pulsesoc_discoveries",
    "crypto_pulse",
    "market_pulse",
    "world_pulse",
    "security_pulse",
    "technology_pulse",
    "pulsesoc_pulse",
    "creator_pulse",
    "music_pulse",
    "system_pulse",
}
PRIORITIES = {"breaking", "urgent", "high", "normal", "low"}
FREQUENCIES = {"realtime", "digest", "morning", "afternoon", "evening", "weekly", "monthly", "muted"}

USER_SURFACES: dict[str, dict[str, Any]] = {
    "alerts": {
        "key": "alerts",
        "title": "Pulse Alerts",
        "eyebrow": "Signals that matter",
        "description": "Your latest high-confidence alerts, ranked by importance and relevance.",
        "stream_keys": [],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
    "forecasts": {
        "key": "forecasts",
        "title": "Pulse Forecasts",
        "eyebrow": "What may matter next",
        "description": "Confidence-labeled forecasts built only when trusted signals support them.",
        "stream_keys": [],
        "show_streams": False,
        "show_events": False,
        "show_forecasts": True,
    },
    "briefing": {
        "key": "briefing",
        "title": "Daily Briefing",
        "eyebrow": "Your day in focus",
        "description": "A compact view of the strongest signals and forecasts available to you.",
        "stream_keys": [],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
        "event_limit": 12,
        "forecast_limit": 6,
    },
    "preferences": {
        "key": "preferences",
        "title": "Signal Preferences",
        "eyebrow": "You control the frequency",
        "description": "Choose the signals you receive, their confidence threshold, and how often they arrive.",
        "stream_keys": [],
        "show_streams": True,
        "show_events": False,
        "show_forecasts": False,
    },
    "security": {
        "key": "security",
        "title": "Security Signals",
        "eyebrow": "Protection intelligence",
        "description": "Defensive security alerts, patch guidance, scam warnings, and account protection signals.",
        "stream_keys": ["security_pulse"],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
    "crypto": {
        "key": "crypto",
        "title": "Crypto Signals",
        "eyebrow": "Market intelligence",
        "description": "High-confidence crypto movement and ecosystem signals without investment advice.",
        "stream_keys": ["crypto_pulse"],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
    "market": {
        "key": "market",
        "title": "Market Signals",
        "eyebrow": "Macro and market activity",
        "description": "Major market, economic, and cross-asset signals without stock-by-stock noise.",
        "stream_keys": ["market_pulse"],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
    "technology": {
        "key": "technology",
        "title": "Tech Signals",
        "eyebrow": "Technology that matters",
        "description": "Major AI, platform, hardware, software, and research developments from trusted sources.",
        "stream_keys": ["technology_pulse"],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
    "music": {
        "key": "music",
        "title": "Music Signals",
        "eyebrow": "Sounds moving through PulseSoc",
        "description": "New releases, trending sounds, and music activity backed by available PulseSoc data.",
        "stream_keys": ["music_pulse"],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
    "creator": {
        "key": "creator",
        "title": "Creator Signals",
        "eyebrow": "Audience and publishing signals",
        "description": "Creator milestones, audience trends, and posting guidance backed by real platform activity.",
        "stream_keys": ["creator_pulse"],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
    "world": {
        "key": "world",
        "title": "World Events",
        "eyebrow": "Major events only",
        "description": "Heavily filtered world, emergency, science, space, and infrastructure signals.",
        "stream_keys": ["world_pulse"],
        "show_streams": False,
        "show_events": True,
        "show_forecasts": True,
    },
}

ADMIN_COMMAND_SECTIONS: tuple[dict[str, str], ...] = (
    {"label": "Overview", "route": "/admin/intelligence"},
    {"label": "Collector Network", "route": "/admin/intelligence#collector-network"},
    {"label": "Source Registry", "route": "/admin/intelligence#source-registry"},
    {"label": "Forecast Engine", "route": "/admin/intelligence#forecast-engine"},
    {"label": "Signal Queue", "route": "/admin/intelligence#signal-queue"},
    {"label": "AI Confidence", "route": "/admin/intelligence-command-center/prediction-engine"},
    {"label": "Threat Monitor", "route": "/admin/intelligence-command-center/threat-intelligence"},
    {"label": "Cyber Security Feed", "route": "/admin/intelligence?stream=security_pulse#signal-queue"},
    {"label": "Crypto Feed", "route": "/admin/intelligence?stream=crypto_pulse#signal-queue"},
    {"label": "Market Feed", "route": "/admin/intelligence?stream=market_pulse#signal-queue"},
    {"label": "Technology Feed", "route": "/admin/intelligence?stream=technology_pulse#signal-queue"},
    {"label": "World Events Feed", "route": "/admin/intelligence?stream=world_pulse#signal-queue"},
    {"label": "PulseSoc Feed", "route": "/admin/intelligence?stream=pulsesoc_pulse#signal-queue"},
    {"label": "Creator Trends", "route": "/admin/intelligence?stream=creator_pulse#signal-queue"},
    {"label": "Music Trends", "route": "/admin/intelligence?stream=music_pulse#signal-queue"},
    {"label": "Broadcast Center", "route": "/admin/notifications"},
    {"label": "Campaign Manager", "route": "/admin/intelligence-command-center/alert-management"},
    {"label": "Delivery Engine", "route": "/admin/intelligence#delivery-engine"},
    {"label": "Notification Delivery", "route": "/admin/notification-delivery"},
    {"label": "Analytics", "route": "/admin/analytics"},
    {"label": "AI Learning Engine", "route": "/admin/pulse-ai/learning"},
    {"label": "Quality Assurance", "route": "/admin/audit-logs"},
    {"label": "Logs", "route": "/admin/audit-logs"},
    {"label": "System Health", "route": "/admin/system"},
    {"label": "Source Health", "route": "/admin/provider-health"},
    {"label": "API Health", "route": "/admin/system-health"},
    {"label": "Worker Health", "route": "/admin/pulse-worker-health"},
    {"label": "Feedback Learning", "route": "/admin/intelligence#feedback-learning"},
    {"label": "Historical Signals", "route": "/admin/intelligence#signal-queue"},
    {"label": "Emergency Broadcast", "route": "/admin/notifications"},
)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, default=str, sort_keys=True, separators=(",", ":"))[:20000]


def _json_loads(value: Any, fallback: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return fallback if fallback is not None else {}
    try:
        return json.loads(str(value))
    except Exception:
        return fallback if fallback is not None else {}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _compact(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ").strip())[:limit]


def _slug(value: Any, limit: int = 80) -> str:
    text = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return (text or "signal")[:limit]


def _confidence_label(score: int) -> str:
    score = max(0, min(int(score or 0), 100))
    for threshold, label in CONFIDENCE_LABELS:
        if score >= threshold:
            return label
    return "Low"


def _source_env_present(source: dict[str, Any]) -> bool:
    env = source.get("required_env") or []
    return all(os.getenv(str(key or "").strip(), "").strip() for key in env)


def _safe_external_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in ALLOWED_ACTION_DOMAINS:
        return ""
    return url


def _safe_internal_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url.startswith("/") or url.startswith("//"):
        return ""
    lowered = url.lower().strip()
    if lowered.startswith("/javascript:") or "\x00" in lowered:
        return ""
    return url[:500]


def _action(label: str, action_type: str, *, url: str = "", style: str = "primary", icon: str = "spark", **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "label": _compact(label, 42),
        "type": _slug(action_type, 30),
        "style": _slug(style, 20),
        "icon": _slug(icon, 24),
    }
    if url:
        item["url"] = url
    for key, value in extra.items():
        if value not in (None, ""):
            item[key] = _compact(value, 500) if isinstance(value, str) else value
    return item


def validate_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    safe: list[dict[str, Any]] = []
    for raw in actions[:4]:
        if not isinstance(raw, dict):
            continue
        action_type = _slug(raw.get("type"), 30)
        label = _compact(raw.get("label"), 42)
        if not label or action_type not in {"app_store", "share", "deep_link"}:
            continue
        item = _action(label, action_type, style=raw.get("style") or "primary", icon=raw.get("icon") or "spark")
        if action_type == "deep_link":
            url = _safe_internal_url(raw.get("url"))
            if not url:
                continue
            item["url"] = url
        elif action_type == "app_store":
            url = _safe_external_url(raw.get("url") or PULSESOC_APP_STORE_URL)
            if not url:
                continue
            item["url"] = url
            item["visibility"] = _slug(raw.get("visibility") or "external_or_not_installed", 40)
        elif action_type == "share":
            share_url = _safe_external_url(raw.get("share_url") or raw.get("url") or PULSESOC_APP_STORE_URL)
            if not share_url:
                continue
            item["share_url"] = share_url
            item["share_title"] = _compact(raw.get("share_title") or "PulseSoc", 90)
            item["share_text"] = _compact(raw.get("share_text") or "Join me on PulseSoc and explore the galaxy.", 220)
        safe.append(item)
    return safe


def default_actions_for_signal(stream_key: str, event_type: str = "", deep_link: str = "") -> list[dict[str, Any]]:
    stream_key = _slug(stream_key)
    event_type = _slug(event_type, 40)
    deep = _safe_internal_url(deep_link) or "/pulse/alerts"
    if stream_key == "pulsesoc_pulse":
        return validate_actions([
            _action("Explore Feature", "deep_link", url=deep, icon="spark", style="primary"),
            _action("Share PulseSoc", "share", icon="share", style="secondary", share_url=PULSESOC_APP_STORE_URL, share_title="PulseSoc", share_text="Join me on PulseSoc and explore the galaxy."),
        ])
    if stream_key == "pulsesoc_discoveries":
        return validate_actions([
            _action("Try It", "deep_link", url=deep, icon="spark", style="primary"),
            _action("Invite Friends", "share", icon="share", style="secondary", share_url=PULSESOC_APP_STORE_URL, share_title="PulseSoc", share_text="Join me on PulseSoc and explore the galaxy."),
        ])
    if stream_key == "system_pulse" and event_type in {"app_update", "launch", "release", "new_version", "system_update"}:
        return validate_actions([
            _action("Open PulseSoc", "deep_link", url=deep, icon="spark", style="primary"),
            _action("Download PulseSoc", "app_store", url=PULSESOC_APP_STORE_URL, icon="apple", style="secondary"),
        ])
    if stream_key == "music_pulse":
        return validate_actions([
            _action("Explore Music", "deep_link", url="/pulse/music", icon="music", style="primary"),
            _action("Share PulseSoc", "share", icon="share", style="secondary", share_url=PULSESOC_APP_STORE_URL, share_title="PulseSoc", share_text="Discover PulseSoc Music with me."),
        ])
    if stream_key == "creator_pulse":
        return validate_actions([
            _action("Open Creator Tools", "deep_link", url="/pulse/creator/dashboard", icon="spark", style="primary"),
            _action("Invite Friends", "share", icon="share", style="secondary", share_url=PULSESOC_APP_STORE_URL, share_title="PulseSoc", share_text="Join me on PulseSoc and explore creator tools."),
        ])
    return []


DEFAULT_STREAMS: list[dict[str, Any]] = [
    {
        "stream_key": "pulsesoc_discoveries",
        "display_name": "PulseSoc Discoveries",
        "purpose": "Teach users useful PulseSoc features naturally without marketing noise.",
        "category": "platform",
        "default_frequency": "digest",
        "default_priority": "normal",
        "default_enabled": True,
        "default_push": False,
        "threshold": 62,
        "examples": [
            "Pulse AI can help you explore PulseSoc.",
            "Create video stories with music.",
            "Messenger supports HD video Pulses.",
        ],
    },
    {
        "stream_key": "crypto_pulse",
        "display_name": "Crypto Pulse",
        "purpose": "Surface high-confidence crypto movement, regulatory, and chain intelligence without investment advice.",
        "category": "crypto",
        "default_frequency": "digest",
        "default_priority": "high",
        "default_enabled": True,
        "default_push": True,
        "threshold": 74,
        "examples": ["Bitcoin breaks major resistance.", "Ethereum volatility rising.", "Large whale movement detected."],
    },
    {
        "stream_key": "market_pulse",
        "display_name": "Market Pulse",
        "purpose": "Watch major market events and macroeconomic signals.",
        "category": "markets",
        "default_frequency": "digest",
        "default_priority": "normal",
        "default_enabled": True,
        "default_push": False,
        "threshold": 72,
        "examples": ["Federal Reserve announcement.", "Inflation report released.", "NASDAQ opens higher."],
    },
    {
        "stream_key": "world_pulse",
        "display_name": "World Pulse",
        "purpose": "Major global events only: emergencies, space, science, infrastructure, elections, and medical breakthroughs.",
        "category": "world",
        "default_frequency": "digest",
        "default_priority": "normal",
        "default_enabled": True,
        "default_push": False,
        "threshold": 80,
        "examples": ["NASA launch.", "Major earthquake.", "Global cybersecurity incident."],
    },
    {
        "stream_key": "security_pulse",
        "display_name": "Security Pulse",
        "purpose": "Protect users with high-confidence security and vulnerability intelligence.",
        "category": "security",
        "default_frequency": "realtime",
        "default_priority": "high",
        "default_enabled": True,
        "default_push": True,
        "threshold": 76,
        "examples": ["Apple emergency update.", "Critical Android vulnerability.", "Major password leak."],
    },
    {
        "stream_key": "technology_pulse",
        "display_name": "Technology Pulse",
        "purpose": "Major AI, device, software, and scientific breakthroughs.",
        "category": "technology",
        "default_frequency": "digest",
        "default_priority": "normal",
        "default_enabled": False,
        "default_push": False,
        "threshold": 70,
        "examples": ["Major AI release.", "Apple keynote.", "New scientific breakthrough."],
    },
    {
        "stream_key": "pulsesoc_pulse",
        "display_name": "PulseSoc Pulse",
        "purpose": "Platform improvements, maintenance, creator spotlights, and trending communities.",
        "category": "platform",
        "default_frequency": "digest",
        "default_priority": "normal",
        "default_enabled": True,
        "default_push": False,
        "threshold": 60,
        "examples": ["New features.", "Maintenance.", "Creator spotlight."],
    },
    {
        "stream_key": "creator_pulse",
        "display_name": "Creator Pulse",
        "purpose": "Personal creator timing, growth, trends, audience, and content recommendations.",
        "category": "creator",
        "default_frequency": "digest",
        "default_priority": "normal",
        "default_enabled": False,
        "default_push": False,
        "threshold": 58,
        "examples": ["Best posting time.", "Weekly growth.", "Trending topics."],
    },
    {
        "stream_key": "music_pulse",
        "display_name": "Music Pulse",
        "purpose": "Trending songs, emerging artists, PulseSoc Music releases, and popular audio.",
        "category": "music",
        "default_frequency": "digest",
        "default_priority": "low",
        "default_enabled": False,
        "default_push": False,
        "threshold": 58,
        "examples": ["Trending songs.", "Emerging artists.", "Popular audio."],
    },
    {
        "stream_key": "system_pulse",
        "display_name": "System Pulse",
        "purpose": "Maintenance, app version, incident, and rollout intelligence from PulseSoc system events.",
        "category": "system",
        "default_frequency": "realtime",
        "default_priority": "high",
        "default_enabled": True,
        "default_push": False,
        "threshold": 82,
        "examples": ["Maintenance notice.", "New app version.", "Incident resolved."],
    },
]


SOURCE_CATALOG: list[dict[str, Any]] = [
    {"source_key": "pulsesoc_feature_registry", "display_name": "PulseSoc Feature Registry", "stream_key": "pulsesoc_discoveries", "provider_type": "internal", "trust_score": 92, "cache_seconds": 60, "required_env": []},
    {"source_key": "pulsesoc_telemetry", "display_name": "PulseSoc Telemetry", "stream_key": "pulsesoc_pulse", "provider_type": "internal", "trust_score": 88, "cache_seconds": 15, "required_env": []},
    {"source_key": "coingecko", "display_name": "CoinGecko", "stream_key": "crypto_pulse", "provider_type": "market_api", "trust_score": 78, "cache_seconds": 60, "required_env": []},
    {"source_key": "binance", "display_name": "Binance Public Market Data", "stream_key": "crypto_pulse", "provider_type": "market_api", "trust_score": 74, "cache_seconds": 30, "required_env": []},
    {"source_key": "kraken", "display_name": "Kraken Public Market Data", "stream_key": "crypto_pulse", "provider_type": "market_api", "trust_score": 74, "cache_seconds": 30, "required_env": []},
    {"source_key": "coinmarketcap", "display_name": "CoinMarketCap", "stream_key": "crypto_pulse", "provider_type": "market_api", "trust_score": 78, "cache_seconds": 60, "required_env": ["COINMARKETCAP_API_KEY"]},
    {"source_key": "yahoo_finance", "display_name": "Yahoo Finance Public Market Data", "stream_key": "market_pulse", "provider_type": "market_api", "trust_score": 70, "cache_seconds": 90, "required_env": []},
    {"source_key": "polygon", "display_name": "Polygon", "stream_key": "market_pulse", "provider_type": "market_api", "trust_score": 78, "cache_seconds": 60, "required_env": ["POLYGON_API_KEY"]},
    {"source_key": "alpha_vantage", "display_name": "Alpha Vantage", "stream_key": "market_pulse", "provider_type": "market_api", "trust_score": 74, "cache_seconds": 60, "required_env": ["ALPHA_VANTAGE_API_KEY"]},
    {"source_key": "reuters", "display_name": "Reuters", "stream_key": "world_pulse", "provider_type": "news", "trust_score": 86, "cache_seconds": 300, "required_env": ["REUTERS_API_KEY"]},
    {"source_key": "ap_news", "display_name": "Associated Press", "stream_key": "world_pulse", "provider_type": "news", "trust_score": 86, "cache_seconds": 300, "required_env": ["AP_NEWS_API_KEY"]},
    {"source_key": "nasa", "display_name": "NASA", "stream_key": "world_pulse", "provider_type": "official", "trust_score": 90, "cache_seconds": 300, "required_env": []},
    {"source_key": "noaa", "display_name": "NOAA", "stream_key": "world_pulse", "provider_type": "official", "trust_score": 90, "cache_seconds": 300, "required_env": []},
    {"source_key": "usgs", "display_name": "USGS", "stream_key": "world_pulse", "provider_type": "official", "trust_score": 90, "cache_seconds": 300, "required_env": []},
    {"source_key": "cisa", "display_name": "CISA", "stream_key": "security_pulse", "provider_type": "official", "trust_score": 92, "cache_seconds": 600, "required_env": []},
    {"source_key": "nist", "display_name": "NIST", "stream_key": "security_pulse", "provider_type": "official", "trust_score": 90, "cache_seconds": 600, "required_env": []},
    {"source_key": "microsoft_security", "display_name": "Microsoft Security", "stream_key": "security_pulse", "provider_type": "official", "trust_score": 88, "cache_seconds": 600, "required_env": []},
    {"source_key": "apple_security", "display_name": "Apple Security", "stream_key": "security_pulse", "provider_type": "official", "trust_score": 88, "cache_seconds": 600, "required_env": []},
    {"source_key": "google_security", "display_name": "Google Security", "stream_key": "security_pulse", "provider_type": "official", "trust_score": 88, "cache_seconds": 600, "required_env": []},
    {"source_key": "openai_updates", "display_name": "OpenAI Official Updates", "stream_key": "technology_pulse", "provider_type": "official", "trust_score": 86, "cache_seconds": 900, "required_env": []},
    {"source_key": "apple_newsroom", "display_name": "Apple Newsroom", "stream_key": "technology_pulse", "provider_type": "official", "trust_score": 84, "cache_seconds": 900, "required_env": []},
    {"source_key": "creator_analytics", "display_name": "Creator Analytics", "stream_key": "creator_pulse", "provider_type": "internal", "trust_score": 84, "cache_seconds": 900, "required_env": []},
    {"source_key": "pulse_music", "display_name": "PulseSoc Music", "stream_key": "music_pulse", "provider_type": "internal", "trust_score": 82, "cache_seconds": 300, "required_env": []},
    {"source_key": "pulsesoc_system", "display_name": "PulseSoc System Events", "stream_key": "system_pulse", "provider_type": "internal", "trust_score": 92, "cache_seconds": 60, "required_env": []},
]


def connect():
    conn = db_service.connect()
    try:
        conn.row_factory = db_service.sqlite3.Row  # type: ignore[attr-defined]
    except Exception:
        pass
    return conn


def ensure_schema(conn: Any | None = None) -> None:
    owns_conn = conn is None
    conn = conn or connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_key TEXT UNIQUE,
            display_name TEXT,
            purpose TEXT,
            category TEXT,
            default_priority TEXT,
            default_frequency TEXT,
            default_enabled INTEGER DEFAULT 1,
            default_push INTEGER DEFAULT 0,
            confidence_threshold INTEGER DEFAULT 70,
            config_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_intelligence_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            stream_key TEXT,
            enabled INTEGER DEFAULT 1,
            frequency TEXT DEFAULT 'digest',
            digest_mode TEXT DEFAULT 'daily',
            push_enabled INTEGER DEFAULT 0,
            email_enabled INTEGER DEFAULT 0,
            sms_enabled INTEGER DEFAULT 0,
            breaking_push_only INTEGER DEFAULT 1,
            confidence_threshold INTEGER DEFAULT 70,
            priority_filter TEXT DEFAULT 'normal',
            quiet_hours_enabled INTEGER DEFAULT 0,
            muted_until TEXT,
            last_opened_at TEXT,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, stream_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT UNIQUE,
            display_name TEXT,
            stream_key TEXT,
            provider_type TEXT,
            trust_score INTEGER DEFAULT 70,
            status TEXT DEFAULT 'configured',
            cache_seconds INTEGER DEFAULT 300,
            required_env_json TEXT,
            last_success_at TEXT,
            last_failure_at TEXT,
            failure_reason TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE,
            stream_key TEXT,
            event_type TEXT,
            headline TEXT,
            summary TEXT,
            why_it_matters TEXT,
            expected_impact TEXT,
            confidence_score INTEGER DEFAULT 0,
            confidence_label TEXT,
            importance_score INTEGER DEFAULT 0,
            freshness_score INTEGER DEFAULT 0,
            accuracy_score INTEGER DEFAULT 0,
            global_impact INTEGER DEFAULT 0,
            regional_impact INTEGER DEFAULT 0,
            duplicate_confidence INTEGER DEFAULT 0,
            spam_probability INTEGER DEFAULT 0,
            priority TEXT DEFAULT 'normal',
            status TEXT DEFAULT 'accepted',
            source_count INTEGER DEFAULT 1,
            sources_json TEXT,
            evidence_json TEXT,
            forecast_json TEXT,
            metadata_json TEXT,
            published_at TEXT,
            expires_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            stream_key TEXT,
            title TEXT,
            forecast_body TEXT,
            confidence_score INTEGER,
            confidence_label TEXT,
            horizon TEXT,
            methodology TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_id INTEGER,
            stream_key TEXT,
            feedback_type TEXT,
            metadata_json TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_collector_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collector_key TEXT,
            stream_key TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            duration_ms INTEGER DEFAULT 0,
            events_seen INTEGER DEFAULT 0,
            events_accepted INTEGER DEFAULT 0,
            failure_reason TEXT,
            metadata_json TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_digest_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            stream_key TEXT,
            digest_type TEXT,
            status TEXT DEFAULT 'pending',
            scheduled_at TEXT,
            sent_at TEXT,
            event_ids_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_delivery_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER DEFAULT 0,
            user_id INTEGER,
            stream_key TEXT,
            delivery_type TEXT DEFAULT 'instant',
            status TEXT DEFAULT 'queued',
            channels_json TEXT,
            dedupe_key TEXT UNIQUE,
            scheduled_at TEXT,
            next_retry_at TEXT,
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            notification_id INTEGER DEFAULT 0,
            failure_reason TEXT,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            sent_at TEXT,
            canceled_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS intelligence_delivery_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            user_id INTEGER,
            stream_key TEXT,
            notification_id INTEGER DEFAULT 0,
            delivery_status TEXT,
            channels_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(event_id, user_id)
        )
        """
    )
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_user_intel_streams_user ON user_intelligence_streams(user_id, enabled, stream_key)",
        "CREATE INDEX IF NOT EXISTS idx_intel_events_stream_created ON intelligence_events(stream_key, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_intel_events_priority ON intelligence_events(priority, confidence_score, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_intel_sources_stream_status ON intelligence_sources(stream_key, status)",
        "CREATE INDEX IF NOT EXISTS idx_intel_forecasts_stream_created ON intelligence_forecasts(stream_key, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_intel_digest_jobs_status ON intelligence_digest_jobs(status, scheduled_at, user_id)",
        "CREATE INDEX IF NOT EXISTS idx_intel_delivery_jobs_status ON intelligence_delivery_jobs(status, scheduled_at, next_retry_at)",
        "CREATE INDEX IF NOT EXISTS idx_intel_delivery_jobs_event ON intelligence_delivery_jobs(event_id, user_id, stream_key)",
        "CREATE INDEX IF NOT EXISTS idx_intel_delivery_user ON intelligence_delivery_log(user_id, stream_key, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_intel_feedback_user ON intelligence_feedback(user_id, stream_key, created_at)",
    ]
    for sql in indexes:
        cur.execute(sql)
    conn.commit()
    seed_defaults(conn)
    if owns_conn:
        conn.close()


def seed_defaults(conn: Any | None = None) -> None:
    owns_conn = conn is None
    conn = conn or connect()
    cur = conn.cursor()
    now = now_iso()
    for stream in DEFAULT_STREAMS:
        cur.execute(
            """
            INSERT OR IGNORE INTO intelligence_streams
            (stream_key, display_name, purpose, category, default_priority, default_frequency,
             default_enabled, default_push, confidence_threshold, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stream["stream_key"],
                stream["display_name"],
                stream["purpose"],
                stream["category"],
                stream["default_priority"],
                stream["default_frequency"],
                1 if stream.get("default_enabled") else 0,
                1 if stream.get("default_push") else 0,
                int(stream.get("threshold") or 70),
                _json_dumps({"examples": stream.get("examples") or []}),
                now,
                now,
            ),
        )
        cur.execute(
            """
            UPDATE intelligence_streams
            SET display_name=?, purpose=?, category=?, default_priority=?, default_frequency=?,
                default_enabled=?, default_push=?, confidence_threshold=?, config_json=?, updated_at=?
            WHERE stream_key=?
            """,
            (
                stream["display_name"],
                stream["purpose"],
                stream["category"],
                stream["default_priority"],
                stream["default_frequency"],
                1 if stream.get("default_enabled") else 0,
                1 if stream.get("default_push") else 0,
                int(stream.get("threshold") or 70),
                _json_dumps({"examples": stream.get("examples") or []}),
                now,
                stream["stream_key"],
            ),
        )
    for source in SOURCE_CATALOG:
        status = "ready" if _source_env_present(source) else ("ready" if not source.get("required_env") else "config_missing")
        cur.execute(
            """
            INSERT OR IGNORE INTO intelligence_sources
            (source_key, display_name, stream_key, provider_type, trust_score, status, cache_seconds,
             required_env_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source["source_key"],
                source["display_name"],
                source["stream_key"],
                source["provider_type"],
                int(source.get("trust_score") or 70),
                status,
                int(source.get("cache_seconds") or 300),
                _json_dumps(source.get("required_env") or []),
                now,
                now,
            ),
        )
        cur.execute(
            "UPDATE intelligence_sources SET status=?, updated_at=? WHERE source_key=?",
            (status, now, source["source_key"]),
        )
    conn.commit()
    if owns_conn:
        conn.close()


def ensure_user_pack(user_id: int, conn: Any | None = None) -> None:
    if not user_id:
        return
    owns_conn = conn is None
    conn = conn or connect()
    ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    for stream in DEFAULT_STREAMS:
        cur.execute(
            """
            INSERT OR IGNORE INTO user_intelligence_streams
            (user_id, stream_key, enabled, frequency, digest_mode, push_enabled, email_enabled, sms_enabled,
             breaking_push_only, confidence_threshold, priority_filter, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'daily', ?, 0, 0, 1, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                stream["stream_key"],
                1 if stream.get("default_enabled") else 0,
                stream.get("default_frequency") or "digest",
                1 if stream.get("default_push") else 0,
                int(stream.get("threshold") or 70),
                stream.get("default_priority") or "normal",
                _json_dumps({"source": "default_intelligence_pack"}),
                now,
                now,
            ),
        )
    conn.commit()
    if owns_conn:
        conn.close()


def _stream_rows(cur: Any) -> list[dict[str, Any]]:
    cur.execute("SELECT * FROM intelligence_streams ORDER BY id")
    rows = cur.fetchall()
    return [format_stream(row) for row in rows]


def format_stream(row: Any, user_row: Any | None = None) -> dict[str, Any]:
    config = _json_loads(_row_get(row, "config_json"), {})
    user_meta = _json_loads(_row_get(user_row, "metadata_json"), {}) if user_row else {}
    return {
        "stream_key": _row_get(row, "stream_key") or _row_get(user_row, "stream_key"),
        "display_name": _row_get(row, "display_name") or "",
        "purpose": _row_get(row, "purpose") or "",
        "category": _row_get(row, "category") or "",
        "default_priority": _row_get(row, "default_priority") or "normal",
        "examples": config.get("examples") or [],
        "enabled": _bool(_row_get(user_row, "enabled"), _bool(_row_get(row, "default_enabled"), True)) if user_row is not None else _bool(_row_get(row, "default_enabled"), True),
        "frequency": _row_get(user_row, "frequency", _row_get(row, "default_frequency", "digest")) if user_row is not None else _row_get(row, "default_frequency", "digest"),
        "digest_mode": _row_get(user_row, "digest_mode", "daily") if user_row is not None else "daily",
        "push_enabled": _bool(_row_get(user_row, "push_enabled"), _bool(_row_get(row, "default_push"), False)) if user_row is not None else _bool(_row_get(row, "default_push"), False),
        "email_enabled": _bool(_row_get(user_row, "email_enabled"), False) if user_row is not None else False,
        "sms_enabled": _bool(_row_get(user_row, "sms_enabled"), False) if user_row is not None else False,
        "breaking_push_only": _bool(_row_get(user_row, "breaking_push_only"), True) if user_row is not None else True,
        "confidence_threshold": _int(_row_get(user_row, "confidence_threshold"), _int(_row_get(row, "confidence_threshold"), 70)) if user_row is not None else _int(_row_get(row, "confidence_threshold"), 70),
        "priority_filter": _row_get(user_row, "priority_filter", _row_get(row, "default_priority", "normal")) if user_row is not None else _row_get(row, "default_priority", "normal"),
        "metadata": user_meta,
    }


def format_event(row: Any) -> dict[str, Any]:
    confidence = _int(_row_get(row, "confidence_score"))
    metadata = _json_loads(_row_get(row, "metadata_json"), {})
    return {
        "id": _int(_row_get(row, "id")),
        "event_key": _row_get(row, "event_key") or "",
        "stream_key": _row_get(row, "stream_key") or "",
        "event_type": _row_get(row, "event_type") or "",
        "headline": _row_get(row, "headline") or "",
        "summary": _row_get(row, "summary") or "",
        "why_it_matters": _row_get(row, "why_it_matters") or "",
        "expected_impact": _row_get(row, "expected_impact") or "",
        "confidence_score": confidence,
        "confidence_label": _row_get(row, "confidence_label") or _confidence_label(confidence),
        "importance_score": _int(_row_get(row, "importance_score")),
        "freshness_score": _int(_row_get(row, "freshness_score")),
        "accuracy_score": _int(_row_get(row, "accuracy_score")),
        "global_impact": _int(_row_get(row, "global_impact")),
        "regional_impact": _int(_row_get(row, "regional_impact")),
        "duplicate_confidence": _int(_row_get(row, "duplicate_confidence")),
        "spam_probability": _int(_row_get(row, "spam_probability")),
        "priority": _row_get(row, "priority") or "normal",
        "status": _row_get(row, "status") or "accepted",
        "source_count": _int(_row_get(row, "source_count"), 1),
        "sources": _json_loads(_row_get(row, "sources_json"), []),
        "evidence": _json_loads(_row_get(row, "evidence_json"), []),
        "forecast": _json_loads(_row_get(row, "forecast_json"), {}),
        "metadata": metadata,
        "actions": validate_actions(metadata.get("actions")),
        "published_at": _row_get(row, "published_at") or _row_get(row, "created_at") or "",
        "created_at": _row_get(row, "created_at") or "",
        "updated_at": _row_get(row, "updated_at") or "",
        "read_time_seconds": max(15, min(120, int(len(str(_row_get(row, "summary") or "")) / 9) + 15)),
    }


def format_forecast(row: Any) -> dict[str, Any]:
    score = _int(_row_get(row, "confidence_score"))
    return {
        "id": _int(_row_get(row, "id")),
        "event_id": _int(_row_get(row, "event_id")),
        "stream_key": _row_get(row, "stream_key") or "",
        "title": _row_get(row, "title") or "",
        "forecast_body": _row_get(row, "forecast_body") or "",
        "confidence_score": score,
        "confidence_label": _row_get(row, "confidence_label") or _confidence_label(score),
        "horizon": _row_get(row, "horizon") or "24 hours",
        "methodology": _row_get(row, "methodology") or "Confidence-scored source and trend analysis.",
        "status": _row_get(row, "status") or "active",
        "created_at": _row_get(row, "created_at") or "",
    }


def _score_signal(payload: dict[str, Any], source_rows: list[dict[str, Any]]) -> dict[str, int]:
    importance = max(0, min(_int(payload.get("importance_score"), 50), 100))
    freshness = max(0, min(_int(payload.get("freshness_score"), 70), 100))
    global_impact = max(0, min(_int(payload.get("global_impact"), 30), 100))
    regional_impact = max(0, min(_int(payload.get("regional_impact"), 20), 100))
    spam_probability = max(0, min(_int(payload.get("spam_probability"), 8), 100))
    duplicate_confidence = max(0, min(_int(payload.get("duplicate_confidence"), min(len(source_rows) * 18, 80)), 100))
    if source_rows:
        source_trust = sum(_int(source.get("trust_score"), 70) for source in source_rows) / len(source_rows)
    else:
        source_trust = 50
    accuracy = max(0, min(_int(payload.get("accuracy_score"), int((source_trust + duplicate_confidence) / 2)), 100))
    confidence = int(
        (accuracy * 0.34)
        + (freshness * 0.20)
        + (importance * 0.24)
        + (max(global_impact, regional_impact) * 0.12)
        + ((100 - spam_probability) * 0.10)
    )
    return {
        "confidence_score": max(0, min(confidence, 100)),
        "importance_score": importance,
        "freshness_score": freshness,
        "accuracy_score": accuracy,
        "global_impact": global_impact,
        "regional_impact": regional_impact,
        "duplicate_confidence": duplicate_confidence,
        "spam_probability": spam_probability,
    }


def _source_rows_for_keys(cur: Any, keys: list[str]) -> list[dict[str, Any]]:
    if not keys:
        return []
    rows = []
    for key in keys[:20]:
        cur.execute("SELECT * FROM intelligence_sources WHERE source_key=? LIMIT 1", (_slug(key),))
        row = cur.fetchone()
        if row:
            rows.append({
                "source_key": _row_get(row, "source_key"),
                "display_name": _row_get(row, "display_name"),
                "trust_score": _int(_row_get(row, "trust_score"), 70),
                "status": _row_get(row, "status") or "",
            })
    return rows


def _event_key(payload: dict[str, Any], stream_key: str, source_keys: list[str]) -> str:
    explicit = payload.get("event_key") or payload.get("dedupe_key")
    if explicit:
        return hashlib.sha256(str(explicit).encode("utf-8")).hexdigest()
    basis = "|".join([
        stream_key,
        _slug(payload.get("event_type") or "signal"),
        _compact(payload.get("headline") or payload.get("title") or "", 220).lower(),
        _compact(payload.get("published_at") or now_iso()[:13], 40),
        ",".join(sorted(source_keys)),
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _priority(payload: dict[str, Any], confidence: int) -> str:
    raw = _slug(payload.get("priority") or "", 20)
    if raw in PRIORITIES:
        return raw
    if confidence >= 90 or _bool(payload.get("breaking"), False):
        return "breaking"
    if confidence >= 82:
        return "high"
    if confidence >= 62:
        return "normal"
    return "low"


def _forecast_for_event(stream_key: str, headline: str, expected_impact: str, confidence: int, priority: str) -> dict[str, Any]:
    if priority not in {"breaking", "urgent", "high"} and stream_key not in {"crypto_pulse", "market_pulse", "creator_pulse"}:
        return {}
    horizon = "24 hours" if stream_key in {"crypto_pulse", "market_pulse", "security_pulse"} else "next cycle"
    body = expected_impact or f"{headline} may increase activity in the {stream_key.replace('_', ' ')} stream during the {horizon}."
    return {
        "title": f"Forecast: {headline[:120]}",
        "forecast_body": body,
        "confidence_score": max(0, min(confidence - 8, 100)),
        "confidence_label": _confidence_label(max(0, min(confidence - 8, 100))),
        "horizon": horizon,
        "methodology": "Source confidence, freshness, impact scoring, duplicate evidence, and spam-risk weighting.",
    }


def ingest_signal(payload: dict[str, Any], *, deliver: bool = False, target_user_id: int = 0) -> dict[str, Any]:
    payload = dict(payload or {})
    stream_key = _slug(payload.get("stream_key") or "pulsesoc_discoveries")
    if stream_key not in STREAM_KEYS:
        return {"ok": False, "error": "invalid_stream", "message": "Unknown intelligence stream.", "http_status": 400}
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM intelligence_streams WHERE stream_key=? LIMIT 1", (stream_key,))
        stream = cur.fetchone()
        if not stream:
            return {"ok": False, "error": "missing_stream", "message": "Stream is not configured.", "http_status": 500}
        source_keys = [_slug(key) for key in (payload.get("source_keys") or payload.get("sources") or []) if key]
        source_rows = _source_rows_for_keys(cur, source_keys)
        scores = _score_signal(payload, source_rows)
        threshold = _int(_row_get(stream, "confidence_threshold"), 70)
        status = "accepted" if scores["confidence_score"] >= threshold and scores["spam_probability"] < 65 else "suppressed"
        priority = _priority(payload, scores["confidence_score"])
        event_key = _event_key(payload, stream_key, source_keys)
        now = now_iso()
        headline = _compact(payload.get("headline") or payload.get("title") or "PulseSoc intelligence signal", 240)
        summary = _compact(payload.get("summary") or payload.get("body") or headline, 1200)
        why = _compact(payload.get("why_it_matters") or "This signal passed PulseSoc confidence and relevance checks.", 800)
        impact = _compact(payload.get("expected_impact") or payload.get("impact") or "", 800)
        forecast = _forecast_for_event(stream_key, headline, impact, scores["confidence_score"], priority)
        sources_json = _json_dumps(source_rows)
        evidence_json = _json_dumps(payload.get("evidence") or [])
        payload_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        actions = validate_actions(payload_metadata.get("actions") or payload.get("actions"))
        if not actions:
            actions = default_actions_for_signal(
                stream_key,
                payload.get("event_type") or "signal",
                payload_metadata.get("deep_link") or payload.get("deep_link") or "/pulse/intelligence",
            )
        metadata = {
            **payload_metadata,
            "collector": payload.get("collector") or "",
            "no_investment_advice": stream_key == "crypto_pulse",
            "ai_summary_status": "queued" if payload.get("ai_summary_requested") else "not_required",
            "source_payload_hash": hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()[:24],
            "actions": actions,
        }
        cur.execute("SELECT * FROM intelligence_events WHERE event_key=? LIMIT 1", (event_key,))
        existing = cur.fetchone()
        if existing:
            event_id = _int(_row_get(existing, "id"))
            merged_sources = _json_loads(_row_get(existing, "sources_json"), [])
            known = {item.get("source_key") for item in merged_sources if isinstance(item, dict)}
            for source in source_rows:
                if source.get("source_key") not in known:
                    merged_sources.append(source)
            merged_confidence = min(100, max(scores["confidence_score"], _int(_row_get(existing, "confidence_score"))) + min(8, len(merged_sources)))
            cur.execute(
                """
                UPDATE intelligence_events
                SET confidence_score=?, confidence_label=?, duplicate_confidence=?, source_count=?,
                    sources_json=?, updated_at=?
                WHERE id=?
                """,
                (merged_confidence, _confidence_label(merged_confidence), max(scores["duplicate_confidence"], _int(_row_get(existing, "duplicate_confidence"))), len(merged_sources), _json_dumps(merged_sources), now, event_id),
            )
            conn.commit()
            return {"ok": True, "deduped": True, "event_id": event_id, "confidence_score": merged_confidence, "status": _row_get(existing, "status") or status}
        cur.execute(
            """
            INSERT INTO intelligence_events
            (event_key, stream_key, event_type, headline, summary, why_it_matters, expected_impact,
             confidence_score, confidence_label, importance_score, freshness_score, accuracy_score,
             global_impact, regional_impact, duplicate_confidence, spam_probability, priority, status,
             source_count, sources_json, evidence_json, forecast_json, metadata_json, published_at,
             expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_key,
                stream_key,
                _slug(payload.get("event_type") or "signal"),
                headline,
                summary,
                why,
                impact,
                scores["confidence_score"],
                _confidence_label(scores["confidence_score"]),
                scores["importance_score"],
                scores["freshness_score"],
                scores["accuracy_score"],
                scores["global_impact"],
                scores["regional_impact"],
                scores["duplicate_confidence"],
                scores["spam_probability"],
                priority,
                status,
                max(1, len(source_rows)),
                sources_json,
                evidence_json,
                _json_dumps(forecast),
                _json_dumps(metadata),
                _compact(payload.get("published_at") or now, 80),
                _compact(payload.get("expires_at") or (datetime.utcnow() + timedelta(days=7)).replace(microsecond=0).isoformat() + "Z", 80),
                now,
                now,
            ),
        )
        event_id = _int(getattr(cur, "lastrowid", 0))
        if forecast and status == "accepted":
            cur.execute(
                """
                INSERT INTO intelligence_forecasts
                (event_id, stream_key, title, forecast_body, confidence_score, confidence_label,
                 horizon, methodology, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    event_id,
                    stream_key,
                    forecast["title"],
                    forecast["forecast_body"],
                    forecast["confidence_score"],
                    forecast["confidence_label"],
                    forecast["horizon"],
                    forecast["methodology"],
                    now,
                    now,
                ),
            )
        conn.commit()
        delivery = {}
        if deliver and status == "accepted":
            delivery = deliver_event(event_id, target_user_id=target_user_id)
        return {"ok": True, "event_id": event_id, "status": status, "confidence_score": scores["confidence_score"], "priority": priority, "delivery": delivery}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("PULSESOC_INTELLIGENCE_INGEST_FAILED error=%s", exc)
        return {"ok": False, "error": "intelligence_ingest_failed", "message": "Intelligence signal could not be processed safely.", "http_status": 500}
    finally:
        conn.close()


def _priority_allowed(event_priority: str, filter_value: str) -> bool:
    order = {"low": 0, "normal": 1, "high": 2, "urgent": 3, "breaking": 4}
    return order.get(event_priority, 1) >= order.get(filter_value or "normal", 1)


def _channels_for_subscription(subscription: dict[str, Any], event: dict[str, Any]) -> list[str]:
    channels = ["in_app"]
    priority = event.get("priority") or "normal"
    if subscription.get("push_enabled") and (
        _priority_allowed(priority, "high") or not subscription.get("breaking_push_only")
    ):
        channels.append("push")
    if subscription.get("email_enabled") and _priority_allowed(priority, "high"):
        channels.append("email")
    if subscription.get("sms_enabled") and priority in {"urgent", "breaking"}:
        channels.append("sms")
    return list(dict.fromkeys(channels))


def _subscription_for_user(cur: Any, user_id: int, stream_key: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT u.*, s.display_name, s.purpose, s.category, s.default_priority, s.default_frequency,
               s.default_enabled, s.default_push, s.confidence_threshold AS stream_threshold, s.config_json
        FROM user_intelligence_streams u
        JOIN intelligence_streams s ON s.stream_key=u.stream_key
        WHERE u.user_id=? AND u.stream_key=?
        LIMIT 1
        """,
        (int(user_id), stream_key),
    )
    row = cur.fetchone()
    if not row:
        return None
    return format_stream(row, row)


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _priority_rank(priority: str) -> int:
    return {"low": 0, "normal": 1, "high": 2, "urgent": 3, "breaking": 4}.get(priority or "normal", 1)


def _intelligence_deep_link(event: dict[str, Any], delivery_type: str = "instant") -> str:
    if delivery_type == "forecast":
        return f"/pulse/forecasts?event={int(event.get('id') or 0)}"
    if delivery_type == "digest":
        return f"/pulse/briefing?event={int(event.get('id') or 0)}"
    return f"/pulse/alerts?event={int(event.get('id') or 0)}"


def _quiet_hours_schedule(subscription: dict[str, Any], event: dict[str, Any]) -> str:
    if not subscription.get("metadata"):
        metadata = {}
    else:
        metadata = subscription.get("metadata") or {}
    priority = event.get("priority") or "normal"
    if not subscription.get("quiet_hours_enabled") or priority in {"urgent", "breaking"}:
        return now_iso()
    start = _compact(metadata.get("quiet_hours_start") or "22:00", 5)
    end = _compact(metadata.get("quiet_hours_end") or "07:00", 5)

    def parse_clock(text: str, fallback: tuple[int, int]) -> tuple[int, int]:
        try:
            hour, minute = str(text or "").split(":", 1)
            return max(0, min(int(hour), 23)), max(0, min(int(minute), 59))
        except Exception:
            return fallback

    start_h, start_m = parse_clock(start, (22, 0))
    end_h, end_m = parse_clock(end, (7, 0))
    current = datetime.utcnow().replace(microsecond=0)
    start_today = current.replace(hour=start_h, minute=start_m, second=0)
    end_today = current.replace(hour=end_h, minute=end_m, second=0)
    if start_today <= end_today:
        active = start_today <= current < end_today
        next_end = end_today
    else:
        active = current >= start_today or current < end_today
        next_end = end_today if current < end_today else end_today + timedelta(days=1)
    return next_end.isoformat() + "Z" if active else now_iso()


def _delivery_type_for_event(event: dict[str, Any], subscription: dict[str, Any], requested: str = "") -> str:
    requested = _slug(requested, 30) if requested else ""
    if requested in {"instant", "digest", "forecast", "feature_discovery"}:
        return requested
    frequency = _slug(subscription.get("frequency") or "digest", 30)
    if frequency == "muted":
        return "muted"
    if event.get("forecast") and _priority_rank(event.get("priority") or "normal") >= _priority_rank("high"):
        return "forecast"
    if event.get("stream_key") in {"pulsesoc_discoveries", "pulsesoc_pulse"} and event.get("priority") in {"low", "normal"}:
        return "feature_discovery"
    if event.get("priority") in {"breaking", "urgent", "high"} or frequency == "realtime":
        return "instant"
    return "digest"


def _notification_type_for_delivery(delivery_type: str) -> str:
    if delivery_type == "digest":
        return "intelligence_digest"
    if delivery_type == "forecast":
        return "intelligence_forecast"
    return "intelligence_pulse"


def _target_user_ids(cur: Any, stream_key: str, target_user_id: int = 0, limit: int = 500, conn: Any | None = None, all_users: bool = False) -> list[int]:
    if target_user_id:
        ensure_user_pack(int(target_user_id), conn)
        return [int(target_user_id)]
    user_ids: list[int] = []
    if not all_users:
        cur.execute(
            """
            SELECT user_id FROM user_intelligence_streams
            WHERE stream_key=? AND enabled=1
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (stream_key, int(limit or 500)),
        )
        user_ids.extend(int(_row_get(item, "user_id", item[0] if item else 0)) for item in cur.fetchall())
        if user_ids:
            return [user_id for user_id in dict.fromkeys(user_ids) if user_id]
    cur.execute("SELECT user_id FROM users ORDER BY COALESCE(updated_at, created_at, signup_time, '') DESC LIMIT ?", (int(limit or 500),))
    user_ids = [int(_row_get(item, "user_id", item[0] if item else 0)) for item in cur.fetchall()]
    for user_id in user_ids[: int(limit or 500)]:
        ensure_user_pack(user_id, conn)
    return [user_id for user_id in dict.fromkeys(user_ids) if user_id]


def _upsert_delivery_log(
    cur: Any,
    event_id: int,
    user_id: int,
    stream_key: str,
    status: str,
    channels: list[str] | tuple[str, ...] | None = None,
    notification_id: int = 0,
) -> None:
    timestamp = now_iso()
    cur.execute(
        """
        INSERT OR IGNORE INTO intelligence_delivery_log
        (event_id, user_id, stream_key, notification_id, delivery_status, channels_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(event_id), int(user_id), stream_key, int(notification_id or 0), status, _json_dumps(list(channels or [])), timestamp, timestamp),
    )
    cur.execute(
        """
        UPDATE intelligence_delivery_log
        SET notification_id=?, delivery_status=?, channels_json=?, updated_at=?
        WHERE event_id=? AND user_id=?
        """,
        (int(notification_id or 0), status, _json_dumps(list(channels or [])), timestamp, int(event_id), int(user_id)),
    )


def _queue_digest_job(cur: Any, event: dict[str, Any], user_id: int, digest_type: str = "daily") -> dict[str, Any]:
    scheduled_at = now_iso()
    event_id = int(event.get("id") or 0)
    stream_key = event.get("stream_key") or ""
    dedupe_status = "queued"
    cur.execute(
        """
        SELECT id, event_ids_json FROM intelligence_digest_jobs
        WHERE user_id=? AND stream_key=? AND digest_type=? AND status='pending'
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id), stream_key, digest_type),
    )
    existing = cur.fetchone()
    if existing:
        job_id = _int(_row_get(existing, "id", existing[0] if existing else 0))
        event_ids = _json_loads(_row_get(existing, "event_ids_json"), [])
        if not isinstance(event_ids, list):
            event_ids = []
        if event_id not in [int(item or 0) for item in event_ids]:
            event_ids.append(event_id)
            cur.execute(
                "UPDATE intelligence_digest_jobs SET event_ids_json=?, updated_at=? WHERE id=?",
                (_json_dumps(event_ids[-25:]), now_iso(), job_id),
            )
        else:
            dedupe_status = "duplicate"
    else:
        cur.execute(
            """
            INSERT INTO intelligence_digest_jobs
            (user_id, stream_key, digest_type, status, scheduled_at, event_ids_json, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (int(user_id), stream_key, digest_type, scheduled_at, _json_dumps([event_id]), now_iso(), now_iso()),
        )
        job_id = _int(getattr(cur, "lastrowid", 0))
    _upsert_delivery_log(cur, event_id, int(user_id), stream_key, "digest_queued", ["in_app"])
    return {"job_id": job_id, "status": dedupe_status, "delivery_type": "digest"}


def queue_event_delivery(
    event_id: int,
    *,
    target_user_id: int = 0,
    limit: int = 500,
    delivery_type: str = "",
    schedule_at: str = "",
    all_users: bool = False,
) -> dict[str, Any]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM intelligence_events WHERE id=? LIMIT 1", (int(event_id),))
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "event_not_found", "message": "Intelligence event not found.", "http_status": 404}
        event = format_event(row)
        if event["status"] != "accepted":
            return {"ok": True, "queued": 0, "skipped": 1, "reason": event["status"]}
        user_ids = _target_user_ids(cur, event["stream_key"], target_user_id=target_user_id, limit=limit, conn=conn, all_users=all_users)
        queued = 0
        digest_queued = 0
        skipped = 0
        jobs = []
        for user_id in user_ids:
            subscription = _subscription_for_user(cur, user_id, event["stream_key"])
            if not subscription or not subscription.get("enabled"):
                skipped += 1
                _upsert_delivery_log(cur, event["id"], user_id, event["stream_key"], "skipped_disabled", [])
                continue
            if event["confidence_score"] < int(subscription.get("confidence_threshold") or 70):
                skipped += 1
                _upsert_delivery_log(cur, event["id"], user_id, event["stream_key"], "skipped_threshold", [])
                continue
            if not _priority_allowed(event["priority"], subscription.get("priority_filter") or "normal"):
                skipped += 1
                _upsert_delivery_log(cur, event["id"], user_id, event["stream_key"], "skipped_priority", [])
                continue
            resolved_delivery_type = _delivery_type_for_event(event, subscription, delivery_type)
            if resolved_delivery_type == "muted":
                skipped += 1
                _upsert_delivery_log(cur, event["id"], user_id, event["stream_key"], "skipped_muted", [])
                continue
            if resolved_delivery_type == "digest":
                jobs.append(_queue_digest_job(cur, event, user_id, subscription.get("digest_mode") or "daily"))
                digest_queued += 1
                continue
            channels = _channels_for_subscription(subscription, event)
            scheduled_at = _compact(schedule_at, 80) if schedule_at else _quiet_hours_schedule(subscription, event)
            dedupe = f"intelligence:{resolved_delivery_type}:{user_id}:{event['id']}:{event['stream_key']}"
            cur.execute("SELECT id FROM intelligence_delivery_jobs WHERE dedupe_key=? LIMIT 1", (dedupe,))
            existing = cur.fetchone()
            if existing:
                skipped += 1
                jobs.append({"job_id": _int(_row_get(existing, "id", existing[0] if existing else 0)), "status": "duplicate", "user_id": user_id})
                _upsert_delivery_log(cur, event["id"], user_id, event["stream_key"], "duplicate", channels)
                continue
            cur.execute(
                """
                INSERT INTO intelligence_delivery_jobs
                (event_id, user_id, stream_key, delivery_type, status, channels_json, dedupe_key,
                 scheduled_at, next_retry_at, attempts, max_attempts, notification_id, failure_reason,
                 metadata_json, created_at, updated_at, sent_at, canceled_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, '', 0, 3, 0, '', ?, ?, ?, '', '')
                """,
                (
                    event["id"],
                    user_id,
                    event["stream_key"],
                    resolved_delivery_type,
                    _json_dumps(channels),
                    dedupe,
                    scheduled_at,
                    _json_dumps({
                        "stream_name": subscription.get("display_name"),
                        "quiet_hours_deferred": scheduled_at != now_iso(),
                        "requested_delivery_type": delivery_type,
                    }),
                    now_iso(),
                    now_iso(),
                ),
            )
            job_id = _int(getattr(cur, "lastrowid", 0))
            queued += 1
            _upsert_delivery_log(cur, event["id"], user_id, event["stream_key"], "queued", channels)
            jobs.append({"job_id": job_id, "user_id": user_id, "status": "queued", "delivery_type": resolved_delivery_type, "channels": channels, "scheduled_at": scheduled_at})
        conn.commit()
        return {"ok": True, "queued": queued, "digest_queued": digest_queued, "skipped": skipped, "jobs": jobs[:50]}
    finally:
        conn.close()


def _send_delivery_job(cur: Any, job: Any) -> dict[str, Any]:
    job_id = _int(_row_get(job, "id", job[0] if job else 0))
    event_id = _int(_row_get(job, "event_id"))
    user_id = _int(_row_get(job, "user_id"))
    delivery_type = _row_get(job, "delivery_type") or "instant"
    cur.execute("SELECT * FROM intelligence_events WHERE id=? LIMIT 1", (event_id,))
    row = cur.fetchone()
    if not row:
        return {"ok": False, "permanent": True, "reason": "event_not_found"}
    event = format_event(row)
    subscription = _subscription_for_user(cur, user_id, event["stream_key"])
    if not subscription or not subscription.get("enabled"):
        _upsert_delivery_log(cur, event_id, user_id, event["stream_key"], "skipped_disabled", [])
        return {"ok": False, "permanent": True, "reason": "stream_disabled"}
    channels = _json_loads(_row_get(job, "channels_json"), ["in_app"])
    if not isinstance(channels, list) or not channels:
        channels = ["in_app"]
    notification_type = _notification_type_for_delivery(delivery_type)
    title = event["headline"]
    body = event["summary"]
    if delivery_type == "forecast" and event.get("forecast"):
        title = event["forecast"].get("title") or title
        body = event["forecast"].get("forecast_body") or body
    if delivery_type == "feature_discovery":
        title = f"Pulse Discovery: {title}"
    try:
        connection = getattr(cur, "connection", None)
        if connection is not None:
            connection.commit()
    except Exception:
        pass
    result = pulsesoc_notification_system.intake_event(
        event_type=notification_type,
        recipient_user_id=user_id,
        actor_user_id=0,
        source_type="intelligence_event",
        source_id=str(event_id),
        title=title,
        body=body,
        preview=body,
        deep_link=_intelligence_deep_link(event, delivery_type),
        metadata={
            "stream_key": event["stream_key"],
            "stream_name": subscription.get("display_name"),
            "delivery_type": delivery_type,
            "signal_id": event_id,
            "confidence": event["confidence_score"],
            "confidence_score": event["confidence_score"],
            "confidence_label": event["confidence_label"],
            "severity": event["priority"],
            "source_count": event["source_count"],
            "why_it_matters": event["why_it_matters"],
            "expected_impact": event["expected_impact"],
            "sources": event["sources"],
            "forecast": event["forecast"],
            "actions": event.get("actions") or [],
            "dedupe_key": _row_get(job, "dedupe_key") or f"intelligence:{delivery_type}:{user_id}:{event_id}",
            "push_allowed": "push" in channels,
            "email_allowed": "email" in channels,
            "sms_allowed": "sms" in channels,
        },
        category="intelligence",
        priority="urgent" if event["priority"] in {"breaking", "urgent"} else ("high" if event["priority"] == "high" else "normal"),
        urgency="immediate" if event["priority"] in {"breaking", "urgent", "high"} else "standard",
        channels=channels,
        dedupe_key=_row_get(job, "dedupe_key") or f"intelligence:{delivery_type}:{user_id}:{event_id}",
    )
    notification_id = _int(result.get("notification_id"))
    if result.get("ok") and (notification_id or result.get("deduped")):
        _upsert_delivery_log(cur, event_id, user_id, event["stream_key"], "sent", channels, notification_id)
        return {"ok": True, "notification_id": notification_id, "result": result}
    if result.get("suppressed"):
        _upsert_delivery_log(cur, event_id, user_id, event["stream_key"], f"skipped_{result.get('reason') or 'suppressed'}", channels, 0)
        return {"ok": False, "permanent": True, "reason": result.get("reason") or "suppressed"}
    return {"ok": False, "permanent": False, "reason": result.get("message") or result.get("error") or "notification_failed"}


def process_delivery_queue(limit: int = 100) -> dict[str, Any]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        due = now_iso()
        cur.execute(
            """
            SELECT * FROM intelligence_delivery_jobs
            WHERE status IN ('queued', 'retry') AND COALESCE(scheduled_at, '') <= ?
              AND (COALESCE(next_retry_at, '')='' OR next_retry_at <= ?)
            ORDER BY id ASC
            LIMIT ?
            """,
            (due, due, int(limit or 100)),
        )
        jobs = cur.fetchall()
        sent = 0
        failed = 0
        retried = 0
        skipped = 0
        results: list[dict[str, Any]] = []
        for job in jobs:
            job_id = _int(_row_get(job, "id", job[0] if job else 0))
            attempts = _int(_row_get(job, "attempts"), 0) + 1
            cur.execute(
                "UPDATE intelligence_delivery_jobs SET status='processing', attempts=?, updated_at=? WHERE id=?",
                (attempts, now_iso(), job_id),
            )
            conn.commit()
            result = _send_delivery_job(cur, job)
            if result.get("ok"):
                sent += 1
                cur.execute(
                    "UPDATE intelligence_delivery_jobs SET status='sent', notification_id=?, sent_at=?, updated_at=?, failure_reason='' WHERE id=?",
                    (_int(result.get("notification_id")), now_iso(), now_iso(), job_id),
                )
            elif result.get("permanent"):
                skipped += 1
                cur.execute(
                    "UPDATE intelligence_delivery_jobs SET status='skipped', failure_reason=?, updated_at=? WHERE id=?",
                    (_compact(result.get("reason") or "skipped", 240), now_iso(), job_id),
                )
            elif attempts >= _int(_row_get(job, "max_attempts"), 3):
                failed += 1
                cur.execute(
                    "UPDATE intelligence_delivery_jobs SET status='failed', failure_reason=?, updated_at=? WHERE id=?",
                    (_compact(result.get("reason") or "failed", 240), now_iso(), job_id),
                )
            else:
                retried += 1
                next_retry = (datetime.utcnow() + timedelta(minutes=min(30, attempts * 5))).replace(microsecond=0).isoformat() + "Z"
                cur.execute(
                    "UPDATE intelligence_delivery_jobs SET status='retry', next_retry_at=?, failure_reason=?, updated_at=? WHERE id=?",
                    (next_retry, _compact(result.get("reason") or "retry", 240), now_iso(), job_id),
                )
            results.append({"job_id": job_id, **result})
        conn.commit()
        return {"ok": True, "processed": len(jobs), "sent": sent, "failed": failed, "retried": retried, "skipped": skipped, "results": results[:50]}
    finally:
        conn.close()


def process_digest_jobs(limit: int = 50) -> dict[str, Any]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        due = now_iso()
        cur.execute(
            """
            SELECT * FROM intelligence_digest_jobs
            WHERE status='pending' AND COALESCE(scheduled_at, '') <= ?
            ORDER BY id ASC LIMIT ?
            """,
            (due, int(limit or 50)),
        )
        jobs = cur.fetchall()
        sent = 0
        skipped = 0
        results: list[dict[str, Any]] = []
        for job in jobs:
            job_id = _int(_row_get(job, "id", job[0] if job else 0))
            user_id = _int(_row_get(job, "user_id"))
            event_ids = _json_loads(_row_get(job, "event_ids_json"), [])
            if not isinstance(event_ids, list):
                event_ids = []
            event_ids = [int(item or 0) for item in event_ids if int(item or 0)]
            if not event_ids:
                skipped += 1
                cur.execute("UPDATE intelligence_digest_jobs SET status='skipped', updated_at=? WHERE id=?", (now_iso(), job_id))
                continue
            placeholders = ",".join("?" for _ in event_ids[:25])
            cur.execute(f"SELECT * FROM intelligence_events WHERE id IN ({placeholders}) AND status='accepted' ORDER BY confidence_score DESC, created_at DESC", tuple(event_ids[:25]))
            events = [format_event(row) for row in cur.fetchall()]
            if not events:
                skipped += 1
                cur.execute("UPDATE intelligence_digest_jobs SET status='skipped', updated_at=? WHERE id=?", (now_iso(), job_id))
                continue
            headline = "Daily Briefing: strongest PulseSoc signals"
            body = " · ".join(event["headline"] for event in events[:5])[:800]
            stream_key = _row_get(job, "stream_key") or events[0].get("stream_key") or "pulsesoc_discoveries"
            channels = ["in_app"]
            subscription = _subscription_for_user(cur, user_id, stream_key)
            if subscription and subscription.get("push_enabled") and any(_priority_allowed(event.get("priority"), "high") for event in events):
                channels.append("push")
            conn.commit()
            result = pulsesoc_notification_system.intake_event(
                event_type="intelligence_digest",
                recipient_user_id=user_id,
                actor_user_id=0,
                source_type="intelligence_digest",
                source_id=str(job_id),
                title=headline,
                body=body,
                preview=body,
                deep_link="/pulse/briefing",
                metadata={
                    "delivery_type": "digest",
                    "stream_key": stream_key,
                    "event_ids": event_ids[:25],
                    "signal_count": len(events),
                    "actions": default_actions_for_signal("pulsesoc_discoveries", "digest", "/pulse/briefing"),
                },
                category="intelligence",
                priority="normal",
                urgency="standard",
                channels=channels,
                dedupe_key=f"intelligence-digest:{user_id}:{job_id}",
            )
            notification_id = _int(result.get("notification_id"))
            if result.get("ok") and notification_id:
                sent += 1
                for event in events:
                    _upsert_delivery_log(cur, event["id"], user_id, event["stream_key"], "digest_sent", channels, notification_id)
                cur.execute("UPDATE intelligence_digest_jobs SET status='sent', sent_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), job_id))
            else:
                skipped += 1
                cur.execute("UPDATE intelligence_digest_jobs SET status='skipped', updated_at=? WHERE id=?", (now_iso(), job_id))
            results.append({"job_id": job_id, "notification_id": notification_id, "ok": bool(result.get("ok"))})
        conn.commit()
        return {"ok": True, "processed": len(jobs), "sent": sent, "skipped": skipped, "results": results[:50]}
    finally:
        conn.close()


def generate_digest_jobs(user_id: int = 0, *, stream_key: str = "", limit: int = 500, digest_type: str = "daily") -> dict[str, Any]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        selected_stream = _slug(stream_key, 80) if stream_key else ""
        if selected_stream and selected_stream not in STREAM_KEYS:
            return {"ok": False, "error": "invalid_stream", "message": "Unknown intelligence stream.", "http_status": 400}
        params: list[Any] = []
        stream_filter = ""
        if selected_stream:
            stream_filter = "AND stream_key=?"
            params.append(selected_stream)
        cur.execute(
            f"""
            SELECT * FROM intelligence_events
            WHERE status='accepted' AND priority IN ('low', 'normal', 'high') {stream_filter}
            ORDER BY confidence_score DESC, created_at DESC
            LIMIT ?
            """,
            (*params, int(limit or 500)),
        )
        events = [format_event(row) for row in cur.fetchall()]
        by_stream: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            by_stream.setdefault(event["stream_key"], []).append(event)
        target_users = [int(user_id)] if user_id else []
        if not target_users:
            cur.execute("SELECT DISTINCT user_id FROM user_intelligence_streams WHERE enabled=1 LIMIT ?", (int(limit or 500),))
            target_users = [int(_row_get(row, "user_id", row[0] if row else 0)) for row in cur.fetchall()]
        queued = 0
        for target in target_users:
            ensure_user_pack(target, conn)
            for stream, stream_events in by_stream.items():
                subscription = _subscription_for_user(cur, target, stream)
                if not subscription or not subscription.get("enabled"):
                    continue
                if _slug(subscription.get("frequency") or "digest") == "realtime":
                    continue
                for event in stream_events[:10]:
                    _queue_digest_job(cur, event, target, digest_type or subscription.get("digest_mode") or "daily")
                    queued += 1
        conn.commit()
        return {"ok": True, "queued": queued, "users": len(target_users), "streams": list(by_stream)}
    finally:
        conn.close()


def deliver_event(event_id: int, *, target_user_id: int = 0, limit: int = 500) -> dict[str, Any]:
    queued = queue_event_delivery(event_id, target_user_id=target_user_id, limit=limit)
    processed = process_delivery_queue(limit=min(int(limit or 100), 100))
    digest = process_digest_jobs(limit=min(int(limit or 50), 50))
    return {"ok": bool(queued.get("ok")), "queue": queued, "processing": processed, "digests": digest}


def center_state(user_id: int, limit: int = 40) -> dict[str, Any]:
    conn = connect()
    try:
        ensure_user_pack(user_id, conn)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.*, u.enabled, u.frequency, u.digest_mode, u.push_enabled, u.email_enabled, u.sms_enabled,
                   u.breaking_push_only, u.confidence_threshold, u.priority_filter, u.metadata_json
            FROM intelligence_streams s
            LEFT JOIN user_intelligence_streams u ON u.stream_key=s.stream_key AND u.user_id=?
            ORDER BY s.id
            """,
            (int(user_id),),
        )
        streams = [format_stream(row, row) for row in cur.fetchall()]
        enabled_streams = [item["stream_key"] for item in streams if item.get("enabled")]
        events: list[dict[str, Any]] = []
        if enabled_streams:
            placeholders = ",".join("?" for _ in enabled_streams)
            cur.execute(
                f"""
                SELECT * FROM intelligence_events
                WHERE status='accepted' AND stream_key IN ({placeholders})
                ORDER BY
                  CASE priority WHEN 'breaking' THEN 5 WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'normal' THEN 2 ELSE 1 END DESC,
                  confidence_score DESC,
                  created_at DESC
                LIMIT ?
                """,
                (*enabled_streams, int(limit or 40)),
            )
            events = [format_event(row) for row in cur.fetchall()]
        forecasts: list[dict[str, Any]] = []
        if enabled_streams:
            placeholders = ",".join("?" for _ in enabled_streams)
            cur.execute(
                f"""
                SELECT * FROM intelligence_forecasts
                WHERE status='active' AND stream_key IN ({placeholders})
                ORDER BY confidence_score DESC, created_at DESC
                LIMIT 20
                """,
                tuple(enabled_streams),
            )
            forecasts = [format_forecast(row) for row in cur.fetchall()]
        summary = {
            "streams_enabled": len(enabled_streams),
            "signals": len(events),
            "forecasts": len(forecasts),
            "breaking": len([event for event in events if event.get("priority") in {"breaking", "urgent"}]),
            "avg_confidence": int(sum(event.get("confidence_score", 0) for event in events) / max(1, len(events))),
        }
        return {
            "ok": True,
            "center_name": PUBLIC_CENTER_NAME,
            "summary": summary,
            "streams": streams,
            "events": events,
            "forecasts": forecasts,
            "privacy": {
                "private_conversations_used": False,
                "private_calls_used": False,
                "learning_mode": "platform_knowledge_feedback_and_opt_in_memory",
            },
        }
    finally:
        conn.close()


def user_surface(surface_key: str = "alerts") -> dict[str, Any]:
    key = _slug(surface_key or "alerts", 40)
    surface = USER_SURFACES.get(key) or USER_SURFACES["alerts"]
    return dict(surface)


def user_surface_state(user_id: int, surface_key: str = "alerts", limit: int = 40) -> dict[str, Any]:
    surface = user_surface(surface_key)
    state = center_state(user_id, limit=max(1, min(int(limit or 40), 100)))
    visible_streams = list(state.get("streams") or [])
    allowed_streams = set(surface.get("stream_keys") or [])
    if allowed_streams:
        visible_streams = [item for item in visible_streams if item.get("stream_key") in allowed_streams]
        state["streams"] = visible_streams
        state["events"] = [item for item in state.get("events") or [] if item.get("stream_key") in allowed_streams]
        state["forecasts"] = [item for item in state.get("forecasts") or [] if item.get("stream_key") in allowed_streams]
    if not surface.get("show_streams"):
        state["streams"] = []
    if not surface.get("show_events"):
        state["events"] = []
    if not surface.get("show_forecasts"):
        state["forecasts"] = []
    event_limit = int(surface.get("event_limit") or limit or 40)
    forecast_limit = int(surface.get("forecast_limit") or 20)
    state["events"] = (state.get("events") or [])[:max(0, event_limit)]
    state["forecasts"] = (state.get("forecasts") or [])[:max(0, forecast_limit)]
    enabled_streams = [item for item in visible_streams if item.get("enabled")]
    events = state.get("events") or []
    forecasts = state.get("forecasts") or []
    state["summary"] = {
        "streams_enabled": len(enabled_streams),
        "signals": len(events),
        "forecasts": len(forecasts),
        "breaking": len([event for event in events if event.get("priority") in {"breaking", "urgent"}]),
        "avg_confidence": int(sum(event.get("confidence_score", 0) for event in events) / max(1, len(events))),
    }
    state["surface"] = surface
    return state


def update_stream(user_id: int, stream_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    stream_key = _slug(stream_key)
    if stream_key not in STREAM_KEYS:
        return {"ok": False, "error": "invalid_stream", "message": "Unknown intelligence stream.", "http_status": 404}
    conn = connect()
    try:
        ensure_user_pack(user_id, conn)
        cur = conn.cursor()
        allowed = {"enabled", "push_enabled", "email_enabled", "sms_enabled", "breaking_push_only", "quiet_hours_enabled"}
        fields = []
        values = []
        for field in allowed:
            if field in payload:
                fields.append(f"{field}=?")
                values.append(1 if _bool(payload.get(field), False) else 0)
        if "frequency" in payload:
            frequency = _slug(payload.get("frequency"), 30)
            if frequency not in FREQUENCIES:
                return {"ok": False, "error": "invalid_frequency", "message": "Unsupported frequency.", "http_status": 400}
            fields.append("frequency=?")
            values.append(frequency)
        if "digest_mode" in payload:
            fields.append("digest_mode=?")
            values.append(_slug(payload.get("digest_mode"), 30))
        if "confidence_threshold" in payload:
            fields.append("confidence_threshold=?")
            values.append(max(0, min(_int(payload.get("confidence_threshold"), 70), 100)))
        if "priority_filter" in payload:
            priority = _slug(payload.get("priority_filter"), 20)
            if priority not in PRIORITIES:
                return {"ok": False, "error": "invalid_priority_filter", "message": "Unsupported priority filter.", "http_status": 400}
            fields.append("priority_filter=?")
            values.append(priority)
        if not fields:
            return {"ok": False, "error": "no_changes", "message": "No stream settings changed.", "http_status": 400}
        fields.append("updated_at=?")
        values.append(now_iso())
        values.extend([int(user_id), stream_key])
        cur.execute(f"UPDATE user_intelligence_streams SET {', '.join(fields)} WHERE user_id=? AND stream_key=?", tuple(values))
        conn.commit()
        return {"ok": True, "stream": _subscription_for_user(cur, int(user_id), stream_key)}
    finally:
        conn.close()


def record_feedback(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    event_id = _int(payload.get("event_id"))
    stream_key = _slug(payload.get("stream_key") or "")
    feedback_type = _slug(payload.get("feedback_type") or payload.get("type") or "", 40)
    if feedback_type not in {"opened", "dismissed", "saved", "shared", "liked", "muted", "helpful", "not_helpful", "wrong", "too_frequent", "outdated", "not_interested"}:
        return {"ok": False, "error": "invalid_feedback", "message": "Unsupported feedback type.", "http_status": 400}
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO intelligence_feedback (user_id, event_id, stream_key, feedback_type, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(user_id), event_id, stream_key, feedback_type, _json_dumps(payload.get("metadata") or {}), now_iso()),
        )
        conn.commit()
        return {"ok": True, "message": "Feedback saved for intelligence tuning."}
    finally:
        conn.close()


def _sample_internal_signal(stream_key: str = "pulsesoc_discoveries") -> dict[str, Any]:
    if stream_key == "security_pulse":
        return {
            "stream_key": "security_pulse",
            "event_type": "security_update",
            "headline": "Security Pulse is watching critical platform and device updates",
            "summary": "PulseSoc can surface high-confidence security updates from trusted sources without exposing private user data.",
            "why_it_matters": "Security intelligence helps users act before small risks become account problems.",
            "expected_impact": "Security Pulses may bypass digest only when confidence and severity are high.",
            "source_keys": ["cisa", "nist"],
            "importance_score": 78,
            "freshness_score": 82,
            "global_impact": 65,
            "duplicate_confidence": 54,
            "priority": "high",
        }
    return {
        "stream_key": stream_key if stream_key in STREAM_KEYS else "pulsesoc_discoveries",
        "event_type": "platform_discovery",
        "headline": "Pulse AI can explain your Intelligence Streams",
        "summary": "Ask Pulse AI to summarize today's market, explain an alert, mute World Pulse, or focus Crypto Pulse on Bitcoin.",
        "why_it_matters": "Users can control intelligence through natural language while keeping private conversations out of the learning loop.",
        "expected_impact": "More users can discover useful PulseSoc features without repeated onboarding popups.",
        "source_keys": ["pulsesoc_feature_registry"],
        "importance_score": 64,
        "freshness_score": 80,
        "global_impact": 35,
        "duplicate_confidence": 38,
        "priority": "normal",
    }


def run_internal_collector(stream_key: str = "pulsesoc_discoveries", *, target_user_id: int = 0, deliver: bool = False) -> dict[str, Any]:
    started = datetime.utcnow()
    run_id = 0
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO intelligence_collector_runs
            (collector_key, stream_key, status, started_at, metadata_json)
            VALUES (?, ?, 'running', ?, ?)
            """,
            ("internal_seed_collector", _slug(stream_key), now_iso(), _json_dumps({"deliver": deliver, "target_user_id": target_user_id})),
        )
        run_id = _int(getattr(cur, "lastrowid", 0))
        conn.commit()
    finally:
        conn.close()
    result = ingest_signal(_sample_internal_signal(stream_key), deliver=deliver, target_user_id=target_user_id)
    duration_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE intelligence_collector_runs
            SET status=?, finished_at=?, duration_ms=?, events_seen=?, events_accepted=?, failure_reason=?, metadata_json=?
            WHERE id=?
            """,
            (
                "success" if result.get("ok") else "failed",
                now_iso(),
                duration_ms,
                1,
                1 if result.get("ok") and result.get("status") == "accepted" else 0,
                "" if result.get("ok") else result.get("message") or result.get("error") or "failed",
                _json_dumps(result),
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": bool(result.get("ok")), "collector_run_id": run_id, "result": result, "duration_ms": duration_ms}


def send_test_alert(admin_user_id: int = 0, *, target_user_id: int = 0, stream_key: str = "pulsesoc_discoveries") -> dict[str, Any]:
    target = int(target_user_id or admin_user_id or 0)
    if not target:
        return {"ok": False, "error": "target_user_required", "message": "Choose a target user for the test alert.", "http_status": 400}
    stream = _slug(stream_key or "pulsesoc_discoveries")
    if stream not in STREAM_KEYS:
        return {"ok": False, "error": "invalid_stream", "message": "Unknown intelligence stream.", "http_status": 400}
    signal = _sample_internal_signal(stream)
    signal["event_key"] = f"admin-test:{target}:{stream}:{secrets.token_hex(6)}"
    signal["headline"] = "PulseSoc feature discovery test"
    signal["summary"] = "This admin-only test verifies the Intelligence alert queue, notification intake, delivery logs, and CTA rendering."
    signal["why_it_matters"] = "Admins can confirm delivery without sending noisy alerts to everyone."
    signal["expected_impact"] = "A single manageable Pulse Alert should appear for the selected test user."
    signal["priority"] = "high"
    signal["importance_score"] = 88
    signal["freshness_score"] = 92
    signal["metadata"] = {
        "admin_test": True,
        "deep_link": "/pulse/alerts",
        "actions": default_actions_for_signal("pulsesoc_discoveries", "platform_discovery", "/pulse/alerts"),
    }
    result = ingest_signal(signal, deliver=False, target_user_id=target)
    if not result.get("ok"):
        return result
    queued = queue_event_delivery(int(result.get("event_id") or 0), target_user_id=target, delivery_type="instant")
    processed = process_delivery_queue(limit=20)
    return {"ok": True, "event_id": result.get("event_id"), "queue": queued, "processing": processed}


def admin_send_event(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload or {})
    event_id = _int(payload.get("event_id"))
    if not event_id:
        return {"ok": False, "error": "event_required", "message": "Choose an event to send.", "http_status": 400}
    mode = _slug(payload.get("mode") or "subscribers", 30)
    if mode not in {"test_user", "selected_user", "subscribers", "all"}:
        return {"ok": False, "error": "invalid_mode", "message": "Unsupported delivery mode.", "http_status": 400}
    target_user_id = _int(payload.get("target_user_id")) if mode in {"test_user", "selected_user"} else 0
    limit = max(1, min(_int(payload.get("limit"), 500), 5000))
    delivery_type = _slug(payload.get("delivery_type") or "", 30)
    schedule_at = _compact(payload.get("schedule_at") or "", 80)
    queued = queue_event_delivery(event_id, target_user_id=target_user_id, limit=limit, delivery_type=delivery_type, schedule_at=schedule_at, all_users=mode == "all")
    processed = {}
    if not schedule_at and _bool(payload.get("process_now"), True):
        processed = process_delivery_queue(limit=min(limit, 500))
    return {"ok": bool(queued.get("ok")), "event_id": event_id, "mode": mode, "queue": queued, "processing": processed}


def cancel_delivery_job(job_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM intelligence_delivery_jobs WHERE id=? LIMIT 1", (int(job_id),))
        job = cur.fetchone()
        if not job:
            return {"ok": False, "error": "job_not_found", "message": "Delivery job not found.", "http_status": 404}
        if _row_get(job, "status") in {"sent", "skipped", "failed", "canceled"}:
            return {"ok": False, "error": "job_closed", "message": "This delivery job is already closed.", "http_status": 409}
        cur.execute(
            "UPDATE intelligence_delivery_jobs SET status='canceled', canceled_at=?, updated_at=? WHERE id=?",
            (now_iso(), now_iso(), int(job_id)),
        )
        conn.commit()
        return {"ok": True, "job_id": int(job_id), "status": "canceled"}
    finally:
        conn.close()


def delivery_diagnostics(limit: int = 50) -> dict[str, Any]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM intelligence_delivery_jobs ORDER BY id DESC LIMIT ?", (max(1, min(int(limit or 50), 200)),))
        jobs = []
        for row in cur.fetchall():
            jobs.append({
                "id": _int(_row_get(row, "id")),
                "event_id": _int(_row_get(row, "event_id")),
                "user_id": _int(_row_get(row, "user_id")),
                "stream_key": _row_get(row, "stream_key") or "",
                "delivery_type": _row_get(row, "delivery_type") or "",
                "status": _row_get(row, "status") or "",
                "channels": _json_loads(_row_get(row, "channels_json"), []),
                "attempts": _int(_row_get(row, "attempts")),
                "notification_id": _int(_row_get(row, "notification_id")),
                "failure_reason": _row_get(row, "failure_reason") or "",
                "scheduled_at": _row_get(row, "scheduled_at") or "",
                "sent_at": _row_get(row, "sent_at") or "",
                "created_at": _row_get(row, "created_at") or "",
            })
        cur.execute("SELECT * FROM intelligence_delivery_log ORDER BY id DESC LIMIT ?", (max(1, min(int(limit or 50), 200)),))
        logs = []
        for row in cur.fetchall():
            logs.append({
                "id": _int(_row_get(row, "id")),
                "event_id": _int(_row_get(row, "event_id")),
                "user_id": _int(_row_get(row, "user_id")),
                "stream_key": _row_get(row, "stream_key") or "",
                "notification_id": _int(_row_get(row, "notification_id")),
                "delivery_status": _row_get(row, "delivery_status") or "",
                "channels": _json_loads(_row_get(row, "channels_json"), []),
                "updated_at": _row_get(row, "updated_at") or "",
            })
        return {"ok": True, "jobs": jobs, "logs": logs}
    finally:
        conn.close()


def source_health() -> list[dict[str, Any]]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM intelligence_sources ORDER BY stream_key, source_key")
        rows = []
        for row in cur.fetchall():
            required_env = _json_loads(_row_get(row, "required_env_json"), [])
            rows.append({
                "source_key": _row_get(row, "source_key") or "",
                "display_name": _row_get(row, "display_name") or "",
                "stream_key": _row_get(row, "stream_key") or "",
                "provider_type": _row_get(row, "provider_type") or "",
                "trust_score": _int(_row_get(row, "trust_score"), 70),
                "status": _row_get(row, "status") or "",
                "cache_seconds": _int(_row_get(row, "cache_seconds"), 300),
                "required_env": required_env,
                "configured": not required_env or all(os.getenv(str(key), "").strip() for key in required_env),
                "last_success_at": _row_get(row, "last_success_at") or "",
                "last_failure_at": _row_get(row, "last_failure_at") or "",
                "failure_reason": _row_get(row, "failure_reason") or "",
            })
        return rows
    finally:
        conn.close()


def admin_dashboard(stream_key: str = "") -> dict[str, Any]:
    conn = connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        counts: dict[str, int] = {}
        for key, sql in {
            "streams": "SELECT COUNT(*) AS count FROM intelligence_streams",
            "sources": "SELECT COUNT(*) AS count FROM intelligence_sources",
            "accepted_events": "SELECT COUNT(*) AS count FROM intelligence_events WHERE status='accepted'",
            "suppressed_events": "SELECT COUNT(*) AS count FROM intelligence_events WHERE status!='accepted'",
            "forecasts": "SELECT COUNT(*) AS count FROM intelligence_forecasts WHERE status='active'",
            "deliveries": "SELECT COUNT(*) AS count FROM intelligence_delivery_log",
            "delivery_jobs_queued": "SELECT COUNT(*) AS count FROM intelligence_delivery_jobs WHERE status IN ('queued', 'retry')",
            "delivery_jobs_sent": "SELECT COUNT(*) AS count FROM intelligence_delivery_jobs WHERE status='sent'",
            "delivery_jobs_failed": "SELECT COUNT(*) AS count FROM intelligence_delivery_jobs WHERE status='failed'",
            "digest_jobs_pending": "SELECT COUNT(*) AS count FROM intelligence_digest_jobs WHERE status='pending'",
            "feedback": "SELECT COUNT(*) AS count FROM intelligence_feedback",
        }.items():
            cur.execute(sql)
            counts[key] = _int(_row_get(cur.fetchone(), "count", 0))
        selected_stream = _slug(stream_key, 80) if stream_key else ""
        if selected_stream not in STREAM_KEYS:
            selected_stream = ""
        if selected_stream:
            cur.execute("SELECT * FROM intelligence_events WHERE stream_key=? ORDER BY created_at DESC LIMIT 25", (selected_stream,))
        else:
            cur.execute("SELECT * FROM intelligence_events ORDER BY created_at DESC LIMIT 25")
        events = [format_event(row) for row in cur.fetchall()]
        if selected_stream:
            cur.execute("SELECT * FROM intelligence_forecasts WHERE stream_key=? ORDER BY created_at DESC LIMIT 12", (selected_stream,))
        else:
            cur.execute("SELECT * FROM intelligence_forecasts ORDER BY created_at DESC LIMIT 12")
        forecasts = [format_forecast(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM intelligence_collector_runs ORDER BY id DESC LIMIT 20")
        runs = [
            {
                "id": _int(_row_get(row, "id")),
                "collector_key": _row_get(row, "collector_key") or "",
                "stream_key": _row_get(row, "stream_key") or "",
                "status": _row_get(row, "status") or "",
                "duration_ms": _int(_row_get(row, "duration_ms")),
                "events_seen": _int(_row_get(row, "events_seen")),
                "events_accepted": _int(_row_get(row, "events_accepted")),
                "failure_reason": _row_get(row, "failure_reason") or "",
                "started_at": _row_get(row, "started_at") or "",
                "finished_at": _row_get(row, "finished_at") or "",
            }
            for row in cur.fetchall()
        ]
        cur.execute("SELECT * FROM intelligence_delivery_jobs ORDER BY id DESC LIMIT 25")
        delivery_jobs = [
            {
                "id": _int(_row_get(row, "id")),
                "event_id": _int(_row_get(row, "event_id")),
                "user_id": _int(_row_get(row, "user_id")),
                "stream_key": _row_get(row, "stream_key") or "",
                "delivery_type": _row_get(row, "delivery_type") or "",
                "status": _row_get(row, "status") or "",
                "channels": _json_loads(_row_get(row, "channels_json"), []),
                "attempts": _int(_row_get(row, "attempts")),
                "notification_id": _int(_row_get(row, "notification_id")),
                "failure_reason": _row_get(row, "failure_reason") or "",
                "scheduled_at": _row_get(row, "scheduled_at") or "",
                "created_at": _row_get(row, "created_at") or "",
            }
            for row in cur.fetchall()
        ]
        cur.execute("SELECT * FROM intelligence_delivery_log ORDER BY id DESC LIMIT 25")
        delivery_logs = [
            {
                "id": _int(_row_get(row, "id")),
                "event_id": _int(_row_get(row, "event_id")),
                "user_id": _int(_row_get(row, "user_id")),
                "stream_key": _row_get(row, "stream_key") or "",
                "notification_id": _int(_row_get(row, "notification_id")),
                "delivery_status": _row_get(row, "delivery_status") or "",
                "channels": _json_loads(_row_get(row, "channels_json"), []),
                "updated_at": _row_get(row, "updated_at") or "",
            }
            for row in cur.fetchall()
        ]
        sources = source_health()
        return {
            "ok": True,
            "center_name": ADMIN_CENTER_NAME,
            "internal_codename": INTERNAL_CODENAME,
            "selected_stream": selected_stream,
            "command_sections": [dict(item) for item in ADMIN_COMMAND_SECTIONS],
            "counts": counts,
            "source_health": sources,
            "events": events,
            "forecasts": forecasts,
            "collector_runs": runs,
            "delivery_jobs": delivery_jobs,
            "delivery_logs": delivery_logs,
            "queue": {
                "digest_jobs_pending": counts.get("digest_jobs_pending", 0),
                "delivery_log_entries": counts.get("deliveries", 0),
                "delivery_jobs_queued": counts.get("delivery_jobs_queued", 0),
                "delivery_jobs_failed": counts.get("delivery_jobs_failed", 0),
            },
            "privacy": {
                "private_conversation_learning": "opt_in_only",
                "private_messages_used_by_collectors": False,
                "secrets_exposed": False,
            },
        }
    finally:
        conn.close()


def health() -> dict[str, Any]:
    dashboard = admin_dashboard()
    sources = dashboard.get("source_health") or []
    configured = len([source for source in sources if source.get("configured")])
    missing = len(sources) - configured
    return {
        "ok": True,
        "status": "ready",
        "streams": dashboard.get("counts", {}).get("streams", 0),
        "sources_configured": configured,
        "sources_missing_config": missing,
        "events": dashboard.get("counts", {}).get("accepted_events", 0),
        "forecasts": dashboard.get("counts", {}).get("forecasts", 0),
        "privacy_safe": True,
        "user_request_fetching": "disabled",
        "collector_mode": "background_worker_or_admin_trigger",
    }
