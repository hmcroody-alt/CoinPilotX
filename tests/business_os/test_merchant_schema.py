"""Merchant automation schema (Stage 6 Part 9).

Proves the canonical business_os_merchant_* surface is created idempotently, the CHECK
on operator is enforced, the (source, external_ref) dedupe indexes exist (NULL exempt)
on rules and signals, the (merchant_id, rule_id, subject_ref) projection key is
exactly-once, and no legacy table is touched.

    python tests/business_os/test_merchant_schema.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_mrchschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.merchant_automation import schema as asch  # noqa: E402

_TABLES = ("business_os_merchant_rules", "business_os_merchant_signals",
           "business_os_merchant_proposals", "business_os_merchant_audit")


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def setup_module(module=None):
    asch.ensure_schema()


def test_tables_created():
    conn = db.connect()
    try:
        names = _tables(conn)
        for t in _TABLES:
            assert t in names, t
    finally:
        conn.close()


def test_idempotent():
    asch.ensure_schema()
    asch.ensure_schema()  # second call must not raise
    conn = db.connect()
    try:
        assert all(t in _tables(conn) for t in _TABLES)
    finally:
        conn.close()


def test_rule_columns():
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(business_os_merchant_rules)").fetchall()}
        for c in ("rule_id", "merchant_id", "name", "signal_type", "operator",
                  "threshold", "action_type", "active", "priority", "source",
                  "external_ref", "created_at"):
            assert c in cols, c
    finally:
        conn.close()


def test_signal_columns():
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(business_os_merchant_signals)").fetchall()}
        for c in ("signal_id", "merchant_id", "subject_ref", "signal_type", "value",
                  "observed_at", "source", "external_ref", "created_at"):
            assert c in cols, c
    finally:
        conn.close()


def test_proposal_columns():
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(business_os_merchant_proposals)").fetchall()}
        for c in ("proposal_id", "merchant_id", "rule_id", "subject_ref",
                  "signal_type", "action_type", "operator", "threshold",
                  "observed_value", "priority", "rank", "reason", "computed_at"):
            assert c in cols, c
    finally:
        conn.close()


def test_operator_check_enforced():
    conn = db.connect()
    try:
        raised = False
        try:
            conn.execute(
                "INSERT INTO business_os_merchant_rules "
                "(rule_id,merchant_id,name,signal_type,operator,threshold,action_type,"
                "active,priority,source,external_ref,meta_json,created_at) "
                "VALUES ('x','m',NULL,'stock','between','5','reorder',1,0,'manual',"
                "NULL,NULL,'t')")
            conn.commit()
            raised = False
        except Exception:
            raised = True
        assert raised, "CHECK on operator should reject 'between'"
    finally:
        conn.rollback()
        conn.close()


def test_rule_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_merchant_rules "
            "(rule_id,merchant_id,name,signal_type,operator,threshold,action_type,"
            "active,priority,source,external_ref,meta_json,created_at) "
            "VALUES ('a','m',NULL,'stock','lte','5','reorder',1,0,'feed','e1',NULL,'t')")
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_merchant_rules "
                "(rule_id,merchant_id,name,signal_type,operator,threshold,action_type,"
                "active,priority,source,external_ref,meta_json,created_at) "
                "VALUES ('b','m',NULL,'stock','lte','5','reorder',1,0,'feed','e1',"
                "NULL,'t')")
            conn.commit()
        except Exception:
            dup = True
        assert dup, "(source, external_ref) must be unique on rules"
        conn.rollback()
        # NULL external_ref exempt: two manual rules allowed
        conn.execute(
            "INSERT INTO business_os_merchant_rules "
            "(rule_id,merchant_id,name,signal_type,operator,threshold,action_type,"
            "active,priority,source,external_ref,meta_json,created_at) "
            "VALUES ('c','m',NULL,'stock','lte','5','reorder',1,0,'manual',NULL,"
            "NULL,'t')")
        conn.execute(
            "INSERT INTO business_os_merchant_rules "
            "(rule_id,merchant_id,name,signal_type,operator,threshold,action_type,"
            "active,priority,source,external_ref,meta_json,created_at) "
            "VALUES ('d','m',NULL,'stock','lte','5','reorder',1,0,'manual',NULL,"
            "NULL,'t')")
        conn.commit()
    finally:
        conn.close()


def test_signal_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_merchant_signals "
            "(signal_id,merchant_id,subject_ref,signal_type,value,observed_at,"
            "source,external_ref,meta_json,created_at) "
            "VALUES ('a','m','sku1','stock','3','t','feed','s1',NULL,'t')")
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_merchant_signals "
                "(signal_id,merchant_id,subject_ref,signal_type,value,observed_at,"
                "source,external_ref,meta_json,created_at) "
                "VALUES ('b','m','sku1','stock','3','t','feed','s1',NULL,'t')")
            conn.commit()
        except Exception:
            dup = True
        assert dup, "(source, external_ref) must be unique on signals"
        conn.rollback()
        conn.execute(
            "INSERT INTO business_os_merchant_signals "
            "(signal_id,merchant_id,subject_ref,signal_type,value,observed_at,"
            "source,external_ref,meta_json,created_at) "
            "VALUES ('c','m','sku1','stock','3','t','manual',NULL,NULL,'t')")
        conn.execute(
            "INSERT INTO business_os_merchant_signals "
            "(signal_id,merchant_id,subject_ref,signal_type,value,observed_at,"
            "source,external_ref,meta_json,created_at) "
            "VALUES ('d','m','sku1','stock','3','t','manual',NULL,NULL,'t')")
        conn.commit()
    finally:
        conn.close()


def test_proposal_dedupe_key():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_merchant_proposals "
            "(proposal_id,merchant_id,rule_id,subject_ref,signal_type,action_type,"
            "operator,threshold,observed_value,priority,rank,reason,computed_at) "
            "VALUES ('p1','m','r1','sku1','stock','reorder','lte','5','3',0,1,'x','t')")
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_merchant_proposals "
                "(proposal_id,merchant_id,rule_id,subject_ref,signal_type,action_type,"
                "operator,threshold,observed_value,priority,rank,reason,computed_at) "
                "VALUES ('p2','m','r1','sku1','stock','reorder','lte','5','3',0,2,'x',"
                "'t')")
            conn.commit()
        except Exception:
            dup = True
        assert dup, "(merchant_id, rule_id, subject_ref) must be unique"
    finally:
        conn.rollback()
        conn.close()


def test_legacy_untouched():
    conn = db.connect()
    try:
        names = _tables(conn)
        mrch = {t for t in names if t.startswith("business_os_merchant_")}
        assert mrch == set(_TABLES), mrch
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_tables_created,
        test_idempotent,
        test_rule_columns,
        test_signal_columns,
        test_proposal_columns,
        test_operator_check_enforced,
        test_rule_source_ref_dedupe_and_null_exempt,
        test_signal_source_ref_dedupe_and_null_exempt,
        test_proposal_dedupe_key,
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
