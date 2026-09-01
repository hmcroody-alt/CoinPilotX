"""Market Pulse → UNDX live crypto context bridge (read model).

When a person taps "Ask UNDX" on an asset screen, the client sends a structured
*market context envelope* built from the same typed state that rendered the
screen — never scraped UI text, never a fabricated user message. This module is
the single place that envelope is validated, persisted, resolved against
("it", "this coin"), and turned into grounded live facts.

Three properties are load-bearing:

**No new provider stack.** Every live read goes through ``services.market_pulse``
and its foundations (``market_data``, ``pulse_briefings.crypto_provider``), which
already share the canonical CoinGecko cache, single-flight, and monthly budget
guard. Ten repeated price questions cost the same one provider call the
dashboard was going to make anyway. There is no API key, no direct HTTP, and no
fourth cache here.

**Context grants READ, never WRITE.** The envelope can make UNDX *know about*
an asset; acting on it (alerts, watchlist) still travels the existing governed
capability gateway with its confirmation, idempotency, and verification
machinery untouched.

**Facts are grounded or absent.** The grounding block this module produces is a
verified live source supplied *with the request*, which under
``undx_fact_policy`` upgrades crypto market claims to CURRENT_VERIFIED — with
the observation time and staleness carried alongside so the model can disclose
rather than pretend. A crypto question must never fall into the "verified
company metric" refusal; when live data is unavailable the block says so
explicitly instead of vanishing.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from services import market_data, market_pulse

LOGGER = logging.getLogger(__name__)

ENVELOPE_VERSION = 1

#: Key under which the envelope rides inside ``ui_context`` and inside the
#: persisted ``pulse_ai_client_contexts.context_json`` blob.
CONTEXT_KEY = "market_context"

#: Past this age the *envelope snapshot* (what the person was looking at) is no
#: longer treated as "what you are seeing now" — live reads are re-fetched every
#: turn regardless, so this only governs how the snapshot is described.
SNAPSHOT_TTL_SECONDS = 180

#: Past this age the stored context stops steering coreference at all. A person
#: asking "what's it at?" a day after leaving the chart is more likely starting
#: fresh than continuing; answering about yesterday's coin would be a guess
#: dressed as memory.
CONTEXT_TTL_SECONDS = 6 * 3600

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,12}$")

_ALLOWED_SOURCES = {"market_pulse", "asset_detail", "watchlist", "alerts", "dashboard"}

#: Deictic phrases that hand the subject to the active context.
_DEICTIC = ("it", "this coin", "this asset", "this token", "this one",
            "the chart", "this chart", "that coin", "the coin")

_CRYPTO_WORDS = ("price", "crypto", "coin", "token", "market cap", "marketcap",
                 "dominance", "volume", "chart", "candle", "rally", "dip",
                 "all-time high", "ath", "bull", "bear", "gainer", "loser",
                 "trending", "watchlist", "portfolio", "satoshi", "blockchain")

_MARKET_WIDE_WORDS = ("dominance", "total market", "market cap", "whole market",
                      "market doing", "market direction", "market overview",
                      "market today", "crypto market")

_HISTORY_WORDS = ("chart", "high", "low", "range", "moved", "performance",
                  "performed", "trend", "history", "over the last", "today",
                  "this week", "this month", "this year", "24 hours", "24h")

_OVERLAY_WORDS = ("watchlist", "watching", "alert", "alerts")

#: Words that, with an active context, mean the person is still talking about
#: the asset on screen even without a pronoun — "what were the high and low
#: today?" from the Ethereum chart is about Ethereum.
_CONTEXT_LEAN_WORDS = _CRYPTO_WORDS + _HISTORY_WORDS + _OVERLAY_WORDS


def _lower_words(text: str) -> str:
    """Lowercased, punctuation folded to spaces, padded for whole-word tests.

    Deixis lives at sentence ends — "alerts on it?" — so matching against raw
    text with punctuation attached would silently drop exactly the phrases this
    module exists to resolve.
    """
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip() + " "


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


# ---------------------------------------------------------------------------
# Envelope validation and persistence
# ---------------------------------------------------------------------------


def sanitize_market_context(value: Any) -> dict[str, Any] | None:
    """Validate one client envelope, or return None if it has no asset identity.

    The client is trusted for *which screen the person was on*, never for what
    is true of the market: numeric snapshot fields are carried only as "what the
    screen showed" and every grounded answer re-reads the canonical layer. A
    symbol that is not plain ``[A-Z0-9]`` is rejected outright — it is about to
    be matched against provider data and echoed into model grounding, and an
    envelope is still untrusted client input.
    """
    if not isinstance(value, dict):
        return None
    asset_raw = value.get("asset")
    if not isinstance(asset_raw, dict):
        return None
    symbol = _text(asset_raw.get("symbol"), 12).upper()
    if not symbol or not _SYMBOL_RE.match(symbol):
        return None
    asset = {
        "id": _text(asset_raw.get("id"), 60).lower() or symbol.lower(),
        "symbol": symbol,
        "name": _text(asset_raw.get("name"), 80) or symbol,
        "rank": _int(asset_raw.get("rank")),
    }
    snapshot_raw = value.get("market_snapshot")
    snapshot: dict[str, Any] = {}
    if isinstance(snapshot_raw, dict):
        snapshot = {
            "price": _num(snapshot_raw.get("price")),
            "change24h": _num(snapshot_raw.get("change24h")),
            "marketCap": _num(snapshot_raw.get("marketCap")),
            "volume24h": _num(snapshot_raw.get("volume24h")),
            "observedAt": _text(snapshot_raw.get("observedAt"), 40) or None,
            "source": _text(snapshot_raw.get("source"), 40) or None,
            "stale": bool(snapshot_raw.get("stale")),
        }
    chart_raw = value.get("chart")
    selected_range = ""
    if isinstance(chart_raw, dict):
        selected_range = normalize_range(chart_raw.get("selected_range"))
    related_raw = value.get("related_market")
    related: dict[str, Any] = {}
    if isinstance(related_raw, dict):
        related = {
            "totalMarketCap": _num(related_raw.get("totalMarketCap")),
            "btcDominance": _num(related_raw.get("btcDominance")),
            "marketDirection": _text(related_raw.get("marketDirection"), 24) or None,
        }
    overlay_raw = value.get("user_overlay")
    overlay: dict[str, Any] = {}
    if isinstance(overlay_raw, dict):
        overlay = {
            # Client hints only. Anything shown to the model about *this*
            # account is recomputed server-side, owner-scoped, at answer time.
            "watchlisted": bool(overlay_raw.get("watchlisted")),
            "alert_count": max(0, _int(overlay_raw.get("alert_count")) or 0),
        }
    source = _text(value.get("source"), 24)
    return {
        "version": ENVELOPE_VERSION,
        "source": source if source in _ALLOWED_SOURCES else "market_pulse",
        "context_type": "asset_focus",
        "asset": asset,
        "market_snapshot": snapshot,
        "chart": {"selected_range": selected_range or "24H"},
        "related_market": related,
        "user_overlay": overlay,
    }


def stamp(context: dict[str, Any]) -> dict[str, Any]:
    """Server attach-time. The client's clock is not part of any TTL decision."""
    context["attached_at"] = _now_iso()
    return context


