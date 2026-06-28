"""Owner-scoped PulseSoc Crypto Command Center state and actions.

This module exposes crypto dashboard data without inventing prices or holdings.
Provider-backed features surface truthful PARTIAL states when live integrations
are unavailable, while alerts and watchlists remain locally functional.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
import hashlib
import json
import os
import re

from services import market_data as market_data_service


STRICT_STATES = {
    "READY",
    "ACTION",
    "REVIEW",
    "WARNING",
    "LOCKED",
    "PREMIUM",
    "BETA",
    "PARTIAL",
    "COMING_SOON",
    "ADMIN",
}

ALERT_CONDITIONS = {"above", "below", "percent_change", "volume_spike", "market_cap_change"}
CRYPTO_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")

MODULES: tuple[dict[str, Any], ...] = (
    {"key": "market_pulse", "widget_key": "crypto_market_pulse", "label": "Market Pulse", "route": "/dashboard/crypto/market-pulse", "action": "View Market Pulse", "description": "BTC, ETH, SOL, market health, sentiment, and provider freshness."},
    {"key": "create_alert", "widget_key": "crypto_create_alert", "label": "Create Alert", "route": "/dashboard/crypto/alerts/create", "action": "Create Alert", "description": "Create owner-scoped price, percentage, volume, and market cap alerts."},
    {"key": "my_alerts", "widget_key": "crypto_my_alerts", "label": "My Alerts", "route": "/dashboard/crypto/alerts", "action": "Manage Alerts", "description": "Pause, resume, edit, delete, duplicate, and inspect your crypto alerts."},
    {"key": "watchlists", "widget_key": "crypto_watchlists", "label": "Watchlists", "route": "/dashboard/crypto/watchlists", "action": "View Watchlists", "description": "Track assets, notes, position, and quick alert context."},
    {"key": "ask_ai", "widget_key": "crypto_ask_ai", "label": "Ask Crypto AI", "route": "/dashboard/crypto/ask-ai", "action": "Ask Crypto AI", "description": "Educational crypto questions with safety boundaries and no guaranteed advice.", "state": "PARTIAL"},
    {"key": "portfolio", "widget_key": "crypto_portfolio", "label": "Portfolio Intelligence", "route": "/dashboard/crypto/portfolio", "action": "Review Portfolio", "description": "Owner-entered portfolio visibility and risk guidance when connected.", "state": "PARTIAL"},
    {"key": "wallet", "widget_key": "crypto_wallet", "label": "Wallet", "route": "/dashboard/crypto/wallet", "action": "Manage Wallet", "description": "Wallet readiness and safety without exposing keys, seed phrases, or secrets.", "state": "PARTIAL"},
    {"key": "market_scanner", "widget_key": "crypto_market_scanner", "label": "Market Scanner", "route": "/dashboard/crypto/market-scanner", "action": "Scan Market", "description": "Volume, volatility, trend, and watchlist market signals."},
    {"key": "whale_alerts", "widget_key": "crypto_whale_alerts", "label": "Whale Alerts", "route": "/dashboard/crypto/whale-alerts", "action": "Track Whales", "description": "Prepared whale intelligence surface; live provider required.", "state": "PARTIAL"},
    {"key": "trending_coins", "widget_key": "crypto_trending_coins", "label": "Trending Coins", "route": "/dashboard/crypto/trending", "action": "View Trending", "description": "Trending assets from available market data and PulseSoc signals."},
    {"key": "top_gainers", "widget_key": "crypto_top_gainers", "label": "Top Gainers", "route": "/dashboard/crypto/gainers", "action": "View Gainers", "description": "Assets with strongest available 24h movement."},
    {"key": "top_losers", "widget_key": "crypto_top_losers", "label": "Top Losers", "route": "/dashboard/crypto/losers", "action": "View Losers", "description": "Assets with weakest available 24h movement."},
    {"key": "token_scanner", "widget_key": "crypto_token_scanner", "label": "Token Scanner", "route": "/dashboard/crypto/token-scanner", "action": "Scan Token", "description": "Symbol and contract risk triage with transparent provider limits.", "state": "BETA"},
    {"key": "crypto_news", "widget_key": "crypto_news", "label": "Crypto News", "route": "/dashboard/crypto/news", "action": "Read Crypto News", "description": "Crypto news and AI summaries when provider data is available.", "state": "PARTIAL"},
    {"key": "economic_calendar", "widget_key": "crypto_economic_calendar", "label": "Economic Calendar", "route": "/dashboard/crypto/calendar", "action": "View Calendar", "description": "Macro and crypto events prepared for reminders and provider expansion.", "state": "PARTIAL"},
    {"key": "ai_market_analysis", "widget_key": "crypto_ai_market_analysis", "label": "AI Market Analysis", "route": "/dashboard/crypto/ai-analysis", "action": "Analyze Market", "description": "Educational market analysis with confidence and disclaimers.", "state": "PARTIAL"},
    {"key": "favorite_coins", "widget_key": "crypto_favorite_coins", "label": "Favorite Coins", "route": "/dashboard/crypto/favorites", "action": "View Favorites", "description": "Favorite assets, notes, alerts, and quick actions."},
    {"key": "recently_viewed", "widget_key": "crypto_recent_assets", "label": "Recently Viewed Assets", "route": "/dashboard/crypto/recent", "action": "Continue Watching", "description": "Recent owner-scoped crypto asset history."},
)

MODULE_BY_KEY = {item["key"]: item for item in MODULES}
MODULE_BY_WIDGET = {item["widget_key"]: item for item in MODULES}
MODULE_BY_ROUTE = {
    "market-pulse": "market_pulse",
    "alerts/create": "create_alert",
    "alerts": "my_alerts",
    "watchlists": "watchlists",
    "ask-ai": "ask_ai",
    "portfolio": "portfolio",
    "wallet": "wallet",
    "market-scanner": "market_scanner",
    "whale-alerts": "whale_alerts",
    "trending": "trending_coins",
    "gainers": "top_gainers",
    "losers": "top_losers",
    "token-scanner": "token_scanner",
    "news": "crypto_news",
    "calendar": "economic_calendar",
    "ai-analysis": "ai_market_analysis",
    "favorites": "favorite_coins",
    "recent": "recently_viewed",
}

ADMIN_SECTIONS: tuple[dict[str, str], ...] = (
    {"key": "alerts", "label": "Crypto Alerts Manager", "route": "/admin/command-center/crypto/alerts", "description": "Owner-scoped alert diagnostics, trigger state, and safe notification status."},
    {"key": "watchlists", "label": "Watchlist Manager", "route": "/admin/command-center/crypto/watchlists", "description": "Watchlist counts and asset diagnostics without leaking private notes."},
    {"key": "market-data", "label": "Market Data Health", "route": "/admin/command-center/crypto/market-data", "description": "Provider source, fallback state, freshness, and unavailable states."},
    {"key": "ai-usage", "label": "Crypto AI Usage", "route": "/admin/command-center/crypto/ai-usage", "description": "Prompt audit metadata and educational usage counts without raw private prompts."},
    {"key": "token-scanner", "label": "Token Scanner Logs", "route": "/admin/command-center/crypto/token-scanner", "description": "Scanner attempts, transparent risk limits, and safe error counts."},
    {"key": "whale-provider", "label": "Whale Signal Provider", "route": "/admin/command-center/crypto/whale-provider", "description": "Whale provider readiness; no provider secrets exposed."},
    {"key": "news-provider", "label": "Crypto News Provider", "route": "/admin/command-center/crypto/news-provider", "description": "News provider readiness and unavailable state."},
    {"key": "portfolio", "label": "Portfolio Intelligence", "route": "/admin/command-center/crypto/portfolio", "description": "Portfolio integration readiness with user-owned data boundaries."},
    {"key": "wallet-safety", "label": "Wallet Safety", "route": "/admin/command-center/crypto/wallet-safety", "description": "Wallet safety readiness without keys, seed phrases, or secrets."},
    {"key": "audit", "label": "Crypto Audit Logs", "route": "/admin/command-center/crypto/audit", "description": "Crypto alert, watchlist, AI, scanner, favorite, and recent-asset audit events."},
)


def _now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _row_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _rows(cur: Any) -> list[dict[str, Any]]:
    return [_row_dict(row) for row in cur.fetchall()]


def _normalize_symbol(symbol: Any) -> str:
    symbol = str(symbol or "").strip().upper().replace("$", "")
    symbol = re.sub(r"[^A-Z0-9]", "", symbol)[:12]
    if not CRYPTO_SYMBOL_RE.match(symbol):
        raise ValueError("Use a valid asset symbol such as BTC, ETH, or SOL.")
    return symbol


def _validate_url_path(path: Any) -> str:
    value = str(path or "").strip()
    if value.startswith("/dashboard/crypto") or value.startswith("/pulse") or value.startswith("/dashboard/economy"):
        return value[:240]
    return "/dashboard/crypto"


def _table_exists(cur: Any, table: str) -> bool:
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        try:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?", (table,))
            return bool(cur.fetchone())
        except Exception:
            return False


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int((_row_dict(cur.fetchone()) or {}).get("total"), 0)
    except Exception:
        return 0


def ensure_tables(conn: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset_symbol TEXT NOT NULL,
            condition_type TEXT NOT NULL,
            target_value REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            notify_push INTEGER NOT NULL DEFAULT 1,
            notify_email INTEGER NOT NULL DEFAULT 0,
            notify_sms INTEGER NOT NULL DEFAULT 0,
            notify_in_app INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_triggered_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_alerts_user ON crypto_alerts(user_id, status, asset_symbol)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_watchlists_user ON crypto_watchlists(user_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_watchlist_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            asset_symbol TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_watchlist_assets_owner ON crypto_watchlist_assets(user_id, watchlist_id, asset_symbol)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_ai_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            prompt_hash TEXT NOT NULL,
            asset_symbol TEXT,
            response_summary TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_ai_queries_user ON crypto_ai_queries(user_id, created_at)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_recent_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset_symbol TEXT NOT NULL,
            last_viewed_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_recent_assets_user ON crypto_recent_assets(user_id, last_viewed_at)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_favorite_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset_symbol TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_favorite_assets_user ON crypto_favorite_assets(user_id, asset_symbol)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_audit_logs_user ON crypto_audit_logs(user_id, created_at)")
    conn.commit()


def _audit(conn: Any, user_id: int, action: str, target_type: str, target_id: Any = "", metadata: dict[str, Any] | None = None, actor_user_id: int | None = None) -> None:
    safe_metadata = {}
    for key, value in (metadata or {}).items():
        if key.lower() in {"token", "secret", "private_key", "seed", "password"}:
            continue
        safe_metadata[str(key)[:50]] = str(value)[:300]
    conn.execute(
        """
        INSERT INTO crypto_audit_logs (user_id, actor_user_id, action, target_type, target_id, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (int(user_id or 0), int(actor_user_id or user_id or 0), str(action)[:80], str(target_type)[:80], str(target_id or "")[:120], json.dumps(safe_metadata), _now()),
    )


