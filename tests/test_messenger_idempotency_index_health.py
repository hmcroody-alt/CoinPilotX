"""The idempotency index must report, precisely, whether it is actually enforcing.

The defect this suite exists to prevent is not a broken index -- it is an index
that never installed, in a deployment that looks identical to one where it did.
Before Step 1.1 the installer returned a bare boolean, the caller discarded it,
and the only log line fired on failure. A production node running on application
idempotency alone was therefore indistinguishable from one with the database gate
in force, and "blocked by historical duplicates" was distinguishable from "the
driver fell over" only by reading exception text.

So these tests hold four things: that each of the four outcomes is reached by
inspecting the database rather than by pattern-matching an error string, that the
result survives into a readable health snapshot, that an index wearing the right
name with the wrong shape is never reported as healthy, and that nothing in the
telemetry carries a conversation id, a sender id or a client id.
"""

import os
import sqlite3
import unittest

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2 import service  # noqa: E402
from pulse_communications_v2.models import ensure_schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _IndexCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        ensure_schema(self.cur)
        self.conn.commit()
        service._record_message_idempotency_health(
            {
                "state": None,
                "hard_uniqueness_active": False,
                "checked_at": None,
                "duplicate_groups": None,
                "duplicate_rows": None,
                "error_class": None,
            }
        )

    def tearDown(self):
        self.conn.close()

    def _insert(self, client_id, conversation_id=10, sender=1):
        self.cur.execute(
            "INSERT INTO comm_v2_messages "
            "(conversation_id, sender_user_id, message_type, body, client_message_id, deleted_at, created_at, updated_at) "
            "VALUES (?, ?, 'text', 'hello', ?, '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (conversation_id, sender, client_id),
        )
        self.conn.commit()

    def _rows(self):
        self.cur.execute("SELECT COUNT(*) FROM comm_v2_messages")
        return int(self.cur.fetchone()[0])


class FourStateOutcomeTest(_IndexCase):
    """A: clean, B: already correct, C: blocked by data, D: genuine failure."""

    def test_a_clean_database_installs_and_enforces(self):
        status = service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertEqual(status["state"], service.IDEMPOTENCY_INDEX_INSTALLED)
        self.assertTrue(status["hard_uniqueness_active"])
        self.assertEqual(status["duplicate_groups"], 0)
        self.assertEqual(status["duplicate_rows"], 0)
        self.assertIsNone(status["error_class"])

    def test_b_a_correct_existing_index_is_recognised_not_reinstalled(self):
        service._ensure_message_idempotency_index(self.cur, self.conn)
        status = service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertEqual(status["state"], service.IDEMPOTENCY_INDEX_ALREADY_PRESENT)
        self.assertTrue(status["hard_uniqueness_active"])

    def test_c_historical_duplicates_block_installation_without_deleting_anything(self):
        """The rows this index forbids may already exist. Boot must say so and
        leave them alone -- resolving them is a human decision, not a side effect
        of a restart."""
        self._insert("native-abc")
        self._insert("native-abc")
        self._insert("native-xyz")
        before = self._rows()
        status = service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertEqual(status["state"], service.IDEMPOTENCY_INDEX_BLOCKED_BY_DUPLICATES)
        self.assertFalse(status["hard_uniqueness_active"])
        self.assertEqual(status["duplicate_groups"], 1)
        self.assertEqual(status["duplicate_rows"], 1)
        self.assertEqual(self._rows(), before)

    def test_c_is_decided_by_counting_rows_not_by_reading_an_exception(self):
        """Classifying on driver error text would silently reclassify itself on a
        driver or server upgrade. The duplicate count is taken before any CREATE
        is attempted, so no index exists afterwards."""
        self._insert("native-abc")
        self._insert("native-abc")
        service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertIsNone(service._inspect_message_idempotency_index(self.cur, self.conn))

    def test_d_a_genuine_failure_is_its_own_state(self):
        self.cur.execute("DROP TABLE comm_v2_messages")
        self.conn.commit()
        status = service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertEqual(status["state"], service.IDEMPOTENCY_INDEX_INSTALL_ERROR)
        self.assertFalse(status["hard_uniqueness_active"])
        self.assertTrue(status["error_class"])

    def test_the_four_states_are_distinct(self):
        states = {
            service.IDEMPOTENCY_INDEX_INSTALLED,
            service.IDEMPOTENCY_INDEX_ALREADY_PRESENT,
            service.IDEMPOTENCY_INDEX_BLOCKED_BY_DUPLICATES,
            service.IDEMPOTENCY_INDEX_INSTALL_ERROR,
        }
        self.assertEqual(len(states), 4)


