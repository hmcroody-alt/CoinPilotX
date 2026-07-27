"""Unified market/quote read layer (Stage 5 Part 3).

Proves the normalized contract without touching the network: an injected source
yields a normalized quote with integer-cent price; a missing symbol degrades to a
stale ok=False quote (never zero); a raising source is swallowed; the cache serves
a repeat within TTL; price_cents_lookup feeds the engine shape.

    python tests/business_os/test_crypto_market.py   # no pytest needed
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.crypto import market as mk  # noqa: E402


def _src(mapping):
    return lambda sym: mapping.get(sym)


def test_normalized_quote_cents():
    src = _src({"BTC": {"symbol": "BTC", "name": "Bitcoin",
                        "price": 60123.45, "change_24h": 1.7}})
    mk.clear_cache()
    q = mk.get_quote("btc", source=src, use_cache=False)
    assert q["ok"] is True
    assert q["price_cents"] == 6012345, q
    assert q["change_24h"] == 1.7
    assert q["stale"] is False


def test_missing_symbol_is_stale_not_zero():
    mk.clear_cache()
    q = mk.get_quote("ZZZ", source=_src({}), use_cache=False)
    assert q["ok"] is False and q["stale"] is True
    assert q["price_cents"] is None  # NOT 0 — unknown must stay unknown


def test_raising_source_degrades():
    def boom(sym):
        raise RuntimeError("upstream down")
    mk.clear_cache()
    q = mk.get_quote("BTC", source=boom, use_cache=False)
    assert q["ok"] is False and q["price_cents"] is None


def test_cache_serves_repeat():
    calls = {"n": 0}

    def counting(sym):
        calls["n"] += 1
        return {"symbol": sym, "price": 100.0}
    mk.clear_cache()
    a = mk.get_quote("ETH", source=counting, use_cache=True)
    b = mk.get_quote("ETH", source=counting, use_cache=True)
    assert a["price_cents"] == 10000 and b["price_cents"] == 10000
    assert calls["n"] == 1, "second read should hit cache"


def test_batch_and_lookup():
    src = _src({"BTC": {"symbol": "BTC", "price": 500.0},
                "ETH": {"symbol": "ETH", "price": 25.5}})
    mk.clear_cache()
    quotes = mk.get_quotes(["btc", "eth"], source=src, use_cache=False)
    assert quotes["BTC"]["price_cents"] == 50000
    assert quotes["ETH"]["price_cents"] == 2550
    lookup = mk.price_cents_lookup(source=src, use_cache=False)
    assert lookup("BTC") == 50000 and lookup("ETH") == 2550
    assert lookup("XXX") is None


def _run_standalone():
    tests = [
        test_normalized_quote_cents,
        test_missing_symbol_is_stale_not_zero,
        test_raising_source_degrades,
        test_cache_serves_repeat,
        test_batch_and_lookup,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