def market_board(category: str = "top_volume", limit: int = 50) -> dict[str, Any]:
    if os.getenv("PULSESOC_CRYPTO_DISABLE_LIVE_MARKETS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return {
            "source": "unavailable",
            "updated_at": _now(),
            "warning": "Market data temporarily unavailable.",
            "markets": [],
            "summary": {"market_trend": "neutral", "risk_level": "Medium", "fallback": True},
        }
    try:
        return market_data_service.live_market_board(category=category, limit=max(1, min(int(limit or 50), 80)))
    except Exception as exc:
        return {
            "source": "unavailable",
            "updated_at": _now(),
            "warning": f"Market data temporarily unavailable. {type(exc).__name__}",
            "markets": [],
            "summary": {"market_trend": "neutral", "risk_level": "Medium", "fallback": True},
        }


def market_pulse() -> dict[str, Any]:
    board = market_board(limit=80)
    markets = board.get("markets") or []
    summary = board.get("summary") or {}
    by_symbol = {str(item.get("symbol") or "").upper(): item for item in markets}
    loaded = [item for item in markets if item.get("change_24h") is not None]
    avg = _safe_float(summary.get("average_change_24h"), None)
    if avg is None and loaded:
        avg = sum(float(item.get("change_24h") or 0) for item in loaded) / len(loaded)
    market_health = 50
    if avg is not None:
        market_health = max(0, min(100, int(55 + avg * 5)))
    provider_ready = bool(markets)
    risk = "Low" if market_health >= 60 else "Medium" if market_health >= 40 else "High"
    sentiment = str(summary.get("market_trend") or "neutral").lower()
    if sentiment not in {"bullish", "neutral", "bearish"}:
        sentiment = "neutral"
    return {
        "provider_ready": provider_ready,
        "source": board.get("source") or "unavailable",
        "warning": board.get("warning") or ("" if provider_ready else "Market data temporarily unavailable. Your alerts and watchlists remain available."),
        "btcPrice": (by_symbol.get("BTC") or {}).get("price"),
        "ethPrice": (by_symbol.get("ETH") or {}).get("price"),
        "solPrice": (by_symbol.get("SOL") or {}).get("price"),
        "btcChange24h": (by_symbol.get("BTC") or {}).get("change_24h"),
        "ethChange24h": (by_symbol.get("ETH") or {}).get("change_24h"),
        "solChange24h": (by_symbol.get("SOL") or {}).get("change_24h"),
        "marketSentiment": sentiment,
        "marketHealthScore": market_health,
        "fearGreedIndex": None,
        "btcDominance": None,
        "altSeasonSignal": "Provider pending" if not provider_ready else "Monitor rotation",
        "aiConfidence": 42 if not provider_ready else 72,
        "riskLevel": risk,
        "lastUpdated": board.get("updated_at") or _now(),
        "markets": markets[:50],
    }


