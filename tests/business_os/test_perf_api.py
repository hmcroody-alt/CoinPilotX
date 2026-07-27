"""Performance controller contract (Stage 6).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400 with curated codes; recording a sample + target; the summaries report
is computed-on-read with a status rollup; targets/samples reports; summarize runs. Curated
codes only, never a raw exception.

    python tests/business_os/test_perf_api.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_perfapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_PERFORMANCE"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.performance import schema as sch  # noqa: E402
from services.business_os.performance import api  # noqa: E402


def setup_module(module=None):
    sch.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_PERFORMANCE"] = "0"
    try:
        assert api.record_sample({})[0] == 404
        assert api.record_target({})[0] == 404
        assert api.summaries_report("o")[0] == 404
        assert api.targets_report("o")[0] == 404
        assert api.samples_report("o")[0] == 404
        assert api.run_summarize("o")[0] == 404
    finally:
        os.environ["BUSINESS_OS_PERFORMANCE"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_sample_missing_fields():
    st, body = api.record_sample({"org_id": "o", "metric_key": "m"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_sample_bad_curated():
    st, body = api.record_sample({"org_id": "o", "metric_key": "m", "value": "nan-ish"})
    assert st == 400 and body["code"] == "invalid_sample", body


def test_target_missing_fields():
    st, body = api.record_target({"org_id": "o"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_target_bad_curated():
    st, body = api.record_target({"org_id": "o", "metric_key": "m",
                                  "direction": "sideways", "warn_threshold": 1})
    assert st == 400 and body["code"] == "invalid_target", body


def test_target_no_threshold_curated():
    st, body = api.record_target({"org_id": "o", "metric_key": "m"})
    assert st == 400 and body["code"] == "invalid_target", body


def test_summaries_missing_org():
    st, body = api.summaries_report("")
    assert st == 400 and body["code"] == "missing_fields", body


# --- (c) record + compute-on-read summaries ---------------------------------
def test_summaries_computed_on_read():
    api.record_target({"org_id": "O1", "metric_key": "lat",
                       "direction": "lower_is_better", "compare_stat": "mean",
                       "warn_threshold": 200, "breach_threshold": 500})
    api.record_sample({"org_id": "O1", "metric_key": "lat", "value": 600,
                       "window": "slow"})   # breach
    api.record_sample({"org_id": "O1", "metric_key": "lat", "value": 100,
                       "window": "fast"})   # ok
    st, body = api.summaries_report("O1")
    assert st == 200, body
    summaries = body["result"]["summaries"]
    by = {(s["metric_key"], s["window"]): s for s in summaries}
    assert by[("lat", "slow")]["status"] == "breach", by
    assert by[("lat", "fast")]["status"] == "ok", by
    # breach ranks ahead of ok.
    assert summaries[0]["status"] == "breach", summaries
    roll = {r["status"]: r["count"] for r in body["result"]["status_rollup"]}
    assert roll["breach"] == 1 and roll["ok"] == 1, roll


# --- (d) targets + samples reports + summarize ------------------------------
def test_targets_and_samples_reports():
    st, body = api.targets_report("O1")
    assert st == 200 and any(t["metric_key"] == "lat"
                             for t in body["result"]["targets"]), body
    st2, b2 = api.samples_report("O1")
    assert st2 == 200 and any(s["metric_key"] == "lat"
                              for s in b2["result"]["samples"]), b2


def test_summarize_runs():
    st, body = api.run_summarize("O1")
    assert st == 200 and "summaries" in body["result"], body
    st2, b2 = api.run_summarize("")
    assert st2 == 400 and b2["code"] == "missing_fields", b2


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_sample_missing_fields,
        test_sample_bad_curated,
        test_target_missing_fields,
        test_target_bad_curated,
        test_target_no_threshold_curated,
        test_summaries_missing_org,
        test_summaries_computed_on_read,
        test_targets_and_samples_reports,
        test_summarize_runs,
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
