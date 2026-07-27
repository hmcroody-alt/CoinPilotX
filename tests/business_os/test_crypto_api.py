"""Crypto controller contract (Stage 5 Part 5).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400; unauthenticated -> 401; a valid transaction records and a
portfolio read reflects P&L against an injected price lookup; alert create/list/
delete are user-scoped; sweep runs. Curated codes only, never a raw exception.

    python tests/business_os/test_crypto_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_cryptoapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_CRYPTO"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.crypto import schema as cs  # noqa: E402
from services.business_os.crypto import api  # noqa: E402


def setup_module(module=None):
    cs.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_CRYPTO"] = "0"
    try:
        assert api.record_transaction("u1", {})[0] == 404
        assert api.portfolio("u1")[0] == 404
        assert api.create_alert("u1", {})[0] == 404
        assert api.list_alerts("u1")[0] == 404
        assert api.run_sweep()[0] == 404
    finally:
        os.environ["BUSINESS_OS_CRYPTO"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_record_missing_fields():
    st, body = api.record_transaction("u1", {"symbol": "BTC"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_record_unauthenticated():
    st, body = api.record_transaction("", {"symbol": "BTC", "side": "buy",
                                           "quantity": "1", "unit_price_cents": 1})
    assert st == 401 and body["code"] == "unauthenticated", body


def test_record_bad_side_curated():
    st, body = api.record_transaction("u1", {"symbol": "BTC", "side": "hodl",
                                             "quantity": "1", "unit_price_cents": 1})
    assert st == 400 and body["code"] == "invalid_transaction", body
    assert "unknown side" in body["error"]


# --- (c) record + portfolio round-trip --------------------------------------
def test_record_then_portfolio():
    r = api.record_transaction("u2", {"symbol": "BTC", "side": "buy",
                                      "quantity": "1", "unit_price_cents": 5000000})
    assert r[0] == 200 and r[1]["result"]["recorded"] is True
    st, body = api.portfolio("u2", price_lookup=lambda s: 6000000)
    assert st == 200
    t = body["result"]["totals"]
    assert t["cost_basis_cents"] == 5000000
    assert t["market_value_cents"] == 6000000
    assert t["unrealized_pnl_cents"] == 1000000, t


# --- (d) alerts scoped CRUD -------------------------------------------------
def test_alert_crud_scoped():
    st, body = api.create_alert("u3", {"symbol": "eth", "comparator": "crosses_above",
                                       "threshold": "2000"})
    assert st == 200 and body["result"]["alert_id"], body
    aid = body["result"]["alert_id"]
    lst = api.list_alerts("u3")[1]["result"]
    assert any(a["alert_id"] == aid for a in lst)
    # another user cannot delete it
    st2, body2 = api.delete_alert("u4", aid)
    assert st2 == 404 and body2["code"] == "not_found", body2
    # owner can
    st3, body3 = api.delete_alert("u3", aid)
    assert st3 == 200 and body3["result"]["deactivated"] is True


def test_alert_bad_comparator_curated():
    st, body = api.create_alert("u3", {"symbol": "eth", "comparator": "sideways",
                                       "threshold": "1"})
    assert st == 400 and body["code"] == "invalid_alert", body


# --- (e) sweep --------------------------------------------------------------
def test_sweep_runs():
    api.create_alert("u5", {"symbol": "SOL", "comparator": "crosses_above",
                            "threshold": "100", "repeat_mode": "always"})
    prices = {"SOL": 5000}
    api.run_sweep(price_lookup=lambda s: prices.get(s))   # arm below
    prices["SOL"] = 12000
    st, body = api.run_sweep(price_lookup=lambda s: prices.get(s))
    assert st == 200 and body["result"]["fired_count"] >= 1, body


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_record_missing_fields,
        test_record_unauthenticated,
        test_record_bad_side_curated,
        test_record_then_portfolio,
        test_alert_crud_scoped,
        test_alert_bad_comparator_curated,
        test_sweep_runs,
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
