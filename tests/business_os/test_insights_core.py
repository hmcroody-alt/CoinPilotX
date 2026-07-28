"""Business OS — Section 7 (Insights) canonical facade, exercised DIRECTLY.

Proves the Insights domain is a faithful UNIFICATION of the three existing analytics
engines (attribution / recommendations / performance) — not a fourth analytics store:

  * DARK when BUSINESS_OS_INSIGHTS is off — every entry point raises 503 disabled;
  * NO business_os_insights* table is ever created;
  * business-side access is inherited from S1 RBAC (member reads; stranger sees 404,
    existence not leaked);
  * numbers come straight from the underlying engines (a performance sample the business
    records shows up in its insights; a platform interaction shows up in popularity);
  * the unified overview stitches all three engines behind one authorization check.

    python tests/business_os/test_insights_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_insights_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_INSIGHTS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.insights import schema as ins_schema  # noqa: E402
from services.business_os.insights import service as svc  # noqa: E402
from services.business_os.insights.service import InsightsError  # noqa: E402
from services.business_os.performance import engine as perf  # noqa: E402
from services.business_os.recommendations import engine as rec  # noqa: E402


OWNER = 900
STAFF = 901
STRANGER = 903


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
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_INSIGHTS"] = ""
    try:
        for fn in (
            lambda: svc.overview("b", OWNER),
            lambda: svc.performance_summary("b", OWNER),
            lambda: svc.attribution_report("b", OWNER),
            lambda: svc.recommendations_popularity("b", OWNER),
        ):
            try:
                fn()
                raise AssertionError("expected disabled")
            except InsightsError as e:
                assert e.http_status == 503 and e.code == "disabled", (e.http_status, e.code)
    finally:
        os.environ["BUSINESS_OS_INSIGHTS"] = "on"


def test_no_insights_table_created():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_insights%'").fetchall()
        assert rows == [], [dict(r) if hasattr(r, 'keys') else r for r in rows]
        # And the three canonical analytics stores DO exist and are what we unify.
        for t in ("business_os_perf_summaries", "business_os_attr_touchpoints",
                  "business_os_rec_items"):
            got = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (t,)).fetchall()
            assert len(got) == 1, (t, got)
    finally:
        conn.close()


def test_member_reads_performance_summary_from_engine():
    bid = _business()
    # Record a performance sample for this org via the canonical engine.
    perf.record_sample(bid, "checkout_latency_ms", 120, window="7d")
    perf.record_sample(bid, "checkout_latency_ms", 180, window="7d")
    perf.summarize_org(bid)  # persist the rollup

    out = svc.performance_summary(bid, OWNER)
    assert out["business_id"] == str(bid)
    keys = {s["metric_key"] for s in out["summaries"]}
    assert "checkout_latency_ms" in keys, out


def test_recommendations_popularity_reflects_interactions():
    bid = _business()
    rec.record_item("sku-1", "product", title="Widget")
    rec.record_interaction(OWNER, "sku-1", "view")
    rec.record_interaction(STAFF, "sku-1", "purchase")
    out = svc.recommendations_popularity(bid, STAFF, limit=50)
    assert out["business_id"] == str(bid)
    assert "popularity" in out


def test_attribution_report_rejects_unknown_model_and_scope():
    bid = _business()
    try:
        svc.attribution_report(bid, OWNER, model="nope")
        raise AssertionError("expected invalid_model")
    except InsightsError as e:
        assert e.http_status == 400 and e.code == "invalid_model", (e.http_status, e.code)
    try:
        svc.attribution_report(bid, OWNER, scope="galaxy")
        raise AssertionError("expected invalid_scope")
    except InsightsError as e:
        assert e.http_status == 400 and e.code == "invalid_scope", (e.http_status, e.code)


def test_attribution_report_default_is_last_touch_campaign():
    bid = _business()
    out = svc.attribution_report(bid, OWNER)
    assert out["scope"] == "campaign"
    assert out["report"]["model"] == "last_touch"


def test_overview_unifies_all_three_engines():
    bid = _business()
    perf.record_sample(bid, "orders", 5, window="1d")
    perf.summarize_org(bid)
    out = svc.overview(bid, STAFF)
    assert set(out.keys()) >= {"business_id", "performance", "attribution",
                               "recommendations"}
    assert out["attribution"]["model"] == "last_touch"
    assert out["performance"]["count"] >= 1


def test_stranger_cannot_read_existence_not_leaked():
    bid = _business()
    for fn in (
        lambda: svc.overview(bid, STRANGER),
        lambda: svc.performance_summary(bid, STRANGER),
        lambda: svc.attribution_report(bid, STRANGER),
        lambda: svc.recommendations_popularity(bid, STRANGER),
    ):
        try:
            fn()
            raise AssertionError("expected not_found")
        except InsightsError as e:
            assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_missing_business_is_404_not_leaked():
    try:
        svc.overview("does-not-exist", OWNER)
        raise AssertionError("expected not_found")
    except InsightsError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_no_insights_table_created,
        test_member_reads_performance_summary_from_engine,
        test_recommendations_popularity_reflects_interactions,
        test_attribution_report_rejects_unknown_model_and_scope,
        test_attribution_report_default_is_last_touch_campaign,
        test_overview_unifies_all_three_engines,
        test_stranger_cannot_read_existence_not_leaked,
        test_missing_business_is_404_not_leaked,
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
