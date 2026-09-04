"""Market Pulse — the read model behind the live crypto command center.

This module owns no networking and no cache of its own. It composes the two
market foundations that already exist:

  * ``services.market_data``                  — the 50-row board (rank, logo,
    price, 24h change, market cap, volume, sparkline) plus real price history.
  * ``services.pulse_briefings.crypto_provider`` — the only place in the product
    that calls ``/global`` and ``/search/trending``, i.e. the only honest source
    for the global strip and for *actual* trending.

Both are already polled for the dashboard board and for Pulse Briefings, and
both already stretch their TTLs under ``coingecko_client.effective_ttl``. So
Market Pulse adds zero new provider polling and zero new budget pressure: N
users still cost one call per window, the same call the rest of the product was
making anyway. A fourth cache here would have been a fourth thing to teach about
the credit guard.

Everything leaves here as a PulseSoc market object — ``{id, symbol, name, rank,
price, change24h, marketCap, volume24h, image, updatedAt}`` — so no CoinGecko
field name reaches a client. Absence is null, never zero: a price of 0.00 and a
0.00% change both read as facts on a screen.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from services import market_data
from services import market_intelligence
from services.pulse_briefings import crypto_provider

LOGGER = logging.getLogger(__name__)

# Beyond this the strip stops claiming to be live. It matches the provider's own
# hard serve limit so the two cannot disagree about what "stale" means.
STALE_AFTER_SECONDS = crypto_provider.STALE_MAX_SECONDS

CATEGORIES = ("all", "gainers", "losers", "trending", "watchlist")

# Chip -> board sort. Only categories the canonical provider data can actually
# answer. "trending" is deliberately absent: it is real /search/trending data,
# not a sort of the board, and labelling a big mover "trending" would be a
# claim the provider never made.
_CATEGORY_SORT = {
    "all": "top_market_cap",
    "gainers": "gainers",
    "losers": "losers",
    "watchlist": "top_market_cap",
}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_asset(row: dict[str, Any] | None, updated_at: str | None = None) -> dict[str, Any] | None:
    """One board row as a PulseSoc market object, or None if it has no identity."""
    if not isinstance(row, dict):
        return None
    symbol = str(row.get("symbol") or "").upper()
    coin_id = str(row.get("id") or "").strip()
    if not symbol and not coin_id:
        return None
    return {
        "id": coin_id or symbol.lower(),
        "symbol": symbol,
        "name": str(row.get("name") or symbol),
        "rank": _int(row.get("market_cap_rank")),
        "price": _num(row.get("price")),
        "change24h": _num(row.get("change_24h")),
        "marketCap": _num(row.get("market_cap")),
        "volume24h": _num(row.get("volume_24h")),
        "image": str(row.get("image") or ""),
        # Free with the board request — a 50-row list still costs one call.
        "sparkline": [p for p in (row.get("sparkline") or []) if isinstance(p, (int, float))],
        "updatedAt": updated_at,
    }


def _board(category: str = "all", limit: int = 50) -> dict[str, Any]:
    sort_key = _CATEGORY_SORT.get(category, "top_market_cap")
    try:
        # One shared board. Sorting happens inside market_data against the
        # cached rows, so changing chips never costs a provider call.
        return market_data.live_market_board(category=sort_key, limit=max(1, min(int(limit or 50), 80)))
    except Exception as exc:  # noqa: BLE001 - the board owns its own fallback ladder
        LOGGER.info("Market Pulse board unavailable: %s", exc)
        return {"source": "unavailable", "markets": [], "warning": "Market data is temporarily unavailable.",
                "age_seconds": None, "observed_epoch": None, "updated_at": None, "summary": {}}


def _freshness(board: dict[str, Any]) -> dict[str, Any]:
    """How old the data is, and whether it may still be called live.

    Age is measured from when the provider answered, not from when this request
    was served, so a cache hit does not reset the label to "just now".
    """
    age = board.get("age_seconds")
    age = _int(age) if age is not None else None
    source = str(board.get("source") or "unavailable")
    degraded = source not in {"coingecko"}
    stale = source == "unavailable" or (age is not None and age > STALE_AFTER_SECONDS)
    return {
        "ageSeconds": age,
        "observedAt": board.get("updated_at"),
        "source": source,
        # The only field a client should consult before drawing a "LIVE" dot.
        "live": bool(not stale and not degraded and board.get("markets")),
        "stale": bool(stale),
        "degraded": bool(degraded),
        "warning": board.get("warning") or None,
    }


def global_metrics() -> dict[str, Any]:
    """The market strip: total cap, 24h volume, BTC/ETH dominance, 24h change.

    Read from the briefings overview, which is the only ``/global`` caller in
    the product. Every field is either a provider number or null — the strip
    renders "--" rather than inventing a figure.
    """
    overview = crypto_provider.get_market_overview() or {}
    if not overview:
        return {"available": False, "provider": None, "observedAt": None, "stale": True,
                "totalMarketCap": None, "totalVolume24h": None, "btcDominance": None,
                "ethDominance": None, "marketCapChange24hPct": None, "marketDirection": None}
    stale = crypto_provider.is_stale(overview)
    return {
        # /global is a CoinGecko aggregate; the Coinbase fallback has no
        # equivalent, so under fallback these are null rather than zero.
        "available": overview.get("total_market_cap") is not None,
        "provider": overview.get("provider"),
        "observedAt": overview.get("generated_at"),
        "stale": bool(stale),
        "totalMarketCap": _num(overview.get("total_market_cap")),
        "totalVolume24h": _num(overview.get("total_volume_24h")),
        "btcDominance": _num(overview.get("btc_dominance")),
        "ethDominance": _num(overview.get("eth_dominance")),
        "marketCapChange24hPct": _num(overview.get("market_cap_change_24h_pct")),
        "marketDirection": overview.get("market_direction"),
    }


def _volume_history(symbol: str) -> list[float]:
    """Recorded 24h-volume readings for one symbol, or [] if none are stored.

    ``market_observations`` is written once per ``alert_worker`` cycle from the
    same board this module reads, so this is a local table read, not a provider
    call. When the table is empty the answer is an empty list and the volume
    layer reports UNAVAILABLE — never "normal", which would be an assertion
    about data we do not have.
    """
    try:
        from services import market_observations

        rows = market_observations.get_observations(symbol, limit=48) or []
    except Exception as exc:  # noqa: BLE001 - volume history is optional depth
        LOGGER.info("Volume history unavailable for %s: %s", symbol, exc)
        return []
    values = []
    for row in rows:
        volume = row.get("volume_24h") if isinstance(row, dict) else None
        number = _num(volume)
        if number is not None:
            values.append(number)
    # ``get_observations`` returns oldest first, which is the order the anomaly
    # scan expects: the last element is the current reading and everything
    # before it is the baseline it is unusual (or not) against.
    return values


def _intelligence_context(board: dict[str, Any]) -> dict[str, Any]:
    """Board-wide context computed once, then shared by every row.

    Breadth, the median 24h change and the BTC benchmark are properties of the
    whole board, so computing them per asset would be fifty times the work for
    the same answer — and would let two rows in one response disagree about what
    the market is doing.
    """
    rows = board.get("markets") or []
    context = market_intelligence.board_context(rows, global_metrics())
    context["observedAt"] = board.get("updated_at")
    return context


def _attach_intelligence(assets: list[dict[str, Any]], board: dict[str, Any],
                         context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Row-level verdicts for a whole list, from data already in hand.

    The series each verdict is built on is the un-thinned 7-day hourly sparkline
    that arrived with the board request (``market_data.hourly_series``). No
    branch of this function can make a network call, which is what keeps a
    fifty-row list at one provider call rather than fifty.
    """
    context = context or _intelligence_context(board)
    for asset in assets:
        symbol = str(asset.get("symbol") or "")
        series = market_data.hourly_series(symbol) or asset.get("sparkline") or []
        try:
            asset["intelligence"] = market_intelligence.assess(
                asset, series, context, volume_series=None, holding=None, depth="list",
            )
        except Exception as exc:  # noqa: BLE001 - a verdict must never break the list
            LOGGER.info("Intelligence unavailable for %s: %s", symbol, exc)
            asset["intelligence"] = None
    return assets


