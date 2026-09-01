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
    # Call *outcomes*, not call lifecycle. See _NETWORK_BUCKETS for why the
    # distinction matters and why the other six call types stay unscored.
    #
    # missed_call at 5 is deliberately below SEND_THRESHOLD: one missed call is
    # worth recording but is not on its own worth waking someone with a
    # briefing. Two are (2 x 5 = 10, exactly the threshold). That puts it level
    # with mention and friend_request -- the "someone wanted you specifically"
    # tier -- which is the honest place for it.
    #
    # declined_call at 1 is a tiebreaker, never a trigger. A decline is often
    # the user's own action replayed back at them, so it is the weakest signal
    # here, level with reaction. The sizing is evidence-led: production's only
    # observed decline burst (2026-07-18, six declines each for five different
    # users within a day -- almost certainly a system artifact rather than real
    # social activity) scores 6 and stays correctly silent. At weight 2 that
    # same artifact would have scored 12 and sent five people a briefing about
    # nothing. Declines still compose: six declines plus one missed call is 11,
    # which sends.
    "missed_call": 5,
    "declined_call": 1,
}
SEND_THRESHOLD = 10
MARKET_MOVE_THRESHOLD_PCT = 2.0  # |BTC 24h| or |cap 24h| beyond this is significant


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# pulse_notifications.type -> significance bucket.
#
# The original chain compared `type` against a list that silently mixed two
# different vocabularies: real type strings ("message", "comment", "follow")
# alongside *category* names ("security", "system_security", "admin_security",
# "account", "community", "reaction"). Categories are never written to
# pulse_notifications.type, so those arms could not match anything -- production
# writes type='security_alert', whose category is 'system_security'. Measured
# against production: 41 of 46 live types matched nothing and 92.3% of all
# notification rows scored zero.
#
# Both vocabularies are kept deliberately: a token resolves as a raw type first,
# then as a canonical category via PULSE_TYPE_TO_CATEGORY. That preserves the
# arms that did work, and routes the rest through the shared map instead of a
# second hand-maintained whitelist that would drift out of sync again the next
# time a notification type is added.
_NETWORK_BUCKETS: dict[str, str] = {
    # --- raw pulse_notifications.type values ---
    "chat_message": "unread_messages", "group_message": "unread_messages",
    "room_message": "unread_messages", "message": "unread_messages",
    "friend_request": "friend_requests",
    "follow": "new_followers", "new_follower": "new_followers",
    "mention": "mentions",
    "comment": "comments", "reply": "comments",
    "reaction": "reactions",
    "marketplace_order": "marketplace_orders", "purchase": "marketplace_orders",
    "teacher_order": "marketplace_orders",
    "community": "community_events", "group": "community_events",
    "live": "community_events", "live_invite": "community_events",
    # Call OUTCOMES are scored; call LIFECYCLE is not. The six unscored types
    # are not an oversight -- they are the whole reason calls were excluded
    # wholesale before. Lifetime production volume:
    #
    #     call_ended     1420      call_missed      46   <- scored
    #     call_started    634      missed_call      27   <- scored (same event)
    #     call_accepted   436      call_declined    72   <- scored
    #     incoming_call   374      call_expired     13   <- see below
    #
    # One user (the owner) accumulated 764 lifecycle rows in 30 days. Scoring
    # those would hand a single active caller a permanent above-threshold score
    # and turn the briefing into a fixed 6-hourly send, which is exactly the
    # failure mode this work is meant to avoid. A call you started, accepted or
    # ended is a thing you were present for; a briefing telling you about it is
    # telling you what you already know. A call you MISSED is the opposite:
    # it is the one call outcome you were by definition not there for.
    #
    # incoming_call is lifecycle too -- it fires on ring, before the outcome is
    # known, so every missed call already counts once as call_missed and would
    # otherwise be counted twice. call_expired (ring timeout, 13 rows lifetime)
    # is arguably a missed call, but it is left unscored deliberately: it was
    # not in the agreed scope, and its semantics (did the callee ever see it?)
    # are not settled. Revisit it as its own decision, with evidence, not as a
    # silent widening of this one.
    "call_declined": "declined_calls",
    # NB: call_missed / missed_call are deliberately absent from this map.
    # They are two spellings of the SAME event and production dual-writes both
    # for the same call, so the GROUP BY type loop below would count each miss
    # twice. They are counted separately, deduplicated by timestamp, in
    # _collect_missed_calls.
    # --- canonical PULSE_TYPE_TO_CATEGORY values ---
    "security": "security_alerts", "system_security": "security_alerts",
    "admin_security": "security_alerts", "account": "security_alerts",
    "follows": "new_followers", "mentions": "mentions", "comments": "comments",
    "likes": "reactions", "status": "reactions",
    "marketplace": "marketplace_orders",
}


