"""Performance engine (Stage 6).

Proves the deterministic summary projection: ingest is idempotent on (source,
external_ref); non-numeric / non-finite / empty inputs and bad enums are curated; the
rollup stats (count/min/max/mean/p50/p95) are computed deterministically; windows are
separate cells; status labels honor direction (lower_is_better / higher_is_better) and the
chosen compare_stat; an untargeted cell is 'none'; the newest active target wins per metric;
summaries rank deterministically (breach < warn < ok < none, then metric asc, then window
asc); the status rollup is correct; recompute is a deterministic idempotent replace (one row
per cell); and nothing beyond the four canonical tables is created (nothing renders).

    python tests/business_os/test_perf_engine.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_perfeng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.performance import schema as sch  # noqa: E402
from services.business_os.performance import engine as eng  # noqa: E402


def setup_module(module=None):
    sch.ensure_schema()


def _cell(out, metric, window=""):
    for s in out["summaries"]:
        if s["metric_key"] == metric and s["window"] == window:
            return s
    return None


def test_sample_and_target_dedupe():
    s1 = eng.record_sample("oA", "latency_ms", 100, source="feed", external_ref="S1")
    s2 = eng.record_sample("oA", "latency_ms", 120, source="feed", external_ref="S1")
    assert s1["recorded"] is True and s2["deduped"] is True, (s1, s2)
    t1 = eng.record_target("oA", "latency_ms", warn_threshold=200, breach_threshold=500,
                           source="feed", external_ref="T1")
    t2 = eng.record_target("oA", "latency_ms", warn_threshold=250, breach_threshold=600,
                           source="feed", external_ref="T1")
    assert t1["recorded"] is True and t2["deduped"] is True, (t1, t2)


def test_bad_input_curated():
    cases = [
        lambda: eng.record_sample("oB", "", 1),
        lambda: eng.record_sample("oB", "m", "not-a-number"),
        lambda: eng.record_sample("oB", "m", float("inf")),
        lambda: eng.record_sample("oB", "m", None),
        lambda: eng.record_target("oB", "m", direction="sideways", warn_threshold=1),
        lambda: eng.record_target("oB", "m", compare_stat="mode", warn_threshold=1),
        lambda: eng.record_target("oB", "m"),  # no thresholds
    ]
    for fn in cases:
        raised = False
        try:
            fn()
        except eng.PerformanceError:
            raised = True
        assert raised, "invalid input should be rejected"


def test_rollup_stats():
    for v in (10, 20, 30):
        eng.record_sample("oC", "m", v)
    out = eng.summarize_org("oC")
    c = _cell(out, "m")
    assert c["count"] == 3 and c["min"] == 10.0 and c["max"] == 30.0, c
    assert c["mean"] == 20.0 and c["p50"] == 20.0, c
    # p95 linear-interp over [10,20,30]: pos=1.9 -> 20 + (30-20)*0.9 = 29.0
    assert c["p95"] == 29.0, c
    assert c["status"] == "none" and c["target_stat"] is None, c


def test_windows_are_separate_cells():
    eng.record_sample("oW", "lat", 100, window="us")
    eng.record_sample("oW", "lat", 200, window="eu")
    out = eng.summarize_org("oW")
    assert _cell(out, "lat", "us")["mean"] == 100.0, out
    assert _cell(out, "lat", "eu")["mean"] == 200.0, out
    assert _cell(out, "lat", "us")["count"] == 1, out


def test_status_lower_is_better():
    eng.record_target("oL", "lat", direction="lower_is_better", compare_stat="mean",
                      warn_threshold=200, breach_threshold=500)
    eng.record_sample("oL", "lat", 100, window="a")   # ok
    eng.record_sample("oL", "lat", 300, window="b")   # warn
    eng.record_sample("oL", "lat", 600, window="c")   # breach
    out = eng.summarize_org("oL")
    assert _cell(out, "lat", "a")["status"] == "ok", out
    assert _cell(out, "lat", "b")["status"] == "warn", out
    assert _cell(out, "lat", "c")["status"] == "breach", out
    assert _cell(out, "lat", "c")["target_stat"] == 600.0, out


def test_status_higher_is_better():
    eng.record_target("oH", "uptime", direction="higher_is_better", compare_stat="mean",
                      warn_threshold=99, breach_threshold=95)
    eng.record_sample("oH", "uptime", 99.9, window="a")  # ok
    eng.record_sample("oH", "uptime", 98.0, window="b")  # warn
    eng.record_sample("oH", "uptime", 90.0, window="c")  # breach
    out = eng.summarize_org("oH")
    assert _cell(out, "uptime", "a")["status"] == "ok", out
    assert _cell(out, "uptime", "b")["status"] == "warn", out
    assert _cell(out, "uptime", "c")["status"] == "breach", out


def test_compare_stat_selects_field():
    # p95 breaches while mean stays healthy -> compare_stat must drive the label.
    eng.record_target("oP", "lat", direction="lower_is_better", compare_stat="p95",
                      breach_threshold=500)
    for v in (100, 100, 100, 100, 1000):
        eng.record_sample("oP", "lat", v)
    out = eng.summarize_org("oP")
    c = _cell(out, "lat")
    assert c["mean"] == 280.0, c            # mean alone would be ok
    assert c["p95"] == 820.0, c            # 100 + (1000-100)*0.8
    assert c["status"] == "breach", c
    assert c["target_stat"] == 820.0, c


def test_no_target_is_none():
    eng.record_sample("oN", "throughput", 42)
    out = eng.summarize_org("oN")
    c = _cell(out, "throughput")
    assert c["status"] == "none" and c["target_stat"] is None, c


def test_newest_target_wins():
    eng.record_target("oG", "lat", direction="lower_is_better", compare_stat="mean",
                      warn_threshold=200, breach_threshold=500)
    eng.record_target("oG", "lat", direction="lower_is_better", compare_stat="mean",
                      warn_threshold=50, breach_threshold=500)  # stricter, newer
    eng.record_sample("oG", "lat", 100)
    out = eng.summarize_org("oG")
    # under the newest target (warn 50), mean 100 >= 50 -> warn, not ok.
    assert _cell(out, "lat")["status"] == "warn", out


def test_deterministic_ordering():
    eng.record_target("oD", "breachm", direction="lower_is_better", breach_threshold=10)
    eng.record_target("oD", "warnm", direction="lower_is_better", warn_threshold=10,
                      breach_threshold=100)
    eng.record_target("oD", "okm", direction="lower_is_better", warn_threshold=100)
    eng.record_sample("oD", "breachm", 50)   # breach
    eng.record_sample("oD", "warnm", 50)     # warn
    eng.record_sample("oD", "okm", 5)        # ok
    eng.record_sample("oD", "nonem", 1)      # none (no target)
    out = eng.summarize_org("oD")
    order = [eng._STATUS_ORDER[s["status"]] for s in out["summaries"]]
    assert order == sorted(order), order
    assert out["summaries"][0]["status"] == "breach", out
    assert out["summaries"][-1]["status"] == "none", out
    ranks = [s["rank"] for s in out["summaries"]]
    assert ranks == list(range(1, len(ranks) + 1)), ranks


def test_status_rollup():
    eng.record_target("oSR", "breachm", direction="lower_is_better", breach_threshold=10)
    eng.record_target("oSR", "okm", direction="lower_is_better", warn_threshold=100)
    eng.record_sample("oSR", "breachm", 50)
    eng.record_sample("oSR", "okm", 5)
    eng.record_sample("oSR", "nonem", 1)
    out = eng.summarize_org("oSR")
    roll = {r["status"]: r["count"] for r in out["status_rollup"]}
    assert roll["breach"] == 1 and roll["ok"] == 1 and roll["none"] == 1, roll
    assert roll["warn"] == 0, roll


def test_recompute_idempotent_replace():
    for v in (10, 20, 30):
        eng.record_sample("oR", "m", v)
    first = eng.summarize_org("oR")
    second = eng.summarize_org("oR")
    assert first["summaries"] == second["summaries"], (first, second)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT metric_key, window, COUNT(*) c FROM business_os_perf_summaries "
            "WHERE org_id = ? GROUP BY metric_key, window", ("oR",)).fetchall()
        for r in rows:
            assert dict(r)["c"] == 1, dict(r)
    finally:
        conn.close()


def test_no_side_effects():
    eng.record_sample("oNS", "m", 1)
    eng.summarize_org("oNS")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_perf_%'").fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "business_os_perf_samples",
            "business_os_perf_targets",
            "business_os_perf_summaries",
            "business_os_perf_audit"}, names
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_sample_and_target_dedupe,
        test_bad_input_curated,
        test_rollup_stats,
        test_windows_are_separate_cells,
        test_status_lower_is_better,
        test_status_higher_is_better,
        test_compare_stat_selects_field,
        test_no_target_is_none,
        test_newest_target_wins,
        test_deterministic_ordering,
        test_status_rollup,
        test_recompute_idempotent_replace,
        test_no_side_effects,
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