def market_rows(category: str = "all", limit: int = 50) -> dict[str, Any]:
    """The asset list for one chip, plus how fresh it is."""
    category = str(category or "all").strip().lower()
    if category not in CATEGORIES:
        category = "all"
    board = _board(category, limit)
    updated_at = board.get("updated_at")
    assets = [a for a in (normalize_asset(row, updated_at) for row in board.get("markets") or []) if a]
    _attach_intelligence(assets, board)
    return {"category": category, "assets": assets, "freshness": _freshness(board)}


def trending() -> dict[str, Any]:
    """CoinGecko's own trending list, priced from the shared board.

    Trending is a provider signal about what people are *searching for*. It is
    not "today's biggest movers" — deriving it from price change would put a
    label on the screen that the provider never asserted. A trending coin that
    is outside the 50-row board keeps a null price rather than being dropped or
    guessed at.
    """
    board = _board("all", 80)
    index = {}
    for row in board.get("markets") or []:
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            index.setdefault(symbol, row)
    updated_at = board.get("updated_at")
    rows = []
    try:
        coins = crypto_provider.get_trending()
    except Exception as exc:  # noqa: BLE001 - trending is optional colour, never fatal
        LOGGER.info("Market Pulse trending unavailable: %s", exc)
        coins = []
    for coin in coins:
        symbol = str(coin.get("symbol") or "").upper()
        if not symbol:
            continue
        asset = normalize_asset(index.get(symbol), updated_at) or {
            "id": symbol.lower(), "symbol": symbol, "name": coin.get("name") or symbol,
            "rank": _int(coin.get("rank")), "price": None, "change24h": None,
            "marketCap": None, "volume24h": None, "image": "", "sparkline": [],
            "updatedAt": updated_at,
        }
        asset["trending"] = True
        rows.append(asset)
    # A trending coin outside the board has a null price and no series, so its
    # verdict comes back DATA_UNAVAILABLE rather than being derived from the one
    # thing we know about it, which is that people are searching for it.
    _attach_intelligence(rows, board)
    return {
        "category": "trending",
        "assets": rows,
        # Named so nobody later mistakes this for a price-derived list.
        "basis": "coingecko_search_trending",
        "freshness": _freshness(board),
    }


