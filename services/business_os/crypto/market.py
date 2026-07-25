"""Unified crypto market / quote read layer (Stage 5 Part 3).

The codebase has three overlapping market modules that all ultimately wrap
``market_data.live_market_board``:

* ``services/market_data.py``        — CoinGecko primary + Coinbase fallback, 60s cache
* ``services/market_service.py``     — thin passthrough
* ``services/live_market_service.py``— per-key cache + fear/greed + btc network + health

This module is the ONE canonical quote source the crypto-intelligence vertical
reads from. It does **not** modify or replace any of those three — it composes
them behind a single normalized contract and a short cache, so the P&L engine and
the alert sweeper have exactly one place to ask "what is X worth right now?"

Normalized quote (all callers depend only on this shape)::

    {
      "symbol": "BTC",
      "ok": True,                # False when no live price is available
      "price_cents": 6012345,    # integer cents, or None when unavailable
      "price": 60123.45,         # float dollars, provider-native, or None
      "change_24h": 1.7,         # percent, or None
      "source": "coingecko",
      "stale": False,
      "as_of": "2026-07-25T...",
    }

Money leaves this layer as **integer cents** so the engine never sees a float for
money. Read-only: nothing here places an order or moves funds.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional


# Injectable source function: symbol -> provider item dict (or None). Defaults to
# the existing market_data.get_symbol, imported lazily so importing this module
# never drags in the network stack during tests.
SourceFn = Callable[[str], Optional[dict]]

CACHE_SECONDS = 30
_CACHE: dict = {}  # symbol -> (expires_at, quote)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _default_source() -> SourceFn:
    from services import market_data  # lazy: avoids import cost / network at import
    return market_data.get_symbol


def _to_cents(price) -> Optional[int]:
    if price is None:
        return None
    try:
        from decimal import Decimal, ROUND_HALF_UP
        return int((Decimal(str(price)) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        return None


def _normalize(symbol: str, item: Optional[dict], *, source_hint=None) -> dict:
    symbol = (symbol or "").strip().upper()
    if not item:
        return {"symbol": symbol, "ok": False, "price_cents": None,
                "price": None, "change_24h": None,
                "source": source_hint or "unavailable", "stale": True,
                "as_of": _now_iso()}
    price = item.get("price")
    return {
        "symbol": symbol or (item.get("symbol") or "").upper(),
        "ok": price is not None,
        "price_cents": _to_cents(price),
        "price": price,
        "change_24h": item.get("change_24h"),
        "name": item.get("name"),
        "source": source_hint or item.get("source") or "coingecko",
        "stale": price is None,
        "as_of": _now_iso(),
    }


def get_quote(symbol: str, *, source: Optional[SourceFn] = None,
              use_cache: bool = True) -> dict:
    """Return a normalized quote for ``symbol``. Short-cached (``CACHE_SECONDS``)
    so a portfolio sweep over many holdings doesn't hammer the upstream board.

    ``source`` is injectable for tests; production uses ``market_data.get_symbol``.
    A source that raises or returns ``None`` degrades to an ``ok=False`` stale
    quote rather than propagating — callers must treat missing prices as "unknown",
    never as zero."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return _normalize("", None)
    now = time.time()
    if use_cache:
        hit = _CACHE.get(symbol)
        if hit and hit[0] > now:
            return dict(hit[1])
    src = source or _default_source()
    try:
        item = src(symbol)
    except Exception:
        item = None
    quote = _normalize(symbol, item)
    if use_cache and quote["ok"]:
        _CACHE[symbol] = (now + CACHE_SECONDS, dict(quote))
    return quote


def get_quotes(symbols: Iterable[str], *, source: Optional[SourceFn] = None,
               use_cache: bool = True) -> dict:
    """Batch: ``{SYMBOL: quote}`` for each requested symbol."""
    out: dict = {}
    src = source or _default_source()
    for s in symbols:
        out[(s or "").strip().upper()] = get_quote(
            s, source=src, use_cache=use_cache)
    return out


def price_cents_lookup(*, source: Optional[SourceFn] = None,
                       use_cache: bool = True) -> Callable[[str], Optional[int]]:
    """Return a ``symbol -> price_cents|None`` callable to hand to the P&L engine's
    ``portfolio_summary`` / alert sweeper. Missing prices come back as ``None``."""
    def _lookup(symbol: str) -> Optional[int]:
        return get_quote(symbol, source=source, use_cache=use_cache)["price_cents"]
    return _lookup


def clear_cache() -> None:
    _CACHE.clear()
