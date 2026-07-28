"""Business OS — Section 7 (Insights) HTTP controller, exercised DIRECTLY.

Proves the framework-agnostic ``(status_code, body)`` controller over the unified
insights service:

  * DARK when BUSINESS_OS_INSIGHTS is off — every handler returns 404 not_found;
  * a business member reads the unified overview (200) and each sub-report (200);
  * access is enforced by the service — a stranger gets 404, existence not leaked;
  * invalid attribution model/scope surface a curated 400 code.

    python tests/business_os/test_insights_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_insights_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_INSIGHTS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.insights import schema as ins_schema  # noqa: E402
from services.business_os.insights import api  # noqa: E402
from services.business_os.performance import engine as perf  # noqa: E402


OWNER = 950
STAFF = 951
STRANGER = 953


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    ins_schema.ensure_schema()


def _business():
    biz = biz_svc.create_business(OWNER, {"display_name": "Acme Co"}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    return bid


# ---------------------------------------------------------------------------
def test_dark_when_disabled_all_handlers_404():
    os.environ["BUSINESS_OS_INSIGHTS"] = ""
    try:
        calls = [
            lambda: api.overview(OWNER, "b"),
            lambda: api.performance_summary(OWNER, "b"),
            lambda: api.attribution_report(OWNER, "b"),
            lambda: api.recommendations_popularity(OWNER, "b"),
        ]
        for fn in calls:
            status, body = fn()
            assert status == 404, (fn, status, body)
            assert body["ok"] is False and body["code"] == "not_found", body
    finally:
        os.environ["BUSINESS_OS_INSIGHTS"] = "on"


def test_overview_200_unified():
    bid = _business()
    perf.record_sample(bid, "orders", 3, window="1d")
    perf.summarize_org(bid)
    status, body = api.overview(OWNER, bid)
    assert status == 200 and body["ok"] is True
    ins = body["insights"]
    assert set(ins.keys()) >= {"business_id", "performance", "attribution",
                               "recommendations"}
    assert ins["attribution"]["model"] == "last_touch"


def test_performance_summary_200():
    bid = _business()
    perf.record_sample(bid, "latency", 200, window="7d")
    perf.summarize_org(bid)
    status, body = api.performance_summary(STAFF, bid)
    assert status == 200
    assert body["performance"]["business_id"] == str(bid)


def test_attribution_report_200_and_scope_channel():
    bid = _business()
    status, body = api.attribution_report(OWNER, bid, scope="channel")
    assert status == 200
    assert body["attribution"]["scope"] == "channel"


def test_attribution_invalid_model_400():
    bid = _business()
    status, body = api.attribution_report(OWNER, bid, model="bogus")
    assert status == 400 and body["code"] == "invalid_model", body


def test_recommendations_popularity_200():
    bid = _business()
    status, body = api.recommendations_popularity(STAFF, bid, limit=10)
    assert status == 200
    assert "popularity" in body["recommendations"]


def test_stranger_404_not_leaked():
    bid = _business()
    for fn in (
        lambda: api.overview(STRANGER, bid),
        lambda: api.performance_summary(STRANGER, bid),
        lambda: api.attribution_report(STRANGER, bid),
        lambda: api.recommendations_popularity(STRANGER, bid),
    ):
        status, body = fn()
        assert status == 404 and body["code"] == "not_found", body


def test_missing_business_404():
    status, body = api.overview(OWNER, "nope-nope")
    assert status == 404 and body["code"] == "not_found", body


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled_all_handlers_404,
        test_overview_200_unified,
        test_performance_summary_200,
        test_attribution_report_200_and_scope_channel,
        test_attribution_invalid_model_400,
        test_recommendations_popularity_200,
        test_stranger_404_not_leaked,
        test_missing_business_404,
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
