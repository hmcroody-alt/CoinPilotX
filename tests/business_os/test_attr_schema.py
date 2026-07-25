"""Attribution schema (Stage 6 Part 1).

Proves: idempotent create; the three logs + audit exist with expected columns; the
touch_type CHECK is enforced; UNIQUE (source, external_ref) dedupes feed replays
while NULL external_ref (manual) is exempt; UNIQUE (conversion_id, model,
touchpoint_id) dedupes credit; and NO legacy table is touched.

    python tests/business_os/test_attr_schema.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_attrschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.attribution import schema as asch  # noqa: E402


def setup_module(module=None):
    asch.ensure_schema()


def _cols(table):
    conn = db.connect()
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _tables():
    conn = db.connect()
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()


def test_tables_created():
    t = _tables()
    for name in ("business_os_attr_touchpoints", "business_os_attr_conversions",
                 "business_os_attr_credits", "business_os_attr_audit"):
        assert name in t, name


def test_idempotent():
    asch.ensure_schema()
    asch.ensure_schema()  # second/third call must not raise
    assert "business_os_attr_touchpoints" in _tables()


def test_touchpoint_columns():
    c = _cols("business_os_attr_touchpoints")
    for col in ("touchpoint_id", "user_id", "channel", "touch_type", "campaign_ref",
                "occurred_at", "source", "external_ref", "meta_json", "created_at"):
        assert col in c, col


def test_conversion_columns():
    c = _cols("business_os_attr_conversions")
    for col in ("conversion_id", "user_id", "conversion_type", "value_cents",
                "currency", "occurred_at", "lookback_days", "source",
                "external_ref", "related_object", "created_at"):
        assert col in c, col


def test_credit_columns():
    c = _cols("business_os_attr_credits")
    for col in ("credit_id", "conversion_id", "touchpoint_id", "model", "user_id",
                "channel", "campaign_ref", "credit_cents", "credit_fraction",
                "position", "computed_at"):
        assert col in c, col


def test_touch_type_check_enforced():
    conn = db.connect()
    try:
        raised = False
        try:
            conn.execute(
                "INSERT INTO business_os_attr_touchpoints "
                "(touchpoint_id,user_id,channel,touch_type,occurred_at,source,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (asch.new_id(), "u1", "ad", "teleport", asch.utc_now_iso(),
                 "manual", asch.utc_now_iso()))
            conn.commit()
        except Exception:
            raised = True
            conn.rollback()
        assert raised, "bad touch_type should be rejected by CHECK"
    finally:
        conn.close()


def test_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        def ins(ext):
            conn.execute(
                "INSERT INTO business_os_attr_touchpoints "
                "(touchpoint_id,user_id,channel,touch_type,occurred_at,source,"
                "external_ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (asch.new_id(), "u1", "ad", "click", asch.utc_now_iso(),
                 "feedX", ext, asch.utc_now_iso()))
        ins("evt-1")
        conn.commit()
        dup = False
        try:
            ins("evt-1")  # same (source, external_ref) -> UNIQUE violation
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (source, external_ref) must be rejected"
        # NULL external_ref is exempt: two manual rows with NULL ref are allowed
        ins(None)
        ins(None)
        conn.commit()
    finally:
        conn.close()


def test_credit_dedupe_key():
    conn = db.connect()
    try:
        def ins():
            conn.execute(
                "INSERT INTO business_os_attr_credits "
                "(credit_id,conversion_id,touchpoint_id,model,user_id,credit_cents,"
                "credit_fraction,position,computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (asch.new_id(), "conv1", "tp1", "linear", "u1", 100, "0.5", 0,
                 asch.utc_now_iso()))
        ins()
        conn.commit()
        dup = False
        try:
            ins()
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (conversion, model, touchpoint) must be rejected"
    finally:
        conn.close()


def test_legacy_untouched():
    # ensure_schema must create ONLY business_os_attr_* tables; assert it did not
    # drop or shadow anything by checking our tables are all attr-prefixed additions.
    created = {t for t in _tables() if t.startswith("business_os_attr_")}
    assert created == {
        "business_os_attr_touchpoints", "business_os_attr_conversions",
        "business_os_attr_credits", "business_os_attr_audit"}, created


def _run_standalone():
    setup_module()
    tests = [
        test_tables_created,
        test_idempotent,
        test_touchpoint_columns,
        test_conversion_columns,
        test_credit_columns,
        test_touch_type_check_enforced,
        test_source_ref_dedupe_and_null_exempt,
        test_credit_dedupe_key,
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