def _format_money(value: Any) -> str:
    number = _safe_float(value, None)
    if number is None:
        return "Unavailable"
    return "${:,.2f}".format(number)


def build_crypto_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = _safe_int(user.get("user_id"), 0)
    pulse = market_pulse()
    provider_ready = bool(pulse.get("provider_ready"))
    active_alerts = _count(cur, "crypto_alerts", "user_id=? AND status='active'", (user_id,))
    total_alerts = _count(cur, "crypto_alerts", "user_id=?", (user_id,))
    watchlists = _count(cur, "crypto_watchlists", "user_id=?", (user_id,))
    watchlist_assets = _count(cur, "crypto_watchlist_assets", "user_id=?", (user_id,))
    favorite_count = _count(cur, "crypto_favorite_assets", "user_id=?", (user_id,))
    recent_count = _count(cur, "crypto_recent_assets", "user_id=?", (user_id,))
    module_states = {}
    for module in MODULES:
        state = module.get("state") or "READY"
        if module["key"] in {"market_pulse", "market_scanner", "trending_coins", "top_gainers", "top_losers"} and not provider_ready:
            state = "PARTIAL"
        if module["key"] == "create_alert":
            count = active_alerts
            detail = "Create price, percent, volume, and market-cap alerts."
        elif module["key"] == "my_alerts":
            count = total_alerts
            detail = f"{active_alerts} active alerts."
        elif module["key"] == "watchlists":
            count = watchlist_assets
            detail = f"{watchlists} watchlists tracking {watchlist_assets} assets."
        elif module["key"] == "favorite_coins":
            count = favorite_count
            detail = "Favorite coins are owner-scoped."
        elif module["key"] == "recently_viewed":
            count = recent_count
            detail = "Recently viewed assets stay private to your account."
        elif module["key"] == "market_pulse":
            count = pulse.get("marketHealthScore") or 0
            detail = pulse.get("warning") or f"Market sentiment is {pulse.get('marketSentiment')}."
        else:
            count = 0
            detail = module.get("description")
        module_states[module["key"]] = {
            **module,
            "state": state,
            "count": count,
            "count_display": str(count),
            "detail": detail,
            "confidence": pulse.get("aiConfidence") if module["key"] in {"market_pulse", "market_scanner", "ai_market_analysis"} else 64,
            "cta_label": module.get("action"),
        }
    return {
        "ok": True,
        "generated_at": _now(),
        "market_status": pulse,
        "hub": {
            "market_health": pulse.get("marketSentiment") or "neutral",
            "risk_level": pulse.get("riskLevel") or "Medium",
            "active_alerts": active_alerts,
            "watchlist_assets": watchlist_assets,
            "watchlists": watchlists,
            "ai_market_summary": pulse.get("warning") or "Market data is connected. Use alerts and watchlists to track movement.",
            "provider_ready": provider_ready,
            "btc": _format_money(pulse.get("btcPrice")),
            "eth": _format_money(pulse.get("ethPrice")),
            "sol": _format_money(pulse.get("solPrice")),
        },
        "modules": module_states,
        "cards": list(module_states.values()),
    }


