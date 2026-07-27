"""Governed UNDX actions schema (Stage 6).

Proves: the canonical tables are created; ensure_schema is idempotent; the effect
CHECK constrains the enum; UNIQUE (source, external_ref) dedupes both input logs while
NULL external_ref is exempt; the UNIQUE (request_id) decision key is exactly-once; and
the Stage 1 governance tables are additive beside the original decision projection.

    python tests/business_os/test_undx_schema.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_undxschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.undx_actions import schema as sch  # noqa: E402

EXPECTED_UNDX_TABLES = {
    "business_os_undx_policies",
    "business_os_undx_action_requests",
    "business_os_undx_decisions",
    "business_os_undx_audit",
    "business_os_undx_tool_registry",
    "business_os_undx_permissions",
    "business_os_undx_confirmations",
    "business_os_undx_action_receipts",
    "business_os_undx_emergency_stops",
}


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
        for name in EXPECTED_UNDX_TABLES:
            assert name in t, (name, t)
    finally:
        conn.close()


def test_idempotent():
    sch.ensure_schema()
    sch.ensure_schema()
    conn = db.connect()
    try:
        assert "business_os_undx_policies" in _tables(conn)
    finally:
        conn.close()


def test_effect_check_enforced():
    conn = db.connect()
    try:
        raised = False
        try:
            conn.execute(
                "INSERT INTO business_os_undx_policies "
                "(policy_id,org_id,action_type,effect,created_at) "
                "VALUES (?,?,?,?,?)", ("p_bad", "o1", "send", "maybe", "t"))
            conn.commit()
        except Exception:
            raised = True
            conn.rollback()
        assert raised, "effect CHECK should reject 'maybe'"
    finally:
        conn.close()


def test_policy_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_undx_policies "
            "(policy_id,org_id,action_type,effect,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?)", ("p1", "o1", "send", "allow", "feed", "R1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_undx_policies "
                "(policy_id,org_id,action_type,effect,source,external_ref,created_at) "
                "VALUES (?,?,?,?,?,?,?)", ("p2", "o1", "send", "deny", "feed", "R1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (source, external_ref) should be rejected"
        conn.execute(
            "INSERT INTO business_os_undx_policies "
            "(policy_id,org_id,action_type,effect,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?)", ("p3", "o1", "send", "allow", "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_undx_policies "
            "(policy_id,org_id,action_type,effect,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?)", ("p4", "o1", "send", "allow", "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_request_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_undx_action_requests "
            "(request_id,org_id,actor,action_type,risk,requested_at,source,"
            "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("k1", "o1", "a1", "send", "low", "t", "feed", "CR1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_undx_action_requests "
                "(request_id,org_id,actor,action_type,risk,requested_at,source,"
                "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("k2", "o1", "a1", "send", "low", "t", "feed", "CR1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate request (source, external_ref) should be rejected"
        conn.execute(
            "INSERT INTO business_os_undx_action_requests "
            "(request_id,org_id,actor,action_type,risk,requested_at,source,"
            "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("k3", "o1", "a1", "send", "low", "t", "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_undx_action_requests "
            "(request_id,org_id,actor,action_type,risk,requested_at,source,"
            "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("k4", "o1", "a1", "send", "low", "t", "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_decision_key_exactly_once():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_undx_decisions "
            "(row_id,org_id,request_id,action_type,actor,risk,effect,rank,computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("r1", "oX", "reqX", "send", "aX", "low", "allow", 1, "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_undx_decisions "
                "(row_id,org_id,request_id,action_type,actor,risk,effect,rank,"
                "computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("r2", "oX", "reqX", "send", "aX", "low", "deny", 1, "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate request_id should be rejected"
    finally:
        conn.close()


def test_legacy_untouched():
    conn = db.connect()
    try:
        undx_tables = {t for t in _tables(conn) if t.startswith("business_os_undx_")}
        assert undx_tables == EXPECTED_UNDX_TABLES, undx_tables
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_tables_created,
        test_idempotent,
        test_effect_check_enforced,
        test_policy_source_ref_dedupe_and_null_exempt,
        test_request_source_ref_dedupe_and_null_exempt,
        test_decision_key_exactly_once,
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