class WrongShapeTest(_IndexCase):
    """An index wearing the right name and the wrong shape must never read healthy.

    `CREATE UNIQUE INDEX IF NOT EXISTS` matches on the NAME alone: if something
    else already owns that name, creation succeeds by doing nothing at all and a
    name-only check would report full protection over an index that enforces
    nothing.
    """

    def _status_for(self, sql):
        self.cur.execute(sql)
        self.conn.commit()
        return service._ensure_message_idempotency_index(self.cur, self.conn)

    def _assert_not_healthy(self, status):
        self.assertNotEqual(status["state"], service.IDEMPOTENCY_INDEX_ALREADY_PRESENT)
        self.assertFalse(status["hard_uniqueness_active"])
        self.assertEqual(status["error_class"], "IndexShapeMismatch")

    def test_wrong_columns_is_not_healthy(self):
        self._assert_not_healthy(
            self._status_for(
                f"CREATE UNIQUE INDEX {service.MESSAGE_IDEMPOTENCY_INDEX} "
                "ON comm_v2_messages (conversation_id, client_message_id) "
                "WHERE client_message_id IS NOT NULL AND client_message_id <> ''"
            )
        )

    def test_a_non_unique_index_is_not_healthy(self):
        self._assert_not_healthy(
            self._status_for(
                f"CREATE INDEX {service.MESSAGE_IDEMPOTENCY_INDEX} "
                "ON comm_v2_messages (conversation_id, sender_user_id, client_message_id) "
                "WHERE client_message_id IS NOT NULL AND client_message_id <> ''"
            )
        )

    def test_a_wrong_predicate_is_not_healthy(self):
        """A wider predicate would collide every legacy row that carries no client
        id against every other one; a narrower one leaves real sends unprotected."""
        self._assert_not_healthy(
            self._status_for(
                f"CREATE UNIQUE INDEX {service.MESSAGE_IDEMPOTENCY_INDEX} "
                "ON comm_v2_messages (conversation_id, sender_user_id, client_message_id) "
                "WHERE client_message_id IS NOT NULL"
            )
        )

    def test_the_expected_shape_is_pinned(self):
        self.assertEqual(
            service.MESSAGE_IDEMPOTENCY_COLUMNS,
            ("conversation_id", "sender_user_id", "client_message_id"),
        )
        self.assertEqual(
            service.MESSAGE_IDEMPOTENCY_PREDICATE,
            "client_message_id IS NOT NULL AND client_message_id <> ''",
        )
        self.assertIn(service.MESSAGE_IDEMPOTENCY_PREDICATE, service._MESSAGE_IDEMPOTENCY_INDEX_SQL)

    def test_predicates_are_compared_by_meaning_not_by_rendering(self):
        """PostgreSQL prints the predicate back through its own formatter, so a
        correct index returns `(client_message_id <> ''::text)`. Comparing raw
        text would report it as malformed."""
        self.assertEqual(
            service._normalise_predicate("(client_message_id IS NOT NULL AND (client_message_id <> ''::text))"),
            service._normalise_predicate(service.MESSAGE_IDEMPOTENCY_PREDICATE),
        )


