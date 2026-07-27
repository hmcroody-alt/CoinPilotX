"""Localization schema (Stage 6).

Proves: the four canonical tables are created; ensure_schema is idempotent; UNIQUE
(source, external_ref) dedupes both input logs while NULL external_ref is exempt; the
UNIQUE (org_id, locale, string_key) resolution key is exactly-once; and no legacy table
is touched (only the four business_os_l10n_* tables exist).

    python tests/business_os/test_l10n_schema.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_l10nschema_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.localization import schema as sch  # noqa: E402


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
        for name in ("business_os_l10n_locales",
                     "business_os_l10n_strings",
                     "business_os_l10n_resolutions",
                     "business_os_l10n_audit"):
            assert name in t, (name, t)
    finally:
        conn.close()


def test_idempotent():
    sch.ensure_schema()
    sch.ensure_schema()
    conn = db.connect()
    try:
        assert "business_os_l10n_locales" in _tables(conn)
    finally:
        conn.close()


def test_locale_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_l10n_locales "
            "(locale_id,org_id,locale,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?)", ("l1", "o1", "en", "feed", "L1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_l10n_locales "
                "(locale_id,org_id,locale,source,external_ref,created_at) "
                "VALUES (?,?,?,?,?,?)", ("l2", "o1", "fr", "feed", "L1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (source, external_ref) should be rejected"
        conn.execute(
            "INSERT INTO business_os_l10n_locales "
            "(locale_id,org_id,locale,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?)", ("l3", "o1", "de", "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_l10n_locales "
            "(locale_id,org_id,locale,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?)", ("l4", "o1", "es", "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_string_source_ref_dedupe_and_null_exempt():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_l10n_strings "
            "(string_id,org_id,string_key,locale,value,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("s1", "o1", "greeting", "en", "Hello", "feed", "SR1", "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_l10n_strings "
                "(string_id,org_id,string_key,locale,value,source,external_ref,"
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("s2", "o1", "greeting", "en", "Hi", "feed", "SR1", "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate string (source, external_ref) should be rejected"
        conn.execute(
            "INSERT INTO business_os_l10n_strings "
            "(string_id,org_id,string_key,locale,value,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("s3", "o1", "greeting", "fr", "Bonjour", "manual", None, "t"))
        conn.execute(
            "INSERT INTO business_os_l10n_strings "
            "(string_id,org_id,string_key,locale,value,source,external_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("s4", "o1", "greeting", "fr", "Salut", "manual", None, "t"))
        conn.commit()
    finally:
        conn.close()


def test_resolution_key_exactly_once():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_l10n_resolutions "
            "(row_id,org_id,locale,string_key,value,resolved_from,match_type,rank,"
            "computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("r1", "oX", "en", "greeting", "Hello", "en", "exact", 1, "t"))
        conn.commit()
        dup = False
        try:
            conn.execute(
                "INSERT INTO business_os_l10n_resolutions "
                "(row_id,org_id,locale,string_key,value,resolved_from,match_type,rank,"
                "computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("r2", "oX", "en", "greeting", "Hi", "en", "exact", 1, "t"))
            conn.commit()
        except Exception:
            dup = True
            conn.rollback()
        assert dup, "duplicate (org_id, locale, string_key) should be rejected"
    finally:
        conn.close()


def test_legacy_untouched():
    conn = db.connect()
    try:
        l10n_tables = {t for t in _tables(conn) if t.startswith("business_os_l10n_")}
        assert l10n_tables == {
            "business_os_l10n_locales",
            "business_os_l10n_strings",
            "business_os_l10n_resolutions",
            "business_os_l10n_audit"}, l10n_tables
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_tables_created,
        test_idempotent,
        test_locale_source_ref_dedupe_and_null_exempt,
        test_string_source_ref_dedupe_and_null_exempt,
        test_resolution_key_exactly_once,
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
