"""Seller payout lifecycle engine — request, submit, webhook projection, money.

Runs hermetically against a temporary SQLite DB (set via DATABASE_URL before
importing services.db), mirroring tests/business_os_finance/test_incidents.py.

    python3 -m unittest tests.business_os_finance.test_seller_payouts -v
"""

import os
import tempfile
import unittest

# --- point services.db at a throwaway SQLite file BEFORE importing it ---
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="fin_payout_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.payments import incidents, seller_payouts  # noqa: E402
from services.business_os.payments import reconciliation  # noqa: E402

ACCOUNT_OK = {"connected_account_id": "acct_test_1", "payouts_enabled": True}


class BaseCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
        ledger.ensure_schema()
        incidents.ensure_schema()
        seller_payouts.ensure_schema()
        reconciliation.ensure_schema()
        conn = db.connect()
        for table in (
            "seller_payout_events", "seller_payout_requests",
            "financial_incidents", "ledger_entries", "ledger_transactions",
            "ledger_balances", "reconciliation_runs",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
        conn.close()

    def _fund(self, user_id, cents, key="fund-1"):
        ledger.post_entry(
            idempotency_key=f"test:{key}:{user_id}:{cents}",
            actor="test",
            amount_cents=cents,
            currency="usd",
            entry_type="sale_settled",
            source="external:test_funding",
            destination=seller_payouts.seller_payable_account(user_id),
            reason="test funding",
        )

    def _balances(self, user_id):
        return (
            ledger.get_balance(seller_payouts.seller_payable_account(user_id)),
            ledger.get_balance(seller_payouts.payout_pending_account(user_id)),
            ledger.get_balance(seller_payouts.PAYOUTS_SETTLED_ACCOUNT),
        )

    def _request(self, user_id="7", cents=500, key="pk-1", account=None):
        return seller_payouts.request_payout(
            user_id, cents, requested_by=f"user:{user_id}", payout_key=key,
            account_status=ACCOUNT_OK if account is None else account,
        )

    def _stripe_event(self, event_type, payout_id="po_1", status="",
                      event_id="evt_1", **obj_extra):
        obj = {"id": payout_id, "object": "payout", "status": status}
        obj.update(obj_extra)
        return {"id": event_id, "type": event_type, "data": {"object": obj}}

    def _incident_types(self):
        conn = db.connect()
        rows = conn.execute(
            "SELECT incident_type, severity FROM financial_incidents"
        ).fetchall()
        conn.close()
        return [(str(r["incident_type"]), str(r["severity"])) for r in rows]


class RequestPayoutTests(BaseCase):
    def test_request_fences_funds_and_records_intent(self):
        self._fund("7", 1000)
        result = self._request(cents=600)
        self.assertFalse(result["duplicate"])
        payout = result["payout"]
        self.assertEqual(payout["status"], "pending")
        self.assertEqual(payout["amount_cents"], 600)
        self.assertEqual(payout["connected_account_id"], "acct_test_1")
        payable, pending, settled = self._balances("7")
        self.assertEqual((payable, pending, settled), (400, 600, 0))
        trail = seller_payouts.list_payout_events(payout["id"])
        self.assertEqual([e["event_type"] for e in trail], ["payout_requested"])

    def test_same_payout_key_twice_moves_money_once(self):
        self._fund("7", 1000)
        first = self._request(cents=600, key="pk-dup")
        second = self._request(cents=600, key="pk-dup")
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["payout"]["id"], first["payout"]["id"])
        self.assertEqual(self._balances("7"), (400, 600, 0))

    def test_insufficient_balance_is_409_and_moves_nothing(self):
        self._fund("7", 100)
        with self.assertRaises(seller_payouts.PayoutError) as ctx:
            self._request(cents=500)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.reason, "insufficient_balance")
        self.assertEqual(self._balances("7"), (100, 0, 0))
        self.assertIsNone(seller_payouts.get_payout(payout_key="pk-1"))

    def test_missing_or_disabled_connect_account_is_409(self):
        self._fund("7", 1000)
        with self.assertRaises(seller_payouts.PayoutError) as ctx:
            self._request(account={})
        self.assertEqual(ctx.exception.reason, "no_connected_account")
        with self.assertRaises(seller_payouts.PayoutError) as ctx:
            self._request(account={"connected_account_id": "acct_1",
                                   "payouts_enabled": False})
        self.assertEqual(ctx.exception.reason, "payouts_disabled")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self._balances("7"), (1000, 0, 0))

    def test_invalid_amounts_and_keys_are_400(self):
        self._fund("7", 1000)
        for bad in (0, -5, "500", 4.2, True, None):
            with self.assertRaises(seller_payouts.PayoutError):
                self._request(cents=bad, key=f"pk-bad-{bad}")
        with self.assertRaises(seller_payouts.PayoutError):
            self._request(key="")
        self.assertEqual(self._balances("7"), (1000, 0, 0))

    def test_build_stripe_payout_args_shape(self):
        self._fund("7", 1000)
        payout = self._request(cents=500)["payout"]
        args = seller_payouts.build_stripe_payout_args(payout)
        self.assertEqual(args["method"], "payout")
        self.assertEqual(args["stripe_account"], "acct_test_1")
        self.assertEqual(args["idempotency_key"], "seller_payout:pk-1")
        self.assertEqual(args["kwargs"]["amount"], 500)
        self.assertEqual(args["kwargs"]["currency"], "usd")
        self.assertEqual(args["kwargs"]["metadata"]["payout_key"], "pk-1")


