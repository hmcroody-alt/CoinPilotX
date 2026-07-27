"""Attribution controller contract (Stage 6 Part 3).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400; unauthenticated -> 401; a conversion records and auto-computes
credit against its path; a conversion's credit report is user-scoped (a stranger gets
404); campaign report and recompute run. Curated codes only, never a raw exception.

    python tests/business_os/test_attr_api.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_attrapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ATTRIBUTION"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.attribution import schema as asch  # noqa: E402
from services.business_os.attribution import api  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).strftime(_FMT)


def setup_module(module=None):
    asch.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_ATTRIBUTION"] = "0"
    try:
        assert api.record_touchpoint("u1", {})[0] == 404
        assert api.record_conversion("u1", {})[0] == 404
        assert api.conversion_report("u1", "c1")[0] == 404
        assert api.campaign_report()[0] == 404
        assert api.run_recompute("c1")[0] == 404
    finally:
        os.environ["BUSINESS_OS_ATTRIBUTION"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_touch_missing_fields():
    st, body = api.record_touchpoint("u1", {"channel": "ad"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_touch_unauthenticated():
    st, body = api.record_touchpoint("", {"channel": "ad", "touch_type": "click"})
    assert st == 401 and body["code"] == "unauthenticated", body


def test_touch_bad_type_curated():
    st, body = api.record_touchpoint("u1", {"channel": "ad", "touch_type": "warp"})
    assert st == 400 and body["code"] == "invalid_touchpoint", body
    assert "touch_type" in body["error"]


# --- (c) record conversion auto-attributes ----------------------------------
def test_conversion_auto_attributes():
    api.record_touchpoint("u2", {"channel": "ad", "touch_type": "impression",
                                 "campaign_ref": "cmp1", "occurred_at": _ts(0)})
    api.record_touchpoint("u2", {"channel": "email", "touch_type": "click",
                                 "campaign_ref": "cmp2", "occurred_at": _ts(1)})
    st, body = api.record_conversion("u2", {"conversion_type": "purchase",
                                            "value_cents": 8000,
                                            "occurred_at": _ts(100),
                                            "model": "last_touch"})
    assert st == 200, body
    res = body["result"]
    assert res["recorded"] is True
    attr = res["attribution"]
    assert attr["attributed"] is True and attr["total_credit_cents"] == 8000
    # last-touch -> the email click (campaign cmp2) holds all credit
    last = [t for t in attr["touchpoints"] if t["credit_cents"] == 8000]
    assert last and last[0]["campaign_ref"] == "cmp2", attr


def test_conversion_missing_fields():
    st, body = api.record_conversion("u2", {"conversion_type": "purchase"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_conversion_bad_value_curated():
    st, body = api.record_conversion("u2", {"conversion_type": "purchase",
                                            "value_cents": -5})
    assert st == 400 and body["code"] == "invalid_conversion", body


# --- (d) conversion report is user-scoped -----------------------------------
def test_conversion_report_scoped():
    api.record_touchpoint("u3", {"channel": "ad", "touch_type": "click",
                                 "occurred_at": _ts(0)})
    st, body = api.record_conversion("u3", {"conversion_type": "purchase",
                                            "value_cents": 500,
                                            "occurred_at": _ts(50)})
    cid = body["result"]["conversion_id"]
    # owner sees credits
    st1, b1 = api.conversion_report("u3", cid, "last_touch")
    assert st1 == 200 and len(b1["result"]["credits"]) == 1, b1
    # a stranger cannot
    st2, b2 = api.conversion_report("u_other", cid, "last_touch")
    assert st2 == 404 and b2["code"] == "not_found", b2
    # bad model rejected
    st3, b3 = api.conversion_report("u3", cid, "psychic")
    assert st3 == 400 and b3["code"] == "invalid_model", b3


# --- (e) campaign report + recompute ----------------------------------------
def test_campaign_report_and_recompute():
    st, body = api.campaign_report("last_touch")
    assert st == 200 and "rows" in body["result"], body
    # recompute a known conversion under all models
    api.record_touchpoint("u4", {"channel": "ad", "touch_type": "click",
                                 "occurred_at": _ts(0)})
    st2, b2 = api.record_conversion("u4", {"conversion_type": "purchase",
                                           "value_cents": 300, "occurred_at": _ts(9)})
    cid = b2["result"]["conversion_id"]
    st3, b3 = api.run_recompute(cid, ["linear", "first_touch"])
    assert st3 == 200 and set(b3["result"]["models"]) == {"linear", "first_touch"}, b3
    # unknown conversion -> not_found
    st4, b4 = api.run_recompute("does-not-exist")
    assert st4 == 404 and b4["code"] == "not_found", b4


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_touch_missing_fields,
        test_touch_unauthenticated,
        test_touch_bad_type_curated,
        test_conversion_auto_attributes,
        test_conversion_missing_fields,
        test_conversion_bad_value_curated,
        test_conversion_report_scoped,
        test_campaign_report_and_recompute,
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
