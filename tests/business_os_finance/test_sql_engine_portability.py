"""Payments SQL must run on Postgres, not just on the SQLite used by tests.

Production runs PostgreSQL via ``DATABASE_URL``; dev and this suite run SQLite.
That gap hid a live outage: ``connect_accounts`` selected ``rowid AS id``, and
``rowid`` is a SQLite-only implicit column, so every Connect account read raised
``psycopg2.errors.UndefinedColumn`` in production while the module tested green.

Two guards, deliberately different in kind:

* ``WithoutRowidTests`` reproduces the failure *dynamically* with no Postgres
  instance. A SQLite ``WITHOUT ROWID`` table has no ``rowid`` either, so the
  same queries raise "no such column: rowid" — and because the module still has
  to return correct rows afterwards, this also catches a "fix" that merely
  makes the query run.
* ``NoImplicitRowidTests`` is a *static* scan over every payments module, so a
  new query reintroducing ``rowid`` fails even with no test exercising it.

    python3 -m unittest tests.business_os_finance.test_sql_engine_portability -v
"""

import ast
import os
import re
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_PAYMENTS_DIR = os.path.join(_ROOT, "services", "business_os", "payments")

# SQLite exposes these implicit columns on ordinary tables; Postgres has none of
# them. `oid` is a Postgres system column too, but it is not user-selectable on
# a normal table there either, so it is equally unsafe to depend on.
_IMPLICIT_COLUMNS = re.compile(r"\b(rowid|_rowid_|oid)\b", re.IGNORECASE)

# Mirrors connect_accounts.ensure_schema(), but WITHOUT ROWID (which requires an
# explicit PRIMARY KEY). `user_id` is already NOT NULL UNIQUE there, so making it
# the primary key does not change which rows are addressable.
_WITHOUT_ROWID_DDL = """
CREATE TABLE connect_account_state (
    user_id TEXT NOT NULL PRIMARY KEY,
    connected_account_id TEXT NOT NULL UNIQUE,
    payouts_enabled INTEGER NOT NULL DEFAULT 0,
    charges_enabled INTEGER NOT NULL DEFAULT 0,
    details_submitted INTEGER NOT NULL DEFAULT 0,
    requirements_json TEXT NOT NULL DEFAULT '{}',
    disabled_reason TEXT NOT NULL DEFAULT '',
    last_synced_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) WITHOUT ROWID;
"""


def _account_event(*, account_id="acct_1", user_id="7", payouts=True):
    return {
        "id": "evt_acct_1",
        "type": "account.updated",
        "data": {"object": {
            "id": account_id,
            "payouts_enabled": payouts,
            "charges_enabled": True,
            "details_submitted": True,
            "requirements": {},
            "metadata": {"user_id": user_id},
        }},
    }


def _sql_string_literals(path):
    """Every string literal in ``path`` that is not a docstring.

    Docstrings are excluded so that prose *describing* this bug (including this
    module's own explanation of it) does not trip the scan. Adjacent literals
    are already concatenated by the parser, so a query split across source lines
    arrives here as one string.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            yield node.lineno, node.value


def _scan_for_implicit_columns(directory):
    """``["file.py:12: SELECT rowid ..."]`` for every offending literal."""
    offenders = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py"):
            continue
        for lineno, text in _sql_string_literals(os.path.join(directory, name)):
            if _IMPLICIT_COLUMNS.search(text):
                offenders.append(f"{name}:{lineno}: {text.strip()[:90]}")
    return offenders


class NoImplicitRowidTests(unittest.TestCase):
    """No payments query may lean on a SQLite-only implicit column."""

    def test_payments_sql_never_references_rowid(self):
        offenders = _scan_for_implicit_columns(_PAYMENTS_DIR)
        self.assertEqual(
            offenders, [],
            "SQLite-only implicit column referenced in payments SQL; this "
            "raises UndefinedColumn on the Postgres used in production:\n  "
            + "\n  ".join(offenders))

    def test_scanner_would_catch_the_original_defect(self):
        """The guard above is only worth having if it fails on the real bug."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "planted.py"), "w",
                      encoding="utf-8") as handle:
                handle.write(
                    '"""A docstring mentioning rowid must not count."""\n'
                    'row = conn.execute(\n'
                    '    "SELECT rowid AS id, * FROM connect_account_state"\n'
                    '    " WHERE user_id = ?", (user_id,))\n')
            offenders = _scan_for_implicit_columns(tmpdir)
        self.assertEqual(len(offenders), 1, offenders)
        self.assertIn("SELECT rowid AS id", offenders[0])


class WithoutRowidTests(unittest.TestCase):
    """connect_accounts must work on a table that has no rowid at all."""

    def setUp(self):
        self._previous_url = os.environ.get("DATABASE_URL")
        self._db_path = tempfile.mktemp(suffix="_without_rowid.db")
        os.environ["DATABASE_URL"] = "sqlite:///" + self._db_path
        bootstrap = sqlite3.connect(self._db_path)
        try:
            bootstrap.executescript(_WITHOUT_ROWID_DDL)
        finally:
            bootstrap.close()

        from services.business_os.payments import connect_accounts, incidents
        self.connect_accounts = connect_accounts
        # ensure_schema() is CREATE TABLE IF NOT EXISTS, so it leaves the
        # WITHOUT ROWID table above in place rather than replacing it.
        connect_accounts.ensure_schema()
        incidents.ensure_schema()

    def tearDown(self):
        if self._previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_url
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self._db_path + suffix)
            except OSError:
                pass

    def _rows(self):
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM connect_account_state").fetchone()[0]
        finally:
            conn.close()

    def test_webhook_insert_and_read_back(self):
        result = self.connect_accounts.apply_account_updated_event(
            _account_event())
        self.assertTrue(result["ok"])
        state = self.connect_accounts.get_state("7")
        self.assertIsNotNone(state, "get_state returned nothing on a rowid-less table")
        self.assertEqual(state["connected_account_id"], "acct_1")
        self.assertTrue(state["payouts_enabled"])

    def test_repeat_event_updates_the_same_row(self):
        """The UPDATE must still address exactly one row without rowid."""
        self.connect_accounts.apply_account_updated_event(_account_event())
        self.connect_accounts.apply_account_updated_event(
            _account_event(payouts=False))
        self.assertEqual(self._rows(), 1, "upsert duplicated the projection row")
        state = self.connect_accounts.get_state("7")
        self.assertFalse(
            state["payouts_enabled"],
            "the update did not land on the row it matched")
        self.assertEqual(state["user_id"], "7")

    def test_lookup_by_account_id(self):
        self.connect_accounts.apply_account_updated_event(_account_event())
        state = self.connect_accounts.get_state_by_account("acct_1")
        self.assertIsNotNone(state)
        self.assertEqual(state["user_id"], "7")

    def test_snapshot_path(self):
        result = self.connect_accounts.record_account_snapshot("7", {
            "ok": True,
            "provider_account_id": "acct_1",
            "payouts_enabled": True,
            "charges_enabled": True,
            "account": {"id": "acct_1", "details_submitted": True},
            "requirements": {},
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["connected_account_id"], "acct_1")

    def test_missing_user_returns_none(self):
        self.assertIsNone(self.connect_accounts.get_state("424242"))


if __name__ == "__main__":
    unittest.main()