def snapshot(category: str = "all", limit: int = 50) -> dict[str, Any]:
    """One round trip for the whole screen: strip, list, and freshness."""
    started = time.time()
    payload = trending() if category == "trending" else market_rows(category, limit)
    payload["global"] = global_metrics()
    # Regime and rotation come from breadth across the board that was already
    # fetched to draw the list — no second provider call, and no paid breadth
    # feed. They sit alongside the global strip rather than inside it so the
    # existing strip fields stay exactly where every client expects them.
    board = _board("all", 80)
    context = _intelligence_context(board)
    payload["regime"] = context.get("regime")
    payload["rotation"] = context.get("rotation")
    payload["categories"] = list(CATEGORIES)
    payload["ranges"] = list(market_data.HISTORY_RANGES)
    payload["rangeAliases"] = dict(market_data.HISTORY_RANGE_ALIASES)
    payload["elapsedMs"] = int((time.time() - started) * 1000)
    return payload


def asset_history(symbol: str, range_key: str = "24H") -> dict[str, Any]:
    """Real price history. Ranges are cached per (coin, range) by market_data."""
    return market_data.asset_history(symbol, range_key)


def search(query: str, limit: int = 25) -> dict[str, Any]:
    """Search the same board that supplies prices.

    Offering an asset the price engine has never heard of would let a user open
    a detail screen that can only ever read "Unavailable". Symbol is not a
    CoinGecko id, and resolution stays server-side in ``coingecko_client`` and
    the board — there is no second resolver.
    """
    needle = str(query or "").strip().lower()
    board = _board("all", 80)
    updated_at = board.get("updated_at")
    matches = []
    for row in board.get("markets") or []:
        symbol = str(row.get("symbol") or "").lower()
        name = str(row.get("name") or "").lower()
        coin_id = str(row.get("id") or "").lower()
        if not needle or needle in symbol or needle in name or needle in coin_id:
            asset = normalize_asset(row, updated_at)
            if asset:
                matches.append(asset)
    matches.sort(key=lambda a: (a.get("rank") if a.get("rank") is not None else 9999, a["symbol"]))
    results = matches[: max(1, min(int(limit or 25), 80))]
    _attach_intelligence(results, board)
    return {
        "query": query or "",
        "assets": results,
        "freshness": _freshness(board),
    }


def asset_intelligence(symbol: str, holding: dict[str, Any] | None = None) -> dict[str, Any]:
    """The full drill-down for the one asset a user has open.

    The deep layers cost at most one cached history call — ``asset_history`` is
    memoised per (symbol, range) — and reuse the board's hourly series when it
    already covers the window. That is the whole reason the expensive layers
    live behind a tap: a screen shows fifty rows and one open asset, so the work
    that cannot be shared is only ever done once.

    ``holding`` must be real portfolio data supplied by the caller. This
    function never guesses whether someone owns the asset; with no holding it
    returns the non-holder framing and says which one it used.
    """
    symbol = str(symbol or "").upper().strip()
    board = _board("all", 80)
    context = _intelligence_context(board)
    row = next((r for r in board.get("markets") or [] if str(r.get("symbol") or "").upper() == symbol), None)
    asset = normalize_asset(row, board.get("updated_at")) if row else {"symbol": symbol, "price": None}

    series = market_data.hourly_series(symbol)
    price_source = "coingecko board sparkline (7d hourly)"
    if len(series) < market_intelligence.MIN_POINTS_FOR_FULL_DEPTH:
        # The board could not supply a usable series — most often because we are
        # on the Coinbase fallback, which carries no sparkline at all. The 7D
        # chart is already cached for this screen, so reading it here adds no
        # provider call in the common case.
        history = market_data.asset_history(symbol, "7D") or {}
        points = [p.get("price") for p in history.get("points") or []]
        if len(points) > len(series):
            series = [p for p in points if isinstance(p, (int, float))]
            price_source = "coingecko 7d market chart"
    context["priceSource"] = price_source

    payload = market_intelligence.assess(
        asset, series, context,
        volume_series=_volume_history(symbol),
        holding=holding,
        depth="full",
    )
    payload["asset"] = asset
    payload["freshness"] = _freshness(board)
    payload["regime"] = context.get("regime")
    payload["rotation"] = context.get("rotation")
    return payload
