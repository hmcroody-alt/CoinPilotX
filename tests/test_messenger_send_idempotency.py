"""One logical outbound message must produce exactly one stored message.

The failure this suite exists to prevent is not theoretical: PulseSoc has shown
the same message twice in production. A send can be observed five times -- as the
local optimistic bubble, the REST response, a realtime echo, a reconnect replay
and a push event -- and all five have to reconcile to one row. They reconcile on
`client_message_id`, so that identity has to be stable across retries on the
client and enforced as unique on the server. These tests hold both halves.
"""

import os
import sqlite3
import unittest

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


def test_messenger_send_idempotency():
    unittest.main(module=__name__, argv=["", "-v"], exit=False)


if __name__ == "__main__":
    unittest.main()