def _network_bucket(kind: str) -> str | None:
    """Resolve a notification type to a significance bucket.

    Raw type wins; otherwise fall back to the canonical category. Types with no
    category, or whose category has no bucket (crypto, call lifecycle, payments,
    premium), return None and are deliberately not counted as *network* activity.

    Calls are a partial exception and are resolved by raw type only: no call type
    has a PULSE_TYPE_TO_CATEGORY entry at all, so the fallback cannot reach them.
    call_declined maps here; call_missed/missed_call are counted separately to
    dedupe their dual-write; every other call type stays unscored on purpose.
    """
    bucket = _NETWORK_BUCKETS.get(kind)
    if bucket:
        return bucket
    try:  # imported lazily: notification_service is heavy and imports back into services
        from ..notification_service import PULSE_TYPE_TO_CATEGORY
    except Exception:  # noqa: BLE001 - degrade to raw-type matching, never fatal
        return None
    category = PULSE_TYPE_TO_CATEGORY.get(kind)
    return _NETWORK_BUCKETS.get(category) if category else None


# The two spellings production uses for one missed call. `missed_call` is the
# older writer (first seen 2026-07-03, 'Z' timestamps), `call_missed` the newer
# one (2026-07-06, '+00:00' timestamps); both are still live and both fire for
# the same call on the modern path.
_MISSED_CALL_TYPES = ("call_missed", "missed_call")


def _collect_missed_calls(cur, user_id: int, since_iso: str) -> int:
    """Count missed calls once each, despite two type spellings per call.

    Production writes BOTH `call_missed` and `missed_call` for the same missed
    call: users 4, 20, 21 and 36 hold exactly equal counts of the two, and the
    rows pair off to the same second. Summing the buckets would therefore score
    every miss twice -- a silent doubling of a weight that was chosen to need
    exactly two misses to send, which would have made one missed call trigger a
    briefing for anyone on the dual-write path.

    Collapsing on the second is what makes this exact rather than approximate.
    Taking max(call_missed, missed_call) would also fix the paired case, but it
    would under-count the unpaired one: user 1 holds 20 and 4, which is not a
    clean pairing, and two genuinely distinct misses filed under different
    spellings must still count as two. SUBSTR is used rather than LEFT because
    it is spelled the same in SQLite and PostgreSQL, and it normalises the two
    timestamp formats to a common 'YYYY-MM-DDTHH:MM:SS' prefix.
    """
    placeholders = ",".join("?" for _ in _MISSED_CALL_TYPES)
    try:
        cur.execute(
            f"""
            SELECT COUNT(*) AS n FROM (
                SELECT DISTINCT SUBSTR(created_at, 1, 19) AS at_second
                FROM pulse_notifications
                WHERE user_id=? AND COALESCE(is_read,0)=0 AND created_at>=?
                  AND type IN ({placeholders})
            ) deduped
            """,
            (int(user_id), since_iso, *_MISSED_CALL_TYPES),
        )
        row = cur.fetchone()
        if not row:
            return 0
        row = dict(row) if not isinstance(row, (tuple, list)) else {"n": row[0]}
        return int(row.get("n") or 0)
    except Exception:  # noqa: BLE001 - a fact-pack fault degrades to zero, never fatal
        logging.exception("BRIEFING_MISSED_CALL_FACTS_FAILED user_id=%s", user_id)
        return 0


def collect_network_facts(cur, user_id: int, since_iso: str) -> dict[str, Any]:
    """Owner-scoped counts from canonical tables. Never another user's metrics."""
    facts = {
        "unread_messages": 0, "friend_requests": 0, "new_followers": 0,
        "mentions": 0, "comments": 0, "reactions": 0,
        "marketplace_orders": 0, "security_alerts": 0, "community_events": 0,
        "missed_calls": 0, "declined_calls": 0,
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
            bucket = _network_bucket(str(r.get("type") or ""))
            if bucket:
                facts[bucket] += int(r.get("n") or 0)
    except Exception:  # noqa: BLE001 - a fact-pack fault degrades to zeros
        logging.exception("BRIEFING_NETWORK_FACTS_FAILED user_id=%s", user_id)
    # Counted apart from the GROUP BY above because one missed call arrives as
    # two rows under two type spellings; see _collect_missed_calls.
    facts["missed_calls"] = _collect_missed_calls(cur, user_id, since_iso)
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
        + network.get("missed_calls", 0) * WEIGHTS["missed_call"]
        + network.get("declined_calls", 0) * WEIGHTS["declined_call"]
    )


