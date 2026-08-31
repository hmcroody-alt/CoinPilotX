"""Paid Pro projection truth (Ops Center Stages 3-5, 34, 40).

Root cause under test: Apple/Google purchases write ONLY the canonical grant
store — the legacy users columns never see them — so any admin count derived
from user rows alone reports Paid Pro = 0 while paid members exist.

Covers:
  1. ``resolve_all_subjects`` parity with per-subject ``has_entitlement``
     across every precedence phase (active/suspended/revoked/expired/grace/
     grandfathered) — the bulk path must be the SAME resolver, not a re-guess.
  2. ``merged_access_type`` precedence: legacy verdict wins when it grants;
     canonical fills the Apple/Google gap; paid vs trial vs granted provenance.
  3. ``canonical_premium_access_map`` (map, ok): store failure returns ok=False
     so admin surfaces never render a failed read as an authoritative zero.

Hermetic: points services.db at a throwaway SQLite file before importing it.
Runs via pytest or ``python -m unittest``.
"""

import os
import tempfile
import time
import unittest

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_paidpro_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import pro_access  # noqa: E402
from services import premium_entitlement_service as premium  # noqa: E402

KEY = "premium.access"


def _reset():
    svc.ensure_schema()
    conn = db.connect()
    for t in ("business_os_ent_grants", "business_os_ent_usage",
              "business_os_ent_audit", "business_os_ent_provider_subs"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _future(days=365):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z",
                         time.gmtime(time.time() + days * 86400))


def _past(days=365):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z",
                         time.gmtime(time.time() - days * 86400))


class BulkResolverParity(unittest.TestCase):
    """resolve_all_subjects must agree with has_entitlement for every subject."""

    def test_parity_across_all_phases(self):
        _reset()
        # 1: active Apple paid, 2: suspended, 3: revoked, 4: expired,
        # 5: grace (allowed), 6: grandfathered, 7: promo grant, 8: no grants.
        svc.grant_entitlement(1, KEY, source="apple_app_store", source_reference="a1")
        svc.grant_entitlement(2, KEY, source="stripe", source_reference="s2")
        svc.suspend_entitlement(2, KEY, reason="fraud")
        svc.grant_entitlement(3, KEY, source="google_play", source_reference="g3")
        svc.revoke_entitlement(3, KEY, reason="refund", source="google_play",
                               source_reference="g3")
        svc.grant_entitlement(4, KEY, source="stripe", source_reference="s4",
                              expires_at=_past())
        svc.grant_entitlement(5, KEY, source="apple_app_store", source_reference="a5",
                              expires_at=_past(1), grace_until=_future(3))
        svc.grant_entitlement(6, KEY, source="legacy_migration",
                              status=svc.STATUS_GRANDFATHERED)
        svc.grant_entitlement(7, KEY, source="promotion", source_reference="promo")

        bulk = svc.resolve_all_subjects(KEY)
        for uid in range(1, 8):
            self.assertEqual(bulk[str(uid)]["allowed"],
                             svc.has_entitlement(uid, KEY),
                             f"bulk/per-subject verdict diverged for user {uid}")
        self.assertNotIn("8", bulk)  # no grants -> absent, resolves to none

        # Winning-source provenance the Paid Pro rule depends on.
        self.assertEqual(bulk["1"]["source"], "apple_app_store")
        self.assertEqual(bulk["5"]["mode"], "grace")
        self.assertTrue(bulk["5"]["allowed"])
        self.assertEqual(bulk["7"]["source"], "promotion")
        self.assertFalse(bulk["2"]["allowed"])
        self.assertFalse(bulk["3"]["allowed"])
        self.assertFalse(bulk["4"]["allowed"])

    def test_exact_paid_count_from_bulk(self):
        _reset()
        svc.grant_entitlement(1, KEY, source="apple_app_store", source_reference="a1")
        svc.grant_entitlement(2, KEY, source="google_play", source_reference="g2")
        svc.grant_entitlement(3, KEY, source="promotion", source_reference="p3")
        bulk = svc.resolve_all_subjects(KEY)
        paid = sum(1 for v in bulk.values()
                   if v["allowed"] and v["source"] in pro_access.PAID_GRANT_SOURCES)
        self.assertEqual(paid, 2)  # promo confers access but is not paid


class MergedAccessType(unittest.TestCase):
    def test_legacy_paid_wins(self):
        row = {"plan": "pro", "subscription_status": "active"}
        self.assertEqual(pro_access.merged_access_type(row, None), "paid")

    def test_canonical_fills_apple_gap(self):
        # THE defect: legacy row is blank (Apple never writes users columns).
        row = {"plan": "free"}
        canonical = {"allowed": True, "mode": "active", "source": "apple_app_store"}
        self.assertEqual(pro_access.merged_access_type(row, canonical), "paid")

    def test_canonical_google_paid(self):
        canonical = {"allowed": True, "mode": "active", "source": "google_play"}
        self.assertEqual(pro_access.merged_access_type({}, canonical), "paid")

    def test_canonical_trial(self):
        canonical = {"allowed": True, "mode": "active", "source": "trial"}
        self.assertEqual(pro_access.merged_access_type({}, canonical), "trial")

    def test_canonical_admin_is_granted_not_paid(self):
        canonical = {"allowed": True, "mode": "active", "source": "admin"}
        self.assertEqual(pro_access.merged_access_type({}, canonical), "granted")

    def test_canonical_denied_is_none(self):
        canonical = {"allowed": False, "mode": "suspended", "source": "stripe"}
        self.assertEqual(pro_access.merged_access_type({}, canonical), "none")

    def test_no_signal_is_none(self):
        self.assertEqual(pro_access.merged_access_type({}, None), "none")

    def test_legacy_trial_beats_canonical_paid(self):
        # Legacy grants first (Stripe-era rows live there); precedence documented.
        row = {"trial_status": "active", "trial_end_date": _future(2)}
        canonical = {"allowed": True, "mode": "active", "source": "apple_app_store"}
        self.assertEqual(pro_access.merged_access_type(row, canonical), "trial")


class CanonicalMapTruth(unittest.TestCase):
    def test_ok_true_with_grants(self):
        _reset()
        svc.grant_entitlement(42, KEY, source="apple_app_store", source_reference="x")
        mapping, ok = premium.canonical_premium_access_map()
        self.assertTrue(ok)
        self.assertTrue(mapping["42"]["allowed"])
        self.assertEqual(mapping["42"]["source"], "apple_app_store")

    def test_ok_true_with_empty_store_is_real_zero(self):
        _reset()
        mapping, ok = premium.canonical_premium_access_map()
        self.assertTrue(ok)
        self.assertEqual(mapping, {})

    def test_store_failure_reports_ok_false_not_empty_zero(self):
        _reset()
        original = svc.resolve_all_subjects
        svc.resolve_all_subjects = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
        try:
            mapping, ok = premium.canonical_premium_access_map()
        finally:
            svc.resolve_all_subjects = original
        self.assertFalse(ok)
        self.assertEqual(mapping, {})


if __name__ == "__main__":
    unittest.main()
