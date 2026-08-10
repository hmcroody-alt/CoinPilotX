"""Financial incident engine — idempotency, workflow, and read surface.

Runs hermetically against a temporary SQLite DB (set via DATABASE_URL before
importing services.db), mirroring tests/business_os/test_ledger_and_webhook_inbox.py.

    python3 -m unittest tests.business_os_finance.test_incidents -v
"""

import os
import tempfile
import unittest

# --- point services.db at a throwaway SQLite file BEFORE importing it ---
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="fin_inc_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.payments import incidents  # noqa: E402


class BaseCase(unittest.TestCase):
    def setUp(self):
        # Re-pin the DB path: another test module in the same process may have
        # pointed DATABASE_URL elsewhere (services.db reads it per-connect).
        os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
        incidents.ensure_schema()
        conn = db.connect()
        conn.execute("DELETE FROM financial_incidents")
        conn.commit()
        conn.close()

    def _count(self):
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) FROM financial_incidents").fetchone()[0]
        conn.close()
        return int(n)


class OpenIncidentTests(BaseCase):
    def test_open_creates_row_with_defaults(self):
        result = incidents.open_incident(
            incidents.BALANCE_MISMATCH,
            domain="ledger",
            severity="critical",
            summary="cache says 100, entries say 50",
            details={"cached_balance_cents": 100, "computed_balance_cents": 50},
            related_object="ledger_balance:user:1:usd",
        )
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["incident_type"], incidents.BALANCE_MISMATCH)
        self.assertEqual(result["domain"], "ledger")
        self.assertEqual(result["severity"], "critical")
        self.assertEqual(result["details"]["cached_balance_cents"], 100)
        self.assertIsNotNone(result["created_at"])
        self.assertIsNone(result["resolved_at"])
        self.assertEqual(self._count(), 1)

    def test_same_key_twice_is_one_row_updated_and_merged(self):
        first = incidents.open_incident(
            incidents.SUSPENSE_FUNDS_HELD, domain="ledger", severity="warning",
            summary="suspense holds 500", details={"balance_cents": 500},
            incident_key="k-suspense-1",
        )
        second = incidents.open_incident(
            incidents.SUSPENSE_FUNDS_HELD, domain="ledger", severity="warning",
            summary="suspense holds 500", details={"seen_again": True},
            incident_key="k-suspense-1",
        )
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(self._count(), 1)
        # Details merged, not replaced; updated_at moved forward or equal.
        self.assertEqual(second["details"]["balance_cents"], 500)
        self.assertTrue(second["details"]["seen_again"])
        self.assertGreaterEqual(second["updated_at"], first["updated_at"])
        # created_at is the original observation time.
        self.assertEqual(second["created_at"], first["created_at"])

    def test_resolved_incident_is_not_reopened_by_repeat(self):
        first = incidents.open_incident(
            incidents.WEBHOOK_DLQ_EXHAUSTED, domain="webhooks", severity="critical",
            summary="evt_1 dead", incident_key="k-dlq-1",
        )
        incidents.update_incident_status(first["id"], "resolved",
                                         resolution_note="replayed manually")
        repeat = incidents.open_incident(
            incidents.WEBHOOK_DLQ_EXHAUSTED, domain="webhooks", severity="critical",
            summary="evt_1 dead", incident_key="k-dlq-1",
        )
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(repeat["status"], "resolved")
        self.assertEqual(self._count(), 1)

    def test_invalid_inputs_rejected_before_any_write(self):
        for kwargs in (
            dict(incident_type="made_up", domain="ledger", summary="x"),
            dict(incident_type=incidents.BALANCE_MISMATCH, domain="vibes", summary="x"),
            dict(incident_type=incidents.BALANCE_MISMATCH, domain="ledger",
                 severity="mild", summary="x"),
            dict(incident_type=incidents.BALANCE_MISMATCH, domain="ledger", summary=""),
        ):
            with self.assertRaises(incidents.IncidentError):
                incidents.open_incident(
                    kwargs.pop("incident_type"), **kwargs
                )
        self.assertEqual(self._count(), 0)

    def test_default_key_derives_from_type_domain_and_related_object(self):
        a = incidents.open_incident(
            incidents.ORPHAN_STRIPE_OBJECT, domain="seller_payments",
            summary="charge ch_1 has no local order", related_object="charge:ch_1",
        )
        b = incidents.open_incident(
            incidents.ORPHAN_STRIPE_OBJECT, domain="seller_payments",
            summary="charge ch_1 has no local order", related_object="charge:ch_1",
        )
        self.assertTrue(b["duplicate"])
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(self._count(), 1)


