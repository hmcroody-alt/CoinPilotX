"""One logical outbound message must produce exactly one stored message.

The failure this suite exists to prevent is not theoretical: PulseSoc has shown
the same message twice in production. A send can be observed five times -- as the
local optimistic bubble, the REST response, a realtime echo, a reconnect replay
and a push event -- and all five have to reconcile to one row. They reconcile on
`client_message_id`, so that identity has to be stable across retries on the
client and enforced as unique on the server. These tests hold both halves.
"""

import ast
import builtins
import contextlib
import importlib.util
import os
import sqlite3
import sys
import unittest
from unittest import mock

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2 import service  # noqa: E402
from pulse_communications_v2.models import ensure_schema  # noqa: E402

SERVICE_SOURCE = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pulse_communications_v2", "service.py"),
    encoding="utf-8",
).read()


def _send_message_source() -> str:
    body = SERVICE_SOURCE[SERVICE_SOURCE.index("def send_message(") :]
    return body[: body.index("\ndef ", 1)]


class MessageIdentityIndexTest(unittest.TestCase):
    """The database itself, not a lucky interleaving, is what stops the second row."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        ensure_schema(self.cur)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _insert(self, client_id, conversation_id=10, sender=1, deleted_at=""):
        self.cur.execute(
            "INSERT INTO comm_v2_messages "
            "(conversation_id, sender_user_id, message_type, body, client_message_id, deleted_at, created_at, updated_at) "
            "VALUES (?, ?, 'text', 'hello', ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (conversation_id, sender, client_id, deleted_at),
        )
        self.conn.commit()
        return int(self.cur.lastrowid)

    def test_index_installs_on_a_clean_database(self):
        status = service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertEqual(status["state"], service.IDEMPOTENCY_INDEX_INSTALLED)
        self.assertTrue(status["hard_uniqueness_active"])

    def test_second_write_of_the_same_client_id_is_rejected(self):
        service._ensure_message_idempotency_index(self.cur, self.conn)
        self._insert("native-abc")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert("native-abc")

    def test_the_same_client_id_from_a_different_sender_is_a_different_message(self):
        service._ensure_message_idempotency_index(self.cur, self.conn)
        self._insert("native-abc", sender=1)
        self._insert("native-abc", sender=2)
        self._insert("native-abc", sender=1, conversation_id=11)

    def test_messages_without_a_client_id_never_collide(self):
        """Legacy and server-authored rows carry no identity claim, so many of
        them must be allowed to coexist. If the index treated blank as a value
        the second system message in any conversation would be rejected."""
        service._ensure_message_idempotency_index(self.cur, self.conn)
        self._insert("")
        self._insert("")
        self.cur.execute("SELECT COUNT(*) FROM comm_v2_messages WHERE COALESCE(client_message_id,'')=''")
        self.assertEqual(self.cur.fetchone()[0], 2)

    def test_installation_reports_failure_instead_of_blocking_boot(self):
        """Production may already hold the duplicates this index forbids. Raising
        here would take Messenger down over historical data; the send path stays
        correct without the index, so the honest response is False plus a log."""
        self._insert("native-abc")
        self._insert("native-abc")
        status = service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertEqual(status["state"], service.IDEMPOTENCY_INDEX_BLOCKED_BY_DUPLICATES)
        self.assertFalse(status["hard_uniqueness_active"])

    def test_lookup_returns_the_original_row(self):
        message_id = self._insert("native-abc")
        found = service._message_for_client_id(self.cur, 10, 1, "native-abc")
        self.assertEqual(int(found["id"]), message_id)

    def test_lookup_does_not_resurrect_a_deleted_message(self):
        """A client id names a logical message. Handing back a row the sender has
        since deleted would report a resend as successful and put a deleted
        message back on screen."""
        self._insert("native-abc", deleted_at="2026-01-02T00:00:00+00:00")
        self.assertFalse(service._message_for_client_id(self.cur, 10, 1, "native-abc"))

    def test_lookup_is_scoped_to_the_sender_and_conversation(self):
        self._insert("native-abc", conversation_id=10, sender=1)
        self.assertFalse(service._message_for_client_id(self.cur, 10, 2, "native-abc"))
        self.assertFalse(service._message_for_client_id(self.cur, 11, 1, "native-abc"))

    def test_blank_client_id_never_matches_an_arbitrary_row(self):
        self._insert("")
        self.assertIsNone(service._message_for_client_id(self.cur, 10, 1, ""))


class SendMessageIdempotencyContractTest(unittest.TestCase):
    """`send_message` needs the full monolith to run, so its idempotency
    contract is asserted structurally. These are the specific lines whose
    removal would silently reintroduce duplicate messages."""

    def setUp(self):
        self.source = _send_message_source()

    def test_a_known_client_id_short_circuits_before_inserting(self):
        precheck = self.source[: self.source.index("insert_sql")]
        self.assertIn("_message_for_client_id(cur, conversation_id, user_id, client_id)", precheck)
        self.assertIn('"idempotent": True', precheck.replace("'idempotent': True", '"idempotent": True'))

    def test_the_insert_is_conflict_safe(self):
        self.assertIn("try:\n            cur.execute(insert_sql, insert_params)", self.source)

    def test_a_lost_race_returns_the_existing_message_rather_than_a_second_one(self):
        recovery = self.source[self.source.index("cur.execute(insert_sql, insert_params)") :]
        recovery = recovery[: recovery.index("message_id = int(cur.lastrowid)")]
        self.assertIn("winner = _message_for_client_id(", recovery)
        self.assertIn('"idempotent": True', recovery)
        self.assertIn('"message_id": int(winner["id"])', recovery)

    def test_the_recovery_rolls_back_before_reading(self):
        """PostgreSQL aborts the whole transaction on a constraint violation, so
        the recovery SELECT fails too unless the transaction is rolled back
        first. Without this line the fix works on SQLite and fails in
        production."""
        recovery = self.source[self.source.index("cur.execute(insert_sql, insert_params)") :]
        recovery = recovery[: recovery.index("winner = _message_for_client_id(")]
        self.assertIn("conn.rollback()", recovery)

    def test_a_failure_with_no_client_id_is_still_raised(self):
        """Without an identity there is nothing to reconcile against, so the
        except block must not swallow a genuine insert failure."""
        recovery = self.source[self.source.index("cur.execute(insert_sql, insert_params)") :]
        self.assertIn("if not client_id:\n                raise", recovery)

    def test_an_unrecoverable_conflict_is_raised_rather_than_reported_as_sent(self):
        recovery = self.source[self.source.index("winner = _message_for_client_id(") :]
        self.assertIn("if not winner:\n                raise", recovery)

    def test_the_index_is_installed_during_schema_bootstrap(self):
        bootstrap = SERVICE_SOURCE[SERVICE_SOURCE.index("def _ensure_schema_ready(") :]
        bootstrap = bootstrap[: bootstrap.index("\nMESSAGE_IDEMPOTENCY_INDEX")]
        self.assertIn("_ensure_message_idempotency_index(cur, conn)", bootstrap)


class IdempotencyAuditScriptTest(unittest.TestCase):
    """The audit exists so duplicates found in live data are resolved by a human.
    It must never be able to become a deletion path by accident."""

    def setUp(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "messenger_idempotency_audit.py",
        )
        self.source = open(path, encoding="utf-8").read()

    def test_the_audit_is_read_only(self):
        for statement in ("DELETE ", "UPDATE ", "DROP ", "INSERT INTO"):
            self.assertNotIn(statement, self.source.upper().replace("INSERT AFTER", ""))

    def test_the_audit_ignores_blank_client_ids(self):
        self.assertIn("client_message_id IS NOT NULL AND client_message_id <> ''", self.source)

    def test_a_violation_is_a_non_zero_exit(self):
        self.assertIn("return 0 if result[\"index_installable\"] else 1", self.source)


AUDIT_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "messenger_idempotency_audit.py",
)


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("_messenger_idempotency_audit", AUDIT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingConnection:
    """Records what the audit does to a connection, in order."""

    def __init__(self):
        self.calls = []

    def set_autocommit(self, enabled):
        self.calls.append(("autocommit", bool(enabled)))
        return True

    def execute(self, sql, params=None):
        self.calls.append(("execute", sql))
        return None

    def commit(self):
        self.calls.append(("commit", None))
        return None


@contextlib.contextmanager
def _preserved_database_url():
    """`_connect` assigns DATABASE_URL, which would leak to the rest of the run."""
    before = os.environ.get("DATABASE_URL")
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = before


class AuditReachesPostgresWithoutTheMonolithTest(unittest.TestCase):
    """The audit must be aimable at the database whose duplicates it reports.

    It used to reach any non-sqlite target through `import bot`, and importing
    the monolith runs `initialize_database_for_web_startup()` at module scope,
    which calls `init_db()`. When a deployment variable such as
    `RAILWAY_ENVIRONMENT` is present -- i.e. under `railway run`, the only
    practical route to production -- that happens on a daemon thread whose
    failures are swallowed into a log line. So the one tool for inspecting
    production duplicates could not be pointed at production, which is exactly
    what resolving them needs.
    """

    def test_the_script_does_not_import_bot_anywhere(self):
        with open(AUDIT_SCRIPT, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn(
            "bot",
            imported,
            "importing bot runs init_db(); a read-only audit must not carry that",
        )

    def test_connecting_to_postgres_never_reaches_for_bot(self):
        """Booby-trapped rather than asserted against `sys.modules`.

        Another test in the same pytest process may already have imported bot,
        which would make a `sys.modules` check vacuous. Trapping `__import__`
        catches the attempt either way, and names the consequence when it fires.
        """
        module = _load_audit_module()
        real_import = builtins.__import__

        def _trap(name, *args, **kwargs):
            if name == "bot" or name.startswith("bot."):
                raise AssertionError(
                    "the audit imported bot; that runs init_db() against whatever "
                    "DATABASE_URL points at, which here would be production"
                )
            return real_import(name, *args, **kwargs)

        import services.db as app_db

        with _preserved_database_url(), mock.patch.object(app_db, "IS_POSTGRES", True), mock.patch.object(
            app_db, "connect", lambda: _RecordingConnection()
        ), mock.patch.object(builtins, "__import__", _trap):
            module._connect("postgresql://user:pw@127.0.0.1:5432/example")

    def test_a_postgres_session_is_read_only_at_the_server(self):
        """Enforced by the database, not by the script's good intentions.

        The commit is the load-bearing half and is asserted as such.
        `default_transaction_read_only` governs transactions that *start* after
        it is set, and psycopg2 has already opened one to run the SET. Verified
        against production: without the commit the GUC reads back as `on` while
        `SHOW transaction_read_only` stays `off` and an UPDATE is accepted. With
        it, UPDATE, DELETE and CREATE INDEX all raise `ReadOnlySqlTransaction`.

        `set_autocommit` is deliberately not the mechanism -- on Postgres it is a
        silent no-op, because `services.db` hands out a SQLAlchemy
        `_ConnectionFairy` that absorbs the attribute without forwarding it.
        """
        module = _load_audit_module()
        recorded = _RecordingConnection()
        import services.db as app_db

        with _preserved_database_url(), mock.patch.object(app_db, "IS_POSTGRES", True), mock.patch.object(
            app_db, "connect", lambda: recorded
        ):
            returned = module._connect("postgresql://user:pw@127.0.0.1:5432/example")

        self.assertIs(returned, recorded)
        statements = [sql for kind, sql in recorded.calls if kind == "execute"]
        self.assertTrue(
            any("default_transaction_read_only" in sql.lower() and " on" in sql.lower() for sql in statements),
            f"no read-only SET was issued; statements were {statements!r}",
        )
        kinds = [kind for kind, _ in recorded.calls]
        self.assertIn(
            "commit",
            kinds,
            "the SET alone leaves the open transaction read-write; production accepted an UPDATE",
        )
        self.assertLess(
            kinds.index("execute"),
            kinds.index("commit"),
            "the commit has to follow the SET, or it commits nothing",
        )
        self.assertNotIn(
            ("autocommit", True),
            recorded.calls,
            "set_autocommit is a silent no-op on Postgres; relying on it re-opens the hole",
        )

    def test_a_sqlite_target_is_still_opened_directly(self):
        """The direct-file path is untouched: no app import, no read-only SET."""
        module = _load_audit_module()
        import services.db as app_db

        def _refuse():
            raise AssertionError("a sqlite file target must not go through services.db")

        with _preserved_database_url(), mock.patch.object(app_db, "connect", _refuse):
            conn = module._connect("sqlite:///:memory:")
        try:
            self.assertIsInstance(conn, sqlite3.Connection)
            self.assertIs(conn.row_factory, sqlite3.Row)
        finally:
            conn.close()


def test_messenger_send_idempotency():
    unittest.main(module=__name__, argv=["", "-v"], exit=False)


if __name__ == "__main__":
    unittest.main()
