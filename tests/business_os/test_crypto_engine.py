"""Crypto cost-basis / P&L engine (Stage 5 Part 2).

Proves: buys open lots; FIFO sells realize (proceeds - oldest cost); AVERAGE
blends cost; fees fold into cost basis / net proceeds; unrealized P&L is computed
against a live price; oversell is rejected; a replayed (source, external_ref) is a
no-op; decimal quantities survive (no float drift on 8-dp amounts).

    python tests/business_os/test_crypto_engine.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_cryptoeng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.crypto import schema as cs  # noqa: E402
from services.business_os.crypto import engine as ce  # noqa: E402


def setup_module(module=None):
    cs.ensure_schema()


# --- (a) single buy -> holding reflects qty + all-in cost -------------------
def test_buy_opens_lot():
    r = ce.record_transaction("u1", "btc", "buy", "0.5", 6000000, fee_cents=1000)
    assert r["recorded"] is True
    h = r["holding"]
    assert h["quantity"] == "0.5"
    # cost basis = 0.5 * (6000000 + 1000/0.5=2000) => unit 6002000 * 0.5 = 3001000
    assert h["cost_basis_cents"] == 3001000, h
    assert h["realized_pnl_cents"] == 0


# --- (b) FIFO sell realizes against oldest lot ------------------------------
def test_fifo_realized():
    # two buys at different prices, then sell 1 unit -> consumes first lot fully.
    ce.record_transaction("u2", "eth", "buy", "1", 200000)   # lot A @2000.00
    ce.record_transaction("u2", "eth", "buy", "1", 300000)   # lot B @3000.00
    r = ce.record_transaction("u2", "eth", "sell", "1", 350000)  # sell @3500.00
    # FIFO: consumes lot A (cost 200000); proceeds 350000 => realized +150000
    assert r["realized_pnl_cents"] == 150000, r
    h = r["holding"]
    assert h["quantity"] == "1"           # lot B remains
    assert h["cost_basis_cents"] == 300000  # only lot B left
    assert h["realized_pnl_cents"] == 150000


# --- (c) AVERAGE method blends cost -----------------------------------------
def test_average_realized():
    ce.record_transaction("u3", "sol", "buy", "2", 10000, method="average")  # @100
    ce.record_transaction("u3", "sol", "buy", "2", 20000, method="average")  # @200
    # avg cost = (2*10000 + 2*20000)/4 = 15000/unit
    r = ce.record_transaction("u3", "sol", "sell", "2", 25000, method="average")
    # consumed cost 2*15000=30000; proceeds 2*25000=50000 => realized +20000
    assert r["realized_pnl_cents"] == 20000, r
    h = r["holding"]
    assert h["quantity"] == "2"
    assert h["cost_basis_cents"] == 30000  # remaining 2 units @ avg 15000


# --- (d) unrealized P&L against a live price --------------------------------
def test_unrealized():
    ce.record_transaction("u4", "btc", "buy", "1", 5000000)  # cost 50k.00
    h = ce.get_holding("u4", "btc")
    u = ce.unrealized_for_holding(h, 6000000)  # price 60k.00
    assert u["market_value_cents"] == 6000000
    assert u["unrealized_pnl_cents"] == 1000000  # +10k.00
    assert u["total_pnl_cents"] == 1000000


# --- (e) oversell rejected --------------------------------------------------
def test_oversell_rejected():
    ce.record_transaction("u5", "doge", "buy", "10", 5)
    try:
        ce.record_transaction("u5", "doge", "sell", "11", 6)
    except ce.CryptoEngineError as e:
        assert "oversell" in str(e), e
        return
    raise AssertionError("expected oversell rejection")


# --- (f) idempotent replay --------------------------------------------------
def test_idempotent_replay():
    r1 = ce.record_transaction("u6", "btc", "buy", "1", 5000000,
                               source="coinbase", external_ref="cb-1")
    r2 = ce.record_transaction("u6", "btc", "buy", "1", 5000000,
                               source="coinbase", external_ref="cb-1")
    assert r1["recorded"] is True and r2["recorded"] is False
    assert r2["duplicate"] is True
    h = ce.get_holding("u6", "btc")
    assert h["quantity"] == "1"  # replay did not double the position


# --- (g) decimal precision survives (8 dp, no float drift) ------------------
def test_decimal_precision():
    ce.record_transaction("u7", "btc", "buy", "0.00000001", 5000000000000)
    h = ce.get_holding("u7", "btc")
    assert h["quantity"] == "0.00000001", h


# --- (h) portfolio summary aggregates with a price lookup -------------------
def test_portfolio_summary():
    ce.record_transaction("u8", "btc", "buy", "1", 5000000)
    ce.record_transaction("u8", "eth", "buy", "2", 200000)
    prices = {"BTC": 6000000, "ETH": 250000}
    s = ce.portfolio_summary("u8", price_lookup=lambda sym: prices.get(sym))
    t = s["totals"]
    # cost = 5000000 + 400000 = 5400000; value = 6000000 + 500000 = 6500000
    assert t["cost_basis_cents"] == 5400000, t
    assert t["market_value_cents"] == 6500000, t
    assert t["unrealized_pnl_cents"] == 1100000, t
    assert t["symbols"] == 2 and t["priced_symbols"] == 2


# --- (i) missing price -> contributes cost but no value ---------------------
def test_summary_missing_price():
    ce.record_transaction("u9", "xmr", "buy", "1", 15000)
    s = ce.portfolio_summary("u9", price_lookup=lambda sym: None)
    t = s["totals"]
    assert t["cost_basis_cents"] == 15000
    assert t["market_value_cents"] == 0 and t["priced_symbols"] == 0


def _run_standalone():
    setup_module()
    tests = [
        test_buy_opens_lot,
        test_fifo_realized,
        test_average_realized,
        test_unrealized,
        test_oversell_rejected,
        test_idempotent_replay,
        test_decimal_precision,
        test_portfolio_summary,
        test_summary_missing_price,
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
