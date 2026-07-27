"""Performance schema (Stage 6).

Proves: the four canonical tables are created; ensure_schema is idempotent; UNIQUE
(source, external_ref) dedupes both input logs while NULL external_ref is exempt; the
UNIQUE (org_id, metric_key, window) summary key is exactly-once; and no legacy table is
touched (only the four business_os_perf_* tables exist).

    python tests/business_os/test_perf_schema.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_perfschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.performance import schema as sch  # noqa: E402


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def setup_module(module=None):
    sch.ensure_schema()


def test_tables_created():
    conn = db.connect()
    try:
        t = _tables(conn)
        for name in ("business_os_perf_samples",
                     "business_os_perf_targets",
                     "business_os_perf_summaries",
                     "business_os_perf_audit"):
            assert name in t, (name, t)
    finally:
        conn.close()


def test_idempotent():
    sch.ensure_schema()
    sch.ensure_schema()
    conn = db.connect()
    try:
        assert "business_os_perf_samples" in _tables(conn)
    finally:
        conn.close()


def test_sample_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_perf_samples "
            "(sample_id,org_id,metric_key,window,value,captured_at,source,external_ref,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("s1", "o1", "latency_ms", "", 100.0, "t", "feed", "SR1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_perf_samples "
                "(sample_id,org_id,metric_key,window,value,captured_at,source,"
                "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("s2", "o1", "latency_ms", "", 120.0, "t", "feed", "SR1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (source, external_ref) should be rejected"
        conn.execute(
            "INSERT INTO business_os_perf_samples "
            "(sample_id,org_id,metric_key,window,value,captured_at,source,external_ref,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("s3", "o1", "latency_ms", "", 130.0, "t", "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_perf_samples "
            "(sample_id,org_id,metric_key,window,value,captured_at,source,external_ref,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("s4", "o1", "latency_ms", "", 140.0, "t", "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_target_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_perf_targets "
            "(target_id,org_id,metric_key,direction,compare_stat,warn_threshold,"
            "breach_threshold,active,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", "o1", "latency_ms", "lower_is_better", "mean", 200.0, 500.0, 1,
             "feed", "TR1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_perf_targets "
                "(target_id,org_id,metric_key,direction,compare_stat,warn_threshold,"
                "breach_threshold,active,source,external_ref,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("t2", "o1", "latency_ms", "lower_is_better", "mean", 250.0, 600.0, 1,
                 "feed", "TR1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate target (source, external_ref) should be rejected"
        conn.execute(
            "INSERT INTO business_os_perf_targets "
            "(target_id,org_id,metric_key,direction,compare_stat,warn_threshold,"
            "breach_threshold,active,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("t3", "o1", "uptime_pct", "higher_is_better", "mean", 99.0, 95.0, 1,
             "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_perf_targets "
            "(target_id,org_id,metric_key,direction,compare_stat,warn_threshold,"
            "breach_threshold,active,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("t4", "o1", "uptime_pct", "higher_is_better", "mean", 99.5, 96.0, 1,
             "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_direction_check_enforced():
    conn = db.connect()
    try:
        bad = False
        try:
            conn.execute(
                "INSERT INTO business_os_perf_targets "
                "(target_id,org_id,metric_key,direction,compare_stat,warn_threshold,"
                "breach_threshold,active,source,external_ref,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("tbad", "o1", "m", "sideways", "mean", 1.0, 2.0, 1, "manual", None,
                 "t"))
            conn.commit()
        except Exception:
            bad = True
            conn.rollback()
        assert bad, "invalid direction should be rejected by the CHECK constraint"
    finally:
        conn.close()


def test_summary_key_exactly_once():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_perf_summaries "
            "(row_id,org_id,metric_key,window,count,min_value,max_value,mean_value,"
            "p50_value,p95_value,target_stat,status,rank,computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("r1", "oX", "latency_ms", "", 3, 1.0, 3.0, 2.0, 2.0, 3.0, 2.0, "ok", 1,
             "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_perf_summaries "
                "(row_id,org_id,metric_key,window,count,min_value,max_value,mean_value,"
                "p50_value,p95_value,target_stat,status,rank,computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("r2", "oX", "latency_ms", "", 3, 1.0, 3.0, 2.0, 2.0, 3.0, 2.0, "ok", 1,
                 "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (org_id, metric_key, window) should be rejected"
    finally:
        conn.close()


def test_legacy_untouched():
    conn = db.connect()
    try:
        perf_tables = {t for t in _tables(conn) if t.startswith("business_os_perf_")}
        assert perf_tables == {
            "business_os_perf_samples",
            "business_os_perf_targets",
            "business_os_perf_summaries",
            "business_os_perf_audit"}, perf_tables
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_tables_created,
        test_idempotent,
        test_sample_source_ref_dedupe_and_null_exempt,
        test_target_source_ref_dedupe_and_null_exempt,
        test_direction_check_enforced,
        test_summary_key_exactly_once,
        test_legacy_untouched,
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