class SubmitAndLocalFailTests(BaseCase):
    def test_mark_submitted_is_idempotent_and_stamps_stripe_id(self):
        self._fund("7", 1000)
        payout = self._request()["payout"]
        updated = seller_payouts.mark_payout_submitted(
            payout["id"], stripe_payout_id="po_9")
        self.assertEqual(updated["status"], "payout_created")
        self.assertEqual(updated["stripe_payout_id"], "po_9")
        again = seller_payouts.mark_payout_submitted(
            payout["id"], stripe_payout_id="po_other")
        self.assertEqual(again["status"], "payout_created")
        self.assertEqual(again["stripe_payout_id"], "po_9")  # first id wins

    def test_local_fail_reverses_the_fence(self):
        self._fund("7", 1000)
        payout = self._request(cents=600)["payout"]
        failed = seller_payouts.fail_payout(
            payout["id"], failure_code="stripe_api_error")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self._balances("7"), (1000, 0, 0))
        # replay is a no-op — no double reversal
        seller_payouts.fail_payout(payout["id"], failure_code="stripe_api_error")
        self.assertEqual(self._balances("7"), (1000, 0, 0))

    def test_submit_after_terminal_is_illegal(self):
        self._fund("7", 1000)
        payout = self._request()["payout"]
        seller_payouts.fail_payout(payout["id"], failure_code="x")
        with self.assertRaises(seller_payouts.PayoutError) as ctx:
            seller_payouts.mark_payout_submitted(payout["id"])
        self.assertEqual(ctx.exception.status_code, 409)