def collect_crypto_facts(cur, user_id: int, *, watchlist_enabled: bool,
                         market_enabled: bool = True,
                         since_iso: str | None = None) -> dict[str, Any]:
    """Grounded market facts from the shared snapshot + user's own alerts.

    The two topics are independent preferences and are collected independently.
    "Crypto market" OFF with "Watchlist" ON is a legitimate combination: the user
    wants their own positions, not general market colour. Previously the caller
    skipped this collector entirely when crypto was off, which silently deleted
    the watchlist too -- a watchlist toggle that read ON while doing nothing.
    """
    facts: dict[str, Any] = {
        "available": False,
        "collected_at": _now_iso(),
        "market_enabled": bool(market_enabled),
        "watchlist_enabled": bool(watchlist_enabled),
        "watchlist": [],
        "alert_proximity": [],
        "alerts_triggered": [],
    }
    if market_enabled:
        _collect_market_overview(facts)
    if watchlist_enabled:
        _collect_watchlist_facts(cur, user_id, facts)
        if since_iso:
            _collect_triggered_alerts(cur, user_id, facts, since_iso)
    return facts


def _collect_market_overview(facts: dict[str, Any]) -> None:
    """General market colour. Only reached when the crypto-market topic is ON."""
    overview = crypto_provider.get_market_overview()
    if not overview or crypto_provider.is_stale(overview):
        # Stage 32/63: never present stale data as current. Omit instead.
        facts["unavailable_reason"] = "stale_or_provider_down"
        return
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
        "eth_dominance": overview.get("eth_dominance"),
        "market_direction": overview.get("market_direction"),
        "breadth_positive_top10": overview.get("breadth_positive_top10"),
        # Paid-depth market snapshot fields. Only facts the provider actually
        # returned — no derived guesses (Coinbase fallback leaves these None).
        "total_volume_24h": overview.get("total_volume_24h"),
        "volatility_avg_abs_24h": overview.get("volatility_avg_abs_24h"),
        "gainers": [{"symbol": a["symbol"], "change_24h": a["change_24h"]} for a in movers["gainers"]],
        "losers": [{"symbol": a["symbol"], "change_24h": a["change_24h"]} for a in movers["losers"]],
    })
    try:
        facts["trending"] = [
            {"symbol": t["symbol"], "rank": t.get("rank")}
            for t in crypto_provider.get_trending() if t.get("symbol")
        ][:5]
    except Exception:  # noqa: BLE001 - trending is optional color, never fatal
        facts["trending"] = []


