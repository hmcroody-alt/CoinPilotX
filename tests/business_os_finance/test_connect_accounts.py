"""Connect account state projection — webhook + snapshot write paths.

Runs hermetically against a temporary SQLite DB (set via DATABASE_URL before
importing services.db), mirroring tests/business_os_finance/test_incidents.py.

    python3 -m unittest tests.business_os_finance.test_connect_accounts -v
"""

import os
import tempfile
import unittest

# --- point services.db at a throwaway SQLite file BEFORE importing it ---
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="fin_conn_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.payments import connect_accounts, incidents  # noqa: E402


def _account_event(account_id="acct_1", user_id="7", *, payouts=True,
                   charges=True, details=True, requirements=None,
                   event_type="account.updated"):
    obj = {
        "id": account_id,
        "object": "account",
        "payouts_enabled": payouts,
        "charges_enabled": charges,
        "details_submitted": details,
        "requirements": requirements or {},
    }
    if user_id is not None:
        obj["metadata"] = {"user_id": user_id}
    return {"id": "evt_acct_1", "type": event_type, "data": {"object": obj}}


class BaseCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
        connect_accounts.ensure_schema()
        incidents.ensure_schema()
        conn = db.connect()
        conn.execute("DELETE FROM connect_account_state")
        conn.execute("DELETE FROM financial_incidents")
        conn.commit()
        conn.close()

    def _count(self):
        conn = db.connect()
        n = conn.execute(
            "SELECT COUNT(*) FROM connect_account_state").fetchone()[0]
        conn.close()
        return int(n)


class AccountUpdatedEventTests(BaseCase):
    def test_event_with_metadata_user_projects_state(self):
        result = connect_accounts.apply_account_updated_event(
            _account_event(requirements={"currently_due": ["tos"],
                                         "disabled_reason": ""}))
        self.assertTrue(result["ok"])
        self.assertFalse(result["ignored"])
        state = connect_accounts.get_state("7")
        self.assertEqual(state["connected_account_id"], "acct_1")
        self.assertTrue(state["payouts_enabled"])
        self.assertTrue(state["charges_enabled"])
        self.assertTrue(state["details_submitted"])
        self.assertEqual(state["requirements"]["currently_due"], ["tos"])
        self.assertEqual(self._count(), 1)

    def test_repeat_event_updates_the_same_row(self):
        connect_accounts.apply_account_updated_event(_account_event())
        connect_accounts.apply_account_updated_event(
            _account_event(payouts=False,
                           requirements={"disabled_reason": "under_review"}))
        self.assertEqual(self._count(), 1)
        state = connect_accounts.get_state("7")
        self.assertFalse(state["payouts_enabled"])
        self.assertEqual(state["disabled_reason"], "under_review")

    def test_event_without_metadata_uses_existing_projection(self):
        connect_accounts.apply_account_updated_event(_account_event())
        result = connect_accounts.apply_account_updated_event(
            _account_event(user_id=None, payouts=False))
        self.assertFalse(result["ignored"])
        self.assertEqual(self._count(), 1)
        self.assertFalse(connect_accounts.get_state("7")["payouts_enabled"])

    def test_unattributable_account_opens_orphan_incident_and_is_ignored(self):
        result = connect_accounts.apply_account_updated_event(
            _account_event(account_id="acct_ghost", user_id=None))
        self.assertTrue(result["ignored"])
        self.assertTrue(result["orphan"])
        self.assertEqual(self._count(), 0)
        conn = db.connect()
        rows = conn.execute(
            "SELECT incident_type, severity FROM financial_incidents"
        ).fetchall()
        conn.close()
        self.assertEqual(
            [(str(r["incident_type"]), str(r["severity"])) for r in rows],
            [(incidents.ORPHAN_STRIPE_OBJECT, "info")])

    def test_wrong_event_type_or_missing_id_is_ignored(self):
        result = connect_accounts.apply_account_updated_event(
            _account_event(event_type="account.application.deauthorized"))
        self.assertTrue(result["ignored"])
        event = _account_event()
        event["data"]["object"]["id"] = ""
        self.assertTrue(
            connect_accounts.apply_account_updated_event(event)["ignored"])
        self.assertEqual(self._count(), 0)


class SnapshotTests(BaseCase):
    def _status(self, *, ok=True, payouts=True):
        return {
            "ok": ok,
            "provider_account_id": "acct_1",
            "payouts_enabled": payouts,
            "charges_enabled": True,
            "onboarding_status": "enabled" if payouts else "restricted",
            "requirements": {"currently_due": []},
            "account": {"id": "acct_1", "details_submitted": True},
        }

    def test_snapshot_projects_provider_status_shape(self):
        result = connect_accounts.record_account_snapshot("7", self._status())
        self.assertTrue(result["ok"])
        state = connect_accounts.get_state("7")
        self.assertEqual(state["connected_account_id"], "acct_1")
        self.assertTrue(state["payouts_enabled"])
        self.assertTrue(state["details_submitted"])

    def test_failed_status_is_never_projected(self):
        result = connect_accounts.record_account_snapshot(
            "7", {"ok": False, "message": "Stripe not configured"})
        self.assertFalse(result["ok"])
        self.assertEqual(self._count(), 0)

    def test_snapshot_and_webhook_land_on_one_row(self):
        connect_accounts.record_account_snapshot("7", self._status())
        connect_accounts.apply_account_updated_event(
            _account_event(user_id=None, payouts=False))
        self.assertEqual(self._count(), 1)
        state = connect_accounts.get_state("7")
        self.assertFalse(state["payouts_enabled"])
        self.assertEqual(
            connect_accounts.get_state_by_account("acct_1")["user_id"], "7")

    def test_get_state_missing_returns_none(self):
        self.assertIsNone(connect_accounts.get_state("424242"))
        self.assertIsNone(connect_accounts.get_state_by_account("acct_none"))


if __name__ == "__main__":
    unittest.main()