class WebhookLifecycleTests(BaseCase):
    def _submitted(self, cents=500):
        self._fund("7", 1000)
        payout = self._request(cents=cents)["payout"]
        return seller_payouts.mark_payout_submitted(
            payout["id"], stripe_payout_id="po_1")

    def test_paid_settles_the_pending_funds_exactly_once(self):
        self._submitted(cents=500)
        event = self._stripe_event("payout.paid", status="paid")
        result = seller_payouts.apply_stripe_payout_event(event)
        self.assertTrue(result["applied"])
        self.assertEqual(result["status_after"], "paid")
        self.assertFalse(result["conflict"])
        self.assertEqual(self._balances("7"), (500, 0, 500))
        # replayed webhook: same ledger key, no second movement
        replay = seller_payouts.apply_stripe_payout_event(event)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(self._balances("7"), (500, 0, 500))

    def test_in_transit_via_payout_updated(self):
        payout = self._submitted()
        result = seller_payouts.apply_stripe_payout_event(
            self._stripe_event("payout.updated", status="in_transit"))
        self.assertEqual(result["status_after"], "in_transit")
        self.assertEqual(
            seller_payouts.get_payout(payout_id=payout["id"])["status"],
            "in_transit")

    def test_failed_reverses_to_payable_with_info_incident(self):
        self._submitted(cents=500)
        result = seller_payouts.apply_stripe_payout_event(
            self._stripe_event("payout.failed", status="failed",
                               failure_code="account_closed"))
        self.assertEqual(result["status_after"], "failed")
        self.assertEqual(self._balances("7"), (1000, 0, 0))
        types = self._incident_types()
        self.assertIn((incidents.PAYOUT_STATE_CONFLICT, "info"), types)

    def test_returned_after_paid_is_critical_and_reverses_settlement(self):
        self._submitted(cents=500)
        seller_payouts.apply_stripe_payout_event(
            self._stripe_event("payout.paid", status="paid", event_id="evt_p"))
        self.assertEqual(self._balances("7"), (500, 0, 500))
        result = seller_payouts.apply_stripe_payout_event(
            self._stripe_event("payout.failed", status="failed",
                               event_id="evt_f", failure_code="bank_return"))
        self.assertEqual(result["status_after"], "returned")
        self.assertTrue(result["conflict"])
        self.assertEqual(self._balances("7"), (1000, 0, 0))
        types = self._incident_types()
        self.assertIn((incidents.PAYOUT_STATE_CONFLICT, "critical"), types)
        row = seller_payouts.get_payout(payout_key="pk-1")
        self.assertEqual(row["status"], "returned")

    def test_orphan_stripe_payout_opens_incident_and_is_ignored(self):
        result = seller_payouts.apply_stripe_payout_event(
            self._stripe_event("payout.paid", payout_id="po_unknown",
                               status="paid"))
        self.assertTrue(result["ignored"])
        self.assertTrue(result["orphan"])
        types = self._incident_types()
        self.assertIn((incidents.ORPHAN_STRIPE_OBJECT, "warning"), types)
        self.assertEqual(self._balances("7"), (0, 0, 0))

    def test_reconciliation_completed_is_ignored_without_incident(self):
        result = seller_payouts.apply_stripe_payout_event(
            self._stripe_event("payout.reconciliation_completed"))
        self.assertTrue(result["ignored"])
        self.assertEqual(self._incident_types(), [])

    def test_illegal_transition_recorded_with_conflict_incident(self):
        # Stripe says paid while the row is still 'pending' (we never saw
        # payout.created). Stripe truth is recorded; a conflict is opened.
        self._fund("7", 1000)
        payout = self._request(cents=500)["payout"]
        conn = db.connect()
        conn.execute(
            "UPDATE seller_payout_requests SET stripe_payout_id='po_1' WHERE id=?",
            (payout["id"],))
        conn.commit()
        conn.close()
        result = seller_payouts.apply_stripe_payout_event(
            self._stripe_event("payout.paid", status="paid"))
        self.assertTrue(result["applied"])
        self.assertTrue(result["conflict"])
        self.assertEqual(result["status_after"], "paid")
        self.assertEqual(self._balances("7"), (500, 0, 500))
        types = self._incident_types()
        self.assertIn((incidents.PAYOUT_STATE_CONFLICT, "warning"), types)

    def test_transfer_events_append_to_trail_or_ignore(self):
        self._fund("7", 1000)
        payout = self._request()["payout"]
        conn = db.connect()
        conn.execute(
            "UPDATE seller_payout_requests SET stripe_transfer_id='tr_1' WHERE id=?",
            (payout["id"],))
        conn.commit()
        conn.close()
        hit = seller_payouts.apply_stripe_transfer_event(
            {"id": "evt_t", "type": "transfer.created",
             "data": {"object": {"id": "tr_1", "amount": 500}}})
        self.assertTrue(hit["applied"])
        miss = seller_payouts.apply_stripe_transfer_event(
            {"id": "evt_t2", "type": "transfer.created",
             "data": {"object": {"id": "tr_other"}}})
        self.assertTrue(miss["ignored"])
        self.assertEqual(self._incident_types(), [])


