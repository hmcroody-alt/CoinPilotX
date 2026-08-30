"""Briefing fact packs: owner-scoped network signals + grounded crypto facts.

Everything the summarizer may say comes from here. Facts are deterministic,
timestamped, and bounded. No LLM involvement at this layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from . import crypto_provider

# Significance weights (Stage 7). A briefing sends only when the total clears
# SEND_THRESHOLD or the market moved meaningfully.
WEIGHTS = {
    "security_alert": 50,
    "unread_message": 8,
    "friend_request": 5,
    "mention": 5,
    "comment": 3,
    "new_follower": 3,
    "marketplace_order": 10,
    "reaction": 1,
    "community_event": 2,
}
SEND_THRESHOLD = 10
MARKET_MOVE_THRESHOLD_PCT = 2.0  # |BTC 24h| or |cap 24h| beyond this is significant


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def collect_network_facts(cur, user_id: int, since_iso: str) -> dict[str, Any]:
    """Owner-scoped counts from canonical tables. Never another user's metrics."""
    facts = {
        "unread_messages": 0, "friend_requests": 0, "new_followers": 0,
        "mentions": 0, "comments": 0, "reactions": 0,
        "marketplace_orders": 0, "security_alerts": 0, "community_events": 0,
        "collected_at": _now_iso(), "since": since_iso,
    }
    try:
        cur.execute(
            """
            SELECT type, COUNT(*) AS n FROM pulse_notifications
            WHERE user_id=? AND COALESCE(is_read,0)=0 AND created_at>=?
            GROUP BY type
            """,
            (int(user_id), since_iso),
        )
        for row in cur.fetchall():
            r = dict(row) if not isinstance(row, dict) else row
            kind = str(r.get("type") or "")
            n = int(r.get("n") or 0)
            if kind in ("chat_message", "group_message", "room_message", "message"):
                facts["unread_messages"] += n
            elif kind in ("friend_request",):
                facts["friend_requests"] += n
            elif kind in ("follow", "new_follower"):
                facts["new_followers"] += n
            elif kind in ("mention",):
                facts["mentions"] += n
            elif kind in ("comment", "reply"):
                facts["comments"] += n
            elif kind in ("reaction",):
                facts["reactions"] += n
            elif kind in ("marketplace_order", "purchase", "teacher_order"):
                facts["marketplace_orders"] += n
            elif kind in ("security", "system_security", "admin_security", "account"):
                facts["security_alerts"] += n
            elif kind in ("community", "group", "live", "live_invite"):
                facts["community_events"] += n
    except Exception:  # noqa: BLE001 - a fact-pack fault degrades to zeros
        logging.exception("BRIEFING_NETWORK_FACTS_FAILED user_id=%s", user_id)
    return facts


def network_significance(network: dict[str, Any]) -> int:
    return (
        network.get("security_alerts", 0) * WEIGHTS["security_alert"]
        + network.get("unread_messages", 0) * WEIGHTS["unread_message"]
        + network.get("friend_requests", 0) * WEIGHTS["friend_request"]
        + network.get("mentions", 0) * WEIGHTS["mention"]
        + network.get("comments", 0) * WEIGHTS["comment"]
        + network.get("new_followers", 0) * WEIGHTS["new_follower"]
        + network.get("marketplace_orders", 0) * WEIGHTS["marketplace_order"]
        + network.get("reactions", 0) * WEIGHTS["reaction"]
        + network.get("community_events", 0) * WEIGHTS["community_event"]
    )


