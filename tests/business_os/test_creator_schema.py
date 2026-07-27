"""Creator commerce schema (Stage 6 Part 13).

Proves: the four canonical tables are created; ensure_schema is idempotent; the
offering_type CHECK constrains the enum; UNIQUE (source, external_ref) dedupes both
input logs while NULL external_ref is exempt; the UNIQUE (creator_id, supporter_id)
projection key is exactly-once; and no legacy table is touched (only the four
business_os_creator_* tables exist).

    python tests/business_os/test_creator_schema.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_creatschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.creator_commerce import schema as sch  # noqa: E402


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
        for name in ("business_os_creator_offerings",
                     "business_os_creator_contributions",
                     "business_os_creator_supporters",
                     "business_os_creator_audit"):
            assert name in t, (name, t)
    finally:
        conn.close()


def test_idempotent():
    sch.ensure_schema()
    sch.ensure_schema()  # second/third call must not raise
    conn = db.connect()
    try:
        assert "business_os_creator_offerings" in _tables(conn)
    finally:
        conn.close()


def test_offering_type_check_enforced():
    conn = db.connect()
    try:
        raised = False
        try:
            conn.execute(
                "INSERT INTO business_os_creator_offerings "
                "(offering_id,creator_id,offering_type,created_at) "
                "VALUES (?,?,?,?)", ("o_bad", "c1", "banana", "t"))
            conn.commit()
        except Exception:
            raised = True
            conn.rollback()
        assert raised, "offering_type CHECK should reject 'banana'"
    finally:
        conn.close()


def test_offering_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_creator_offerings "
            "(offering_id,creator_id,offering_type,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?)", ("o1", "c1", "tip", "feed", "R1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_creator_offerings "
                "(offering_id,creator_id,offering_type,source,external_ref,created_at) "
                "VALUES (?,?,?,?,?,?)", ("o2", "c1", "tip", "feed", "R1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (source, external_ref) should be rejected"
        # NULL external_ref is exempt: two manual rows both allowed
        conn.execute(
            "INSERT INTO business_os_creator_offerings "
            "(offering_id,creator_id,offering_type,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?)", ("o3", "c1", "tip", "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_creator_offerings "
            "(offering_id,creator_id,offering_type,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?)", ("o4", "c1", "tip", "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_contribution_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_creator_contributions "
            "(contribution_id,creator_id,supporter_id,amount,occurred_at,source,"
            "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("k1", "c1", "s1", "10.00", "t", "feed", "CR1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_creator_contributions "
                "(contribution_id,creator_id,supporter_id,amount,occurred_at,source,"
                "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("k2", "c1", "s1", "10.00", "t", "feed", "CR1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate contribution (source, external_ref) should be rejected"
        conn.execute(
            "INSERT INTO business_os_creator_contributions "
            "(contribution_id,creator_id,supporter_id,amount,occurred_at,source,"
            "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("k3", "c1", "s1", "10.00", "t", "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_creator_contributions "
            "(contribution_id,creator_id,supporter_id,amount,occurred_at,source,"
            "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("k4", "c1", "s1", "10.00", "t", "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_supporter_projection_key_exactly_once():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_creator_supporters "
            "(row_id,creator_id,supporter_id,total_amount,contribution_count,tier,"
            "rank,computed_at) VALUES (?,?,?,?,?,?,?,?)",
            ("r1", "cX", "sX", "10.00", 1, "bronze", 1, "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_creator_supporters "
                "(row_id,creator_id,supporter_id,total_amount,contribution_count,tier,"
                "rank,computed_at) VALUES (?,?,?,?,?,?,?,?)",
                ("r2", "cX", "sX", "20.00", 2, "bronze", 1, "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (creator_id, supporter_id) should be rejected"
    finally:
        conn.close()


def test_legacy_untouched():
    conn = db.connect()
    try:
        creator_tables = {t for t in _tables(conn)
                          if t.startswith("business_os_creator_")}
        assert creator_tables == {
            "business_os_creator_offerings",
            "business_os_creator_contributions",
            "business_os_creator_supporters",
            "business_os_creator_audit"}, creator_tables
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_tables_created,
        test_idempotent,
        test_offering_type_check_enforced,
        test_offering_source_ref_dedupe_and_null_exempt,
        test_contribution_source_ref_dedupe_and_null_exempt,
        test_supporter_projection_key_exactly_once,
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