def state_for_widget(state: dict[str, Any], widget_key: str) -> dict[str, Any]:
    module = MODULE_BY_WIDGET.get(widget_key)
    if not module:
        return {"state": "PARTIAL", "cta_label": "Review Crypto", "route": "/dashboard/crypto", "detail": "Crypto module is not registered."}
    current = (state.get("modules") or {}).get(module["key"]) or module
    return {
        "state": current.get("state") or "READY",
        "cta_label": current.get("action") or module.get("action") or "Review Crypto",
        "route": _validate_url_path(current.get("route") or module.get("route")),
        "detail": current.get("detail") or module.get("description") or "",
        "count": current.get("count") or 0,
        "confidence": current.get("confidence") or 0,
    }


def list_alerts(conn: Any, user_id: int) -> list[dict[str, Any]]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT * FROM crypto_alerts WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT 100", (int(user_id),))
    return _rows(cur)


def create_alert(conn: Any, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    symbol = _normalize_symbol(payload.get("assetSymbol") or payload.get("asset_symbol"))
    condition = str(payload.get("condition") or payload.get("condition_type") or "").strip().lower()
    if condition not in ALERT_CONDITIONS:
        raise ValueError("Choose a supported alert condition.")
    try:
        target = Decimal(str(payload.get("targetValue") or payload.get("target_value") or ""))
    except (InvalidOperation, ValueError):
        raise ValueError("Use a valid target value.")
    if target <= 0:
        raise ValueError("Target value must be greater than zero.")
    cur = conn.cursor()
    active_count = _count(cur, "crypto_alerts", "user_id=? AND status='active'", (int(user_id),))
    if active_count >= 100:
        raise ValueError("Alert limit reached. Pause or delete older alerts first.")
    now = _now()
    cur.execute(
        """
        INSERT INTO crypto_alerts
        (user_id, asset_symbol, condition_type, target_value, status, notify_push, notify_email, notify_sms, notify_in_app, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            symbol,
            condition,
            float(target),
            1 if payload.get("notifyPush", True) else 0,
            1 if payload.get("notifyEmail", False) else 0,
            1 if payload.get("notifySMS", False) else 0,
            1 if payload.get("notifyInApp", True) else 0,
            str(payload.get("note") or "")[:240],
            now,
            now,
        ),
    )
    alert_id = int(cur.lastrowid)
    _audit(conn, user_id, "create_alert", "crypto_alert", alert_id, {"asset": symbol, "condition": condition})
    conn.commit()
    return {"ok": True, "alert_id": alert_id, "message": f"{symbol} alert created."}


def update_alert(conn: Any, user_id: int, alert_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"active", "paused", "expired", "triggered"}:
        raise ValueError("Unsupported alert status.")
    cur = conn.cursor()
    cur.execute("UPDATE crypto_alerts SET status=?, updated_at=? WHERE id=? AND user_id=?", (status, _now(), int(alert_id), int(user_id)))
    if cur.rowcount == 0:
        raise ValueError("Alert not found.")
    _audit(conn, user_id, "update_alert", "crypto_alert", alert_id, {"status": status})
    conn.commit()
    return {"ok": True, "message": "Alert updated."}


def delete_alert(conn: Any, user_id: int, alert_id: int) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("DELETE FROM crypto_alerts WHERE id=? AND user_id=?", (int(alert_id), int(user_id)))
    if cur.rowcount == 0:
        raise ValueError("Alert not found.")
    _audit(conn, user_id, "delete_alert", "crypto_alert", alert_id, {})
    conn.commit()
    return {"ok": True, "message": "Alert deleted."}


def list_watchlists(conn: Any, user_id: int) -> list[dict[str, Any]]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT * FROM crypto_watchlists WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT 50", (int(user_id),))
    watchlists = _rows(cur)
    for watchlist in watchlists:
        cur.execute("SELECT * FROM crypto_watchlist_assets WHERE watchlist_id=? AND user_id=? ORDER BY position ASC, id ASC", (int(watchlist["id"]), int(user_id)))
        watchlist["assets"] = _rows(cur)
    return watchlists


def create_watchlist(conn: Any, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    name = re.sub(r"\s+", " ", str(payload.get("name") or "Crypto Watchlist").strip())[:80] or "Crypto Watchlist"
    now = _now()
    cur = conn.cursor()
    cur.execute("INSERT INTO crypto_watchlists (user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (int(user_id), name, now, now))
    watchlist_id = int(cur.lastrowid)
    _audit(conn, user_id, "create_watchlist", "crypto_watchlist", watchlist_id, {"name": name})
    conn.commit()
    return {"ok": True, "watchlist_id": watchlist_id, "message": "Watchlist created."}


def add_watchlist_asset(conn: Any, user_id: int, watchlist_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    symbol = _normalize_symbol(payload.get("assetSymbol") or payload.get("asset_symbol"))
    cur = conn.cursor()
    cur.execute("SELECT id FROM crypto_watchlists WHERE id=? AND user_id=? LIMIT 1", (int(watchlist_id), int(user_id)))
    if not cur.fetchone():
        raise ValueError("Watchlist not found.")
    position = _count(cur, "crypto_watchlist_assets", "watchlist_id=? AND user_id=?", (int(watchlist_id), int(user_id))) + 1
    cur.execute(
        "INSERT INTO crypto_watchlist_assets (watchlist_id, user_id, asset_symbol, position, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (int(watchlist_id), int(user_id), symbol, position, str(payload.get("notes") or "")[:240], _now()),
    )
    asset_id = int(cur.lastrowid)
    cur.execute("UPDATE crypto_watchlists SET updated_at=? WHERE id=? AND user_id=?", (_now(), int(watchlist_id), int(user_id)))
    _audit(conn, user_id, "add_watchlist_asset", "crypto_watchlist_asset", asset_id, {"asset": symbol})
    conn.commit()
    return {"ok": True, "asset_id": asset_id, "message": f"{symbol} added."}


def delete_watchlist_asset(conn: Any, user_id: int, watchlist_id: int, asset_id: int) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("DELETE FROM crypto_watchlist_assets WHERE id=? AND watchlist_id=? AND user_id=?", (int(asset_id), int(watchlist_id), int(user_id)))
    if cur.rowcount == 0:
        raise ValueError("Watchlist asset not found.")
    cur.execute("UPDATE crypto_watchlists SET updated_at=? WHERE id=? AND user_id=?", (_now(), int(watchlist_id), int(user_id)))
    _audit(conn, user_id, "delete_watchlist_asset", "crypto_watchlist_asset", asset_id, {})
    conn.commit()
    return {"ok": True, "message": "Asset removed."}


def list_recent_assets(conn: Any, user_id: int) -> list[dict[str, Any]]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT * FROM crypto_recent_assets WHERE user_id=? ORDER BY last_viewed_at DESC LIMIT 40", (int(user_id),))
    return _rows(cur)


def list_favorite_assets(conn: Any, user_id: int) -> list[dict[str, Any]]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT * FROM crypto_favorite_assets WHERE user_id=? ORDER BY created_at DESC LIMIT 40", (int(user_id),))
    return _rows(cur)


def record_recent_asset(conn: Any, user_id: int, symbol: str) -> None:
    ensure_tables(conn)
    symbol = _normalize_symbol(symbol)
    cur = conn.cursor()
    cur.execute("DELETE FROM crypto_recent_assets WHERE user_id=? AND asset_symbol=?", (int(user_id), symbol))
    cur.execute("INSERT INTO crypto_recent_assets (user_id, asset_symbol, last_viewed_at) VALUES (?, ?, ?)", (int(user_id), symbol, _now()))
    _audit(conn, user_id, "record_recent_asset", "crypto_asset", symbol, {"asset": symbol})
    conn.commit()


def ask_crypto_ai(conn: Any, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    prompt = re.sub(r"\s+", " ", str(payload.get("prompt") or "").strip())[:1000]
    if len(prompt) < 4:
        raise ValueError("Ask a clear crypto question.")
    symbol = ""
    if payload.get("assetSymbol"):
        symbol = _normalize_symbol(payload.get("assetSymbol"))
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:32]
    enabled = os.getenv("PULSE_AI_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if enabled:
        response = "Crypto AI is enabled. This educational answer should use configured AI routing before production release."
        state = "BETA"
    else:
        response = "Crypto AI is not enabled yet. Use alerts, watchlists, and market pulse while AI analysis remains in safe preview. This is educational only and not financial advice."
        state = "PARTIAL"
    conn.execute(
        "INSERT INTO crypto_ai_queries (user_id, prompt_hash, asset_symbol, response_summary, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(user_id), digest, symbol or None, response[:300], _now()),
    )
    _audit(conn, user_id, "ask_crypto_ai", "crypto_ai_query", digest, {"asset": symbol or "general", "state": state})
    conn.commit()
    return {
        "ok": True,
        "state": state,
        "answer": response,
        "disclaimer": "Educational information only. This is not financial advice. PulseSoc does not guarantee returns or tell users to buy or sell assets.",
    }


def scan_token(conn: Any, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    symbol = _normalize_symbol(payload.get("assetSymbol") or payload.get("symbol") or "TOKEN")
    contract = str(payload.get("contract") or payload.get("contractAddress") or "").strip()[:120]
    risk = 65 if contract and not re.match(r"^0x[a-fA-F0-9]{40}$", contract) else 38
    board = market_board(limit=80)
    known = next((item for item in board.get("markets") or [] if str(item.get("symbol") or "").upper() == symbol), None)
    if not known and not contract:
        risk = max(risk, 55)
    _audit(conn, user_id, "scan_token", "crypto_asset", symbol, {"has_contract": bool(contract), "known_market": bool(known)})
    conn.commit()
    return {
        "ok": True,
        "symbol": symbol,
        "risk_score": min(100, max(0, risk)),
        "state": "BETA",
        "known_market": bool(known),
        "warnings": [
            "Live contract intelligence is limited." if not contract else "Contract format reviewed only.",
            "Verify liquidity, holders, contract ownership, and official sources before acting.",
        ],
        "disclaimer": "Safety scan is educational and incomplete until full token-risk providers are connected.",
    }


def admin_overview(conn: Any) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    pulse = market_pulse()
    return {
        "ok": True,
        "generated_at": _now(),
        "market_source": pulse.get("source"),
        "market_provider_ready": bool(pulse.get("provider_ready")),
        "sections": list(ADMIN_SECTIONS),
        "metrics": {
            "alerts": _count(cur, "crypto_alerts"),
            "active_alerts": _count(cur, "crypto_alerts", "status='active'"),
            "watchlists": _count(cur, "crypto_watchlists"),
            "watchlist_assets": _count(cur, "crypto_watchlist_assets"),
            "ai_queries": _count(cur, "crypto_ai_queries"),
            "audit_events": _count(cur, "crypto_audit_logs"),
        },
        "provider_notes": [
            "Market data uses configured CoinGecko/Coinbase public paths when available.",
            "Whale, news, portfolio, and wallet intelligence are reported as partial unless their providers are connected.",
            "No private keys, seed phrases, raw wallet secrets, or provider secrets are returned.",
        ],
    }