def _collect_watchlist_facts(cur, user_id: int, facts: dict[str, Any]) -> None:
    """The user's own assets and alert proximity. Reached whenever the watchlist
    topic is ON, independently of the general crypto-market topic.

    Reads ``alert_rules``, the table the alert engine actually evaluates.

    This used to read ``crypto_alerts``, which is a LEGACY table: it is imported
    one-way into alert_rules by reconcile_legacy_alerts (source_ref
    'crypto_alerts:<id>') and never written back, so it drifts further from
    reality with every alert a user creates. The whole of production holds just
    two legacy rows against three live rules, and they disagree in all three
    possible directions at once:

      * stale     -- the owner (user 1) has a legacy row reading "BTC above
        45000" while their live rules are "BTC above 80000" and "BTC below
        79000". With BTC near 78400 the briefing was reporting proximity to a
        threshold crossed long ago, and was blind to the below-79000 rule that
        is the one actually firing;
      * invisible -- user 34 holds a live rule (BTC above 61000) and no legacy
        row at all, so their alert could never appear in a briefing;
      * phantom   -- user 19 holds a legacy row (BTC above 50000) with no live
        rule behind it, so briefings reported proximity for an alert the engine
        does not evaluate and which can never fire.

    The liveness predicate is copied verbatim from the engine's own claim guard
    (alert_engine._active_claim_guard) so that briefings describe exactly the
    rule set the engine evaluates -- no more, no less. The COALESCEs are not
    defensive padding: `active` and `deleted_at` are both nullable, the engine
    treats NULL and '' alike for deleted_at, and a rule can carry
    status='active' while still being soft-deleted (the owner has 38 such rows).
    Matching on status alone would resurrect all of them.

    Column names differ between the two tables (symbol/condition/threshold_value
    here, asset_symbol/condition_type/target_value there). threshold_value is
    COALESCEd with the older target_value spelling because both are populated
    depending on which writer created the rule.
    """
    try:
        cur.execute(
            "SELECT symbol, condition, "
            "       COALESCE(threshold_value, target_value) AS threshold_value "
            "FROM alert_rules "
            "WHERE user_id=? "
            "  AND COALESCE(status, 'active')='active' "
            "  AND COALESCE(active, 1)=1 "
            "  AND COALESCE(deleted_at, '')='' "
            "LIMIT 25",
            (int(user_id),),
        )
        rows = [dict(r) if not isinstance(r, dict) else r for r in cur.fetchall()]
        symbols = sorted({str(r.get("symbol") or "").upper() for r in rows if r.get("symbol")})
        snapshots = {a["symbol"]: a for a in crypto_provider.get_watchlist_snapshots(symbols)}
        facts["watchlist"] = [
            {"symbol": s, "price": snapshots[s]["price"], "change_24h": snapshots[s]["change_24h"]}
            for s in symbols if s in snapshots
        ]
        for r in rows:  # Stage 41: proximity only — never trigger early
            symbol = str(r.get("symbol") or "").upper()
            snap = snapshots.get(symbol)
            try:
                threshold = float(r.get("threshold_value") or 0)
            except (TypeError, ValueError):
                continue
            # `condition` holds the stored condition vocabulary
            # (above/below/moves_up_percent/...), NOT the derived alert-type
            # strings the old code compared against. Percent-move conditions
            # carry a percentage in the threshold, so a price-distance
            # calculation would be meaningless for them.
            if not snap or threshold <= 0 or str(r.get("condition") or "") not in ("above", "below"):
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


def _collect_triggered_alerts(cur, user_id: int, facts: dict[str, Any],
                              since_iso: str) -> None:
    """Alerts that actually fired in this window, one entry per latch episode.

    Reads ``alert_events`` -- the canonical record the alert engine writes under
    a compare-and-set claim -- and NOT the ``crypto_alert_triggered`` rows in
    pulse_notifications. The two are not interchangeable, and the difference is
    the whole point of this collector.

    An alert_event is created once per *notification*, so a rule in 'progress'
    repeat mode emits one every time the price moves further into the breach.
    Production, measured over a single 6h briefing window: the owner had 21 raw
    events carrying 21 distinct trigger_keys -- but all 21 belonged to ONE rule,
    BTC below 79,000, walking down from 78,996 to 78,432. Every one of those
    keys is legitimately distinct; they are not duplicates and there is nothing
    to clean up. They are simply not 21 things to tell someone about. They are
    one thing: "BTC fell below your 79,000 alert, now 78,432."

    So the unit here is the latch EPISODE, keyed by alert_rule_id within the
    window, not the event and not the trigger_key. Deduping on trigger_key would
    have produced 21 items; counting raw notifications would also have produced
    21; grouping by rule produces 1. The progression is preserved as a
    `notifications` count on the single entry, so the fact pack can still say
    "and it kept moving" without scoring it 21 times.

    Only the newest event per rule is reported: for a rule walking into a
    breach, the latest observed value is the true current state, and the
    intermediate steps are history the user does not need recited.
    """
    try:
        cur.execute(
            """
            SELECT alert_rule_id, symbol, condition, threshold_value,
                   observed_value, created_at, trigger_key
            FROM alert_events
            WHERE user_id=? AND created_at>=? AND alert_rule_id IS NOT NULL
            ORDER BY alert_rule_id ASC, created_at ASC, id ASC
            """,
            (int(user_id), since_iso),
        )
        episodes: dict[int, dict[str, Any]] = {}
        for row in cur.fetchall():
            r = dict(row) if not isinstance(row, dict) else row
            try:
                rule_id = int(r.get("alert_rule_id"))
            except (TypeError, ValueError):
                continue
            entry = episodes.get(rule_id)
            if entry is None:
                episodes[rule_id] = entry = {
                    "rule_id": rule_id,
                    "symbol": str(r.get("symbol") or "").upper(),
                    "condition": str(r.get("condition") or ""),
                    "threshold": r.get("threshold_value"),
                    "observed": r.get("observed_value"),
                    "first_at": r.get("created_at"),
                    "last_at": r.get("created_at"),
                    "notifications": 0,
                }
            # Ordered oldest-first above, so the last row seen is the newest and
            # its observed value is the rule's current state.
            entry["observed"] = r.get("observed_value")
            entry["last_at"] = r.get("created_at")
            entry["notifications"] += 1
        facts["alerts_triggered"] = [
            episodes[k] for k in sorted(episodes)
        ][:10]
    except Exception:  # noqa: BLE001 - a fact-pack fault degrades to none, never fatal
        logging.exception("BRIEFING_TRIGGERED_ALERT_FACTS_FAILED user_id=%s", user_id)


