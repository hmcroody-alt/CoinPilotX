"""Recommendations schema (Stage 6 Part 5).

Proves the canonical business_os_rec_* surface is created idempotently, the CHECK on
interaction_type is enforced, the (source, external_ref) dedupe indexes exist (NULL
exempt), the (user_id, model, item_id) projection key is exactly-once, and no legacy
table is touched.

    python tests/business_os/test_rec_schema.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_recschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.recommendations import schema as asch  # noqa: E402

_TABLES = ("business_os_rec_items", "business_os_rec_interactions",
           "business_os_rec_recommendations", "business_os_rec_audit")


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


def test_item_columns():
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(business_os_rec_items)").fetchall()}
        for c in ("item_id", "item_type", "title", "category", "tags_json",
                  "owner_ref", "source", "external_ref", "created_at"):
            assert c in cols, c
    finally:
        conn.close()


def test_interaction_columns():
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(business_os_rec_interactions)").fetchall()}
        for c in ("interaction_id", "user_id", "item_id", "interaction_type",
                  "weight", "occurred_at", "source", "external_ref", "created_at"):
            assert c in cols, c
    finally:
        conn.close()


def test_recommendation_columns():
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(business_os_rec_recommendations)").fetchall()}
        for c in ("rec_id", "user_id", "model", "item_id", "item_type", "category",
                  "score", "rank", "reason", "computed_at"):
            assert c in cols, c
    finally:
        conn.close()


def test_interaction_type_check_enforced():
    conn = db.connect()
    try:
        raised = False
        try:
            conn.execute(
                "INSERT INTO business_os_rec_interactions "
                "(interaction_id,user_id,item_id,interaction_type,weight,occurred_at,"
                "source,external_ref,meta_json,created_at) "
                "VALUES ('x','u','i','warp',1,'t','manual',NULL,NULL,'t')")
            conn.commit()
            raised = False
        except Exception:
            raised = True
        assert raised, "CHECK on interaction_type should reject 'warp'"
    finally:
        conn.rollback()
        conn.close()


def test_interaction_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_rec_interactions "
            "(interaction_id,user_id,item_id,interaction_type,weight,occurred_at,"
            "source,external_ref,meta_json,created_at) "
            "VALUES ('a','u','i','view',1,'t','feed','e1',NULL,'t')")
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_rec_interactions "
                "(interaction_id,user_id,item_id,interaction_type,weight,occurred_at,"
                "source,external_ref,meta_json,created_at) "
                "VALUES ('b','u','i','view',1,'t','feed','e1',NULL,'t')")
            conn.commit()
        except Exception:
            dup = True
        assert dup, "(source, external_ref) must be unique"
        conn.rollback()
        # NULL external_ref is exempt: two manual rows both allowed
        conn.execute(
            "INSERT INTO business_os_rec_interactions "
            "(interaction_id,user_id,item_id,interaction_type,weight,occurred_at,"
            "source,external_ref,meta_json,created_at) "
            "VALUES ('c','u','i','view',1,'t','manual',NULL,NULL,'t')")
        conn.execute(
            "INSERT INTO business_os_rec_interactions "
            "(interaction_id,user_id,item_id,interaction_type,weight,occurred_at,"
            "source,external_ref,meta_json,created_at) "
            "VALUES ('d','u','i','view',1,'t','manual',NULL,NULL,'t')")
        conn.commit()
    finally:
        conn.close()


def test_recommendation_dedupe_key():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_rec_recommendations "
            "(rec_id,user_id,model,item_id,item_type,category,score,rank,reason,"
            "computed_at) VALUES ('r1','u','hybrid','i','x',NULL,'1.0',1,'r','t')")
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_rec_recommendations "
                "(rec_id,user_id,model,item_id,item_type,category,score,rank,reason,"
                "computed_at) VALUES ('r2','u','hybrid','i','x',NULL,'1.0',2,'r','t')")
            conn.commit()
        except Exception:
            dup = True
        assert dup, "(user_id, model, item_id) must be unique"
    finally:
        conn.rollback()
        conn.close()


def test_legacy_untouched():
    conn = db.connect()
    try:
        names = _tables(conn)
        rec = {t for t in names if t.startswith("business_os_rec_")}
        assert rec == set(_TABLES), rec
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_tables_created,
        test_idempotent,
        test_item_columns,
        test_interaction_columns,
        test_recommendation_columns,
        test_interaction_type_check_enforced,
        test_interaction_source_ref_dedupe_and_null_exempt,
        test_recommendation_dedupe_key,
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
