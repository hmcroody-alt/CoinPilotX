"""Merchant automation controller contract (Stage 6 Part 11).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400 with curated codes; recording a rule + signal; proposals report
is computed-on-read; rules/signals reports; evaluate runs. Curated codes only, never a
raw exception.

    python tests/business_os/test_merchant_api.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_mrchapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MERCHANT_AUTOMATION"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.merchant_automation import schema as asch  # noqa: E402
from services.business_os.merchant_automation import api  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).strftime(_FMT)


def setup_module(module=None):
    asch.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MERCHANT_AUTOMATION"] = "0"
    try:
        assert api.record_rule({})[0] == 404
        assert api.record_signal({})[0] == 404
        assert api.proposals_report("m")[0] == 404
        assert api.rules_report("m")[0] == 404
        assert api.signals_report("m")[0] == 404
        assert api.run_evaluate("m")[0] == 404
    finally:
        os.environ["BUSINESS_OS_MERCHANT_AUTOMATION"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_rule_missing_fields():
    st, body = api.record_rule({"merchant_id": "m", "signal_type": "stock"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_signal_missing_fields():
    st, body = api.record_signal({"merchant_id": "m", "subject_ref": "s"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_rule_bad_operator_curated():
    st, body = api.record_rule({"merchant_id": "m", "signal_type": "stock",
                                "operator": "between", "threshold": 5,
                                "action_type": "reorder"})
    assert st == 400 and body["code"] == "invalid_rule", body
    assert "operator" in body["error"]


def test_signal_bad_value_curated():
    st, body = api.record_signal({"merchant_id": "m", "subject_ref": "s",
                                  "signal_type": "stock", "value": "notnum"})
    assert st == 400 and body["code"] == "invalid_signal", body


def test_proposals_missing_merchant():
    st, body = api.proposals_report("")
    assert st == 400 and body["code"] == "missing_fields", body


# --- (c) record + compute-on-read proposals ---------------------------------
def test_proposals_computed_on_read():
    api.record_rule({"merchant_id": "M1", "signal_type": "stock", "operator": "lte",
                     "threshold": 5, "action_type": "reorder", "priority": 5})
    api.record_signal({"merchant_id": "M1", "subject_ref": "sku_low", "signal_type":
                       "stock", "value": 2, "observed_at": _ts(0)})
    api.record_signal({"merchant_id": "M1", "subject_ref": "sku_ok", "signal_type":
                       "stock", "value": 40, "observed_at": _ts(1)})
    st, body = api.proposals_report("M1")
    assert st == 200, body
    subs = {p["subject_ref"] for p in body["result"]["proposals"]}
    assert "sku_low" in subs, subs      # under threshold -> proposed
    assert "sku_ok" not in subs, subs   # above threshold -> excluded


# --- (d) rules + signals reports + evaluate ---------------------------------
def test_rules_and_signals_reports():
    st, body = api.rules_report("M1")
    assert st == 200 and any(r["action_type"] == "reorder"
                             for r in body["result"]["rules"]), body
    st2, b2 = api.signals_report("M1")
    assert st2 == 200 and any(s["subject_ref"] == "sku_low"
                              for s in b2["result"]["signals"]), b2


def test_evaluate_runs():
    st, body = api.run_evaluate("M1")
    assert st == 200 and "proposals" in body["result"], body
    st2, b2 = api.run_evaluate("")
    assert st2 == 400 and b2["code"] == "missing_fields", b2


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_rule_missing_fields,
        test_signal_missing_fields,
        test_rule_bad_operator_curated,
        test_signal_bad_value_curated,
        test_proposals_missing_merchant,
        test_proposals_computed_on_read,
        test_rules_and_signals_reports,
        test_evaluate_runs,
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