class StatusWorkflowTests(BaseCase):
    def _open(self, key="k-1"):
        return incidents.open_incident(
            incidents.BALANCE_MISMATCH, domain="ad_wallet", severity="warning",
            summary="wallet 7 drifted", incident_key=key,
        )

    def test_resolve_requires_a_note(self):
        row = self._open()
        with self.assertRaises(incidents.IncidentError):
            incidents.update_incident_status(row["id"], "resolved")
        with self.assertRaises(incidents.IncidentError):
            incidents.update_incident_status(row["id"], "ignored", resolution_note="  ")
        # Untouched by the rejected attempts.
        self.assertEqual(incidents.get_incident(row["id"])["status"], "open")

    def test_resolve_with_note_sets_resolved_at_and_actor_stamp(self):
        row = self._open()
        updated = incidents.update_incident_status(
            row["id"], "resolved", resolution_note="recomputed by hand", actor="admin:9",
        )
        self.assertEqual(updated["status"], "resolved")
        self.assertIsNotNone(updated["resolved_at"])
        self.assertIn("recomputed by hand", updated["resolution_note"])
        self.assertIn("admin:9", updated["resolution_note"])

    def test_acknowledge_needs_no_note_and_keeps_resolved_at_empty(self):
        row = self._open()
        updated = incidents.update_incident_status(row["id"], "acknowledged")
        self.assertEqual(updated["status"], "acknowledged")
        self.assertIsNone(updated["resolved_at"])

    def test_unknown_incident_or_status_rejected(self):
        with self.assertRaises(incidents.IncidentError) as ctx:
            incidents.update_incident_status(999999, "acknowledged")
        self.assertEqual(ctx.exception.status_code, 404)
        row = self._open()
        with self.assertRaises(incidents.IncidentError):
            incidents.update_incident_status(row["id"], "escalated")


class ReadSurfaceTests(BaseCase):
    def _seed(self):
        ids = []
        for index in range(5):
            row = incidents.open_incident(
                incidents.BALANCE_MISMATCH, domain="ledger", severity="warning",
                summary=f"drift {index}", incident_key=f"ledger-{index}",
            )
            ids.append(row["id"])
        wallet = incidents.open_incident(
            incidents.NEGATIVE_BALANCE_DETECTED, domain="ad_wallet",
            severity="critical", summary="wallet negative", incident_key="wallet-neg",
        )
        incidents.update_incident_status(ids[0], "resolved", resolution_note="fixed")
        return ids, wallet

    def test_list_filters_by_domain_and_status(self):
        ids, wallet = self._seed()
        page = incidents.list_incidents(domain="ad_wallet")
        self.assertEqual([r["id"] for r in page["incidents"]], [wallet["id"]])
        open_only = incidents.list_incidents(status="open")
        self.assertEqual(len(open_only["incidents"]), 5)  # 4 ledger + 1 wallet
        resolved = incidents.list_incidents(status="resolved")
        self.assertEqual([r["id"] for r in resolved["incidents"]], [ids[0]])
        with self.assertRaises(incidents.IncidentError):
            incidents.list_incidents(domain="vibes")

    def test_keyset_pagination_newest_first_no_overlap(self):
        self._seed()
        page1 = incidents.list_incidents(limit=4)
        self.assertEqual(len(page1["incidents"]), 4)
        self.assertTrue(page1["has_more"])
        ids1 = [r["id"] for r in page1["incidents"]]
        self.assertEqual(ids1, sorted(ids1, reverse=True))
        page2 = incidents.list_incidents(limit=4, before_id=page1["next_before_id"])
        ids2 = [r["id"] for r in page2["incidents"]]
        self.assertEqual(len(ids2), 2)
        self.assertFalse(page2["has_more"])
        self.assertIsNone(page2["next_before_id"])
        self.assertTrue(max(ids2) < min(ids1))

    def test_counts_by_status_zero_filled(self):
        self._seed()
        counts = incidents.counts_by_status()
        self.assertEqual(counts["open"], 5)
        self.assertEqual(counts["resolved"], 1)
        self.assertEqual(counts["acknowledged"], 0)
        self.assertEqual(counts["ignored"], 0)

    def test_get_incident_missing_returns_none(self):
        self.assertIsNone(incidents.get_incident(424242))


if __name__ == "__main__":
    unittest.main()
