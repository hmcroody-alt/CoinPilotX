"""Cross-engine column introspection — `services.db.get_table_columns`.

The ads/financial services used to send `PRAGMA table_info(...)` unconditionally.
SQLite answers it; PostgreSQL (production, via DATABASE_URL) raises a SQL error,
poisons the transaction, and every defensive `ALTER TABLE ... ADD COLUMN`
fallback silently became a no-op. The shared helper answers via PRAGMA on SQLite
and via information_schema.columns scoped to current_schema() on PostgreSQL.

Real Postgres cannot run in this sandbox, so the Postgres branch is proved
structurally: a stub cursor records exactly what would be executed, and the test
asserts the parameterized information_schema query (and the absence of any
PRAGMA) rather than the round trip.

    python3 -m unittest tests.business_os_finance.test_db_introspection -v
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from services import db  # noqa: E402


class _RecordingCursor:
    """Stub DBAPI cursor that records execute() calls and serves canned rows."""

    def __init__(self, rows=None):
        self.executed = []  # list of (sql, params) tuples
        self._rows = list(rows or [])

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))
        return self

    def fetchall(self):
        return list(self._rows)


class _RecordingConnection:
    """Stub connection so the helper's `.cursor()` path is exercised too."""

    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class SQLiteBranchTest(unittest.TestCase):
    def _make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE pulse_ad_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                available_balance_cents INTEGER DEFAULT 0,
                daily_limit_cents INTEGER DEFAULT 0
            )
            """
        )
        return conn

    def test_returns_correct_columns_on_sqlite(self):
        conn = self._make_conn()
        try:
            columns = db.get_table_columns(conn, "pulse_ad_wallets", engine_name="sqlite")
            self.assertEqual(
                columns,
                {"id", "account_id", "available_balance_cents", "daily_limit_cents"},
            )
        finally:
            conn.close()

    def test_default_engine_detection_is_sqlite_here(self):
        # Locally (no PostgreSQL DATABASE_URL) the module-level detection must
        # choose the PRAGMA branch — the exact behavior the call sites had.
        self.assertEqual(db.ENGINE_NAME, "sqlite")
        conn = self._make_conn()
        try:
            columns = db.get_table_columns(conn, "pulse_ad_wallets")
            self.assertIn("available_balance_cents", columns)
        finally:
            conn.close()

    def test_accepts_a_cursor_as_well_as_a_connection(self):
        conn = self._make_conn()
        try:
            cur = conn.cursor()
            columns = db.get_table_columns(cur, "pulse_ad_wallets", engine_name="sqlite")
            self.assertEqual(len(columns), 4)
        finally:
            conn.close()

    def test_missing_table_yields_empty_set(self):
        # PRAGMA table_info on an absent table returns zero rows, not an error —
        # callers rely on falsy meaning "table not there / add nothing".
        conn = sqlite3.connect(":memory:")
        try:
            self.assertEqual(
                db.get_table_columns(conn, "no_such_table", engine_name="sqlite"), set()
            )
        finally:
            conn.close()


class PostgresBranchTest(unittest.TestCase):
    def test_postgres_branch_builds_information_schema_query(self):
        cursor = _RecordingCursor(rows=[("id",), ("account_id",), ("daily_limit_cents",)])
        columns = db.get_table_columns(
            cursor, "pulse_ad_wallets", engine_name="postgresql"
        )
        self.assertEqual(columns, {"id", "account_id", "daily_limit_cents"})
        self.assertEqual(len(cursor.executed), 1)
        sql, params = cursor.executed[0]
        self.assertEqual(sql, db.POSTGRES_TABLE_COLUMNS_SQL)
        self.assertIn("information_schema.columns", sql)
        self.assertIn("current_schema()", sql)
        self.assertEqual(params, ("pulse_ad_wallets",))
        # The table name travels as a bound parameter, never interpolated.
        self.assertNotIn("pulse_ad_wallets", sql)
        self.assertEqual(sql.count("?"), 1)

    def test_postgres_branch_never_sends_pragma(self):
        cursor = _RecordingCursor(rows=[])
        db.get_table_columns(cursor, "pulse_ad_invoices", engine_name="postgresql")
        for sql, _params in cursor.executed:
            self.assertNotIn("PRAGMA", sql.upper())

    def test_postgres_branch_via_connection_object(self):
        cursor = _RecordingCursor(rows=[("funding_session_id",)])
        conn = _RecordingConnection(cursor)
        columns = db.get_table_columns(
            conn, "pulse_ad_invoices", engine_name="postgresql"
        )
        self.assertEqual(columns, {"funding_session_id"})
        self.assertEqual(cursor.executed[0][1], ("pulse_ad_invoices",))

    def test_invalid_table_name_is_rejected_before_any_sql(self):
        cursor = _RecordingCursor()
        for bad in ("", None, "users; DROP TABLE x", "a-b", 'x"y'):
            with self.assertRaises(ValueError):
                db.get_table_columns(cursor, bad, engine_name="postgresql")
        self.assertEqual(cursor.executed, [])


class AdsServicesContainNoPragmaTest(unittest.TestCase):
    """The named ads/financial services must not contain PRAGMA at all —
    not even in comments, so a future revert is impossible to miss."""

    FILES = (
        "services/pulse_ad_payments.py",
        "services/pulse_ads_adsets.py",
        "services/pulse_advertiser_portal.py",
    )

    def test_zero_pragma_occurrences(self):
        for rel_path in self.FILES:
            path = os.path.join(REPO_ROOT, rel_path)
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn(
                "PRAGMA",
                source.upper(),
                msg=f"{rel_path} still references PRAGMA (SQLite-only; breaks PostgreSQL)",
            )

    def test_files_use_shared_helper(self):
        for rel_path in self.FILES:
            path = os.path.join(REPO_ROOT, rel_path)
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            self.assertIn(
                "db.get_table_columns(",
                source,
                msg=f"{rel_path} should introspect via services.db.get_table_columns",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