class HealthSnapshotTest(_IndexCase):
    """The installer's answer has to survive somewhere a human can read it."""

    def test_the_result_is_not_discarded(self):
        source = open(os.path.join(ROOT, "pulse_communications_v2", "service.py"), encoding="utf-8").read()
        bootstrap = source[source.index("def _ensure_schema_ready(") :]
        bootstrap = bootstrap[: bootstrap.index("\nMESSAGE_IDEMPOTENCY_INDEX")]
        self.assertIn("= _ensure_message_idempotency_index(cur, conn)", bootstrap)
        self.assertIn("_log_message_idempotency_status(", bootstrap)

    def test_health_reports_the_state_that_was_reached(self):
        service._ensure_message_idempotency_index(self.cur, self.conn)
        snapshot = service.message_idempotency_health()
        self.assertEqual(snapshot["state"], service.IDEMPOTENCY_INDEX_INSTALLED)
        self.assertTrue(snapshot["hard_uniqueness_active"])
        self.assertEqual(snapshot["index_name"], service.MESSAGE_IDEMPOTENCY_INDEX)
        self.assertTrue(snapshot["checked_at"])

    def test_a_degraded_deployment_is_visible_as_degraded(self):
        """Losing the index does not take Messenger offline -- the send path is
        still correct without it. What must not happen is that it looks fine."""
        self._insert("native-abc")
        self._insert("native-abc")
        service._ensure_message_idempotency_index(self.cur, self.conn)
        self.assertFalse(service.message_idempotency_health()["hard_uniqueness_active"])

    def test_the_snapshot_is_a_copy_not_the_live_dictionary(self):
        service._ensure_message_idempotency_index(self.cur, self.conn)
        snapshot = service.message_idempotency_health()
        snapshot["hard_uniqueness_active"] = "tampered"
        self.assertTrue(service.message_idempotency_health()["hard_uniqueness_active"])

    def test_the_health_surface_is_admin_gated(self):
        """Deployment health, not a public debug endpoint."""
        routes = open(os.path.join(ROOT, "pulse_communications_v2", "routes.py"), encoding="utf-8").read()
        handler = routes[routes.index("def admin_messenger_idempotency_health(") :]
        handler = handler[: handler.index("\n@comm_v2_blueprint")]
        self.assertIn("_current_admin()", handler)
        self.assertIn("403", handler)
        self.assertIn("message_idempotency_health()", handler)


class TelemetryCarriesNoMessageDataTest(_IndexCase):
    """Logs are read by more people, and retained in more places, than the database."""

    def test_the_status_carries_counts_only(self):
        self._insert("native-secret-client-id")
        self._insert("native-secret-client-id")
        status = service._ensure_message_idempotency_index(self.cur, self.conn)
        blob = repr(status)
        self.assertNotIn("native-secret-client-id", blob)
        self.assertNotIn("hello", blob)
        self.assertEqual(
            set(status),
            {
                "state",
                "hard_uniqueness_active",
                "index_name",
                "checked_at",
                "duplicate_groups",
                "duplicate_rows",
                "error_class",
            },
        )

    def test_the_duplicate_query_selects_no_identifying_columns(self):
        upper = service._MESSAGE_IDEMPOTENCY_DUPLICATE_SQL.upper()
        self.assertIn("COUNT(*)", upper)
        select = upper[upper.index("SELECT") : upper.index("FROM")]
        self.assertNotIn("BODY", select)
        self.assertNotIn("CLIENT_MESSAGE_ID", select)

    def test_exactly_one_structured_startup_line(self):
        source = open(os.path.join(ROOT, "pulse_communications_v2", "service.py"), encoding="utf-8").read()
        self.assertEqual(source.count('"PULSE_COMM_V2_IDEMPOTENCY_INDEX state=%s'), 1)
        emitter = source[source.index("def _log_message_idempotency_status(") :]
        emitter = emitter[: emitter.index("\ndef ", 1)]
        for field in ("state=%s", "hard_uniqueness_active=%s", "index=%s"):
            self.assertIn(field, emitter)
        self.assertNotIn("body", emitter)

    def test_the_log_line_never_contains_a_client_id(self):
        records = []

        class _Capture:
            def info(self, message, *args):
                records.append(message % args)

        original = service.logging
        service.logging = _Capture()
        try:
            self._insert("native-secret-client-id")
            self._insert("native-secret-client-id")
            service._log_message_idempotency_status(
                service._ensure_message_idempotency_index(self.cur, self.conn)
            )
        finally:
            service.logging = original
        self.assertEqual(len(records), 1)
        self.assertNotIn("native-secret-client-id", records[0])
        self.assertIn("hard_uniqueness_active=false", records[0])
        self.assertIn("state=blocked_by_duplicates", records[0])


def test_messenger_idempotency_index_health():
    unittest.main(module=__name__, argv=["", "-v"], exit=False)


if __name__ == "__main__":
    unittest.main()