class ReadSurfaceTests(BaseCase):
    def test_list_payouts_filters_and_keyset_pagination(self):
        self._fund("7", 10000)
        self._fund("8", 1000, key="fund-8")
        ids = []
        for index in range(5):
            ids.append(self._request(cents=100, key=f"pk-{index}")["payout"]["id"])
        other = self._request(user_id="8", cents=100, key="pk-other")["payout"]
        mine = seller_payouts.list_payouts("7")
        self.assertNotIn(other["id"], [r["id"] for r in mine["payouts"]])
        page1 = seller_payouts.list_payouts("7", limit=3)
        self.assertEqual(len(page1["payouts"]), 3)
        self.assertTrue(page1["has_more"])
        page2 = seller_payouts.list_payouts(
            "7", limit=3, before_id=page1["next_before_id"])
        ids2 = [r["id"] for r in page2["payouts"]]
        self.assertEqual(len(ids2), 2)
        self.assertFalse(page2["has_more"])
        self.assertTrue(max(ids2) < min(r["id"] for r in page1["payouts"]))
        pending_only = seller_payouts.list_payouts("7", status="pending")
        self.assertEqual(len(pending_only["payouts"]), 5)
        with self.assertRaises(seller_payouts.PayoutError):
            seller_payouts.list_payouts("7", status="vibes")

    def test_other_users_request_needs_own_funding(self):
        self._fund("7", 10000)
        with self.assertRaises(seller_payouts.PayoutError):
            self._request(user_id="8", cents=100, key="pk-other")

    def test_seller_balance_summary_is_ledger_derived(self):
        self._fund("7", 1000)
        self._request(cents=400)
        summary = seller_payouts.seller_balance_summary("7")
        self.assertEqual(summary["available_cents"], 600)
        self.assertEqual(summary["payout_pending_cents"], 400)
        self.assertEqual(summary["currency"], "usd")
        # Marketplace vertical is flag-gated off in this harness: the absence
        # is reported explicitly, never faked as a zero-that-means-two-things.
        self.assertIn(summary["processing_source"],
                      {"marketplace_escrow", "unavailable"})
        self.assertEqual(summary["accounts"]["available"], "seller_payable:7")


class ReconcileSellerPayoutsTests(BaseCase):
    def test_stale_submitted_payout_opens_warning_conflict(self):
        self._fund("7", 1000)
        payout = self._request()["payout"]
        seller_payouts.mark_payout_submitted(payout["id"], stripe_payout_id="po_1")
        conn = db.connect()
        conn.execute(
            "UPDATE seller_payout_requests SET updated_at='2020-01-01T00:00:00.000000Z' "
            "WHERE id=?", (payout["id"],))
        conn.commit()
        conn.close()
        result = reconciliation.reconcile_seller_payouts()
        self.assertEqual(result["stale_payouts"], 1)
        self.assertIn((incidents.PAYOUT_STATE_CONFLICT, "warning"),
                      self._incident_types())

    def test_fresh_pending_state_is_clean(self):
        self._fund("7", 1000)
        self._request()
        result = reconciliation.reconcile_seller_payouts()
        self.assertEqual(result["stale_payouts"], 0)
        self.assertEqual(result["orphaned_pending_balances"], 0)
        self.assertEqual(result["negative_payables"], 0)
        self.assertEqual(result["incidents"], [])

    def test_orphaned_pending_balance_is_reported(self):
        # Money fenced in payout_pending with no live payout row to explain it.
        ledger.post_entry(
            idempotency_key="test:orphan-pending", actor="test",
            amount_cents=250, currency="usd", entry_type="payout_request",
            source="external:test_funding",
            destination=seller_payouts.payout_pending_account("9"),
            reason="orphan")
        result = reconciliation.reconcile_seller_payouts()
        self.assertEqual(result["orphaned_pending_balances"], 1)
        self.assertIn(incidents.ORPHAN_LOCAL_RECORD,
                      [t for t, _s in self._incident_types()])

    def test_negative_payable_is_critical(self):
        ledger.post_entry(
            idempotency_key="test:negative-payable", actor="test",
            amount_cents=300, currency="usd", entry_type="adjustment",
            source=seller_payouts.seller_payable_account("9"),
            destination="external:test_sink", reason="force negative",
            allow_negative=True)
        result = reconciliation.reconcile_seller_payouts()
        self.assertEqual(result["negative_payables"], 1)
        self.assertIn((incidents.NEGATIVE_BALANCE_DETECTED, "critical"),
                      self._incident_types())

    def test_run_all_includes_the_seller_payout_check(self):
        summary = reconciliation.run_all()
        self.assertIn("seller_payouts", summary["checks"])
        self.assertEqual(summary["check_errors"], 0)


if __name__ == "__main__":
    unittest.main()