def age_seconds(context: dict[str, Any] | None) -> int | None:
    if not isinstance(context, dict):
        return None
    raw = str(context.get("attached_at") or "")
    try:
        attached = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if attached.tzinfo is None:
        attached = attached.replace(tzinfo=timezone.utc)
    return max(0, int(datetime.now(timezone.utc).timestamp() - attached.timestamp()))


def is_expired(context: dict[str, Any] | None) -> bool:
    age = age_seconds(context)
    return age is None or age > CONTEXT_TTL_SECONDS


def load_stored(cur, user_id: int, conversation_id: int) -> dict[str, Any] | None:
    """The persisted envelope for this conversation, or None."""
    try:
        cur.execute(
            "SELECT context_json FROM pulse_ai_client_contexts WHERE user_id=? AND conversation_id=?",
            (int(user_id), int(conversation_id)),
        )
        row = cur.fetchone()
    except Exception:  # noqa: BLE001 - context is optional colour, never fatal
        return None
    if not row:
        return None
    try:
        stored = json.loads(row["context_json"] if hasattr(row, "keys") else row[0])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    context = stored.get(CONTEXT_KEY) if isinstance(stored, dict) else None
    return context if isinstance(context, dict) and context.get("asset") else None


def merge_for_persist(ui_context: dict[str, Any], incoming: dict[str, Any] | None,
                      stored: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Combine hint context with the market envelope; return (to_persist, active).

    A fresh envelope *replaces* the stored one unconditionally — opening
    Solana's screen after asking about Ethereum must not leave two assets
    fighting over "it" (mission stage 20). A message that carries no envelope
    *preserves* the stored one, so the context survives ordinary turns and
    re-renders rather than evaporating on the first follow-up question.
    """
    if incoming:
        active: dict[str, Any] | None = stamp(dict(incoming))
    elif stored and not is_expired(stored):
        active = stored
    else:
        active = None
    combined = dict(ui_context or {})
    if active:
        combined[CONTEXT_KEY] = active
    return combined, active


def active_context_for_user(user_id: int) -> dict[str, Any] | None:
    """The freshest stored envelope for this account, for agent executors.

    There is exactly one Pulse AI conversation per user, so "freshest row for
    this user" and "the conversation's context" are the same thing. Fails open
    to None: an executor without context asks for a symbol, it never guesses.
    """
    try:
        import bot

        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT context_json FROM pulse_ai_client_contexts WHERE user_id=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (int(user_id),),
        )
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    try:
        stored = json.loads(row["context_json"])
    except (TypeError, ValueError, KeyError):
        return None
    context = stored.get(CONTEXT_KEY) if isinstance(stored, dict) else None
    if not isinstance(context, dict) or not context.get("asset"):
        return None
    return None if is_expired(context) else context


# ---------------------------------------------------------------------------
# Resolution: which asset, which range
# ---------------------------------------------------------------------------


_RANGE_WORDS = (
    ("1H", ("last hour", "past hour", "this hour", "1 hour", "one hour")),
    ("1Y", ("this year", "past year", "last year", "12 months", "one year", "1 year")),
    ("3M", ("90 days", "3 months", "three months", "quarter")),
    ("1M", ("30 days", "this month", "past month", "last month", "1 month", "one month")),
    ("7D", ("this week", "past week", "last week", "7 days", "seven days")),
    ("24H", ("today", "24 hours", "24h", "last day", "past day")),
    ("ALL", ("all time", "all-time", "max", "since launch", "ever")),
)


def normalize_range(value: Any) -> str:
    key = _text(value, 8).upper()
    key = dict(market_data.HISTORY_RANGE_ALIASES).get(key, key)
    return key if key in market_data.HISTORY_RANGES else ""


def resolve_range(text: str, context: dict[str, Any] | None) -> str:
    """Explicit words win; otherwise the range the person is looking at.

    Range continuity (mission stage 14): "and over 30 days?" switches the
    grounding window without touching the stored envelope, so "back to today"
    still lands on the chart's own selected range.
    """
    lowered = " ".join(str(text or "").lower().split())
    for key, phrases in _RANGE_WORDS:
        if any(phrase in lowered for phrase in phrases):
            return key
    if isinstance(context, dict):
        stored = normalize_range((context.get("chart") or {}).get("selected_range"))
        if stored:
            return stored
    return "24H"


def _board_assets() -> list[dict[str, Any]]:
    try:
        return market_pulse.market_rows("all", 80).get("assets") or []
    except Exception:  # noqa: BLE001
        return []


def resolve_asset(text: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
    """The asset a message is about: explicit mention wins, then deixis.

    A person on the Ethereum screen who asks about Solana gets Solana — the
    envelope steers pronouns, it does not overrule names. Matching runs against
    the same board that supplies prices, so a resolved asset is always one the
    price engine can actually answer for.
    """
    lowered = _lower_words(text)
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    explicit = None
    for asset in _board_assets():
        symbol = str(asset.get("symbol") or "").lower()
        name = str(asset.get("name") or "").lower()
        if symbol and symbol in tokens:
            explicit = asset
            break
        if name and len(name) >= 3 and f" {name} " in lowered:
            explicit = asset
            break
    if explicit:
        return {"id": explicit.get("id"), "symbol": explicit.get("symbol"),
                "name": explicit.get("name"), "via": "explicit"}
    if isinstance(context, dict) and not is_expired(context):
        asset = context.get("asset") or {}
        if asset.get("symbol") and (
            any(f" {phrase} " in lowered or lowered.strip().startswith(phrase) for phrase in _DEICTIC)
            or any(word in lowered for word in _CONTEXT_LEAN_WORDS)
        ):
            return {"id": asset.get("id"), "symbol": asset.get("symbol"),
                    "name": asset.get("name"), "via": "context"}
    return None


def is_crypto_query(text: str, context: dict[str, Any] | None) -> bool:
    lowered = _lower_words(text)
    if any(word in lowered for word in _CRYPTO_WORDS):
        return True
    if resolve_asset(text, None):
        return True
    if isinstance(context, dict) and not is_expired(context):
        return any(f" {phrase} " in lowered for phrase in _DEICTIC)
    return False


# ---------------------------------------------------------------------------
# Canonical live reads (shared caches; zero new provider polling)
# ---------------------------------------------------------------------------


def quote(symbol: str) -> dict[str, Any] | None:
    """One live-priced asset row from the shared board, with freshness."""
    symbol = _text(symbol, 12).upper()
    if not symbol:
        return None
    try:
        result = market_pulse.search(symbol, limit=5)
    except Exception:  # noqa: BLE001
        return None
    match = next((a for a in result.get("assets") or [] if a.get("symbol") == symbol), None)
    if not match:
        return None
    return {**match, "freshness": result.get("freshness") or {}}


def history_pack(symbol: str, range_key: str) -> dict[str, Any]:
    """A chart summarized as facts — never raw point dumps into a prompt.

    Start, end, high, low, and percent change are things the series actually
    asserts; "support levels" and "patterns" are not, and are deliberately
    absent. Cached per (coin, range) inside ``market_data``.
    """
    range_key = normalize_range(range_key) or "24H"
    try:
        series = market_pulse.asset_history(symbol, range_key)
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("undx market history unavailable symbol=%s: %s", symbol, exc)
        series = {"ok": False}
    points = [p for p in (series.get("points") or []) if isinstance(p, dict) and _num(p.get("price")) is not None]
    if not series.get("ok") or len(points) < 2:
        return {"ok": False, "symbol": symbol, "range": range_key,
                "warning": series.get("warning") or "History for this range is unavailable right now."}
    prices = [float(p["price"]) for p in points]
    start, end = prices[0], prices[-1]
    return {
        "ok": True,
        "symbol": symbol,
        "range": range_key,
        "start": start,
        "end": end,
        "high": max(prices),
        "low": min(prices),
        "changePct": round(((end - start) / start) * 100, 4) if start else None,
        "points": len(prices),
        "source": series.get("source"),
        "stale": bool(series.get("stale")),
    }


def overview() -> dict[str, Any]:
    try:
        return market_pulse.global_metrics()
    except Exception:  # noqa: BLE001
        return {"available": False}


def overlay(user_id: int, symbol: str) -> dict[str, Any]:
    """Owner-scoped watchlist/alert facts, recomputed at answer time.

    The requester's id comes from the session, never from the envelope, so one
    account can never be shown another's overlay (mission stage 15). READ only:
    changing anything still requires the governed write capabilities.
    """
    symbol = _text(symbol, 12).upper()
    result: dict[str, Any] = {"symbol": symbol, "watchlisted": None, "alert_count": None}
    try:
        from services import portfolio_service

        result["watchlisted"] = symbol in (portfolio_service.watchlist_symbols(int(user_id)) or set())
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import alert_engine

        rules = (alert_engine.list_alert_rules(int(user_id), limit=50, symbol=symbol) or {}).get("alerts") or []
        result["alert_count"] = len(rules)
    except Exception:  # noqa: BLE001
        pass
    return result


# ---------------------------------------------------------------------------
# Grounding block for the conversational path
# ---------------------------------------------------------------------------


def grounding_block(user_id: int, body: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
    """A knowledge item that grounds crypto claims in the canonical live layer.

    Returned in the exact shape ``pulse_ai_knowledge.build_messages`` consumes.
    Present whenever the message is a crypto question or leans on the active
    context; absent otherwise, so non-crypto turns pay nothing. When the live
    layer is down the block still appears — carrying the unavailability — so
    the model discloses instead of either inventing a price or misrouting to
    the company-metric refusal.
    """
    lowered = " ".join(str(body or "").lower().split())
    target = resolve_asset(body, context)
    market_wide = any(word in lowered for word in _MARKET_WIDE_WORDS)
    if not target and not market_wide:
        return None
    payload: dict[str, Any] = {"instructions": (
        "Live crypto market context from PulseSoc's canonical market feed "
        "(CoinGecko-backed, shared cache). Treat these figures as the verified "
        "live source for crypto market claims in this reply. Always disclose "
        "the observation time or staleness when stating a figure. If a figure "
        "is null or marked unavailable, say the live value is unavailable — "
        "never estimate one. This context grants no permission to change "
        "alerts or watchlists."
    )}
    if isinstance(context, dict) and context.get("asset"):
        snapshot_age = age_seconds(context)
        payload["viewing"] = {
            "screen": context.get("source"),
            "asset": context.get("asset"),
            "selected_range": (context.get("chart") or {}).get("selected_range"),
            "attached_seconds_ago": snapshot_age,
            "screen_snapshot": (context.get("market_snapshot")
                                if snapshot_age is not None and snapshot_age <= SNAPSHOT_TTL_SECONDS
                                else None),
        }
    if target:
        live = quote(str(target.get("symbol") or ""))
        payload["asset"] = {
            "resolved_via": target.get("via"),
            "symbol": target.get("symbol"),
            "name": target.get("name"),
            "live_quote": live or {"available": False,
                                   "note": "Live pricing is unavailable for this asset right now."},
        }
        if any(word in lowered for word in _HISTORY_WORDS) or (isinstance(context, dict) and context.get("chart")):
            payload["history"] = history_pack(str(target.get("symbol") or ""), resolve_range(body, context))
        if any(word in lowered for word in _OVERLAY_WORDS):
            payload["your_account"] = overlay(int(user_id), str(target.get("symbol") or ""))
    if market_wide:
        payload["market_overview"] = overview()
    return {
        "id": 0,
        "title": "Live crypto market context",
        "category": "crypto_market",
        "body": json.dumps(payload, separators=(",", ":"), default=str)[:6000],
    }


def telemetry(context: dict[str, Any] | None, block: dict[str, Any] | None) -> dict[str, Any]:
    """What happened, without user text or account data (mission stage 27)."""
    return {
        "context_attached": bool(context),
        "context_source": (context or {}).get("source") if isinstance(context, dict) else None,
        "context_symbol": ((context or {}).get("asset") or {}).get("symbol") if isinstance(context, dict) else None,
        "context_age_seconds": age_seconds(context),
        "grounded": bool(block),
    }
