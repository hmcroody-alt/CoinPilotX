"""The ledger, tested against the crash it exists to survive.

An audit trail written only *after* a mutation is not an audit trail; it is a
best-effort note. Between the executor returning and the row being written there is
a window in which a user's data has really changed and nothing anywhere remembers
it. These tests are about that window, and about the more uncomfortable case where
the mutation succeeded and the write recording it did not.

The uncomfortable case has exactly one correct response, and it is not "retry": the
change is already real, so retrying could apply it twice. What the system owes the
user is honesty — the operation is marked for reconciliation, the idempotency key is
preserved so nothing repeats it, and the receipt stops claiming success.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


class LedgerTestCase(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_architecture, undx_tool_gateway

        self.runtime = undx_agent_runtime
        self.architecture = undx_architecture
        self.gateway = undx_tool_gateway
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")

    def tearDown(self) -> None:
        self.fx.stop()

    def operations(self, tool_name: str = "") -> list[dict]:
        """Every ledger row, read straight from the table."""
        sql = ("SELECT operation_id, tool_name, status, canonical_entity_id, verification_json, "
               "idempotency_key FROM pulse_ai_tool_operations")
        params: tuple = ()
        if tool_name:
            sql += " WHERE tool_name=?"
            params = (tool_name,)
        self.fx.cur.execute(sql + " ORDER BY id", params)
        columns = ["operation_id", "tool_name", "status", "canonical_entity_id",
                   "verification_json", "idempotency_key"]
        return [dict(zip(columns, row)) for row in self.fx.cur.fetchall()]


class ReservationPrecedesExecution(LedgerTestCase):
    """Property: no mutation runs before a durable row claims it."""

    def test_the_row_exists_before_the_executor_is_called(self):
        """Observed from inside the executor, which is the only honest vantage point.

        Asserting on the row afterwards would prove only that it exists eventually —
        which the post-execution write already guaranteed and which says nothing about
        the crash window. So the check happens while the mutation is in flight.
        """
        from services import undx_agent_tools

        seen: dict = {}
        original = undx_agent_tools.EXECUTORS["crypto_alerts_pause"]

        def watching(user_id, arguments):
            # A second cursor: the reservation was committed by the gateway, so it is
            # visible to any reader, which is the whole point of committing it.
            probe = self.fx.conn.cursor()
            probe.execute(
                "SELECT status FROM pulse_ai_tool_operations WHERE tool_name=?",
                ("pulsesoc.crypto_alerts.pause",))
            seen["rows"] = [row[0] for row in probe.fetchall()]
            return original(user_id, arguments)

        undx_agent_tools.EXECUTORS["crypto_alerts_pause"] = watching
        try:
            response = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert")
            self.fx.commit()
        finally:
            undx_agent_tools.EXECUTORS["crypto_alerts_pause"] = original

        self.assertEqual(response.status, "verified_success")
        self.assertEqual(seen.get("rows"), ["pending"],
                         "the ledger must already hold a pending row when the mutation runs")

    def test_the_reservation_becomes_the_verdict_rather_than_a_second_row(self):
        """One operation, one row. The pending claim is upgraded in place.

        If the post-execution write had stayed an ``INSERT OR IGNORE`` the reservation
        would silently win and every verified action would read as ``pending`` forever —
        a ledger that is worse than none, because it looks complete.
        """
        response = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert")
        self.fx.commit()
        rows = self.operations("pulsesoc.crypto_alerts.pause")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "verified")
        self.assertEqual(response.status, "verified_success")

    def test_a_read_is_audited_but_not_reserved(self):
        """Reads are recorded; they are not pre-claimed.

        The reservation exists to make a *mutation* crash-safe. A read changes nothing,
        so there is no window to protect and no reason to pay for a second commit on
        every list request — but it is still audited afterwards, because who read what
        is exactly the kind of thing an audit trail is for.
        """
        from services import undx_agent_tools

        seen: dict = {}
        original = undx_agent_tools.EXECUTORS["crypto_alerts_list"]

        def watching(user_id, arguments):
            probe = self.fx.conn.cursor()
            probe.execute("SELECT status FROM pulse_ai_tool_operations WHERE tool_name=?",
                          ("pulsesoc.crypto_alerts.list",))
            seen["rows"] = [row[0] for row in probe.fetchall()]
            return original(user_id, arguments)

        undx_agent_tools.EXECUTORS["crypto_alerts_list"] = watching
        try:
            self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="show me my alerts")
            self.fx.commit()
        finally:
            undx_agent_tools.EXECUTORS["crypto_alerts_list"] = original

        self.assertEqual(seen.get("rows"), [], "a read must not reserve a ledger row")
        after = self.operations("pulsesoc.crypto_alerts.list")
        self.assertEqual([row["status"] for row in after], ["verified"])


class LateAuditFailure(LedgerTestCase):
    """Property: a mutation that happened is never reported as one that did not.

    The failure injected here is the exact one the ordering cannot prevent — the write
    succeeds, and the attempt to record its verdict raises.
    """

    def break_audit(self):
        def raising(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        original = self.architecture.record_tool_result
        self.architecture.record_tool_result = raising
        self.addCleanup(lambda: setattr(self.architecture, "record_tool_result", original))

    def test_the_mutation_still_happens_and_is_still_recorded_as_unsettled(self):
        self.break_audit()
        response = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert")
        self.fx.commit()

        # The change is real. Denying it would be the larger lie.
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")

        rows = self.operations("pulsesoc.crypto_alerts.pause")
        self.assertEqual(len(rows), 1, "the reservation must survive as the evidence")
        self.assertEqual(rows[0]["status"], "needs_reconciliation")
        self.assertIn("reconciliation_reason", rows[0]["verification_json"])

    def test_the_receipt_admits_the_ledger_and_the_world_disagree(self):
        self.break_audit()
        response = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert")
        self.fx.commit()
        audit = response.receipt.evidence.get("audit") or {}
        self.assertEqual(audit.get("status"), "audit_failed")
        self.assertTrue(audit.get("needs_reconciliation"))

    def test_the_next_identical_request_does_not_repeat_the_mutation(self):
        """The most dangerous moment: an unsettled operation invites a retry.

        The alert is resumed out of band first, so if the gateway *did* re-execute the
        pause the alert would end up paused again and the test would see it. Nothing
        else in this file could catch a silent second mutation.
        """
        self.break_audit()
        self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert",
                            client_request_id="same-request")
        self.fx.commit()
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")

        from services import alert_engine

        alert_engine.resume_alert(int(self.alert_id), OWNER_ID)
        self.fx.commit()
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

        replay = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert",
                                     client_request_id="same-request")
        self.fx.commit()
        self.assertEqual(self.fx.alert_status(self.alert_id), "active",
                         "an unsettled operation must not be re-executed")
        self.assertTrue(replay.receipt.evidence.get("idempotent_replay"))
        self.assertTrue(replay.receipt.evidence.get("needs_reconciliation"))

    def test_the_user_is_told_the_outcome_is_unknown_rather_than_fine(self):
        self.break_audit()
        self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert",
                            client_request_id="same-request")
        self.fx.commit()
        replay = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert",
                                     client_request_id="same-request")
        self.fx.commit()
        self.assertNotEqual(replay.status, "verified_success")
        self.assertIn("could not confirm", replay.reply)
        self.assertFalse(replay.receipt.may_claim_completed)


class ReconciliationMarker(LedgerTestCase):
    """The marker itself, exercised directly rather than through a broken gateway."""

    def test_it_marks_the_reserved_row_without_creating_a_new_one(self):
        prepared = self.architecture.prepare_tool_operation(
            OWNER_ID, "pulsesoc.crypto_alerts.pause", "req-1", str(self.alert_id))
        self.architecture.begin_tool_operation(self.fx.cur, OWNER_ID, prepared, "corr-1")
        self.fx.commit()

        result = self.architecture.flag_operation_for_reconciliation(
            self.fx.cur, OWNER_ID, prepared, "RuntimeError")
        self.fx.commit()

        self.assertTrue(result["marked"])
        rows = self.operations("pulsesoc.crypto_alerts.pause")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "needs_reconciliation")

    def test_it_reports_honestly_when_nothing_could_be_marked(self):
        """No reservation exists, so there is nothing durable left to write to.

        ``marked`` is False and the critical log is the only remaining channel. The
        function does not raise: it is already the error path, and raising here would
        replace a recorded incident with an unhandled one.
        """
        prepared = self.architecture.prepare_tool_operation(
            OWNER_ID, "pulsesoc.crypto_alerts.pause", "never-reserved", str(self.alert_id))
        with self.assertLogs("services.undx_architecture", level="CRITICAL") as logged:
            result = self.architecture.flag_operation_for_reconciliation(
                self.fx.cur, OWNER_ID, prepared, "RuntimeError")
        self.assertFalse(result["marked"])
        self.assertTrue(any("undx_audit_lost" in line for line in logged.output))


class AuditContent(LedgerTestCase):
    """Property: the row records what was observed, not what was requested."""

    def test_a_verified_write_records_the_read_back_verdict(self):
        self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert")
        self.fx.commit()
        row = self.operations("pulsesoc.crypto_alerts.pause")[0]
        self.assertEqual(row["status"], "verified")
        self.assertIn('"canonical_read_back": true', row["verification_json"])
        self.assertTrue(row["canonical_entity_id"])

    def test_a_write_whose_verification_disagrees_is_not_recorded_as_verified(self):
        """The audit verdict comes from the verifier, so lying to it changes the row.

        This is the property that makes the ledger worth reading: it cannot be talked
        into ``verified`` by an executor that merely returned successfully.
        """
        from services import undx_verification

        original = undx_verification.VERIFIERS["crypto_alert_status"]
        undx_verification.VERIFIERS["crypto_alert_status"] = lambda *a, **k: original(*a, **k).__class__(
            state="mismatch", expected="paused", observed="active",
            detail="injected disagreement")
        try:
            response = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="pause my bitcoin alert")
            self.fx.commit()
        finally:
            undx_verification.VERIFIERS["crypto_alert_status"] = original

        self.assertNotEqual(response.status, "verified_success")
        row = self.operations("pulsesoc.crypto_alerts.pause")[0]
        self.assertNotEqual(row["status"], "verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