def collect_crypto_facts(cur, user_id: int, *, watchlist_enabled: bool) -> dict[str, Any]:
    """Grounded market facts from the shared snapshot + user's own alerts."""
    overview = crypto_provider.get_market_overview()
    facts: dict[str, Any] = {"available": False, "collected_at": _now_iso()}
    if not overview or crypto_provider.is_stale(overview):
        # Stage 32/63: never present stale data as current. Omit instead.
        facts["unavailable_reason"] = "stale_or_provider_down"
        return facts
    movers = crypto_provider.get_top_movers()
    btc, eth = overview.get("btc") or {}, overview.get("eth") or {}
    facts.update({
        "available": True,
        "provider": overview.get("provider"),
        "observed_at": overview.get("generated_at"),
        "btc_price": btc.get("price"), "btc_change_24h": btc.get("change_24h"),
        "eth_price": eth.get("price"), "eth_change_24h": eth.get("change_24h"),
        "total_market_cap": overview.get("total_market_cap"),
        "market_cap_change_24h_pct": overview.get("market_cap_change_24h_pct"),
        "btc_dominance": overview.get("btc_dominance"),
        "market_direction": overview.get("market_direction"),
        "breadth_positive_top10": overview.get("breadth_positive_top10"),
        # Paid-depth market snapshot fields. Only facts the provider actually
        # returned — no derived guesses (Coinbase fallback leaves these None/0).
        "total_volume_24h": overview.get("total_volume_24h"),
        "volatility_avg_abs_24h": overview.get("volatility_avg_abs_24h"),
        "gainers": [{"symbol": a["symbol"], "change_24h": a["change_24h"]} for a in movers["gainers"]],
        "losers": [{"symbol": a["symbol"], "change_24h": a["change_24h"]} for a in movers["losers"]],
        "watchlist": [], "alert_proximity": [],
    })
    try:
        facts["trending"] = [
            {"symbol": t["symbol"], "rank": t.get("rank")}
            for t in crypto_provider.get_trending() if t.get("symbol")
        ][:5]
    except Exception:  # noqa: BLE001 - trending is optional color, never fatal
        facts["trending"] = []
    if watchlist_enabled:
        try:
            # Column names are asset_symbol/condition_type/target_value -- the older
            # symbol/type/threshold spelling raises UndefinedColumn on Postgres, and
            # because the whole block is one try/except that silently emptied BOTH
            # watchlist and alert_proximity for every user on every cycle.
            cur.execute(
                "SELECT asset_symbol, condition_type, target_value FROM crypto_alerts "
                "WHERE user_id=? AND COALESCE(status,'active')='active' LIMIT 25",
                (int(user_id),),
            )
            rows = [dict(r) if not isinstance(r, dict) else r for r in cur.fetchall()]
            symbols = sorted({str(r.get("asset_symbol") or "").upper() for r in rows if r.get("asset_symbol")})
            snapshots = {a["symbol"]: a for a in crypto_provider.get_watchlist_snapshots(symbols)}
            facts["watchlist"] = [
                {"symbol": s, "price": snapshots[s]["price"], "change_24h": snapshots[s]["change_24h"]}
                for s in symbols if s in snapshots
            ]
            for r in rows:  # Stage 41: proximity only — never trigger early
                symbol = str(r.get("asset_symbol") or "").upper()
                snap = snapshots.get(symbol)
                try:
                    threshold = float(r.get("target_value") or 0)
                except (TypeError, ValueError):
                    continue
                # condition_type holds the stored condition vocabulary
                # (above/below/moves_up_percent/...), NOT the derived alert-type
                # strings the old code compared against. Percent-move conditions
                # carry a percentage in target_value, so a price-distance
                # calculation would be meaningless for them.
                if not snap or threshold <= 0 or str(r.get("condition_type") or "") not in ("above", "below"):
                    continue
                price = float(snap.get("price") or 0)
                if price <= 0:
                    continue
                distance_pct = (threshold - price) / price * 100
                if abs(distance_pct) <= 5.0:
                    facts["alert_proximity"].append({
                        "symbol": symbol, "threshold": threshold,
                        "distance_pct": round(distance_pct, 1),
                    })
        except Exception:  # noqa: BLE001
            logging.exception("BRIEFING_WATCHLIST_FACTS_FAILED user_id=%s", user_id)
    return facts


def crypto_significance(crypto: dict[str, Any]) -> int:
    if not crypto.get("available"):
        return 0
    score = 0
    btc = abs(crypto.get("btc_change_24h") or 0)
    cap = abs(crypto.get("market_cap_change_24h_pct") or 0)
    if btc >= MARKET_MOVE_THRESHOLD_PCT:
        score += 10
    if cap >= MARKET_MOVE_THRESHOLD_PCT:
        score += 6
    score += 8 * len(crypto.get("alert_proximity") or [])
    score += sum(4 for w in crypto.get("watchlist") or [] if abs(w.get("change_24h") or 0) >= 4.0)
    return score


def build_briefing_facts(cur, user_id: int, *, since_iso: str, timezone_name: str,
                         locale: str, prefs: dict[str, Any]) -> dict[str, Any]:
    """Stage 10 contract: the ONLY payload the summarizer ever sees."""
    network = collect_network_facts(cur, user_id, since_iso) if prefs.get("network_enabled", True) else None
    crypto = collect_crypto_facts(cur, user_id, watchlist_enabled=bool(prefs.get("watchlist_enabled", True))) \
        if prefs.get("crypto_enabled", True) else None
    net_score = network_significance(network) if network else 0
    cry_score = crypto_significance(crypto) if crypto else 0
    urgency = "high" if (network or {}).get("security_alerts") else "normal"
    return {
        "user_id": int(user_id),
        "generated_at": _now_iso(),
        "timezone": timezone_name,
        "locale": locale,
        "network": network,
        "crypto": crypto,
        "urgency": urgency,
        "significance_score": net_score + cry_score,
        "network_score": net_score,
        "crypto_score": cry_score,
    }


def fact_fingerprint(facts: dict[str, Any]) -> str:
    """Stage 17: compact market-state + network signature for dedupe.

    Prices are bucketed (1% resolution) so a flat market + unchanged network
    hashes identically across windows.
    """
    network = facts.get("network") or {}
    crypto = facts.get("crypto") or {}

    def bucket(value):
        try:
            return round(float(value or 0) / 1.0)
        except (TypeError, ValueError):
            return 0

    signature = {
        "net_counts": [network.get(k, 0) for k in (
            "unread_messages", "friend_requests", "new_followers", "mentions",
            "comments", "marketplace_orders", "security_alerts")] if network else [],
        "btc": bucket(crypto.get("btc_change_24h")),
        "eth": bucket(crypto.get("eth_change_24h")),
        "cap": bucket(crypto.get("market_cap_change_24h_pct")),
        "direction": crypto.get("market_direction"),
        "proximity": sorted(p["symbol"] for p in crypto.get("alert_proximity") or []),
    }
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:32]