def crypto_significance(crypto: dict[str, Any]) -> int:
    """Score the two crypto topics independently.

    General market movement only scores when the crypto-market topic is ON and a
    fresh snapshot exists. Watchlist and alert-proximity entries are only ever
    populated when the watchlist topic is ON, so they are scored unconditionally
    -- gating them behind the market snapshot (the old `available` early return)
    meant a stale provider silently zeroed a user's own alert proximity too.
    """
    if not crypto:
        return 0
    score = 0
    if crypto.get("available") and crypto.get("market_enabled", True):
        btc = abs(crypto.get("btc_change_24h") or 0)
        cap = abs(crypto.get("market_cap_change_24h_pct") or 0)
        if btc >= MARKET_MOVE_THRESHOLD_PCT:
            score += 10
        if cap >= MARKET_MOVE_THRESHOLD_PCT:
            score += 6
    score += 8 * len(crypto.get("alert_proximity") or [])
    score += sum(4 for w in crypto.get("watchlist") or [] if abs(w.get("change_24h") or 0) >= 4.0)
    # An alert the user configured themselves actually firing is the single most
    # requested thing a briefing can carry, so one episode (12) clears
    # SEND_THRESHOLD on its own -- more than proximity (8), which is only a
    # near-miss. Scored per EPISODE, never per notification: the owner's 21
    # events in one window were one rule progressing, and scoring them
    # individually would have contributed 252 and pinned the briefing on
    # forever. That is the amplification this collector exists to prevent.
    score += 12 * len(crypto.get("alerts_triggered") or [])
    return score


def build_briefing_facts(cur, user_id: int, *, since_iso: str, timezone_name: str,
                         locale: str, prefs: dict[str, Any]) -> dict[str, Any]:
    """Stage 10 contract: the ONLY payload the summarizer ever sees."""
    network = collect_network_facts(cur, user_id, since_iso) if prefs.get("network_enabled", True) else None
    # Crypto market and watchlist are independent topics. Collect if EITHER is on
    # and let the collector include only the enabled halves; skipping the whole
    # collector when the market topic was off also deleted the watchlist.
    market_on = bool(prefs.get("crypto_enabled", True))
    watchlist_on = bool(prefs.get("watchlist_enabled", True))
    crypto = collect_crypto_facts(
        cur, user_id, watchlist_enabled=watchlist_on, market_enabled=market_on,
        since_iso=since_iso,
    ) if (market_on or watchlist_on) else None
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
        # missed_calls joins the signature because it can carry a briefing on its
        # own (two misses hit SEND_THRESHOLD exactly), so a window whose only
        # change is a new missed call must not hash identically to the previous
        # one and get dropped as a duplicate. declined_calls is deliberately
        # left out: at weight 1 it can never reach the threshold unaided, so it
        # has nothing to say that would justify re-sending a briefing.
        "net_counts": [network.get(k, 0) for k in (
            "unread_messages", "friend_requests", "new_followers", "mentions",
            "comments", "marketplace_orders", "security_alerts",
            "missed_calls")] if network else [],
        "btc": bucket(crypto.get("btc_change_24h")),
        "eth": bucket(crypto.get("eth_change_24h")),
        "cap": bucket(crypto.get("market_cap_change_24h_pct")),
        "direction": crypto.get("market_direction"),
        "proximity": sorted(p["symbol"] for p in crypto.get("alert_proximity") or []),
        # Keyed by rule id, not by trigger_key or event count: a rule that keeps
        # progressing within one latch episode must hash the SAME so the window
        # is suppressed as a duplicate rather than re-sent on every further tick.
        # A different rule firing is genuinely new and must change the hash.
        "fired": sorted(a["rule_id"] for a in crypto.get("alerts_triggered") or []),
    }
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:32]
